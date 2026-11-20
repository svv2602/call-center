"""``scripts/replay_scenarios.py``: loading, resuming, the cap, pairs lost to LLM errors.

No LLM, no DB, no network: the agent factory and the turn player are plain
stand-ins, and ``main`` runs for real from the command line down to the result
file in ``tmp_path``. Every customer line here is invented.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from scripts import replay_scenarios as rs
from scripts import run_goldset as rg

if TYPE_CHECKING:
    import argparse
    from collections.abc import AsyncIterator

TS, PK = "tvoya-shina", "prokoleso"
MODEL = "gpt-4.1-mini"  # $0.40 per 1M input tokens in rg.PRICES
TOKENS_PER_TURN = 100_000  # => $0.04 a turn
TIMEOUT = "LLM router: timeout"

HELLO, WINTER, PRIVET, WARRANTY = (
    "добрий день",
    "потрібні зимові шини",
    "здравствуйте",
    "яка гарантія?",
)
# s1 costs $0.08, s2 and s3 $0.04 each.
SCENARIOS: list[dict[str, Any]] = [
    {"id": "s1", "locale": "ua", "turns": [HELLO, WINTER]},
    {"id": "s2", "locale": "ru", "turns": [PRIVET], "note": "ignored field"},
    {"id": "s3", "locale": "ua", "turns": [WARRANTY]},
    {"id": "s_skip", "locale": "ua", "turns": ["цей сценарій пропущено"], "skip": True},
]


@dataclass
class FakeAgent:
    serial: int
    network: str
    scenario: str
    sales_enabled: bool


class FakeSandbox:
    """Stands in for the sandbox: a new agent per pair, a scripted error per (network, line)."""

    def __init__(
        self, errors: dict[tuple[str, str], list[str | None]] | None = None, model: str = MODEL
    ) -> None:
        self.errors = {key: list(plan) for key, plan in (errors or {}).items()}
        self.model = model
        self.agents: list[FakeAgent] = []
        self.played: list[tuple[int, str, int]] = []  # (agent serial, line, len(history))
        self.opened: list[tuple[str | None, tuple[str, ...]]] = []

    async def make_agent(self, network: str, case: rg.Case) -> FakeAgent:
        assert case.networks == (network,)
        assert all(not t.expect and not t.expect_by_network for t in case.turns)
        agent = FakeAgent(len(self.agents) + 1, network, case.id, case.sales_enabled)
        self.agents.append(agent)
        return agent

    async def play_turn(
        self, agent: FakeAgent, user: str, history: list[dict[str, Any]]
    ) -> tuple[rg.TurnObservation, list[dict[str, Any]]]:
        self.played.append((agent.serial, user, len(history)))
        plan = self.errors.get((agent.network, user)) or []
        error = plan.pop(0) if plan else None
        calls = []
        if user == WINTER:
            calls = [rg.ToolCall("search_tires", {"season": "winter", "size": "205/55 R16"})]
        if user == WARRANTY:
            calls = [rg.ToolCall("transfer_to_operator", {"reason": "non_fitting_scope"})]
        obs = rg.TurnObservation(
            response_text="" if error else f"відповідь на «{user}»",
            tool_calls=calls,
            error=error,
            input_tokens=TOKENS_PER_TURN,
            output_tokens=0,
        )
        return obs, [
            *history,
            {"role": "user", "content": user},
            {"role": "assistant", "content": obs.response_text},
        ]

    @contextlib.asynccontextmanager
    async def open(
        self, opts: argparse.Namespace, networks: tuple[str, ...]
    ) -> AsyncIterator[rs.Wiring]:
        self.opened.append((opts.provider, networks))
        yield rs.Wiring(self.make_agent, self.play_turn, {net: self.model for net in networks})

    @property
    def pairs_played(self) -> list[tuple[str, str]]:
        return [(a.scenario, a.network) for a in self.agents]


@pytest.fixture
def scenario_file(tmp_path: Path) -> Path:
    return write_lines(tmp_path / "set.jsonl", SCENARIOS)


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "result.jsonl"


def write_lines(path: Path, items: list[Any]) -> Path:
    path.write_text(
        "".join(
            (i if isinstance(i, str) else json.dumps(i, ensure_ascii=False)) + "\n" for i in items
        ),
        encoding="utf-8",
    )
    return path


def run(
    sandbox: FakeSandbox,
    scenario_file: Path,
    out: Path,
    *extra: str,
    network: str = TS,
    cap: str = "5",
) -> int:
    argv = ["--scenarios", str(scenario_file), "--out", str(out), "--network", network]
    argv += ["--sales-enabled", "on"] if "--sales-enabled" not in extra else []
    return rs.main([*argv, "--cap-usd", cap, *extra], open_wiring=sandbox.open)


def lines_of(out: Path) -> list[dict[str, Any]]:
    if not out.exists():
        return []
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def keys_of(out: Path) -> list[tuple[str, str, int]]:
    return [(r["id"], r["network"], r["run"]) for r in lines_of(out)]


class TestLoadScenarios:
    def test_sample_in_the_repository_loads(self) -> None:
        sample = Path(__file__).resolve().parents[1] / "goldset" / "replay" / "sample.jsonl"
        scenarios, skipped = rs.load_scenarios(sample)
        assert len(scenarios) == 3
        assert skipped == ["sample_skipped"]
        assert {s.locale for s in scenarios} == {"ua", "ru"}

    def test_skip_leaves_the_scenario_out_and_other_fields_are_ignored(
        self, scenario_file: Path
    ) -> None:
        scenarios, skipped = rs.load_scenarios(scenario_file)
        assert [(s.id, s.locale, s.turns) for s in scenarios] == [
            ("s1", "ua", [HELLO, WINTER]),
            ("s2", "ru", [PRIVET]),
            ("s3", "ua", [WARRANTY]),
        ]
        assert skipped == ["s_skip"]

    @pytest.mark.parametrize(
        ("bad", "message"),
        [
            ("{not json", "рядок 3: зіпсований JSON"),
            (
                {"id": "s1", "locale": "ua", "turns": ["ще раз"]},
                r"рядок 3: дубль id 's1' \(вперше — рядок 1\)",
            ),
            (
                {"id": "x", "locale": "pl", "turns": ["dzień dobry"]},
                "рядок 3: невідома локаль 'pl'",
            ),
            ({"id": "x", "turns": ["без локалі"]}, "рядок 3: невідома локаль None"),
            ({"id": "x", "locale": "ua", "turns": []}, "рядок 3: turns має бути непорожнім"),
            ({"id": "x", "locale": "ua"}, "рядок 3: turns має бути непорожнім"),
            ({"id": "x", "locale": "ua", "turns": ["добре", "  "]}, "рядок 3: кожна репліка"),
            ({"id": "x", "locale": "ua", "turns": ["добре", 5]}, "рядок 3: кожна репліка"),
            ({"locale": "ua", "turns": ["без id"]}, "рядок 3: немає id"),
            (["список"], "рядок 3: сценарій має бути об'єктом"),
        ],
    )
    def test_input_error_names_the_line_and_nothing_is_played(
        self,
        tmp_path: Path,
        out: Path,
        capsys: pytest.CaptureFixture[str],
        bad: Any,
        message: str,
    ) -> None:
        path = write_lines(tmp_path / "bad.jsonl", [SCENARIOS[0], SCENARIOS[1], bad, SCENARIOS[2]])
        with pytest.raises(rs.InputError, match=message):
            rs.load_scenarios(path)
        sandbox = FakeSandbox()
        assert run(sandbox, path, out, "--yes") == rs.EXIT_INPUT
        assert "Помилка входу" in capsys.readouterr().err
        assert (sandbox.opened, sandbox.played, out.exists()) == ([], [], False)

    def test_a_duplicate_of_a_skipped_id_is_still_a_duplicate(self, tmp_path: Path) -> None:
        path = write_lines(tmp_path / "bad.jsonl", [SCENARIOS[3], {**SCENARIOS[0], "id": "s_skip"}])
        with pytest.raises(rs.InputError, match="рядок 2: дубль id"):
            rs.load_scenarios(path)

    def test_missing_or_all_skipped_set_is_an_input_error(self, tmp_path: Path) -> None:
        with pytest.raises(rs.InputError, match="не вдалося прочитати"):
            rs.load_scenarios(tmp_path / "absent.jsonl")
        with pytest.raises(rs.InputError, match="жодного сценарію"):
            rs.load_scenarios(write_lines(tmp_path / "skipped.jsonl", [SCENARIOS[3]]))


class TestReplay:
    def test_one_line_per_pair_with_the_turns(self, scenario_file: Path, out: Path) -> None:
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--yes") == rs.EXIT_OK
        assert keys_of(out) == [("s1", TS, 1), ("s2", TS, 1), ("s3", TS, 1)]
        s1, s2, s3 = lines_of(out)
        assert (s1["locale"], s2["locale"]) == ("ua", "ru")
        assert (s1["sales_enabled"], s1["model"]) == (True, MODEL)
        assert [t["user"] for t in s1["turns"]] == [HELLO, WINTER]
        assert s1["turns"][1] == {
            "user": WINTER,
            "response": f"відповідь на «{WINTER}»",
            "tool_calls": [
                {"name": "search_tires", "args": {"season": "winter", "size": "205/55 R16"}}
            ],
            "transfer_reason": None,
            "error": None,
            "input_tokens": TOKENS_PER_TURN,
            "output_tokens": 0,
        }
        assert s3["turns"][0]["transfer_reason"] == "non_fitting_scope"
        assert (s1["input_tokens"], s1["cost_usd"]) == (2 * TOKENS_PER_TURN, pytest.approx(0.08))
        assert s2["cost_usd"] == pytest.approx(0.04)
        assert sandbox.pairs_played == [("s1", TS), ("s2", TS), ("s3", TS)]

    def test_every_pair_gets_a_fresh_agent_and_an_empty_history(
        self, scenario_file: Path, out: Path
    ) -> None:
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--yes", "--runs", "2", network="both") == 0
        # A run is a whole pass over the set: every pair once, then every pair again.
        one_pass = [(s, net) for net in (TS, PK) for s in ("s1", "s2", "s3")]
        assert sandbox.pairs_played == one_pass * 2
        assert keys_of(out) == [(s, net, n) for n in (1, 2) for (s, net) in one_pass]
        assert [a.serial for a in sandbox.agents] == list(range(1, 13))
        first_turns = [h for _, line, h in sandbox.played if line in (HELLO, PRIVET, WARRANTY)]
        assert first_turns == [0] * 12
        assert [h for _, line, h in sandbox.played if line == WINTER] == [2] * 4

    def test_network_and_sales_and_provider_options_reach_the_agent(
        self, scenario_file: Path, out: Path
    ) -> None:
        sandbox = FakeSandbox()
        code = run(
            sandbox,
            scenario_file,
            out,
            "--yes",
            "--sales-enabled",
            "off",
            "--provider",
            "p1",
            network=PK,
        )
        assert code == 0
        assert {a.network for a in sandbox.agents} == {PK}
        assert {a.sales_enabled for a in sandbox.agents} == {False}
        assert {r["sales_enabled"] for r in lines_of(out)} == {False}
        assert sandbox.opened == [("p1", (PK,))]

        on = FakeSandbox()
        assert run(on, scenario_file, out.with_name("on.jsonl"), "--yes") == 0
        assert {a.sales_enabled for a in on.agents} == {True}


class TestConfirmation:
    def test_without_yes_nothing_is_played(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--input-tokens-per-turn", "1000000") == rs.EXIT_OK
        assert (sandbox.agents, sandbox.played, out.exists()) == ([], [], False)
        printed = capsys.readouterr().out
        assert "Сценаріїв: 3; пропущено (skip): 1" in printed
        assert "до програвання: 3 (4 реплік)" in printed
        # 4 turns × 1M input tokens × $0.40 + 4 × 200 output tokens × $1.60.
        assert f"tvoya-shina: модель {MODEL}, реплік 4, ≈ $1.60" in printed
        assert "кап $5.00, стоп на 80% = $4.00" in printed
        assert "Платний прогін не запущено: додайте --yes." in printed

    def test_validate_does_not_open_the_wiring(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--validate", "--yes", network="both") == rs.EXIT_OK
        assert (sandbox.opened, sandbox.played, out.exists()) == ([], [], False)
        assert "Пар (сценарій × мережа × run): 6; вже у файлі: 0" in capsys.readouterr().out


class TestResume:
    def test_second_call_plays_nothing(self, scenario_file: Path, out: Path) -> None:
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--yes") == 0
        played, opened = list(sandbox.played), list(sandbox.opened)
        assert run(sandbox, scenario_file, out, "--yes") == rs.EXIT_OK
        assert (sandbox.played, sandbox.opened) == (played, opened)
        assert keys_of(out) == [("s1", TS, 1), ("s2", TS, 1), ("s3", TS, 1)]

    def test_only_the_pairs_not_in_the_file_are_played(
        self, scenario_file: Path, out: Path
    ) -> None:
        first = FakeSandbox()
        assert run(first, scenario_file, out, "--yes") == 0
        second = FakeSandbox()
        assert run(second, scenario_file, out, "--yes", "--runs", "2", network="both") == 0
        # Run 1 of Tvoya Shina is there already; the other nine pairs are new.
        assert len(second.agents) == 9
        ts, pk = [(s, TS) for s in ("s1", "s2", "s3")], [(s, PK) for s in ("s1", "s2", "s3")]
        assert second.pairs_played == [*pk, *ts, *pk]
        assert len(keys_of(out)) == len(set(keys_of(out))) == 12

    def test_a_file_played_with_other_sales_enabled_is_refused(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(FakeSandbox(), scenario_file, out, "--yes") == 0
        sandbox = FakeSandbox()
        code = run(sandbox, scenario_file, out, "--yes", "--sales-enabled", "off", network=PK)
        assert code == rs.EXIT_INPUT
        assert "іншим sales_enabled" in capsys.readouterr().err
        assert (sandbox.opened, sandbox.played, len(lines_of(out))) == ([], [], 3)

    def test_a_broken_result_file_is_an_input_error(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out.write_text('{"id": "s1", "network": "tvoya-shina", "run": 1, "tur', encoding="utf-8")
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--yes") == rs.EXIT_INPUT
        assert "рядок 1: зіпсований JSON" in capsys.readouterr().err
        assert sandbox.played == []


class TestCap:
    def test_stops_at_80_percent_and_keeps_what_was_done(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # 80 % of $0.14 is $0.112: s1 ($0.08) is under it, s1 + s2 ($0.12) is not — and
        # $0.12 is still under the cap itself.
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--yes", cap="0.14") == rs.EXIT_CAP
        assert keys_of(out) == [("s1", TS, 1), ("s2", TS, 1)]
        assert sandbox.pairs_played == [("s1", TS), ("s2", TS)]
        assert "Стоп за капом" in capsys.readouterr().out

    def test_what_the_file_already_holds_counts(self, scenario_file: Path, out: Path) -> None:
        assert run(FakeSandbox(), scenario_file, out, "--yes", cap="0.14") == rs.EXIT_CAP
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, out, "--yes", cap="0.14") == rs.EXIT_CAP
        assert (sandbox.played, len(lines_of(out))) == ([], 2)
        # A higher cap lets the same command finish the set.
        assert run(sandbox, scenario_file, out, "--yes", cap="1") == rs.EXIT_OK
        assert sandbox.pairs_played == [("s3", TS)]
        assert keys_of(out) == [("s1", TS, 1), ("s2", TS, 1), ("s3", TS, 1)]

    def test_a_lost_pair_is_paid_for_too_and_the_cap_outranks_the_llm_code(
        self, scenario_file: Path, out: Path
    ) -> None:
        # s1 dies on its first turn: $0.04 spent, nothing written. 80 % of $0.045 is $0.036.
        sandbox = FakeSandbox({(TS, HELLO): [TIMEOUT]})
        assert run(sandbox, scenario_file, out, "--yes", cap="0.045") == rs.EXIT_CAP
        assert (sandbox.pairs_played, lines_of(out)) == ([("s1", TS)], [])

    def test_unknown_model_price_refuses_to_start(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sandbox = FakeSandbox(model="mystery-model")
        assert run(sandbox, scenario_file, out, "--yes") == rs.EXIT_INPUT
        assert "немає ціни в PRICES" in capsys.readouterr().err
        assert (sandbox.played, out.exists()) == ([], False)

    @pytest.mark.parametrize("cap", ["0", "-1", "abc"])
    def test_cap_must_be_a_positive_number(self, scenario_file: Path, out: Path, cap: str) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run(FakeSandbox(), scenario_file, out, "--yes", cap=cap)
        assert exit_info.value.code == 2

    @pytest.mark.parametrize(
        "missing", ["--scenarios", "--out", "--network", "--cap-usd", "--sales-enabled"]
    )
    def test_required_options_have_no_default(self, missing: str) -> None:
        argv = {
            "--scenarios": "set.jsonl",
            "--out": "replay-out.jsonl",
            "--network": TS,
            "--cap-usd": "1",
            "--sales-enabled": "on",
        }
        del argv[missing]
        with pytest.raises(SystemExit) as exit_info:
            rs.main(
                [part for item in argv.items() for part in item], open_wiring=FakeSandbox().open
            )
        assert exit_info.value.code == 2


class TestLlmFailure:
    def test_a_pair_with_a_failed_turn_is_not_written_and_is_replayed_next_time(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        sandbox = FakeSandbox({(TS, WINTER): [TIMEOUT]})
        assert run(sandbox, scenario_file, out, "--yes") == rs.EXIT_LLM
        assert keys_of(out) == [("s2", TS, 1), ("s3", TS, 1)]
        assert all(t["error"] is None for r in lines_of(out) for t in r["turns"])
        printed = capsys.readouterr().out
        assert f"ЗБІЙ LLM s1 [tvoya-shina] run 1: {TIMEOUT} — пару не записано" in printed
        assert "Записано пар: 2; не записано через збій LLM: 1; не зіграно: 0" in printed

        assert run(sandbox, scenario_file, out, "--yes") == rs.EXIT_OK
        assert sandbox.pairs_played[3:] == [("s1", TS)]
        assert keys_of(out) == [("s2", TS, 1), ("s3", TS, 1), ("s1", TS, 1)]

    def test_the_pair_stops_at_its_failed_turn(self, scenario_file: Path, out: Path) -> None:
        sandbox = FakeSandbox({(TS, HELLO): [TIMEOUT]})
        assert run(sandbox, scenario_file, out, "--yes") == rs.EXIT_LLM
        assert [line for _, line, _ in sandbox.played] == [HELLO, PRIVET, WARRANTY]

    def test_failures_in_a_row_stop_the_run(
        self, scenario_file: Path, out: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        down = {
            (net, line): [TIMEOUT] * 9 for net in (TS, PK) for line in (HELLO, PRIVET, WARRANTY)
        }
        sandbox = FakeSandbox(down)
        assert run(sandbox, scenario_file, out, "--yes", network="both") == rs.EXIT_LLM
        assert len(sandbox.agents) == rs.DEFAULT_MAX_CONSECUTIVE_ERRORS == 3
        assert "Стоп: збій LLM у 3 парах поспіль." in capsys.readouterr().out
        assert not out.exists()

        shorter = FakeSandbox(down)
        code = run(
            shorter, scenario_file, out, "--yes", "--max-consecutive-errors", "2", network="both"
        )
        assert (code, len(shorter.agents)) == (rs.EXIT_LLM, 2)

    def test_a_clean_pair_resets_the_count(self, scenario_file: Path, out: Path) -> None:
        # lost, clean, lost, clean, lost, clean: never two in a row.
        sandbox = FakeSandbox(
            {(TS, HELLO): [TIMEOUT], (TS, WARRANTY): [TIMEOUT], (PK, PRIVET): [TIMEOUT]}
        )
        code = run(
            sandbox, scenario_file, out, "--yes", "--max-consecutive-errors", "2", network="both"
        )
        assert code == rs.EXIT_LLM
        assert len(sandbox.agents) == 6
        assert keys_of(out) == [("s2", TS, 1), ("s1", PK, 1), ("s3", PK, 1)]


class TestOutPath:
    def test_a_result_path_inside_the_repository_is_refused(
        self, scenario_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        inside = rs.REPO_ROOT / "tests" / "goldset" / "replay" / "never-written.jsonl"
        sandbox = FakeSandbox()
        assert run(sandbox, scenario_file, inside, "--yes") == rs.EXIT_INPUT
        assert "поза репозиторієм" in capsys.readouterr().err
        assert (sandbox.opened, sandbox.played, inside.exists()) == ([], [], False)


def test_the_result_file_is_what_the_compare_script_reads(scenario_file: Path, out: Path) -> None:
    from scripts import replay_compare as rc

    assert run(FakeSandbox(), scenario_file, out, "--yes", "--runs", "2") == 0
    records = rc.read_results(out)
    assert {r.cls for r in rc.compare_files(records, records)} == {rc.IDENTICAL}
    assert {g.cls for g in rc.compare_within(records)} == {rc.IDENTICAL}
