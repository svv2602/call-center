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
            mock_get.assert_called_once_with(
                "/Trade/hs/site/get_stock/",
                params={"TradingNetwork": "ProKoleso"},
            )
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


class TestOneCClientFittingREST:
    @pytest.mark.asyncio
    async def test_get_fitting_stations_rest(self, onec_client: OneCClient) -> None:
        mock_response = {
            "data": [
                {
                    "StationID": "000000001",
                    "StationName": "Центральний",
                    "StationCity": "Київ",
                    "StationAdress": "вул. Хрещатик, 1",
                }
            ]
        }
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_fitting_stations_rest()
            mock_get.assert_called_once_with("/Trade/hs/site/TireService/Station")
            assert result["data"][0]["StationID"] == "000000001"

    @pytest.mark.asyncio
    async def test_find_storage_by_phone(self, onec_client: OneCClient) -> None:
        mock_response = {"contracts": [{"contract_number": "ДЗ-2025-0001"}], "total": 1}
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.find_storage(phone="0501234567")
            mock_get.assert_called_once_with(
                "/Trade/hs/site/TireService/findStorage",
                params={"phone": "0501234567"},
            )
            assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_find_storage_by_number(self, onec_client: OneCClient) -> None:
        mock_response = {"contracts": [{"contract_number": "ДЗ-2025-0001"}], "total": 1}
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.find_storage(storage_number="ДЗ-2025-0001")
            mock_get.assert_called_once_with(
                "/Trade/hs/site/TireService/findStorage",
                params={"StorageNumber": "ДЗ-2025-0001"},
            )
            assert result["contracts"][0]["contract_number"] == "ДЗ-2025-0001"

    @pytest.mark.asyncio
    async def test_find_storage_no_params(self, onec_client: OneCClient) -> None:
        mock_response = {"contracts": [], "total": 0}
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.find_storage()
            mock_get.assert_called_once_with(
                "/Trade/hs/site/TireService/findStorage",
                params={},
            )
            assert result["total"] == 0


