"""Unit tests for X-Forwarded-For validation in rate_limit middleware."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestGetClientIp:
    """Test _get_client_ip with trusted proxy validation."""

    def _make_request(
        self, client_host: str, xff: str | None = None
    ) -> MagicMock:
        request = MagicMock()
        request.client.host = client_host
        headers: dict[str, str] = {}
        if xff is not None:
            headers["X-Forwarded-For"] = xff
        request.headers = headers
        return request

    @patch("src.config.get_settings")
    def test_xff_from_trusted_proxy_used(self, mock_settings: MagicMock) -> None:
        """XFF from trusted proxy (127.0.0.1) should return the XFF value."""
        mock_settings.return_value.trusted_proxy.ips = "127.0.0.1"
        from src.api.middleware.rate_limit import _get_client_ip

        request = self._make_request("127.0.0.1", xff="203.0.113.50")
        assert _get_client_ip(request) == "203.0.113.50"

    @patch("src.config.get_settings")
    def test_xff_from_untrusted_ip_ignored(self, mock_settings: MagicMock) -> None:
        """XFF from untrusted IP should be ignored; client.host used instead."""
        mock_settings.return_value.trusted_proxy.ips = "127.0.0.1"
        from src.api.middleware.rate_limit import _get_client_ip

        request = self._make_request("203.0.113.99", xff="10.0.0.1")
        assert _get_client_ip(request) == "203.0.113.99"

    @patch("src.config.get_settings")
    def test_no_xff_returns_client_host(self, mock_settings: MagicMock) -> None:
        """Without XFF header, should return client.host."""
        mock_settings.return_value.trusted_proxy.ips = "127.0.0.1"
        from src.api.middleware.rate_limit import _get_client_ip

        request = self._make_request("192.168.1.100")
        assert _get_client_ip(request) == "192.168.1.100"

    @patch("src.config.get_settings")
    def test_cidr_trusted_proxy(self, mock_settings: MagicMock) -> None:
        """Trusted proxy specified as CIDR range should be recognized."""
        mock_settings.return_value.trusted_proxy.ips = "172.16.0.0/12"
        from src.api.middleware.rate_limit import _get_client_ip

        request = self._make_request("172.18.0.1", xff="203.0.113.50")
        assert _get_client_ip(request) == "203.0.113.50"

    @patch("src.config.get_settings")
    def test_multiple_xff_returns_first(self, mock_settings: MagicMock) -> None:
        """Multiple XFF values: first (leftmost) is the original client."""
        mock_settings.return_value.trusted_proxy.ips = "127.0.0.1"
        from src.api.middleware.rate_limit import _get_client_ip

        request = self._make_request("127.0.0.1", xff="203.0.113.50, 10.0.0.1")
        assert _get_client_ip(request) == "203.0.113.50"
