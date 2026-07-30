"""API tests for the STT corrections router.

Focuses on the bulk-import endpoint (partial failure semantics, duplicate
skipping, empty payload). Other endpoints are covered by their runtime
behavior in tests/unit/test_stt_corrections.py.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.stt_corrections import router


async def _fake_require_perm(*_args: object, **_kwargs: object) -> dict[str, Any]:
    return {"sub": "test-user", "role": "admin"}


@pytest.fixture()
def mock_redis():
    """Minimal in-memory redis stub. Also invalidates the corrections
    module-level cache before yielding so tests are isolated."""
    from src.stt import corrections as corr_mod

    corr_mod.invalidate_cache()

    store: dict[str, bytes] = {}
    mock = AsyncMock()

    async def _get(key: str) -> bytes | None:
        return store.get(key)

    async def _set(key: str, value: str) -> None:
        store[key] = value.encode() if isinstance(value, str) else value

    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(side_effect=_set)
    mock._store = store
    yield mock
    corr_mod.invalidate_cache()


@pytest.fixture()
def app():
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


def _patch_deps(mock_redis: AsyncMock):
    """Common patch stack for the corrections router.

    ``require_permission`` returns a *dependency callable* (not the callable
    itself), so we can't monkey-patch it — instead we patch the two module
    globals bound at import time to fakes that always allow the request.
    """
    return (
        patch(
            "src.api.stt_corrections._perm_r", _fake_require_perm
        ),
        patch(
            "src.api.stt_corrections._perm_w", _fake_require_perm
        ),
        patch(
            "src.api.stt_corrections._get_redis",
            AsyncMock(return_value=mock_redis),
        ),
    )


class TestBulkCreate:
    """POST /admin/stt/corrections/bulk."""

    @pytest.mark.asyncio()
    async def test_all_valid_rules_inserted(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        payload = {
            "rules": [
                {"pattern": r"\bвифт\b", "replacement": "вівторок", "context_hint": "date"},
                {"pattern": r"\bлет\b", "replacement": "липня", "context_hint": "date"},
            ]
        }
        p1, p2, p3 = _patch_deps(mock_redis)
        with p1, p2, p3:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/admin/stt/corrections/bulk", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] == 2
        assert body["skipped"] == 0
        assert body["errors"] == 0
        assert len(body["results"]) == 2
        assert all(r["status"] == "created" for r in body["results"])
        # Redis has both rules with server-assigned ids
        stored = json.loads(mock_redis._store["stt:corrections"].decode())
        assert len(stored) == 2
        assert all("id" in r and len(r["id"]) == 12 for r in stored)

    @pytest.mark.asyncio()
    async def test_partial_failure_bad_regex(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        payload = {
            "rules": [
                {"pattern": r"\bok\b", "replacement": "OK"},
                {"pattern": "[unclosed", "replacement": "x"},
                {"pattern": r"\bfine\b", "replacement": "fine"},
            ]
        }
        p1, p2, p3 = _patch_deps(mock_redis)
        with p1, p2, p3:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/admin/stt/corrections/bulk", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] == 2
        assert body["errors"] == 1
        assert body["results"][1]["status"] == "error"
        assert "invalid regex" in body["results"][1]["error"]
        stored = json.loads(mock_redis._store["stt:corrections"].decode())
        assert len(stored) == 2

    @pytest.mark.asyncio()
    async def test_skip_duplicates_by_default(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        # Pre-seed one rule
        mock_redis._store["stt:corrections"] = json.dumps(
            [{"id": "seed12345678", "pattern": r"\bвифт\b", "replacement": "вівторок",
              "enabled": True, "flags": "i", "context_hint": "date", "note": ""}]
        ).encode()

        payload = {
            "rules": [
                {"pattern": r"\bвифт\b", "replacement": "вівторок"},  # dup
                {"pattern": r"\bлет\b", "replacement": "липня"},       # new
            ]
        }
        p1, p2, p3 = _patch_deps(mock_redis)
        with p1, p2, p3:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/admin/stt/corrections/bulk", json=payload)

        body = resp.json()
        assert body["created"] == 1
        assert body["skipped"] == 1
        assert body["results"][0]["status"] == "skipped"
        assert body["results"][0]["reason"] == "duplicate pattern"
        # Original seed rule kept + one new rule
        stored = json.loads(mock_redis._store["stt:corrections"].decode())
        assert len(stored) == 2
        assert stored[0]["id"] == "seed12345678"

    @pytest.mark.asyncio()
    async def test_skip_duplicates_off(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        mock_redis._store["stt:corrections"] = json.dumps(
            [{"id": "seed12345678", "pattern": r"\bвифт\b", "replacement": "вівторок",
              "enabled": True, "flags": "i", "context_hint": "date", "note": ""}]
        ).encode()
        payload = {
            "rules": [{"pattern": r"\bвифт\b", "replacement": "вівторок"}],
            "skip_duplicates": False,
        }
        p1, p2, p3 = _patch_deps(mock_redis)
        with p1, p2, p3:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/admin/stt/corrections/bulk", json=payload)

        body = resp.json()
        assert body["created"] == 1
        stored = json.loads(mock_redis._store["stt:corrections"].decode())
        assert len(stored) == 2  # both kept — duplicate allowed

    @pytest.mark.asyncio()
    async def test_invalid_context_hint_flagged(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        payload = {
            "rules": [
                {"pattern": r"\bx\b", "replacement": "y", "context_hint": "bogus"},
            ]
        }
        p1, p2, p3 = _patch_deps(mock_redis)
        with p1, p2, p3:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/admin/stt/corrections/bulk", json=payload)

        body = resp.json()
        assert body["errors"] == 1
        assert body["results"][0]["status"] == "error"
        assert "context_hint" in body["results"][0]["error"]

    @pytest.mark.asyncio()
    async def test_empty_rules_is_noop(
        self, app: Any, mock_redis: AsyncMock
    ) -> None:
        p1, p2, p3 = _patch_deps(mock_redis)
        with p1, p2, p3:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/admin/stt/corrections/bulk", json={"rules": []}
                )

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"created": 0, "skipped": 0, "errors": 0, "results": []}
        assert "stt:corrections" not in mock_redis._store
