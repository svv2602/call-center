"""Compress tool results before saving to conversation history.

Strips fields that the LLM doesn't need for continuing the conversation,
reducing input token count by 20-30%.
"""

from __future__ import annotations

from typing import Any


def _compress_vehicle_sizes(result: dict[str, Any]) -> str:
    """Keep found, brand, model, stock_sizes, acceptable_sizes; trim years."""
    out: dict[str, Any] = {}
    for key in ("found", "brand", "model", "stock_sizes", "acceptable_sizes"):
        if key in result:
            out[key] = result[key]
    # Keep years only if <= 5 elements
    years = result.get("years")
    if years is not None and len(years) <= 5:
        out["years"] = years
    return str(out)


def _compress_order_status(result: dict[str, Any]) -> str:
    """Strip id and items_summary from each order."""
    if "orders" not in result:
        return str(result)
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
    return str(out)


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
    return str(out)


def _compress_fitting_stations(result: dict[str, Any]) -> str:
    """Keep id, name, address, working_hours per station."""
    if "stations" not in result:
        return str(result)
    stations = [
        {k: v for k, v in s.items() if k in ("id", "name", "address", "working_hours")}
        for s in result["stations"]
    ]
    out = {k: v for k, v in result.items() if k != "stations"}
    out["stations"] = stations
    return str(out)


def _compress_pickup_points(result: dict[str, Any]) -> str:
    """Keep id, address, city per point."""
    if "points" not in result:
        return str(result)
    points = [
        {k: v for k, v in p.items() if k in ("id", "address", "city")} for p in result["points"]
    ]
    out = {k: v for k, v in result.items() if k != "points"}
    out["points"] = points
    return str(out)


def _compress_knowledge(result: dict[str, Any]) -> str:
    """Keep title + truncated content per article."""
    if "articles" not in result:
        return str(result)
    articles = []
    for a in result["articles"]:
        entry: dict[str, Any] = {}
        if "title" in a:
            entry["title"] = a["title"]
        content = a.get("content", "")
        if len(content) > 500:
            content = content[:500] + "..."
        entry["content"] = content
        articles.append(entry)
    out = {k: v for k, v in result.items() if k != "articles"}
    out["articles"] = articles
    return str(out)


def _compress_search_tires(result: dict[str, Any]) -> str:
    """Limit to top 3 results, keep only essential fields.

    Drops id (SKU), season (already known from query context).
    """
    items = result.get("items", [])
    essential_keys = ("brand", "model", "size", "price", "in_stock")
    compressed = [{k: v for k, v in item.items() if k in essential_keys} for item in items[:3]]
    out: dict[str, Any] = {"total": result.get("total", len(items))}
    out["items"] = compressed
    return str(out)


def _compress_check_availability(result: dict[str, Any]) -> str:
    """Keep availability essentials, trim warehouses to first 3."""
    essential_keys = ("available", "price", "stock_quantity")
    out = {k: v for k, v in result.items() if k in essential_keys}
    warehouses = result.get("warehouses")
    if warehouses:
        out["warehouses"] = warehouses[:3]
    return str(out)


def _compress_fitting_slots(result: dict[str, Any]) -> str:
    """Keep date, time, available per slot; drop internal IDs."""
    slots = result.get("slots", [])
    compressed = [{k: v for k, v in s.items() if k in ("date", "time", "available")} for s in slots]
    out = {k: v for k, v in result.items() if k != "slots"}
    out["slots"] = compressed
    return str(out)


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


def compress_tool_result(tool_name: str, result: Any) -> str:
    """Compress a tool result for LLM history.

    If the tool has a registered compressor and the result is a dict,
    applies field stripping.  Otherwise falls back to ``str(result)``.
    """
    if not isinstance(result, dict):
        return str(result)

    compressor = _COMPRESSORS.get(tool_name)
    if compressor is not None:
        return compressor(result)

    return str(result)
