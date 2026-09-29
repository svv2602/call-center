"""Store API HTTP client with circuit breaker and retry.

Integrates with the tire shop's REST API for product search,
availability checks, and order management.

MVP: search_tires and check_availability can use PostgreSQL + Redis
(synced from 1C) when db_engine/onec_client are provided.
Falls back to HTTP calls if not (backward compat).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

import aiohttp
from aiobreaker import CircuitBreaker, CircuitBreakerError

from src.agent.network_policy import RECOMMEND_COUNT_MAX, RECOMMEND_COUNT_MIN
from src.store_client.catalog_types import TIRE_SEARCH_TYPES, WHEEL

logger = logging.getLogger(__name__)

# ── Tyre attributes the catalog keeps only as text ──────────────────────
# Measured on prod 2026-09-28: ``tire_products.studded`` is false on all
# 68730 rows — the sync never sets it. 1C writes the fact into
# ``description``: «… [99T] Шип» (studded) vs «… Під шип» / «Под шип»
# (studdable, sold without studs). Model names like «WinSpike» / «Stud»
# are sold unstudded, so only the standalone word «шип» counts.
_STUDDED_SQL = (
    r"(COALESCE(p.description, '') ~* '(^|[^а-яёіїєґa-z])шип([^а-яёіїєґa-z]|$)'"
    r" AND COALESCE(p.description, '') !~* '(под|під)\s+шип')"
)
# RunFlat: 1034 of 60578 passenger SKU, markers RunFlat / Run Flat / ZP /
# SSR / ROF / RFT in ``description``.
_RUNFLAT_RE = r"run\s?flat|(^|[^a-z])(rft|zp|ssr|rof)([^a-z]|$)"
_RUNFLAT_SQL = f"COALESCE(p.description, '') ~* '{_RUNFLAT_RE}'"
#: Per-row RunFlat fact of the offer (``runflat: true`` on the item); not the
#: filter expression, so the filter stays absent from an unfiltered query.
_RUNFLAT_COLUMN_SQL = f"(p.description ~* '{_RUNFLAT_RE}') IS TRUE"
# XL (reinforced): ``load_rating`` holds only the index (no «XL» on any of
# 68814 rows, prod 2026-09-28); 1C writes the fact into ``description`` —
# «… Pilot Alpin 5 XL NF0 [103V]» (3876 rows). ``commercial`` is a column.
_XL_SQL = r"COALESCE(p.description, '') ~* '(^|[^a-z])xl([^a-z]|$)|extra\s*load|reinforced'"

#: Rows the offer ranking chooses from (in-stock passenger SKU of one size
#: and season: at most ~110 on prod 2026-09-28).
_RANKING_WINDOW = 300

#: Result markers of the relaxation ladder (voiced by the prompt layer).
CAVEAT_NO_STUDDED = "no_studded_offer_friction"
CAVEAT_BRAND_UNAVAILABLE = "brand_unavailable_alternatives"
CAVEAT_XL_NONE = "xl_none_offer_regular"
CAVEAT_RUNFLAT_NONE = "runflat_none"
#: ``warning`` of a result offering non-RunFlat tyres for a car that leaves
#: the factory on RunFlat (set by the ``search_tires`` wrapper in ``main``).
WARNING_RUNFLAT_REQUIRED = "runflat_required"

#: ``search_tires(price_mode=…)`` under sales (tshina ``cc06d65ac``/``f7f5418b8``):
#: «а дешевше?» — cheaper than the cheapest tyre shown; «схожі за ціною» — a
#: corridor round the median of the shown prices, widened once when empty.
PRICE_MODE_CHEAPER = "cheaper"
PRICE_MODE_SIMILAR = "similar"
PRICE_CORRIDOR_STEPS = (0.10, 0.20)
#: How many offered tyres the caller hears (the compressor keeps ``items[:3]``).
SHOWN_TIRES = 3
#: Search params that carry price bounds (never the rear axle's).
PRICE_FILTER_KEYS = frozenset({"price_below", "price_from", "price_to", "exclude_ids"})
#: What a «дешевше/схожі» search keeps from the last offer when the model
#: leaves it out: the size (both axles), the season and the caller's needs —
#: never the brand (a preference the model passes itself).
TIRE_OFFER_KEYS = (
    "width",
    "profile",
    "diameter",
    "rear_width",
    "rear_profile",
    "rear_diameter",
    "season",
    "studded",
    "runflat",
    "xl",
    "commercial",
)
#: ``budget_source`` of a sales ``search_tires`` result: the caller named a
#: budget (only then «в межах вашого бюджету»), or the bounds are the corridor
#: of the last offer.
BUDGET_SOURCE_CALLER = "caller"
BUDGET_SOURCE_CORRIDOR = "price_corridor"


def _positive_price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def shown_tire_offer(result: Any) -> tuple[list[float], list[str]]:
    """Per-tyre prices and ids of the tyres the caller heard (``items[:3]``)."""
    if not isinstance(result, dict):
        return [], []
    prices: list[float] = []
    ids: list[str] = []
    for item in (result.get("items") or [])[:SHOWN_TIRES]:
        if not isinstance(item, dict):
            continue
        price = _positive_price(item.get("price"))
        if price is not None:
            prices.append(price)
        if item.get("id"):
            ids.append(str(item["id"]))
    return prices, ids


def price_filter(
    mode: str, prices: list[float], step: float = PRICE_CORRIDOR_STEPS[0]
) -> dict[str, float]:
    """Search bounds for ``price_mode`` over the shown per-tyre ``prices``.

    ``cheaper`` → ``price_below`` = the cheapest shown (strictly below);
    ``similar`` → ``price_from``/``price_to`` = median ± ``step``. No prices or
    an unknown mode → ``{}`` (an ordinary search).
    """
    valid = sorted(p for p in (_positive_price(x) for x in prices) if p is not None)
    if not valid:
        return {}
    if mode == PRICE_MODE_CHEAPER:
        return {"price_below": valid[0]}
    if mode == PRICE_MODE_SIMILAR:
        mid = len(valid) // 2
        median = valid[mid] if len(valid) % 2 else (valid[mid - 1] + valid[mid]) / 2
        return {
            "price_from": round(median * (1 - step), 2),
            "price_to": round(median * (1 + step), 2),
        }
    return {}


def within_price_filter(item: Any, bounds: dict[str, Any]) -> bool:
    """An offered item obeys ``price_filter`` bounds (default-deny on no price)."""
    if not bounds:
        return True
    price = _positive_price(item.get("price")) if isinstance(item, dict) else None
    if price is None:
        return False
    if "price_below" in bounds and not price < bounds["price_below"]:
        return False
    return "price_from" not in bounds or bounds["price_from"] <= price <= bounds["price_to"]


#: Cars that leave the factory on RunFlat (tshina_new ``2232038ce``): the
#: brand of ``vehicle_brands.name`` (lower-case) → the model families, or
#: ``None`` for every model. A family is the first segment of the model name
#: («GLE-Class (W166)», «GLE AMG» → ``gle``; «G-Class (W463)» → ``g``;
#: «GL-Class» / «GLA-Class» stay out).
RUNFLAT_REQUIRED_VEHICLES: dict[str, frozenset[str] | None] = {
    "bmw": None,
    "mercedes": frozenset({"gle", "gls", "g"}),
}


def runflat_required(vehicle: dict[str, Any] | None) -> bool:
    """The car (``{brand, model?}``) is on `RUNFLAT_REQUIRED_VEHICLES` — default-deny:
    an unknown brand, or a listed brand whose model is unknown when the list
    names families, is not."""
    if not isinstance(vehicle, dict):
        return False
    brand = str(vehicle.get("brand") or "").strip().lower()
    if brand not in RUNFLAT_REQUIRED_VEHICLES:
        return False
    families = RUNFLAT_REQUIRED_VEHICLES[brand]
    if families is None:
        return True
    model = str(vehicle.get("model") or "").strip().lower()
    family = re.split(r"[\s(\-]", model, maxsplit=1)[0] if model else ""
    return family in families


def runflat_warning(
    result: Any, vehicle: dict[str, Any] | None, params: dict[str, Any]
) -> str | None:
    """`WARNING_RUNFLAT_REQUIRED` when a RunFlat car is offered a non-RunFlat tyre.

    Silent when the caller refused RunFlat (``runflat=False``), when the
    ladder already said RunFlat is missing (``relaxed`` has ``runflat``), or
    when every offered item is RunFlat.
    """
    if not isinstance(result, dict) or not result.get("items"):
        return None
    if params.get("runflat") is False or "runflat" in (result.get("relaxed") or []):
        return None
    if not runflat_required(vehicle):
        return None
    if all(isinstance(i, dict) and i.get("runflat") is True for i in result["items"]):
        return None
    return WARNING_RUNFLAT_REQUIRED


def _tire_types_sql(bind_params: dict[str, Any]) -> str:
    """``m.type_id IN (…)`` over the tyre-search allowlist (default-deny).

    Wheels (``566``), truck tyres and any type 1C starts sending later stay
    out of every lookup that answers "which tyre".
    """
    names = []
    for i, type_id in enumerate(sorted(TIRE_SEARCH_TYPES)):
        name = f"tire_type_{i}"
        bind_params[name] = type_id
        names.append(f":{name}")
    return f"m.type_id IN ({', '.join(names)})"


def _clamp_recommend_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return RECOMMEND_COUNT_MAX
    return max(RECOMMEND_COUNT_MIN, min(RECOMMEND_COUNT_MAX, value))


def _format_tire_size(row: Any) -> str:
    """Format a tire size row as '235/65 R17' with optional axle suffix."""
    diameter = row["diameter"]
    # Show integer diameter when possible (17 not 17.0)
    d_str = str(int(diameter)) if diameter == int(diameter) else str(diameter)
    size = f"{row['width']}/{row['height']} R{d_str}"
    axle = row.get("axle", 0)
    if axle == 1:
        size += " (перед)"
    elif axle == 2:
        size += " (зад)"
    return size


def _staggered_pairs(rows: list[Any]) -> list[dict[str, str]] | None:
    """Factory front/rear pairs of a staggered fitment, or ``None``.

    ``vehicle_tire_sizes`` groups a pair by ``(kit_id, axle_group)`` with
    ``axle`` 1 = front, 2 = rear (prod 2026-09-28: 84211 clean 1+2 groups).
    Only factory sizes (``type`` 1), and only groups with exactly one front
    and one rear row — an ambiguous group is not guessed.
    """
    groups: dict[tuple[Any, Any], dict[int, list[Any]]] = {}
    for row in rows:
        if row["type"] != 1 or row.get("axle") not in (1, 2):
            continue
        kit_id = row.get("kit_id")
        if kit_id is None:
            continue
        axles = groups.setdefault((kit_id, row.get("axle_group")), {1: [], 2: []})
        axles[row["axle"]].append(row)

    pairs: list[dict[str, str]] = []
    for axles in groups.values():
        if len(axles[1]) != 1 or len(axles[2]) != 1:
            continue
        pair = {
            "front": _format_tire_size({**axles[1][0], "axle": 0}),
            "rear": _format_tire_size({**axles[2][0], "axle": 0}),
        }
        if pair not in pairs:
            pairs.append(pair)
    return pairs or None


# Retry config
_MAX_RETRIES = 2
_RETRY_DELAYS = [0.5, 1.0]  # exponential backoff (short for real-time calls)
_RETRYABLE_STATUSES = {429, 503}

class StoreAPIError(Exception):
    """Raised when a Store API call fails."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"Store API {status}: {message}")


