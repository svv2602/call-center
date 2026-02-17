"""Unit tests for training response templates API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.training_templates import (
    TEMPLATE_KEYS,
    TemplateCreateRequest,
    TemplateUpdateRequest,
)


class TestTemplateKeys:
    """Test template key definitions."""

    def test_template_keys_complete(self) -> None:
        expected = [
            "greeting",
            "farewell",
            "silence_prompt",
            "transfer",
            "error",
            "wait",
            "order_cancelled",
        ]
        assert expected == TEMPLATE_KEYS

    def test_template_keys_count(self) -> None:
        assert len(TEMPLATE_KEYS) == 7


class TestTemplateCreateRequest:
    """Test Pydantic request model."""

    def test_valid_request(self) -> None:
        req = TemplateCreateRequest(
            template_key="greeting",
            title="Приветствие",
            content="Добрий день!",
        )
        assert req.template_key == "greeting"
        assert req.description is None

    def test_with_description(self) -> None:
        req = TemplateCreateRequest(
            template_key="farewell",
            title="Прощание",
            content="До побачення!",
            description="Farewell message",
        )
        assert req.description == "Farewell message"


class TestTemplateUpdateRequest:
    """Test Pydantic update model."""

    def test_all_optional(self) -> None:
        req = TemplateUpdateRequest()
        assert req.title is None
        assert req.content is None
        assert req.is_active is None

    def test_partial_update(self) -> None:
        req = TemplateUpdateRequest(content="New content")
        assert req.content == "New content"
        assert req.title is None


def _make_engine_and_conn():
    """Create mock engine + conn with asynccontextmanager for begin()."""
    mock_conn = AsyncMock()
    mock_engine = AsyncMock()

    @asynccontextmanager
    async def fake_begin():
        yield mock_conn

    mock_engine.begin = fake_begin
    return mock_engine, mock_conn


class TestTemplateEndpoints:
    """Test API endpoint logic."""

    @pytest.mark.asyncio
    async def test_create_validates_template_key(self) -> None:
        """Invalid template_key should raise 400."""
        from fastapi import HTTPException

        from src.api.training_templates import create_template

        req = TemplateCreateRequest(
            template_key="invalid_key",
            title="Test",
            content="Test",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_template(req, {})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_no_fields_raises_400(self) -> None:
        """Empty update request should raise 400."""
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.training_templates import update_template

        with patch("src.api.training_templates._get_engine", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_template(uuid4(), TemplateUpdateRequest(), {})
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_auto_assigns_variant_number(self) -> None:
        """New template gets next variant_number for its key."""
        from src.api.training_templates import create_template

        mock_engine, mock_conn = _make_engine_and_conn()

        # First call: max variant query returns 2
        max_result = MagicMock()
        max_result.first.return_value = MagicMock(max_variant=2)

        # Second call: INSERT RETURNING
        insert_result = MagicMock()
        insert_result.first.return_value = MagicMock(
            _mapping={
                "id": "new-id",
                "template_key": "greeting",
                "variant_number": 3,
                "title": "Test",
                "is_active": True,
                "created_at": "2026-01-01",
            }
        )

        mock_conn.execute = AsyncMock(side_effect=[max_result, insert_result])

        with patch(
            "src.api.training_templates._get_engine",
            new_callable=AsyncMock,
            return_value=mock_engine,
        ):
            req = TemplateCreateRequest(
                template_key="greeting",
                title="Test",
                content="Hello!",
            )
            result = await create_template(req, {})
            assert result["item"]["variant_number"] == 3

    @pytest.mark.asyncio
    async def test_delete_prevents_last_variant(self) -> None:
        """Cannot delete the last variant for a key."""
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.training_templates import delete_template

        mock_engine, mock_conn = _make_engine_and_conn()

        # First call: find template
        tpl_result = MagicMock()
        tpl_result.first.return_value = MagicMock(template_key="greeting")

        # Second call: count = 1
        count_result = MagicMock()
        count_result.first.return_value = MagicMock(cnt=1)

        mock_conn.execute = AsyncMock(side_effect=[tpl_result, count_result])

        with patch(
            "src.api.training_templates._get_engine",
            new_callable=AsyncMock,
            return_value=mock_engine,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_template(uuid4(), {})
            assert exc_info.value.status_code == 400
            assert "last variant" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_delete_allows_when_multiple_variants(self) -> None:
        """Can delete when more than one variant exists."""
        from uuid import uuid4

        from src.api.training_templates import delete_template

        mock_engine, mock_conn = _make_engine_and_conn()

        # First call: find template
        tpl_result = MagicMock()
        tpl_result.first.return_value = MagicMock(template_key="greeting")

        # Second call: count = 3
        count_result = MagicMock()
        count_result.first.return_value = MagicMock(cnt=3)

        # Third call: DELETE
        delete_result = AsyncMock()

        mock_conn.execute = AsyncMock(side_effect=[tpl_result, count_result, delete_result])

        with patch(
            "src.api.training_templates._get_engine",
            new_callable=AsyncMock,
            return_value=mock_engine,
        ):
            result = await delete_template(uuid4(), {})
            assert "deleted" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_not_found(self) -> None:
        """Delete nonexistent template raises 404."""
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.training_templates import delete_template

        mock_engine, mock_conn = _make_engine_and_conn()

        tpl_result = MagicMock()
        tpl_result.first.return_value = None

        mock_conn.execute = AsyncMock(return_value=tpl_result)

        with patch(
            "src.api.training_templates._get_engine",
            new_callable=AsyncMock,
            return_value=mock_engine,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_template(uuid4(), {})
            assert exc_info.value.status_code == 404


class TestPromptManagerRandomVariants:
    """Test random variant selection in prompt_manager."""

    @pytest.mark.asyncio
    async def test_random_variant_selection(self) -> None:
        """get_active_templates picks one variant per key."""
        from src.agent.prompt_manager import PromptManager

        mock_engine = AsyncMock()
        mock_conn = AsyncMock()

        @asynccontextmanager
        async def fake_begin():
            yield mock_conn

        mock_engine.begin = fake_begin

        # Simulate 2 greeting variants + 1 farewell
        rows = [
            MagicMock(template_key="greeting", content="Привіт!"),
            MagicMock(template_key="greeting", content="Добрий день!"),
            MagicMock(template_key="farewell", content="До побачення!"),
        ]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows
        mock_conn.execute = AsyncMock(return_value=mock_result)

        pm = PromptManager(mock_engine)
        templates = await pm.get_active_templates()

        # greeting should be one of the two variants
        assert templates["greeting"] in ["Привіт!", "Добрий день!"]
        # farewell has only one variant
        assert templates["farewell"] == "До побачення!"
        # Missing keys should fall back to hardcoded
        assert "silence_prompt" in templates
        assert "error" in templates
