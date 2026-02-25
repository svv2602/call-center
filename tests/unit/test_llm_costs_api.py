"""Tests for LLM cost analysis API endpoints."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.llm_costs import router


async def _fake_require_admin(*_args: object, **_kwargs: object) -> dict[str, Any]:
    return {"sub": "test-user", "role": "admin"}


def _make_mock_engine(rows=None, rowcount=1):
    """Create a mock async engine that returns predefined rows."""
    mock_conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = rowcount

    if rows is not None:
        mock_result.__iter__ = lambda self: iter(rows)
        mock_result.fetchall = MagicMock(return_value=rows)
        mock_result.first = MagicMock(return_value=rows[0] if rows else None)
    else:
        mock_result.__iter__ = lambda self: iter([])
        mock_result.fetchall = MagicMock(return_value=[])
        mock_result.first = MagicMock(return_value=None)

    mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=mock_ctx)

    return mock_engine, mock_conn


def _row(**kwargs):
    """Create a mock row with attribute access."""
    obj = MagicMock()
    for k, v in kwargs.items():
        setattr(obj, k, v)
    return obj


@pytest.fixture()
def app():
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


SAMPLE_PRICING_ROW = _row(
    id=uuid4(),
    provider_key="gemini-flash",
    model_name="gemini-2.5-flash",
    display_name="Gemini 2.5 Flash",
    input_price_per_1m=0.30,
    output_price_per_1m=2.50,
    is_system=True,
    provider_type="gemini",
    include_in_comparison=True,
    catalog_model_key=None,
    created_at=None,
    updated_at=None,
)


class TestListPricing:
    @pytest.mark.asyncio()
    async def test_returns_pricing_list(self, app: Any) -> None:
        engine, _ = _make_mock_engine(rows=[SAMPLE_PRICING_ROW])

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/pricing")

        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["provider_key"] == "gemini-flash"
        assert item["input_price_per_1m"] == 0.30
        assert item["is_system"] is True
        assert item["include_in_comparison"] is True
        assert item["provider_type"] == "gemini"


class TestCreatePricing:
    @pytest.mark.asyncio()
    async def test_creates_custom_pricing(self, app: Any) -> None:
        new_id = uuid4()
        # First call: duplicate check (returns None), Second call: INSERT (returns id)
        engine, mock_conn = _make_mock_engine()

        dup_result = MagicMock()
        dup_result.first = MagicMock(return_value=None)

        insert_result = MagicMock()
        insert_result.first = MagicMock(return_value=_row(id=new_id))

        mock_conn.execute = AsyncMock(side_effect=[dup_result, insert_result])

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/admin/llm-costs/pricing",
                    json={
                        "provider_key": "my-custom",
                        "model_name": "custom-model",
                        "display_name": "My Custom",
                        "input_price_per_1m": 1.0,
                        "output_price_per_1m": 2.0,
                    },
                )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == str(new_id)

    @pytest.mark.asyncio()
    async def test_rejects_duplicate_provider_key(self, app: Any) -> None:
        engine, mock_conn = _make_mock_engine()

        dup_result = MagicMock()
        dup_result.first = MagicMock(return_value=_row(id=uuid4()))
        mock_conn.execute = AsyncMock(return_value=dup_result)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/admin/llm-costs/pricing",
                    json={
                        "provider_key": "gemini-flash",
                        "model_name": "gemini-2.5-flash",
                        "display_name": "Gemini 2.5 Flash",
                        "input_price_per_1m": 0.30,
                        "output_price_per_1m": 2.50,
                    },
                )

        assert resp.status_code == 409


class TestUpdatePricing:
    @pytest.mark.asyncio()
    async def test_updates_pricing(self, app: Any) -> None:
        pid = uuid4()
        engine, _ = _make_mock_engine(rowcount=1)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.patch(
                    f"/admin/llm-costs/pricing/{pid}",
                    json={"input_price_per_1m": 0.50},
                )

        assert resp.status_code == 200

    @pytest.mark.asyncio()
    async def test_updates_include_in_comparison(self, app: Any) -> None:
        pid = uuid4()
        engine, _ = _make_mock_engine(rowcount=1)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.patch(
                    f"/admin/llm-costs/pricing/{pid}",
                    json={"include_in_comparison": False},
                )

        assert resp.status_code == 200

    @pytest.mark.asyncio()
    async def test_404_on_missing(self, app: Any) -> None:
        pid = uuid4()
        engine, _ = _make_mock_engine(rowcount=0)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.patch(
                    f"/admin/llm-costs/pricing/{pid}",
                    json={"input_price_per_1m": 0.50},
                )

        assert resp.status_code == 404

    @pytest.mark.asyncio()
    async def test_400_on_empty_body(self, app: Any) -> None:
        pid = uuid4()
        engine, _ = _make_mock_engine()

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.patch(f"/admin/llm-costs/pricing/{pid}", json={})

        assert resp.status_code == 400


class TestDeletePricing:
    @pytest.mark.asyncio()
    async def test_deletes_custom_pricing(self, app: Any) -> None:
        pid = uuid4()
        engine, mock_conn = _make_mock_engine()

        select_result = MagicMock()
        select_result.first = MagicMock(return_value=_row(is_system=False))
        delete_result = MagicMock()

        mock_conn.execute = AsyncMock(side_effect=[select_result, delete_result])

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.delete(f"/admin/llm-costs/pricing/{pid}")

        assert resp.status_code == 200

    @pytest.mark.asyncio()
    async def test_forbids_deleting_system_pricing(self, app: Any) -> None:
        pid = uuid4()
        engine, mock_conn = _make_mock_engine()

        select_result = MagicMock()
        select_result.first = MagicMock(return_value=_row(is_system=True))
        mock_conn.execute = AsyncMock(return_value=select_result)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.delete(f"/admin/llm-costs/pricing/{pid}")

        assert resp.status_code == 403

    @pytest.mark.asyncio()
    async def test_404_on_missing(self, app: Any) -> None:
        pid = uuid4()
        engine, mock_conn = _make_mock_engine()

        select_result = MagicMock()
        select_result.first = MagicMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=select_result)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.delete(f"/admin/llm-costs/pricing/{pid}")

        assert resp.status_code == 404


class TestSyncSystemPricing:
    @pytest.mark.asyncio()
    async def test_syncs_all_providers(self, app: Any) -> None:
        engine, _ = _make_mock_engine()

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/admin/llm-costs/pricing/sync-system")

        assert resp.status_code == 200
        data = resp.json()
        assert "Synced" in data["message"]
        # Should sync all 8 providers from DEFAULT_ROUTING_CONFIG
        assert "8" in data["message"]


class TestUsageSummary:
    @pytest.mark.asyncio()
    async def test_returns_summary(self, app: Any) -> None:
        rows = [
            _row(
                task_type="agent",
                provider_key="gemini-flash",
                call_count=100,
                total_input_tokens=5000000,
                total_output_tokens=800000,
                avg_latency_ms=250.0,
                total_cost=3.50,
            )
        ]
        engine, _ = _make_mock_engine(rows=rows)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/usage/summary?date_from=2026-02-01&date_to=2026-02-25")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["task_type"] == "agent"
        assert data["items"][0]["total_cost"] == 3.50

    @pytest.mark.asyncio()
    async def test_empty_result(self, app: Any) -> None:
        engine, _ = _make_mock_engine(rows=[])

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/usage/summary")

        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestModelComparison:
    @pytest.mark.asyncio()
    async def test_returns_comparison(self, app: Any) -> None:
        usage_rows = [
            _row(
                actual_provider="gemini-flash",
                total_input_tokens=1000000,
                total_output_tokens=200000,
                call_count=50,
            )
        ]
        pricing_rows = [
            _row(
                provider_key="gemini-flash",
                display_name="Gemini 2.5 Flash",
                input_price_per_1m=0.30,
                output_price_per_1m=2.50,
            ),
            _row(
                provider_key="anthropic-sonnet",
                display_name="Claude Sonnet 4.5",
                input_price_per_1m=3.00,
                output_price_per_1m=15.00,
            ),
        ]
        all_pricing_rows = [
            _row(provider_key="gemini-flash", input_price_per_1m=0.30, output_price_per_1m=2.50),
            _row(provider_key="anthropic-sonnet", input_price_per_1m=3.00, output_price_per_1m=15.00),
        ]

        engine, mock_conn = _make_mock_engine()

        agg_result = MagicMock()
        agg_result.fetchall = MagicMock(return_value=usage_rows)

        pricing_result = MagicMock()
        pricing_result.fetchall = MagicMock(return_value=pricing_rows)

        all_pricing_result = MagicMock()
        all_pricing_result.fetchall = MagicMock(return_value=all_pricing_rows)

        mock_conn.execute = AsyncMock(
            side_effect=[agg_result, pricing_result, all_pricing_result]
        )

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get(
                    "/admin/llm-costs/usage/model-comparison?task_type=agent"
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["actual_provider"] == "gemini-flash"
        assert data["total_input_tokens"] == 1000000
        assert len(data["comparisons"]) == 2

        # Gemini should be cheaper than Sonnet
        gemini = next(c for c in data["comparisons"] if c["provider_key"] == "gemini-flash")
        sonnet = next(c for c in data["comparisons"] if c["provider_key"] == "anthropic-sonnet")
        assert gemini["cost"] < sonnet["cost"]
        assert gemini["is_actual"] is True

    @pytest.mark.asyncio()
    async def test_empty_usage(self, app: Any) -> None:
        engine, mock_conn = _make_mock_engine()

        agg_result = MagicMock()
        agg_result.fetchall = MagicMock(return_value=[])

        mock_conn.execute = AsyncMock(return_value=agg_result)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/usage/model-comparison")

        assert resp.status_code == 200
        data = resp.json()
        assert data["comparisons"] == []
        assert data["actual_cost"] == 0


class TestCatalogList:
    @pytest.mark.asyncio()
    async def test_returns_catalog(self, app: Any) -> None:
        from datetime import UTC, datetime

        rows = [
            _row(
                model_key="gpt-5-mini",
                provider_type="openai",
                display_name="GPT 5 Mini",
                input_price_per_1m=0.25,
                output_price_per_1m=2.00,
                max_input_tokens=1000000,
                max_output_tokens=100000,
                is_new=False,
                synced_at=datetime(2026, 2, 25, tzinfo=UTC),
                is_added=True,
            )
        ]
        engine, _ = _make_mock_engine(rows=rows)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/catalog?provider_type=openai")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["model_key"] == "gpt-5-mini"
        assert item["is_added"] is True

    @pytest.mark.asyncio()
    async def test_catalog_with_search(self, app: Any) -> None:
        engine, _ = _make_mock_engine(rows=[])

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/catalog?search=gpt")

        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestCatalogNewCount:
    @pytest.mark.asyncio()
    async def test_returns_count(self, app: Any) -> None:
        engine, mock_conn = _make_mock_engine()

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=5)
        mock_conn.execute = AsyncMock(return_value=count_result)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/catalog/new-count")

        assert resp.status_code == 200
        assert resp.json()["count"] == 5

    @pytest.mark.asyncio()
    async def test_returns_zero_when_empty(self, app: Any) -> None:
        engine, mock_conn = _make_mock_engine()

        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=0)
        mock_conn.execute = AsyncMock(return_value=count_result)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/catalog/new-count")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestCatalogSyncStatus:
    @pytest.mark.asyncio()
    async def test_returns_timestamp(self, app: Any) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=b"2026-02-25T05:30:00+00:00")
        mock_redis.close = AsyncMock()

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("redis.asyncio.from_url", return_value=mock_redis),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/catalog/sync-status")

        assert resp.status_code == 200
        assert resp.json()["last_sync_at"] == "2026-02-25T05:30:00+00:00"

    @pytest.mark.asyncio()
    async def test_returns_null_when_never_synced(self, app: Any) -> None:
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.close = AsyncMock()

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("redis.asyncio.from_url", return_value=mock_redis),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/admin/llm-costs/catalog/sync-status")

        assert resp.status_code == 200
        assert resp.json()["last_sync_at"] is None


class TestCatalogAdd:
    @pytest.mark.asyncio()
    async def test_adds_model_from_catalog(self, app: Any) -> None:
        new_id = uuid4()
        engine, mock_conn = _make_mock_engine()

        # 1: catalog SELECT, 2: dup check, 3: INSERT, 4: UPDATE is_new
        cat_result = MagicMock()
        cat_result.first = MagicMock(
            return_value=_row(
                model_key="gpt-5-mini",
                provider_type="openai",
                display_name="GPT 5 Mini",
                input_price_per_1m=0.25,
                output_price_per_1m=2.00,
            )
        )
        dup_result = MagicMock()
        dup_result.first = MagicMock(return_value=None)
        insert_result = MagicMock()
        insert_result.first = MagicMock(return_value=_row(id=new_id))
        update_result = MagicMock()

        mock_conn.execute = AsyncMock(
            side_effect=[cat_result, dup_result, insert_result, update_result]
        )

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/admin/llm-costs/catalog/add",
                    json={"model_key": "gpt-5-mini", "provider_key": "openai-gpt5-mini"},
                )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == str(new_id)

    @pytest.mark.asyncio()
    async def test_404_when_catalog_missing(self, app: Any) -> None:
        engine, mock_conn = _make_mock_engine()

        cat_result = MagicMock()
        cat_result.first = MagicMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=cat_result)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/admin/llm-costs/catalog/add",
                    json={"model_key": "no-such-model", "provider_key": "x"},
                )

        assert resp.status_code == 404

    @pytest.mark.asyncio()
    async def test_409_duplicate_provider_key(self, app: Any) -> None:
        engine, mock_conn = _make_mock_engine()

        cat_result = MagicMock()
        cat_result.first = MagicMock(
            return_value=_row(
                model_key="gpt-5-mini",
                provider_type="openai",
                display_name="GPT 5 Mini",
                input_price_per_1m=0.25,
                output_price_per_1m=2.00,
            )
        )
        dup_result = MagicMock()
        dup_result.first = MagicMock(return_value=_row(id=uuid4()))

        mock_conn.execute = AsyncMock(side_effect=[cat_result, dup_result])

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/admin/llm-costs/catalog/add",
                    json={"model_key": "gpt-5-mini", "provider_key": "gemini-flash"},
                )

        assert resp.status_code == 409


class TestCatalogDismiss:
    @pytest.mark.asyncio()
    async def test_dismisses_models(self, app: Any) -> None:
        engine, _mock_conn = _make_mock_engine(rowcount=3)

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/admin/llm-costs/catalog/dismiss",
                    json={"model_keys": ["a", "b", "c"]},
                )

        assert resp.status_code == 200
        assert resp.json()["dismissed"] == 3

    @pytest.mark.asyncio()
    async def test_400_on_empty_keys(self, app: Any) -> None:
        engine, _ = _make_mock_engine()

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch("src.api.llm_costs._get_engine", AsyncMock(return_value=engine)),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/admin/llm-costs/catalog/dismiss",
                    json={"model_keys": []},
                )

        assert resp.status_code == 400


class TestCatalogSync:
    @pytest.mark.asyncio()
    async def test_triggers_sync_task(self, app: Any) -> None:
        mock_task = MagicMock()
        mock_task.delay = MagicMock()

        with (
            patch("src.api.auth.require_admin", _fake_require_admin),
            patch(
                "src.tasks.pricing_sync.sync_llm_pricing_catalog",
                mock_task,
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/admin/llm-costs/catalog/sync")

        assert resp.status_code == 200
        assert "started" in resp.json()["message"].lower()


class TestLlmUsageLogger:
    """Tests for the fire-and-forget logger module."""

    @pytest.mark.asyncio()
    async def test_insert_usage_calls_db(self) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        with patch("src.monitoring.llm_usage_logger._get_engine", return_value=mock_engine):
            from src.monitoring.llm_usage_logger import _insert_usage

            await _insert_usage(
                task_type="agent",
                provider_key="gemini-flash",
                model_name="gemini-2.5-flash",
                input_tokens=1000,
                output_tokens=200,
                latency_ms=300,
                call_id="test-call-id",
                tenant_id=None,
            )

        mock_conn.execute.assert_called_once()

    @pytest.mark.asyncio()
    async def test_insert_usage_catches_errors(self) -> None:
        with patch(
            "src.monitoring.llm_usage_logger._get_engine",
            side_effect=RuntimeError("DB down"),
        ):
            from src.monitoring.llm_usage_logger import _insert_usage

            # Should not raise
            await _insert_usage(
                task_type="agent",
                provider_key="gemini-flash",
                model_name="gemini-2.5-flash",
                input_tokens=1000,
                output_tokens=200,
                latency_ms=None,
                call_id=None,
                tenant_id=None,
            )
