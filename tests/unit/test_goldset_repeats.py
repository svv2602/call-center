"""Repeats in the goldset harness: ``--repeat N`` and the stability report.

No LLM, no DB, no network: the sandbox is replaced at its boundary
(``create_sandbox_agent`` / ``process_sandbox_turn``) by scripted replies, so
``main`` runs for real from the command line down to ``--json-out``.

``TestOneRepeatIsUnchanged`` compares against literals recorded from the
harness BEFORE repeats existed (commit 2e723da), not against strings the
current code builds — it has to stay green on that old file too.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any
from unittest.mock import create_autospec, patch

import pytest

from scripts import run_goldset as rg

if TYPE_CHECKING:
    from pathlib import Path

TS, PK = "tvoya-shina", "prokoleso"
PROVIDER = "openai-gpt41-mini"

CASES_YAML = """\
- id: stable_ok
  network: both
  sales_enabled: true
  turns:
    - user: добрий день
      expect: {must_contain: ['вітаю']}
- id: delivery_cost
  network: both
  sales_enabled: true
  turns:
    - user: мені потрібна доставка
    - user: скільки коштує доставка?
      expect_by_network:
        tvoya-shina: {must_contain: ['безкоштовн']}
        prokoleso: {must_contain: ['тариф'], tool_called: [get_delivery_cost]}
- id: pk_storage
  network: [prokoleso]
  sales_enabled: true
  turns:
    - user: чи можна залишити шини на зберігання?
      expect: {transfer_reason: non_fitting_scope}
- id: later_case
  network: both
  sales_enabled: true
  pending: 'wave-9-Z-later: not merged'
  turns:
    - user: потім
