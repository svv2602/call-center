"""Store API HTTP client with circuit breaker and retry.

Integrates with the tire shop's REST API for product search,
availability checks, and (in later phases) order management.
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

    # --- HTTP helpers ---

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Make a GET request with circuit breaker and retry."""
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
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
    ) -> dict[str, Any]:
        """Execute request with retry for 429/503."""
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._do_request(
                    method, url, request_id, params=params, json_data=json_data
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
    ) -> dict[str, Any]:
        """Execute a single HTTP request."""
        assert self._session is not None

        headers = {"X-Request-Id": request_id}

        async with self._session.request(
            method, url, params=params, json=json_data, headers=headers
        ) as resp:
            if resp.status == 401:
                logger.critical("Store API authentication failed — check API key")

            if resp.status >= 400:
                body = await resp.text()
                raise StoreAPIError(resp.status, body[:200])

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
