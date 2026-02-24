"""Tests for knowledge API deduplication checks."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from src.api.auth import create_jwt
from src.api.knowledge import router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _make_mock_row(data: dict[str, Any]) -> MagicMock:
    row = MagicMock()
    row._mapping = data
    for k, v in data.items():
        setattr(row, k, v)
    return row


def _make_mock_engine(
    rows: list[dict[str, Any]],
    *,
    multi_results: list[list[dict[str, Any]]] | None = None,
) -> tuple[Any, AsyncMock]:
    mock_conn = AsyncMock()

    if multi_results is not None:
        results = []
        for result_rows in multi_results:
            mock_result = MagicMock()
            mock_result.__iter__ = lambda self, r=result_rows: iter(
                [_make_mock_row(row) for row in r]
            )
            mock_result.first.return_value = (
                _make_mock_row(result_rows[0]) if result_rows else None
            )
            mock_result.scalar.return_value = len(result_rows)
            results.append(mock_result)
        mock_conn.execute = AsyncMock(side_effect=results)
    else:
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([_make_mock_row(r) for r in rows])
        mock_result.first.return_value = _make_mock_row(rows[0]) if rows else None
        mock_result.scalar.return_value = len(rows)
        mock_conn.execute = AsyncMock(return_value=mock_result)

    mock_engine = MagicMock()

    @asynccontextmanager
    async def _begin() -> AsyncIterator[AsyncMock]:
        yield mock_conn

    mock_engine.begin = _begin
    return mock_engine, mock_conn


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, "test-secret")


_SAMPLE_ARTICLE = {
    "id": "art-001",
    "title": "Test Article",
    "category": "faq",
    "content": "Some content",
    "active": True,
    "embedding_status": "pending",
    "tenant_id": None,
    "expires_at": None,
    "created_at": "2026-02-24T10:00:00",
}


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestCreateArticleDuplication:
    """POST /knowledge/articles dedup checks."""

    @patch("src.api.knowledge._dispatch_embedding")
    @patch("src.api.knowledge.check_title_exists", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_409_duplicate_title(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        mock_title_check: AsyncMock,
        mock_dispatch: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_ARTICLE])
        mock_engine_fn.return_value = mock_engine
        mock_title_check.return_value = {"id": "existing-id", "title": "Test Article"}

        response = client.post(
            "/knowledge/articles",
            json={"title": "Test Article", "category": "faq", "content": "New content"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 409
        data = response.json()["detail"]
        assert data["error"] == "duplicate_title"
        assert data["existing_id"] == "existing-id"

    @patch("src.api.knowledge._dispatch_embedding")
    @patch("src.api.knowledge.check_semantic_duplicate", new_callable=AsyncMock)
    @patch("src.api.knowledge.check_title_exists", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_409_semantic_duplicate(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        mock_title_check: AsyncMock,
        mock_semantic_check: AsyncMock,
        mock_dispatch: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_ARTICLE])
        mock_engine_fn.return_value = mock_engine
        mock_title_check.return_value = None  # No title match
        mock_semantic_check.return_value = {
            "status": "duplicate",
            "similar_title": "Similar Article",
            "similarity": 0.95,
        }

        response = client.post(
            "/knowledge/articles",
            json={"title": "New Title", "category": "faq", "content": "Similar content"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 409
        data = response.json()["detail"]
        assert data["error"] == "semantic_duplicate"
        assert data["similar_title"] == "Similar Article"

    @patch("src.api.knowledge._dispatch_embedding")
    @patch("src.api.knowledge.check_semantic_duplicate", new_callable=AsyncMock)
    @patch("src.api.knowledge.check_title_exists", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_warning_on_suspect(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        mock_title_check: AsyncMock,
        mock_semantic_check: AsyncMock,
        mock_dispatch: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_ARTICLE])
        mock_engine_fn.return_value = mock_engine
        mock_title_check.return_value = None
        mock_semantic_check.return_value = {
            "status": "suspect",
            "similar_title": "Somewhat Similar",
            "similarity": 0.85,
        }

        response = client.post(
            "/knowledge/articles",
            json={"title": "New Title", "category": "faq", "content": "Some content"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "warning" in data
        assert data["warning"]["type"] == "semantic_suspect"
        assert data["warning"]["similar_title"] == "Somewhat Similar"

    @patch("src.api.knowledge._dispatch_embedding")
    @patch("src.api.knowledge.check_semantic_duplicate", new_callable=AsyncMock)
    @patch("src.api.knowledge.check_title_exists", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_force_skips_semantic_check(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        mock_title_check: AsyncMock,
        mock_semantic_check: AsyncMock,
        mock_dispatch: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_ARTICLE])
        mock_engine_fn.return_value = mock_engine
        mock_title_check.return_value = None

        response = client.post(
            "/knowledge/articles?force=true",
            json={"title": "New Title", "category": "faq", "content": "Some content"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        mock_semantic_check.assert_not_called()

    @patch("src.api.knowledge._dispatch_embedding")
    @patch("src.api.knowledge.check_semantic_duplicate", new_callable=AsyncMock)
    @patch("src.api.knowledge.check_title_exists", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_integrity_error_safety_net(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        mock_title_check: AsyncMock,
        mock_semantic_check: AsyncMock,
        mock_dispatch: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_title_check.return_value = None
        mock_semantic_check.return_value = {"status": "new"}

        # Engine that raises IntegrityError on execute
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(
            side_effect=IntegrityError("dup", {}, Exception("unique violation"))
        )
        mock_engine = MagicMock()

        @asynccontextmanager
        async def _begin():
            yield mock_conn

        mock_engine.begin = _begin
        mock_engine_fn.return_value = mock_engine

        response = client.post(
            "/knowledge/articles",
            json={"title": "Race Condition Title", "category": "faq", "content": "Content"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 409
        data = response.json()["detail"]
        assert data["error"] == "duplicate_title"


class TestImportArticlesDuplication:
    """POST /knowledge/articles/import dedup checks."""

    @patch("src.api.knowledge._dispatch_embedding")
    @patch("src.api.knowledge.check_title_exists", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_skip_file_with_existing_title(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        mock_title_check: AsyncMock,
        mock_dispatch: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_engine, _ = _make_mock_engine([_SAMPLE_ARTICLE])
        mock_engine_fn.return_value = mock_engine
        mock_title_check.return_value = {"id": "existing-id", "title": "Test Title"}

        response = client.post(
            "/knowledge/articles/import",
            files=[("files", ("test.md", b"# Test Title\n\nSome content", "text/markdown"))],
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["imported"] == 0
        assert data["errors"] == 1
        assert "already exists" in data["error_details"][0]["error"]

    @patch("src.api.knowledge._dispatch_embedding")
    @patch("src.api.knowledge.check_title_exists", new_callable=AsyncMock)
    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_integrity_error_on_import(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        mock_title_check: AsyncMock,
        mock_dispatch: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"
        mock_title_check.return_value = None  # No title match at app level

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(
            side_effect=IntegrityError("dup", {}, Exception("unique violation"))
        )
        mock_engine = MagicMock()

        @asynccontextmanager
        async def _begin():
            yield mock_conn

        mock_engine.begin = _begin
        mock_engine_fn.return_value = mock_engine

        response = client.post(
            "/knowledge/articles/import",
            files=[("files", ("test.md", b"# New Title\n\nSome content", "text/markdown"))],
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["imported"] == 0
        assert data["errors"] == 1
        assert "already exists" in data["error_details"][0]["error"]


class TestUpdateArticleDuplication:
    """PATCH /knowledge/articles/{id} IntegrityError handling."""

    @patch("src.api.auth.get_settings")
    @patch("src.api.knowledge._get_engine")
    def test_integrity_error_on_update(
        self,
        mock_engine_fn: MagicMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = "test-secret"

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(
            side_effect=IntegrityError("dup", {}, Exception("unique violation"))
        )
        mock_engine = MagicMock()

        @asynccontextmanager
        async def _begin():
            yield mock_conn

        mock_engine.begin = _begin
        mock_engine_fn.return_value = mock_engine

        response = client.patch(
            "/knowledge/articles/00000000-0000-0000-0000-000000000001",
            json={"title": "Existing Title"},
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
        assert response.status_code == 409
        data = response.json()["detail"]
        assert data["error"] == "duplicate_title"