class TestOneCClientTireServiceREST:
    """Tests for new TireService REST API methods."""

    @pytest.mark.asyncio
    async def test_get_station_schedule(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "data": [
                {
                    "StationID": "000000009",
                    "Data": "2026-03-09T00:00:00",
                    "Time": "0001-01-01T08:20:00",
                    "Period": "2026-03-09T08:20:00",
                    "Quantity": 0,
                },
                {
                    "StationID": "000000009",
                    "Data": "2026-03-09T00:00:00",
                    "Time": "0001-01-01T09:20:00",
                    "Period": "2026-03-09T09:20:00",
                    "Quantity": 1,
                },
            ],
            "errors": [],
        }
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_station_schedule(
                station_id="000000009",
                date_from="2026-03-09",
                date_to="2026-03-09",
            )
            mock_get.assert_called_once_with(
                "/Trade/hs/site/TireService/StationSchedule",
                params={
                    "StationID": "000000009",
                    "DataBig": "2026-03-09T00:00:00",
                    "DataEnd": "2026-03-09T00:00:00",
                },
            )
            assert result["success"] is True
            assert len(result["data"]) == 2
            assert result["data"][0]["Quantity"] == 0
            assert result["data"][1]["Quantity"] == 1

    @pytest.mark.asyncio
    async def test_get_station_schedule_datetime_passthrough(self, onec_client: OneCClient) -> None:
        """If date already has T, don't add another T00:00:00."""
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value={"data": []}
        ) as mock_get:
            await onec_client.get_station_schedule(
                station_id="000000001",
                date_from="2026-03-07T00:00:00",
                date_to="2026-03-10T23:59:59",
            )
            mock_get.assert_called_once_with(
                "/Trade/hs/site/TireService/StationSchedule",
                params={
                    "StationID": "000000001",
                    "DataBig": "2026-03-07T00:00:00",
                    "DataEnd": "2026-03-10T23:59:59",
                },
            )

    @pytest.mark.asyncio
    async def test_book_fitting_rest(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "data": [{"GUID": "35fe605b-186f-11f1-9733-00155d021200"}],
            "errors": [],
        }
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            result = await onec_client.book_fitting_rest(
                person="Артем",
                phone="+380672677825",
                station_id="000000019",
                date="2026-03-10",
                time="09:00",
                vehicle_info="БМВ",
                auto_number="11111",
            )
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            body = call_args[1]["json_data"]
            assert body["Person"] == "Артем"
            assert body["PhoneNumber"] == "+380672677825"
            assert body["StationID"] == "000000019"
            assert body["Date"] == "2026-03-10T00:00:00"
            assert body["Time"] == "0001-01-01T09:00:00"
            assert body["AutoType"] == "БМВ"
            assert body["AutoNumber"] == "11111"
            assert body["Status"] == "Записан"
            assert body["CheckBalance"] is True
            assert body["StoreTires"] is False
            assert result["success"] is True
            assert result["data"][0]["GUID"] == "35fe605b-186f-11f1-9733-00155d021200"

    @pytest.mark.asyncio
    async def test_book_fitting_rest_phone_normalization(self, onec_client: OneCClient) -> None:
        """Phone 0XXXXXXXXX should be converted to +380XXXXXXXXX."""
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_post:
            await onec_client.book_fitting_rest(
                person="Test",
                phone="0672677825",
                station_id="000000001",
                date="2026-03-10",
                time="09:00",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["PhoneNumber"] == "+380672677825"

    @pytest.mark.asyncio
    async def test_book_fitting_rest_comment_built(self, onec_client: OneCClient) -> None:
        """Comment should include vehicle info, diameter, and service type."""
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_post:
            await onec_client.book_fitting_rest(
                person="Test",
                phone="+380501234567",
                station_id="000000001",
                date="2026-03-10",
                time="09:00",
                vehicle_info="Toyota Camry",
                tire_diameter=16,
                service_type="full_service",
            )
            body = mock_post.call_args[1]["json_data"]
            assert "Toyota Camry" in body["Comment"]
            assert "R16" in body["Comment"]
            assert "повний сервіс" in body["Comment"]

    @pytest.mark.asyncio
    async def test_cancel_fitting_rest(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "data": [{"Canceled": True}],
            "errors": [],
        }
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            result = await onec_client.cancel_fitting_rest("50c98a6c-17a8-11f1-9733-00155d021200")
            mock_post.assert_called_once_with(
                "/Trade/hs/site/TireService/CancelRecord",
                json_data={"GUID": "50c98a6c-17a8-11f1-9733-00155d021200"},
            )
            assert result["success"] is True
            assert result["data"][0]["Canceled"] is True

    @pytest.mark.asyncio
    async def test_get_customer_bookings_rest(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "data": [
                {
                    "StationID": "000000019",
                    "Data": "2026-03-07T00:00:00",
                    "Time": "0001-01-01T09:40:00",
                    "Period": "2026-03-07T09:40:00",
                    "Customer": "Артем",
                    "AutoType": "",
                    "AutoNumber": "AE7826CX",
                    "GUID": "6ea6bbe4-179e-11f1-9733-00155d021200",
                }
            ],
            "errors": [],
        }
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            result = await onec_client.get_customer_bookings_rest(
                phone="+380668949063",
                station_id="000000019",
            )
            mock_post.assert_called_once()
            body = mock_post.call_args[1]["json_data"]
            assert body["PhoneNumber"] == "+380668949063"
            assert body["StationID"] == "000000019"
            assert result["data"][0]["GUID"] == "6ea6bbe4-179e-11f1-9733-00155d021200"
            assert result["data"][0]["Customer"] == "Артем"

    @pytest.mark.asyncio
    async def test_get_customer_bookings_rest_phone_only(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_post:
            await onec_client.get_customer_bookings_rest(phone="0501234567")
            body = mock_post.call_args[1]["json_data"]
            assert body["PhoneNumber"] == "+380501234567"
            assert body["StationID"] == ""

    @pytest.mark.asyncio
    async def test_reserve_fitting_slot(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "data": [{"GUID": "f74b4a52-1870-11f1-9733-00155d021200"}],
            "errors": [],
        }
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value=mock_response
        ) as mock_post:
            result = await onec_client.reserve_fitting_slot(
                station_id="000000019",
                date="2026-03-11",
                time="09:00",
                comment="тест",
            )
            mock_post.assert_called_once_with(
                "/Trade/hs/site/TireService/TireBooking",
                json_data={
                    "StationID": "000000019",
                    "Date": "2026-03-11T00:00:00",
                    "Time": "0001-01-01T09:00:00",
                    "Comment": "тест",
                },
            )
            assert result["success"] is True
            assert result["data"][0]["GUID"] == "f74b4a52-1870-11f1-9733-00155d021200"

    @pytest.mark.asyncio
    async def test_reserve_fitting_slot_no_comment(self, onec_client: OneCClient) -> None:
        with patch.object(
            onec_client, "_post", new_callable=AsyncMock, return_value={"success": True, "data": []}
        ) as mock_post:
            await onec_client.reserve_fitting_slot(
                station_id="000000001",
                date="2026-03-12",
                time="10:30",
            )
            body = mock_post.call_args[1]["json_data"]
            assert body["Comment"] == ""


class TestOneCClientHelpers:
    """Tests for helper functions used by TireService REST methods."""

    def test_normalize_phone_plus_from_0(self) -> None:
        from src.onec_client.client import _normalize_phone_plus

        assert _normalize_phone_plus("0672677825") == "+380672677825"

    def test_normalize_phone_plus_from_380(self) -> None:
        from src.onec_client.client import _normalize_phone_plus

        assert _normalize_phone_plus("380672677825") == "+380672677825"

    def test_normalize_phone_plus_already_plus(self) -> None:
        from src.onec_client.client import _normalize_phone_plus

        assert _normalize_phone_plus("+380672677825") == "+380672677825"

    def test_normalize_phone_plus_from_80(self) -> None:
        from src.onec_client.client import _normalize_phone_plus

        assert _normalize_phone_plus("80672677825") == "+380672677825"

    def test_to_datetime_date_only(self) -> None:
        from src.onec_client.client import _to_datetime

        assert _to_datetime("2026-03-09") == "2026-03-09T00:00:00"

    def test_to_datetime_already_has_t(self) -> None:
        from src.onec_client.client import _to_datetime

        assert _to_datetime("2026-03-09T12:30:00") == "2026-03-09T12:30:00"

    def test_to_1c_time_hhmm(self) -> None:
        from src.onec_client.client import _to_1c_time

        assert _to_1c_time("09:00") == "0001-01-01T09:00:00"

    def test_to_1c_time_hhmmss(self) -> None:
        from src.onec_client.client import _to_1c_time

        assert _to_1c_time("09:00:00") == "0001-01-01T09:00:00"

    def test_to_1c_time_already_1c_format(self) -> None:
        from src.onec_client.client import _to_1c_time

        assert _to_1c_time("0001-01-01T09:00:00") == "0001-01-01T09:00:00"

    def test_build_comment_all_fields(self) -> None:
        from src.onec_client.client import _build_comment

        result = _build_comment("Toyota Camry", 16, "balancing")
        assert "Авто: Toyota Camry" in result
        assert "R16" in result
        assert "балансування" in result

    def test_build_comment_tire_change_no_label(self) -> None:
        from src.onec_client.client import _build_comment

        result = _build_comment("", 0, "tire_change")
        assert result == ""

    def test_build_comment_vehicle_only(self) -> None:
        from src.onec_client.client import _build_comment

        result = _build_comment("BMW X5", 0, "tire_change")
        assert result == "Авто: BMW X5"


class TestOneCClientFittingPrices:
    @pytest.mark.asyncio
    async def test_get_fitting_prices(self, onec_client: OneCClient) -> None:
        mock_response = {
            "success": True,
            "data": [
                {
                    "service": "Шиномонтаж R14",
                    "artikul": "SM-R14",
                    "price": "350",
                    "point_id": "ST-001",
                    "city": "Київ",
                    "city_id": "city-001",
                },
                {
                    "service": "Шиномонтаж R16",
                    "artikul": "SM-R16",
                    "price": "450",
                    "point_id": "ST-002",
                    "city": "Одеса",
                    "city_id": "city-002",
                },
            ],
        }
        with patch.object(
            onec_client, "_get", new_callable=AsyncMock, return_value=mock_response
        ) as mock_get:
            result = await onec_client.get_fitting_prices()
            mock_get.assert_called_once_with("/Trade/hs/site/price_service")
            assert result["success"] is True
            assert len(result["data"]) == 2
            assert result["data"][0]["point_id"] == "ST-001"
            assert result["data"][1]["price"] == "450"


class TestOneCAPIError:
    def test_error_message(self) -> None:
        err = OneCAPIError(401, "Unauthorized")
        assert err.status == 401
        assert "401" in str(err)
        assert "Unauthorized" in str(err)

    def test_error_is_exception(self) -> None:
        err = OneCAPIError(500, "Internal error")
        assert isinstance(err, Exception)
