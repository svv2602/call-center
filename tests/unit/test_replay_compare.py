"""``scripts/replay_compare.py``: the signature, the five classes, the two reports.

No LLM, no DB, no network. Every customer line and reply here is invented.
"""

from __future__ import annotations

import json
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import replay_compare as rc

TS, PK = "tvoya-shina", "prokoleso"
REPO_ROOT = Path(__file__).resolve().parents[2]

# A report names classes only; whether a difference matters is the reader's call.
VERDICT_WORDS = re.compile(
    r"дефект|регрес|краще|гірш|кращ|лучше|хуже|defect|regress|better|worse", re.IGNORECASE
)


def turn(
    user: str, response: str, *calls: tuple[str, dict[str, Any]], error: str | None = None
) -> dict[str, Any]:
    return {
        "user": user,
        "response": response,
        "tool_calls": [{"name": name, "args": args} for name, args in calls],
        "error": error,
    }


def rec(sid: str, network: str, run: int, *turns: dict[str, Any]) -> dict[str, Any]:
    return rc.normalize_record(
        "test", {"id": sid, "network": network, "run": run, "turns": list(turns)}
    )


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return path


SEARCH = ("search_tires", {"size": "205/55 R16", "season": "winter"})
SEARCH_REORDERED = ("search_tires", {"season": "winter", "size": "205/55 R16"})
SEARCH_NO_SPACE = ("search_tires", {"size": "205/55R16", "season": "winter"})
STOCK = ("check_availability", {"product_id": "T-100"})
TRANSFER_SCOPE = ("transfer_to_operator", {"reason": "non_fitting_scope", "summary": "рахунок"})
TRANSFER_ASKED = ("transfer_to_operator", {"reason": "customer_request", "summary": "рахунок"})

HELLO = turn("добрий день", "Вітаю, слухаю вас.")
FOUND = turn("зимові шини на шістнадцять", "Є три варіанти.", SEARCH)


def classes(results: list[rc.PairResult]) -> dict[str, str]:
    return {r.key[0]: r.cls for r in results}


class TestClasses:
    def test_each_class_on_its_own_pair(self) -> None:
        timeout = turn("зимові шини на шістнадцять", "", error="LLM router: timeout")
        a = [
            rec("same", TS, 1, HELLO, FOUND),
            rec("worded", TS, 1, HELLO, FOUND),
            rec("decided", TS, 1, HELLO, FOUND),
            rec("failed", TS, 1, HELLO, FOUND),
            rec("only_a", TS, 1, HELLO),
        ]
        b = [
            rec("same", TS, 1, HELLO, FOUND),
            rec("worded", TS, 1, HELLO, {**FOUND, "response": "Знайшла три моделі."}),
            rec("decided", TS, 1, HELLO, turn(FOUND["user"], "Є три варіанти.", STOCK)),
            rec("failed", TS, 1, HELLO, timeout),
            rec("only_b", TS, 1, HELLO),
        ]
        results = rc.compare_files(a, b)
        assert classes(results) == {
            "same": rc.IDENTICAL,
            "worded": rc.TEXT_ONLY,
            "decided": rc.STRUCTURAL,
            "failed": rc.ERROR,
            "only_a": rc.MISSING,
            "only_b": rc.MISSING,
        }
        present = {r.key[0]: r.present_in for r in results if r.cls == rc.MISSING}
        assert present == {"only_a": "A", "only_b": "B"}

    def test_error_wins_over_a_structural_difference(self) -> None:
        a = [rec("s", TS, 1, turn("шини", "Шукаю.", SEARCH))]
        b = [rec("s", TS, 1, turn("шини", "", STOCK, error="LLM router: 503"))]
        assert classes(rc.compare_files(a, b)) == {"s": rc.ERROR}
        assert classes(rc.compare_files(b, a)) == {"s": rc.ERROR}

    def test_same_scenario_in_two_networks_and_runs_are_separate_pairs(self) -> None:
        a = [rec("s", TS, 1, HELLO), rec("s", PK, 1, HELLO), rec("s", TS, 2, HELLO)]
        b = [rec("s", TS, 1, HELLO), rec("s", PK, 1, FOUND), rec("s", TS, 2, HELLO)]
        got = {r.key: r.cls for r in rc.compare_files(a, b)}
        assert got == {
            ("s", TS, 1): rc.IDENTICAL,
            ("s", PK, 1): rc.STRUCTURAL,
            ("s", TS, 2): rc.IDENTICAL,
        }


