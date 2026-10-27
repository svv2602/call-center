"""Nova Poshta delivery quote for a sales call — pure helpers.

The ``get_delivery_cost`` tool (``src/main.py``) answers «скільки доставка» with
an approximate Nova Poshta sum. Everything the sum depends on comes from the
call, never from the model's arguments: the price and rim size of each item
from the search results the caller heard (``offered_entry``), the declared
value as Σ price × qty (owner: «Объявленная стоимость = сумма заказа»). The
model passes only the city, the product ids with their counts, and whether the
caller asked about a courier.

The phrase follows the owner's words: «орієнтовно 810 грн, при накладеному
платежі ще додатково 2% від суми замовлення»; a courier surcharge only when
the caller asked about delivery to the door. A free-delivery promotion
covering every item answers with the promotion's own text and Nova Poshta is
not asked; a promotion covering only some items → the neutral «уточнить
менеджер». Any failure → the old «за тарифами перевізника» phrase.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

from src.agent.promotions import GRANT_FREE_DELIVERY

if TYPE_CHECKING:
    from src.agent.network_policy import NetworkPolicy
    from src.agent.promotions import PromoOverrides
    from src.integrations.novaposhta import Quote

#: The tool name — canonical list in ``doc/development/00-overview.md``.
DELIVERY_COST_TOOL = "get_delivery_cost"

#: `status` of every tool result and `outcome` of `callcenter_delivery_quote_total`.
#: ToolRouter logs every returned dict as success=t, so the result carries its
#: own marker.
STATUS_QUOTED = "quoted"
STATUS_NEED_MORE = "need_more"
STATUS_PROMO_FREE = "promo_free"
STATUS_PROMO_MIXED = "promo_mixed"
STATUS_API_FAILED = "api_failed"

#: Spoken when only some items fall under a free-delivery promotion (same
#: wording as the network-claim guard's neutral replacement).
PROMO_MIXED_PHRASE = "Умови доставки для цих шин уточнить менеджер."

COD_FEE_PHRASE = "При накладеному платежі — ще додатково 2% від суми замовлення."

NEED_MORE_HINT = "Суму назву, коли оберемо шини й місто."

#: A rim size in a tyre size string: «205/55 R16», «225/45 ZR17», «215/75 R17.5»,
#: «195/70 R15C».
_RIM_RE = re.compile(r"R\s*(\d{2}(?:[.,]5)?)(?![\d.,])", re.IGNORECASE)

#: Upper bound of a sane count of one product in a quote.
MAX_QTY = 50


def quote_available(policy: NetworkPolicy | None) -> bool:
    """May this call quote delivery: the network flag is valid AND its key is set.

    Default-deny: no policy, flag off, sales off, or an empty env variable →
    ``False``. The key itself is never returned or logged.
    """
    if policy is None or not policy.delivery_quote_enabled:
        return False
    key_env = policy.delivery_quote_key_env
    return bool(key_env and os.environ.get(key_env, "").strip())


def tariff_phrase(policy: NetworkPolicy | None) -> str:
    """The old answer: «Доставка — за тарифами перевізника <carriers>.»"""
    carriers = ", ".join(policy.delivery_carriers) if policy is not None else ""
    return f"Доставка — за тарифами перевізника{f' {carriers}' if carriers else ''}."


def tire_rim(size: Any) -> tuple[float, bool] | None:
    """``(diameter, truck)`` from a tyre size string; ``.5`` rims are truck sizes."""
    if not isinstance(size, str):
        return None
    match = _RIM_RE.search(size)
    if match is None:
        return None
    value = float(match.group(1).replace(",", "."))
    return value, value != int(value)


def positive_price(value: Any) -> float | None:
    """A price > 0 as float, else ``None``."""
    if isinstance(value, bool):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def offered_entry(item: Any, kind: str) -> tuple[str, dict[str, Any]] | None:
    """``(product_id, {price, diameter, truck, kind, brand})`` of one search item.

    Tyres carry the rim only inside ``size``; wheels carry ``diameter``. An
    item without an id, a price or a rim size is not quotable → ``None``.
    """
    if not isinstance(item, dict):
        return None
    product_id = str(item.get("id") or "").strip()
    price = positive_price(item.get("price"))
    if not product_id or price is None:
        return None
    if kind == "tire":
        rim = tire_rim(item.get("size"))
    elif kind == "wheel":
        diameter = positive_price(item.get("diameter"))
        rim = (diameter, diameter != int(diameter)) if diameter is not None else None
    else:
        return None
    if rim is None:
        return None
    return product_id, {
        "price": price,
        "diameter": rim[0],
        "truck": rim[1],
        "kind": kind,
        "brand": str(item.get("brand") or "").strip(),
    }


def remember_offered(offered: dict[str, dict[str, Any]], result: Any, kind: str) -> None:
    """Add every quotable item of a search result to the call's registry."""
    if not isinstance(result, dict):
        return
    for item in result.get("items") or ():
        entry = offered_entry(item, kind)
        if entry is not None:
            offered[entry[0]] = entry[1]


