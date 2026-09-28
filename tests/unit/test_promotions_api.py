"""Admin API «Акції» — ``/admin/promotions`` (table from migration 063).

Invariants:
- the write path is strict: a promotion without ``valid_to``, with
  ``valid_to < valid_from``, a blank or >600-char ``bot_text``, an unknown or
  mis-shaped ``overrides`` key, or a missing network is a 422 and nothing is
  written;
- brands are stored lowercase;
- every role gets ``promotions:read`` / ``promotions:write`` by default, and the
  endpoints actually check them (an explicit empty permission list is a 403);
- every successful write bumps ``PROMOS_CACHE_REDIS_KEY`` — the key the call
  processor's promotions cache reads — and a refused write does not;
- the router is wired into the real application;
- «створити з статті» never invents dates: they come from the admin.
"""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.network_policy import SERVICE_LABELS
from src.agent.prompt_manager import PROMOS_CACHE_REDIS_KEY
from src.api.auth import create_jwt, resolve_permissions
from src.api.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_GROUPS,
    ROLE_DEFAULT_PERMISSIONS,
)
from src.api.promotions import (
    BOT_TEXT_MAX,
    DRAFT_BOT_TEXT_MAX,
    KYIV_TZ,
    draft_bot_text,
    router,
)

_SECRET = "test-secret"
_TENANT = str(uuid4())
_OTHER_TENANT = str(uuid4())


def _token(role: str = "admin", user_id: str | None = None) -> dict[str, str]:
    payload: dict[str, Any] = {"sub": "u", "role": role}
    if user_id:
        payload["user_id"] = user_id
    return {"Authorization": f"Bearer {create_jwt(payload, _SECRET)}"}


# ─── Fake database ────────────────────────────────────────