class TestSignature:
    def pair(self, a_turn: dict[str, Any], b_turn: dict[str, Any], *ignore: str) -> rc.PairResult:
        a, b = [rec("s", TS, 1, a_turn)], [rec("s", TS, 1, b_turn)]
        return rc.compare_files(a, b, rc.parse_ignore(list(ignore)))[0]

    def test_tool_order_is_structural(self) -> None:
        got = self.pair(turn("шини", "Ось.", SEARCH, STOCK), turn("шини", "Ось.", STOCK, SEARCH))
        assert got.cls == rc.STRUCTURAL
        assert got.fields == (rc.F_TOOLS, rc.F_TOOL_ARGS)

    def test_argument_key_order_is_not_a_difference(self) -> None:
        got = self.pair(turn("шини", "Ось.", SEARCH), turn("шини", "Ось.", SEARCH_REORDERED))
        assert got.cls == rc.IDENTICAL

    def test_whitespace_runs_in_an_argument_are_not_a_difference(self) -> None:
        spaced = ("search_tires", {"size": " 205/55  R16 ", "season": "winter"})
        assert self.pair(turn("шини", "Ось.", SEARCH), turn("шини", "Ось.", spaced)).cls == (
            rc.IDENTICAL
        )

    def test_a_different_argument_value_is_structural(self) -> None:
        got = self.pair(turn("шини", "Ось.", SEARCH), turn("шини", "Ось.", SEARCH_NO_SPACE))
        assert got.cls == rc.STRUCTURAL
        assert got.fields == (rc.F_TOOL_ARGS,)
        assert got.divergences[0].turn == 1
        assert "205/55 R16" in got.divergences[0].a[0]
        assert "205/55R16" in got.divergences[0].b[0]

    def test_transfer_reason_is_its_own_field(self) -> None:
        got = self.pair(
            turn("рахунок", "З'єдную.", TRANSFER_SCOPE), turn("рахунок", "З'єдную.", TRANSFER_ASKED)
        )
        assert got.cls == rc.STRUCTURAL
        assert got.fields == (rc.F_TOOL_ARGS, rc.F_TRANSFER)
        reasons = [(d.a, d.b) for d in got.divergences if d.field == rc.F_TRANSFER]
        assert reasons == [("non_fitting_scope", "customer_request")]

    def test_turn_count_is_structural(self) -> None:
        a, b = [rec("s", TS, 1, HELLO, FOUND)], [rec("s", TS, 1, HELLO)]
        got = rc.compare_files(a, b)[0]
        assert (got.cls, got.fields) == (rc.STRUCTURAL, (rc.F_TURN_COUNT,))
        assert (got.divergences[0].a, got.divergences[0].b) == (2, 1)

    def test_a_changed_customer_line_is_structural(self) -> None:
        got = self.pair(turn("добрий день", "Вітаю."), turn("доброго ранку", "Вітаю."))
        assert (got.cls, got.fields) == (rc.STRUCTURAL, (rc.F_USER,))

    def test_reply_whitespace_is_not_a_text_difference(self) -> None:
        assert self.pair(turn("шини", "Ось  вони. "), turn("шини", "Ось вони.")).cls == rc.IDENTICAL


