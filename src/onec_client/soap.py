"""1C SOAP client for tire fitting (TireAssemblyExchange) service.

Uses raw XML templates + aiohttp (no zeep — only 4 methods needed).
Same patterns as OneCClient: Basic Auth, circuit breaker, retry, Windows-1251 fallback.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import escape

import aiohttp
from aiobreaker import CircuitBreaker, CircuitBreakerError

logger = logging.getLogger(__name__)

# Retry config (same as REST client)
_MAX_RETRIES = 2
_RETRY_DELAYS = [1.0, 2.0]
_RETRYABLE_STATUSES = {429, 503}

# Separate circuit breaker for SOAP calls
_soap_breaker = CircuitBreaker(fail_max=5, timeout_duration=30)

_SOAP_NS = "http://www.1c.ru/SSL/TireAssemblyExchange"

# SOAP 1.2 envelope template
_ENVELOPE_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:ns="{ns}">'
    "<soap:Body>{body}</soap:Body>"
    "</soap:Envelope>"
)


class OneCSOAPError(Exception):
    """Raised when a 1C SOAP call fails."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"1C SOAP {status}: {message}")


class OneCSOAPClient:
    """SOAP client for the 1C TireAssemblyExchange web service.

    Features:
      - Basic Auth authentication
      - Circuit breaker (aiobreaker: fail_max=5, timeout=30s)
      - Retry with backoff (1s, 2s) for 429/503
      - Windows-1251 fallback decoding
      - XML injection protection via xml.sax.saxutils.escape
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        wsdl_path: str = "/Trade/ws/TireAssemblyExchange.1cws",
        timeout: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._wsdl_path = wsdl_path
        self._auth = aiohttp.BasicAuth(username, password)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    @property
    def endpoint_url(self) -> str:
        return f"{self._base_url}{self._wsdl_path}"

    async def open(self) -> None:
        """Open the HTTP session."""
        self._session = aiohttp.ClientSession(
            timeout=self._timeout,
            auth=self._auth,
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    # --- Public SOAP methods ---

    async def get_station_schedule(
        self,
        date_from: str,
        date_to: str,
        station_id: str = "",
    ) -> list[dict[str, Any]]:
        """Get fitting station schedule (available slots).

        SOAP operation: GetStationSchedule

        Args:
            date_from: Start date (YYYY-MM-DD).
            date_to: End date (YYYY-MM-DD).
            station_id: Optional station ID to filter by.

        Returns:
            List of slot dicts with keys: station_id, date, time, available.
        """
        params = f"<ns:DateStart>{escape(date_from)}</ns:DateStart>"
        params += f"<ns:DateEnd>{escape(date_to)}</ns:DateEnd>"
        if station_id:
            params += f"<ns:StationID>{escape(station_id)}</ns:StationID>"

        body = f"<ns:GetStationSchedule>{params}</ns:GetStationSchedule>"
        root = await self._soap_request(body)
        return self._parse_schedule(root)

    async def book_fitting(
        self,
        person: str,
        phone: str,
        station_id: str,
        date: str,
        time: str,
        vehicle_info: str = "",
        tire_diameter: int = 0,
        service_type: str = "tire_change",
    ) -> dict[str, Any]:
        """Book a tire fitting appointment.

        SOAP operation: GetTireRecording (Status=Записан)

        Args:
            person: Customer name.
            phone: Customer phone (0XXXXXXXXX).
            station_id: Fitting station ID.
            date: Appointment date (YYYY-MM-DD).
            time: Appointment time (HH:MM).
            vehicle_info: Vehicle description (optional).
            tire_diameter: Tire diameter in inches (optional).
            service_type: Service type (tire_change, balancing, full_service).

        Returns:
            Dict with booking_id, status, etc.
        """
        comment = _build_comment(vehicle_info, tire_diameter, service_type)

        params = f"<ns:Person>{escape(person)}</ns:Person>"
        params += f"<ns:Phone>{escape(phone)}</ns:Phone>"
        params += f"<ns:StationID>{escape(station_id)}</ns:StationID>"
        params += f"<ns:Date>{escape(date)}</ns:Date>"
        params += f"<ns:Time>{escape(time)}</ns:Time>"
        params += '<ns:Status>Записан</ns:Status>'
        params += '<ns:CheckBalance>true</ns:CheckBalance>'
        params += '<ns:CallBack>false</ns:CallBack>'
        params += '<ns:ClientMode>1</ns:ClientMode>'
        if comment:
            params += f"<ns:Comment>{escape(comment)}</ns:Comment>"

        body = f"<ns:GetTireRecording>{params}</ns:GetTireRecording>"
        root = await self._soap_request(body)
        return self._parse_booking(root)

    async def get_customer_bookings(
        self,
        phone: str,
        station_id: str = "",
    ) -> list[dict[str, Any]]:
        """Get existing bookings for a customer by phone number.

        SOAP operation: GetListOfEntries

        Args:
            phone: Customer phone (0XXXXXXXXX).
            station_id: Optional station ID to filter by.

        Returns:
            List of booking dicts.
        """
        params = f"<ns:Phone>{escape(phone)}</ns:Phone>"
        if station_id:
            params += f"<ns:StationID>{escape(station_id)}</ns:StationID>"

        body = f"<ns:GetListOfEntries>{params}</ns:GetListOfEntries>"
        root = await self._soap_request(body)
        return self._parse_bookings_list(root)

    async def cancel_booking(self, guid: str) -> dict[str, Any]:
        """Cancel a fitting booking.

        SOAP operation: GetCancelRecords (UPP=true — not from ERP)

        Args:
            guid: Booking GUID to cancel.

        Returns:
            Dict with status info.
        """
        params = f"<ns:GUID>{escape(guid)}</ns:GUID>"
        params += '<ns:UPP>true</ns:UPP>'

        body = f"<ns:GetCancelRecords>{params}</ns:GetCancelRecords>"
        root = await self._soap_request(body)
        return self._parse_cancel(root)

    # --- SOAP transport ---

    async def _soap_request(self, body_xml: str) -> ET.Element:
        """Send a SOAP request and return parsed XML root."""
        if self._session is None:
            raise RuntimeError("OneCSOAPClient not opened — call open() first")

        envelope = _ENVELOPE_TEMPLATE.format(ns=_SOAP_NS, body=body_xml)

        try:
            root: ET.Element = await _soap_breaker.call_async(
                self._request_with_retry,
                envelope,
            )
            return root
        except CircuitBreakerError as err:
            logger.error("Circuit breaker OPEN for 1C SOAP API")
            raise OneCSOAPError(503, "1С SOAP сервіс тимчасово недоступний") from err

    async def _request_with_retry(self, envelope: str) -> ET.Element:
        """Execute SOAP request with retry for 429/503."""
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._do_request(envelope)
            except OneCSOAPError as exc:
                last_exc = exc
                if exc.status not in _RETRYABLE_STATUSES:
                    raise
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "1C SOAP %d, retry %d/%d in %.1fs",
                        exc.status,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def _do_request(self, envelope: str) -> ET.Element:
        """Execute a single SOAP HTTP request."""
        assert self._session is not None

        headers = {
            "Content-Type": "application/soap+xml; charset=UTF-8",
        }

        async with self._session.post(
            self.endpoint_url,
            data=envelope.encode("utf-8"),
            headers=headers,
        ) as resp:
            if resp.status == 401:
                logger.critical("1C SOAP authentication failed — check credentials")

            raw = await resp.read()
            try:
                body_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                body_text = raw.decode("windows-1251")

            if resp.status >= 400:
                raise OneCSOAPError(resp.status, body_text[:200])

            try:
                root = ET.fromstring(body_text)
            except ET.ParseError as exc:
                raise OneCSOAPError(502, f"Invalid XML response: {exc}") from exc

            # Check for SOAP Fault
            fault = root.find(".//{http://www.w3.org/2003/05/soap-envelope}Fault")
            if fault is not None:
                fault_string = fault.findtext(
                    "{http://www.w3.org/2003/05/soap-envelope}Reason/"
                    "{http://www.w3.org/2003/05/soap-envelope}Text",
                    "Unknown SOAP Fault",
                )
                raise OneCSOAPError(500, fault_string)

            return root

    # --- Response parsers ---

    @staticmethod
    def _parse_schedule(root: ET.Element) -> list[dict[str, Any]]:
        """Parse GetStationSchedule response into slot list."""
        slots: list[dict[str, Any]] = []
        for entry in root.iter(f"{{{_SOAP_NS}}}ScheduleEntry"):
            slot: dict[str, Any] = {
                "station_id": _text(entry, "StationID"),
                "date": _text(entry, "Date"),
                "time": _text(entry, "Time"),
                "available": _text(entry, "Available", "true").lower() == "true",
            }
            station_name = _text(entry, "StationName")
            if station_name:
                slot["station_name"] = station_name
            slots.append(slot)
        return slots

    @staticmethod
    def _parse_booking(root: ET.Element) -> dict[str, Any]:
        """Parse GetTireRecording response into booking dict."""
        result: dict[str, Any] = {"status": "error"}
        for resp in root.iter(f"{{{_SOAP_NS}}}RecordingResult"):
            result = {
                "booking_id": _text(resp, "GUID"),
                "status": _text(resp, "Status", "unknown"),
                "message": _text(resp, "Message"),
            }
            break
        # Also check top-level elements in the Body
        body = root.find(".//{http://www.w3.org/2003/05/soap-envelope}Body")
        if body is not None and result.get("booking_id") is None:
            for child in body:
                guid = _text(child, "GUID")
                if guid:
                    result = {
                        "booking_id": guid,
                        "status": _text(child, "Status", "unknown"),
                        "message": _text(child, "Message"),
                    }
                    break
        return result

    @staticmethod
    def _parse_bookings_list(root: ET.Element) -> list[dict[str, Any]]:
        """Parse GetListOfEntries response into list of bookings."""
        bookings: list[dict[str, Any]] = []
        for entry in root.iter(f"{{{_SOAP_NS}}}Entry"):
            booking: dict[str, Any] = {
                "booking_id": _text(entry, "GUID"),
                "station_id": _text(entry, "StationID"),
                "station_name": _text(entry, "StationName"),
                "date": _text(entry, "Date"),
                "time": _text(entry, "Time"),
                "status": _text(entry, "Status"),
                "person": _text(entry, "Person"),
                "phone": _text(entry, "Phone"),
            }
            bookings.append(booking)
        return bookings

    @staticmethod
    def _parse_cancel(root: ET.Element) -> dict[str, Any]:
        """Parse GetCancelRecords response."""
        result: dict[str, Any] = {"status": "error"}
        for resp in root.iter(f"{{{_SOAP_NS}}}CancelResult"):
            result = {
                "status": _text(resp, "Status", "unknown"),
                "message": _text(resp, "Message"),
            }
            break
        # Fallback: check Body children
        body = root.find(".//{http://www.w3.org/2003/05/soap-envelope}Body")
        if body is not None and result.get("status") == "error":
            for child in body:
                status = _text(child, "Status")
                if status:
                    result = {
                        "status": status,
                        "message": _text(child, "Message"),
                    }
                    break
        return result


# --- Helpers ---


def _text(element: ET.Element, tag: str, default: str = "") -> str:
    """Extract text from a child element, trying with and without namespace."""
    # Try with namespace first
    child = element.find(f"{{{_SOAP_NS}}}{tag}")
    if child is not None and child.text:
        return child.text
    # Try without namespace
    child = element.find(tag)
    if child is not None and child.text:
        return child.text
    return default


def _build_comment(vehicle_info: str, tire_diameter: int, service_type: str) -> str:
    """Build a comment string from optional booking parameters."""
    parts: list[str] = []
    if vehicle_info:
        parts.append(f"Авто: {vehicle_info}")
    if tire_diameter:
        parts.append(f"R{tire_diameter}")
    if service_type and service_type != "tire_change":
        service_labels = {
            "balancing": "балансування",
            "full_service": "повний сервіс",
        }
        parts.append(service_labels.get(service_type, service_type))
    return "; ".join(parts)
