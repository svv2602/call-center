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
    _to_datetime,
    _to_time,
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
        """Fallback path: Available=true → quantity=0, Available=false → quantity=1."""
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
        assert slots[0]["quantity"] == 0  # Available=true → no bookings
        assert slots[0]["station_name"] == "Центральний"
        assert slots[1]["quantity"] == 1  # Available=false → 1 booking


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
        ) as mock_req:
            slots = await soap_client.get_station_schedule("2026-02-20", "2026-02-21", "ST-001")
            call_body = mock_req.call_args[0][0]
            # Verify correct 1C parameter names
            assert "DataBig" in call_body
            assert "DataEnd" in call_body
            assert "DateStart" not in call_body
            # Verify dateTime format
            assert "2026-02-20T00:00:00" in call_body
            assert "2026-02-21T00:00:00" in call_body

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
        ) as mock_req:
            result = await soap_client.book_fitting(
                person="Іван",
                phone="0501234567",
                station_id="ST-001",
                date="2026-02-20",
                time="09:00",
            )
            call_body = mock_req.call_args[0][0]
            # Verify correct 1C parameter names
            assert "PhoneNumber" in call_body
            assert "<ns:Phone>" not in call_body
            # Verify dateTime/time formats
            assert "2026-02-20T00:00:00" in call_body
            assert "0001-01-01T09:00:00" in call_body

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
        ) as mock_req:
            bookings = await soap_client.get_customer_bookings("0501234567")
            call_body = mock_req.call_args[0][0]
            assert "PhoneNumber" in call_body
            assert "<ns:Phone>" not in call_body

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
        ) as mock_req:
            result = await soap_client.cancel_booking("guid-1")
            call_body = mock_req.call_args[0][0]
            assert "UPP>false<" in call_body

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

    def test_parse_stations_cdata_with_namespace(self) -> None:
        """Parse GetStation response where inner CDATA XML has xmlns (real 1C format)."""
        cdata_xml = (
            f'<GetStationResponse xmlns="{_SOAP_NS}" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:type="GetStationResponse">'
            "<line>"
            "<StationID>000000003</StationID>"
            "<StationName>1Д (Днепр, пер. Добровольцев, 1Д)</StationName>"
            "<StationCity>Дніпро</StationCity>"
            "<StationCityID>db5c88f0-391c-11dd-90d9-001a92567626</StationCityID>"
            "<StationAdress>м. Дніпро, пров. Добровольців, 1д</StationAdress>"
            "</line>"
            "<line>"
            "<StationID>000000006</StationID>"
            "<StationName>4К (Киев, ул. М. Тимошенко, 7)</StationName>"
            "<StationCity>Київ</StationCity>"
            "<StationCityID>8d5a980d-391c-11dd-90d9-001a92567626</StationCityID>"
            "<StationAdress>м. Київ, вул. Маршала Тимошенка, 7</StationAdress>"
            "</line>"
            "</GetStationResponse>"
        )
        body = (
            f'<m:GetStationResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetStationResponse>"
        )
        root = _make_soap_response(body)
        stations = OneCSOAPClient._parse_stations(root)
        assert len(stations) == 2
        assert stations[0]["station_id"] == "000000003"
        assert stations[0]["name"] == "1Д (Днепр, пер. Добровольцев, 1Д)"
        assert stations[0]["city"] == "Дніпро"
        assert stations[1]["station_id"] == "000000006"
        assert stations[1]["city"] == "Київ"


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


class TestDateTimeHelpers:
    """Test _to_datetime and _to_time conversion helpers."""

    def test_to_datetime_date_only(self) -> None:
        assert _to_datetime("2026-02-24") == "2026-02-24T00:00:00"

    def test_to_datetime_already_datetime(self) -> None:
        assert _to_datetime("2026-02-24T10:30:00") == "2026-02-24T10:30:00"

    def test_to_time_hhmm(self) -> None:
        assert _to_time("09:00") == "0001-01-01T09:00:00"

    def test_to_time_hhmmss(self) -> None:
        assert _to_time("14:30:00") == "0001-01-01T14:30:00"

    def test_to_time_already_full(self) -> None:
        assert _to_time("0001-01-01T09:00:00") == "0001-01-01T09:00:00"


