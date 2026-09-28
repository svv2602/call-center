"""The tyre search the code makes itself when the caller's request is complete.

Goldset №5 (2026-09-28): after «шиповані, бренд будь-який, без побажань» the
model called nothing in five runs out of five (it answered from the previous,
studless search), and after «літні 205/55 R16, бюджет до трьох тисяч за шину»
it asked for the car instead of searching. The prompt already says «Розмір +
сезон відомі (зимові — і шипи) → одразу search_tires у цьому ж ході»; a prompt
rule regresses under attention dilution, so the search is made by code.

The decision is made BEFORE the first LLM round of the turn, on the parsed
request (`merge_tire_query`), in both loops. The voice loop speaks the LLM's
words while they are generated, so a gate after the round would come too late:
the caller would hear «уточніть авто» and then the results. A complete request
leaves nothing to ask, so the model loses nothing by seeing the result first —
and saves the round it would have spent calling the tool.

The call goes through the same ``ToolRouter.execute`` as the model's own (the
same audit row, the same guards of the ``search_tires`` handler) and lands in
the history as an ordinary ``tool_use`` / ``tool_result`` pair, which every
provider's converter already understands.

Loop-breakers — a forced call must tell a new request from a repeat:

- once per turn (`ForcedTireSearch`);
- the caller's utterance of this turn must itself say something about the
  tyres (size, season, studs, brand, RunFlat/XL/C) — a delivery question asked
  while a complete request sits unsearched is not a search request;
- not when the arguments are already covered by the last ``search_tires`` of
  the history (the same request was searched — «уже шукали»);
- not on a fitting request (``_FITTING_REQUEST_RE``) or a wheel request.

Sales scope only: with ``sales_enabled`` off nothing is armed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from src.agent.disk_intent import has_disk_intent
from src.agent.parsers.tire_query import parse_tire_size

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

SEARCH_TOOL = "search_tires"

#: ``tire_query["nail"]`` → ``search_tires(studded=…)``. «Під шип» tyres are
#: sold without studs (`StoreClient._STUDDED_SQL`), so they are not studded.
_NAIL_STUDDED: dict[str, bool] = {"studded": True, "studless": False, "studdable": False}
#: Seasons ``search_tires`` takes; ``any`` («без різниці») searches without one.
_SEASONS = frozenset({"summer", "winter", "all_season"})
#: ``tire_query["tech"]`` values that are ``search_tires`` boolean arguments.
_TECH_FLAGS = frozenset({"runflat", "xl", "commercial"})
#: What an utterance must say (in ``merge_tire_query`` terms) to be a request.
_REQUEST_KEYS = ("sizes", "rear_size", "season", "nail", "brands", "tech")


def _size(text: Any) -> tuple[int, int, int] | None:
    sizes = [s for s in parse_tire_size(str(text or "")) or [] if s.is_full]
    if len(sizes) != 1:
        return None
    s = sizes[0]
    return int(s.width or 0), int(s.aspect or 0), s.diameter


def search_args_from_query(query: dict[str, Any] | None) -> dict[str, Any] | None:
    """``search_tires`` arguments of the caller's request, or ``None`` if incomplete.

    Complete = one full front size + a season + (winter) the studs. A season
    ``any`` is complete and searches without one. Brand only when exactly one
    was named; RunFlat/XL/C only as requirements (``True``).
    """
    if not query:
        return None
    sizes = query.get("sizes") or []
    if len(sizes) != 1:
        return None
    front = _size(sizes[0])
    if front is None:
        return None
    season = str(query.get("season") or "")
    if season not in _SEASONS and season != "any":
        return None
    nail = query.get("nail")
    if season == "winter" and nail not in _NAIL_STUDDED:
        return None
    args: dict[str, Any] = {"width": front[0], "profile": front[1], "diameter": front[2]}
    if season in _SEASONS:
        args["season"] = season
    if nail in _NAIL_STUDDED:
        args["studded"] = _NAIL_STUDDED[nail]
    rear = _size(query.get("rear_size")) if query.get("rear_size") else None
    if rear is not None:
        args["rear_width"], args["rear_profile"], args["rear_diameter"] = rear
    brands = query.get("brands") or []
    if len(brands) == 1:
        args["brand"] = str(brands[0])
    for flag in query.get("tech") or []:
        if flag in _TECH_FLAGS:
            args[str(flag)] = True
    return args


def _norm(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v.isdigit():
            return int(v)
        return {"true": True, "false": False}.get(v, v)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def last_search_args(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The arguments of the last ``search_tires`` call in the history, or ``None``."""
    for msg in reversed(history):
        if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
            continue
        for block in reversed(msg["content"]):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == SEARCH_TOOL
            ):
                args = block.get("input")
                return args if isinstance(args, dict) else {}
    return None


