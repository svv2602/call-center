"""Tests for the runtime alias lookup helper (Wave 8 Phase 2).

Mocks sqlalchemy conn.execute to inject fake vehicle_aliases rows. Covers:
- unambiguous brand-only alias ("тойота")
- unambiguous brand+model alias ("дастер")
- cross-brand ambiguity (same alias, 2 brands)
- same-brand ambiguity (single brand, multiple models)
- normalization (case, ё→е, accents)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.vehicle_alias_lookup import (
    find_brand_by_alias,
    find_model_by_alias,
    resolve_by_alias,
)


def _mk_row(**kwargs):
    """SQLAlchemy Row-like object: has _mapping attr with dict-like access."""
    return SimpleNamespace(_mapping=kwargs)


def _mk_conn(rows: list[dict]) -> AsyncMock:
    """Fake conn whose execute() returns an iterable of Row-like objects."""
    conn = AsyncMock()
    row_objs = [_mk_row(**r) for r in rows]
    result_proxy = AsyncMock()
    result_proxy.__iter__ = lambda self: iter(row_objs)
    # execute is awaitable and returns the proxy
    conn.execute.return_value = row_objs  # will be iterated in resolve_by_alias
    return conn


class TestResolveByAlias:
    @pytest.mark.asyncio
    async def test_empty_utterance(self) -> None:
        """Empty or whitespace input short-circuits without DB call."""
        conn = AsyncMock()
        res = await resolve_by_alias(conn, "")
        assert res.brand_id is None
        assert res.model_id is None
        assert not res.ambiguous
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_match(self) -> None:
        """Alias not in table → empty ResolveResult."""
        conn = _mk_conn([])
        res = await resolve_by_alias(conn, "нонсенс")
        assert res.brand_id is None
        assert not res.ambiguous

    @pytest.mark.asyncio
    async def test_unambiguous_brand_only(self) -> None:
        """Single brand-only alias row → brand_id set, model_id None."""
        conn = _mk_conn([
            {"brand_id": 1, "brand_name": "Toyota", "model_id": None,
             "model_name": None, "source": "auto_translit"},
        ])
        res = await resolve_by_alias(conn, "Тойота")
        assert res.brand_id == 1
        assert res.brand_name == "Toyota"
        assert res.model_id is None
        assert not res.ambiguous

    @pytest.mark.asyncio
    async def test_unambiguous_brand_and_model(self) -> None:
        """Single (brand, model) alias → both IDs set."""
        conn = _mk_conn([
            {"brand_id": 2, "brand_name": "Renault", "model_id": 20,
             "model_name": "Duster", "source": "auto_translit"},
        ])
        res = await resolve_by_alias(conn, "Дастер")
        assert res.brand_id == 2
        assert res.brand_name == "Renault"
        assert res.model_id == 20
        assert res.model_name == "Duster"
        assert not res.ambiguous

    @pytest.mark.asyncio
    async def test_cross_brand_ambiguous(self) -> None:
        """Same alias, different brands → ambiguous=True, brand_id=None."""
        conn = _mk_conn([
            {"brand_id": 6, "brand_name": "Fiat", "model_id": 60,
             "model_name": "500", "source": "auto_model_name"},
            {"brand_id": 99, "brand_name": "OtherMake", "model_id": 999,
             "model_name": "500 Something", "source": "auto_model_name"},
        ])
        res = await resolve_by_alias(conn, "500")
        assert res.brand_id is None
        assert res.ambiguous is True
        assert len(res.ambiguous_matches or []) == 2

    @pytest.mark.asyncio
    async def test_same_brand_multiple_models_ambiguous(self) -> None:
        """Multiple models under one brand → brand only, mark ambiguous."""
        conn = _mk_conn([
            {"brand_id": 1, "brand_name": "Toyota", "model_id": 10,
             "model_name": "Camry", "source": "manual"},
            {"brand_id": 1, "brand_name": "Toyota", "model_id": 11,
             "model_name": "Corolla", "source": "manual"},
        ])
        res = await resolve_by_alias(conn, "камри-corolla")
        assert res.brand_id == 1
        assert res.brand_name == "Toyota"
        assert res.model_id is None  # can't decide which model
        assert res.ambiguous is True

    @pytest.mark.asyncio
    async def test_normalization_applied(self) -> None:
        """Uppercase / accent / ё variants should all normalize before query."""
        captured_norm = {}

        class Recorder:
            def __init__(self):
                self.execute = AsyncMock(side_effect=self._capture)
            async def _capture(self, stmt, params):
                captured_norm["value"] = params["norm"]
                return []

        conn = Recorder()
        await resolve_by_alias(conn, "  ТОЙо́ТА  ")
        # Trim + lower + strip combining acute → тойота
        assert captured_norm["value"] == "тойота"


class TestFindBrandByAlias:
    @pytest.mark.asyncio
    async def test_returns_dict_on_unambiguous_hit(self) -> None:
        conn = _mk_conn([
            {"brand_id": 3, "brand_name": "Volkswagen", "model_id": None,
             "model_name": None, "source": "auto_translit"},
        ])
        row = await find_brand_by_alias(conn, "Фольксваген")
        assert row == {"id": 3, "name": "Volkswagen"}

    @pytest.mark.asyncio
    async def test_returns_none_on_no_match(self) -> None:
        conn = _mk_conn([])
        row = await find_brand_by_alias(conn, "неизвестно")
        assert row is None

    @pytest.mark.asyncio
    async def test_returns_none_on_ambiguity(self) -> None:
        """Ambiguous → None so caller falls back to fuzzy."""
        conn = _mk_conn([
            {"brand_id": 6, "brand_name": "Fiat", "model_id": 60,
             "model_name": "500", "source": "auto_model_name"},
            {"brand_id": 99, "brand_name": "X", "model_id": 999,
             "model_name": "500", "source": "auto_model_name"},
        ])
        row = await find_brand_by_alias(conn, "500")
        assert row is None
