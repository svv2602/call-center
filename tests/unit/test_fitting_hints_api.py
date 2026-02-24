"""Unit tests for point hints admin API (src/api/fitting_hints.py)."""

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


class TestGetStationHints:
    """Test GET /admin/fitting/station-hints."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_empty_when_no_hints(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        resp = client.get("/admin/fitting/station-hints", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"hints": {}}

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_existing_hints(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        hints = {
            "station-1": {
                "district": "Правий берег",
                "landmarks": "біля Піт Лайн",
                "description": "",
            }
        }
        mock_redis._store["fitting:station_hints"] = json.dumps(hints)
        resp = client.get("/admin/fitting/station-hints", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hints"]["station-1"]["district"] == "Правий берег"
        assert data["hints"]["station-1"]["landmarks"] == "біля Піт Лайн"


class TestUpsertStationHint:
    """Test PUT /admin/fitting/station-hints/{station_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_creates_new_hint(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
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
        stored = json.loads(mock_redis._store["fitting:station_hints"])
        assert "st-123" in stored
        assert stored["st-123"]["landmarks"] == "Мост"

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_updates_existing_hint(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        initial = {"st-123": {"district": "old", "landmarks": "", "description": ""}}
        mock_redis._store["fitting:station_hints"] = json.dumps(initial)
        resp = client.put(
            "/admin/fitting/station-hints/st-123",
            json={"district": "new district", "landmarks": "new lm", "description": "desc"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        stored = json.loads(mock_redis._store["fitting:station_hints"])
        assert stored["st-123"]["district"] == "new district"

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_defaults_to_empty_strings(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        resp = client.put(
            "/admin/fitting/station-hints/st-456",
            json={},
            headers=_auth(),
        )
        assert resp.status_code == 200
        stored = json.loads(mock_redis._store["fitting:station_hints"])
        assert stored["st-456"] == {"district": "", "landmarks": "", "description": ""}


class TestDeleteStationHint:
    """Test DELETE /admin/fitting/station-hints/{station_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_deletes_existing_hint(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        initial = {"st-1": {"district": "d", "landmarks": "l", "description": ""}}
        mock_redis._store["fitting:station_hints"] = json.dumps(initial)
        resp = client.delete("/admin/fitting/station-hints/st-1", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        stored = json.loads(mock_redis._store["fitting:station_hints"])
        assert "st-1" not in stored

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_404_when_not_found(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        resp = client.delete("/admin/fitting/station-hints/nonexistent", headers=_auth())
        assert resp.status_code == 404


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


class TestGetPickupHints:
    """Test GET /admin/fitting/pickup-hints."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_empty_when_no_hints(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        resp = client.get("/admin/fitting/pickup-hints", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"hints": {}}

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_returns_existing_hints(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        hints = {
            "pp-1": {
                "district": "Центр",
                "landmarks": "біля метро Університет",
                "description": "",
            }
        }
        mock_redis._store["pickup:point_hints"] = json.dumps(hints)
        resp = client.get("/admin/fitting/pickup-hints", headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["hints"]["pp-1"]["district"] == "Центр"


class TestUpsertPickupHint:
    """Test PUT /admin/fitting/pickup-hints/{point_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_creates_new_hint(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
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
        stored = json.loads(mock_redis._store["pickup:point_hints"])
        assert "pp-100" in stored
        assert stored["pp-100"]["landmarks"] == "ТЦ Блокбастер"

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_updates_existing_hint(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        initial = {"pp-100": {"district": "old", "landmarks": "", "description": ""}}
        mock_redis._store["pickup:point_hints"] = json.dumps(initial)
        resp = client.put(
            "/admin/fitting/pickup-hints/pp-100",
            json={"district": "new", "landmarks": "new lm", "description": "desc"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        stored = json.loads(mock_redis._store["pickup:point_hints"])
        assert stored["pp-100"]["district"] == "new"


class TestDeletePickupHint:
    """Test DELETE /admin/fitting/pickup-hints/{point_id}."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_deletes_existing_hint(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        initial = {"pp-1": {"district": "d", "landmarks": "l", "description": ""}}
        mock_redis._store["pickup:point_hints"] = json.dumps(initial)
        resp = client.delete("/admin/fitting/pickup-hints/pp-1", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        stored = json.loads(mock_redis._store["pickup:point_hints"])
        assert "pp-1" not in stored

    @patch("src.api.auth.get_settings")
    @patch("src.api.fitting_hints._get_redis")
    def test_404_when_not_found(
        self,
        mock_get_redis: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
        mock_redis: AsyncMock,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_get_redis.return_value = mock_redis
        resp = client.delete("/admin/fitting/pickup-hints/nonexistent", headers=_auth())
        assert resp.status_code == 404


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
