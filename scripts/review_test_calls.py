"""Review acceptance test calls: transcript + tool calls + order requests, as markdown.

Read-only: every query runs in a ``SET TRANSACTION READ ONLY`` transaction and
the script has no INSERT/UPDATE/DELETE.

For each call in the window (``calls.started_at``), optionally narrowed to the
testers' numbers, one network or one call id, it prints a header (time,
network, masked number, duration, transfer and its reason), then the
chronology — the caller's and the bot's lines (``call_turns.content``) merged
with the tool calls (``call_tool_calls``) by ``created_at`` — and at the end
the order requests (``submit_order_request`` / ``confirm_order``).

Traps this script is built around:

- ``call_tool_calls.turn_number`` is the ordinal of the tool call, not of a
  line — the only join to the transcript is ``created_at``. A caller's line is
  written when the bot's answer is logged, so it can land a moment *after* the
  tool call it caused: the chronology is approximate within a turn.
- ``call_tool_calls.success`` means only «did not raise»: guard refusals are
  ``success=true`` with ``blocked`` / ``error`` / ``action_required`` /
  ``status=error|request_failed|missing_field`` in ``tool_result`` — they are
  flagged from the result, not from ``success``.
- The 1C request number (``AI-TEST-<n>``) is not in the DB — it stays out of the
  LLM-visible result and is only logged (``Order request AI-TEST-<n> created in
  1C for call <uuid>``). The report prints the grep for it.
- ``call_turns`` is post-``_strip_filler``: filler words the caller heard are
  not in the transcript.
- All three tables are partitioned by month — every query filters by time.

Usage (inside the container on prod, where ``DATABASE__URL`` is set):
    docker exec call-center-call-processor-1 python -m scripts.review_test_calls \\
        --since 2026-09-29T06:00 --callers 0671234567,0501234567
    ... --tenant prokoleso
    ... --call-id 26c5ebc3-0ee8-4300-9d60-d02525c78b5f --since 2026-09-25
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.agent.network_policy import mask_phone, phone_key

#: Tools whose result is an order request handed to a manager.
ORDER_TOOLS: frozenset[str] = frozenset({"submit_order_request", "confirm_order"})

#: ``tool_result.status`` values that are a refusal / failure, not an answer.
FAIL_STATUSES: frozenset[str] = frozenset({"error", "request_failed", "missing_field"})

#: Turns and tool calls of a call are searched up to this long after ``--until``
#: (a call that started inside the window may run past it).
CALL_TAIL = timedelta(hours=2)

ARGS_LIMIT = 160
RESULT_LIMIT = 220
LINE_LIMIT = 400

CALLS_SQL = """
    SELECT c.id::text AS id, c.caller_id, c.started_at, c.ended_at, c.duration_seconds,
           c.scenario, c.transferred_to_operator, c.transfer_reason,
           t.slug AS tenant_slug, t.name AS tenant_name
    FROM calls c
    LEFT JOIN tenants t ON t.id = c.tenant_id
    WHERE c.started_at >= :since AND c.started_at < :until
      AND (CAST(:tenant AS text) IS NULL OR t.slug = CAST(:tenant AS text))
      AND (CAST(:call_id AS text) IS NULL OR c.id = CAST(CAST(:call_id AS text) AS uuid))
    ORDER BY c.started_at
"""

TURNS_SQL = """
    SELECT call_id::text AS call_id, created_at, speaker, content
    FROM call_turns
    WHERE call_id = ANY(CAST(:ids AS uuid[])) AND created_at >= :since AND created_at < :until
    ORDER BY created_at
"""

TOOL_CALLS_SQL = """
    SELECT call_id::text AS call_id, created_at, tool_name, tool_args, tool_result, success,
           duration_ms
    FROM call_tool_calls
    WHERE call_id = ANY(CAST(:ids AS uuid[])) AND created_at >= :since AND created_at < :until
    ORDER BY created_at
"""


# ── Pure helpers (unit-tested) ──────────────────────────────────────────


def parse_callers(raw: str | None) -> set[str]:
    """``--callers`` → comparison keys (``network_policy.phone_key``); junk dropped."""
    if not raw:
        return set()
    keys = {phone_key(part.strip()) for part in raw.split(",")}
    return {k for k in keys if k}


def caller_matches(caller_id: Any, keys: set[str]) -> bool:
    """No filter → every call; otherwise the caller's key must be in ``keys``."""
    if not keys:
        return True
    key = phone_key(caller_id)
    return key is not None and key in keys


