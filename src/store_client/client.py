"""Store API HTTP client with circuit breaker and retry.

Integrates with the tire shop's REST API for product search,
availability checks, and order management.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import aiohttp
from aiobreaker import CircuitBreaker, CircuitBreakerError

logger = logging.getLogger(__name__)

# Retry config
_MAX_RETRIES = 2
_RETRY_DELAYS = [1.0, 2.0]  # exponential backoff
_RETRYABLE_STATUSES = {429, 503}

# Circuit breaker
_store_breaker = CircuitBreaker(fail_max=5, timeout_duration=30)


class StoreAPIError(Exception):
    """Raised when a Store API call fails."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"Store API {status}: {message}")


class StoreClient:
    """HTTP client for the tire shop Store API.

    Features:
      - Circuit breaker (aiobreaker: fail_max=5, timeout=30s)
      - Retry with exponential backoff (1s, 2s) for 429/503
      - Request timeout: 5 seconds
      - X-Request-Id header for distributed tracing
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 5) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        """Open the HTTP session."""
        self._session = aiohttp.ClientSession(
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    # --- MVP Tool Handlers ---

    async def search_tires(self, **params: Any) -> dict[str, Any]:
        """Search tires by parameters.

        Maps to: GET /api/v1/tires/search
        Also supports vehicle search: GET /api/v1/vehicles/tires
        """
        # If vehicle params provided, use vehicle endpoint
        if any(k in params for k in ("vehicle_make", "vehicle_model", "vehicle_year")):
            query = {
                "make": params.get("vehicle_make", ""),
                "model": params.get("vehicle_model", ""),
                "year": params.get("vehicle_year", ""),
            }
            if params.get("season"):
                query["season"] = params["season"]
            data = await self._get("/api/v1/vehicles/tires", params=query)
        else:
            query = {}
            for key in ("width", "profile", "diameter", "season", "brand"):
                if params.get(key):
                    query[key] = params[key]
            data = await self._get("/api/v1/tires/search", params=query)

        return self._format_tire_results(data)

    async def check_availability(
        self, product_id: str = "", query: str = "", **_: Any
    ) -> dict[str, Any]:
        """Check tire availability.

        Maps to: GET /api/v1/tires/{id}/availability
        """
        if not product_id and query:
            # Search by name first
            search_result = await self._get(
                "/api/v1/tires/search", params={"q": query}
            )
            items = search_result.get("items", [])
            if not items:
                return {"available": False, "message": "Товар не знайдено"}
            product_id = items[0].get("id", "")

        if not product_id:
            return {"available": False, "message": "Потрібен ID товару або запит"}

        try:
            data = await self._get(f"/api/v1/tires/{product_id}/availability")
        except StoreAPIError as exc:
            if exc.status == 404:
                return {"available": False, "message": "Товар не знайдено"}
            raise

        return {
            "available": data.get("in_stock", False),
            "quantity": data.get("quantity", 0),
            "price": data.get("price"),
            "delivery_days": data.get("delivery_days"),
        }

    async def get_tire(self, tire_id: str) -> dict[str, Any]:
        """Get tire details.

        Maps to: GET /api/v1/tires/{id}
        """
        return await self._get(f"/api/v1/tires/{tire_id}")

    # --- Order Tool Handlers ---

    async def search_orders(
        self,
        phone: str = "",
        order_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Search orders by phone or get a specific order.

        Maps to:
          - GET /api/v1/orders/search?phone=...
          - GET /api/v1/orders/{id}
        """
        if order_id:
            try:
                data = await self._get(f"/api/v1/orders/{order_id}")
            except StoreAPIError as exc:
                if exc.status == 404:
                    return {"found": False, "message": "Замовлення не знайдено"}
                raise
            return {"found": True, "orders": [self._format_order(data)]}

        if phone:
            data = await self._get(
                "/api/v1/orders/search", params={"phone": phone}
            )
            items = data.get("items", [])
            if not items:
                return {"found": False, "message": "Замовлень не знайдено"}
            return {
                "found": True,
                "total": data.get("total", len(items)),
                "orders": [self._format_order(o) for o in items[:5]],
            }

        return {"found": False, "message": "Потрібен номер телефону або номер замовлення"}

    async def create_order(
        self,
        items: list[dict[str, Any]],
        customer_phone: str,
        customer_name: str = "",
        call_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Create an order draft.

        Maps to: POST /api/v1/orders (with Idempotency-Key)
        """
        idempotency_key = str(uuid.uuid4())
        body: dict[str, Any] = {
            "items": items,
            "customer_phone": customer_phone,
            "source": "ai_agent",
        }
        if customer_name:
            body["customer_name"] = customer_name
        if call_id:
            body["call_id"] = call_id

        data = await self._post(
            "/api/v1/orders",
            json_data=body,
            idempotency_key=idempotency_key,
        )
        return {
            "order_id": data.get("id"),
            "order_number": data.get("order_number"),
            "status": data.get("status"),
            "items": data.get("items", []),
            "subtotal": data.get("subtotal"),
            "total": data.get("total"),
        }

    async def update_delivery(
        self,
        order_id: str,
        delivery_type: str,
        city: str = "",
        address: str = "",
        pickup_point_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Update delivery info for an order.

        Maps to: PATCH /api/v1/orders/{id}/delivery
        """
        body: dict[str, Any] = {"delivery_type": delivery_type}
        if city:
            body["city"] = city
        if address:
            body["address"] = address
        if pickup_point_id:
            body["pickup_point_id"] = pickup_point_id

        data = await self._patch(
            f"/api/v1/orders/{order_id}/delivery", json_data=body
        )
        return {
            "order_id": data.get("id", order_id),
            "delivery_type": data.get("delivery_type"),
            "delivery_cost": data.get("delivery_cost"),
            "estimated_days": data.get("estimated_days"),
            "total": data.get("total"),
        }

    async def confirm_order(
        self,
        order_id: str,
        payment_method: str,
        customer_name: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Confirm and finalize an order.

        Maps to: POST /api/v1/orders/{id}/confirm (with Idempotency-Key)
        """
        idempotency_key = str(uuid.uuid4())
        body: dict[str, Any] = {
            "payment_method": payment_method,
            "send_sms_confirmation": True,
        }
        if customer_name:
            body["customer_name"] = customer_name

        data = await self._post(
            f"/api/v1/orders/{order_id}/confirm",
            json_data=body,
            idempotency_key=idempotency_key,
        )
        return {
            "order_id": data.get("id", order_id),
            "order_number": data.get("order_number"),
            "status": data.get("status"),
            "estimated_delivery": data.get("estimated_delivery"),
            "sms_sent": data.get("sms_sent", False),
            "total": data.get("total"),
        }

    async def get_pickup_points(self, city: str = "") -> dict[str, Any]:
        """Get available pickup points.

        Maps to: GET /api/v1/pickup-points
        """
        params = {}
        if city:
            params["city"] = city
        data = await self._get("/api/v1/pickup-points", params=params)
        points = data.get("items", [])
        return {
            "total": data.get("total", len(points)),
            "points": [
                {
                    "id": p.get("id"),
                    "name": p.get("name", ""),
                    "address": p.get("address", ""),
                    "city": p.get("city", ""),
                }
                for p in points[:10]
            ],
        }

    async def calculate_delivery(
        self, city: str, order_id: str = ""
    ) -> dict[str, Any]:
        """Calculate delivery cost.

        Maps to: GET /api/v1/delivery/calculate
        """
        params: dict[str, Any] = {"city": city}
        if order_id:
            params["order_id"] = order_id
        return await self._get("/api/v1/delivery/calculate", params=params)

    # --- Fitting Tool Handlers ---

    async def get_fitting_stations(
        self, city: str, **_: Any
    ) -> dict[str, Any]:
        """Get fitting stations in a city.

        Maps to: GET /api/v1/fitting/stations?city=...
        """
        data = await self._get(
            "/api/v1/fitting/stations", params={"city": city}
        )
        stations = data.get("data", data.get("items", []))
        return {
            "total": len(stations),
            "stations": [
                {
                    "id": s.get("id"),
                    "name": s.get("name", ""),
                    "city": s.get("city", ""),
                    "district": s.get("district", ""),
                    "address": s.get("address", ""),
                    "phone": s.get("phone", ""),
                    "working_hours": s.get("working_hours", ""),
                    "services": s.get("services", []),
                }
                for s in stations
            ],
        }

    async def get_fitting_slots(
        self,
        station_id: str,
        date_from: str = "today",
        date_to: str = "",
        service_type: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Get available fitting slots for a station.

        Maps to: GET /api/v1/fitting/stations/{id}/slots
        """
        params: dict[str, Any] = {"date_from": date_from}
        if date_to:
            params["date_to"] = date_to
        if service_type:
            params["service_type"] = service_type

        data = await self._get(
            f"/api/v1/fitting/stations/{station_id}/slots", params=params
        )
        return {
            "station_id": station_id,
            "slots": data.get("data", {}).get("slots", data.get("slots", [])),
        }

    async def book_fitting(
        self,
        station_id: str,
        date: str,
        time: str,
        customer_phone: str,
        vehicle_info: str = "",
        service_type: str = "tire_change",
        tire_diameter: int = 0,
        linked_order_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Book a fitting appointment.

        Maps to: POST /api/v1/fitting/bookings (with Idempotency-Key)
        """
        idempotency_key = str(uuid.uuid4())
        body: dict[str, Any] = {
            "station_id": station_id,
            "date": date,
            "time": time,
            "customer_phone": customer_phone,
            "service_type": service_type,
            "source": "ai_agent",
        }
        if vehicle_info:
            body["vehicle_info"] = vehicle_info
        if tire_diameter:
            body["tire_diameter"] = tire_diameter
        if linked_order_id:
            body["linked_order_id"] = linked_order_id

        data = await self._post(
            "/api/v1/fitting/bookings",
            json_data=body,
            idempotency_key=idempotency_key,
        )
        booking = data.get("data", data)
        return {
            "booking_id": booking.get("id"),
            "station_name": booking.get("station", {}).get("name", ""),
            "station_address": booking.get("station", {}).get("address", ""),
            "date": booking.get("date"),
            "time": booking.get("time"),
            "service_type": booking.get("service_type"),
            "estimated_duration_min": booking.get("estimated_duration_min"),
            "price": booking.get("price"),
            "currency": booking.get("currency", "UAH"),
            "sms_sent": booking.get("sms_sent", False),
        }

    async def cancel_fitting(
        self,
        booking_id: str,
        action: str = "cancel",
        new_date: str = "",
        new_time: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Cancel or reschedule a fitting booking.

        Maps to:
          - cancel: DELETE /api/v1/fitting/bookings/{id}
          - reschedule: PATCH /api/v1/fitting/bookings/{id}
        """
        if action == "reschedule":
            body: dict[str, Any] = {}
            if new_date:
                body["date"] = new_date
            if new_time:
                body["time"] = new_time
            data = await self._patch(
                f"/api/v1/fitting/bookings/{booking_id}", json_data=body
            )
            booking = data.get("data", data)
            return {
                "booking_id": booking_id,
                "action": "rescheduled",
                "new_date": booking.get("date", new_date),
                "new_time": booking.get("time", new_time),
            }

        # cancel
        await self._delete(f"/api/v1/fitting/bookings/{booking_id}")
        return {
            "booking_id": booking_id,
            "action": "cancelled",
        }

    async def get_fitting_price(
        self,
        tire_diameter: int,
        station_id: str = "",
        service_type: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Get fitting service prices.

        Maps to: GET /api/v1/fitting/prices
        """
        params: dict[str, Any] = {"tire_diameter": tire_diameter}
        if station_id:
            params["station_id"] = station_id
        if service_type:
            params["service_type"] = service_type

        data = await self._get("/api/v1/fitting/prices", params=params)
        return {
            "prices": data.get("data", data.get("prices", data.get("items", []))),
        }

    async def search_knowledge_base(
        self,
        query: str,
        category: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        """Search the knowledge base (RAG).

        Maps to: GET /api/v1/knowledge/search
        """
        params: dict[str, Any] = {"query": query, "limit": 5}
        if category:
            params["category"] = category

        data = await self._get("/api/v1/knowledge/search", params=params)
        articles = data.get("data", data.get("items", []))
        return {
            "total": len(articles),
            "articles": [
                {
                    "title": a.get("title", ""),
                    "category": a.get("category", ""),
                    "content": a.get("content", a.get("chunk_text", "")),
                    "relevance": a.get("relevance", a.get("score", 0)),
                }
                for a in articles[:5]
            ],
        }

    # --- HTTP helpers ---

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make a GET request with circuit breaker and retry."""
        return await self._request("GET", path, params=params)

    async def _post(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Make a POST request with circuit breaker and retry."""
        return await self._request(
            "POST", path, json_data=json_data, idempotency_key=idempotency_key
        )

    async def _patch(
        self,
        path: str,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a PATCH request with circuit breaker and retry."""
        return await self._request("PATCH", path, json_data=json_data)

    async def _delete(self, path: str) -> dict[str, Any]:
        """Make a DELETE request with circuit breaker and retry."""
        return await self._request("DELETE", path)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with circuit breaker, retry, and error handling."""
        if self._session is None:
            raise RuntimeError("StoreClient not opened — call open() first")

        url = f"{self._base_url}{path}"
        request_id = str(uuid.uuid4())

        try:
            return await _store_breaker.call_async(
                self._request_with_retry,
                method,
                url,
                request_id,
                params=params,
                json_data=json_data,
                idempotency_key=idempotency_key,
            )
        except CircuitBreakerError:
            logger.error("Circuit breaker OPEN for Store API")
            raise StoreAPIError(
                503, "Сервіс тимчасово недоступний. Спробуйте пізніше."
            )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        request_id: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute request with retry for 429/503."""
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._do_request(
                    method, url, request_id,
                    params=params,
                    json_data=json_data,
                    idempotency_key=idempotency_key,
                )
            except StoreAPIError as exc:
                last_exc = exc
                if exc.status not in _RETRYABLE_STATUSES:
                    raise
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "Store API %d, retry %d/%d in %.1fs: %s",
                        exc.status,
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                        url,
                    )
                    await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def _do_request(
        self,
        method: str,
        url: str,
        request_id: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute a single HTTP request."""
        assert self._session is not None

        headers: dict[str, str] = {"X-Request-Id": request_id}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        async with self._session.request(
            method, url, params=params, json=json_data, headers=headers
        ) as resp:
            if resp.status == 401:
                logger.critical("Store API authentication failed — check API key")

            if resp.status >= 400:
                body = await resp.text()
                raise StoreAPIError(resp.status, body[:200])

            if resp.status == 204:
                return {}

            return await resp.json()

    @staticmethod
    def _format_tire_results(data: dict[str, Any]) -> dict[str, Any]:
        """Format tire search results for LLM consumption.

        Strips large fields (images, long descriptions) to reduce token usage.
        """
        items = data.get("items", [])
        formatted = []
        for item in items[:5]:  # Limit to 5 results for LLM
            formatted.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name", ""),
                    "brand": item.get("brand", ""),
                    "size": item.get("size", ""),
                    "season": item.get("season", ""),
                    "price": item.get("price"),
                    "in_stock": item.get("in_stock", False),
                }
            )
        return {
            "total": data.get("total", len(formatted)),
            "items": formatted,
        }

    @staticmethod
    def _format_order(data: dict[str, Any]) -> dict[str, Any]:
        """Format order data for LLM consumption."""
        return {
            "id": data.get("id"),
            "order_number": data.get("order_number"),
            "status": data.get("status"),
            "status_label": data.get("status_label", ""),
            "items_summary": data.get("items_summary", ""),
            "total": data.get("total"),
            "estimated_delivery": data.get("estimated_delivery"),
        }