class _Row:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping

    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__["_mapping"][name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def first(self) -> _Row | None:
        return _Row(self._rows[0]) if self._rows else None

    def __iter__(self) -> Any:
        return iter(_Row(r) for r in self._rows)


# The status filters the API may emit, evaluated here in Python. A changed
# condition string is not recognised and fails the test loudly.
_CONDITIONS = {
    "tenant_id = CAST(:tenant_id AS uuid)": lambda r, p: str(r["tenant_id"]) == p["tenant_id"],
    "active AND valid_from <= :today AND valid_to >= :today": lambda r, p: (
        r["active"] and r["valid_from"] <= p["today"] <= r["valid_to"]
    ),
    "active AND valid_from > :today": lambda r, p: r["active"] and r["valid_from"] > p["today"],
    "valid_to < :today": lambda r, p: r["valid_to"] < p["today"],
}


class _DB:
    def __init__(self) -> None:
        self.tenants = {_TENANT, _OTHER_TENANT}
        self.articles: dict[str, dict[str, Any]] = {}
        self.promotions: dict[str, dict[str, Any]] = {}
        self.statements: list[str] = []

    def add(self, **fields: Any) -> dict[str, Any]:
        now = datetime.now(KYIV_TZ)
        row = {
            "id": uuid4(),
            "tenant_id": UUID(_TENANT),
            "title": f"t-{len(self.promotions)}",
            "bot_text": "текст",
            "valid_from": date(2026, 1, 1),
            "valid_to": date(2026, 12, 31),
            "overrides": {},
            "mention_brands": [],
            "active": True,
            "source_article_id": None,
            "created_by": None,
            "created_at": now,
            "updated_at": now,
            **fields,
        }
        self.promotions[str(row["id"])] = row
        return row

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = " ".join(str(clause).split())
        p = dict(params or {})
        self.statements.append(sql)
        if sql.startswith("SELECT id FROM tenants"):
            return _Result([{"id": p["id"]}] if p["id"] in self.tenants else [])
        if "FROM knowledge_articles" in sql:
            art = self.articles.get(p["id"])
            return _Result([art] if art else [])
        if sql.startswith("INSERT INTO promotions"):
            if any(
                str(r["tenant_id"]) == p["tenant_id"] and r["title"] == p["title"]
                for r in self.promotions.values()
            ):
                msg = "duplicate key value violates unique constraint"
                raise RuntimeError(msg)

            row = self.add(
                tenant_id=UUID(p["tenant_id"]),
                title=p["title"],
                bot_text=p["bot_text"],
                valid_from=p["valid_from"],
                valid_to=p["valid_to"],
                overrides=json.loads(p["overrides"]),
                mention_brands=p["mention_brands"],
                active=p["active"],
                source_article_id=UUID(p["source_article_id"]) if p["source_article_id"] else None,
                created_by=UUID(p["created_by"]) if p["created_by"] else None,
            )
            return _Result([row])
        if sql.startswith("SELECT valid_from, valid_to FROM promotions"):
            row = self.promotions.get(p["id"])
            return _Result([row] if row else [])
        if sql.startswith("UPDATE promotions"):
            row = self.promotions.get(p["id"])
            if row is None:
                return _Result([])
            sets = re.search(r"SET (.*) WHERE", sql)
            assert sets is not None
            for part in sets.group(1).split(", "):
                key = part.split(" = ")[0]
                if key == "updated_at":
                    assert part == "updated_at = now()"
                    row["updated_at"] = datetime.now(KYIV_TZ) + timedelta(seconds=1)
                elif key == "overrides":
                    row[key] = json.loads(p[key])
                elif key == "tenant_id":
                    row[key] = UUID(p[key])
                else:
                    row[key] = p[key]
            return _Result([row])
        if sql.startswith("DELETE FROM promotions"):
            row = self.promotions.pop(p["id"], None)
            return _Result([{"id": row["id"]}] if row else [])
        if sql.startswith("SELECT") and "FROM promotions" in sql:
            if "WHERE id =" in sql:
                row = self.promotions.get(p["id"])
                return _Result([row] if row else [])
            where = re.search(r"FROM promotions (?:WHERE (.*) )?ORDER BY", sql)
            assert where is not None
            rows = list(self.promotions.values())
            if where.group(1):
                clauses = _split_conditions(where.group(1))
                for cond in clauses:
                    assert cond in _CONDITIONS, f"unrecognised condition: {cond}"
                    rows = [r for r in rows if _CONDITIONS[cond](r, p)]
            return _Result(rows)
        msg = f"unexpected SQL: {sql}"
        raise AssertionError(msg)

    def engine(self) -> Any:
        engine = MagicMock(spec=["begin"])

        @asynccontextmanager
        async def _begin() -> Any:
            yield self

        engine.begin = _begin
        return engine


def _split_conditions(where: str) -> list[str]:
    out: list[str] = []
    rest = where
    while rest:
        for cond in sorted(_CONDITIONS, key=len, reverse=True):
            if rest.startswith(cond):
                out.append(cond)
                rest = rest[len(cond) :]
                rest = rest.removeprefix(" AND ")
                break
        else:
            out.append(rest)
            break
    return out


# ─── Harness ──────────────────────────────────────────────


@pytest.fixture()
def db() -> _DB:
    return _DB()


@pytest.fixture()
def redis() -> Any:
    r = MagicMock(spec=["set"])
    r.set = AsyncMock()
    return r


@pytest.fixture()
def call(db: _DB, redis: Any) -> Any:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    def _call(
        method: str,
        path: str,
        *,
        role: str = "admin",
        user_id: str | None = None,
        perms: list[str] | None = None,
        **kw: Any,
    ) -> Any:
        with (
            patch("src.api.auth.get_settings") as settings,
            patch(
                "src.api.auth._load_user_permissions", new_callable=AsyncMock, return_value=perms
            ),
            patch("src.api.promotions._get_engine", new_callable=AsyncMock) as get_engine,
            patch("src.api.promotions._get_redis", new_callable=AsyncMock, return_value=redis),
        ):
            settings.return_value.admin.jwt_secret = _SECRET
            get_engine.return_value = db.engine()
            return client.request(
                method, f"/admin/promotions{path}", headers=_token(role, user_id), **kw
            )

    return _call


def _body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "tenant_id": _TENANT,
        "title": "Зимова знижка",
        "bot_text": "До кінця року — безкоштовна доставка комплекту шин.",
        "valid_from": "2026-10-01",
        "valid_to": "2026-12-31",
    }
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not ...}


# ─── Validation ───────────────────────────────────────────


