"""Unit tests for audit logging middleware."""

from __future__ import annotations

from src.api.middleware.audit import _extract_resource


class TestExtractResource:
    """Test resource extraction from URL paths."""

    def test_simple_path(self) -> None:
        resource_type, resource_id = _extract_resource("/prompts")
        assert resource_type == "prompts"
        assert resource_id is None

    def test_path_with_id(self) -> None:
        resource_type, resource_id = _extract_resource("/prompts/123")
        assert resource_type == "prompts"
        assert resource_id == "123"

    def test_nested_path(self) -> None:
        resource_type, resource_id = _extract_resource("/admin/users/abc-123")
        assert resource_type == "admin/users"
        assert resource_id == "abc-123"

    def test_empty_path(self) -> None:
        resource_type, resource_id = _extract_resource("/")
        assert resource_type == ""
        assert resource_id is None