def resolve_items(
    items: Any, offered: dict[str, dict[str, Any]]
) -> list[tuple[dict[str, Any], int]] | None:
    """``[(offered entry, qty)]`` — every item must be one this call offered.

    Default-deny: an empty list, a malformed item, a count outside 1..MAX_QTY
    or a product id not in ``offered`` → ``None`` (nothing is quoted).
    """
    if not isinstance(items, list) or not items:
        return None
    out: list[tuple[dict[str, Any], int]] = []
    for raw in items:
        if not isinstance(raw, dict):
            return None
        product_id = str(raw.get("product_id") or "").strip()
        qty = raw.get("quantity")
        if isinstance(qty, str) and qty.strip().isdigit():
            qty = int(qty.strip())
        if isinstance(qty, bool) or not isinstance(qty, int) or not 1 <= qty <= MAX_QTY:
            return None
        entry = offered.get(product_id)
        if entry is None:
            return None
        out.append((entry, qty))
    return out


def declared_value(lines: list[tuple[dict[str, Any], int]]) -> int:
    """Σ price × qty in whole hryvnias — the order sum (R05)."""
    return round(sum(entry["price"] * qty for entry, qty in lines))


def cargo_lines(lines: list[tuple[dict[str, Any], int]]) -> list[tuple[str, float, int, bool]]:
    """``CargoLine`` tuples ``(kind, diameter, qty, truck)`` for ``NovaPoshtaClient.quote``."""
    return [(entry["kind"], entry["diameter"], qty, entry["truck"]) for entry, qty in lines]


def _brand_covered(brand: str, scope: frozenset[str]) -> bool:
    if not scope:
        return True
    low = brand.strip().lower()
    return bool(low) and low in scope


def free_delivery_cover(
    lines: list[tuple[dict[str, Any], int]], promos: PromoOverrides | None
) -> tuple[int, str | None]:
    """How many items a live free-delivery promotion covers, and its text.

    The text is the ``bot_text`` of the first grant covering an item — the
    promotion's own words, never a composed one.
    """
    if promos is None:
        return 0, None
    grants = [g for g in promos.grants if g.condition == GRANT_FREE_DELIVERY]
    covered = 0
    text: str | None = None
    for entry, _qty in lines:
        grant = next((g for g in grants if _brand_covered(entry["brand"], g.brands)), None)
        if grant is not None:
            covered += 1
            text = text or grant.bot_text
    return covered, text


def _uah(value: float) -> int:
    return int(value + 0.5) if value >= 0 else 0


def quote_phrase(quote: Quote, courier: bool) -> str:
    """The owner's wording (R04, R08, R09) — whole hryvnias, always «орієнтовно»."""
    phrase = f"Доставка Новою поштою — орієнтовно {_uah(quote.cost)} грн"
    surcharge = quote.door_surcharge
    if courier and surcharge is not None:
        extra = _uah(surcharge)
        phrase += (
            f"; кур'єром до дверей — ще {extra} грн"
            if extra > 0
            else "; кур'єром до дверей — без доплати"
        )
    return f"{phrase}. {COD_FEE_PHRASE}"


def quoted_sums(quote: Quote, courier: bool) -> list[int]:
    """The sums the caller hears — for the speech guard (``CallSession.delivery_quotes``)."""
    sums = [_uah(quote.cost)]
    if courier and quote.door_surcharge is not None and _uah(quote.door_surcharge) > 0:
        sums.append(_uah(quote.door_surcharge))
    return sums