def test_valid_promotion_is_created_and_invalidates_the_cache(
    call: Any, db: _DB, redis: Any
) -> None:
    resp = call("POST", "", json=_body(mention_brands=["Matador", " matador ", "Kormoran"]))
    assert resp.status_code == 200, resp.text
    promo = resp.json()["promotion"]
    assert promo["valid_to"] == "2026-12-31"
    assert promo["mention_brands"] == ["matador", "kormoran"]
    assert len(db.promotions) == 1
    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[0] == PROMOS_CACHE_REDIS_KEY


@pytest.mark.parametrize(
    "bad",
    [
        {"valid_to": ...},
        {"valid_to": None},
        {"valid_from": ...},
        {"valid_from": "2026-12-31", "valid_to": "2026-12-30"},
        {"bot_text": "   "},
        {"bot_text": "б" * (BOT_TEXT_MAX + 1)},
        {"title": "  "},
        {"tenant_id": ...},
        {"overrides": {"cashback": True}},
        {"overrides": {"free_delivery": True, "gift": "wipers"}},
        {"overrides": {"free_delivery": "yes"}},
        {"overrides": {"discount": 1}},
        {"overrides": {"extended_warranty_brands": "premiorri"}},
        {"overrides": {"partner_service": {"service": "car_wash", "network_label": "X"}}},
        {"overrides": {"partner_service": {"service": "fitting"}}},
        {"overrides": {"partner_service": {"service": "fitting", "network_label": " "}}},
        {"overrides": []},
        {"mention_brands": "matador"},
    ],
    ids=repr,
)
def test_invalid_promotion_is_422_and_nothing_is_written(
    call: Any, db: _DB, redis: Any, bad: dict[str, Any]
) -> None:
    resp = call("POST", "", json=_body(**bad))
    assert resp.status_code == 422, resp.text
    assert db.promotions == {}
    redis.set.assert_not_awaited()


def test_bot_text_of_exactly_600_is_accepted(call: Any) -> None:
    resp = call("POST", "", json=_body(bot_text="б" * BOT_TEXT_MAX))
    assert resp.status_code == 200, resp.text


def test_every_known_override_is_accepted_and_normalised(call: Any) -> None:
    service = sorted(SERVICE_LABELS)[0]
    overrides = {
        "free_delivery": True,
        "discount": False,
        "extended_warranty_brands": ["Premiorri"],
        "partner_service": {"service": service, "network_label": " Партнер "},
    }
    resp = call("POST", "", json=_body(overrides=overrides))
    assert resp.status_code == 200, resp.text
    stored = resp.json()["promotion"]["overrides"]
    assert stored["extended_warranty_brands"] == ["premiorri"]
    assert stored["partner_service"] == {"service": service, "network_label": "Партнер"}
    assert set(stored) == set(overrides)


def test_unknown_tenant_is_422(call: Any, db: _DB, redis: Any) -> None:
    resp = call("POST", "", json=_body(tenant_id=str(uuid4())))
    assert resp.status_code == 422
    assert db.promotions == {}
    redis.set.assert_not_awaited()


def test_duplicate_title_in_a_network_is_409(call: Any, redis: Any) -> None:
    assert call("POST", "", json=_body()).status_code == 200
    redis.set.reset_mock()
    resp = call("POST", "", json=_body())
    assert resp.status_code == 409
    redis.set.assert_not_awaited()