def _as_obj(value: Any) -> Any:
    """asyncpg returns jsonb as ``str`` unless a codec is set — accept both."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def short_json(value: Any, limit: int) -> str:
    value = _as_obj(value)
    if value is None:
        return "—"
    return shorten(json.dumps(value, ensure_ascii=False, default=str), limit)


def classify_result(result: Any, success: Any) -> str | None:
    """The refusal / failure mark of a tool call, ``None`` for a normal answer.

    ``success=False`` is an exception. A dict result is a refusal when it says
    so positively — ``blocked``, ``error``, ``action_required`` or a failing
    ``status`` — because a guard refusal is logged as ``success=True``.
    """
    if success is False:
        return "exception"
    result = _as_obj(result)
    if not isinstance(result, dict):
        return None
    if result.get("blocked"):
        return "blocked"
    if result.get("error"):
        return "error"
    if result.get("action_required"):
        return "action_required"
    status = result.get("status")
    if isinstance(status, str) and status in FAIL_STATUSES:
        return status
    return None


@dataclass
class Event:
    at: datetime
    kind: str  # "turn" | "tool"
    row: dict[str, Any]


def build_timeline(turns: list[dict[str, Any]], tools: list[dict[str, Any]]) -> list[Event]:
    """Lines and tool calls of one call merged by ``created_at``.

    Stable: on equal timestamps a line comes before a tool call and the input
    order is kept within each kind.
    """
    events = [Event(r["created_at"], "turn", r) for r in turns]
    events += [Event(r["created_at"], "tool", r) for r in tools]
    order = {"turn": 0, "tool": 1}
    return sorted(events, key=lambda e: (e.at, order[e.kind]))


def format_event(ev: Event, started_at: datetime | None) -> str:
    offset = ""
    if started_at is not None:
        secs = int((ev.at - started_at).total_seconds())
        offset = f"+{secs // 60:02d}:{secs % 60:02d}"
    row = ev.row
    if ev.kind == "turn":
        who = {"customer": "Клієнт", "bot": "Бот"}.get(row.get("speaker") or "", row.get("speaker"))
        return f"- `{offset}` **{who}:** {shorten(row.get('content') or '', LINE_LIMIT)}"
    mark = classify_result(row.get("tool_result"), row.get("success"))
    flag = f" ⛔ **{mark.upper()}**" if mark else ""
    return (
        f"- `{offset}` 🔧 `{row.get('tool_name')}`{flag} "
        f"args={short_json(row.get('tool_args'), ARGS_LIMIT)} "
        f"→ {short_json(row.get('tool_result'), RESULT_LIMIT)}"
    )


def order_requests(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The order-request tool calls of a call with their outcome."""
    out = []
    for row in tools:
        if row.get("tool_name") not in ORDER_TOOLS:
            continue
        result = _as_obj(row.get("tool_result"))
        status = result.get("status") if isinstance(result, dict) else None
        out.append(
            {
                "tool": row["tool_name"],
                "status": status or classify_result(result, row.get("success")) or "unknown",
                "args": _as_obj(row.get("tool_args")),
                # since 2026-09-28 the handler's raw result carries it
                "number": result.get("_audit_order_number") if isinstance(result, dict) else None,
            }
        )
    return out


