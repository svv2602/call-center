"""1C REST API HTTP client with circuit breaker and retry.

Integrates with the 1C ERP system for tire catalog sync
and stock/price queries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from aiobreaker import CircuitBreaker, CircuitBreakerError

logger = logging.getLogger(__name__)

# Retry config
_MAX_RETRIES = 2
_RETRY_DELAYS = [1.0, 2.0]
_RETRYABLE_STATUSES = {429, 503}

# Separate circuit breaker for 1C API
_onec_breaker = CircuitBreaker(fail_max=5, timeout_duration=30)


class OneCAPIError(Exception):
    """Raised when a 1C API call fails."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"1C API {status}: {message}")


class OneCClient:
    """HTTP client for the 1C ERP REST API.

    Features:
      - Basic Auth authentication
      - Circuit breaker (aiobreaker: fail_max=5, timeout=30s)
      - Retry with exponential backoff (1s, 2s) for 429/503
      - Configurable request timeout
    """

    def __init__(self, base_url: str, username: str, password: str, timeout: int = 10) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = aiohttp.BasicAuth(username, password)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def open(self) -> None:
        """Open the HTTP session."""
        self._session = aiohttp.ClientSession(
            timeout=self._timeout,
            auth=self._auth,
            headers={
                "Accept": "application/json",
            },
        )

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    # --- Catalog (get_wares) ---

    async def get_wares_incremental(self, network: str) -> dict[str, Any]:
        """Get changed wares for a trading network (incremental sync).

        GET /Trade/hs/site/get_wares/?TradingNetwork={network}
        """
        return await self._get(
            "/Trade/hs/site/get_wares/",
            params={"TradingNetwork": network},
        )

    async def confirm_wares_receipt(self, network: str) -> dict[str, Any]:
        """Confirm receipt of incremental wares update.

        GET /Trade/hs/site/get_wares/?TradingNetwork={network}&ConfirmationOfReceipt
        """
        return await self._get(
            "/Trade/hs/site/get_wares/",
            params={"TradingNetwork": network, "ConfirmationOfReceipt": ""},
        )

    async def get_wares_full(self, limit: int = 0) -> dict[str, Any]:
        """Get full catalog (all wares).

        GET /Trade/hs/site/get_wares/?UploadingAll
        """
        params: dict[str, Any] = {"UploadingAll": ""}
        if limit > 0:
            params["limit"] = limit
        return await self._get("/Trade/hs/site/get_wares/", params=params)

    async def get_wares_by_sku(self, sku: str) -> dict[str, Any]:
        """Get a specific ware by SKU.

        GET /Trade/hs/site/get_wares/?sku={sku}
        """
        return await self._get("/Trade/hs/site/get_wares/", params={"sku": sku})

    # --- Stock & Prices ---

    async def get_stock(self, network: str) -> dict[str, Any]:
        """Get stock and prices for a trading network.

        GET /Trade/hs/site/get_stock/?TradingNetwork={network}
        """
        return await self._get(
            "/Trade/hs/site/get_stock/",
            params={"TradingNetwork": network},
        )

    # --- Pickup points ---

    async def get_pickup_points(self, network: str) -> dict[str, Any]:
        """Get pickup/delivery points for a trading network.

        GET /Trade/hs/site/points/?TradingNetwork={network}
        """
        return await self._get(
            "/Trade/hs/site/points/",
            params={"TradingNetwork": network},
        )

    # --- Fitting service prices ---

    async def get_fitting_prices(self) -> dict[str, Any]:
        """Get fitting service prices for all stations.

        GET /Trade/hs/site/price_service
        """
        return await self._get("/Trade/hs/site/price_service")

    # --- Fitting service (REST) ---

    async def get_fitting_stations_rest(self) -> dict[str, Any]:
        """Get fitting stations via REST API.

        GET /Trade/hs/site/TireService/station
        """
        return await self._get("/Trade/hs/site/TireService/station")

    async def find_storage(self, storage_number: str = "", phone: str = "") -> dict[str, Any]:
        """Find tire storage contracts by phone or contract number.

        GET /Trade/hs/site/TireService/findStorage
        """
        params: dict[str, Any] = {}
        if storage_number:
            params["StorageNumber"] = storage_number
        if phone:
            params["phone"] = phone
        return await self._get("/Trade/hs/site/TireService/findStorage", params=params)

    # --- Nova Poshta reference data ---

    async def get_novapost_cities(self) -> dict[str, Any]:
        """GET /Trade/hs/site/novapost/city"""
        return await self._get("/Trade/hs/site/novapost/city")

    async def get_novapost_branches(self) -> dict[str, Any]:
        """GET /Trade/hs/site/novapost/branch"""
        return await self._get("/Trade/hs/site/novapost/branch")

    # --- Order creation (direct 1C REST) ---

    async def create_order_1c(
        self,
        order_number: str,
        items: list[dict[str, Any]],
        customer_phone: str,
        payment_method: str = "cod",
        delivery_type: str = "pickup",
        delivery_address: str = "",
        delivery_city: str = "",
        pickup_point_id: str = "",
        customer_name: str = "",
        network: str = "ProKoleso",
    ) -> dict[str, Any]:
        """Create an order directly in 1C ERP.

        POST /Trade/hs/site/zakaz/

        Args:
            order_number: Order number (e.g. "AI-42").
            items: List of {product_id, quantity, price?}.
            customer_phone: Customer phone.
            payment_method: "cod", "online", or "card_on_delivery".
            delivery_type: "pickup" or "delivery".
            delivery_address: Delivery address (for delivery type).
            delivery_city: Delivery city (for delivery type).
            pickup_point_id: Pickup point ID (for pickup type).
            customer_name: Customer name.
            network: Trading network ("ProKoleso" or "Tshina").

        Returns:
            1C response dict.
        """
        # Map payment method to 1C code
        payment_map = {"cod": "1", "online": "6", "card_on_delivery": "4"}
        payment_code = payment_map.get(payment_method, "1")

        # Map delivery type
        delivery_map = {"pickup": "Точки выдачи", "delivery": "NovaPost"}
        delivery_1c = delivery_map.get(delivery_type, "Точки выдачи")

        # Map network to store code
        store_map = {"ProKoleso": "prokoleso", "Tshina": "tshina"}
        store_code = store_map.get(network, network.lower())

        # Build items list for 1C
        order_items = []
        for item in items:
            order_item: dict[str, Any] = {
                "sku": item.get("product_id", ""),
                "quantity": item.get("quantity", 1),
            }
            if "price" in item:
                order_item["price"] = item["price"]
            order_items.append(order_item)

        body: dict[str, Any] = {
            "order_number": order_number,
            "store": store_code,
            "order_channel": "AI_AGENT",
            "fizlico": "ФизЛицо",
            "phone": customer_phone,
            "payment_type": payment_code,
            "delivery_type": delivery_1c,
            "items": order_items,
        }

        if customer_name:
            body["person"] = customer_name
        if delivery_address:
            body["delivery_address"] = delivery_address
        if delivery_city:
            body["delivery_city"] = delivery_city
        if pickup_point_id:
            body["pickup_point_id"] = pickup_point_id

        return await self._post("/Trade/hs/site/zakaz/", json_data=body)

    # --- HTTP helpers ---

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GET request with circuit breaker and retry."""
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a POST request with circuit breaker and retry."""
        return await self._request("POST", path, json_data=json_data)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with circuit breaker, retry, and error handling."""
        if self._session is None:
            raise RuntimeError("OneCClient not opened — call open() first")

        url = f"{self._base_url}{path}"

        try:
            result: dict[str, Any] = await _onec_breaker.call_async(
                self._request_with_retry,
                method,
                url,
                params=params,
                json_data=json_data,
            )
            return result
        except CircuitBreakerError as err:
            logger.error("Circuit breaker OPEN for 1C API")
            raise OneCAPIError(503, "1С сервіс тимчасово недоступний") from err

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute request with retry for 429/503."""
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await self._do_request(method, url, params=params, json_data=json_data)
            except OneCAPIError as exc:
                last_exc = exc
                if exc.status not in _RETRYABLE_STATUSES:
                    raise
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "1C API %d, retry %d/%d in %.1fs: %s",
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
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a single HTTP request."""
        assert self._session is not None

        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if json_data is not None:
            kwargs["json"] = json_data

        async with self._session.request(method, url, **kwargs) as resp:
            if resp.status == 401:
                logger.critical("1C API authentication failed — check credentials")

            # 1C may return body in Windows-1251 instead of UTF-8
            raw = await resp.read()
            try:
                body_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                body_text = raw.decode("windows-1251")

            if resp.status >= 400:
                raise OneCAPIError(resp.status, body_text[:200])

            if resp.status == 204:
                return {}

            data: dict[str, Any] = json.loads(body_text)
            return data
