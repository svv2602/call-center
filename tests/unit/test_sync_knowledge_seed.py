"""Planning tests for scripts/sync_knowledge_seed.py — pure functions, no DB."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from scripts.sync_knowledge_seed import (
    CREATE,
    SET_TENANT,
    UNCHANGED,
    UPDATE_CONTENT,
    DbArticle,
    SeedArticle,
    SyncError,
    _apply,
    build_plan,
    load_manifest,
    load_seed,
    parse_manifest,
)

if TYPE_CHECKING:
    from pathlib import Path

TVOYA = "11111111-1111-1111-1111-111111111111"
PROKOLESO = "22222222-2222-2222-2222-222222222222"
TENANT_IDS = {"tvoya-shina": TVOYA, "prokoleso": PROKOLESO}

MANIFEST = {
    "fitting/01_fitting_a.md": "tvoya-shina",
    "delivery/07_delivery_prokoleso.md": "prokoleso",
    "delivery/01_delivery_shared.md": None,
}


def seed(
    rel: str, title: str, body: str = "Тіло статті.", category: str = "general"
) -> SeedArticle:
    return SeedArticle(
        rel_path=rel, title=title, category=category, body=body, raw=f"# {title}\n\n{body}"
    )


def row(
    title: str,
    content: str = "Тіло статті.",
    tenant: str | None = None,
    category: str = "general",
    aid: str = "a0",
) -> DbArticle:
    return DbArticle(
        id=aid, title=title, category=category, content=content, tenant_id=tenant, active=True
    )


def only(plan: Any) -> Any:
    assert len(plan.actions) == 1, plan.actions
    return plan.actions[0]


# ── manifest ────────────────────────────────────────────────────────────────


def test_parse_manifest_table_rows() -> None:
    text = (
        "| Файл | Тенант |\n|---|---|\n"
        "| `delivery/01_x.md` | shared |\n"
        "| `delivery/06_y.md` | `tvoya-shina` |\n"
        "| `delivery/07_z.md` | `prokoleso` |\n"
        "Итого: 22 shared\n"
    )
    assert parse_manifest(text) == {
        "delivery/01_x.md": None,
        "delivery/06_y.md": "tvoya-shina",
        "delivery/07_z.md": "prokoleso",
    }


def test_real_manifest_matches_its_own_totals() -> None:
    m = load_manifest("knowledge_seed")
    values = list(m.values())
    assert values.count(None) == 22
    assert values.count("tvoya-shina") == 10
    assert values.count("prokoleso") == 3
    assert all(m[p] == "tvoya-shina" for p in m if p.startswith("fitting/"))


def test_real_manifest_paths_all_exist_in_seed() -> None:
    paths = {s.rel_path for s in load_seed("knowledge_seed")}
    assert set(load_manifest("knowledge_seed")) <= paths
    assert "TENANTS.md" not in paths


def test_real_seed_bodies_have_no_h1() -> None:
    articles = load_seed("knowledge_seed")
    assert sum(a.rel_path.startswith("wheels/") for a in articles) == 8
    for a in articles:
        assert a.body
        assert not a.body.startswith("# "), a.rel_path


def test_missing_manifest_refuses(tmp_path: Path) -> None:
    (tmp_path / "faq").mkdir()
    (tmp_path / "faq" / "01_faq_x.md").write_text("# X\n\nbody", encoding="utf-8")
    with pytest.raises(SyncError, match=r"TENANTS\.md not found"):
        load_manifest(str(tmp_path))


def test_manifest_without_rows_refuses(tmp_path: Path) -> None:
    (tmp_path / "TENANTS.md").write_text("# Манифест\n\nничего\n", encoding="utf-8")
    with pytest.raises(SyncError):
        load_manifest(str(tmp_path))


def test_unknown_slug_refuses_before_planning() -> None:
    with pytest.raises(SyncError, match="prokoleso"):
        build_plan([], [], MANIFEST, {"tvoya-shina": TVOYA})


# ── plan ────────────────────────────────────────────────────────────────────


def test_new_network_article_is_created_with_its_tenant() -> None:
    s = seed("fitting/01_fitting_a.md", "Шиномонтаж", category="fitting")
    a = only(build_plan([s], [], MANIFEST, TENANT_IDS))
    assert a.kinds == [CREATE]
    assert a.new_tenant_id == TVOYA
    assert a.new_content == s.body


def test_slugs_are_not_crossed() -> None:
    s1 = seed("fitting/01_fitting_a.md", "A")
    s2 = seed("delivery/07_delivery_prokoleso.md", "B")
    plan = build_plan([s1, s2], [], MANIFEST, TENANT_IDS)
    tenants = {a.seed.rel_path: a.new_tenant_id for a in plan.actions}
    assert tenants == {
        "fitting/01_fitting_a.md": TVOYA,
        "delivery/07_delivery_prokoleso.md": PROKOLESO,
    }


def test_new_article_outside_manifest_is_shared() -> None:
    s = seed("wheels/01_wheels_x.md", "Диски", category="wheels")
    a = only(build_plan([s], [], MANIFEST, TENANT_IDS))
    assert a.kinds == [CREATE]
    assert a.new_tenant_id is None


def test_changed_text_is_updated_with_body() -> None:
    s = seed("faq/01_faq.md", "Питання", body="Новий текст.")
    a = only(build_plan([s], [row("Питання", "Старий текст.")], MANIFEST, TENANT_IDS))
    assert a.kinds == [UPDATE_CONTENT]
    assert a.new_content == "Новий текст."
    assert not a.set_tenant


@pytest.mark.parametrize(
    "stored", ["Тіло статті.", "\n Тіло статті.\n\n", "# Питання\n\nТіло статті.\n"]
)
def test_same_text_modulo_strip_or_legacy_h1_is_unchanged(stored: str) -> None:
    s = seed("faq/01_faq.md", "Питання")
    a = only(build_plan([s], [row("Питання", stored)], MANIFEST, TENANT_IDS))
    assert a.kinds == [UNCHANGED]
    assert not a.is_write


def test_wrong_network_tenant_is_corrected() -> None:
    s = seed("fitting/01_fitting_a.md", "Шиномонтаж", category="fitting")
    a = only(
        build_plan(
            [s], [row("Шиномонтаж", tenant=PROKOLESO, category="fitting")], MANIFEST, TENANT_IDS
        )
    )
    assert a.kinds == [SET_TENANT]
    assert a.new_tenant_id == TVOYA


def test_shared_fitting_article_gets_network_tenant() -> None:
    s = seed("fitting/01_fitting_a.md", "Шиномонтаж", category="fitting")
    a = only(
        build_plan([s], [row("Шиномонтаж", tenant=None, category="fitting")], MANIFEST, TENANT_IDS)
    )
    assert a.kinds == [SET_TENANT]
    assert a.new_tenant_id == TVOYA


def test_shared_manifest_article_with_tenant_is_reset_to_null() -> None:
    s = seed("delivery/01_delivery_shared.md", "Доставка", category="delivery")
    a = only(
        build_plan([s], [row("Доставка", tenant=TVOYA, category="delivery")], MANIFEST, TENANT_IDS)
    )
    assert a.kinds == [SET_TENANT]
    assert a.set_tenant is True
    assert a.new_tenant_id is None


def test_existing_article_outside_manifest_keeps_its_tenant() -> None:
    s = seed("faq/01_faq.md", "Питання")
    a = only(build_plan([s], [row("Питання", tenant=PROKOLESO)], MANIFEST, TENANT_IDS))
    assert a.kinds == [UNCHANGED]


def test_text_and_tenant_combine() -> None:
    s = seed("delivery/01_delivery_shared.md", "Доставка", body="Нове.", category="delivery")
    a = only(
        build_plan(
            [s],
            [row("Доставка", "Старе.", tenant=PROKOLESO, category="delivery")],
            MANIFEST,
            TENANT_IDS,
        )
    )
    assert a.kinds == [UPDATE_CONTENT, SET_TENANT]
    assert a.new_tenant_id is None


def test_title_matching_ignores_spacing_and_case() -> None:
    s = seed("faq/01_faq.md", "Як  обрати Шини")
    plan = build_plan([s], [row(" як обрати шини ")], MANIFEST, TENANT_IDS)
    assert only(plan).kinds == [UNCHANGED]
    assert any("spacing/case" in w for w in plan.warnings)


def test_ambiguous_title_is_reported_not_acted_on() -> None:
    s = seed("faq/01_faq.md", "Питання", body="Нове.")
    rows = [row("Питання", aid="a1"), row("питання ", aid="a2")]
    plan = build_plan([s], rows, MANIFEST, TENANT_IDS)
    assert plan.actions == []
    assert len(plan.ambiguous) == 1


def test_promotion_with_seed_title_is_never_touched() -> None:
    s = seed("delivery/01_delivery_shared.md", "Акція", body="Нове.")
    rows = [row("Акція", "Інше.", tenant=TVOYA, category="promotions")]
    plan = build_plan([s], rows, MANIFEST, TENANT_IDS)
    assert plan.actions == []
    assert len(plan.protected) == 1


def test_articles_outside_seed_are_only_reported() -> None:
    s = seed("faq/01_faq.md", "Питання")
    extra = row("Сторонняя статья", tenant=TVOYA, aid="x")
    promo = row("Знижка", category="promotions", aid="p")
    plan = build_plan([s], [row("Питання"), extra, promo], MANIFEST, TENANT_IDS)
    assert {r.id for r in plan.out_of_seed} == {"x", "p"}
    assert [a.db.id for a in plan.actions if a.db] == ["a0"]


# ── apply (hand-written fake connection, records SQL) ───────────────────────


class _Result:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount

    def scalar_one(self) -> str:
        return "new-id"


class _FakeConn:
    def __init__(self, rowcount: int = 1) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.rowcount = rowcount

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _Result:
        self.calls.append((str(stmt), params))
        return _Result(self.rowcount)


def test_apply_sets_pending_on_create_and_retext_but_not_tenant_only() -> None:
    s_new = seed("wheels/01_wheels_x.md", "Диски")
    s_txt = seed("faq/01_faq.md", "Питання", body="Нове.")
    s_ten = seed("delivery/01_delivery_shared.md", "Доставка")
    rows = [row("Питання", "Старе.", aid="t1"), row("Доставка", tenant=TVOYA, aid="t2")]
    plan = build_plan([s_new, s_txt, s_ten], rows, MANIFEST, TENANT_IDS)
    conn = _FakeConn()
    created, retexted = asyncio.run(_apply(conn, plan))
    assert created == ["new-id"]
    assert retexted == ["t1"]
    insert, upd_text, upd_tenant = (sql for sql, _ in conn.calls)
    assert "INSERT" in insert and "'pending'" in insert
    assert "embedding_status = 'pending'" in upd_text
    assert "embedding_status" not in upd_tenant
    assert conn.calls[2][1]["tenant_id"] is None
    assert all("category <> 'promotions'" in sql for sql, _ in conn.calls[1:])


def test_apply_fails_when_update_hits_no_row() -> None:
    s = seed("faq/01_faq.md", "Питання", body="Нове.")
    plan = build_plan([s], [row("Питання", "Старе.")], MANIFEST, TENANT_IDS)
    with pytest.raises(SyncError, match="touched 0 rows"):
        asyncio.run(_apply(_FakeConn(rowcount=0), plan))