def already_searched(args: dict[str, Any], history: list[dict[str, Any]]) -> bool:
    """True when the last ``search_tires`` of the history already covers ``args``.

    Covers = every argument of the request has the same value there (the
    model may have added more, e.g. a brand it heard). Only the last search
    counts: the caller who went back to an older request is asking anew.
    """
    last = last_search_args(history)
    if last is None:
        return False
    return all(_norm(last.get(k)) == _norm(v) for k, v in args.items())


def last_assistant_text(history: list[dict[str, Any]]) -> str:
    """The bot's latest spoken text in the history (tool blocks skipped)."""
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = " ".join(
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
            if text:
                return text
    return ""


def turn_names_the_tyres(user_text: str, last_bot_text: str = "") -> bool:
    """True when this utterance itself says something about the tyres wanted."""
    from src.core.pipeline import _FITTING_REQUEST_RE, merge_tire_query

    if not user_text or _FITTING_REQUEST_RE.search(user_text.lower()):
        return False
    if has_disk_intent(user_text):
        return False
    said = merge_tire_query({}, user_text, last_bot_text=last_bot_text, consult_started=True)
    return any(said.get(k) for k in _REQUEST_KEYS)


def accumulate_query(
    query: dict[str, Any],
    user_text: str,
    history: list[dict[str, Any]],
    tools_called: Iterable[str],
) -> dict[str, Any]:
    """The text path's ``session.tire_query``: the live pipeline's own merge."""
    from src.core.pipeline import merge_tire_query

    return merge_tire_query(
        query,
        user_text,
        last_bot_text=last_assistant_text(history),
        consult_started=bool({SEARCH_TOOL, "get_vehicle_tire_sizes"} & set(tools_called)),
    )


class ForcedTireSearch:
    """Per-turn state: the code's own ``search_tires``, at most once per turn."""

    def __init__(self, *, sales_enabled: bool, tools: Iterable[dict[str, Any]]) -> None:
        self._armed = sales_enabled and any(t.get("name") == SEARCH_TOOL for t in tools)
        self._fired = False

    def plan(
        self,
        query: dict[str, Any] | None,
        user_text: str,
        history: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """The arguments to search with now, or ``None``. Call before round 1."""
        if not self._armed or self._fired:
            return None
        args = search_args_from_query(query)
        if args is None:
            return None
        if not turn_names_the_tyres(user_text, last_assistant_text(history)):
            return None
        if already_searched(args, history):
            return None
        self._fired = True
        logger.warning(
            "forced_tire_search args=%s last_customer_text=%r",
            json.dumps(args, ensure_ascii=False, sort_keys=True),
            user_text[:120],
        )
        return args


async def run_forced_search(
    args: dict[str, Any],
    execute: Callable[[str, dict[str, Any]], Awaitable[Any]],
    *,
    timeout: float,
) -> Any:
    """Run ``search_tires`` through ``execute`` (``ToolRouter.execute``)."""
    try:
        return await asyncio.wait_for(execute(SEARCH_TOOL, dict(args)), timeout=timeout)
    except TimeoutError:
        logger.error("Forced %s timed out after %ss", SEARCH_TOOL, timeout)
        return {"error": "Сервіс тимчасово не відповідає, спробуйте ще раз"}


def forced_search_messages(
    args: dict[str, Any], content: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The history pair of the forced call: assistant ``tool_use`` + user ``tool_result``."""
    tool_id = f"toolu_forced_{uuid.uuid4().hex[:16]}"
    return (
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": SEARCH_TOOL, "input": dict(args)}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
        },
    )