class TestIgnore:
    def pair(self, a_turn: dict[str, Any], b_turn: dict[str, Any], *ignore: str) -> rc.PairResult:
        return TestSignature().pair(a_turn, b_turn, *ignore)

    def test_ignoring_tool_args_removes_the_difference(self) -> None:
        a, b = turn("шини", "Ось.", SEARCH), turn("шини", "Ось.", SEARCH_NO_SPACE)
        assert self.pair(a, b).cls == rc.STRUCTURAL
        assert self.pair(a, b, "tool_args").cls == rc.IDENTICAL

    def test_ignoring_one_argument_keeps_the_others(self) -> None:
        a = turn("шини", "Ось.", ("search_tires", {"size": "205/55 R16", "query": "зима"}))
        b = turn("шини", "Ось.", ("search_tires", {"size": "205/55 R16", "query": "зимові"}))
        c = turn("шини", "Ось.", ("search_tires", {"size": "195/65 R15", "query": "зима"}))
        assert self.pair(a, b).cls == rc.STRUCTURAL
        assert self.pair(a, b, "tool_args.search_tires.query").cls == rc.IDENTICAL
        assert self.pair(a, c, "tool_args.search_tires.query").cls == rc.STRUCTURAL

    def test_ignoring_one_tool_keeps_the_other_tools(self) -> None:
        a = turn("шини", "Ось.", SEARCH, STOCK)
        b = turn("шини", "Ось.", SEARCH_NO_SPACE, STOCK)
        c = turn("шини", "Ось.", SEARCH, ("check_availability", {"product_id": "T-200"}))
        assert self.pair(a, b, "tool_args.search_tires").cls == rc.IDENTICAL
        assert self.pair(a, c, "tool_args.search_tires").cls == rc.STRUCTURAL

    def test_ignoring_tools_and_transfer_reason(self) -> None:
        a, b = turn("шини", "Ось.", SEARCH), turn("шини", "Ось.", STOCK)
        assert self.pair(a, b, "tool_args").fields == (rc.F_TOOLS,)
        assert self.pair(a, b, "tools", "tool_args").cls == rc.IDENTICAL
        x, y = turn("рахунок", "Ок.", TRANSFER_SCOPE), turn("рахунок", "Ок.", TRANSFER_ASKED)
        assert self.pair(x, y, "tool_args").fields == (rc.F_TRANSFER,)
        assert self.pair(x, y, "tool_args", "transfer_reason").cls == rc.IDENTICAL

    @pytest.mark.parametrize("name", ["turn_count", "error", "user"])
    def test_locked_fields_cannot_be_ignored(self, name: str) -> None:
        with pytest.raises(rc.InputError, match="виключити не можна"):
            rc.parse_ignore([name])

    @pytest.mark.parametrize("name", ["tool", "response", "tool_args.", "tool_args.a.b.c", ""])
    def test_unknown_field_is_an_input_error(self, name: str) -> None:
        with pytest.raises(rc.InputError, match="невідоме поле"):
            rc.parse_ignore([name])

    def test_turn_count_still_decides_with_everything_ignored(self) -> None:
        everything = rc.parse_ignore(["tools", "tool_args", "transfer_reason"])
        a, b = [rec("s", TS, 1, HELLO, FOUND)], [rec("s", TS, 1, HELLO)]
        assert rc.compare_files(a, b, everything)[0].fields == (rc.F_TURN_COUNT,)

    def test_an_exclusion_that_names_nothing_in_the_files_is_an_input_error(self) -> None:
        records = [rec("s", TS, 1, turn("шини", "Ось.", SEARCH))]
        rc.check_ignore_seen(rc.parse_ignore(["tool_args.search_tires.size"]), records)
        rc.check_ignore_seen(rc.parse_ignore(["tool_args.search_tires"]), records)
        with pytest.raises(rc.InputError, match="такого tool у файлах немає"):
            rc.check_ignore_seen(rc.parse_ignore(["tool_args.serch_tires"]), records)
        with pytest.raises(rc.InputError, match="такого аргументу у файлах немає"):
            rc.check_ignore_seen(rc.parse_ignore(["tool_args.search_tires.sise"]), records)


