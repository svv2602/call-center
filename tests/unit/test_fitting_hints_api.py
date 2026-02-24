"""Unit tests for point hints admin API (src/api/fitting_hints.py).

Tests cover PG write-through with Redis cache sync.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.fitting_hints import router

_TEST_SECRET = "test-secret"


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, _TEST_SECRET)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


# ── In-memory PG mock ─────────────────────────────────────


class _FakeResult:
    """Mimics SQLAlchemy CursorResult for simple queries."""

    def __init__(self, rows: list[dict[str, str]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> list[dict[str, str]]:
        return self._rows


class _FakeConn:
    """In-memory connection that stores point_hints rows."""

    def __init__(self, store: dict[tuple[str, str], dict[str, str]]) -> None:
        self._store = store

    async def execute(self, stmt: object, params: dict[str, str] | None = None) -> _FakeResult:
        sql = str(stmt)
        params = params or {}

        if "INSERT INTO point_hints" in sql:
            key = (params["point_type"], params["point_id"])
            self._store[key] = {
                "district": params.get("district", ""),
                "landmarks": params.get("landmarks", ""),
                "description": params.get("description", ""),
            }
            return _FakeResult([], rowcount=1)

        if "DELETE FROM point_hints" in sql:
            point_id = params["point_id"]
            # Determine point_type from SQL
            if "fitting_station" in sql:
                key = ("fitting_station", point_id)
            else:
                key = ("pickup_point", point_id)
            if key in self._store:
                del self._store[key]
                return _FakeResult([], rowcount=1)
            return _FakeResult([], rowcount=0)

        if "SELECT" in sql and "point_hints" in sql:
            point_type = params.get("point_type", "")
            rows = []
            for (pt, pid), hint in self._store.items():
                if pt == point_type:
                    rows.append({"point_id": pid, **hint})
            return _FakeResult(rows)

        return _FakeResult([])


class _FakeEngine:
    """Mimics AsyncEngine with context-managed connection."""

    def __init__(self, store: dict[tuple[str, str], dict[str, str]]) -> None:
        self._store = store

    def begin(self) -> _FakeEngine:
        self._conn = _FakeConn(self._store)
        return self

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        pass


@pytest.fixture()
def pg_store() -> dict[tuple[str, str], dict[str, str]]:
    return {}


@pytest.fixture()
def mock_engine(pg_store: dict[tuple[str, str], dict[str, str]]) -> _FakeEngine:
    return _FakeEngine(pg_store)


@pytest.fixture()
def mock_redis() -> AsyncMock:
    store: dict[str, str] = {}
    r = AsyncMock()

    async def _get(key: str) -> str | None:
        return store.get(key)

    async def _set(key: str, value: str) -> None:
        store[key] = value

    async def _setex(key: str, _ttl: int, value: str) -> None:
        store[key] = value

    async def _delete(key: str) -> None:
        store.pop(key, None)

    r.get = AsyncMock(side_effect=_get)
    r.set = AsyncMock(side_effect=_set)
    r.setex = AsyncMock(side_effect=_setex)
    r.delete = AsyncMock(side_effect=_delete)
    r._store = store
    return r


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Station Hints: GET ────────────────────────────────────


class TestGetStationHints:
    """Test GET /admin/fitting/station-hints."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_returns_empty_when_no_hints(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        resp = client.get("/admin/fitting/station-hints", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"hints": {}}

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_returns_existing_hints(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("fitting_station", "station-1")] = {
            "district": "Правий берег",
            "landmarks": "біля Піт Лайн",
            "description": "",
        }
        resp = client.get("/admin/fitting/station-hints", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hints"]["station-1"]["district"] == "Правий берег"
        assert data["hints"]["station-1"]["landmarks"] == "біля Піт Лайн"

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_get_warms_redis_cache(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        """GET station-hints should sync PG data into Redis cache."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("fitting_station", "st-1")] = {
            "district": "Центр",
            "landmarks": "",
            "description": "",
        }
        client.get("/admin/fitting/station-hints", headers=_auth())
        # Redis should be warmed with PG data
        assert "fitting:station_hints" in mock_redis._store
        cached = json.loads(mock_redis._store["fitting:station_hints"])
        assert cached["st-1"]["district"] == "Центр"


# ── Station Hints: PUT ────────────────────────────────────


class TestUpsertStationHint:
    """Test PUT /admin/fitting/station-hints/{station_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_creates_new_hint(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        resp = client.put(
            "/admin/fitting/station-hints/st-123",
            json={"district": "Лівий берег", "landmarks": "Мост", "description": "test"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["station_id"] == "st-123"
        assert data["hint"]["district"] == "Лівий берег"
        # Verify persisted in PG store
        assert ("fitting_station", "st-123") in pg_store
        assert pg_store[("fitting_station", "st-123")]["landmarks"] == "Мост"
        # Verify synced to Redis
        cached = json.loads(mock_redis._store["fitting:station_hints"])
        assert "st-123" in cached

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_updates_existing_hint(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("fitting_station", "st-123")] = {
            "district": "old",
            "landmarks": "",
            "description": "",
        }
        resp = client.put(
            "/admin/fitting/station-hints/st-123",
            json={"district": "new district", "landmarks": "new lm", "description": "desc"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert pg_store[("fitting_station", "st-123")]["district"] == "new district"

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_defaults_to_empty_strings(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        resp = client.put(
            "/admin/fitting/station-hints/st-456",
            json={},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert pg_store[("fitting_station", "st-456")] == {
            "district": "",
            "landmarks": "",
            "description": "",
        }


# ── Station Hints: DELETE ─────────────────────────────────


class TestDeleteStationHint:
    """Test DELETE /admin/fitting/station-hints/{station_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_deletes_existing_hint(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("fitting_station", "st-1")] = {
            "district": "d",
            "landmarks": "l",
            "description": "",
        }
        resp = client.delete("/admin/fitting/station-hints/st-1", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert ("fitting_station", "st-1") not in pg_store

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_404_when_not_found(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        resp = client.delete("/admin/fitting/station-hints/nonexistent", headers=_auth())
        assert resp.status_code == 404

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_delete_syncs_redis(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        """After delete, Redis cache should reflect remaining hints."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("fitting_station", "st-1")] = {
            "district": "d1",
            "landmarks": "",
            "description": "",
        }
        pg_store[("fitting_station", "st-2")] = {
            "district": "d2",
            "landmarks": "",
            "description": "",
        }
        client.delete("/admin/fitting/station-hints/st-1", headers=_auth())
        cached = json.loads(mock_redis._store["fitting:station_hints"])
        assert "st-1" not in cached
        assert "st-2" in cached


# ── List Stations (Redis proxy) ───────────────────────────


class TestListStations:
    """Test GET /admin/fitting/stations."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_empty_when_no_cache(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        resp = client.get("/admin/fitting/stations", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"stations": []}

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_cached_stations(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        stations = [
            {"station_id": "st-1", "name": "Station 1", "city": "Дніпро", "address": "вул. Тест 1"},
            {"station_id": "st-2", "name": "Station 2", "city": "Київ", "address": "вул. Тест 2"},
        ]
        mock_redis._store["onec:fitting_stations"] = json.dumps(stations)
        resp = client.get("/admin/fitting/stations", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["stations"]) == 2
        assert data["stations"][0]["name"] == "Station 1"


# ── Hint Merge Logic ──────────────────────────────────────


class TestHintMergeLogic:
    """Test that hints are correctly merged into station data."""

    def test_merge_hints_into_stations(self) -> None:
        """Simulate the merge logic from main.py _get_fitting_stations."""
        stations = [
            {"id": "st-1", "name": "Station 1", "city": "Дніпро", "address": "вул. Тест 1"},
            {"id": "st-2", "name": "Station 2", "city": "Дніпро", "address": "вул. Тест 2"},
            {"id": "st-3", "name": "Station 3", "city": "Київ", "address": "вул. Тест 3"},
        ]
        hints = {
            "st-1": {"district": "Правий берег", "landmarks": "біля Піт Лайн", "description": ""},
            "st-2": {"district": "Лівий берег", "landmarks": "", "description": "Нова точка"},
        }

        for s in stations:
            sid = s.get("id", "")
            if sid in hints:
                h = hints[sid]
                if h.get("district"):
                    s["district"] = h["district"]
                if h.get("landmarks"):
                    s["landmarks"] = h["landmarks"]
                if h.get("description"):
                    s["description"] = h["description"]

        assert stations[0]["district"] == "Правий берег"
        assert stations[0]["landmarks"] == "біля Піт Лайн"
        assert "description" not in stations[0]  # empty string → not added
        assert stations[1]["district"] == "Лівий берег"
        assert "landmarks" not in stations[1]  # empty string → not added
        assert stations[1]["description"] == "Нова точка"
        assert "district" not in stations[2]  # no hint for st-3


# ── Pickup Point Hints ────────────────────────────────────


class TestGetPickupHints:
    """Test GET /admin/fitting/pickup-hints."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_returns_empty_when_no_hints(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        resp = client.get("/admin/fitting/pickup-hints", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"hints": {}}

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_returns_existing_hints(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("pickup_point", "pp-1")] = {
            "district": "Центр",
            "landmarks": "біля метро Університет",
            "description": "",
        }
        resp = client.get("/admin/fitting/pickup-hints", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hints"]["pp-1"]["district"] == "Центр"

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_get_warms_redis_cache(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        """GET pickup-hints should sync PG data into Redis cache."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("pickup_point", "pp-1")] = {
            "district": "Оболонь",
            "landmarks": "",
            "description": "",
        }
        client.get("/admin/fitting/pickup-hints", headers=_auth())
        assert "pickup:point_hints" in mock_redis._store
        cached = json.loads(mock_redis._store["pickup:point_hints"])
        assert cached["pp-1"]["district"] == "Оболонь"


class TestUpsertPickupHint:
    """Test PUT /admin/fitting/pickup-hints/{point_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_creates_new_hint(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        resp = client.put(
            "/admin/fitting/pickup-hints/pp-100",
            json={"district": "Оболонь", "landmarks": "ТЦ Блокбастер", "description": "test"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["point_id"] == "pp-100"
        assert data["hint"]["district"] == "Оболонь"
        # Verify PG persistence
        assert ("pickup_point", "pp-100") in pg_store
        assert pg_store[("pickup_point", "pp-100")]["landmarks"] == "ТЦ Блокбастер"
        # Verify Redis sync
        cached = json.loads(mock_redis._store["pickup:point_hints"])
        assert "pp-100" in cached

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_updates_existing_hint(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("pickup_point", "pp-100")] = {
            "district": "old",
            "landmarks": "",
            "description": "",
        }
        resp = client.put(
            "/admin/fitting/pickup-hints/pp-100",
            json={"district": "new", "landmarks": "new lm", "description": "desc"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert pg_store[("pickup_point", "pp-100")]["district"] == "new"


class TestDeletePickupHint:
    """Test DELETE /admin/fitting/pickup-hints/{point_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_deletes_existing_hint(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
        pg_store: dict,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        pg_store[("pickup_point", "pp-1")] = {
            "district": "d",
            "landmarks": "l",
            "description": "",
        }
        resp = client.delete("/admin/fitting/pickup-hints/pp-1", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert ("pickup_point", "pp-1") not in pg_store

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_404_when_not_found(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        mock_get_redis.return_value = mock_redis
        resp = client.delete("/admin/fitting/pickup-hints/nonexistent", headers=_auth())
        assert resp.status_code == 404


# ── List Pickup Points (Redis proxy) ──────────────────────


class TestListPickupPoints:
    """Test GET /admin/fitting/pickup-points."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_empty_when_no_cache(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        resp = client.get("/admin/fitting/pickup-points", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"points": []}

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_cached_points_from_multiple_networks(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        pk_points = [{"id": "pp-1", "address": "вул. Тест 1", "city": "Дніпро"}]
        ts_points = [{"id": "pp-2", "address": "вул. Тест 2", "city": "Київ"}]
        mock_redis._store["onec:points:ProKoleso"] = json.dumps(pk_points)
        mock_redis._store["onec:points:Tshina"] = json.dumps(ts_points)
        resp = client.get("/admin/fitting/pickup-points", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["points"]) == 2


class TestPickupPointHintMergeLogic:
    """Test that hints are correctly merged into pickup point data."""

    def test_merge_hints_into_points(self) -> None:
        """Simulate the merge logic from main.py _get_pickup_points."""
        points = [
            {"id": "pp-1", "address": "вул. Тест 1", "city": "Дніпро"},
            {"id": "pp-2", "address": "вул. Тест 2", "city": "Дніпро"},
            {"id": "pp-3", "address": "вул. Тест 3", "city": "Київ"},
        ]
        hints = {
            "pp-1": {"district": "Центр", "landmarks": "біля метро", "description": ""},
            "pp-2": {"district": "Лівий берег", "landmarks": "", "description": "Новий пункт"},
        }

        for p in points:
            pid = p.get("id", "")
            if pid in hints:
                h = hints[pid]
                if h.get("district"):
                    p["district"] = h["district"]
                if h.get("landmarks"):
                    p["landmarks"] = h["landmarks"]
                if h.get("description"):
                    p["description"] = h["description"]

        assert points[0]["district"] == "Центр"
        assert points[0]["landmarks"] == "біля метро"
        assert "description" not in points[0]  # empty string → not added
        assert points[1]["district"] == "Лівий берег"
        assert "landmarks" not in points[1]  # empty string → not added
        assert points[1]["description"] == "Новий пункт"
        assert "district" not in points[2]  # no hint for pp-3


# ── Tool Result Compressor ────────────────────────────────


class TestCompressorKeepsHintFields:
    """Test that tool_result_compressor preserves district/landmarks."""

    def test_fitting_stations_compressor_keeps_hints(self) -> None:
        from src.agent.tool_result_compressor import compress_tool_result

        result = {
            "total": 2,
            "stations": [
                {
                    "id": "st-1",
                    "name": "S1",
                    "address": "A1",
                    "working_hours": "9-18",
                    "district": "Правий берег",
                    "landmarks": "біля Піт Лайн",
                    "extra_field": "drop",
                },
                {"id": "st-2", "name": "S2", "address": "A2"},
            ],
        }
        compressed = compress_tool_result("get_fitting_stations", result)
        data = json.loads(compressed)
        assert data["stations"][0]["district"] == "Правий берег"
        assert data["stations"][0]["landmarks"] == "біля Піт Лайн"
        assert "extra_field" not in data["stations"][0]
        assert "district" not in data["stations"][1]

    def test_pickup_points_compressor_keeps_hints(self) -> None:
        from src.agent.tool_result_compressor import compress_tool_result

        result = {
            "total": 2,
            "points": [
                {
                    "id": "pp-1",
                    "address": "вул. Тест 1",
                    "city": "Дніпро",
                    "type": "pickup",
                    "district": "Центр",
                    "landmarks": "біля метро",
                },
                {"id": "pp-2", "address": "вул. Тест 2", "city": "Київ"},
            ],
        }
        compressed = compress_tool_result("get_pickup_points", result)
        data = json.loads(compressed)
        assert data["points"][0]["district"] == "Центр"
        assert data["points"][0]["landmarks"] == "біля метро"
        assert "type" not in data["points"][0]
        assert "district" not in data["points"][1]


# ── Refresh Endpoints (unchanged, Redis-only) ─────────────


class TestRefreshFittingStations:
    """Test POST /admin/fitting/stations/refresh."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_refresh_stations_success(
        self,
        mock_get_redis: MagicMock,
        mock_hints_settings: MagicMock,
        mock_auth_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_auth_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_hints_settings.return_value.onec.username = "user"
        mock_hints_settings.return_value.onec.url = "http://1c"
        mock_hints_settings.return_value.onec.password = "pass"
        mock_hints_settings.return_value.onec.soap_wsdl_path = "/wsdl"
        mock_hints_settings.return_value.onec.soap_timeout = 30
        mock_get_redis.return_value = mock_redis

        stations = [
            {"station_id": "st-1", "name": "Station 1", "city": "Дніпро", "address": "вул. 1"},
            {"station_id": "st-2", "name": "Station 2", "city": "Київ", "address": "вул. 2"},
        ]
        mock_client = AsyncMock()
        mock_client.get_stations.return_value = stations

        with patch("src.onec_client.soap.OneCSOAPClient", return_value=mock_client):
            resp = client.post("/admin/fitting/stations/refresh", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["stations"]) == 2
        assert data["stations"][0]["name"] == "Station 1"
        # Verify cached in Redis
        assert "onec:fitting_stations" in mock_redis._store

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_refresh_stations_no_credentials(
        self,
        mock_get_redis: MagicMock,
        mock_hints_settings: MagicMock,
        mock_auth_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_auth_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_hints_settings.return_value.onec.username = ""
        mock_get_redis.return_value = mock_redis

        resp = client.post("/admin/fitting/stations/refresh", headers=_auth())
        assert resp.status_code == 503

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_refresh_stations_soap_error(
        self,
        mock_get_redis: MagicMock,
        mock_hints_settings: MagicMock,
        mock_auth_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_auth_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_hints_settings.return_value.onec.username = "user"
        mock_hints_settings.return_value.onec.url = "http://1c"
        mock_hints_settings.return_value.onec.password = "pass"
        mock_hints_settings.return_value.onec.soap_wsdl_path = "/wsdl"
        mock_hints_settings.return_value.onec.soap_timeout = 30
        mock_get_redis.return_value = mock_redis

        mock_client = AsyncMock()
        mock_client.get_stations.side_effect = ConnectionError("SOAP timeout")

        with patch("src.onec_client.soap.OneCSOAPClient", return_value=mock_client):
            resp = client.post("/admin/fitting/stations/refresh", headers=_auth())

        assert resp.status_code == 502
        assert "SOAP" in resp.json()["detail"]


class TestRefreshPickupPoints:
    """Test POST /admin/fitting/pickup-points/refresh."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_refresh_pickup_points_success(
        self,
        mock_get_redis: MagicMock,
        mock_hints_settings: MagicMock,
        mock_auth_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_auth_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_hints_settings.return_value.onec.username = "user"
        mock_hints_settings.return_value.onec.url = "http://1c"
        mock_hints_settings.return_value.onec.password = "pass"
        mock_hints_settings.return_value.onec.timeout = 30
        mock_get_redis.return_value = mock_redis

        pk_data = {"data": [
            {"id": "pk-1", "point": "вул. А 1", "point_type": "pickup", "City": "Дніпро"},
        ]}
        ts_data = {"data": [
            {"id": "ts-1", "point": "вул. Б 2", "point_type": "pickup", "City": "Київ"},
            {"id": "ts-2", "point": "вул. В 3", "point_type": "pickup", "City": "Київ"},
        ]}
        mock_client = AsyncMock()
        mock_client.get_pickup_points.side_effect = [pk_data, ts_data]

        with patch("src.onec_client.client.OneCClient", return_value=mock_client):
            resp = client.post("/admin/fitting/pickup-points/refresh", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["points"]) == 3
        # Verify cached in Redis
        assert "onec:points:ProKoleso" in mock_redis._store
        assert "onec:points:Tshina" in mock_redis._store

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_refresh_pickup_points_no_credentials(
        self,
        mock_get_redis: MagicMock,
        mock_hints_settings: MagicMock,
        mock_auth_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_auth_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_hints_settings.return_value.onec.username = ""
        mock_get_redis.return_value = mock_redis

        resp = client.post("/admin/fitting/pickup-points/refresh", headers=_auth())
        assert resp.status_code == 503

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_refresh_pickup_points_partial_failure(
        self,
        mock_get_redis: MagicMock,
        mock_hints_settings: MagicMock,
        mock_auth_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        """One network fails but the other succeeds — still returns partial data."""
        mock_auth_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_hints_settings.return_value.onec.username = "user"
        mock_hints_settings.return_value.onec.url = "http://1c"
        mock_hints_settings.return_value.onec.password = "pass"
        mock_hints_settings.return_value.onec.timeout = 30
        mock_get_redis.return_value = mock_redis

        pk_data = {"data": [
            {"id": "pk-1", "point": "вул. А 1", "point_type": "pickup", "City": "Дніпро"},
        ]}
        mock_client = AsyncMock()
        mock_client.get_pickup_points.side_effect = [pk_data, ConnectionError("Tshina down")]

        with patch("src.onec_client.client.OneCClient", return_value=mock_client):
            resp = client.post("/admin/fitting/pickup-points/refresh", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1  # only ProKoleso succeeded
        assert len(data["points"]) == 1


# ── Redis Sync Failure Resilience ─────────────────────────


class TestRedisSyncResilience:
    """Verify that PG write succeeds even when Redis sync fails."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_upsert_succeeds_when_redis_fails(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        pg_store: dict,
    ) -> None:
        """PUT should succeed even if Redis is down — PG is primary."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        # Redis that raises on set
        broken_redis = AsyncMock()
        broken_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_get_redis.return_value = broken_redis

        resp = client.put(
            "/admin/fitting/station-hints/st-99",
            json={"district": "Тест", "landmarks": "", "description": ""},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert ("fitting_station", "st-99") in pg_store
        assert pg_store[("fitting_station", "st-99")]["district"] == "Тест"

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    @patch("src.api.fitting_hints._get_engine")
    def test_delete_succeeds_when_redis_fails(
        self,
        mock_get_engine: MagicMock,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_engine: _FakeEngine,
        pg_store: dict,
    ) -> None:
        """DELETE should succeed even if Redis is down — PG is primary."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_engine.return_value = mock_engine
        broken_redis = AsyncMock()
        broken_redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_get_redis.return_value = broken_redis

        pg_store[("fitting_station", "st-99")] = {
            "district": "d",
            "landmarks": "",
            "description": "",
        }
        resp = client.delete("/admin/fitting/station-hints/st-99", headers=_auth())
        assert resp.status_code == 200
        assert ("fitting_station", "st-99") not in pg_store