"""


def said(
    text: str, *calls: tuple[str, dict[str, Any]], error: str | None = None
) -> rg.TurnObservation:
    return rg.TurnObservation(
        response_text=text, tool_calls=[rg.ToolCall(n, a) for n, a in calls], error=error
    )


HELLO = said("Вітаю, слухаю вас.")
ASK_CITY = said("Підкажіть, будь ласка, місто.")
TS_FREE = said("Доставка по Україні безкоштовна, Новою поштою.")
PK_QUOTE = said(
    "Доставка за тарифами перевізника, приблизно вісімсот гривень.",
    ("get_pickup_points", {"city": "Київ"}),
    ("get_delivery_cost", {"city": "Київ", "quantity": 4, "diameter": 16}),
)
PK_VAGUE = said(
    "Доставку оформимо, вартість уточнить менеджер.", ("get_pickup_points", {"city": "Київ"})
)
PK_TIMEOUT = said("", error="LLM timeout after 30s")

# (network, customer line) -> the reply of each take, in play order.
# Over three takes: `stable_ok` is green in both networks, `delivery_cost` is
# green in Tvoya Shina and green in 1 of 3 in Pro Koleso (red, green, red),
# `pk_storage` dies on a turn error every time.
SCRIPT: dict[tuple[str, str], list[rg.TurnObservation]] = {
    (TS, "добрий день"): [HELLO, HELLO, HELLO],
    (PK, "добрий день"): [HELLO, HELLO, HELLO],
    (TS, "мені потрібна доставка"): [ASK_CITY, ASK_CITY, ASK_CITY],
    (TS, "скільки коштує доставка?"): [TS_FREE, TS_FREE, TS_FREE],
    (PK, "мені потрібна доставка"): [ASK_CITY, ASK_CITY, ASK_CITY],
    (PK, "скільки коштує доставка?"): [PK_VAGUE, PK_QUOTE, PK_VAGUE],
    (PK, "чи можна залишити шини на зберігання?"): [PK_TIMEOUT, PK_TIMEOUT, PK_TIMEOUT],
}
PAIRS = [
    ("stable_ok", TS),
    ("stable_ok", PK),
    ("delivery_cost", TS),
    ("delivery_cost", PK),
    ("pk_storage", PK),
]


@dataclass
class FakeAgent:
    serial: int
    network: str


class ScriptedSandbox:
    """Stands in for the sandbox: a new agent per build, scripted replies per take."""

    def __init__(self, script: dict[tuple[str, str], list[rg.TurnObservation]]) -> None:
        self.script = script
        self.built: list[FakeAgent] = []
        self.played: list[tuple[int, str, int]] = []  # (agent serial, line, len(history))

    def _build(self, network: str) -> FakeAgent:
        agent = FakeAgent(serial=len(self.built) + 1, network=network)
        self.built.append(agent)
        return agent

    def _reply(
        self, agent: FakeAgent, user: str, history: list[dict[str, Any]]
    ) -> tuple[rg.TurnObservation, list[dict[str, Any]]]:
        take = sum(
            1
            for serial, line, _ in self.played
            if line == user and self.built[serial - 1].network == agent.network
        )
        self.played.append((agent.serial, user, len(history)))
        o = self.script[(agent.network, user)][take]
        return o, [
            *history,
            {"role": "user", "content": user},
            {"role": "assistant", "content": o.response_text},
        ]

    # The harness seam: `make_agent` / `play_turn`.
    async def make_agent(self, network: str, case: rg.Case) -> FakeAgent:
        return self._build(network)

    async def play_turn(
        self, agent: FakeAgent, user: str, history: list[dict[str, Any]]
    ) -> tuple[rg.TurnObservation, list[dict[str, Any]]]:
        return self._reply(agent, user, history)

    # The sandbox boundary: what `SandboxAgentFactory` and `_play_sandbox_turn` call.
    async def create_sandbox_agent(self, engine: Any, **kwargs: Any) -> FakeAgent:
        return self._build(kwargs["tenant"]["slug"])

    async def process_sandbox_turn(
        self, agent: FakeAgent, user_text: str, history: list[dict[str, Any]], is_mock: bool = True
    ) -> Any:
        from src.sandbox.agent_runner import SandboxTurnResult, ToolCallRecord

        o, updated = self._reply(agent, user_text, history)
        return SandboxTurnResult(
            response_text=o.response_text,
            updated_history=updated,
            latency_ms=0,
            input_tokens=o.input_tokens,
            output_tokens=o.output_tokens,
            model="fake",
            tool_calls=[ToolCallRecord(c.name, c.args, None, 0, True) for c in o.tool_calls],
            error=o.error,
        )


def run_main(sandbox: ScriptedSandbox, cases_dir: Path, *argv: str) -> tuple[int, str, str]:
    """``main`` with the DB, the LLM router and the sandbox replaced; (rc, stdout, stderr)."""
    import sqlalchemy.ext.asyncio as sa_async
    from sqlalchemy.ext.asyncio import AsyncEngine

    import src.llm.router as router_mod
    import src.sandbox.agent_runner as runner

    engine = create_autospec(AsyncEngine, instance=True)
    router = create_autospec(router_mod.LLMRouter, instance=True)
    router.providers = {PROVIDER: object()}
    router.config = {"providers": {PROVIDER: {"model": "gpt-4.1-mini"}}}

    async def tenants(_engine: Any) -> dict[str, dict[str, Any]]:
        return {
            n: {"id": n, "slug": n, "config": {"agent_provider_override": PROVIDER}}
            for n in rg.NETWORKS
        }

    out, err = io.StringIO(), io.StringIO()
    with (
        patch.object(sa_async, "create_async_engine", lambda *a, **k: engine),
        patch.object(router_mod, "LLMRouter", lambda: router),
        patch.object(rg, "_load_tenants", tenants),
        patch.object(runner, "create_sandbox_agent", sandbox.create_sandbox_agent),
        patch.object(runner, "process_sandbox_turn", sandbox.process_sandbox_turn),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        rc = rg.main(["--cases-dir", str(cases_dir), "--input-tokens-per-turn", "1000000", *argv])
    return rc, out.getvalue(), err.getvalue()


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cases"
    d.mkdir()
    (d / "cases.yaml").write_text(CASES_YAML, encoding="utf-8")
    return d


def played_runs(cases_dir: Path) -> tuple[list[rg.Run], list[tuple[str, str]]]:
    return rg.expand_runs(rg.load_cases(cases_dir))


def play(sandbox: ScriptedSandbox, runs: list[rg.Run], **kwargs: Any) -> list[rg.RunResult]:
    return asyncio.run(rg.run_all(runs, sandbox.make_agent, sandbox.play_turn, **kwargs))


# ── one repeat: recorded on the harness before repeats (2e723da) ──────────

HEAD_HEADER = (
    "Кейсів: 4; прогонів (кейс × мережа): 5; pending пропущено: 1\n"
    "  skip later_case: pending wave-9-Z-later: not merged\n"
)
HEAD_PLAN = (
    "prokoleso: 3 runs, 4 turns, provider=openai-gpt41-mini (gpt-4.1-mini), ≈ $1.60\n"
    "tvoya-shina: 2 runs, 3 turns, provider=openai-gpt41-mini (gpt-4.1-mini), ≈ $1.20\n"
    "Разом: 7 turns ≈ $2.80 (оцінка; 1000000 input tok/turn)\n"
)
HEAD_NO_YES = "Платний прогін не запущено: додайте --yes.\n"
HEAD_REPORT = """\
Зелёних кейсів: 3/5 (кейс × мережа)
  tvoya-shina: 2/2
  prokoleso: 1/3