def render_call(
    call: dict[str, Any], turns: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> str:
    started = call.get("started_at")
    lines = [
        f"## {started:%Y-%m-%d %H:%M:%S} UTC — {call.get('tenant_name') or call.get('tenant_slug') or '?'}"
        if started
        else "## ? — ?",
        "",
        f"- call_id: `{call['id']}`",
        f"- номер: {mask_phone(call.get('caller_id'))}",
        f"- длительность: {call['duration_seconds']} с"
        if call.get("duration_seconds") is not None
        else "- длительность: — (ended_at пуст — звонок оборван/не закрыт)",
        f"- scenario: {call.get('scenario') or '—'}",
    ]
    if call.get("transferred_to_operator"):
        lines.append(
            f"- **перевод на оператора:** да, reason=`{call.get('transfer_reason') or '—'}`"
        )
    else:
        lines.append("- перевод на оператора: нет")
    refusals = sum(
        1 for r in tools if classify_result(r.get("tool_result"), r.get("success")) is not None
    )
    lines.append(f"- вызовов инструментов: {len(tools)}, из них отказов/ошибок: {refusals}")
    lines += ["", "### Хронология", ""]
    timeline = build_timeline(turns, tools)
    if not timeline:
        lines.append("_реплик и вызовов нет_")
    lines += [format_event(ev, started) for ev in timeline]
    orders = order_requests(tools)
    lines += ["", "### Заявки", ""]
    if not orders:
        lines.append("_заявок нет_")
    for o in orders:
        number = f" `{o['number']}`" if o.get("number") else ""
        lines.append(
            f"- `{o['tool']}` → **{o['status']}**{number}; args={short_json(o['args'], 400)}"
        )
    if any(o["status"] == "request_created" and not o.get("number") for o in orders):
        lines.append(
            f"- номер заявки (`AI-TEST-<n>`) до 2026-09-28 в БД не писался — искать в логе: "
            f"`docker logs call-center-call-processor-1 2>&1 | grep 'Order request' | grep {call['id']}`"
            " (channel_uuid в логе = calls.id; лог живёт до пересоздания контейнера деплоем)"
        )
    lines.append("")
    return "\n".join(lines)


def render_report(
    calls: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    header: str,
) -> str:
    by_call_turns: dict[str, list[dict[str, Any]]] = {}
    by_call_tools: dict[str, list[dict[str, Any]]] = {}
    for r in turns:
        by_call_turns.setdefault(r["call_id"], []).append(r)
    for r in tools:
        by_call_tools.setdefault(r["call_id"], []).append(r)
    parts = [
        f"# Разбор тестовых звонков\n\n{header}\n\nЗвонков: {len(calls)}. "
        "Хронология по `created_at`: реплика клиента может стоять чуть позже "
        "вызова, который она вызвала; слова-паразиты вырезаны из транскрипта.\n"
    ]
    for call in calls:
        parts.append(
            render_call(call, by_call_turns.get(call["id"], []), by_call_tools.get(call["id"], []))
        )
    return "\n".join(parts)


# ── CLI / DB ────────────────────────────────────────────────────────────


def parse_time(value: str) -> datetime:
    """ISO date/time; naive = UTC."""
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Разбор тестовых звонков (read-only).")
    p.add_argument("--since", required=True, type=parse_time, help="начало окна, UTC (ISO)")
    p.add_argument(
        "--until", type=parse_time, default=None, help="конец окна, UTC (по умолч. сейчас)"
    )
    p.add_argument("--callers", default=None, help="номера тестировщиков через запятую")
    p.add_argument("--tenant", default=None, help="slug сети: prokoleso | tvoya-shina")
    p.add_argument("--call-id", default=None, help="один звонок (uuid)")
    return p.parse_args(argv)


async def fetch(args: argparse.Namespace) -> tuple[list[dict], list[dict], list[dict]]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config import get_settings

    until = args.until or datetime.now(UTC)
    engine = create_async_engine(get_settings().database.url)
    try:
        async with engine.connect() as conn, conn.begin() as tx:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            res = await conn.execute(
                text(CALLS_SQL),
                {
                    "since": args.since,
                    "until": until,
                    "tenant": args.tenant,
                    "call_id": args.call_id,
                },
            )
            keys = parse_callers(args.callers)
            calls = [dict(r) for r in res.mappings().all() if caller_matches(r["caller_id"], keys)]
            turns: list[dict] = []
            tools: list[dict] = []
            if calls:
                params = {
                    "ids": [c["id"] for c in calls],
                    "since": args.since,
                    "until": until + CALL_TAIL,
                }
                turns = [
                    dict(r) for r in (await conn.execute(text(TURNS_SQL), params)).mappings().all()
                ]
                tools = [
                    dict(r)
                    for r in (await conn.execute(text(TOOL_CALLS_SQL), params)).mappings().all()
                ]
            await tx.rollback()
    finally:
        await engine.dispose()
    return calls, turns, tools


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    calls, turns, tools = asyncio.run(fetch(args))
    until = args.until.isoformat() if args.until else "now"
    header = (
        f"Окно: {args.since.isoformat()} … {until}; сеть: {args.tenant or 'все'}; "
        f"номера: {', '.join(mask_phone(c) for c in (args.callers or '').split(',') if c) or 'все'}"
    )
    sys.stdout.write(render_report(calls, turns, tools, header=header))
    return 0


if __name__ == "__main__":
    sys.exit(main())