class TestWithin:
    def test_two_of_three_runs_agree(self) -> None:
        records = [
            rec("s", TS, 1, turn("шини", "Ось.", SEARCH)),
            rec("s", TS, 2, turn("шини", "Є такі.", SEARCH_REORDERED)),
            rec("s", TS, 3, turn("шини", "Ось.", SEARCH_NO_SPACE)),
        ]
        (group,) = rc.compare_within(records)
        assert (group.cls, group.agree, group.runs) == (rc.STRUCTURAL, 2, (1, 2, 3))
        assert group.fields == (rc.F_TOOL_ARGS,)
        report = rc.format_within_report([group], records)
        assert "s [tvoya-shina] structural: однаковий підпис 2 з 3; поля: tool_args" in report

    def test_group_classes(self) -> None:
        records = [
            rec("same", TS, 1, FOUND),
            rec("same", TS, 2, FOUND),
            rec("worded", TS, 1, FOUND),
            rec("worded", TS, 2, {**FOUND, "response": "Знайшла три моделі."}),
            rec("failed", TS, 1, FOUND),
            rec("failed", TS, 2, turn(FOUND["user"], "", error="LLM router: timeout")),
            rec("single", TS, 1, FOUND),
            rec("same", PK, 1, FOUND),
            rec("same", PK, 2, HELLO),
        ]
        groups = {(g.scenario, g.network): g for g in rc.compare_within(records)}
        assert {key: g.cls for key, g in groups.items()} == {
            ("same", TS): rc.IDENTICAL,
            ("worded", TS): rc.TEXT_ONLY,
            ("failed", TS): rc.ERROR,
            ("single", TS): rc.MISSING,
            ("same", PK): rc.STRUCTURAL,
        }
        assert (groups[("failed", TS)].clean, groups[("failed", TS)].agree) == (1, 1)
        report = rc.format_within_report(list(groups.values()), records)
        assert "failed [tvoya-shina] error: однаковий підпис 1 з 2; з помилкою ходу: 1" in report

    def test_ignore_reaches_the_within_mode(self) -> None:
        records = [
            rec("s", TS, 1, turn("шини", "Ось.", SEARCH)),
            rec("s", TS, 2, turn("шини", "Ось.", SEARCH_NO_SPACE)),
        ]
        assert rc.compare_within(records)[0].cls == rc.STRUCTURAL
        assert rc.compare_within(records, rc.parse_ignore(["tool_args"]))[0].cls == rc.IDENTICAL


def mixed_pair() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    a = [
        rec("same", TS, 1, HELLO, FOUND),
        rec("worded", TS, 1, FOUND),
        rec("decided", PK, 1, turn("шини", "Ось.", SEARCH)),
        rec("failed", PK, 1, FOUND),
        rec("only_a", TS, 1, HELLO),
    ]
    b = [
        rec("same", TS, 1, HELLO, FOUND),
        rec("worded", TS, 1, {**FOUND, "response": "Знайшла три моделі."}),
        rec("decided", PK, 1, turn("шини", "Ось.", SEARCH_NO_SPACE)),
        rec("failed", PK, 1, turn(FOUND["user"], "", error="LLM router: timeout")),
    ]
    return a, b


class TestReport:
    def test_counts_fields_and_structural_pairs(self) -> None:
        a, b = mixed_pair()
        report = rc.format_pair_report(
            rc.compare_files(a, b), a, b, ("before.jsonl", "after.jsonl")
        )
        assert "Порівняння: A = before.jsonl; B = after.jsonl" in report
        assert "Пар: 5" in report
        for line in (
            "  identical: 1 (20.0 %)",
            "  text_only: 1 (20.0 %)",
            "  structural: 1 (20.0 %)",
            "  error: 1 (20.0 %)",
            "  missing: 1 (20.0 %) (лише в A: 1, лише в B: 0)",
            "  tool_args: 1",
            "  decided [prokoleso] run 1: tool_args",
        ):
            assert line in report
        assert "хід 1 tool_args: A=" in report
        assert "Знайшла три моделі." not in report

    def test_excluded_fields_are_named_in_the_header_and_the_summary(self) -> None:
        a, b = mixed_pair()
        ignore = rc.parse_ignore(["tool_args.search_tires.size", "transfer_reason"])
        report = rc.format_pair_report(rc.compare_files(a, b, ignore), a, b, ignore=ignore)
        lines = report.splitlines()
        named = "виключені поля: tool_args.search_tires.size, transfer_reason"
        assert lines[1].endswith(named)
        assert lines[-1].endswith(named)
        assert "structural 0 (0.0 %)" in lines[-1]
        plain = rc.format_pair_report(rc.compare_files(a, b), a, b).splitlines()
        assert plain[1].endswith("виключені поля: немає")
        assert plain[-1].endswith("виключені поля: немає")
        within = rc.format_within_report(rc.compare_within(a, ignore), a, ignore=ignore)
        assert within.splitlines()[1].endswith(named)
        assert within.splitlines()[-1].endswith(named)

    def test_show_text_prints_both_replies(self) -> None:
        a, b = mixed_pair()
        report = rc.format_pair_report(rc.compare_files(a, b), a, b, show_text=True)
        assert "Пари text_only:" in report
        assert "        A: Є три варіанти." in report
        assert "        B: Знайшла три моделі." in report

    def test_the_reports_assign_classes_and_no_verdicts(self) -> None:
        a, b = mixed_pair()
        ignore = rc.parse_ignore(["transfer_reason"])
        reports = [
            rc.format_pair_report(rc.compare_files(a, b), a, b, show_text=True),
            rc.format_pair_report(rc.compare_files(a, b, ignore), a, b, ignore=ignore),
            rc.format_within_report(rc.compare_within([*a, rec("same", TS, 2, FOUND)]), a),
            rc.format_within_report(rc.compare_within(b), b, show_text=True),
        ]
        for report in reports:
            assert not VERDICT_WORDS.search(report), report
        assert not VERDICT_WORDS.search(rc.__doc__ or "")


