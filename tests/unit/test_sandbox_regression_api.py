"""HTTP-level TestClient tests for sandbox regression API endpoints."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.auth import create_jwt
from src.api.sandbox import router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ── helpers ───────────────────────────────────────────────────────────────────

_TEST_SECRET = "test-secret"


def _admin_token() -> str:
    return create_jwt({"sub": "admin", "role": "admin"}, _TEST_SECRET)


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {_admin_token()}"}


def _patch_engine(conn: AsyncMock) -> Any:
    engine = MagicMock()

    @asynccontextmanager
    async def _begin() -> AsyncIterator[AsyncMock]:
        yield conn

    engine.begin = _begin
    return engine


def _mock_row(data: dict[str, Any]) -> MagicMock:
    row = MagicMock()
    row._mapping = data
    for k, v in data.items():
        setattr(row, k, v)
    return row


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── sample data ───────────────────────────────────────────────────────────────

_RUN_ID = str(uuid4())
_SOURCE_CONV_ID = str(uuid4())
_NEW_CONV_ID = str(uuid4())
_PROMPT_VER_ID = str(uuid4())

_SAMPLE_RUN = {
    "id": _RUN_ID,
    "source_conversation_id": _SOURCE_CONV_ID,
    "new_prompt_version_id": _PROMPT_VER_ID,
    "new_conversation_id": _NEW_CONV_ID,
    "status": "completed",
    "turns_compared": 3,
    "avg_source_rating": 4.0,
    "avg_new_rating": None,
    "score_diff": None,
    "verdict": None,
    "error_message": None,
    "started_at": "2026-02-23T10:00:00",
    "completed_at": "2026-02-23T10:01:00",
    "created_at": "2026-02-23T10:00:00",
    "source_title": "Baseline conversation",
    "prompt_version_name": "v2",
    "summary": json.dumps(
        {
            "avg_similarity": 0.88,
            "turn_diffs": [
                {
                    "turn_number": 1,
                    "customer_message": "Привіт",
                    "source_response": "Вітаю",
                    "new_response": "Доброго дня",
                    "source_rating": 4,
                    "diff_lines": [],
                    "similarity_score": 0.88,
                }
            ],
        }
    ),
}


# ── TestListRegressionRuns ────────────────────────────────────────────────────


class TestListRegressionRuns:
    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_no_filter_returns_items(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        count_res = MagicMock()
        count_res.scalar.return_value = 1
        rows_res = MagicMock()
        rows_res.__iter__ = MagicMock(return_value=iter([_mock_row(_SAMPLE_RUN)]))
        conn.execute = AsyncMock(side_effect=[count_res, rows_res])
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.get("/admin/sandbox/regression-runs", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == _RUN_ID

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_verdict_pending_adds_is_null_filter(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        count_res = MagicMock()
        count_res.scalar.return_value = 0
        rows_res = MagicMock()
        rows_res.__iter__ = MagicMock(return_value=iter([]))
        conn.execute = AsyncMock(side_effect=[count_res, rows_res])
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.get("/admin/sandbox/regression-runs?verdict=pending", headers=_auth())

        assert resp.status_code == 200
        # Verify the IS NULL clause was used in the SQL
        first_call_sql = str(conn.execute.call_args_list[0][0][0])
        assert "IS NULL" in first_call_sql

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_verdict_approved_adds_eq_filter(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        approved_run = dict(_SAMPLE_RUN, verdict="approved")
        count_res = MagicMock()
        count_res.scalar.return_value = 1
        rows_res = MagicMock()
        rows_res.__iter__ = MagicMock(return_value=iter([_mock_row(approved_run)]))
        conn.execute = AsyncMock(side_effect=[count_res, rows_res])
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.get("/admin/sandbox/regression-runs?verdict=approved", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["items"][0]["verdict"] == "approved"
        # Verify equality clause used (not IS NULL)
        first_call_sql = str(conn.execute.call_args_list[0][0][0])
        assert "= :verdict" in first_call_sql

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_empty_result(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        count_res = MagicMock()
        count_res.scalar.return_value = 0
        rows_res = MagicMock()
        rows_res.__iter__ = MagicMock(return_value=iter([]))
        conn.execute = AsyncMock(side_effect=[count_res, rows_res])
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.get("/admin/sandbox/regression-runs", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.get("/admin/sandbox/regression-runs")
        assert resp.status_code == 401


# ── TestGetRegressionRun ──────────────────────────────────────────────────────


class TestGetRegressionRun:
    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_success_returns_item(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = _mock_row(_SAMPLE_RUN)
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.get(f"/admin/sandbox/regression-runs/{_RUN_ID}", headers=_auth())

        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["id"] == _RUN_ID
        assert data["item"]["status"] == "completed"
        assert data["item"]["source_title"] == "Baseline conversation"

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_not_found_returns_404(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = None
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.get(f"/admin/sandbox/regression-runs/{uuid4()}", headers=_auth())

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ── TestDeleteRegressionRun ───────────────────────────────────────────────────


class TestDeleteRegressionRun:
    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_success_without_new_conv(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        """Delete run that has no generated conversation."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        # SELECT → find run (no new_conversation_id)
        run_row = _mock_row({"id": _RUN_ID, "new_conversation_id": None})
        select_res = MagicMock()
        select_res.first.return_value = run_row
        # DELETE run → no return value needed
        del_res = MagicMock()
        conn.execute = AsyncMock(side_effect=[select_res, del_res])
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.delete(f"/admin/sandbox/regression-runs/{_RUN_ID}", headers=_auth())

        assert resp.status_code == 200
        assert "deleted" in resp.json()["message"].lower()
        # Only 2 executes: SELECT + DELETE run (no conversation cleanup)
        assert conn.execute.call_count == 2

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_success_with_new_conv_deletes_both(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        """Delete run that has a generated conversation — both must be cleaned up."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        # SELECT → run exists with a new_conversation_id
        run_row = _mock_row({"id": _RUN_ID, "new_conversation_id": _NEW_CONV_ID})
        select_res = MagicMock()
        select_res.first.return_value = run_row
        del_run_res = MagicMock()
        del_conv_res = MagicMock()
        conn.execute = AsyncMock(side_effect=[select_res, del_run_res, del_conv_res])
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.delete(f"/admin/sandbox/regression-runs/{_RUN_ID}", headers=_auth())

        assert resp.status_code == 200
        # 3 executes: SELECT + DELETE run + DELETE conversation
        assert conn.execute.call_count == 3
        # Third call should reference the conversation id
        third_call_sql = str(conn.execute.call_args_list[2][0][0])
        assert "sandbox_conversations" in third_call_sql

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_not_found_returns_404(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        not_found_res = MagicMock()
        not_found_res.first.return_value = None
        conn.execute = AsyncMock(return_value=not_found_res)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.delete(f"/admin/sandbox/regression-runs/{uuid4()}", headers=_auth())

        assert resp.status_code == 404


# ── TestRateRegressionTurn ────────────────────────────────────────────────────


class TestRateRegressionTurn:
    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_success_recalculates_avg(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()

        summary_data = {
            "avg_similarity": 0.88,
            "turn_diffs": [
                {
                    "turn_number": 1,
                    "customer_message": "Привіт",
                    "source_response": "Вітаю",
                    "new_response": "Доброго дня",
                    "source_rating": 4,
                    "diff_lines": [],
                    "similarity_score": 0.88,
                },
                {
                    "turn_number": 2,
                    "customer_message": "Шини 205/55 R16",
                    "source_response": "Знайшов 10 варіантів",
                    "new_response": "Є кілька варіантів",
                    "source_rating": 5,
                    "diff_lines": [],
                    "similarity_score": 0.92,
                },
            ],
        }

        # SELECT summary
        run_row = _mock_row(
            {
                "summary": summary_data,
                "avg_source_rating": 4.5,
            }
        )
        select_res = MagicMock()
        select_res.first.return_value = run_row
        # UPDATE
        update_res = MagicMock()
        conn.execute = AsyncMock(side_effect=[select_res, update_res])
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{_RUN_ID}/rate",
            json={"turn_number": 1, "rating": 5},
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Rating saved"
        # avg_new_rating should be computed from the one rated turn
        assert data["avg_new_rating"] == 5.0
        # score_diff = avg_new - avg_source = 5.0 - 4.5 = 0.5
        assert data["score_diff"] == pytest.approx(0.5, abs=0.01)

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_turn_not_found_returns_404(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        """Turn number that doesn't exist in summary returns 404."""
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()

        summary_data = {
            "turn_diffs": [
                {"turn_number": 1, "customer_message": "Hi", "source_rating": 4},
            ]
        }
        run_row = _mock_row({"summary": summary_data, "avg_source_rating": 4.0})
        select_res = MagicMock()
        select_res.first.return_value = run_row
        conn.execute = AsyncMock(return_value=select_res)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{_RUN_ID}/rate",
            json={"turn_number": 99, "rating": 3},
            headers=_auth(),
        )

        assert resp.status_code == 404
        assert "99" in resp.json()["detail"]

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_run_not_found_returns_404(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        not_found_res = MagicMock()
        not_found_res.first.return_value = None
        conn.execute = AsyncMock(return_value=not_found_res)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{uuid4()}/rate",
            json={"turn_number": 1, "rating": 4},
            headers=_auth(),
        )

        assert resp.status_code == 404

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_invalid_rating_returns_422(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{_RUN_ID}/rate",
            json={"turn_number": 1, "rating": 6},
            headers=_auth(),
        )

        assert resp.status_code == 422


