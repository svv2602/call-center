"""Async client for the Nova Poshta JSON API — delivery price quotes.

One endpoint (``POST <api_url>``), body ``{apiKey, modelName, calledMethod,
methodProperties}``, answer ``{success, data: [...], errors: [...]}``. Used:

- ``Address.searchSettlements {CityName, Limit}`` → ``data[0].Addresses[0].DeliveryCity``
  is the CityRef;
- ``Common.getTiresWheelsList`` → cargo descriptions for tyres and wheels, by
  rim-size bucket («Шина легкова R 15-17 », «Шина вантажна R 17,5», …; the
  descriptions carry trailing spaces);
- ``InternetDocument.getDocumentPrice`` with ``CargoType=TiresWheels`` →
  ``data[0].Cost`` and, with ``RedeliveryCalculate``, ``data[0].CostRedelivery``
  (the cash-on-delivery fee). The price depends on the declared value
  (``Cost``), not only on the bucket — so the value is part of the cache key;
- ``Address.getWarehouses {CityRef, WarehouseId}`` → is there such a branch.

The client is called from a live call: the timeout is short, there is no
retry loop, and every failure (timeout, HTTP ≠ 200, ``success=false``, empty
``data``, breaker open) yields ``None`` plus a warning — never an exception —
so the caller falls back to the «за тарифами перевізника» phrase.

The API key is passed in by the caller (it lives only in the prod env) and
never reaches a log line, an exception message or ``repr``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import aiohttp
from aiobreaker import CircuitBreaker, CircuitBreakerError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from src.config import NovaPoshtaSettings

logger = logging.getLogger(__name__)

# Separate circuit breaker for the Nova Poshta API. ``timeout_duration`` must be a
# timedelta: aiobreaker adds it to a datetime when the breaker opens.
_np_breaker = CircuitBreaker(fail_max=5, timeout_duration=timedelta(seconds=30))

CargoKind = Literal["tire", "wheel"]

# Passenger rim-size buckets, as Nova Poshta groups them.
_PASSENGER_BUCKETS: tuple[tuple[int, int], ...] = ((13, 14), (15, 17), (18, 19), (20, 21), (22, 23))
# Truck rim sizes Nova Poshta lists one by one.
_TRUCK_SIZES: tuple[float, ...] = (17.5, 19.5, 20.0, 22.5)

_BUCKET_RE = re.compile(r"R\s*(\d+(?:[.,]\d+)?(?:\s*-\s*\d+)?)")

_CACHE_PREFIX = "np:"


class _NovaPoshtaTransportError(Exception):
    """HTTP status or body shape that counts as an outage for the breaker."""


class CargoLine(NamedTuple):
    """One position of the parcel: ``qty`` tyres or wheels of one rim size."""

    kind: CargoKind
    diameter: float | str
    qty: int
    truck: bool = False


@dataclass(frozen=True)
class Quote:
    """Delivery price in UAH.

    ``cost`` — warehouse → warehouse; ``door_cost`` — warehouse → door, only
    when asked for; ``cod_fee`` — Nova Poshta's cash-on-delivery fee for the
    declared value.
    """

    cost: float
    door_cost: float | None
    cod_fee: float

    @property
    def door_surcharge(self) -> float | None:
        """How much more the courier costs than the branch, if it was asked for."""
        if self.door_cost is None:
            return None
        return self.door_cost - self.cost


def _format_size(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _parse_diameter(diameter: float | str) -> float | None:
    if isinstance(diameter, bool):
        return None
    if isinstance(diameter, int | float):
        return float(diameter)
    text = str(diameter).strip().upper().lstrip("R").strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def cargo_bucket(diameter: float | str, kind: CargoKind, truck: bool = False) -> str | None:
    """Nova Poshta bucket label for a rim size, e.g. ``"R 15-17"`` / ``"R 17.5"``.

    The label matches the keys of ``NovaPoshtaClient.cargo_refs()``. Tyres and
    wheels share the buckets; ``kind`` is validated only. A size outside the
    list (R12, R24, a passenger R17.5, …) → ``None``.
    """
    if kind not in ("tire", "wheel"):
        return None
    value = _parse_diameter(diameter)
    if value is None:
        return None
    if truck:
        for size in _TRUCK_SIZES:
            if value == size:
                return f"R {_format_size(size)}"
        return None
    if value != int(value):
        return None
    for low, high in _PASSENGER_BUCKETS:
        if low <= value <= high:
            return f"R {low}-{high}"
    return None


def _bucket_from_description(description: str) -> str | None:
    match = _BUCKET_RE.search(description)
    if match is None:
        return None
    raw = re.sub(r"\s+", "", match.group(1)).replace(",", ".")
    if "-" in raw:
        return f"R {raw}"
    try:
        return f"R {_format_size(float(raw))}"
    except ValueError:
        return None


def _normalize_city(name: str) -> str:
    return " ".join(name.split()).casefold()


class NovaPoshtaClient:
    """Quote client; safe to keep one instance per process (it holds the cache)."""

    def __init__(
        self,
        api_key: str,
        settings: NovaPoshtaSettings,
        redis: Any = None,
        http: aiohttp.ClientSession | None = None,
        *,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Nova Poshta client needs an API key")
        self._api_key = api_key.strip()
        self._settings = settings
        self._redis = redis
        self._http = http
        self._own_http = http is None
        self._breaker = breaker if breaker is not None else _np_breaker
        self._clock = clock
        self._timeout = aiohttp.ClientTimeout(total=settings.timeout_sec)
        self._memory: dict[str, tuple[float, Any]] = {}

    def __repr__(self) -> str:
        return f"NovaPoshtaClient(api_url={self._settings.api_url!r})"

    async def close(self) -> None:
        """Close the HTTP session if the client opened it."""
        if self._own_http and self._http is not None:
            await self._http.close()
            self._http = None

    # --- transport ---

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***")

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One HTTP round trip; raises on transport errors (counted by the breaker)."""
        if self._http is None:
            self._http = aiohttp.ClientSession()
        async with self._http.post(
            self._settings.api_url, json=payload, timeout=self._timeout
        ) as resp:
            if resp.status != 200:
                raise _NovaPoshtaTransportError(f"HTTP {resp.status}")
            body = await resp.json(content_type=None)
        if not isinstance(body, dict):
            raise _NovaPoshtaTransportError("response is not a JSON object")
        return body

    async def _call(
        self, model: str, method: str, properties: dict[str, Any]
    ) -> list[dict[str, Any]] | None:
        """``data`` of a successful answer (may be empty), else ``None``."""
        payload = {
            "apiKey": self._api_key,
            "modelName": model,
            "calledMethod": method,
            "methodProperties": properties,
        }
        try:
            body = await self._breaker.call_async(self._post, payload)
        except CircuitBreakerError:
            logger.warning("Nova Poshta %s.%s skipped: circuit breaker open", model, method)
            return None
        except (aiohttp.ClientError, TimeoutError, _NovaPoshtaTransportError, ValueError) as exc:
            logger.warning(
                "Nova Poshta %s.%s failed: %s %s",
                model,
                method,
                type(exc).__name__,
                self._redact(str(exc))[:200],
            )
            return None
        if body.get("success") is not True:
            errors = body.get("errors") or body.get("errorCodes") or []
            logger.warning(
                "Nova Poshta %s.%s refused: %s",
                model,
                method,
                self._redact(json.dumps(errors, ensure_ascii=False))[:300],
            )
            return None
        data = body.get("data")
        if not isinstance(data, list):
            logger.warning("Nova Poshta %s.%s: no data list in the answer", model, method)
            return None
        return [item for item in data if isinstance(item, dict)]

    # --- cache ---

    async def _cache_get(self, key: str) -> Any:
        hit = self._memory.get(key)
        if hit is not None:
            expires_at, value = hit
            if self._clock() < expires_at:
                return value
            del self._memory[key]
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_CACHE_PREFIX + key)
        except Exception as exc:  # the cache is optional
            logger.warning("Nova Poshta cache read failed: %s", type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw if isinstance(raw, str) else raw.decode())
        except (ValueError, UnicodeDecodeError):
            logger.warning("Nova Poshta cache entry %s is not JSON", key)
            return None

    async def _cache_set(self, key: str, value: Any, ttl: int) -> None:
        self._memory[key] = (self._clock() + ttl, value)
        if self._redis is None:
            return
        try:
            await self._redis.setex(_CACHE_PREFIX + key, ttl, json.dumps(value, ensure_ascii=False))
        except Exception as exc:  # the cache is optional
            logger.warning("Nova Poshta cache write failed: %s", type(exc).__name__)

    # --- API ---

    async def resolve_city(self, name: str) -> str | None:
        """CityRef (``DeliveryCity``) of the first settlement found, else ``None``."""
        normalized = _normalize_city(name or "")
        if not normalized:
            return None
        key = "city:" + hashlib.sha256(normalized.encode()).hexdigest()[:24]
        cached = await self._cache_get(key)
        if isinstance(cached, str) and cached:
            return cached
        data = await self._call(
            "Address", "searchSettlements", {"CityName": " ".join(name.split()), "Limit": "5"}
        )
        if not data:
            return None
        addresses = data[0].get("Addresses")
        if not isinstance(addresses, list) or not addresses or not isinstance(addresses[0], dict):
            logger.info("Nova Poshta: no settlement for %r", normalized)
            return None
        ref = addresses[0].get("DeliveryCity")
        if not isinstance(ref, str) or not ref:
            return None
        await self._cache_set(key, ref, self._settings.cache_ttl_refs_sec)
        return ref

    async def cargo_refs(self) -> dict[tuple[CargoKind, bool, str], str] | None:
        """``{(kind, truck, bucket): Ref}`` from ``getTiresWheelsList``, cached for a day."""
        cached = await self._cache_get("cargo_refs")
        if isinstance(cached, list) and cached:
            return {(row[0], bool(row[1]), row[2]): row[3] for row in cached}
        data = await self._call("Common", "getTiresWheelsList", {})
        if not data:
            return None
        refs: dict[tuple[CargoKind, bool, str], str] = {}
        for item in data:
            ref = item.get("Ref")
            description = str(item.get("Description") or "").strip()
            if not isinstance(ref, str) or not ref or not description:
                continue
            kind_raw = str(item.get("DescriptionType") or "").strip()
            lowered = description.casefold()
            kind: CargoKind
            if kind_raw == "Tires" or (not kind_raw and lowered.startswith("шина")):
                kind = "tire"
            elif kind_raw == "Wheels" or (not kind_raw and lowered.startswith("диск")):
                kind = "wheel"
            else:
                continue
            bucket = _bucket_from_description(description)
            if bucket is None:
                continue
            refs[(kind, "вантаж" in lowered, bucket)] = ref
        if not refs:
            logger.warning("Nova Poshta getTiresWheelsList: no usable descriptions")
            return None
        rows = [[k[0], k[1], k[2], v] for k, v in refs.items()]
        await self._cache_set("cargo_refs", rows, self._settings.cache_ttl_refs_sec)
        return refs

    async def quote(
        self,
        city_ref: str,
        lines: Iterable[CargoLine | tuple[Any, ...]],
        declared_value: int,
        door: bool = False,
    ) -> Quote | None:
        """Price from the sender city to ``city_ref`` for the given tyres/wheels.

        ``declared_value`` (UAH) is both the declared cost and the
        cash-on-delivery amount. ``door=True`` adds a warehouse → door price.
        """
        if not city_ref or declared_value <= 0:
            return None
        parsed = [line if isinstance(line, CargoLine) else CargoLine(*line) for line in lines]
        if not parsed:
            return None
        refs = await self.cargo_refs()
        if refs is None:
            return None
        amounts: dict[str, int] = {}
        for line in parsed:
            bucket = cargo_bucket(line.diameter, line.kind, line.truck)
            ref = refs.get((line.kind, bool(line.truck), bucket)) if bucket else None
            if ref is None or line.qty <= 0:
                logger.warning(
                    "Nova Poshta quote: no cargo bucket for %s R%s x%s",
                    line.kind,
                    line.diameter,
                    line.qty,
                )
                return None
            amounts[ref] = amounts.get(ref, 0) + int(line.qty)
        details = sorted(amounts.items())

        cache_src = json.dumps([city_ref, details, int(declared_value), bool(door)])
        key = "quote:" + hashlib.sha256(cache_src.encode()).hexdigest()[:24]
        cached = await self._cache_get(key)
        if isinstance(cached, dict):
            return Quote(**cached)

        sender = await self.resolve_city(self._settings.sender_city)
        if sender is None:
            return None
        properties: dict[str, Any] = {
            "CitySender": sender,
            "CityRecipient": city_ref,
            "ServiceType": "WarehouseWarehouse",
            "Cost": str(int(declared_value)),
            "CargoType": "TiresWheels",
            "CargoDetails": [
                {"CargoDescription": ref, "Amount": str(amount)} for ref, amount in details
            ],
            "RedeliveryCalculate": {"CargoType": "Money", "Amount": str(int(declared_value))},
        }
        data = await self._call("InternetDocument", "getDocumentPrice", properties)
        prices = self._prices(data, with_cod=True)
        if prices is None:
            return None
        cost, cod_fee = prices
        door_cost: float | None = None
        if door:
            door_data = await self._call(
                "InternetDocument",
                "getDocumentPrice",
                {**properties, "ServiceType": "WarehouseDoors"},
            )
            door_prices = self._prices(door_data, with_cod=False)
            if door_prices is None:
                return None
            door_cost = door_prices[0]
        result = Quote(cost=cost, door_cost=door_cost, cod_fee=cod_fee or 0.0)
        await self._cache_set(key, asdict(result), self._settings.cache_ttl_price_sec)
        return result

    @staticmethod
    def _prices(
        data: list[dict[str, Any]] | None, *, with_cod: bool
    ) -> tuple[float, float | None] | None:
        if not data:
            if data is not None:
                logger.warning("Nova Poshta getDocumentPrice: empty data")
            return None
        try:
            cost = float(data[0]["Cost"])
            cod = float(data[0]["CostRedelivery"]) if with_cod else None
        except (KeyError, TypeError, ValueError):
            logger.warning("Nova Poshta getDocumentPrice: no Cost/CostRedelivery in the answer")
            return None
        return cost, cod

    async def find_warehouse(
        self, city_ref: str, number: str
    ) -> Literal["found", "not_found"] | None:
        """Is there a branch/postomat with this number in the city; ``None`` on failure."""
        wanted = (number or "").strip().lstrip("№#").strip()
        if not city_ref or not wanted:
            return None
        data = await self._call(
            "Address", "getWarehouses", {"CityRef": city_ref, "WarehouseId": wanted}
        )
        if data is None:
            return None
        found = any(str(item.get("Number", "")).strip() == wanted for item in data)
        return "found" if found else "not_found"
