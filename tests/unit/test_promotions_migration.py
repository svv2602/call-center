"""Migration 063 (promotions table + permissions) and scripts/migrate_promotions.py.

The migration SQL is collected by importing the revision with a stand-in
``alembic`` module (alembic is not a test dependency) and executed on a
throw-away PostgreSQL cluster when the server binaries are installed; those
tests are skipped otherwise. The script's plan logic is tested without a DB,
its INSERT on the same throw-away cluster.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import date
from glob import glob
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from scripts import migrate_promotions as mp

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "versions" / "063_add_promotions.py"
TODAY = date(2026, 9, 28)
PK = "bb0e4d02-e1f3-4ac7-b584-3189ee927138"
TS = "2e47a882-4f12-4adc-98b3-dd853ad19682"

# Titles as stored on prod 2026-09-28 (knowledge_articles, category=promotions, active).
PROD_TITLES = [
    (PK, "Безкоштовна доставка шин Doublestar та Rydanz"),
    (PK, "Безкоштовна доставка шин від Prokoleso.ua — встигніть скористатися акцією"),
    (PK, "Безкоштовна доставка шин від провідних брендів (січень–лютий 2026)"),
    (PK, "Гарантія на шини Matador від будь-яких пошкоджень"),
    (PK, "Ексклюзивне сервісне обслуговування на 3 роки"),
    (PK, "Знижка 5% на всесезонні шини Bridgestone"),
    (TS, "Акція «Доставимо безплатно»"),
    (TS, "Сервісна програма MICHELIN"),
]


def _articles() -> list[dict[str, Any]]:
    return [
        {
            "id": f"00000000-0000-0000-0000-00000000000{i}",
            "tenant_id": tid,
            "title": title,
            "promo_summary": "Коротко про акцію. Діє з 1 по 28 лютого 2026 року.",
            "content": "## Акція\n\n**Умови:** дивіться [сайт](https://example.com).",
        }
        for i, (tid, title) in enumerate(PROD_TITLES, start=1)
    ]


# --------------------------------------------------------------------------- #
# Migration SQL
# --------------------------------------------------------------------------- #


def _render(fn_name: str) -> list[str]:
    statements: list[str] = []
    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = types.SimpleNamespace(execute=statements.append)  # type: ignore[attr-defined]
    saved = sys.modules.get("alembic")
    sys.modules["alembic"] = fake_alembic
    try:
        spec = importlib.util.spec_from_file_location("_mig_063", MIGRATION)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        getattr(mod, fn_name)()
    finally:
        if saved is None:
            del sys.modules["alembic"]
        else:
            sys.modules["alembic"] = saved
    return statements


def test_revision_chain() -> None:
    src = MIGRATION.read_text()
    assert 'revision: str = "063"' in src
    assert 'down_revision: str | None = "062"' in src


def test_upgrade_and_downgrade_render() -> None:
    up = "\n".join(_render("upgrade"))
    down = "\n".join(_render("downgrade"))
    assert "CREATE TABLE promotions" in up
    assert "DROP TABLE IF EXISTS promotions" in down


def _pg_bin() -> Path | None:
    for d in sorted(glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if Path(d, "initdb").exists() and Path(d, "postgres").exists():
            return Path(d)
    return None


class _Pg:
    def __init__(self, bindir: Path, sockdir: str) -> None:
        self.bindir = bindir
        self.sockdir = sockdir

    def sql(self, statement: str) -> str:
        res = subprocess.run(
            [
                str(self.bindir / "psql"),
                "-h",
                self.sockdir,
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-X",
                "-Atq",
                "-c",
                statement,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr)
        return res.stdout.strip()

    def migrate(self, fn_name: str) -> None:
        for stmt in _render(fn_name):
            self.sql(stmt)


_BASE_SCHEMA = """
CREATE TABLE tenants (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug varchar(50) NOT NULL);
CREATE TABLE knowledge_articles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), title varchar(300) NOT NULL,
    category varchar(50) NOT NULL, content text NOT NULL, active boolean NOT NULL DEFAULT true,
    tenant_id uuid REFERENCES tenants(id) ON DELETE SET NULL, promo_summary text);
CREATE TABLE admin_users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), username varchar(100) NOT NULL UNIQUE,
    role varchar(20) NOT NULL DEFAULT 'operator', permissions jsonb);