# ── TestSetRegressionVerdict ──────────────────────────────────────────────────


class TestSetRegressionVerdict:
    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_approved_success(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = _mock_row({"id": _RUN_ID, "verdict": "approved"})
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{_RUN_ID}",
            json={"verdict": "approved"},
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["item"]["verdict"] == "approved"
        assert data["message"] == "Verdict set"

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_rejected_success(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = _mock_row({"id": _RUN_ID, "verdict": "rejected"})
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{_RUN_ID}",
            json={"verdict": "rejected"},
            headers=_auth(),
        )

        assert resp.status_code == 200
        assert resp.json()["item"]["verdict"] == "rejected"

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    def test_not_found_returns_404(
        self, mock_engine_fn: AsyncMock, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        conn = AsyncMock()
        result = MagicMock()
        result.first.return_value = None
        conn.execute = AsyncMock(return_value=result)
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{uuid4()}",
            json={"verdict": "approved"},
            headers=_auth(),
        )

        assert resp.status_code == 404

    @patch("src.api.auth.get_settings")
    def test_invalid_verdict_returns_422(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        resp = client.patch(
            f"/admin/sandbox/regression-runs/{_RUN_ID}",
            json={"verdict": "maybe"},
            headers=_auth(),
        )

        assert resp.status_code == 422


# ── TestBatchReplay ───────────────────────────────────────────────────────────


class TestBatchReplay:
    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    @patch("src.sandbox.regression.run_regression", new_callable=AsyncMock)
    def test_batch_creates_run_per_conversation(
        self,
        mock_run_regression: AsyncMock,
        mock_engine_fn: AsyncMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        from src.sandbox.regression import RegressionResult

        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        # Empty openai key so EmbeddingGenerator is skipped
        mock_settings.return_value.openai.api_key = ""

        conv_id_1 = str(uuid4())
        conv_id_2 = str(uuid4())
        run_id_1 = str(uuid4())
        run_id_2 = str(uuid4())

        mock_result = RegressionResult(
            turns_compared=2,
            avg_source_rating=4.0,
            avg_new_rating=None,
            score_diff=None,
            new_conversation_id=str(uuid4()),
            avg_similarity=0.85,
        )
        mock_run_regression.return_value = mock_result

        conn = AsyncMock()

        # Per conversation: INSERT returning id, then UPDATE
        def _make_insert_result(run_id: str) -> MagicMock:
            r = MagicMock()
            r.first.return_value = _mock_row({"id": run_id})
            return r

        update_res = MagicMock()
        conn.execute = AsyncMock(
            side_effect=[
                _make_insert_result(run_id_1),  # INSERT for conv_id_1
                update_res,  # UPDATE for conv_id_1
                _make_insert_result(run_id_2),  # INSERT for conv_id_2
                update_res,  # UPDATE for conv_id_2
            ]
        )
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.post(
            "/admin/sandbox/regression-runs/batch",
            json={
                "conversation_ids": [conv_id_1, conv_id_2],
                "new_prompt_version_id": _PROMPT_VER_ID,
            },
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["completed"] == 2
        assert data["failed"] == 0
        assert len(data["results"]) == 2
        assert mock_run_regression.call_count == 2

    @patch("src.api.auth.get_settings")
    @patch("src.api.sandbox._get_engine", new_callable=AsyncMock)
    @patch("src.sandbox.regression.run_regression", new_callable=AsyncMock)
    def test_batch_handles_partial_failure(
        self,
        mock_run_regression: AsyncMock,
        mock_engine_fn: AsyncMock,
        mock_settings: MagicMock,
        client: TestClient,
    ) -> None:
        from src.sandbox.regression import RegressionResult

        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET
        mock_settings.return_value.openai.api_key = ""

        conv_id_1 = str(uuid4())
        conv_id_2 = str(uuid4())
        run_id_1 = str(uuid4())
        run_id_2 = str(uuid4())

        good_result = RegressionResult(
            turns_compared=1,
            avg_source_rating=None,
            avg_new_rating=None,
            score_diff=None,
            new_conversation_id=str(uuid4()),
            avg_similarity=None,
        )
        mock_run_regression.side_effect = [good_result, RuntimeError("LLM error")]

        conn = AsyncMock()

        def _make_insert_result(run_id: str) -> MagicMock:
            r = MagicMock()
            r.first.return_value = _mock_row({"id": run_id})
            return r

        update_res = MagicMock()
        conn.execute = AsyncMock(
            side_effect=[
                _make_insert_result(run_id_1),
                update_res,
                _make_insert_result(run_id_2),
                update_res,  # UPDATE for failure case (sets status=failed)
            ]
        )
        mock_engine_fn.return_value = _patch_engine(conn)

        resp = client.post(
            "/admin/sandbox/regression-runs/batch",
            json={
                "conversation_ids": [conv_id_1, conv_id_2],
                "new_prompt_version_id": _PROMPT_VER_ID,
            },
            headers=_auth(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["completed"] == 1
        assert data["failed"] == 1
        failed = [r for r in data["results"] if r["status"] == "failed"]
        assert len(failed) == 1
        assert "error" in failed[0]

    @patch("src.api.auth.get_settings")
    def test_batch_too_many_conversations_returns_422(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        # max_length=20 enforced by Pydantic
        resp = client.post(
            "/admin/sandbox/regression-runs/batch",
            json={
                "conversation_ids": [str(uuid4()) for _ in range(21)],
                "new_prompt_version_id": _PROMPT_VER_ID,
            },
            headers=_auth(),
        )

        assert resp.status_code == 422

    @patch("src.api.auth.get_settings")
    def test_batch_empty_list_returns_422(
        self, mock_settings: MagicMock, client: TestClient
    ) -> None:
        mock_settings.return_value.admin.jwt_secret = _TEST_SECRET

        resp = client.post(
            "/admin/sandbox/regression-runs/batch",
            json={
                "conversation_ids": [],
                "new_prompt_version_id": _PROMPT_VER_ID,
            },
            headers=_auth(),
        )

        assert resp.status_code == 422

    def test_batch_no_auth_returns_401(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sandbox/regression-runs/batch",
            json={
                "conversation_ids": [str(uuid4())],
                "new_prompt_version_id": _PROMPT_VER_ID,
            },
        )
        assert resp.status_code == 401
