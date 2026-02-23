"""Unit tests for sandbox API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.api.sandbox import (
    BulkDeleteRequest,
    ConversationCreate,
    ConversationUpdate,
    RateTurn,
    SendMessage,
)


class TestPydanticModels:
    """Test request model validation."""

    def test_conversation_create_valid(self) -> None:
        req = ConversationCreate(title="Test conversation")
        assert req.title == "Test conversation"
        assert req.tool_mode == "mock"
        assert req.tags == []

    def test_conversation_create_with_all_fields(self) -> None:
        req = ConversationCreate(
            title="Full test",
            prompt_version_id="12345678-1234-1234-1234-123456789012",
            tool_mode="live",
            tags=["test", "qa"],
            scenario_type="tire_search",
        )
        assert req.tool_mode == "live"
        assert len(req.tags) == 2

    def test_conversation_create_empty_title_fails(self) -> None:
        with pytest.raises(ValidationError):
            ConversationCreate(title="")

    def test_conversation_update_all_optional(self) -> None:
        req = ConversationUpdate()
        assert req.title is None
        assert req.status is None

    def test_send_message_valid(self) -> None:
        req = SendMessage(message="Привіт, підберіть шини")
        assert req.message == "Привіт, підберіть шини"
        assert req.parent_turn_id is None

    def test_send_message_empty_fails(self) -> None:
        with pytest.raises(ValidationError):
            SendMessage(message="")

    def test_rate_turn_valid(self) -> None:
        req = RateTurn(rating=5, comment="Excellent response")
        assert req.rating == 5

    def test_rate_turn_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            RateTurn(rating=6)

    def test_rate_turn_zero_fails(self) -> None:
        with pytest.raises(ValidationError):
            RateTurn(rating=0)


class TestConversationEndpoints:
    """Test API endpoint logic with mocked DB."""

    @pytest.fixture
    def mock_engine(self) -> AsyncMock:
        engine = AsyncMock()
        conn = AsyncMock()
        engine.begin.return_value.__aenter__ = AsyncMock(return_value=conn)
        engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        return engine

    @pytest.mark.asyncio
    async def test_update_validates_status(self) -> None:
        """Invalid status should raise 400."""
        from fastapi import HTTPException

        from src.api.sandbox import update_conversation

        req = ConversationUpdate(status="invalid_status")
        with pytest.raises(HTTPException) as exc_info:
            await update_conversation("12345678-1234-1234-1234-123456789012", req, {})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_no_fields_raises_400(self) -> None:
        """Empty update request should raise 400."""
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.sandbox import update_conversation

        req = ConversationUpdate()
        with pytest.raises(HTTPException) as exc_info:
            await update_conversation(uuid4(), req, {})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rate_validates_range(self) -> None:
        """Rating outside 1-5 should fail at Pydantic level."""
        with pytest.raises(ValidationError):
            RateTurn(rating=10)

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        """Deleting non-existent conversation should raise 404."""
        from contextlib import asynccontextmanager
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.sandbox import delete_conversation

        mock_conn = AsyncMock()
        # First call: regression check (no refs), second call: delete (not found)
        no_refs = MagicMock()
        no_refs.first.return_value = None
        not_found = MagicMock()
        not_found.first.return_value = None
        mock_conn.execute.side_effect = [no_refs, not_found]

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin

        import src.api.sandbox as sandbox_module

        original_engine = sandbox_module._engine
        sandbox_module._engine = mock_engine

        try:
            with pytest.raises(HTTPException) as exc_info:
                await delete_conversation(uuid4(), {})
            assert exc_info.value.status_code == 404
        finally:
            sandbox_module._engine = original_engine

    @pytest.mark.asyncio
    async def test_delete_blocked_by_regression_run(self) -> None:
        """Deleting a conversation referenced by regression runs should raise 409."""
        from contextlib import asynccontextmanager
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.sandbox import delete_conversation

        mock_conn = AsyncMock()
        # Regression check returns a row → block deletion
        has_ref = MagicMock()
        has_ref.first.return_value = MagicMock()  # truthy = reference exists
        mock_conn.execute.return_value = has_ref

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin

        import src.api.sandbox as sandbox_module

        original_engine = sandbox_module._engine
        sandbox_module._engine = mock_engine

        try:
            with pytest.raises(HTTPException) as exc_info:
                await delete_conversation(uuid4(), {})
            assert exc_info.value.status_code == 409
            assert "regression" in exc_info.value.detail.lower()
        finally:
            sandbox_module._engine = original_engine

    @pytest.mark.asyncio
    async def test_delete_success(self) -> None:
        """Deleting a conversation with no regression refs should succeed."""
        from contextlib import asynccontextmanager
        from uuid import uuid4

        from src.api.sandbox import delete_conversation

        mock_conn = AsyncMock()
        # First call: regression check (no refs)
        no_refs = MagicMock()
        no_refs.first.return_value = None
        # Second call: delete returns the row
        deleted_row = MagicMock()
        deleted_row.title = "Test Chat"
        deleted_row._mapping = {"id": str(uuid4()), "title": "Test Chat"}
        deleted_row.first.return_value = deleted_row
        mock_conn.execute.side_effect = [no_refs, deleted_row]

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin

        import src.api.sandbox as sandbox_module

        original_engine = sandbox_module._engine
        sandbox_module._engine = mock_engine

        try:
            result = await delete_conversation(uuid4(), {})
            assert "Test Chat" in result["message"]
            assert mock_conn.execute.call_count == 2
        finally:
            sandbox_module._engine = original_engine


class TestBulkDelete:
    """Test bulk delete endpoint."""

    def test_bulk_delete_request_valid(self) -> None:
        req = BulkDeleteRequest(
            conversation_ids=[
                "12345678-1234-1234-1234-123456789012",
                "22345678-1234-1234-1234-123456789012",
            ]
        )
        assert len(req.conversation_ids) == 2

    def test_bulk_delete_request_empty_list_fails(self) -> None:
        with pytest.raises(ValidationError):
            BulkDeleteRequest(conversation_ids=[])

    @pytest.mark.asyncio
    async def test_bulk_delete_success(self) -> None:
        """Delete 2 conversations with no regression refs."""
        from contextlib import asynccontextmanager
        from uuid import uuid4

        from src.api.sandbox import bulk_delete_conversations

        cid1, cid2 = uuid4(), uuid4()

        mock_conn = AsyncMock()
        # First call: regression refs query (empty result)
        no_refs = MagicMock()
        no_refs.__iter__ = MagicMock(return_value=iter([]))
        # Second call: DELETE returns 2 rows
        del_result = MagicMock()
        del_result.rowcount = 2
        mock_conn.execute.side_effect = [no_refs, del_result]

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin

        import src.api.sandbox as sandbox_module

        original_engine = sandbox_module._engine
        sandbox_module._engine = mock_engine

        try:
            req = BulkDeleteRequest(conversation_ids=[cid1, cid2])
            result = await bulk_delete_conversations(req, {})
            assert result["deleted"] == 2
            assert result["skipped"] == 0
            assert result["skipped_ids"] == []
        finally:
            sandbox_module._engine = original_engine

    @pytest.mark.asyncio
    async def test_bulk_delete_skips_regression_refs(self) -> None:
        """One of two conversations is protected by regression runs."""
        from contextlib import asynccontextmanager
        from uuid import uuid4

        from src.api.sandbox import bulk_delete_conversations

        cid1, cid2 = uuid4(), uuid4()

        mock_conn = AsyncMock()
        # First call: regression refs query returns cid1 as protected
        protected_row = MagicMock()
        protected_row.id = cid1
        refs_result = MagicMock()
        refs_result.__iter__ = MagicMock(return_value=iter([protected_row]))
        # Second call: DELETE returns 1 row (only cid2 deleted)
        del_result = MagicMock()
        del_result.rowcount = 1
        mock_conn.execute.side_effect = [refs_result, del_result]

        @asynccontextmanager
        async def mock_begin():
            yield mock_conn

        mock_engine = MagicMock()
        mock_engine.begin = mock_begin

        import src.api.sandbox as sandbox_module

        original_engine = sandbox_module._engine
        sandbox_module._engine = mock_engine

        try:
            req = BulkDeleteRequest(conversation_ids=[cid1, cid2])
            result = await bulk_delete_conversations(req, {})
            assert result["deleted"] == 1
            assert result["skipped"] == 1
            assert str(cid1) in result["skipped_ids"]
        finally:
            sandbox_module._engine = original_engine