"""


@pytest.fixture(scope="module")
def pg():
    bindir = _pg_bin()
    if bindir is None:
        pytest.skip("PostgreSQL server binaries not installed")
    base = tempfile.mkdtemp(prefix="pg063")  # short: unix socket path ≤ 107 bytes
    data = f"{base}/d"
    subprocess.run(
        [str(bindir / "initdb"), "-D", data, "-U", "postgres", "-A", "trust", "--no-sync"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            str(bindir / "pg_ctl"),
            "-D",
            data,
            "-w",
            "-l",
            f"{base}/log",
            "-o",
            f"-c listen_addresses='' -k {base} -c fsync=off",
            "start",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield _Pg(bindir, base)
    finally:
        subprocess.run(
            [str(bindir / "pg_ctl"), "-D", data, "-m", "immediate", "stop"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def db(pg: _Pg):
    """Fresh base schema (pre-063) for every test."""
    pg.sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    pg.sql(_BASE_SCHEMA)
    pg.sql(f"INSERT INTO tenants (id, slug) VALUES ('{PK}', 'prokoleso'), ('{TS}', 'tvoya-shina')")
    return pg


_USERS = {
    "role_defaults": None,
    "explicit_none": "[]",
    "wildcard": '["*"]',
    "custom": '["knowledge:read", "sandbox:write", "analytics:read"]',
    "half": '["tenants:read", "promotions:read"]',
    "full": '["promotions:write", "tenants:read", "promotions:read"]',
    "json_null": "null",
}


def _seed_users(db: _Pg) -> None:
    for name, perms in _USERS.items():
        value = "NULL" if perms is None else f"'{perms}'::jsonb"
        db.sql(f"INSERT INTO admin_users (username, permissions) VALUES ('{name}', {value})")


def _perms(db: _Pg) -> dict[str, Any]:
    out = db.sql("SELECT json_object_agg(username, permissions) FROM admin_users")
    return json.loads(out)


def test_up_down_up(db: _Pg) -> None:
    db.migrate("upgrade")
    assert db.sql("SELECT to_regclass('promotions') IS NOT NULL") == "t"
    db.migrate("downgrade")
    assert db.sql("SELECT to_regclass('promotions') IS NULL") == "t"
    db.migrate("upgrade")
    assert db.sql("SELECT to_regclass('promotions') IS NOT NULL") == "t"


def test_permissions_appended_without_losing_the_list(db: _Pg) -> None:
    _seed_users(db)
    before = _perms(db)
    db.migrate("upgrade")
    after = _perms(db)
    new = {"promotions:read", "promotions:write"}

    # NULL (role defaults), [] (explicit none), ["*"] and JSON null: untouched.
    for name in ("role_defaults", "explicit_none", "wildcard", "json_null"):
        assert after[name] == before[name], name
    # Custom lists: old elements kept in order as a prefix, new ones added once.
    for name in ("custom", "half", "full"):
        assert after[name][: len(before[name])] == before[name], name
        assert new <= set(after[name]), name
        assert len(after[name]) == len(set(after[name])), name
    assert after["full"] == before["full"]


def test_permissions_upgrade_is_idempotent(db: _Pg) -> None:
    _seed_users(db)
    db.migrate("upgrade")
    once = _perms(db)
    for stmt in _render("upgrade"):
        if stmt.lstrip().startswith("UPDATE admin_users"):
            db.sql(stmt)
    assert _perms(db) == once


def test_downgrade_removes_only_the_new_permissions(db: _Pg) -> None:
    _seed_users(db)
    before = _perms(db)
    db.migrate("upgrade")
    db.migrate("downgrade")
    after = _perms(db)
    for name, perms in before.items():
        expected = (
            [p for p in perms if not p.startswith("promotions:")]
            if isinstance(perms, list)
            else perms
        )
        assert after[name] == expected, name


def _insert_promo(db: _Pg, **cols: str) -> None:
    base = {
        "tenant_id": f"'{PK}'",
        "title": "'T'",
        "bot_text": "'Текст.'",
        "valid_from": "'2026-09-28'",
        "valid_to": "'2026-12-31'",
    }
    base.update(cols)
    db.sql(f"INSERT INTO promotions ({', '.join(base)}) VALUES ({', '.join(base.values())})")


@pytest.mark.parametrize(
    "cols",
    [
        {"valid_to": "'2026-09-27'"},
        {"valid_to": "NULL"},
        {"valid_from": "NULL"},
        {"tenant_id": "NULL"},
        {"bot_text": "repeat('а', 601)"},
        {"bot_text": "'  '"},
        {"overrides": """'{"free_shipping": true}'"""},
        {"overrides": "'[]'"},
    ],
)
def test_table_rejects_invalid_rows(db: _Pg, cols: dict[str, str]) -> None:
    db.migrate("upgrade")
    with pytest.raises(RuntimeError):
        _insert_promo(db, **cols)


def test_table_accepts_a_full_row_and_rejects_duplicate_title(db: _Pg) -> None:
    db.migrate("upgrade")
    overrides = json.dumps(
        {
            "free_delivery": True,
            "extended_warranty_brands": ["matador"],
            "discount": True,
            "partner_service": {"service": "fitting", "network_label": "Твоя Шина"},
        },
        ensure_ascii=False,
    )
    _insert_promo(db, overrides=f"'{overrides}'", bot_text="repeat('а', 600)")
    with pytest.raises(RuntimeError):
        _insert_promo(db)
    _insert_promo(db, tenant_id=f"'{TS}'")  # same title, other network — allowed
    row = db.sql(f"SELECT active, mention_brands = '{{}}' FROM promotions WHERE tenant_id = '{PK}'")
    assert row == "t|t"


# --------------------------------------------------------------------------- #
# Transfer script
# --------------------------------------------------------------------------- #


def test_every_prod_title_matches_exactly_one_spec() -> None:
    for _tid, title in PROD_TITLES:
        norm = title.casefold()
        hits = [s for s in mp.KNOWN_PROMOTIONS if norm.startswith(s.title_prefix.casefold())]
        assert len(hits) == 1, title


def test_overrides_mapping() -> None:
    plan = {p.title: p for p in mp.build_plan(_articles(), set(), TODAY)}
    t = [title for _tid, title in PROD_TITLES]
    for title in [*t[:3], t[6]]:
        assert plan[title].overrides == {"free_delivery": True}, title
    assert plan[t[3]].overrides == {"extended_warranty_brands": ["matador"]}
    assert plan[t[4]].overrides == {
        "discount": True,
        "partner_service": {"service": "fitting", "network_label": "Твоя Шина"},
    }
    assert plan[t[5]].overrides == {"discount": True}
    assert plan[t[5]].mention_brands == ["bridgestone"]
    assert plan[t[7]].overrides == {"extended_warranty_brands": ["michelin"]}
    assert "торговому центрі" in plan[t[7]].bot_text


def test_known_plan_invariants() -> None:
    allowed = {"free_delivery", "extended_warranty_brands", "discount", "partner_service"}
    plan = mp.build_plan(_articles(), set(), TODAY)
    assert len(plan) == len(PROD_TITLES)
    for p in plan:
        assert p.action == "insert"
        assert p.warnings == []
        assert (p.valid_from, p.valid_to) == (TODAY, date(2026, 12, 31))
        assert set(p.overrides) <= allowed and p.overrides
        assert 0 < len(p.bot_text) <= mp.BOT_TEXT_MAX
        assert "http" not in p.bot_text and "**" not in p.bot_text and "#" not in p.bot_text
        # Stale periods from the articles must not reach the bot.
        assert "2026 року" not in p.bot_text and "лютого" not in p.bot_text
        assert all(b == b.lower() for b in p.mention_brands)
        for brand in p.overrides.get("extended_warranty_brands", []):
            assert brand in p.mention_brands


def test_spec_is_not_shared_between_rows() -> None:
    plan = mp.build_plan(_articles(), set(), TODAY)
    plan[0].overrides["discount"] = True
    plan[1].mention_brands.append("x")
    again = mp.build_plan(_articles(), set(), TODAY)
    assert "discount" not in again[0].overrides
    assert "x" not in again[1].mention_brands


def test_unknown_title_gets_empty_overrides_and_a_warning() -> None:
    art = {
        "id": "u1",
        "tenant_id": PK,
        "title": "Нова акція на диски",
        "promo_summary": None,
        "content": (
            "## Акція\n\n- **Знижка** 10% на [диски](https://prokoleso.ua/x). "
            "Деталі на www.prokoleso.ua. " + "Ще речення. " * 80
        ),
    }
    [p] = mp.build_plan([art], set(), TODAY)
    assert p.action == "insert"
    assert p.overrides == {} and p.mention_brands == []
    assert p.warnings
    assert 0 < len(p.bot_text) <= mp.BOT_TEXT_MAX
    for junk in ("http", "www.", "**", "##", "]("):
        assert junk not in p.bot_text
    assert p.bot_text.startswith("Акція Знижка 10% на диски.")


def test_article_without_tenant_is_skipped() -> None:
    art = dict(_articles()[0], tenant_id=None)
    [p] = mp.build_plan([art], set(), TODAY)
    assert p.action != "insert" and p.warnings


def test_existing_rows_are_skipped() -> None:
    existing = {(tid, title) for tid, title in PROD_TITLES[:5]}
    plan = mp.build_plan(_articles(), existing, TODAY)
    assert [p.action for p in plan] == ["exists"] * 5 + ["insert"] * 3


def test_refuses_to_plan_past_valid_to() -> None:
    with pytest.raises(ValueError):
        mp.build_plan(_articles(), set(), date(2027, 1, 1))


def _conn(existing: list[dict[str, str]]) -> MagicMock:
    conn = create_autospec(AsyncConnection, instance=True)

    def _result(rows: list[dict[str, Any]]) -> MagicMock:
        mappings = MagicMock(spec=["all"])
        mappings.all.return_value = rows
        res = MagicMock(spec=["mappings"])
        res.mappings.return_value = mappings
        return res

    async def execute(stmt: Any, params: Any = None) -> MagicMock:
        sql = str(stmt)
        if "FROM knowledge_articles" in sql:
            return _result(_articles())
        if "FROM promotions" in sql:
            return _result(existing)
        return _result([])

    conn.execute.side_effect = execute
    return conn


def _writes(conn: MagicMock) -> list[str]:
    return [
        str(c.args[0])
        for c in conn.execute.call_args_list
        if not str(c.args[0]).lstrip().upper().startswith("SELECT")
    ]


async def test_dry_run_writes_nothing() -> None:
    conn = _conn([])
    plan = await mp.run(conn, apply=False, today=TODAY)
    assert sum(p.action == "insert" for p in plan) == len(PROD_TITLES)
    assert _writes(conn) == []


async def test_apply_inserts_each_planned_row_once() -> None:
    conn = _conn([])
    await mp.run(conn, apply=True, today=TODAY)
    writes = _writes(conn)
    assert len(writes) == len(PROD_TITLES)
    assert all("ON CONFLICT (tenant_id, title) DO NOTHING" in w for w in writes)


async def test_apply_twice_inserts_nothing_the_second_time() -> None:
    existing = [{"tenant_id": tid, "title": title} for tid, title in PROD_TITLES]
    conn = _conn(existing)
    await mp.run(conn, apply=True, today=TODAY)
    assert _writes(conn) == []


def _render_insert(params: dict[str, Any]) -> str:
    """INSERT_SQL with psql literals — the statement text itself is the script's."""

    def lit(v: Any) -> str:
        if isinstance(v, list):
            return "ARRAY[" + ", ".join(lit(x) for x in v) + "]::text[]"
        return "'" + str(v).replace("'", "''") + "'"

    sql = mp.INSERT_SQL
    for key in sorted(params, key=len, reverse=True):
        sql = sql.replace(f":{key}", lit(params[key]))
    return sql


def test_insert_sql_runs_and_is_conflict_safe(db: _Pg) -> None:
    db.migrate("upgrade")
    plan = mp.build_plan(_articles(), set(), TODAY)
    for p in plan:
        # source_article_id must reference a real article.
        db.sql(
            "INSERT INTO knowledge_articles (id, title, category, content, tenant_id) "
            f"VALUES ('{p.article_id}', 'x{p.article_id}', 'promotions', 'c', '{p.tenant_id}')"
        )
    for _ in range(2):
        for p in plan:
            db.sql(_render_insert(p.insert_params()))
    assert db.sql("SELECT count(*) FROM promotions") == str(len(PROD_TITLES))
    got = json.loads(
        db.sql(
            "SELECT overrides FROM promotions WHERE title = "
            "'Ексклюзивне сервісне обслуговування на 3 роки'"
        )
    )
    assert got["partner_service"] == {"service": "fitting", "network_label": "Твоя Шина"}
