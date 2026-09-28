"""Bring the production knowledge base in line with ``knowledge_seed/``.

For every seed article (``scripts.seed_knowledge.list_seed_files``) the script
finds the DB article with the same H1 title (whitespace/case-insensitive) and
plans one of:

* ``CREATE``         — no article with that title; inserted with the manifest
                       tenant (files outside the manifest → shared/NULL).
* ``UPDATE_CONTENT`` — text differs from the file; content is replaced with
                       the file body (H1 stripped, as ``POST /import`` does).
* ``SET_TENANT``     — the manifest (``knowledge_seed/TENANTS.md``) binds the
                       file to a network (or to shared) and ``tenant_id``
                       differs. Files outside the manifest keep their tenant.
* ``SET_CATEGORY``   — the DB row sits in another category than the seed
                       folder (prod had the wheels articles under
                       ``general``, so a ``category="wheels"`` search missed
                       them).
* ``DEACTIVATE``     — a second active row with the same title (prod had
                       shared ``general`` copies of the network-only fitting
                       articles): ``active = false``, never deleted. The row
                       kept is the one in the seed category, then the one
                       whose text matches the file, then an active one.
* ``UNCHANGED``.

Never touched: articles of category ``promotions`` (admin promos), articles
that match no seed file ("вне seed", reported only), duplicates when one of
them is a promotion (reported only).

Created and re-texted articles get ``embedding_status = 'pending'`` and an
embedding task (``generate_article_embeddings.delay``), exactly like
``src/api/knowledge.py``. If Celery is unreachable they stay ``pending`` and
``_periodic_embedding_check`` in the call-processor picks them up (every
5 min, 20 per pass).

Dry-run by default; ``--apply`` writes everything in ONE transaction, after
saving a snapshot of the affected rows (id, title, tenant_id, md5(content))
to a JSON file. Any write error rolls the transaction back and exits non-zero.

Usage:
    python -m scripts.sync_knowledge_seed                      # dry-run
    python -m scripts.sync_knowledge_seed --apply --snapshot /tmp/kb_snapshot.json
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.seed_knowledge import detect_category, list_seed_files, parse_md

SEED_ROOT = "knowledge_seed"
MANIFEST_NAME = "TENANTS.md"
PROTECTED_CATEGORIES = frozenset({"promotions"})
SHARED = "shared"

CREATE = "CREATE"
UPDATE_CONTENT = "UPDATE_CONTENT"
SET_TENANT = "SET_TENANT"
SET_CATEGORY = "SET_CATEGORY"
DEACTIVATE = "DEACTIVATE"
UNCHANGED = "UNCHANGED"

# | `delivery/06_delivery_tvoya_shina.md` | `tvoya-shina` |   /   | `...md` | shared |
_MANIFEST_ROW = re.compile(r"^\|\s*`([^`]+\.md)`\s*\|\s*`?([A-Za-z0-9_-]+)`?\s*\|\s*$")


class SyncError(Exception):
    """A condition under which the script must not write anything."""


# ── Pure helpers ────────────────────────────────────────────────────────────


def normalize_title(title: str) -> str:
    """Title key for matching: collapsed whitespace, case-folded."""
    return " ".join(title.split()).casefold()


def normalize_content(content: str | None) -> str:
    """Same normalisation as the import path (``body.strip()``)."""
    return (content or "").strip()


def extract_body(raw: str) -> str:
    """File body without the H1 line — what ``POST /knowledge/articles/import`` stores."""
    from src.knowledge.parsers import parse_markdown

    return parse_markdown(raw.encode("utf-8"), "seed.md")[1]


def parse_manifest(text: str) -> dict[str, str | None]:
    """``TENANTS.md`` table → {relative path: tenant slug, or None for shared}."""
    mapping: dict[str, str | None] = {}
    for line in text.splitlines():
        m = _MANIFEST_ROW.match(line.strip())
        if not m:
            continue
        path, tenant = m.group(1).strip(), m.group(2).strip()
        if path in mapping:
            msg = f"TENANTS.md: {path} listed twice"
            raise SyncError(msg)
        mapping[path] = None if tenant == SHARED else tenant
    return mapping


def load_manifest(seed_root: str = SEED_ROOT) -> dict[str, str | None]:
    path = Path(seed_root) / MANIFEST_NAME
    if not path.is_file():
        msg = f"{path} not found — refusing to sync without the tenant manifest"
        raise SyncError(msg)
    mapping = parse_manifest(path.read_text(encoding="utf-8"))
    if not mapping:
        msg = f"{path}: no `| \\`path\\` | tenant |` rows parsed"
        raise SyncError(msg)
    return mapping


@dataclass(frozen=True)
class SeedArticle:
    rel_path: str  # relative to the seed root, forward slashes
    title: str
    category: str
    body: str  # H1 stripped, stripped
    raw: str  # whole file, stripped (legacy seed_knowledge.py stored this)


@dataclass(frozen=True)
class DbArticle:
    id: str
    title: str
    category: str
    content: str
    tenant_id: str | None
    active: bool


@dataclass
class Action:
    kind: str  # CREATE | UPDATE_CONTENT | SET_TENANT | UNCHANGED
    seed: SeedArticle
    db: DbArticle | None = None
    new_content: str | None = None  # set → content is rewritten (+ reindex)
    set_tenant: bool = False
    new_tenant_id: str | None = None
    new_tenant_slug: str | None = None
    new_category: str | None = None
    kinds: list[str] = field(default_factory=list)  # UPDATE_CONTENT and SET_TENANT can combine

    @property
    def is_write(self) -> bool:
        return self.kind != UNCHANGED


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=list)
    out_of_seed: list[DbArticle] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[Action]:
        return [a for a in self.actions if kind in a.kinds]


def load_seed(seed_root: str = SEED_ROOT) -> list[SeedArticle]:
    articles: list[SeedArticle] = []
    for filepath in list_seed_files(seed_root):
        title, raw = parse_md(filepath)
        rel = os.path.relpath(filepath, seed_root).replace("\\", "/")
        articles.append(
            SeedArticle(
                rel_path=rel,
                title=title,
                category=detect_category(filepath),
                body=normalize_content(extract_body(raw)),
                raw=normalize_content(raw),
            )
        )
    return articles


def required_slugs(manifest: dict[str, str | None]) -> set[str]:
    return {slug for slug in manifest.values() if slug is not None}


def build_plan(
    seed: list[SeedArticle],
    db_rows: list[DbArticle],
    manifest: dict[str, str | None],
    tenant_ids: dict[str, str],
) -> Plan:
    """Pure planning: nothing here touches the DB."""
    missing = sorted(required_slugs(manifest) - tenant_ids.keys())
    if missing:
        msg = f"tenants not found in DB by slug: {', '.join(missing)}"
        raise SyncError(msg)

    plan = Plan()

    seed_paths = {s.rel_path for s in seed}
    for path in sorted(manifest.keys() - seed_paths):
        plan.warnings.append(f"TENANTS.md lists {path}, but there is no such seed file")

    by_title: dict[str, list[DbArticle]] = {}
    for row in db_rows:
        by_title.setdefault(normalize_title(row.title), []).append(row)

    seed_by_title: dict[str, list[SeedArticle]] = {}
    for s in seed:
        seed_by_title.setdefault(normalize_title(s.title), []).append(s)

    for s in seed:
        key = normalize_title(s.title)
        if len(seed_by_title[key]) > 1:
            others = ", ".join(x.rel_path for x in seed_by_title[key])
            plan.ambiguous.append(f"{s.rel_path}: several seed files share the title ({others})")
            continue
        if not s.body:
            plan.warnings.append(f"{s.rel_path}: empty body after stripping H1 — skipped")
            continue

        in_manifest = s.rel_path in manifest
        slug = manifest.get(s.rel_path)
        target_tenant = tenant_ids[slug] if slug is not None else None

        matches = by_title.get(key, [])
        if len(matches) > 1:
            if any(m.category in PROTECTED_CATEGORIES for m in matches):
                ids = ", ".join(f"{m.id} ({m.category})" for m in matches)
                plan.ambiguous.append(
                    f"{s.rel_path}: «{s.title}» matches {len(matches)} rows: {ids}"
                )
                continue
            keeper, *dupes = sorted(matches, key=lambda m: _keeper_rank(m, s))
            for dupe in dupes:
                if dupe.active:
                    plan.actions.append(
                        Action(kind=DEACTIVATE, kinds=[DEACTIVATE], seed=s, db=dupe)
                    )
            matches = [keeper]

        if not matches:
            plan.actions.append(
                Action(
                    kind=CREATE,
                    kinds=[CREATE],
                    seed=s,
                    new_content=s.body,
                    set_tenant=True,
                    new_tenant_id=target_tenant,
                    new_tenant_slug=slug,
                )
            )
            continue

        row = matches[0]
        if row.category in PROTECTED_CATEGORIES:
            plan.protected.append(
                f"{s.rel_path}: «{s.title}» matches {row.category} article {row.id} — not touched"
            )
            continue

        action = Action(kind=UNCHANGED, seed=s, db=row)
        current = normalize_content(row.content)
        if current not in (s.body, s.raw):
            action.new_content = s.body
            action.kinds.append(UPDATE_CONTENT)
        if in_manifest and row.tenant_id != target_tenant:
            action.set_tenant = True
            action.new_tenant_id = target_tenant
            action.new_tenant_slug = slug
            action.kinds.append(SET_TENANT)
        if row.category != s.category:
            action.new_category = s.category
            action.kinds.append(SET_CATEGORY)
        if action.kinds:
            action.kind = "+".join(action.kinds)
        else:
            action.kinds.append(UNCHANGED)

        if row.title != s.title:
            plan.warnings.append(
                f"{s.rel_path}: DB title «{row.title}» differs from H1 «{s.title}» only by "
                f"spacing/case (id={row.id}) — title not changed"
            )
        if not row.active and action.is_write:
            plan.warnings.append(
                f"{s.rel_path}: article {row.id} is inactive — updated, stays inactive"
            )
        plan.actions.append(action)

    seed_keys = set(seed_by_title)
    plan.out_of_seed = sorted(
        (r for r in db_rows if normalize_title(r.title) not in seed_keys),
        key=lambda r: (r.category, r.title),
    )
    return plan


def _keeper_rank(row: DbArticle, s: SeedArticle) -> tuple[bool, bool, bool, str]:
    """Sort key of duplicate rows: the kept one sorts first."""
    same_text = normalize_content(row.content) in (s.body, s.raw)
    return (row.category != s.category, not same_text, not row.active, row.id)


def format_plan(plan: Plan, slug_by_id: dict[str, str]) -> str:
    def tenant_label(tid: str | None) -> str:
        return SHARED if tid is None else slug_by_id.get(tid, tid)

    lines: list[str] = []
    for kind in (CREATE, UPDATE_CONTENT, SET_TENANT, SET_CATEGORY, DEACTIVATE):
        acts = plan.of_kind(kind)
        lines.append(f"── {kind}: {len(acts)}")
        for a in acts:
            if kind == CREATE:
                lines.append(
                    f"  + {a.seed.rel_path} [{a.seed.category}] «{a.seed.title}» → {tenant_label(a.new_tenant_id)}"
                )
            elif kind == UPDATE_CONTENT:
                assert a.db is not None
                lines.append(
                    f"  ~ {a.seed.rel_path} id={a.db.id} «{a.db.title}» "
                    f"({len(normalize_content(a.db.content))} → {len(a.seed.body)} chars)"
                )
            elif kind == SET_CATEGORY:
                assert a.db is not None
                lines.append(
                    f"  # {a.seed.rel_path} id={a.db.id} «{a.db.title}» "
                    f"{a.db.category} → {a.new_category}"
                )
            elif kind == DEACTIVATE:
                assert a.db is not None
                lines.append(
                    f"  x {a.seed.rel_path} duplicate id={a.db.id} [{a.db.category}] "
                    f"tenant={tenant_label(a.db.tenant_id)} «{a.db.title}»"
                )
            else:
                assert a.db is not None
                lines.append(
                    f"  @ {a.seed.rel_path} id={a.db.id} «{a.db.title}» "
                    f"{tenant_label(a.db.tenant_id)} → {tenant_label(a.new_tenant_id)}"
                )
    lines.append(f"── {UNCHANGED}: {len(plan.of_kind(UNCHANGED))}")
    lines.append(f"── AMBIGUOUS (no action): {len(plan.ambiguous)}")
    lines.extend(f"  ? {x}" for x in plan.ambiguous)
    lines.append(f"── PROTECTED (no action): {len(plan.protected)}")
    lines.extend(f"  ! {x}" for x in plan.protected)
    lines.append(f"── WARNINGS: {len(plan.warnings)}")
    lines.extend(f"  * {x}" for x in plan.warnings)
    lines.append(f"── OUT OF SEED (never touched): {len(plan.out_of_seed)}")
    lines.extend(
        f"  - [{r.category}] «{r.title}» id={r.id} tenant={tenant_label(r.tenant_id)}"
        for r in plan.out_of_seed
    )
    return "\n".join(lines)


# ── DB side ─────────────────────────────────────────────────────────────────


async def _fetch_db_rows(conn: Any) -> list[DbArticle]:
    from sqlalchemy import text

    result = await conn.execute(
        text(
            "SELECT id, title, category, content, tenant_id, active "
            "FROM knowledge_articles ORDER BY category, title"
        )
    )
    return [
        DbArticle(
            id=str(r.id),
            title=r.title,
            category=r.category,
            content=r.content or "",
            tenant_id=str(r.tenant_id) if r.tenant_id is not None else None,
            active=bool(r.active),
        )
        for r in result
    ]


async def _fetch_tenant_ids(conn: Any, slugs: set[str]) -> dict[str, str]:
    from sqlalchemy import text

    if not slugs:
        return {}
    result = await conn.execute(
        text("SELECT id, slug FROM tenants WHERE slug = ANY(:slugs)"), {"slugs": sorted(slugs)}
    )
    return {r.slug: str(r.id) for r in result}


async def _snapshot(conn: Any, plan: Plan) -> dict[str, Any]:
    from sqlalchemy import text

    ids = [a.db.id for a in plan.actions if a.db is not None and a.is_write]
    rows: list[dict[str, Any]] = []
    if ids:
        result = await conn.execute(
            text(
                "SELECT id, title, category, tenant_id, active, md5(content) AS content_md5, "
                "embedding_status "
                "FROM knowledge_articles WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY title"
            ),
            {"ids": ids},
        )
        rows = [
            {
                "id": str(r.id),
                "title": r.title,
                "category": r.category,
                "tenant_id": str(r.tenant_id) if r.tenant_id is not None else None,
                "active": r.active,
                "content_md5": r.content_md5,
                "embedding_status": r.embedding_status,
            }
            for r in result
        ]
    return {
        "taken_at": dt.datetime.now(dt.UTC).isoformat(),
        "updated_rows_before": rows,
        "to_create": [
            {"title": a.seed.title, "category": a.seed.category, "tenant_id": a.new_tenant_id}
            for a in plan.of_kind(CREATE)
        ],
    }


async def _apply(conn: Any, plan: Plan) -> tuple[list[str], list[str]]:
    """Write the plan. Returns (created ids, content-updated ids). Raises on any mismatch."""
    from sqlalchemy import text

    created: list[str] = []
    retexted: list[str] = []
    for a in plan.actions:
        if CREATE in a.kinds:
            result = await conn.execute(
                text(
                    "INSERT INTO knowledge_articles (title, category, content, embedding_status, tenant_id) "
                    "VALUES (:title, :category, :content, 'pending', CAST(:tenant_id AS uuid)) RETURNING id"
                ),
                {
                    "title": a.seed.title,
                    "category": a.seed.category,
                    "content": a.new_content,
                    "tenant_id": a.new_tenant_id,
                },
            )
            created.append(str(result.scalar_one()))
            continue
        if not a.is_write:
            continue
        assert a.db is not None
        sets = ["updated_at = now()"]
        params: dict[str, Any] = {"id": a.db.id}
        if UPDATE_CONTENT in a.kinds:
            sets += ["content = :content", "embedding_status = 'pending'"]
            params["content"] = a.new_content
        if SET_TENANT in a.kinds:
            sets.append("tenant_id = CAST(:tenant_id AS uuid)")
            params["tenant_id"] = a.new_tenant_id
        if SET_CATEGORY in a.kinds:
            sets.append("category = :category")
            params["category"] = a.new_category
        if DEACTIVATE in a.kinds:
            sets.append("active = false")
        # category guard: a row that turned into a promotion since planning is left alone
        result = await conn.execute(
            text(
                f"UPDATE knowledge_articles SET {', '.join(sets)} "
                "WHERE id = CAST(:id AS uuid) AND category <> 'promotions'"
            ),
            params,
        )
        if result.rowcount != 1:
            msg = f"UPDATE of {a.db.id} («{a.db.title}») touched {result.rowcount} rows, expected 1"
            raise SyncError(msg)
        if UPDATE_CONTENT in a.kinds:
            retexted.append(a.db.id)
    return created, retexted


def dispatch_embeddings(article_ids: list[str]) -> list[str]:
    """Queue embedding tasks like ``src.api.knowledge._dispatch_embedding``.

    Returns the ids that could not be queued (they stay ``pending`` for the
    call-processor's periodic embedding check). Not a DB write — the rows are
    already committed with ``embedding_status='pending'``.
    """
    if not article_ids:
        return []
    try:
        from src.tasks.embedding_tasks import generate_article_embeddings
    except Exception as exc:  # celery/app import failure → all stay pending
        print(f"WARN: embedding task unavailable ({exc!r})")
        return list(article_ids)
    failed: list[str] = []
    for aid in article_ids:
        try:
            generate_article_embeddings.delay(aid)
        except Exception as exc:
            print(f"WARN: could not queue embedding for {aid}: {exc!r}")
            failed.append(aid)
    return failed


async def run(apply: bool, snapshot_path: str, seed_root: str = SEED_ROOT) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config import get_settings

    manifest = load_manifest(seed_root)
    seed = load_seed(seed_root)
    print(f"Seed: {len(seed)} files, manifest: {len(manifest)} rows")

    engine = create_async_engine(get_settings().database.url)
    try:
        # Plain connection: reads autobegin a transaction; closing without
        # commit rolls back (dry-run, or any exception during --apply).
        async with engine.connect() as conn:
            tenant_ids = await _fetch_tenant_ids(conn, required_slugs(manifest))
            db_rows = await _fetch_db_rows(conn)
            plan = build_plan(seed, db_rows, manifest, tenant_ids)
            print(f"DB: {len(db_rows)} articles; tenants: {tenant_ids}")
            print(format_plan(plan, {v: k for k, v in tenant_ids.items()}))

            writes = [a for a in plan.actions if a.is_write]
            if not apply:
                print(f"\nDRY-RUN: {len(writes)} article(s) would be written. Re-run with --apply.")
                return 0
            if not writes:
                print("\nNothing to write.")
                return 0

            snap = await _snapshot(conn, plan)
            snap_json = json.dumps(snap, ensure_ascii=False, indent=2)
            Path(snapshot_path).write_text(snap_json, encoding="utf-8")
            print(f"\nSnapshot written to {snapshot_path}:\n{snap_json}")

            created, retexted = await _apply(conn, plan)
            await conn.commit()
        print(
            f"\nAPPLIED: created {len(created)}, content updated {len(retexted)}, "
            f"tenant/category/deactivate only {len(writes) - len(created) - len(retexted)}"
        )
        for aid in created:
            print(f"  created id={aid}")
    finally:
        await engine.dispose()

    to_index = created + retexted
    not_queued = dispatch_embeddings(to_index)
    print(f"Embeddings queued: {len(to_index) - len(not_queued)} / {len(to_index)}")
    if not_queued:
        print(
            "Left with embedding_status='pending' (call-processor _periodic_embedding_check "
            "indexes them within ~5 min, 20 per pass):"
        )
        for aid in not_queued:
            print(f"  {aid}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    parser.add_argument(
        "--snapshot",
        default=f"kb_sync_snapshot_{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}.json",
        help="where to save the pre-write snapshot (default: current directory)",
    )
    parser.add_argument("--seed-root", default=SEED_ROOT)
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args.apply, args.snapshot, args.seed_root))
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
