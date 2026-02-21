"""Unit tests for 1C API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.onec_client.client import OneCAPIError, OneCClient


@pytest.fixture
def onec_client() -> OneCClient:
    return OneCClient(
        base_url="http://192.168.11.9",
        username="web_service",
        password="44332211",
        timeout=10,
    )


class TestOneCClientInit:
    def test_base_url_strips_trailing_slash(self) -> None:
        client = OneCClient("http://host:8080/", "user", "pass")
        assert client._base_url == "http://host:8080"

    def test_basic_auth_configured(self, onec_client: OneCClient) -> None:
        assert onec_client._auth.login == "web_service"
        assert onec_client._auth.password == "44332211"


class TestOneCClientNotOpened:
    @pytest.mark.asyncio
    async def test_request_before_open_raises(self, onec_client: OneCClient) -> None:
        with pytest.raises(RuntimeError, match="not opened"):
            await onec_client.get_wares_full()


class TestOneCClientRequests:
    @pytest.mark.asyncio
    async def test_get_wares_full(self, onec_client: OneCClient) -> None:
        mock_response = {"success": True, "data": [{"model_id": "123"}], "errors": []}
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_wares_full()
            mock_get.assert_called_once_with(
                "/Trade/hs/site/get_wares/",
                params={"UploadingAll": ""},
            )
            assert result["success"] is True
            assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_get_wares_full_with_limit(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_get:
            await onec_client.get_wares_full(limit=100)
            mock_get.assert_called_once_with(
                "/Trade/hs/site/get_wares/",
                params={"UploadingAll": "", "limit": 100},
            )

    @pytest.mark.asyncio
    async def test_get_wares_incremental(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_get:
            await onec_client.get_wares_incremental("ProKoleso")
            mock_get.assert_called_once_with(
                "/Trade/hs/site/get_wares/",
                params={"TradingNetwork": "ProKoleso"},
            )

    @pytest.mark.asyncio
    async def test_confirm_wares_receipt(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value={"success": True}
        ) as mock_get:
            await onec_client.confirm_wares_receipt("Tshina")
            mock_get.assert_called_once_with(
                "/Trade/hs/site/get_wares/",
                params={"TradingNetwork": "Tshina", "ConfirmationOfReceipt": ""},
            )

    @pytest.mark.asyncio
    async def test_get_wares_by_sku(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_get:
            await onec_client.get_wares_by_sku("00-00001688")
            mock_get.assert_called_once_with(
                "/Trade/hs/site/get_wares/",
                params={"sku": "00-00001688"},
            )

    @pytest.mark.asyncio
    async def test_get_stock(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "TradingNetwork": "ProKoleso",
            "data": [{"sku": "00000000023", "price": "2540", "stock": "8"}],
        }
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_stock("ProKoleso")
            mock_get.assert_called_once_with("/Trade/hs/site/get_stock/ProKoleso")
            assert result["data"][0]["sku"] == "00000000023"

    @pytest.mark.asyncio
    async def test_get_novapost_cities(self, onec_client: OneCClient) -> None:
        mock_response = {"success": True, "data": [{"Ref": "abc", "Description": "Київ"}]}
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_novapost_cities()
            mock_get.assert_called_once_with("/Trade/hs/site/novapost/city")
            assert result["data"][0]["Description"] == "Київ"

    @pytest.mark.asyncio
    async def test_get_novapost_branches(self, onec_client: OneCClient) -> None:
        mock_response = {"success": True, "data": [{"Ref": "xyz", "Description": "Відділення №1"}]}
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_novapost_branches()
            mock_get.assert_called_once_with("/Trade/hs/site/novapost/branch")
            assert result["data"][0]["Ref"] == "xyz"


    @pytest.mark.asyncio
    async def test_get_pickup_points(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "data": [
                {
                    "id": "000000054",
                    "point": "вул. Академіка Заболотного 3",
                    "point_type": "Стороння точка",
                    "City": "Київ",
                    "CityRef": "8d5a980d-0000-0000-0000-000000000000",
                }
            ],
            "errors": [],
        }
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_pickup_points("ProKoleso")
            mock_get.assert_called_once_with(
                "/Trade/hs/site/points/",
                params={"TradingNetwork": "ProKoleso"},
            )
            assert result["success"] is True
            assert result["data"][0]["id"] == "000000054"
            assert result["data"][0]["City"] == "Київ"

    @pytest.mark.asyncio
    async def test_get_pickup_points_tshina(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_get:
            await onec_client.get_pickup_points("Tshina")
            mock_get.assert_called_once_with(
                "/Trade/hs/site/points/",
                params={"TradingNetwork": "Tshina"},
            )


class TestOneCAPIError:
    def test_error_message(self) -> None:
        err = OneCAPIError(401, "Unauthorized")
        assert err.status == 401
        assert "401" in str(err)
        assert "Unauthorized" in str(err)

    def test_error_is_exception(self) -> None:
        err = OneCAPIError(500, "Internal error")
        assert isinstance(err, Exception)
