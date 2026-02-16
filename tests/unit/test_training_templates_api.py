"""Unit tests for training response templates API."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    async def test_create_checks_uniqueness(self) -> None:
        """Duplicate template_key should raise 409."""
        from contextlib import asynccontextmanager

        from fastapi import HTTPException

        from src.api.training_templates import create_template

        mock_conn = AsyncMock()

        # Simulate existing row found
        mock_result = AsyncMock()
        mock_result.first.return_value = {"id": "existing-id"}
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = AsyncMock()

        @asynccontextmanager
        async def fake_begin():
            yield mock_conn

        mock_engine.begin = fake_begin

        with patch(
            "src.api.training_templates._get_engine",
            new_callable=AsyncMock,
            return_value=mock_engine,
        ):
            req = TemplateCreateRequest(
                template_key="greeting",
                title="Test",
                content="Test",
            )
            with pytest.raises(HTTPException) as exc_info:
                await create_template(req, {})
            assert exc_info.value.status_code == 409
