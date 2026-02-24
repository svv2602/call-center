"""Unit tests for fitting station hints admin API (src/api/fitting_hints.py)."""

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

    async def _delete(key: str) -> None:
        store.pop(key, None)

    r.get = AsyncMock(side_effect=_get)
    r.set = AsyncMock(side_effect=_set)
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
