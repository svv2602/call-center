"""Compress tool results before saving to conversation history.

Strips fields that the LLM doesn't need for continuing the conversation,
reducing input token count by 20-30%.
"""

from __future__ import annotations

import json
from typing import Any

#: Result keys with this prefix are written to ``call_tool_calls`` (the router
#: audits the handler's raw result) and dropped before the model sees it.
AUDIT_KEY_PREFIX = "_audit_"
#: The 1C request number (``AI-<n>`` / ``AI-TEST-<n>``) of a created order
#: request: kept out of the model's view — it must not name it to the caller.
AUDIT_ORDER_NUMBER_KEY = "_audit_order_number"


def _compact(obj: Any) -> str:
    """Compact JSON serialization (no spaces, no ASCII escaping)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


def _compress_vehicle_sizes(result: dict[str, Any], *, sales_enabled: bool = False) -> str:
    """Keep found, brand, model, stock_sizes, acceptable_sizes; trim years.

    ``sales_enabled`` (the network's ``NetworkPolicy``): the factory front/rear
    pairs (``staggered_pairs``) stay — without them the LLM cannot pass
    ``rear_*`` to ``search_tires`` — and the non-factory sizes are replaced by
    ``acceptable_sizes_policy``: a specialist picks them, the bot never offers
    one (owner decision 2026-09-28). A list the model cannot see is a size it
    cannot offer; a prompt rule alone regresses. Off — byte-identical.
    """
    out: dict[str, Any] = {}
    keys: tuple[str, ...] = ("found", "brand", "model", "stock_sizes", "acceptable_sizes")
    if sales_enabled:
        keys = ("found", "brand", "model", "stock_sizes", "staggered_pairs")
    for key in keys:
        if key in result:
            out[key] = result[key]
    if sales_enabled and (result.get("acceptable_sizes") or result.get("acceptable_sizes_policy")):
        out["acceptable_sizes_policy"] = (
            "нештатні розміри підбирає спеціаліст — не пропонуй їх клієнту"
        )
    # Keep years only if <= 5 elements
    years = result.get("years")
    if years is not None and len(years) <= 5:
        out["years"] = years
    return _compact(out)


def _compress_order_status(result: dict[str, Any]) -> str:
    """Strip id and items_summary from each order."""
    if "orders" not in result:
        return _compact(result)
    orders = []
    for o in result["orders"]:
        orders.append(
            {
                k: v
                for k, v in o.items()
                if k in ("order_number", "status", "status_label", "total", "estimated_delivery")
            }
        )
    out = {k: v for k, v in result.items() if k != "orders"}
    out["orders"] = orders
    return _compact(out)


def _compress_order_draft(result: dict[str, Any]) -> str:
    """Keep order_id, order_number, status; slim down items."""
    out: dict[str, Any] = {}
    for key in ("order_id", "order_number", "status", "total"):
        if key in result:
            out[key] = result[key]
    if "items" in result:
        out["items"] = [
            {k: v for k, v in item.items() if k in ("name", "quantity", "price", "total")}
            for item in result["items"]
        ]
    return _compact(out)


def _compress_fitting_stations(result: dict[str, Any]) -> str:
    """Keep id, name, city, address, phone, district, landmarks per station."""
    if "stations" not in result:
        return _compact(result)
    stations = [
        {
            k: v
            for k, v in s.items()
            if k in ("id", "name", "city", "address", "phone", "district", "landmarks")
        }
        for s in result["stations"]
    ]
    out = {k: v for k, v in result.items() if k != "stations"}
    out["stations"] = stations
    return _compact(out)


def _compress_pickup_points(result: dict[str, Any]) -> str:
    """Keep id, address, city, district, landmarks per point."""
    if "points" not in result:
        return _compact(result)
    points = [
        {k: v for k, v in p.items() if k in ("id", "address", "city", "district", "landmarks")}
        for p in result["points"]
    ]
    out = {k: v for k, v in result.items() if k != "points"}
    out["points"] = points
    return _compact(out)


def _compress_knowledge(result: dict[str, Any]) -> str:
    """Keep title + truncated content per article."""
    if "articles" not in result:
        return _compact(result)
    articles = []
    for a in result["articles"]:
        entry: dict[str, Any] = {}
        if "title" in a:
            entry["title"] = a["title"]
        content = a.get("content", "")
        if len(content) > 300:
            content = content[:300] + "..."
        entry["content"] = content
        articles.append(entry)
    out = {k: v for k, v in result.items() if k != "articles"}
    out["articles"] = articles
    return _compact(out)


#: ``caveat_key`` of ``search_tires`` (``StoreClient._search_tires_ladder``) →
#: the sentence the caller hears before the variants. Spoken by the streaming
#: loop itself (`tire_caveat_phrase`), not left to the LLM.
_CAVEAT_NO_STUDDED = "no_studded_offer_friction"
_CAVEAT_BRAND_UNAVAILABLE = "brand_unavailable_alternatives"
_CAVEAT_XL_NONE = "xl_none_offer_regular"
_CAVEAT_RUNFLAT_NONE = "runflat_none"
_WARNING_RUNFLAT_REQUIRED = "runflat_required"
#: ``budget_source`` (``StoreClient.BUDGET_SOURCE_CALLER``): only a budget the
#: caller named is «ваш бюджет»; a price corridor of the last offer is not.
_BUDGET_SOURCE_CALLER = "caller"
_NO_CALLER_BUDGET_NOTE = "клієнт бюджет не називав — не кажи «в межах вашого бюджету»"
_NO_STUDDED_PHRASE = (
    "Шипованих у цьому розмірі зараз немає — можу запропонувати фрикційні (липучку)."
)


def tire_caveat_phrase(result: Any, args: dict[str, Any] | None = None) -> str | None:
    """The caveat for a relaxed ``search_tires`` answer, or ``None``.

    Only a result that carries both ``caveat_key`` and items has one: the
    ladder marks a result only when it found something after dropping a
    filter. An unknown key → ``None`` (nothing is invented for it). A
    ``warning: runflat_required`` adds its sentence after the caveat.
    """
    if not isinstance(result, dict) or not result.get("items"):
        return None
    parts = [p for p in (_caveat_phrase(result, args), _runflat_warning_phrase(result)) if p]
    return " ".join(parts) or None


def _caveat_phrase(result: dict[str, Any], args: dict[str, Any] | None) -> str | None:
    """The sentence of ``caveat_key`` (the ladder's relaxation), or ``None``."""
    key = result.get("caveat_key")
    relaxed = result.get("relaxed") or []
    brand = str((args or {}).get("brand") or "").strip()
    brand_missing = (
        f"{brand} зараз немає в наявності" if brand else "Цього бренду зараз немає в наявності"
    )
    if key == _CAVEAT_NO_STUDDED:
        # «Шипованих немає» answers only a studded request: said after a
        # search without ``studded``, it denies what the caller never asked.
        if (args or {}).get("studded") is not True:
            return None
        if "brand" in relaxed:
            return f"{brand_missing}. {_NO_STUDDED_PHRASE}"
        return _NO_STUDDED_PHRASE
    if key == _CAVEAT_BRAND_UNAVAILABLE:
        return f"{brand_missing}, ось альтернативи."
    size = _result_size(result)
    in_size = f"у розмірі {size}" if size else "у цьому розмірі"
    if key == _CAVEAT_XL_NONE:
        return f"Посилених шин (XL) {in_size} зараз немає — ось звичайні шини цього розміру."
    if key == _CAVEAT_RUNFLAT_NONE:
        return f"RunFlat {in_size} зараз немає в наявності — ось звичайні шини цього розміру."
    return None


def _result_size(result: dict[str, Any]) -> str:
    """The size of the first offered item (the caller's size), or ``""``."""
    first = result["items"][0]
    return str(first.get("size") or "").strip() if isinstance(first, dict) else ""


def _runflat_warning_phrase(result: dict[str, Any]) -> str | None:
    """The sentence for ``warning: runflat_required`` (``StoreClient.runflat_warning``)."""
    if result.get("warning") != _WARNING_RUNFLAT_REQUIRED:
        return None
    items = result.get("items") or []
    some_runflat = any(isinstance(i, dict) and i.get("runflat") is True for i in items)
    tail = "не всі ці варіанти — RunFlat" if some_runflat else "ці варіанти — не RunFlat"
    return f"Зверніть увагу: на ваш автомобіль з заводу ставлять шини RunFlat, а {tail}."


def _compress_search_tires(
    result: dict[str, Any],
    *,
    sales_enabled: bool = False,
    args: dict[str, Any] | None = None,
) -> str:
    """Limit to top 3 results, keep only essential fields.

    Drops id (SKU), season (already known from query context).

    ``sales_enabled``: the relaxation marks (``relaxed``, ``caveat_key``), the
    rear axle of a staggered pair (``rear_size``/``rear_price``, never
    ``rear_id``) and the caveat the loop has already spoken stay, so the model
    neither repeats the caveat nor presents a friction tyre as the studded one
    asked for. Off — byte-identical.
    """
    items = result.get("items", [])
    essential_keys: tuple[str, ...] = ("brand", "model", "size", "price", "in_stock")
    if sales_enabled:
        essential_keys = (*essential_keys, "rear_size", "rear_price", "runflat")
    compressed = [{k: v for k, v in item.items() if k in essential_keys} for item in items[:3]]
    out: dict[str, Any] = {"total": result.get("total", len(items))}
    out["items"] = compressed
    if sales_enabled:
        out["price_per"] = "1 шина"
        if result.get("staggered"):
            out["staggered"] = True
        if result.get("relaxed"):
            out["relaxed"] = list(result["relaxed"])
        if result.get("caveat_key"):
            out["caveat_key"] = result["caveat_key"]
        if result.get("warning"):
            out["warning"] = result["warning"]
        if result.get("price_mode"):
            out["price_mode"] = result["price_mode"]
            if result.get("price_corridor_widened"):
                out["price_corridor_widened"] = True
            if result.get("message"):
                out["message"] = result["message"]
        if result.get("budget_source"):
            out["budget_source"] = result["budget_source"]
        if result.get("price_mode") and result.get("budget_source") != _BUDGET_SOURCE_CALLER:
            out["budget_note"] = _NO_CALLER_BUDGET_NOTE
        phrase = tire_caveat_phrase(result, args)
        if phrase:
            out["caveat_already_said"] = (
                f"«{phrase}» — вже сказано клієнту, не повторюй; одразу назви варіанти"
            )
    return _compact(out)


#: The sentence for a car the catalogue has no wheel data on (every offered
#: wheel ``cannot_confirm``, or ``vehicle.found`` false).
DISK_NO_CAR_DATA_PHRASE = (
    "По цьому авто в мене немає даних, тож сумісність дисків не можу підтвердити."
)


def _disk_fit(item: Any) -> dict[str, Any] | None:
    fit = item.get("fit") if isinstance(item, dict) else None
    return fit if isinstance(fit, dict) else None


def disk_caveat_phrase(result: Any) -> str | None:
    """The compatibility verdict of a ``search_disks`` answer, or ``None``.

    Spoken by the loop, as ``tire_caveat_phrase`` is: the verdict of each
    offered wheel is ``fit.text`` from the result (``disk_fitment``), never
    composed here. No car in the call (no ``vehicle``) or no wheel with a
    ``fit`` → ``None``; ``ambiguous_car`` → its question once; a car the
    catalogue has no data on → «не можу підтвердити».
    """
    from src.agent.disk_fitment import AMBIGUOUS_CAR, CANNOT_CONFIRM, VERDICT_TEXT_UK

    if not isinstance(result, dict) or not isinstance(result.get("vehicle"), dict):
        return None
    vehicle = result["vehicle"]
    if vehicle.get("status") == AMBIGUOUS_CAR:
        text = VERDICT_TEXT_UK[AMBIGUOUS_CAR]
        return f"{text[:1].upper()}{text[1:]}."
    items = [i for i in (result.get("items") or []) if _disk_fit(i) is not None]
    if not items:
        return None
    if vehicle.get("found") is False or all(
        _disk_fit(i).get("status") == CANNOT_CONFIRM  # type: ignore[union-attr]
        for i in items
    ):
        return DISK_NO_CAR_DATA_PHRASE
    parts: list[str] = []
    for item in items:
        text = str(_disk_fit(item).get("text") or "").strip()  # type: ignore[union-attr]
        name = " ".join(str(item.get(k) or "").strip() for k in ("brand", "model") if item.get(k))
        if text and name:
            parts.append(f"{name} — {text}")
    if not parts:
        return None
    return "Щодо сумісності з вашим авто: " + "; ".join(parts) + "."


def _compress_search_disks(result: dict[str, Any]) -> str:
    """Sales only: the result as is, plus the verdict the loop has already spoken."""
    out = dict(result)
    phrase = disk_caveat_phrase(result)
    if phrase:
        out["caveat_already_said"] = (
            f"«{phrase}» — вже сказано клієнту, не повторюй і не кажи від себе, що диски підходять"
        )
    return _compact(out)


def _compress_check_availability(result: dict[str, Any]) -> str:
    """Keep availability essentials, trim warehouses to first 3."""
    essential_keys = ("available", "price", "stock_quantity")
    out = {k: v for k, v in result.items() if k in essential_keys}
    warehouses = result.get("warehouses")
    if warehouses:
        out["warehouses"] = warehouses[:3]
    return _compact(out)


def _compress_fitting_slots(result: dict[str, Any]) -> str:
    """Keep date, time, available per slot; drop internal IDs.

    Handles both formats:
    - Legacy: list of dicts (with date/time/available/id) → strip to essentials
    - Current: list of bare "HH:MM" strings (as returned by _get_fitting_slots
      in main.py) → pass through unchanged. Crashed the pipeline with
      AttributeError("'str' object has no attribute 'items'") before this
      guard (anchor: call f7555aac 2026-08-17 — bot died right after tool
      returned, was misdiagnosed as SIP RST for hours).
    """
    slots = result.get("slots", [])
    compressed: list[Any]
    if slots and isinstance(slots[0], dict):
        compressed = [
            {k: v for k, v in s.items() if k in ("date", "time", "available")}
            for s in slots
        ]
    else:
        compressed = list(slots)  # bare strings, pass through
    out = {k: v for k, v in result.items() if k != "slots"}
    out["slots"] = compressed
    return _compact(out)


_COMPRESSORS: dict[str, Any] = {
    "get_vehicle_tire_sizes": _compress_vehicle_sizes,
    "get_order_status": _compress_order_status,
    "create_order_draft": _compress_order_draft,
    "get_fitting_stations": _compress_fitting_stations,
    "get_pickup_points": _compress_pickup_points,
    "search_knowledge_base": _compress_knowledge,
    "search_tires": _compress_search_tires,
    "check_availability": _compress_check_availability,
    "get_fitting_slots": _compress_fitting_slots,
}


def compress_tool_result(
    tool_name: str,
    result: Any,
    *,
    sales_enabled: bool = False,
    args: dict[str, Any] | None = None,
) -> str:
    """Compress a tool result for LLM history.

    If the tool has a registered compressor and the result is a dict,
    applies field stripping.  Otherwise falls back to ``str(result)``.

    ``sales_enabled`` (``NetworkPolicy.sales_enabled``) keeps what the tyre
    consultation needs from ``search_tires`` / ``get_vehicle_tire_sizes``;
    ``args`` are the tool call's arguments (the brand of a caveat). Off —
    byte-identical to the fitting-only output.
    """
    if not isinstance(result, dict):
        return str(result)
    if any(k.startswith(AUDIT_KEY_PREFIX) for k in result):
        # Audit-only fields (the 1C request number) never reach the model.
        result = {k: v for k, v in result.items() if not k.startswith(AUDIT_KEY_PREFIX)}

    if sales_enabled and tool_name == "search_tires":
        return _compress_search_tires(result, sales_enabled=True, args=args)
    if sales_enabled and tool_name == "get_vehicle_tire_sizes":
        return _compress_vehicle_sizes(result, sales_enabled=True)
    if sales_enabled and tool_name == "search_disks":
        return _compress_search_disks(result)

    compressor = _COMPRESSORS.get(tool_name)
    if compressor is not None:
        return compressor(result)

    return _compact(result)