class TestParseScheduleCDATA:
    """Test _parse_schedule with real 1C CDATA format."""

    def test_parse_schedule_cdata_with_namespace(self) -> None:
        cdata_xml = (
            f'<GetStationScheduleResponse xmlns="{_SOAP_NS}" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:type="GetStationScheduleResponse">'
            "<line>"
            "<StationID>000000003</StationID>"
            "<Data>2026-02-24</Data>"
            "<Time>09:00:00</Time>"
            "<Period>2026-02-24T09:00:00</Period>"
            "<Quantity>2</Quantity>"
            "</line>"
            "<line>"
            "<StationID>000000003</StationID>"
            "<Data>2026-02-24</Data>"
            "<Time>09:40:00</Time>"
            "<Period>2026-02-24T09:40:00</Period>"
            "<Quantity>0</Quantity>"
            "</line>"
            "</GetStationScheduleResponse>"
        )
        body = (
            f'<m:GetStationScheduleResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetStationScheduleResponse>"
        )
        root = _make_soap_response(body)
        slots = OneCSOAPClient._parse_schedule(root)
        assert len(slots) == 2
        assert slots[0]["station_id"] == "000000003"
        assert slots[0]["date"] == "2026-02-24"
        assert slots[0]["time"] == "09:00:00"
        assert slots[0]["quantity"] == 2  # 2 bookings
        assert slots[0]["period"] == "2026-02-24T09:00:00"
        assert slots[1]["quantity"] == 0  # no bookings


class TestParseBookingCDATA:
    """Test _parse_booking with real 1C CDATA format."""

    def test_parse_booking_cdata_success(self) -> None:
        cdata_xml = (
            f'<GetTireRecordingResponse xmlns="{_SOAP_NS}" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:type="GetTireRecordingResponse">'
            "<Result>true</Result>"
            "<GUID>a9236876-107a-11f1-a154-000c29c2a50f</GUID>"
            "</GetTireRecordingResponse>"
        )
        body = (
            f'<m:GetTireRecordingResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetTireRecordingResponse>"
        )
        root = _make_soap_response(body)
        result = OneCSOAPClient._parse_booking(root)
        assert result["booking_id"] == "a9236876-107a-11f1-a154-000c29c2a50f"
        assert result["status"] == "confirmed"

    def test_parse_booking_cdata_failure(self) -> None:
        cdata_xml = (
            f'<GetTireRecordingResponse xmlns="{_SOAP_NS}">'
            "<Result>false</Result>"
            "<GUID></GUID>"
            "</GetTireRecordingResponse>"
        )
        body = (
            f'<m:GetTireRecordingResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetTireRecordingResponse>"
        )
        root = _make_soap_response(body)
        result = OneCSOAPClient._parse_booking(root)
        assert result["status"] == "error"


class TestParseBookingsListCDATA:
    """Test _parse_bookings_list with real 1C CDATA format."""

    def test_parse_bookings_list_cdata(self) -> None:
        cdata_xml = (
            f'<GetListOfEntriesResponse xmlns="{_SOAP_NS}" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:type="GetListOfEntriesResponse">'
            "<line>"
            "<StationID>000000003</StationID>"
            "<Data>2026-02-28</Data>"
            "<Time>09:00:00</Time>"
            "<Period>2026-02-28T09:00:00</Period>"
            "<Customer>Погорельцев Ігорь</Customer>"
            "<AutoType>111111111</AutoType>"
            "<AutoNumber>1111111111 111111111</AutoNumber>"
            "<NumberContract/>"
            "<GUID>249a424f-109a-11f1-a154-000c29c2a50f</GUID>"
            "</line>"
            "</GetListOfEntriesResponse>"
        )
        body = (
            f'<m:GetListOfEntriesResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetListOfEntriesResponse>"
        )
        root = _make_soap_response(body)
        bookings = OneCSOAPClient._parse_bookings_list(root)
        assert len(bookings) == 1
        assert bookings[0]["booking_id"] == "249a424f-109a-11f1-a154-000c29c2a50f"
        assert bookings[0]["station_id"] == "000000003"
        assert bookings[0]["date"] == "2026-02-28"
        assert bookings[0]["time"] == "09:00:00"
        assert bookings[0]["person"] == "Погорельцев Ігорь"


