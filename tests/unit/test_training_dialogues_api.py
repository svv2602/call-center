"""Unit tests for training dialogues API."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.api.training_dialogues import (
    PHASES,
    SCENARIO_TYPES,
    DialogueCreateRequest,
    DialogueUpdateRequest,
)


class TestDialogueEnums:
    """Test enum definitions."""

    def test_scenario_types_complete(self) -> None:
        assert "tire_search" in SCENARIO_TYPES
        assert "order_creation" in SCENARIO_TYPES
        assert "fitting_booking" in SCENARIO_TYPES
        assert len(SCENARIO_TYPES) == 7

    def test_phases_complete(self) -> None:
        assert PHASES == ["mvp", "orders", "services"]


class TestDialogueCreateRequest:
    """Test Pydantic request model."""

    def test_valid_request(self) -> None:
        req = DialogueCreateRequest(
            title="Test dialogue",
            scenario_type="tire_search",
            phase="mvp",
            dialogue=[{"role": "customer", "text": "Hello"}],
        )
        assert req.title == "Test dialogue"
        assert req.sort_order == 0

    def test_optional_fields(self) -> None:
        req = DialogueCreateRequest(
            title="Test",
            scenario_type="tire_search",
            phase="mvp",
            dialogue=[{"role": "customer", "text": "hello"}],
            tools_used=["search_tires"],
            description="Test desc",
            sort_order=5,
        )
        assert req.tools_used == ["search_tires"]
        assert req.sort_order == 5


class TestDialogueUpdateRequest:
    """Test Pydantic update model."""

    def test_all_optional(self) -> None:
        req = DialogueUpdateRequest()
        assert req.title is None
        assert req.scenario_type is None
        assert req.is_active is None

    def test_partial_update(self) -> None:
        req = DialogueUpdateRequest(title="Updated", is_active=False)
        assert req.title == "Updated"
        assert req.is_active is False


class TestDialogueEndpoints:
    """Test API endpoint logic with mocked DB."""

    @pytest.fixture
    def mock_engine(self) -> AsyncMock:
        engine = AsyncMock()
        conn = AsyncMock()
        engine.begin.return_value.__aenter__ = AsyncMock(return_value=conn)
        engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
        return engine

    @pytest.mark.asyncio
    async def test_create_validates_scenario_type(self) -> None:
        """Invalid scenario_type should raise 400."""
        from fastapi import HTTPException

        from src.api.training_dialogues import create_dialogue

        req = DialogueCreateRequest(
            title="Test",
            scenario_type="invalid_type",
            phase="mvp",
            dialogue=[{"role": "customer", "text": "hello"}],
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_dialogue(req, {})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_validates_phase(self) -> None:
        """Invalid phase should raise 400."""
        from fastapi import HTTPException

        from src.api.training_dialogues import create_dialogue

        req = DialogueCreateRequest(
            title="Test",
            scenario_type="tire_search",
            phase="invalid_phase",
            dialogue=[{"role": "customer", "text": "hello"}],
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_dialogue(req, {})
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_no_fields_raises_400(self) -> None:
        """Empty update request should raise 400."""
        from uuid import uuid4

        from fastapi import HTTPException

        from src.api.training_dialogues import update_dialogue

        with patch("src.api.training_dialogues._get_engine", new_callable=AsyncMock):
            with pytest.raises(HTTPException) as exc_info:
                await update_dialogue(uuid4(), DialogueUpdateRequest(), {})
            assert exc_info.value.status_code == 400