class TestMain:
    def test_pairs_are_matched_by_key_not_by_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        records = [
            rec(
                f"s{n}",
                net,
                run,
                turn(f"репліка {n}", f"відповідь {n}", ("search_tires", {"n": n})),
            )
            for n in range(6)
            for net in (TS, PK)
            for run in (1, 2)
        ]
        shuffled = list(records)
        random.Random(7).shuffle(shuffled)
        assert [rc.record_key(r) for r in shuffled] != [rc.record_key(r) for r in records]
        a = write_jsonl(tmp_path / "a.jsonl", records)
        b = write_jsonl(tmp_path / "b.jsonl", shuffled)
        assert rc.main([str(a), str(b)]) == 0
        out = capsys.readouterr().out
        assert "Пар: 24" in out
        assert "  identical: 24 (100.0 %)" in out
        assert "  structural: 0 (0.0 %)" in out

    def test_a_file_against_itself_is_all_identical(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        a, _ = mixed_pair()
        path = write_jsonl(tmp_path / "a.jsonl", a)
        assert rc.main([str(path), str(path)]) == 0
        assert "  identical: 5 (100.0 %)" in capsys.readouterr().out

    def test_structural_pairs_do_not_change_the_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        a, b = mixed_pair()
        code = rc.main(
            [str(write_jsonl(tmp_path / "a.jsonl", a)), str(write_jsonl(tmp_path / "b.jsonl", b))]
        )
        assert code == 0
        assert "  structural: 1 (20.0 %)" in capsys.readouterr().out

    def test_ignore_and_show_text_reach_the_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        a, b = mixed_pair()
        files = [
            str(write_jsonl(tmp_path / "a.jsonl", a)),
            str(write_jsonl(tmp_path / "b.jsonl", b)),
        ]
        assert rc.main([*files, "--ignore", "tool_args.search_tires.size", "--show-text"]) == 0
        out = capsys.readouterr().out
        assert "  structural: 0 (0.0 %)" in out
        assert "виключені поля: tool_args.search_tires.size" in out
        assert "        B: Знайшла три моделі." in out

    def test_within_mode(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        records = [
            rec("s", TS, 1, turn("шини", "Ось.", SEARCH)),
            rec("s", TS, 2, turn("шини", "Ось.", SEARCH)),
            rec("s", TS, 3, turn("шини", "Ось.", STOCK)),
        ]
        path = write_jsonl(tmp_path / "runs.jsonl", records)
        assert rc.main(["--within", str(path)]) == 0
        out = capsys.readouterr().out
        assert "Груп (сценарій × мережа): 1; записів: 3" in out
        assert "однаковий підпис 2 з 3" in out
        assert "    run 3:" not in out
        assert rc.main(["--within", str(path), "--show-text"]) == 0
        assert "    run 3:" in capsys.readouterr().out
        assert rc.main(["--within", str(path), "--ignore", "tools", "--ignore", "tool_args"]) == 0
        assert "  identical: 1 (100.0 %)" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("argv_tail", "message"),
        [
            (["--ignore", "turn_count"], "виключити не можна"),
            (["--ignore", "error"], "виключити не можна"),
            (["--ignore", "nonsense"], "невідоме поле"),
            (["--ignore", "tool_args.no_such_tool"], "такого tool у файлах немає"),
            (["--within"], "потрібно файлів: 1, передано: 2"),
        ],
    )
    def test_input_errors_exit_2(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        argv_tail: list[str],
        message: str,
    ) -> None:
        a, b = mixed_pair()
        files = [
            str(write_jsonl(tmp_path / "a.jsonl", a)),
            str(write_jsonl(tmp_path / "b.jsonl", b)),
        ]
        assert rc.main([*files, *argv_tail]) == 2
        captured = capsys.readouterr()
        assert message in captured.err
        assert captured.out == ""

    def test_one_file_without_within_is_an_input_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        a, _ = mixed_pair()
        assert rc.main([str(write_jsonl(tmp_path / "a.jsonl", a))]) == 2
        assert "потрібно файлів: 2, передано: 1" in capsys.readouterr().err


class TestReadResults:
    def test_broken_line_is_reported_with_its_number(self, tmp_path: Path) -> None:
        path = tmp_path / "a.jsonl"
        good = json.dumps(rec("s1", TS, 1, HELLO), ensure_ascii=False)
        path.write_text(f"{good}\n\n{{broken\n", encoding="utf-8")
        with pytest.raises(rc.InputError, match=r"рядок 3: зіпсований JSON"):
            rc.read_results(path)

    def test_duplicate_key_is_reported_with_both_lines(self, tmp_path: Path) -> None:
        path = write_jsonl(
            tmp_path / "a.jsonl",
            [rec("s1", TS, 1, HELLO), rec("s1", PK, 1, HELLO), rec("s1", TS, 1)],
        )
        with pytest.raises(rc.InputError, match=r"рядок 3: ключ .* вже є \(.*рядок 1\)"):
            rc.read_results(path)

    @pytest.mark.parametrize(
        "raw",
        [
            {"network": TS, "run": 1, "turns": []},
            {"id": "s", "run": 1, "turns": []},
            {"id": "s", "network": TS, "turns": []},
            {"id": "s", "network": TS, "run": 0, "turns": []},
            {"id": "s", "network": TS, "run": 1},
            {"id": "s", "network": TS, "run": 1, "turns": [{"tool_calls": [{"args": {}}]}]},
            "not a record",
        ],
    )
    def test_malformed_record_is_an_input_error(self, tmp_path: Path, raw: Any) -> None:
        path = tmp_path / "a.jsonl"
        path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        with pytest.raises(rc.InputError, match="рядок 1"):
            rc.read_results(path)

    def test_missing_file_is_an_input_error(self, tmp_path: Path) -> None:
        with pytest.raises(rc.InputError, match="не вдалося прочитати"):
            rc.read_results(tmp_path / "absent.jsonl")

    def test_goldset_json_out_is_readable(self, tmp_path: Path) -> None:
        goldset = [
            {
                "case": "delivery_cost",
                "network": PK,
                "repeat": 2,
                "passed": False,
                "turns": [
                    {
                        "user": "скільки коштує доставка?",
                        "response": "",
                        "tools": ["get_delivery_cost"],
                        "tool_calls": [{"name": "get_delivery_cost", "args": {"city": "Київ"}}],
                        "failed": [{"name": "turn_error", "passed": False, "detail": "timeout"}],
                    }
                ],
            },
            {
                "case": "stable_ok",
                "network": TS,
                "repeat": 1,
                "passed": False,
                "turns": [
                    {
                        "user": "добрий день",
                        "response": "Слухаю.",
                        "tools": [],
                        "tool_calls": [],
                        "failed": [{"name": "must_contain", "passed": False, "detail": "x"}],
                    }
                ],
            },
        ]
        path = tmp_path / "goldset.json"
        path.write_text(json.dumps(goldset, ensure_ascii=False, indent=2), encoding="utf-8")
        records = rc.read_results(path)
        assert [rc.record_key(r) for r in records] == [
            ("delivery_cost", PK, 2),
            ("stable_ok", TS, 1),
        ]
        assert [rc.has_error(r) for r in records] == [True, False]
        assert classes(rc.compare_files(records, records)) == {
            "delivery_cost": rc.ERROR,
            "stable_ok": rc.IDENTICAL,
        }


def test_importing_the_module_pulls_in_no_project_code() -> None:
    """It runs on a workstation over copied files: no ``src``, no settings, no SQLAlchemy."""
    probe = (
        "import sys, scripts.replay_compare\n"
        "heavy = ('src', 'sqlalchemy', 'pydantic', 'dotenv', 'scripts.run_goldset')\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in heavy or m in heavy))\n"
    )
    done = subprocess.run(
        [sys.executable, "-B", "-c", probe],
        cwd=REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert done.stdout.strip() == "[]"
