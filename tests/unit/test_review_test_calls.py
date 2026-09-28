"""``scripts/review_test_calls.py`` — pure helpers of the acceptance-call report.

The DB part is a few read-only SELECTs checked by hand on prod; here: the
testers' number filter (same normalisation as ``network_policy.phone_key``),
the merge of lines and tool calls by ``created_at``, the refusal marks (a guard
refusal is ``success=true``) and the order-request section.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scripts import review_test_calls as r

T0 = datetime(2026, 9, 29, 7, 0, 0, tzinfo=UTC)


def at(sec: float) -> datetime:
    return T0 + timedelta(seconds=sec)


def turn(sec: float, speaker: str, content: str, call_id: str = "c1") -> dict:
    return {"call_id": call_id, "created_at": at(sec), "speaker": speaker, "content": content}


def tool(
    sec: float,
    name: str,
    result: object = None,
    *,
    args: object = None,
    success: bool = True,
    call_id: str = "c1",
) -> dict:
    return {
        "call_id": call_id,
        "created_at": at(sec),
        "tool_name": name,
        "tool_args": args if args is not None else {},
        "tool_result": result,
        "success": success,
        "duration_ms": 5,
    }


# ── number filter ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "caller", ["0671234567", "+380671234567", "380671234567", "80671234567", "067 123-45-67"]
)
def test_filter_matches_every_spelling_of_the_same_number(caller: str) -> None:
    keys = r.parse_callers("+38 (067) 123 45 67")
    assert r.caller_matches(caller, keys)


def test_filter_rejects_other_numbers() -> None:
    keys = r.parse_callers("0671234567,0501112233")
    assert not r.caller_matches("0671234568", keys)
    assert not r.caller_matches("0931234567", keys)  # same tail, other operator code
    assert not r.caller_matches(None, keys)
    assert not r.caller_matches("", keys)


def test_filter_internal_extension_compares_whole() -> None:
    keys = r.parse_callers("7770")
    assert r.caller_matches("7770", keys)
    assert not r.caller_matches("17770", keys)


def test_no_filter_passes_every_call_and_junk_is_dropped() -> None:
    assert r.parse_callers(None) == set()
    assert r.parse_callers(" , abc ,") == set()
    assert r.caller_matches("0671234567", set())
    assert r.caller_matches(None, set())


def test_masked_number_shows_only_last_four() -> None:
    call = {
        "id": "c1",
        "caller_id": "0671234567",
        "started_at": T0,
        "duration_seconds": 10,
        "tenant_name": "Про Колесо",
    }
    out = r.render_call(call, [], [])
    assert "***4567" in out
    assert "0671234567" not in out


# ── chronology ──────────────────────────────────────────────────────────


def test_timeline_merges_by_created_at_not_by_input_order() -> None:
    turns = [
        turn(0, "bot", "Добрий день"),
        turn(5, "customer", "шини 205/55 R16"),
        turn(9, "bot", "Ось варіанти"),
    ]
    tools = [tool(7, "search_tires", {"total": 3})]
    kinds = [(e.kind, e.at) for e in r.build_timeline(turns, tools)]
    assert kinds == [("turn", at(0)), ("turn", at(5)), ("tool", at(7)), ("turn", at(9))]


def test_timeline_puts_line_first_on_equal_timestamp() -> None:
    ev = r.build_timeline([turn(3, "customer", "так")], [tool(3, "search_tires", {})])
    assert [e.kind for e in ev] == ["turn", "tool"]


def test_rendered_chronology_is_in_time_order_with_offsets() -> None:
    call = {
        "id": "c1",
        "caller_id": "0671234567",
        "started_at": T0,
        "duration_seconds": 70,
        "tenant_name": "Твоя Шина",
    }
    out = r.render_call(
        call,
        [turn(65, "bot", "Заявку прийнято"), turn(1, "customer", "потрібні шини")],
        [tool(30, "search_tires", {"total": 2})],
    )
    i_customer = out.index("потрібні шини")
    i_tool = out.index("search_tires")
    i_bot = out.index("Заявку прийнято")
    assert i_customer < i_tool < i_bot
    assert "`+00:30`" in out and "`+01:05`" in out


# ── refusal marks ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("result", "success", "mark"),
    [
        ({"blocked": True, "reason": "customer_request"}, True, "blocked"),
        ({"error": True, "message": "Неможливо записати без: ..."}, True, "error"),
        ({"action_required": "ask_city", "stations": []}, True, "action_required"),
        ({"status": "request_failed", "error": False}, True, "request_failed"),
        ({"status": "missing_field"}, True, "missing_field"),
        ('{"error": true}', True, "error"),  # jsonb as a string
        ({"total": 3}, False, "exception"),
        ({"status": "request_created"}, True, None),
        ({"total": 3, "items": []}, True, None),
        (None, True, None),
    ],
)
def test_classify_result(result: object, success: bool, mark: str | None) -> None:
    assert r.classify_result(result, success) == mark


def test_refusal_is_flagged_in_the_chronology_and_counted() -> None:
    call = {
        "id": "c1",
        "caller_id": "0671234567",
        "started_at": T0,
        "duration_seconds": 20,
        "transferred_to_operator": True,
        "transfer_reason": "customer_request",
        "tenant_name": "Про Колесо",
    }
    out = r.render_call(
        call,
        [],
        [
            tool(4, "transfer_to_operator", {"blocked": True}, args={"reason": "customer_request"}),
            tool(8, "search_tires", {"total": 1}),
        ],
    )
    assert "`transfer_to_operator` ⛔ **BLOCKED**" in out
    assert "`search_tires` ⛔" not in out
    assert "из них отказов/ошибок: 1" in out
    assert "reason=`customer_request`" in out


# ── order requests ──────────────────────────────────────────────────────


def test_order_request_section_lists_outcome_and_points_to_the_log() -> None:
    call = {
        "id": "c1",
        "caller_id": "0671234567",
        "started_at": T0,
        "duration_seconds": 90,
        "tenant_name": "Твоя Шина",
    }
    tools = [
        tool(
            40,
            "submit_order_request",
            {"status": "missing_field", "message": "місто"},
            args={"items": [{"product_id": "t3", "quantity": 2}]},
        ),
        tool(
            60,
            "submit_order_request",
            {"status": "request_created"},
            args={"recipient_name": "Петренко Іван", "payment_method": "cod"},
        ),
    ]
    orders = r.order_requests(tools)
    assert [o["status"] for o in orders] == ["missing_field", "request_created"]
    out = r.render_call(call, [], tools)
    assert "→ **request_created**" in out
    assert "grep c1" in out


def test_no_order_request_no_log_hint() -> None:
    call = {
        "id": "c1",
        "caller_id": "0671234567",
        "started_at": T0,
        "duration_seconds": 9,
        "tenant_name": "Твоя Шина",
    }
    out = r.render_call(call, [], [tool(3, "submit_order_request", {"status": "request_failed"})])
    assert "request_failed" in out
    assert "Order request" not in out
    assert "_заявок нет_" in r.render_call(call, [], [tool(3, "search_tires", {})])


def test_report_groups_rows_by_call() -> None:
    calls = [
        {
            "id": "c1",
            "caller_id": "0671234567",
            "started_at": T0,
            "duration_seconds": 5,
            "tenant_name": "Про Колесо",
        },
        {
            "id": "c2",
            "caller_id": "0501112233",
            "started_at": at(100),
            "duration_seconds": 5,
            "tenant_name": "Твоя Шина",
        },
    ]
    out = r.render_report(
        calls,
        [turn(1, "customer", "перший", "c1"), turn(101, "customer", "другий", "c2")],
        [],
        header="h",
    )
    c1, c2 = out.split("call_id: `c1`")[1].split("call_id: `c2`")
    assert "перший" in c1 and "другий" not in c1
    assert "другий" in c2 and "перший" not in c2


def test_unfinished_call_has_no_duration() -> None:
    call = {
        "id": "c1",
        "caller_id": "0671234567",
        "started_at": T0,
        "duration_seconds": None,
        "tenant_name": "Твоя Шина",
    }
    assert "ended_at пуст" in r.render_call(call, [], [])


def test_order_request_shows_the_audited_number() -> None:
    from scripts.review_test_calls import order_requests

    rows = [
        {
            "tool_name": "submit_order_request",
            "tool_args": {"payment_method": "cod"},
            "tool_result": {"status": "request_created", "_audit_order_number": "AI-TEST-7"},
            "success": True,
        }
    ]
    (order,) = order_requests(rows)
    assert order["number"] == "AI-TEST-7"
    assert order["status"] == "request_created"