class StoreClient:
    """HTTP client for the tire shop Store API.

    Features:
      - Per-instance circuit breaker (aiobreaker: fail_max=5, timeout=30s)
      - Retry with exponential backoff (1s, 2s) for 429/503
      - Request timeout: 5 seconds
      - X-Request-Id header for distributed tracing
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 5,
        db_engine: Any = None,
        redis: Any = None,
        stock_cache_ttl: int = 300,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        # Per-instance circuit breaker: each tenant gets its own breaker
        # so that Store API failure for one tenant doesn't block others
        self._breaker = CircuitBreaker(fail_max=5, timeout_duration=30)
        # 1C integration (MVP): PostgreSQL catalog + Redis stock cache
        self._db_engine = db_engine
        self._redis = redis
        self._stock_cache_ttl = stock_cache_ttl

    async def open(self) -> None:
        """Open the HTTP session with connection pooling."""
        connector = aiohttp.TCPConnector(
            limit=20,
            limit_per_host=10,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
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

    async def search_tires(self, network: str = "", **params: Any) -> dict[str, Any]:
        """Search tires by parameters.

        If db_engine is available, queries PostgreSQL catalog (synced from 1C).
        Otherwise falls back to HTTP Store API.
        """
        # MVP: use PostgreSQL catalog if available
        if self._db_engine is not None:
            try:
                return await self._search_tires_ladder(network=network, **params)
            except Exception:
                logger.warning("DB tire search failed, falling back to HTTP API", exc_info=True)

        # Fallback: HTTP Store API (knows nothing of the network ranking)
        for key in ("brand_priority", "recommend_count"):
            params.pop(key, None)

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
        self, product_id: str = "", query: str = "", network: str = "", **_: Any
    ) -> dict[str, Any]:
        """Check tire availability.

        If db_engine is available, checks Redis cache then PostgreSQL (synced from 1C).
        Otherwise falls back to HTTP Store API.
        """
        # MVP: use Redis/PostgreSQL if available
        if self._db_engine is not None:
            try:
                return await self._check_availability_1c(product_id, query, network=network)
            except Exception:
                logger.warning("DB availability check failed, falling back to HTTP API", exc_info=True)

        # Fallback: HTTP Store API
        if not product_id and query:
            search_result = await self._get("/api/v1/tires/search", params={"q": query})
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

    # --- Wheels (disks) ---

    async def search_disks(
        self,
        diameter: Any = None,
        pcd: Any = None,
        et: Any = None,
        dia: Any = None,
        width: Any = None,
        vehicle: Any = None,
        network: str = "",
        recommend_count: int = RECOMMEND_COUNT_MAX,
        vehicle_text: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """In-stock wheels of the network (``disk_products`` + ``tire_stock``).

        ``vehicle`` (``{brand, model, year}``) named → every offered wheel
        carries a ``check_disk_fit`` verdict; wheels that do not fit or are not
        recommended stay out of the offer; a car with several PCD / hub bores
        gets no offer but ``ambiguous_car`` (ask the year / modification).
        At most ``recommend_count`` (the network policy) variants, one per model.

        ``vehicle_text`` — the caller's utterance, passed by the `disk_intent`
        substitution (not in the LLM schema): with no ``vehicle`` the car is
        read out of it (`resolve_vehicle_text`).
        """
        from sqlalchemy import text

        from src.agent import disk_fitment as fit

        if self._db_engine is None:
            return {"total": 0, "items": [], "message": "Каталог дисків тимчасово недоступний"}

        diameter_i = fit.to_int(diameter)
        if diameter_i is None or diameter_i <= 0:
            return {"total": 0, "items": [], "error": "diameter_required"}
        wanted_pcd = None
        if pcd not in (None, ""):
            wanted_pcd = fit.parse_pcd(pcd)
            if wanted_pcd is None:
                return {"total": 0, "items": [], "error": "pcd_format"}
        network = network or "ProKoleso"
        count = _clamp_recommend_count(recommend_count)

        async with self._db_engine.connect() as conn:
            car = None
            vehicle_info: dict[str, Any] | None = None
            named = isinstance(vehicle, dict) and (vehicle.get("brand") or vehicle.get("model"))
            if not named and isinstance(vehicle_text, str) and vehicle_text.strip():
                vehicle = await self._vehicle_from_text(conn, vehicle_text)
            if isinstance(vehicle, dict) and (vehicle.get("brand") or vehicle.get("model")):
                car, vehicle_info = await self._disk_car_data(conn, vehicle)

            if car is not None and vehicle_info is not None:
                verdict = fit.check_disk_fit(fit.DiskSpec(), car)
                if verdict.status == fit.AMBIGUOUS_CAR:
                    vehicle_info["status"] = fit.AMBIGUOUS_CAR
                    vehicle_info["variants"] = verdict.reasons[0].car
                    return {
                        "total": 0,
                        "items": [],
                        "vehicle": vehicle_info,
                        "need": "vehicle_year_or_modification",
                    }

            # The car's own pattern narrows the query when the caller named none.
            if wanted_pcd is None and car is not None and car.pcds:
                wanted_pcd = car.pcds[0]

            conditions = [
                "m.type_id = :wheel_type",
                "d.parse_ok",
                "d.diameter = :diameter",
                "s.stock_quantity > 0",
            ]
            bind: dict[str, Any] = {
                "wheel_type": WHEEL,
                "diameter": diameter_i,
                "network": network,
                "result_limit": _RANKING_WINDOW,
            }
            if wanted_pcd is not None:
                # Coarse filter (own or doubled bolt count, either PCD); the
                # exact pattern match is ``disk_bolt_patterns`` below.
                conditions.append(
                    "d.bolt_count IN (:bolts, :bolts_double) AND (d.pcd = :pcd OR d.pcd_alt = :pcd)"
                )
                bind["bolts"] = wanted_pcd[0]
                bind["bolts_double"] = wanted_pcd[0] * 2
                bind["pcd"] = wanted_pcd[1]
            width_d = fit.to_decimal(width)
            if width_d is not None and width_d > 0:
                conditions.append("d.width_j = :width")
                bind["width"] = width_d
            et_d = fit.to_decimal(et)
            if et_d is not None:
                conditions.append("ABS(d.et - :et) <= :et_tolerance")
                bind["et"] = et_d
                bind["et_tolerance"] = fit.ET_FITS_MAX
            dia_d = fit.to_decimal(dia)
            if dia_d is not None and dia_d > 0:
                # The hub bore: a smaller wheel bore never mounts.
                conditions.append("d.dia > :dia_min")
                bind["dia_min"] = dia_d - fit.DIA_TOLERANCE

            where = " AND ".join(conditions)
            query = text(f"""
                SELECT p.sku AS id, m.manufacturer AS brand, m.name AS model, p.size,
                       d.diameter, d.width_j, d.bolt_count, d.pcd, d.pcd_alt, d.et, d.dia,
                       d.color,
                       COALESCE(s.price, 0) AS price,
                       COALESCE(s.stock_quantity, 0) AS stock_quantity
                FROM disk_products d
                JOIN tire_products p ON p.sku = d.sku
                JOIN tire_models m ON m.id = p.model_id
                JOIN tire_stock s ON s.sku = d.sku AND s.trading_network = :network
                WHERE {where}
                ORDER BY s.price ASC NULLS LAST
                LIMIT :result_limit
            """)
            rows = list((await conn.execute(query, bind)).mappings().all())

        tiers: dict[int, list[dict[str, Any]]] = {}
        excluded = 0
        for row in rows:
            spec = fit.DiskSpec.from_row(row)
            patterns = fit.disk_bolt_patterns(spec)
            if wanted_pcd is not None and wanted_pcd not in patterns:
                continue
            item: dict[str, Any] = {
                "id": row["id"],
                "brand": row["brand"],
                "model": row["model"],
                "size": row["size"],
                "color": row["color"],
                "diameter": spec.diameter,
                "width": fit.num_str(spec.width),
                "pcd": "/".join(fit.pcd_str(p) for p in patterns),
                "et": fit.num_str(spec.et),
                "dia": fit.num_str(spec.dia),
                "price": row["price"],
                "stock_quantity": row["stock_quantity"],
                "in_stock": row["stock_quantity"] > 0,
            }
            severity = 0
            if car is not None:
                verdict = fit.check_disk_fit(spec, car)
                if verdict.status not in fit.OFFERABLE:
                    excluded += 1
                    continue
                item["fit"] = verdict.as_dict()
                severity = fit.SEVERITY[verdict.status]
            elif vehicle_info is not None:
                # Car named but not in the catalogue: never "fits".
                item["fit"] = fit.FitVerdict(fit.CANNOT_CONFIRM).as_dict()
            tiers.setdefault(severity, []).append(item)

        # Best verdict first; within a verdict — one model each, price spread.
        picked: list[dict[str, Any]] = []
        for severity in sorted(tiers):
            left = count - len(picked)
            if left <= 0:
                break
            picked.extend(self._rank_tires(tiers[severity], (), count)[:left])

        items = [{k: v for k, v in i.items() if k != "stock_quantity"} for i in picked]
        result: dict[str, Any] = {"total": len(items), "items": items}
        if vehicle_info is not None:
            result["vehicle"] = vehicle_info
        if excluded:
            result["excluded_not_fitting"] = excluded
        if car is not None:
            # tshina owner rule: spacers and re-drilling are never offered.
            result["fit_policy"] = "no_spacers_no_redrilling"
        return result

    async def resolve_vehicle_text(self, text: str) -> dict[str, Any] | None:
        """The car named in a caller's utterance: ``{brand, model?, year?}`` or None.

        «потрібні литі диски шістнадцятий радіус на Шкоду Октавію 2018» →
        ``{"brand": "Skoda", "model": "Octavia", "year": 2018}``. The brand must
        be the only one every car word agrees on; a model not found or not
        unique leaves the brand alone — never a guess.
        """
        if self._db_engine is None or not text:
            return None
        async with self._db_engine.connect() as conn:
            return await self._vehicle_from_text(conn, text)

    @staticmethod
    async def _car_word_brands(conn: Any, phrase: str) -> dict[int, dict[str, Any]]:
        """Brands a phrase may name: ``{brand_id: {"name", "models": {id: name}}}``.

        Exact brand name, and ``vehicle_aliases`` of the phrase as heard or put
        back into the nominative (first form with a hit). Every brand an
        ambiguous alias spans is kept — the caller intersects them.
        """
        from sqlalchemy import text

        from src.agent.disk_intent import word_forms
        from src.agent.vehicle_alias_lookup import resolve_by_alias

        brands: dict[int, dict[str, Any]] = {}

        def _add(brand_id: Any, name: Any, model_id: Any = None, model_name: Any = None) -> None:
            entry = brands.setdefault(brand_id, {"name": name, "models": {}})
            if model_id is not None:
                entry["models"][model_id] = model_name

        result = await conn.execute(
            text("SELECT id, name FROM vehicle_brands WHERE LOWER(name) = LOWER(:name)"),
            {"name": phrase},
        )
        for row in result.mappings().all():
            _add(row["id"], row["name"])
        for form in word_forms(phrase):
            res = await resolve_by_alias(conn, form)
            if res.ambiguous_matches:
                for m in res.ambiguous_matches:
                    _add(m["brand_id"], m["brand_name"], m["model_id"], m.get("model_name"))
                break
            if res.brand_id is not None:
                _add(res.brand_id, res.brand_name, res.model_id, res.model_name)
                break
        return brands

    @classmethod
    async def _vehicle_from_text(cls, conn: Any, text: str) -> dict[str, Any] | None:
        """`resolve_vehicle_text` on an open connection."""
        from src.agent.disk_intent import vehicle_words, word_forms

        words, year = vehicle_words(text)
        hits: list[tuple[int, int, dict[int, dict[str, Any]]]] = []
        for n in (2, 1):
            for i in range(len(words) - n + 1):
                brands = await cls._car_word_brands(conn, " ".join(words[i : i + n]))
                if brands:
                    hits.append((i, i + n, brands))
        if not hits:
            return None
        agreed = set.intersection(*(set(b) for _, _, b in hits))
        if len(agreed) != 1:
            # Two brands named, or a word several brands share: ask, never guess.
            logger.info(
                "vehicle_from_text ambiguous brands=%s text=%r",
                sorted({str(e["name"]) for _, _, b in hits for e in b.values()}),
                text[:120],
            )
            return None
        [brand_id] = agreed
        entries = [b[brand_id] for _, _, b in hits]
        vehicle: dict[str, Any] = {"brand": entries[0]["name"]}
        models = {mid: name for e in entries for mid, name in e["models"].items()}
        if len(models) == 1:
            vehicle["model"] = next(iter(models.values()))
        else:
            # The words after the brand name, up to three, longest first.
            brand_spans = [(s, e) for s, e, b in hits if not b[brand_id]["models"]]
            used = {i for s, e in brand_spans for i in range(s, e)}
            start = min(e for _, e in brand_spans) if brand_spans else min(s for s, _, _ in hits)
            rest = [w for i, w in enumerate(words) if i >= start and i not in used][:3]
            for size in range(len(rest), 0, -1):
                for form in word_forms(" ".join(rest[:size])):
                    row = await cls._find_vehicle_model(conn, brand_id, form)
                    if row is not None:
                        vehicle["model"] = row["name"]
                        break
                if "model" in vehicle:
                    break
        if year is not None:
            vehicle["year"] = year
        logger.info("vehicle_from_text vehicle=%r text=%r", vehicle, text[:120])
        return vehicle

    async def _disk_car_data(
        self, conn: Any, vehicle: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        """``CarFitData`` of a named car (``None`` — car not in the catalogue)
        and what to tell the LLM about the lookup."""
        from sqlalchemy import text

        from src.agent.disk_fitment import CarFitData, to_int

        brand = str(vehicle.get("brand") or "")
        model = str(vehicle.get("model") or "")
        info: dict[str, Any] = {"found": False, "brand": brand, "model": model}
        brand_row = await self._find_vehicle_brand(conn, brand) if brand else None
        if brand_row is None:
            return None, info
        info["brand"] = brand_row["name"]
        model_row = await self._find_vehicle_model(conn, brand_row["id"], model) if model else None
        if model_row is None:
            return None, info
        info["model"] = model_row["name"]

        kit_filter = "k.model_id = :mid"
        bind: dict[str, Any] = {"mid": model_row["id"]}
        year = to_int(vehicle.get("year"))
        if year:
            probe = await conn.execute(
                text(
                    "SELECT k.id FROM vehicle_kits k WHERE k.model_id = :mid AND k.year = :year LIMIT 1"
                ),
                {"mid": model_row["id"], "year": year},
            )
            if probe.mappings().first() is not None:
                kit_filter += " AND k.year = :year"
                bind["year"] = year
                info["year"] = year
            else:
                # Year not in the catalogue: the union over all years decides
                # (several PCD → ambiguous_car, never a guess).
                info["year_not_found"] = year

        kits = (
            (
                await conn.execute(
                    text(
                        f"SELECT k.bolt_count, k.pcd, k.dia FROM vehicle_kits k WHERE {kit_filter}"
                    ),
                    bind,
                )
            )
            .mappings()
            .all()
        )
        sizes = (
            (
                await conn.execute(
                    text(f"""
                        SELECT DISTINCT ds.width, ds.diameter, ds.et
                        FROM vehicle_disk_sizes ds
                        JOIN vehicle_kits k ON k.id = ds.kit_id
                        WHERE {kit_filter}
                    """),
                    bind,
                )
            )
            .mappings()
            .all()
        )
        info["found"] = True
        return CarFitData.from_rows(kits, sizes), info

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
            data = await self._get("/api/v1/orders/search", params={"phone": phone})
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

        data = await self._patch(f"/api/v1/orders/{order_id}/delivery", json_data=body)
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

    async def calculate_delivery(self, city: str, order_id: str = "") -> dict[str, Any]:
        """Calculate delivery cost.

        Maps to: GET /api/v1/delivery/calculate
        """
        params: dict[str, Any] = {"city": city}
        if order_id:
            params["order_id"] = order_id
        return await self._get("/api/v1/delivery/calculate", params=params)

    # --- Fitting Tool Handlers ---

    async def get_fitting_stations(self, city: str, **_: Any) -> dict[str, Any]:
        """Get fitting stations in a city.

        Maps to: GET /api/v1/fitting/stations?city=...
        """
        data = await self._get("/api/v1/fitting/stations", params={"city": city})
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

        data = await self._get(f"/api/v1/fitting/stations/{station_id}/slots", params=params)
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
            data = await self._patch(f"/api/v1/fitting/bookings/{booking_id}", json_data=body)
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

    # --- Vehicle tire size lookup ---

    async def get_vehicle_tire_sizes(
        self, brand: str = "", model: str = "", year: int = 0, **_: Any
    ) -> dict[str, Any]:
        """Look up factory tire sizes for a vehicle.

        Uses the vehicle tire size database (migration 014).
        Falls back gracefully if DB is unavailable.
        """
        if self._db_engine is None:
            return {"found": False, "message": "База авто тимчасово недоступна"}

        from sqlalchemy import text

        engine = self._db_engine

        async with engine.connect() as conn:
            # 1. Find brand (exact, then fuzzy)
            brand_row = await self._find_vehicle_brand(conn, brand)
            if brand_row is None:
                return {"found": False, "message": f"Марку '{brand}' не знайдено в базі"}

            # 2. Find model
            model_row = await self._find_vehicle_model(conn, brand_row["id"], model)
            if model_row is None:
                return {
                    "found": False,
                    "brand": brand_row["name"],
                    "message": f"Модель '{model}' не знайдено для {brand_row['name']}",
                }

            # 3. Get available years
            years_result = await conn.execute(
                text("""
                    SELECT DISTINCT k.year FROM vehicle_kits k
                    WHERE k.model_id = :mid ORDER BY k.year DESC
                """),
                {"mid": model_row["id"]},
            )
            years = [r[0] for r in years_result]

            # 4. Query tire sizes
            if year and year in years:
                selected_year = year
            elif year:
                # Year not in DB — use closest available
                selected_year = min(years, key=lambda y: abs(y - year)) if years else 0
            else:
                # No year specified — use most recent
                selected_year = years[0] if years else 0

            size_params: dict[str, Any] = {"mid": model_row["id"]}
            year_filter = ""
            if selected_year:
                year_filter = "AND k.year = :year"
                size_params["year"] = selected_year

            sizes_result = await conn.execute(
                text(f"""
                    SELECT DISTINCT ts.width, ts.height, ts.diameter, ts.type, ts.axle,
                           ts.kit_id, ts.axle_group
                    FROM vehicle_tire_sizes ts
                    JOIN vehicle_kits k ON ts.kit_id = k.id
                    WHERE k.model_id = :mid {year_filter}
                    ORDER BY ts.type, ts.width
                """),
                size_params,
            )
            rows = sizes_result.mappings().all()

        if not rows:
            return {
                "found": False,
                "brand": brand_row["name"],
                "model": model_row["name"],
                "message": "Розміри шин не знайдено",
            }

        stock_sizes: list[str] = []
        acceptable_sizes: list[str] = []
        for row in rows:
            size_str = _format_tire_size(row)
            target = stock_sizes if row["type"] == 1 else acceptable_sizes
            if size_str not in target:
                target.append(size_str)

        result: dict[str, Any] = {
            "found": True,
            "brand": brand_row["name"],
            "model": model_row["name"],
            "years": years[:10],
            "stock_sizes": stock_sizes,
        }
        staggered_pairs = _staggered_pairs(rows)
        if staggered_pairs:
            result["staggered_pairs"] = staggered_pairs
        if acceptable_sizes:
            result["acceptable_sizes"] = acceptable_sizes
            # Non-factory sizes: a specialist picks them, the bot never
            # offers one on its own (owner decision 2026-09-28).
            result["acceptable_sizes_policy"] = "specialist_only"
        if selected_year:
            result["selected_year"] = selected_year

        return result

    @staticmethod
    async def _find_vehicle_brand(conn: Any, name: str) -> Any:
        """Find vehicle brand: exact → aliases → pg_trgm fuzzy."""
        from sqlalchemy import text

        from src.agent.vehicle_alias_lookup import find_brand_by_alias

        # 1. Exact (case-insensitive)
        result = await conn.execute(
            text("SELECT id, name FROM vehicle_brands WHERE LOWER(name) = LOWER(:name)"),
            {"name": name},
        )
        row = result.mappings().first()
        if row:
            return row

        # 2. vehicle_aliases (Wave 8) — catches "Тойота" → Toyota, "Фольксваген" → Volkswagen
        alias_row = await find_brand_by_alias(conn, name)
        if alias_row:
            return alias_row

        # 3. Fuzzy via pg_trgm — last resort for typos not covered by aliases
        result = await conn.execute(
            text("""
                SELECT id, name, similarity(LOWER(name), LOWER(:name)) AS sim
                FROM vehicle_brands
                WHERE similarity(LOWER(name), LOWER(:name)) > 0.3
                ORDER BY sim DESC LIMIT 1
            """),
            {"name": name},
        )
        return result.mappings().first()

    @staticmethod
    async def _find_vehicle_model(conn: Any, brand_id: int, name: str) -> Any:
        """Find vehicle model within a brand.

        as heard (exact → alias) → normalized variants from
        ``vehicle_model_normalizer`` (exact → alias: «ГЛА», «джі ел ей»,
        «GLA 200» → ``gla``) → prefix fallback (``gla`` → ``GLA-Class``, the
        base model, never an AMG/Coupe sibling; ambiguous → None, no guess)
        → pg_trgm fuzzy on the name as heard.

        Same-name duplicates (two ``GLA-Class`` rows) resolve to the one with
        the most kits.
        """
        from sqlalchemy import text

        from src.agent.vehicle_alias_lookup import find_model_by_alias
        from src.agent.vehicle_model_normalizer import (
            model_query_variants,
            normalize_model_text,
            pick_prefix_model,
            prefix_like_pattern,
        )

        kits_order = (
            "(SELECT COUNT(*) FROM vehicle_kits k WHERE k.model_id = vehicle_models.id) DESC, id"
        )

        async def _exact_or_alias(key: str) -> Any:
            result = await conn.execute(
                text(f"""
                    SELECT id, name FROM vehicle_models
                    WHERE brand_id = :bid AND LOWER(name) = LOWER(:name)
                    ORDER BY {kits_order}
                """),
                {"bid": brand_id, "name": key},
            )
            row = result.mappings().first()
            if row:
                return row
            # vehicle_aliases (Wave 8) — catches "Дастер" → Duster within Renault
            return await find_model_by_alias(conn, brand_id, key)

        # 1. As heard: exact, then alias
        row = await _exact_or_alias(name)
        if row:
            return row

        # 2. Normalized variants: exact, then alias
        variants = model_query_variants(name)
        for key in variants:
            row = await _exact_or_alias(key)
            if row:
                return row

        # 3. Prefix fallback: "gle" → "GLE-Class" (base model, not "GLE AMG")
        as_heard = normalize_model_text(name)
        for key in dict.fromkeys([as_heard, *variants]):
            if not key:
                continue
            result = await conn.execute(
                text(f"""
                    SELECT id, name FROM vehicle_models
                    WHERE brand_id = :bid AND LOWER(name) LIKE :pattern ESCAPE '\\'
                    ORDER BY {kits_order}
                """),
                {"bid": brand_id, "pattern": prefix_like_pattern(key)},
            )
            picked, ambiguous = pick_prefix_model(key, result.mappings().all())
            if picked is not None:
                return picked
            if ambiguous:
                # Several siblings, no base: a wrong model is worse than asking.
                return None

        # 4. Fuzzy
        result = await conn.execute(
            text("""
                SELECT id, name, similarity(LOWER(name), LOWER(:name)) AS sim
                FROM vehicle_models
                WHERE brand_id = :bid AND similarity(LOWER(name), LOWER(:name)) > 0.3
                ORDER BY sim DESC LIMIT 1
            """),
            {"bid": brand_id, "name": name},
        )
        return result.mappings().first()

    # --- 1C / PostgreSQL / Redis helpers (MVP) ---

    @staticmethod
    def _pick_diverse_tires(rows: list[Any], target: int = 5) -> list[Any]:
        """Pick tires from different price segments for a balanced overview.

        Selects 1 budget + 1-2 mid-range + 1-2 premium, one per manufacturer,
        so the customer sees the full price range.
        """
        if not rows:
            return rows

        # Deduplicate by manufacturer (keep cheapest per brand)
        seen_brands: dict[str, Any] = {}
        for r in rows:
            brand = r["brand"]
            if brand not in seen_brands:
                seen_brands[brand] = r
        unique = list(seen_brands.values())

        if len(unique) <= target:
            return unique

        # Split into 3 price segments
        prices = sorted(r["price"] for r in unique if r["price"] > 0)
        if len(prices) < 3:
            return unique[:target]

        p33 = prices[len(prices) // 3]
        p66 = prices[2 * len(prices) // 3]

        budget = [r for r in unique if 0 < r["price"] <= p33]
        mid = [r for r in unique if p33 < r["price"] <= p66]
        premium = [r for r in unique if r["price"] > p66]

        # Pick from each segment
        result: list[Any] = []
        for segment, count in [(budget, 1), (mid, 2), (premium, 2)]:
            result.extend(segment[:count])

        # Fill remaining slots if a segment was too small
        remaining = [r for r in unique if r not in result]
        while len(result) < target and remaining:
            result.append(remaining.pop(0))

        return sorted(result, key=lambda r: r["price"])

    @staticmethod
    def _rank_tires(
        rows: list[Any],
        brand_priority: tuple[str, ...] | list[str] = (),
        recommend_count: int = RECOMMEND_COUNT_MAX,
    ) -> list[Any]:
        """Pick what the bot offers: ``recommend_count`` (2–3) in-stock tyres.

        1. One model — one variant (the first row of a model wins; the SQL
           orders by price, so it is the cheapest in-stock SKU).
        2. The network's ``brand_priority`` brands go first, one model each,
           in the network's order — only when in stock.
        3. The rest is spread over the price range (budget → premium), one
           model per brand.

        A brand the customer named is a SQL filter, so every row here is
        that brand already. Empty ``brand_priority`` → only step 3.
        """
        count = _clamp_recommend_count(recommend_count)

        seen_models: dict[tuple[str, str], Any] = {}
        for r in rows:
            if r["stock_quantity"] <= 0:
                continue
            key = (str(r["brand"]).strip().lower(), str(r["model"]).strip().lower())
            seen_models.setdefault(key, r)
        unique = list(seen_models.values())

        picked: list[Any] = []
        for brand in brand_priority or ():
            if len(picked) >= count:
                break
            wanted = str(brand).strip().lower()
            for r in unique:
                if str(r["brand"]).strip().lower() == wanted and r not in picked:
                    picked.append(r)
                    break

        # A named brand fills every slot with its models; otherwise one
        # model per brand keeps the offer diverse.
        brands = {str(r["brand"]).strip().lower() for r in unique}
        one_per_brand = len(brands) > 1
        taken = {str(r["brand"]).strip().lower() for r in picked}
        rest: list[Any] = []
        for r in unique:
            b = str(r["brand"]).strip().lower()
            if r in picked or (one_per_brand and b in taken):
                continue
            rest.append(r)
            if one_per_brand:
                taken.add(b)

        slots = count - len(picked)
        if slots > 0 and rest:
            rest.sort(key=lambda r: r["price"])
            if len(rest) <= slots:
                picked.extend(rest)
            elif slots == 1:
                picked.append(rest[len(rest) // 2])
            else:
                last = len(rest) - 1
                idx = sorted({round(i * last / (slots - 1)) for i in range(slots)})
                picked.extend(rest[i] for i in idx)

        return picked[:count]

    async def _query_tire_rows(
        self,
        network: str,
        params: dict[str, Any],
        brand_priority: tuple[str, ...] | list[str] = (),
    ) -> list[Any]:
        """Catalog rows for a tyre query — tyre types only, cheapest first.

        ``brand_priority`` brands sort ahead of the rest, so the LIMIT never
        cuts them off (they are rarely among the cheapest rows).
        """
        from sqlalchemy import text

        conditions = []
        bind_params: dict[str, Any] = {"network": network}
        conditions.append(_tire_types_sql(bind_params))

        if params.get("width"):
            conditions.append("p.width = :width")
            bind_params["width"] = int(params["width"])
        if params.get("profile"):
            conditions.append("p.profile = :profile")
            bind_params["profile"] = int(params["profile"])
        if params.get("diameter"):
            conditions.append("p.diameter = :diameter")
            bind_params["diameter"] = int(params["diameter"])
        if params.get("season"):
            # LLM sends English enum (summer/winter/all_season),
            # but DB may have Ukrainian values from 1C sync
            _season_to_db = {
                "summer": "літня",
                "winter": "зимова",
                "all_season": "всесезонна",
            }
            db_season = _season_to_db.get(params["season"], params["season"])
            conditions.append("m.seasonality IN (:season, :season_alt)")
            bind_params["season"] = params["season"]
            bind_params["season_alt"] = db_season
        if params.get("brand"):
            conditions.append("LOWER(m.manufacturer) = LOWER(:brand)")
            bind_params["brand"] = params["brand"]
        # Only a real bool filters; None / anything else = "not asked".
        studded = params.get("studded")
        if studded is True:
            conditions.append(_STUDDED_SQL)
        elif studded is False:
            conditions.append(f"NOT {_STUDDED_SQL}")
        runflat = params.get("runflat")
        if runflat is True:
            conditions.append(_RUNFLAT_SQL)
        elif runflat is False:
            conditions.append(f"NOT ({_RUNFLAT_SQL})")
        xl = params.get("xl")
        if xl is True:
            conditions.append(_XL_SQL)
        elif xl is False:
            conditions.append(f"NOT ({_XL_SQL})")
        commercial = params.get("commercial")
        if commercial is True:
            conditions.append("p.commercial IS TRUE")
        elif commercial is False:
            conditions.append("p.commercial IS NOT TRUE")
        # «А дешевше?» / «схожі за ціною» (sales): bounds computed by the
        # search_tires wrapper from the last offer the caller heard.
        if params.get("price_below") is not None:
            conditions.append("s.price > 0 AND s.price < :price_below")
            bind_params["price_below"] = float(params["price_below"])
        if params.get("price_from") is not None and params.get("price_to") is not None:
            conditions.append("s.price BETWEEN :price_from AND :price_to")
            bind_params["price_from"] = float(params["price_from"])
            bind_params["price_to"] = float(params["price_to"])
        exclude_ids = [str(i) for i in params.get("exclude_ids") or () if i]
        if exclude_ids:
            names = []
            for i, sku in enumerate(exclude_ids):
                bind_params[f"exclude_{i}"] = sku
                names.append(f":exclude_{i}")
            conditions.append(f"p.sku NOT IN ({', '.join(names)})")

        where_clause = " AND ".join(conditions)
        limit = int(params.get("_limit") or 50)

        order = "s.price ASC NULLS LAST"
        prio_names = []
        for i, brand in enumerate(brand_priority or ()):
            bind_params[f"prio_{i}"] = str(brand).strip().lower()
            prio_names.append(f":prio_{i}")
        if prio_names:
            order = (
                f"CASE WHEN LOWER(m.manufacturer) IN ({', '.join(prio_names)}) "
                f"THEN 0 ELSE 1 END, {order}"
            )

        query = text(f"""
            SELECT p.sku AS id, m.manufacturer AS brand, m.name AS model,
                   p.size, m.seasonality AS season,
                   COALESCE(s.price, 0) AS price,
                   COALESCE(s.stock_quantity, 0) AS stock_quantity,
                   {_RUNFLAT_COLUMN_SQL} AS runflat,
                   l.energy_class AS eu_fuel, l.wet_grip_class AS eu_wet,
                   l.noise_db AS eu_noise_db
            FROM tire_products p
            JOIN tire_models m ON p.model_id = m.id
            LEFT JOIN tire_stock s ON p.sku = s.sku AND s.trading_network = :network
            LEFT JOIN tire_eu_labels l ON l.sku = p.sku
            WHERE {where_clause}
            ORDER BY {order}
            LIMIT :result_limit
        """)
        bind_params["result_limit"] = limit

        async with self._db_engine.connect() as conn:
            result = await conn.execute(query, bind_params)
            return list(result.mappings().all())

    @staticmethod
    def _tire_item(row: Any) -> dict[str, Any]:
        item = {
            "id": row["id"],
            "brand": row["brand"],
            "model": row["model"],
            "size": row["size"],
            "season": row["season"],
            "price": row["price"],
            "in_stock": row["stock_quantity"] > 0,
        }
        if row.get("runflat") is True:
            item["runflat"] = True
        label = StoreClient._eu_label(row)
        if label:
            item["eu_label"] = label
        return item

    @staticmethod
    def _eu_label(row: Any) -> dict[str, Any]:
        """The EU label of a catalogue row (``tire_eu_labels``, LEFT JOIN).

        ``{"fuel": "C", "wet": "B", "noise_db": 71}`` — only the fields the
        label has; no label row (or a row without the columns) → ``{}``, so
        the item carries no ``eu_label`` key at all.
        """
        label: dict[str, Any] = {}
        for key, column in (("fuel", "eu_fuel"), ("wet", "eu_wet")):
            value = row.get(column)
            if value is not None and str(value).strip():
                label[key] = str(value).strip().upper()
        noise = row.get("eu_noise_db")
        if noise is not None:
            label["noise_db"] = int(noise)
        return label

    async def _search_tires_db(self, network: str = "", **params: Any) -> dict[str, Any]:
        """Search tires in PostgreSQL catalog (synced from 1C).

        ``recommend_count`` given (the network policy) → the offer ranking of
        ``_rank_tires``; absent → the legacy 5-card overview.
        """
        # Vehicle search is not supported via 1C catalog
        if any(k in params for k in ("vehicle_make", "vehicle_model", "vehicle_year")):
            return {
                "total": 0,
                "items": [],
                "message": "Для пошуку за авто спочатку виклич get_vehicle_tire_sizes, потім search_tires з розміром",
            }

        if not network:
            network = "ProKoleso"

        brand_priority = params.pop("brand_priority", None) or ()
        recommend_count = params.pop("recommend_count", None)

        if recommend_count is None:
            if params.get("brand"):
                params = {**params, "_limit": 5}
            rows = await self._query_tire_rows(network, params)
        else:
            # Ranking path: a wide window, so the price spread and the
            # network's brands are not cut to the 50 cheapest rows.
            params = {**params, "_limit": _RANKING_WINDOW}
            rows = await self._query_tire_rows(network, params, brand_priority)

        if recommend_count is not None:
            rows = self._rank_tires(rows, brand_priority, recommend_count)
        elif not params.get("brand") and len(rows) > 5:
            # Legacy overview: budget, mid-range and premium options
            rows = self._pick_diverse_tires(rows)

        items = [self._tire_item(row) for row in rows]
        return {
            "total": len(items),
            "items": items,
        }

    async def _search_tires_ladder(self, network: str = "", **params: Any) -> dict[str, Any]:
        """``_search_tires_db`` + the relaxation ladder.

        Nothing matches → drop a filter and mark the result, so the answer
        carries its own caveat instead of relying on the prompt:

        - brand + studded: studded of other brands (``relaxed: ["brand"]``),
          then the brand without studs, then anything;
        - studded: friction tyres, ``caveat_key = no_studded_offer_friction``;
        - brand: other brands, ``caveat_key = brand_unavailable_alternatives``;
        - xl: regular tyres of the size, ``caveat_key = xl_none_offer_regular``;
        - runflat: regular tyres of the size, ``caveat_key = runflat_none``.

        XL / RunFlat are dropped first (alone, then both), keeping brand and
        studs; ``commercial`` is never dropped (a passenger tyre on a van is
        not an alternative).

        A rear size (``rear_width/rear_profile/rear_diameter``) → staggered
        search: one model in both sizes.
        """
        rear = {
            "width": params.pop("rear_width", None),
            "profile": params.pop("rear_profile", None),
            "diameter": params.pop("rear_diameter", None),
        }
        has_rear = all(rear.values())

        async def run(p: dict[str, Any]) -> dict[str, Any]:
            if has_rear:
                return await self._search_staggered(network, p, rear)
            return await self._search_tires_db(network=network, **p)

        result = await run(dict(params))
        if result.get("items"):
            return result

        studded = params.get("studded") is True
        brand = bool(params.get("brand"))
        tech = [k for k in ("xl", "runflat") if params.get(k) is True]
        steps: list[list[str]] = [[k] for k in tech]
        if len(tech) > 1:
            steps.append(list(tech))
        if studded and brand:
            steps.append(["brand"])
        if studded:
            steps.append(["studded"])
        if brand:
            steps.append(["studded", "brand"] if studded else ["brand"])

        for drop in steps:
            relaxed_params = {k: v for k, v in params.items() if k not in drop}
            relaxed = await run(relaxed_params)
            if relaxed.get("items"):
                relaxed["relaxed"] = drop
                if "runflat" in drop:
                    relaxed["caveat_key"] = CAVEAT_RUNFLAT_NONE
                elif "xl" in drop:
                    relaxed["caveat_key"] = CAVEAT_XL_NONE
                elif "studded" in drop:
                    relaxed["caveat_key"] = CAVEAT_NO_STUDDED
                else:
                    relaxed["caveat_key"] = CAVEAT_BRAND_UNAVAILABLE
                return relaxed
        return result

    async def _search_staggered(
        self, network: str, params: dict[str, Any], rear: dict[str, Any]
    ) -> dict[str, Any]:
        """Staggered axles: one model offered in the front and the rear size."""
        brand_priority = params.pop("brand_priority", None) or ()
        recommend_count = params.pop("recommend_count", None)
        params = {**params, "_limit": _RANKING_WINDOW}
        front_rows = await self._query_tire_rows(network, params, brand_priority)
        # Price bounds are about the front tyre the caller heard, not the rear.
        rear_params = {k: v for k, v in params.items() if k not in PRICE_FILTER_KEYS}
        rear_rows = await self._query_tire_rows(network, {**rear_params, **rear}, brand_priority)

        def key(r: Any) -> tuple[str, str]:
            return (str(r["brand"]).strip().lower(), str(r["model"]).strip().lower())

        rear_by_model: dict[tuple[str, str], Any] = {}
        for r in rear_rows:
            if r["stock_quantity"] > 0:
                rear_by_model.setdefault(key(r), r)
        paired = [r for r in front_rows if key(r) in rear_by_model]
        ranked = self._rank_tires(
            paired, brand_priority, recommend_count if recommend_count is not None else 3
        )

        items = []
        for row in ranked:
            item = self._tire_item(row)
            rear_row = rear_by_model[key(row)]
            item["rear_id"] = rear_row["id"]
            item["rear_size"] = rear_row["size"]
            item["rear_price"] = rear_row["price"]
            items.append(item)
        return {"total": len(items), "items": items, "staggered": True}

    async def _find_tire_sku_by_brand(self, brand: str, network: str) -> str:
        """Cheapest tyre SKU of a brand (tyre types only); ``""`` if none."""
        from sqlalchemy import text

        bind_params: dict[str, Any] = {"brand": brand, "network": network}
        types_sql = _tire_types_sql(bind_params)
        query = text(f"""
            SELECT p.sku AS id
            FROM tire_products p
            JOIN tire_models m ON p.model_id = m.id
            LEFT JOIN tire_stock s ON p.sku = s.sku AND s.trading_network = :network
            WHERE LOWER(m.manufacturer) = LOWER(:brand) AND {types_sql}
            ORDER BY s.price ASC NULLS LAST
            LIMIT 1
        """)
        async with self._db_engine.connect() as conn:
            result = await conn.execute(query, bind_params)
            row = result.mappings().first()
        return str(row["id"]) if row else ""

    async def _check_availability_1c(
        self, product_id: str, query: str, network: str = ""
    ) -> dict[str, Any]:
        """Check availability via Redis cache → PostgreSQL fallback."""
        from sqlalchemy import text

        if not network:
            network = "ProKoleso"

        sku = product_id
        if not sku and query:
            # If query is numeric, treat as SKU (LLM sometimes puts article in query)
            if query.strip().isdigit():
                sku = query.strip()
            else:
                # Find the cheapest tyre of the named brand. Own lookup, not
                # search_tires: the relaxation ladder would answer with
                # another brand. Tyre types only — a wheel brand must not
                # come back as ``items[0]`` (FINDINGS I §4, D1).
                sku = await self._find_tire_sku_by_brand(query, network)
                if not sku:
                    return {"available": False, "message": "Товар не знайдено"}

        if not sku:
            return {"available": False, "message": "Потрібен ID товару або запит"}

        # 1C SKUs are zero-padded to 11 chars (e.g. "00000064314")
        if sku.isdigit() and len(sku) < 11:
            sku = sku.zfill(11)

        # 1) Try Redis cache first (fastest)
        stock_data = await self._get_stock_from_redis(sku, network=network)
        if stock_data is not None:
            return stock_data

        # 2) Fallback to PostgreSQL (last synced data)
        engine = self._db_engine
        async with engine.connect() as conn:
            result = await conn.execute(
                text("""
                    SELECT s.price, s.stock_quantity, s.country, s.year_issue,
                           s.trading_network
                    FROM tire_stock s
                    WHERE s.sku = :sku AND s.trading_network = :network
                    ORDER BY s.stock_quantity DESC
                    LIMIT 1
                """),
                {"sku": sku, "network": network},
            )
            row = result.mappings().first()

        if row is None:
            return {"available": False, "message": "Дані про наявність відсутні"}

        return {
            "available": row["stock_quantity"] > 0,
            "quantity": row["stock_quantity"],
            "price": row["price"],
            "country": row["country"],
            "year": row["year_issue"],
        }

    async def _get_stock_from_redis(self, sku: str, network: str = "") -> dict[str, Any] | None:
        """Try to get stock data from Redis cache for a specific network."""
        if self._redis is None:
            return None

        if not network:
            network = "ProKoleso"

        try:
            key = f"onec:stock:{network}"
            raw = await self._redis.hget(key, sku)
            if raw is not None:
                data = json.loads(raw)
                return {
                    "available": data["stock"] > 0,
                    "quantity": data["stock"],
                    "price": data["price"],
                    "country": data.get("country", ""),
                    "year": data.get("year_issue", ""),
                }
        except Exception:
            logger.warning("Redis stock lookup failed for sku=%s", sku, exc_info=True)

        return None

    # --- HTTP helpers ---

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
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
            result: dict[str, Any] = await self._breaker.call_async(
                self._request_with_retry,
                method,
                url,
                request_id,
                params=params,
                json_data=json_data,
                idempotency_key=idempotency_key,
            )
            return result
        except CircuitBreakerError as err:
            logger.error("Circuit breaker OPEN for Store API")
            raise StoreAPIError(503, "Сервіс тимчасово недоступний. Спробуйте пізніше.") from err

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
                    method,
                    url,
                    request_id,
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

            data: dict[str, Any] = await resp.json()
            return data

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