def test_created_by_comes_from_the_jwt(call: Any) -> None:
    user_id = str(uuid4())
    resp = call("POST", "", json=_body(), role="operator", user_id=user_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["promotion"]["created_by"] == user_id


# ─── Update / delete ──────────────────────────────────────


def test_patch_updates_fields_bumps_updated_at_and_the_cache(
    call: Any, db: _DB, redis: Any
) -> None:
    row = db.add()
    before = row["updated_at"]
    resp = call("PATCH", f"/{row['id']}", json={"bot_text": "Новий текст", "active": False})
    assert resp.status_code == 200, resp.text
    promo = resp.json()["promotion"]
    assert promo["bot_text"] == "Новий текст"
    assert promo["active"] is False
    assert row["updated_at"] > before
    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[0] == PROMOS_CACHE_REDIS_KEY


@pytest.mark.parametrize(
    "bad",
    [
        {"valid_to": "2025-12-31"},  # before the stored valid_from
        {"valid_to": None},
        {"bot_text": ""},
        {"overrides": {"cashback": True}},
        {"tenant_id": str(uuid4())},
    ],
    ids=repr,
)
def test_invalid_patch_is_422_and_the_row_is_unchanged(
    call: Any, db: _DB, redis: Any, bad: dict[str, Any]
) -> None:
    row = db.add()
    snapshot = dict(row)
    resp = call("PATCH", f"/{row['id']}", json=bad)
    assert resp.status_code == 422, resp.text
    assert row == snapshot
    redis.set.assert_not_awaited()


def test_patch_and_delete_of_a_missing_promotion_are_404(call: Any, redis: Any) -> None:
    assert call("PATCH", f"/{uuid4()}", json={"active": False}).status_code == 404
    assert call("DELETE", f"/{uuid4()}").status_code == 404
    redis.set.assert_not_awaited()


def test_delete_removes_the_row_and_invalidates_the_cache(call: Any, db: _DB, redis: Any) -> None:
    row = db.add()
    resp = call("DELETE", f"/{row['id']}")
    assert resp.status_code == 200
    assert db.promotions == {}
    redis.set.assert_awaited_once()
    assert redis.set.await_args.args[0] == PROMOS_CACHE_REDIS_KEY


# ─── Read ─────────────────────────────────────────────────


def test_get_serialises_native_types(call: Any, db: _DB) -> None:
    row = db.add(mention_brands=["matador"])
    resp = call("GET", f"/{row['id']}")
    assert resp.status_code == 200
    promo = resp.json()["promotion"]
    assert promo["id"] == str(row["id"])
    assert promo["valid_from"] == row["valid_from"].isoformat()
    assert call("GET", f"/{uuid4()}").status_code == 404


def test_status_filter_follows_kyiv_dates_and_active(call: Any, db: _DB) -> None:
    today = datetime.now(KYIV_TZ).date()
    day = timedelta(days=1)
    rows = {
        "today_only": db.add(valid_from=today, valid_to=today),
        "running": db.add(valid_from=today - day, valid_to=today + day),
        "tomorrow": db.add(valid_from=today + day, valid_to=today + 5 * day),
        "ended_yesterday": db.add(valid_from=today - 5 * day, valid_to=today - day),
        "disabled_running": db.add(valid_from=today - day, valid_to=today + day, active=False),
        "other_network": db.add(tenant_id=UUID(_OTHER_TENANT), valid_from=today, valid_to=today),
    }
    names = {str(r["id"]): n for n, r in rows.items()}

    def listed(**params: str) -> set[str]:
        resp = call("GET", "", params=params)
        assert resp.status_code == 200, resp.text
        return {names[i["id"]] for i in resp.json()["items"]}

    assert listed(status="active") == {"today_only", "running", "other_network"}
    assert listed(status="upcoming") == {"tomorrow"}
    assert listed(status="expired") == {"ended_yesterday"}
    assert listed(status="all") == set(rows)
    assert listed(status="active", tenant_id=_OTHER_TENANT) == {"other_network"}
    assert call("GET", "", params={"status": "soon"}).status_code == 422


# ─── From article ─────────────────────────────────────────


def test_from_article_takes_title_text_and_network_but_not_dates(
    call: Any, db: _DB, redis: Any
) -> None:
    art_id = str(uuid4())
    db.articles[art_id] = {
        "id": art_id,
        "title": "Акція Matador",
        "content": "## Умови\nЗнижка 10% на [шини Matador](https://x.ua/m). Деталі: https://x.ua",
        "promo_summary": None,
        "tenant_id": UUID(_OTHER_TENANT),
    }
    no_dates = call("POST", f"/from-article/{art_id}", json={})
    assert no_dates.status_code == 422
    assert "valid_to" in no_dates.text
    assert db.promotions == {}

    resp = call(
        "POST",
        f"/from-article/{art_id}",
        json={"valid_from": "2026-10-01", "valid_to": "2026-12-31"},
    )
    assert resp.status_code == 200, resp.text
    promo = resp.json()["promotion"]
    assert promo["title"] == "Акція Matador"
    assert promo["tenant_id"] == _OTHER_TENANT
    assert promo["source_article_id"] == art_id
    assert (promo["valid_from"], promo["valid_to"]) == ("2026-10-01", "2026-12-31")
    assert "http" not in promo["bot_text"]
    assert "шини Matador" in promo["bot_text"]
    redis.set.assert_awaited_once()


def test_from_article_prefers_promo_summary(call: Any, db: _DB) -> None:
    art_id = str(uuid4())
    db.articles[art_id] = {
        "id": art_id,
        "title": "A",
        "content": "довгий текст " * 200,
        "promo_summary": "Коротко: безкоштовна доставка.",
        "tenant_id": UUID(_TENANT),
    }
    resp = call(
        "POST",
        f"/from-article/{art_id}",
        json={"valid_from": "2026-10-01", "valid_to": "2026-10-02"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["promotion"]["bot_text"] == "Коротко: безкоштовна доставка."


def test_from_shared_article_needs_a_network(call: Any, db: _DB) -> None:
    art_id = str(uuid4())
    db.articles[art_id] = {
        "id": art_id,
        "title": "A",
        "content": "текст",
        "promo_summary": None,
        "tenant_id": None,
    }
    dates = {"valid_from": "2026-10-01", "valid_to": "2026-10-02"}
    assert call("POST", f"/from-article/{art_id}", json=dates).status_code == 422
    resp = call("POST", f"/from-article/{art_id}", json={**dates, "tenant_id": _TENANT})
    assert resp.status_code == 200, resp.text
    assert call("POST", f"/from-article/{uuid4()}", json=dates).status_code == 404


def test_draft_bot_text_is_bounded_and_link_free() -> None:
    content = ("Слово [посилання](https://example.com/a) www.site.ua " * 60).strip()
    draft = draft_bot_text(content)
    assert len(draft) <= DRAFT_BOT_TEXT_MAX
    assert "http" not in draft
    assert "www." not in draft
    assert "](" not in draft


# ─── Permissions ──────────────────────────────────────────


@pytest.mark.parametrize("role", sorted(ROLE_DEFAULT_PERMISSIONS))
def test_every_role_reads_and_writes_promotions_by_default(role: str, call: Any) -> None:
    effective = resolve_permissions(role, None)
    for perm in ("promotions:read", "promotions:write"):
        assert "*" in effective or perm in effective, (role, perm)
    assert call("GET", "", role=role).status_code == 200
    assert call("POST", "", role=role, json=_body(title=f"t-{role}")).status_code == 200


def test_permissions_are_registered() -> None:
    assert {"promotions:read", "promotions:write"} <= set(ALL_PERMISSIONS)
    assert PERMISSION_GROUPS["promotions"] == ["promotions:read", "promotions:write"]


def test_endpoints_check_the_promotions_permissions(call: Any, db: _DB) -> None:
    row = db.add()
    uid = str(uuid4())
    # An explicit empty custom list means «no permissions».
    assert call("GET", "", role="operator", user_id=uid, perms=[]).status_code == 403
    assert call("GET", f"/{row['id']}", role="operator", user_id=uid, perms=[]).status_code == 403
    read_only = {"role": "operator", "user_id": uid, "perms": ["promotions:read"]}
    assert call("GET", "", **read_only).status_code == 200
    assert call("POST", "", json=_body(), **read_only).status_code == 403
    assert call("PATCH", f"/{row['id']}", json={"active": False}, **read_only).status_code == 403
    assert call("DELETE", f"/{row['id']}", **read_only).status_code == 403
    assert (
        call(
            "POST",
            f"/from-article/{uuid4()}",
            json={"valid_from": "2026-10-01", "valid_to": "2026-10-02"},
            **read_only,
        ).status_code
        == 403
    )
    assert row["id"] and str(row["id"]) in db.promotions


# ─── Wiring ───────────────────────────────────────────────


def test_router_is_mounted_in_the_application() -> None:
    from src.main import app

    paths = app.openapi()["paths"]
    assert "/admin/promotions" in paths
    assert "/admin/promotions/{promotion_id}" in paths
    assert "/admin/promotions/from-article/{article_id}" in paths
    assert {"get", "post"} <= set(paths["/admin/promotions"])
    assert {"get", "patch", "delete"} <= set(paths["/admin/promotions/{promotion_id}"])