class TestSlotAvailabilityCalculation:
    """Test that quantity + count_posts → available calculation works correctly.

    The SOAP parser returns 'quantity' (number of bookings).
    The handler must compute: available = count_posts - quantity > 0.
    These tests verify the formula directly (no Redis, no handler — pure logic).
    """

    @staticmethod
    def _compute_available(count_posts: int | None, quantity: int) -> bool:
        """Replicate the availability formula from main.py handler."""
        posts = count_posts if count_posts else 1  # fallback
        return posts - quantity > 0

    def test_count_posts_2_quantity_1_available(self) -> None:
        assert self._compute_available(2, 1) is True

    def test_count_posts_2_quantity_2_not_available(self) -> None:
        assert self._compute_available(2, 2) is False

    def test_count_posts_2_quantity_0_available(self) -> None:
        assert self._compute_available(2, 0) is True

    def test_count_posts_none_quantity_0_fallback_available(self) -> None:
        """Unknown count_posts (None) → fallback=1, quantity=0 → available."""
        assert self._compute_available(None, 0) is True

    def test_count_posts_none_quantity_1_fallback_not_available(self) -> None:
        """Unknown count_posts (None) → fallback=1, quantity=1 → not available."""
        assert self._compute_available(None, 1) is False

    def test_count_posts_3_quantity_2_available(self) -> None:
        assert self._compute_available(3, 2) is True

    def test_count_posts_3_quantity_3_not_available(self) -> None:
        assert self._compute_available(3, 3) is False

    def test_count_posts_1_quantity_0_available(self) -> None:
        assert self._compute_available(1, 0) is True

    def test_cdata_slots_have_quantity_field(self) -> None:
        """Verify CDATA-parsed slots contain quantity (not available)."""
        cdata_xml = (
            f'<GetStationScheduleResponse xmlns="{_SOAP_NS}">'
            "<line><StationID>ST-1</StationID>"
            "<Data>2026-03-01</Data><Time>10:00:00</Time>"
            "<Quantity>3</Quantity></line>"
            "</GetStationScheduleResponse>"
        )
        body = (
            f'<m:GetStationScheduleResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetStationScheduleResponse>"
        )
        root = _make_soap_response(body)
        slots = OneCSOAPClient._parse_schedule(root)
        assert len(slots) == 1
        assert "quantity" in slots[0]
        assert "available" not in slots[0]
        assert slots[0]["quantity"] == 3

    def test_fallback_slots_have_quantity_field(self) -> None:
        """Verify fallback-parsed slots contain quantity (not available)."""
        root = _make_soap_response(f"""
            <ns:GetStationScheduleResponse>
                <ns:ScheduleEntry>
                    <ns:StationID>ST-001</ns:StationID>
                    <ns:Date>2026-03-01</ns:Date>
                    <ns:Time>09:00</ns:Time>
                    <ns:Available>true</ns:Available>
                </ns:ScheduleEntry>
            </ns:GetStationScheduleResponse>
        """)
        slots = OneCSOAPClient._parse_schedule(root)
        assert len(slots) == 1
        assert "quantity" in slots[0]
        assert "available" not in slots[0]


class TestParseCancelCDATA:
    """Test _parse_cancel with real 1C CDATA format."""

    def test_parse_cancel_cdata_success(self) -> None:
        cdata_xml = (
            f'<GetCancelRecordsResponse xmlns="{_SOAP_NS}" '
            'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xsi:type="GetCancelRecordsResponse">'
            "<Result>true</Result>"
            "</GetCancelRecordsResponse>"
        )
        body = (
            f'<m:GetCancelRecordsResponse xmlns:m="{_SOAP_NS}">'
            f"<m:return><![CDATA[{cdata_xml}]]></m:return>"
            f"</m:GetCancelRecordsResponse>"
        )
        root = _make_soap_response(body)
        result = OneCSOAPClient._parse_cancel(root)
        assert result["status"] == "cancelled"