Кейсів, зелених у всіх своїх мережах: 1/3
Провали за асертами (runs × turns):
  must_contain: 1
  tool_called: 1
  turn_error: 1
  transfer_reason: 1
Червоні:
  delivery_cost [prokoleso] «скільки коштує доставка?» must_contain: missing ['тариф']
      бот: 'Доставку оформимо, вартість уточнить менеджер.' tools=['get_pickup_points']
  delivery_cost [prokoleso] «скільки коштує доставка?» tool_called: missing ['get_delivery_cost']; called ['get_pickup_points']
      бот: 'Доставку оформимо, вартість уточнить менеджер.' tools=['get_pickup_points']
  pk_storage [prokoleso] «чи можна залишити шини на зберігання?» turn_error: LLM timeout after 30s
      бот: '' tools=[]
  pk_storage [prokoleso] «чи можна залишити шини на зберігання?» transfer_reason: want non_fitting_scope, got []
      бот: '' tools=[]
Пропущено (pending): 1
  later_case: pending wave-9-Z-later: not merged
"""
HEAD_STDERR = (
    "  OK   stable_ok [tvoya-shina]\n"
    "  OK   stable_ok [prokoleso]\n"
    "  OK   delivery_cost [tvoya-shina]\n"
    "  FAIL delivery_cost [prokoleso]\n"
    "  FAIL pk_storage [prokoleso]\n"
)
HEAD_JSON = [
    {
        "case": "stable_ok",
        "network": TS,
        "passed": True,
        "turns": [
            {"user": "добрий день", "response": "Вітаю, слухаю вас.", "tools": [], "failed": []}
        ],
    },
    {
        "case": "stable_ok",
        "network": PK,
        "passed": True,
        "turns": [
            {"user": "добрий день", "response": "Вітаю, слухаю вас.", "tools": [], "failed": []}
        ],
    },
    {
        "case": "delivery_cost",
        "network": TS,
        "passed": True,
        "turns": [
            {
                "user": "мені потрібна доставка",
                "response": "Підкажіть, будь ласка, місто.",
                "tools": [],
                "failed": [],
            },
            {
                "user": "скільки коштує доставка?",
                "response": "Доставка по Україні безкоштовна, Новою поштою.",
                "tools": [],
                "failed": [],
            },
        ],
    },
    {
        "case": "delivery_cost",
        "network": PK,
        "passed": False,
        "turns": [
            {
                "user": "мені потрібна доставка",
                "response": "Підкажіть, будь ласка, місто.",
                "tools": [],
                "failed": [],
            },
            {
                "user": "скільки коштує доставка?",
                "response": "Доставку оформимо, вартість уточнить менеджер.",
                "tools": ["get_pickup_points"],
                "failed": [
                    {"name": "must_contain", "passed": False, "detail": "missing ['тариф']"},
                    {
                        "name": "tool_called",
                        "passed": False,
                        "detail": "missing ['get_delivery_cost']; called ['get_pickup_points']",
                    },
                ],
            },
        ],
    },
    {
        "case": "pk_storage",
        "network": PK,
        "passed": False,
        "turns": [
            {
                "user": "чи можна залишити шини на зберігання?",
                "response": "",
                "tools": [],
                "failed": [
                    {"name": "turn_error", "passed": False, "detail": "LLM timeout after 30s"},
                    {
                        "name": "transfer_reason",
                        "passed": False,
                        "detail": "want non_fitting_scope, got []",
                    },
                ],
            }
        ],
    },
]
OLD_RECORD_KEYS = ("case", "network", "passed")
OLD_TURN_KEYS = ("user", "response", "tools", "failed")


def old_fields(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **{k: r[k] for k in OLD_RECORD_KEYS},
            "turns": [{k: t[k] for k in OLD_TURN_KEYS} for t in r["turns"]],
        }
        for r in records
    ]


class TestOneRepeatIsUnchanged:
    """No ``--repeat``: stdout, stderr, the exit code and the old JSON fields as before."""

    def test_paid_run_prints_the_single_pass_report(self, cases_dir: Path) -> None:
        rc, out, err = run_main(ScriptedSandbox(SCRIPT), cases_dir, "--yes")
        assert out == HEAD_HEADER + HEAD_PLAN + HEAD_REPORT
        assert err == HEAD_STDERR
        assert rc == 1

    def test_plan_without_yes_plays_nothing(self, cases_dir: Path) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        rc, out, err = run_main(sandbox, cases_dir)
        assert out == HEAD_HEADER + HEAD_PLAN + HEAD_NO_YES
        assert (rc, err, sandbox.built) == (0, "", [])

    def test_json_out_keeps_the_old_fields(self, cases_dir: Path, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        run_main(ScriptedSandbox(SCRIPT), cases_dir, "--yes", "--json-out", str(path))
        assert old_fields(json.loads(path.read_text(encoding="utf-8"))) == HEAD_JSON

    def test_all_green_run_returns_zero(self, cases_dir: Path) -> None:
        rc, out, _ = run_main(ScriptedSandbox(SCRIPT), cases_dir, "--yes", "--case", "stable_ok")
        assert rc == 0
        assert "Зелёних кейсів: 2/2 (кейс × мережа)" in out.splitlines()

    def test_format_report_of_one_pass(self, cases_dir: Path) -> None:
        runs, skipped = played_runs(cases_dir)
        results = play(ScriptedSandbox(SCRIPT), runs)
        assert rg.format_report(results, skipped) + "\n" == HEAD_REPORT


# ── several repeats ───────────────────────────────────────────────────────


class TestRunAllRepeats:
    def test_default_is_one_repeat(self, cases_dir: Path) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        results = play(sandbox, played_runs(cases_dir)[0])
        assert [(r.case_id, r.network, r.repeat) for r in results] == [(c, n, 1) for c, n in PAIRS]
        assert len(sandbox.built) == len(PAIRS)

    def test_every_pair_is_played_in_every_repeat(self, cases_dir: Path) -> None:
        results = play(ScriptedSandbox(SCRIPT), played_runs(cases_dir)[0], repeat=3)
        # A repeat is a whole pass: pair order inside, repeats one after another.
        assert [(r.case_id, r.network, r.repeat) for r in results] == [
            (c, n, number) for number in (1, 2, 3) for c, n in PAIRS
        ]

    def test_every_repeat_gets_a_new_agent_and_an_empty_history(self, cases_dir: Path) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        play(sandbox, played_runs(cases_dir)[0], repeat=3)
        assert len(sandbox.built) == len(PAIRS) * 3
        histories: dict[int, list[int]] = {}
        for serial, _line, history_len in sandbox.played:
            histories.setdefault(serial, []).append(history_len)
        # Every agent that was built played, each from an empty history; the
        # two-turn case carries its history inside a repeat, never across.
        assert sorted(histories) == list(range(1, len(PAIRS) * 3 + 1))
        assert all(h in ([0], [0, 2]) for h in histories.values())
        assert sum(1 for h in histories.values() if h == [0, 2]) == 2 * 3

    def test_repeat_below_one_is_refused_before_anything_is_played(self, cases_dir: Path) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        with pytest.raises(ValueError, match="repeat"):
            play(sandbox, played_runs(cases_dir)[0], repeat=0)
        assert sandbox.built == []

    def test_progress_names_the_repeat(self, cases_dir: Path, capsys: Any) -> None:
        play(ScriptedSandbox(SCRIPT), played_runs(cases_dir)[0], repeat=3)
        lines = capsys.readouterr().err.splitlines()
        assert lines[3] == "  FAIL delivery_cost [prokoleso] повтор 1/3"
        assert lines[8] == "  OK   delivery_cost [prokoleso] повтор 2/3"
        assert len(lines) == len(PAIRS) * 3


class TestStabilityGroups:
    def _pairs(self, cases_dir: Path) -> dict[tuple[str, str], rg.PairStability]:
        results = play(ScriptedSandbox(SCRIPT), played_runs(cases_dir)[0], repeat=3)
        return {(p.case_id, p.network): p for p in rg.group_pairs(results)}

    def test_green_red_and_flaky_pairs_land_in_their_groups(self, cases_dir: Path) -> None:
        pairs = self._pairs(cases_dir)
        assert list(pairs) == PAIRS
        assert {key: (p.green, len(p.repeats), p.verdict) for key, p in pairs.items()} == {
            ("stable_ok", TS): (3, 3, rg.GREEN),
            ("stable_ok", PK): (3, 3, rg.GREEN),
            ("delivery_cost", TS): (3, 3, rg.GREEN),
            ("delivery_cost", PK): (1, 3, rg.FLAKY),
            ("pk_storage", PK): (0, 3, rg.RED),
        }

    def test_repeats_of_a_pair_stay_in_repeat_order(self, cases_dir: Path) -> None:
        flaky = self._pairs(cases_dir)[("delivery_cost", PK)]
        assert [(r.repeat, r.passed) for r in flaky.repeats] == [
            (1, False),
            (2, True),
            (3, False),
        ]

    def test_two_of_three_green_is_still_flaky(self) -> None:
        ok = rg.AssertionResult("must_contain", True)
        bad = rg.AssertionResult("must_contain", False, "missing")
        turns = [[ok], [ok], [bad]]
        results = [
            rg.RunResult("a", TS, [rg.TurnReport("u", "r", [], t)], repeat=n)
            for n, t in enumerate(turns, 1)
        ]
        (pair,) = rg.group_pairs(results)
        assert (pair.green, pair.verdict) == (2, rg.FLAKY)
        assert rg.exit_code(results) == 1

    def test_exit_code_is_zero_only_when_every_pair_is_stable_green(self) -> None:
        ok = rg.AssertionResult("must_contain", True)
        green = [
            rg.RunResult("a", TS, [rg.TurnReport("u", "r", [], [ok])], repeat=n) for n in (1, 2)
        ]
        assert rg.exit_code(green) == 0


class TestStabilityReport:
    def _report(self, cases_dir: Path, script: Any = None) -> str:
        runs, skipped = played_runs(cases_dir)
        return rg.format_report(play(ScriptedSandbox(script or SCRIPT), runs, repeat=3), skipped)

    def test_summary_counts_pairs_by_stability(self, cases_dir: Path) -> None:
        lines = self._report(cases_dir).splitlines()
        assert lines[:7] == [
            "Повторів на пару: 3; пар (кейс × мережа): 5; зіграно прогонів: 15",
            "Стабільно зелені (3/3): 3/5",
            "Стабільно червоні (0/3): 1/5",
            "Плаваючі (k/3): 1/5",
            "  tvoya-shina: стабільно зелені 2, стабільно червоні 0, плаваючі 0 (пар 2)",
            "  prokoleso: стабільно зелені 1, стабільно червоні 1, плаваючі 1 (пар 3)",
            "Зелених за повторами: №1 3/5, №2 4/5, №3 3/5",
        ]

    def test_single_pass_headline_is_not_printed_over_repeats(self, cases_dir: Path) -> None:
        # «Зелёних кейсів N/M» means one pass; over pairs × repeats it would
        # read 10/15 and hide that only 3 of 5 pairs are stable.
        assert "Зелёних кейсів" not in self._report(cases_dir)

    def test_flaky_pair_is_listed_repeat_by_repeat(self, cases_dir: Path) -> None:
        lines = self._report(cases_dir).splitlines()
        start = lines.index("Плаваючі:")
        failed_turn = (
            "        хід 2 «скільки коштує доставка?» "
            "бот: 'Доставку оформимо, вартість уточнить менеджер.' tools=['get_pickup_points']"
        )
        failures = [
            "          must_contain: missing ['тариф']",
            "          tool_called: missing ['get_delivery_cost']; called ['get_pickup_points']",
        ]
        assert lines[start + 1 : start + 11] == [
            "  delivery_cost [prokoleso]: зелених 1/3, червоних 2/3",
            "      повтор 1: провал асерта",
            failed_turn,
            *failures,
            "      повтор 2: OK",
            "      повтор 3: провал асерта",
            failed_turn,
            *failures,
        ]
        assert lines[start + 11] == "Стабільно червоні:"

    def test_turn_error_is_counted_apart_from_a_failed_assertion(self, cases_dir: Path) -> None:
        lines = self._report(cases_dir).splitlines()
        assert "Червоних повторів: 5 — помилка ходу (turn_error): 3, провал асерта: 2" in lines
        start = lines.index("Стабільно червоні:")
        assert lines[start + 1 : start + 4] == [
            "  pk_storage [prokoleso]: зелених 0/3, червоних 3/3 (з них помилка ходу: 3)",
            "      повтор 1: помилка ходу (turn_error)",
            "        хід 1 «чи можна залишити шини на зберігання?» бот: '' tools=[]",
        ]

    def test_pair_flaky_only_through_a_turn_error_says_so(self, cases_dir: Path) -> None:
        script = {**SCRIPT, (PK, "добрий день"): [HELLO, PK_TIMEOUT, HELLO]}
        lines = self._report(cases_dir, script).splitlines()
        start = lines.index(
            "  stable_ok [prokoleso]: зелених 2/3, червоних 1/3 (з них помилка ходу: 1)"
        )
        assert lines[start + 1 : start + 3] == [
            "      повтор 1: OK",
            "      повтор 2: помилка ходу (turn_error)",
        ]
        assert "Червоних повторів: 6 — помилка ходу (turn_error): 4, провал асерта: 2" in lines

    def test_breakdown_and_pending_close_the_report(self, cases_dir: Path) -> None:
        lines = self._report(cases_dir).splitlines()
        start = lines.index("Провали за асертами (повтори × ходи):")
        assert sorted(lines[start + 1 : start + 5]) == [
            "  must_contain: 2",
            "  tool_called: 2",
            "  transfer_reason: 3",
            "  turn_error: 3",
        ]
        assert lines[-2:] == [
            "Пропущено (pending): 1",
            "  later_case: pending wave-9-Z-later: not merged",
        ]


class TestCostOfRepeats:
    def test_estimate_is_three_times_larger_for_three_repeats(self, cases_dir: Path) -> None:
        runs, _ = played_runs(cases_dir)
        turns, usd = rg.estimate_cost(runs, "gpt-4.1-mini")
        turns3, usd3 = rg.estimate_cost(runs, "gpt-4.1-mini", repeat=3)
        assert (turns, turns3) == (7, 21)
        assert usd is not None and usd3 == pytest.approx(3 * usd)

    def test_plan_without_yes_shows_the_cost_of_all_repeats(self, cases_dir: Path) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        rc, out, _ = run_main(sandbox, cases_dir, "--repeat", "3")
        assert out == (
            "Кейсів: 4; прогонів (кейс × мережа): 5; pending пропущено: 1; повторів на пару: 3\n"
            "  skip later_case: pending wave-9-Z-later: not merged\n"
            "prokoleso: 3 runs × 3 повторів, 12 turns, "
            "provider=openai-gpt41-mini (gpt-4.1-mini), ≈ $4.80\n"
            "tvoya-shina: 2 runs × 3 повторів, 9 turns, "
            "provider=openai-gpt41-mini (gpt-4.1-mini), ≈ $3.60\n"
            "Разом: 21 turns ≈ $8.41 (оцінка; 1000000 input tok/turn)\n" + HEAD_NO_YES
        )
        assert (rc, sandbox.built) == (0, [])


class TestRepeatOption:
    """``--repeat`` from the command line down to the played repeats."""

    def test_option_reaches_the_played_repeats(self, cases_dir: Path) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        rc, out, err = run_main(sandbox, cases_dir, "--repeat", "3", "--yes")
        assert len(sandbox.built) == len(PAIRS) * 3
        assert "Стабільно зелені (3/3): 3/5" in out.splitlines()
        assert "  OK   delivery_cost [prokoleso] повтор 2/3" in err.splitlines()
        assert rc == 1

    def test_flaky_pair_alone_fails_the_run(self, cases_dir: Path) -> None:
        argv = ("--repeat", "3", "--yes", "--network", PK, "--case", "delivery_cost")
        rc, out, _ = run_main(ScriptedSandbox(SCRIPT), cases_dir, *argv)
        assert "Плаваючі (k/3): 1/1" in out.splitlines()
        assert rc != 0

    def test_stable_green_pairs_pass_the_run(self, cases_dir: Path) -> None:
        argv = ("--repeat", "3", "--yes", "--case", "stable_ok")
        rc, out, _ = run_main(ScriptedSandbox(SCRIPT), cases_dir, *argv)
        assert "Стабільно зелені (3/3): 2/2" in out.splitlines()
        assert rc == 0

    @pytest.mark.parametrize("value", ["0", "-1", "two", "1.5"])
    def test_bad_value_is_an_input_error_and_nothing_starts(
        self, cases_dir: Path, value: str
    ) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        with pytest.raises(SystemExit) as exc:
            run_main(sandbox, cases_dir, "--yes", f"--repeat={value}")
        assert exc.value.code == 2
        assert (sandbox.built, sandbox.played) == ([], [])


class TestJsonOutOfRepeats:
    def _records(self, cases_dir: Path, tmp_path: Path, script: Any = None) -> list[dict[str, Any]]:
        path = tmp_path / "out.json"
        argv = ("--repeat", "3", "--yes", "--json-out", str(path))
        run_main(ScriptedSandbox(script or SCRIPT), cases_dir, *argv)
        return json.loads(path.read_text(encoding="utf-8"))

    def test_one_record_per_pair_and_repeat(self, cases_dir: Path, tmp_path: Path) -> None:
        records = self._records(cases_dir, tmp_path)
        assert [(r["case"], r["network"], r["repeat"]) for r in records] == [
            (c, n, number) for number in (1, 2, 3) for c, n in PAIRS
        ]
        flaky = [r["passed"] for r in records if (r["case"], r["network"]) == ("delivery_cost", PK)]
        assert flaky == [False, True, False]

    def test_turn_carries_tool_calls_with_arguments_in_call_order(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        records = self._records(cases_dir, tmp_path)
        (green,) = [
            r for r in records if (r["case"], r["network"], r["repeat"]) == ("delivery_cost", PK, 2)
        ]
        turn = green["turns"][1]
        assert turn["tool_calls"] == [
            {"name": "get_pickup_points", "args": {"city": "Київ"}},
            {"name": "get_delivery_cost", "args": {"city": "Київ", "quantity": 4, "diameter": 16}},
        ]
        # The old field stays what it was: the names.
        assert turn["tools"] == ["get_pickup_points", "get_delivery_cost"]
        assert green["turns"][0]["tool_calls"] == []

    def test_old_fields_of_the_first_repeat_are_the_single_pass_ones(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        records = self._records(cases_dir, tmp_path)
        assert old_fields([r for r in records if r["repeat"] == 1]) == HEAD_JSON

    def test_argument_json_cannot_encode_does_not_lose_the_report(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        dated = said("Вітаю.", ("get_fitting_slots", {"date": date(2026, 11, 20)}))
        script = {**SCRIPT, (TS, "добрий день"): [dated, dated, dated]}
        records = self._records(cases_dir, tmp_path, script)
        assert len(records) == len(PAIRS) * 3
        assert records[0]["turns"][0]["tool_calls"] == [
            {"name": "get_fitting_slots", "args": {"date": "2026-11-20"}}
        ]
