"""A size that is not the car's is not searched (sales scope).

Call 78139ffb (Про Колесо, 2026-10-27): the bot read out the Tiguan's factory
sizes, the caller said «235/55 R18», STT wavered between 235 and 255 and
finalized «255 55 18». The bot searched 255/55 R18 and offered tyres that do
not fit. Call 9b3a975f: the caller named 255/50 R18 for the same Tiguan, the
bot searched it — against the owner's rule «нештатний розмір підбирає
спеціаліст», which the prompt states and the model did not keep.

`search_tires` is refused when the car's sizes are known and the size asked
is neither factory nor acceptable for it:

- exactly one factory size differs from it in one number (width, profile or
  diameter) — the shape of a misheard number: the bot asks about that size;
- otherwise, or once the caller confirmed the misheard-looking size — a
  non-stock size: the bot names the factory sizes and offers a specialist;
  a caller who insists is offered the manager. The search never runs, so
  there is nothing to loop on: every refusal ends in a question to the caller.

An acceptable size (the catalogue's «fits, not factory») is searched as
before: the caller has it on the car.
"""

from __future__ import annotations

import re
from typing import Any

_SIZE_RE = re.compile(r"(\d{3})\s*/\s*(\d{2})\s*Z?R?\s*(\d{2})", re.IGNORECASE)


def size_key(text: Any) -> tuple[int, int, int] | None:
    """``(width, profile, diameter)`` of a «235/55 R18»-style size, or ``None``."""
    m = _SIZE_RE.search(str(text or ""))
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def args_size_key(params: dict[str, Any]) -> tuple[int, int, int] | None:
    """The size of `search_tires` arguments (width/profile/diameter), or ``None``."""
    try:
        return int(params["width"]), int(params["profile"]), int(params["diameter"])
    except (KeyError, TypeError, ValueError):
        return None


def format_size(key: tuple[int, int, int]) -> str:
    return f"{key[0]}/{key[1]} R{key[2]}"


def vehicle_sizes(result: Any) -> tuple[list[str], list[str]]:
    """Factory and acceptable sizes of a found `get_vehicle_tire_sizes` result."""
    if not isinstance(result, dict) or result.get("found") is not True:
        return [], []

    def _sizes(name: str) -> list[str]:
        return [format_size(k) for k in map(size_key, result.get(name) or []) if k]

    return _sizes("stock_sizes"), _sizes("acceptable_sizes")


def near_factory_size(
    asked: tuple[int, int, int], stock: list[str], acceptable: list[str]
) -> str | None:
    """The one factory size ``asked`` is a misheard number of, or ``None``."""
    known = {size_key(s) for s in [*stock, *acceptable]}
    if asked in known:
        return None
    near = [
        s
        for s in stock
        if (k := size_key(s)) is not None
        and sum(a != b for a, b in zip(asked, k, strict=True)) == 1
    ]
    return near[0] if len(near) == 1 else None


def tire_size_refusal(
    params: dict[str, Any],
    *,
    stock: list[str],
    acceptable: list[str],
    refused: dict[str, int],
    caller_turns: int,
) -> dict[str, Any] | None:
    """The size guard of `search_tires`, or ``None`` to allow.

    ``refused`` maps a size already refused to the caller-turn count at that
    moment — the caller has spoken since when ``caller_turns`` is greater. The
    caller records each refusal (``refused[size] = caller_turns``).
    """
    asked = args_size_key(params)
    if asked is None or not stock:
        return None
    if asked in {size_key(s) for s in [*stock, *acceptable]}:
        return None
    asked_text = format_size(asked)
    answered = asked_text in refused and caller_turns > refused[asked_text]
    factory = near_factory_size(asked, stock, acceptable)
    if factory is not None and not answered:
        return {
            "error": True,
            "reason": "size_not_for_vehicle",
            "action_required": "confirm_size",
            "asked_size": asked_text,
            "factory_size": factory,
            "message": (
                f"Розміру {asked_text} немає серед розмірів цього авто, а заводський "
                f"{factory} відрізняється одним числом — схоже, розмір почули неточно. "
                f"Шини не шукай і не називай. Коротко перепитай клієнта, чи йдеться про "
                f"{factory}."
            ),
        }
    factory_list = ", ".join(stock)
    if answered:
        message = (
            f"Клієнт наполягає на нештатному розмірі {asked_text}. Шини в ньому не "
            f"шукай і не називай. Скажи: «Нештатний розмір підбирає спеціаліст» — і "
            f"запропонуй передати запит менеджеру (transfer_to_operator або "
            f"create_callback_request, якщо вони є серед твоїх інструментів)."
        )
    else:
        message = (
            f"Розмір {asked_text} не заводський для цього авто (заводські: "
            f"{factory_list}). Шини в ньому не шукай і не називай. Скажи клієнту, що "
            f"розмір не заводський, назви заводські і спитай, чи підібрати в "
            f"заводському, чи передати запит спеціалісту."
        )
    return {
        "error": True,
        "reason": "non_stock_size",
        "action_required": "offer_manager" if answered else "offer_factory_size",
        "asked_size": asked_text,
        "factory_sizes": list(stock),
        "message": message,
    }
