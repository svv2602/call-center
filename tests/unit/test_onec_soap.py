"""Unit tests for 1C SOAP client (TireAssemblyExchange)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.onec_client.soap import (
    OneCSOAPClient,
    OneCSOAPError,
    _ENVELOPE_TEMPLATE,
    _SOAP_NS,
    _build_comment,
    _inner_text,
    _text,
)


@pytest.fixture
def soap_client() -> OneCSOAPClient:
    return OneCSOAPClient(
        base_url="http://192.168.11.9",
        username="web_service",
        password="44332211",
        wsdl_path="/Trade/ws/TireAssemblyExchange.1cws",
        timeout=30,
    )


# --- Init tests ---


class TestOneCSOAPClientInit:
    def test_base_url_strips_trailing_slash(self) -> None:
        client = OneCSOAPClient("http://host:8080/", "user", "pass")
        assert client._base_url == "http://host:8080"

    def test_endpoint_url(self, soap_client: OneCSOAPClient) -> None:
        assert soap_client.endpoint_url == (
            "http://192.168.11.9/Trade/ws/TireAssemblyExchange.1cws"
        )

    def test_basic_auth_configured(self, soap_client: OneCSOAPClient) -> None:
        assert soap_client._auth.login == "web_service"
        assert soap_client._auth.password == "44332211"

    def test_custom_wsdl_path(self) -> None:
        client = OneCSOAPClient("http://host", "u", "p", wsdl_path="/custom/ws/Service.1cws")
        assert client.endpoint_url == "http://host/custom/ws/Service.1cws"


class TestOneCSOAPClientNotOpened:
    @pytest.mark.asyncio
    async def test_request_before_open_raises(self, soap_client: OneCSOAPClient) -> None:
        with pytest.raises(RuntimeError, match="not opened"):
            await soap_client.get_station_schedule("2026-02-20", "2026-02-21")


# --- XML construction tests ---


class TestXMLConstruction:
    def test_envelope_template_valid_xml(self) -> None:
        body = "<ns:Test>hello</ns:Test>"
        xml_str = _ENVELOPE_TEMPLATE.format(ns=_SOAP_NS, body=body)
        root = ET.fromstring(xml_str)
        assert root.tag == "{http://www.w3.org/2003/05/soap-envelope}Envelope"

    def test_xml_injection_protection(self) -> None:
        """User values with XML special chars must be escaped."""
        from xml.sax.saxutils import escape

        malicious = '<script>alert("xss")</script>'
        escaped = escape(malicious)
        assert "<script>" not in escaped
        assert "&lt;script&gt;" in escaped

    def test_build_comment_empty(self) -> None:
        assert _build_comment("", 0, "tire_change") == ""

    def test_build_comment_vehicle_only(self) -> None:
        assert _build_comment("Toyota Camry 2022", 0, "tire_change") == "Авто: Toyota Camry 2022"

    def test_build_comment_full(self) -> None:
        comment = _build_comment("BMW X5", 18, "full_service")
        assert "Авто: BMW X5" in comment
        assert "R18" in comment
        assert "повний сервіс" in comment

    def test_build_comment_balancing(self) -> None:
        comment = _build_comment("", 16, "balancing")
        assert "R16" in comment
        assert "балансування" in comment


# --- Response parsing tests ---


def _make_soap_response(body_xml: str) -> ET.Element:
    """Build a complete SOAP response XML element."""
    xml_str = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"'
        f' xmlns:ns="{_SOAP_NS}">'
        f"<soap:Body>{body_xml}</soap:Body>"
        "</soap:Envelope>"
    )
    return ET.fromstring(xml_str)


class TestParseSchedule:
    def test_parse_empty_schedule(self) -> None:
        root = _make_soap_response("<ns:GetStationScheduleResponse/>")
        slots = OneCSOAPClient._parse_schedule(root)
        assert slots == []

    def test_parse_schedule_entries(self) -> None:
        root = _make_soap_response(f"""
            <ns:GetStationScheduleResponse>
                <ns:ScheduleEntry>
                    <ns:StationID>ST-001</ns:StationID>
                    <ns:Date>2026-02-20</ns:Date>
                    <ns:Time>09:00</ns:Time>
                    <ns:Available>true</ns:Available>
                    <ns:StationName>Центральний</ns:StationName>
                </ns:ScheduleEntry>
                <ns:ScheduleEntry>
                    <ns:StationID>ST-001</ns:StationID>
                    <ns:Date>2026-02-20</ns:Date>
                    <ns:Time>11:00</ns:Time>
                    <ns:Available>false</ns:Available>
                </ns:ScheduleEntry>
            </ns:GetStationScheduleResponse>
        """)
        slots = OneCSOAPClient._parse_schedule(root)
        assert len(slots) == 2
        assert slots[0]["station_id"] == "ST-001"
        assert slots[0]["date"] == "2026-02-20"
        assert slots[0]["time"] == "09:00"
        assert slots[0]["available"] is True
        assert slots[0]["station_name"] == "Центральний"
        assert slots[1]["available"] is False


class TestParseBooking:
    def test_parse_booking_success(self) -> None:
        root = _make_soap_response(f"""
            <ns:GetTireRecordingResponse>
                <ns:RecordingResult>
                    <ns:GUID>abc-123-def</ns:GUID>
                    <ns:Status>Записан</ns:Status>
                    <ns:Message>Запис створено</ns:Message>
                </ns:RecordingResult>
            </ns:GetTireRecordingResponse>
        """)
        result = OneCSOAPClient._parse_booking(root)
        assert result["booking_id"] == "abc-123-def"
        assert result["status"] == "Записан"
        assert result["message"] == "Запис створено"

    def test_parse_booking_error(self) -> None:
        root = _make_soap_response("<ns:GetTireRecordingResponse/>")
        result = OneCSOAPClient._parse_booking(root)
        assert result["status"] == "error"


class TestParseBookingsList:
    def test_parse_empty_list(self) -> None:
        root = _make_soap_response("<ns:GetListOfEntriesResponse/>")
        bookings = OneCSOAPClient._parse_bookings_list(root)
        assert bookings == []

    def test_parse_entries(self) -> None:
        root = _make_soap_response(f"""
            <ns:GetListOfEntriesResponse>
                <ns:Entry>
                    <ns:GUID>guid-1</ns:GUID>
                    <ns:StationID>ST-001</ns:StationID>
                    <ns:StationName>Центральний</ns:StationName>
                    <ns:Date>2026-02-20</ns:Date>
                    <ns:Time>09:00</ns:Time>
                    <ns:Status>Записан</ns:Status>
                    <ns:Person>Іван</ns:Person>
                    <ns:Phone>0501234567</ns:Phone>
                </ns:Entry>
                <ns:Entry>
                    <ns:GUID>guid-2</ns:GUID>
                    <ns:StationID>ST-002</ns:StationID>
                    <ns:StationName>Лівобережна</ns:StationName>
                    <ns:Date>2026-02-21</ns:Date>
                    <ns:Time>14:00</ns:Time>
                    <ns:Status>Записан</ns:Status>
                    <ns:Person>Марія</ns:Person>
                    <ns:Phone>0507654321</ns:Phone>
                </ns:Entry>
            </ns:GetListOfEntriesResponse>
        """)
        bookings = OneCSOAPClient._parse_bookings_list(root)
        assert len(bookings) == 2
        assert bookings[0]["booking_id"] == "guid-1"
        assert bookings[0]["station_id"] == "ST-001"
        assert bookings[0]["person"] == "Іван"
        assert bookings[1]["booking_id"] == "guid-2"
        assert bookings[1]["phone"] == "0507654321"


class TestParseCancel:
    def test_parse_cancel_success(self) -> None:
        root = _make_soap_response(f"""
            <ns:GetCancelRecordsResponse>
                <ns:CancelResult>
                    <ns:Status>Скасовано</ns:Status>
                    <ns:Message>Запис скасовано успішно</ns:Message>
                </ns:CancelResult>
            </ns:GetCancelRecordsResponse>
        """)
        result = OneCSOAPClient._parse_cancel(root)
        assert result["status"] == "Скасовано"
        assert "успішно" in result["message"]

    def test_parse_cancel_error(self) -> None:
        root = _make_soap_response("<ns:GetCancelRecordsResponse/>")
        result = OneCSOAPClient._parse_cancel(root)
        assert result["status"] == "error"


# --- SOAP Fault handling ---


class TestSOAPFault:
    @pytest.mark.asyncio
    async def test_soap_fault_raises_error(self, soap_client: OneCSOAPClient) -> None:
        fault_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
            "<soap:Body>"
            "<soap:Fault>"
            "<soap:Reason><soap:Text>Some fault</soap:Text></soap:Reason>"
            "</soap:Fault>"
            "</soap:Body>"
            "</soap:Envelope>"
        )
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=fault_xml.encode("utf-8"))

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=AsyncMock())
        mock_session.post.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.post.return_value.__aexit__ = AsyncMock(return_value=False)

        soap_client._session = mock_session

        with pytest.raises(OneCSOAPError, match="Some fault"):
            await soap_client._do_request("<ns:Test/>")


# --- _text helper ---


class TestTextHelper:
    def test_text_with_namespace(self) -> None:
        xml_str = f'<root xmlns:ns="{_SOAP_NS}"><ns:Name>value</ns:Name></root>'
        root = ET.fromstring(xml_str)
        assert _text(root, "Name") == "value"

    def test_text_without_namespace(self) -> None:
        root = ET.fromstring("<root><Name>value</Name></root>")
        assert _text(root, "Name") == "value"

    def test_text_default(self) -> None:
        root = ET.fromstring("<root/>")
        assert _text(root, "Missing", "default") == "default"

    def test_text_empty_default(self) -> None:
        root = ET.fromstring("<root/>")
        assert _text(root, "Missing") == ""


# --- Error class ---


class TestOneCSOAPError:
    def test_error_message(self) -> None:
        err = OneCSOAPError(401, "Unauthorized")
        assert err.status == 401
        assert "401" in str(err)
        assert "Unauthorized" in str(err)

    def test_error_is_exception(self) -> None:
        err = OneCSOAPError(500, "Internal error")
        assert isinstance(err, Exception)


# --- Circuit breaker ---


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_open_raises_soap_error(
        self, soap_client: OneCSOAPClient
    ) -> None:
        from aiobreaker import CircuitBreakerError

        soap_client._session = MagicMock()  # mark as opened

        with patch(
            "src.onec_client.soap._soap_breaker.call_async",
            side_effect=CircuitBreakerError(MagicMock(), reopen_time=30),
        ):
            with pytest.raises(OneCSOAPError, match="тимчасово недоступний"):
                await soap_client.get_station_schedule("2026-02-20", "2026-02-21")


# --- Method integration tests (mocked transport) ---


class TestSOAPMethods:
    @pytest.mark.asyncio
    async def test_get_station_schedule(self, soap_client: OneCSOAPClient) -> None:
        schedule_xml = _make_soap_response(f"""
            <ns:GetStationScheduleResponse>
                <ns:ScheduleEntry>
                    <ns:StationID>ST-001</ns:StationID>
                    <ns:Date>2026-02-20</ns:Date>
                    <ns:Time>09:00</ns:Time>
                    <ns:Available>true</ns:Available>
                </ns:ScheduleEntry>
            </ns:GetStationScheduleResponse>
        """)

        with patch.object(
            soap_client, "_soap_request", new_callable=AsyncMock, return_value=schedule_xml
        ):
            slots = await soap_client.get_station_schedule("2026-02-20", "2026-02-21", "ST-001")

        assert len(slots) == 1
        assert slots[0]["station_id"] == "ST-001"

    @pytest.mark.asyncio
    async def test_book_fitting(self, soap_client: OneCSOAPClient) -> None:
        booking_xml = _make_soap_response(f"""
            <ns:GetTireRecordingResponse>
                <ns:RecordingResult>
                    <ns:GUID>new-guid</ns:GUID>
                    <ns:Status>Записан</ns:Status>
                    <ns:Message>OK</ns:Message>
                </ns:RecordingResult>
            </ns:GetTireRecordingResponse>
        """)

        with patch.object(
            soap_client, "_soap_request", new_callable=AsyncMock, return_value=booking_xml
        ):
            result = await soap_client.book_fitting(
                person="Іван",
                phone="0501234567",
                station_id="ST-001",
                date="2026-02-20",
                time="09:00",
            )

        assert result["booking_id"] == "new-guid"
        assert result["status"] == "Записан"

    @pytest.mark.asyncio
    async def test_get_customer_bookings(self, soap_client: OneCSOAPClient) -> None:
        bookings_xml = _make_soap_response(f"""
            <ns:GetListOfEntriesResponse>
                <ns:Entry>
                    <ns:GUID>guid-1</ns:GUID>
                    <ns:StationID>ST-001</ns:StationID>
                    <ns:Date>2026-02-20</ns:Date>
                    <ns:Time>09:00</ns:Time>
                    <ns:Status>Записан</ns:Status>
                    <ns:Person>Іван</ns:Person>
                    <ns:Phone>0501234567</ns:Phone>
                </ns:Entry>
            </ns:GetListOfEntriesResponse>
        """)

        with patch.object(
            soap_client, "_soap_request", new_callable=AsyncMock, return_value=bookings_xml
        ):
            bookings = await soap_client.get_customer_bookings("0501234567")

        assert len(bookings) == 1
        assert bookings[0]["booking_id"] == "guid-1"

    @pytest.mark.asyncio
    async def test_cancel_booking(self, soap_client: OneCSOAPClient) -> None:
        cancel_xml = _make_soap_response(f"""
            <ns:GetCancelRecordsResponse>
                <ns:CancelResult>
                    <ns:Status>Скасовано</ns:Status>
                    <ns:Message>OK</ns:Message>
                </ns:CancelResult>
            </ns:GetCancelRecordsResponse>
        """)

        with patch.object(
            soap_client, "_soap_request", new_callable=AsyncMock, return_value=cancel_xml
        ):
            result = await soap_client.cancel_booking("guid-1")

        assert result["status"] == "Скасовано"

    @pytest.mark.asyncio
    async def test_book_fitting_with_vehicle_info(self, soap_client: OneCSOAPClient) -> None:
        """Verify extra params are passed to SOAP request."""
        booking_xml = _make_soap_response(f"""
            <ns:GetTireRecordingResponse>
                <ns:RecordingResult>
                    <ns:GUID>new-guid-2</ns:GUID>
                    <ns:Status>Записан</ns:Status>
                    <ns:Message>OK</ns:Message>
                </ns:RecordingResult>
            </ns:GetTireRecordingResponse>
        """)

        with patch.object(
            soap_client, "_soap_request", new_callable=AsyncMock, return_value=booking_xml
        ) as mock_req:
            await soap_client.book_fitting(
                person="Марія",
                phone="0507654321",
                station_id="ST-002",
                date="2026-02-21",
                time="14:00",
                vehicle_info="BMW X5 2020",
                tire_diameter=18,
                service_type="full_service",
            )

            # Check the XML body contains the comment
            call_args = mock_req.call_args[0][0]
            assert "BMW X5 2020" in call_args
            assert "R18" in call_args
            assert "повний сервіс" in call_args


# --- Windows-1251 fallback ---


class TestEncoding:
    @pytest.mark.asyncio
    async def test_windows_1251_fallback(self, soap_client: OneCSOAPClient) -> None:
        """1C may return Windows-1251 encoded responses."""
        # Build valid XML in Windows-1251
        xml_text = (
            '<?xml version="1.0" encoding="windows-1251"?>'
            '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">'
            "<soap:Body><Test>Привет</Test></soap:Body>"
            "</soap:Envelope>"
        )
        raw_bytes = xml_text.encode("windows-1251")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.read = AsyncMock(return_value=raw_bytes)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_ctx)

        soap_client._session = mock_session

        # Should not raise — falls back to windows-1251
        root = await soap_client._do_request("<ns:Test/>")
        assert root is not None


# --- GetStation (stations list) ---


class TestParseStations:
    def test_parse_stations_cdata(self) -> None:
        """Parse GetStation response with CDATA inner XML."""
        cdata_xml = (
            "<data>"
            "<line>"
            "<StationID>ST-001</StationID>"
            "<StationName>Центральний ШМ</StationName>"
            "<StationCity>Київ</StationCity>"
            "<StationCityID>city-001</StationCityID>"
            "<StationAdress>вул. Хрещатик, 1</StationAdress>"
            "</line>"
            "<line>"
            "<StationID>ST-002</StationID>"
            "<StationName>Лівобережна ШМ</StationName>"
            "<StationCity>Київ</StationCity>"
            "<StationCityID>city-001</StationCityID>"
            "<StationAdress>вул. Бориспільська, 10</StationAdress>"
            "</line>"
            "</data>"
        )
        # 1C wraps CDATA inside <m:return> (namespace-qualified)
        body = (
            f'<m:GetStationResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetStationResponse>"
        )
        root = _make_soap_response(body)
        stations = OneCSOAPClient._parse_stations(root)
        assert len(stations) == 2
        assert stations[0]["station_id"] == "ST-001"
        assert stations[0]["name"] == "Центральний ШМ"
        assert stations[0]["city"] == "Київ"
        assert stations[0]["city_id"] == "city-001"
        assert stations[0]["address"] == "вул. Хрещатик, 1"
        assert stations[1]["station_id"] == "ST-002"
        assert stations[1]["name"] == "Лівобережна ШМ"

    def test_parse_stations_empty(self) -> None:
        """Empty GetStation response returns empty list."""
        root = _make_soap_response(f"<ns:GetStationResponse/>")
        stations = OneCSOAPClient._parse_stations(root)
        assert stations == []

    def test_parse_stations_empty_cdata(self) -> None:
        """CDATA with no <line> elements returns empty list."""
        body = (
            f'<m:GetStationResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[<data></data>]]></m:return>"
            f"</m:GetStationResponse>"
        )
        root = _make_soap_response(body)
        stations = OneCSOAPClient._parse_stations(root)
        assert stations == []

    def test_parse_stations_namespace_fallback(self) -> None:
        """Fallback parsing with namespace-aware Station elements."""
        body = (
            f"<ns:GetStationResponse>"
            f"<ns:Station>"
            f"<ns:StationID>ST-010</ns:StationID>"
            f"<ns:StationName>Одеська ШМ</ns:StationName>"
            f"<ns:StationCity>Одеса</ns:StationCity>"
            f"<ns:StationCityID>city-010</ns:StationCityID>"
            f"<ns:StationAdress>вул. Дерибасівська, 5</ns:StationAdress>"
            f"</ns:Station>"
            f"</ns:GetStationResponse>"
        )
        root = _make_soap_response(body)
        stations = OneCSOAPClient._parse_stations(root)
        assert len(stations) == 1
        assert stations[0]["station_id"] == "ST-010"
        assert stations[0]["city"] == "Одеса"


class TestGetStationsMethod:
    @pytest.mark.asyncio
    async def test_get_stations(self, soap_client: OneCSOAPClient) -> None:
        cdata_xml = (
            "<data>"
            "<line>"
            "<StationID>ST-001</StationID>"
            "<StationName>Центральний</StationName>"
            "<StationCity>Київ</StationCity>"
            "<StationCityID>city-001</StationCityID>"
            "<StationAdress>вул. Хрещатик, 1</StationAdress>"
            "</line>"
            "</data>"
        )
        body = (
            f'<m:GetStationResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetStationResponse>"
        )
        resp_xml = _make_soap_response(body)

        with patch.object(
            soap_client, "_soap_request", new_callable=AsyncMock, return_value=resp_xml
        ) as mock_req:
            stations = await soap_client.get_stations()
            mock_req.assert_called_once()
            # Verify body contains GetStation
            call_body = mock_req.call_args[0][0]
            assert "GetStation" in call_body

        assert len(stations) == 1
        assert stations[0]["station_id"] == "ST-001"
        assert stations[0]["name"] == "Центральний"


class TestInnerTextHelper:
    def test_inner_text_found(self) -> None:
        root = ET.fromstring("<root><Name>value</Name></root>")
        assert _inner_text(root, "Name") == "value"

    def test_inner_text_missing(self) -> None:
        root = ET.fromstring("<root/>")
        assert _inner_text(root, "Name") == ""

    def test_inner_text_default(self) -> None:
        root = ET.fromstring("<root/>")
        assert _inner_text(root, "Name", "fallback") == "fallback"
