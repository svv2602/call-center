"""Unit tests for the 1C data viewer admin API (src/api/onec_data.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.onec_data import router

_TEST_SECRET = "test-secret"


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, _TEST_SECRET)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


def _make_main_mock(
    *,
    redis: AsyncMock | None = None,
    onec_client: AsyncMock | None = None,
) -> MagicMock:
    """Build a fake main-module object with _redis and _onec_client attributes."""
    m = MagicMock()
    m._redis = redis
    m._onec_client = onec_client
    return m


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def mock_redis() -> AsyncMock:
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.ttl = AsyncMock(return_value=-1)
    r.type = AsyncMock(return_value="none")
    r.hlen = AsyncMock(return_value=0)
    r.hget = AsyncMock(return_value=None)
    return r


class TestOnecStatus:
    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_main_module")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_status_no_onec_client(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_get_main: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        """When no 1C client is configured, status is 'not_configured'."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_onec.return_value = None
        mock_get_redis.return_value = None

        resp = client.get("/admin/onec/status", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["onec_configured"] is False
        assert data["status"] == "not_configured"

    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_status_healthy_from_cache(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """When Redis has cached pickup data, status is 'reachable' without calling 1C."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        # Simulate cached pickup points for ProKoleso
        cached_points = json.dumps([{"id": "1", "address": "Київ вул. Хрещатик 1"}])
        mock_redis.get = AsyncMock(return_value=cached_points)
        mock_redis.ttl = AsyncMock(return_value=3500)
        mock_redis.type = AsyncMock(return_value="none")
        mock_get_redis.return_value = mock_redis

        mock_onec = AsyncMock()
        mock_get_onec.return_value = mock_onec

        # Also patch get_settings for the onec config section inside the endpoint
        mock_settings.return_value.onec.username = ""

        resp = client.get("/admin/onec/status", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["onec_configured"] is True
        assert data["status"] == "reachable"

    def test_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/admin/onec/status")
        assert resp.status_code == 401


class TestOnecPickupPoints:
    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_pickup_points_from_cache(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """Points served from Redis cache without calling 1C."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        points = [
            {"id": "1", "address": "Київ вул. 1", "type": "pickup", "city": "Київ"},
            {"id": "2", "address": "Дніпро вул. 2", "type": "pickup", "city": "Дніпро"},
        ]
        mock_redis.get = AsyncMock(return_value=json.dumps(points))
        mock_redis.ttl = AsyncMock(return_value=3000)
        mock_get_redis.return_value = mock_redis
        mock_get_onec.return_value = None

        resp = client.get("/admin/onec/pickup-points?network=ProKoleso", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "cache"
        assert data["total"] == 2
        assert len(data["points"]) == 2

    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_pickup_points_with_city_filter(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """City filter applied to cached results."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        points = [
            {"id": "1", "address": "Київ вул. 1", "type": "pickup", "city": "Київ"},
            {"id": "2", "address": "Дніпро вул. 2", "type": "pickup", "city": "Дніпро"},
        ]
        mock_redis.get = AsyncMock(return_value=json.dumps(points))
        mock_redis.ttl = AsyncMock(return_value=3000)
        mock_get_redis.return_value = mock_redis
        mock_get_onec.return_value = None

        resp = client.get(
            "/admin/onec/pickup-points?network=ProKoleso&city=Київ",
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["points"][0]["city"] == "Київ"

    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_pickup_points_no_cache_no_client(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """No cache and no 1C client — returns empty list with source='none'."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.get = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis
        mock_get_onec.return_value = None

        resp = client.get("/admin/onec/pickup-points?network=ProKoleso", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["source"] == "none"


class TestOnecStockLookup:
    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_stock_lookup_found(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """hget returns data — response has found=True and the stock data."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        stock_data = {"quantity": 8, "price": 3200}
        mock_redis.type = AsyncMock(return_value="hash")
        mock_redis.hget = AsyncMock(return_value=json.dumps(stock_data))
        mock_get_redis.return_value = mock_redis
        mock_get_onec.return_value = None

        resp = client.get(
            "/admin/onec/stock-lookup?network=ProKoleso&sku=225/45R17",
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert data["sku"] == "225/45R17"
        assert data["data"]["quantity"] == 8

    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_stock_lookup_not_found(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """hget returns None — response has found=False."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.type = AsyncMock(return_value="hash")
        mock_redis.hget = AsyncMock(return_value=None)
        mock_get_redis.return_value = mock_redis
        mock_get_onec.return_value = None

        resp = client.get(
            "/admin/onec/stock-lookup?network=ProKoleso&sku=999/99R99",
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        assert data["sku"] == "999/99R99"

    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_stock_lookup_no_redis(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        """No Redis available — returns found=False with error."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = None
        mock_get_onec.return_value = None

        resp = client.get(
            "/admin/onec/stock-lookup?network=ProKoleso&sku=225/45R17",
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
        assert "error" in data

    @patch("src.api.auth.get_settings")
    @patch("src.api.onec_data._get_redis")
    @patch("src.api.onec_data._get_onec_client")
    def test_stock_lookup_cache_not_populated(
        self,
        mock_get_onec: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """Stock key exists but is not a hash type — returns found=False."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_redis.type = AsyncMock(return_value="none")
        mock_get_redis.return_value = mock_redis
        mock_get_onec.return_value = None

        resp = client.get(
            "/admin/onec/stock-lookup?network=ProKoleso&sku=225/45R17",
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False
