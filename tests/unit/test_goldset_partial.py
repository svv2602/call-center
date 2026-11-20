"""A paid goldset run that breaks off keeps what was already played.

No LLM, no DB, no network: ``main`` runs for real, the sandbox is replaced by
the scripted one from ``test_goldset_repeats``. Files only in ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from scripts import run_goldset as rg
from tests.unit.test_goldset_repeats import (
    CASES_YAML,
    PAIRS,
    SCRIPT,
    ScriptedSandbox,
    played_runs,
    run_main,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def cases_dir(tmp_path: Path) -> Path:
    d = tmp_path / "cases"
    d.mkdir()
    (d / "cases.yaml").write_text(CASES_YAML, encoding="utf-8")
    return d


class BreakingSandbox(ScriptedSandbox):
    """Raises ``exc`` when the ``at``-th agent is built; snapshots the json file before each build."""

    def __init__(self, at: int, exc: BaseException | None, json_out: Path | None = None) -> None:
        super().__init__(SCRIPT)
        self.at, self.exc, self.json_out = at, exc, json_out
        self.on_disk: list[int | None] = []

    async def create_sandbox_agent(self, engine: Any, **kwargs: Any) -> Any:
        if self.json_out is not None:
            self.on_disk.append(
                len(json.loads(self.json_out.read_text("utf-8")))
                if self.json_out.exists()
                else None
            )
        if self.exc is not None and len(self.built) + 1 == self.at:
            raise self.exc
        return await super().create_sandbox_agent(engine, **kwargs)


class TestCheckpointAfterEveryPair:
    def test_file_holds_exactly_what_was_played_before_each_pair(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.json"
        sandbox = BreakingSandbox(at=99, exc=None, json_out=out)
        rc, _, _ = run_main(sandbox, cases_dir, "--yes", "--json-out", str(out))
        assert sandbox.on_disk == [None, 1, 2, 3, 4]
        assert len(json.loads(out.read_text("utf-8"))) == len(PAIRS)
        assert rc == 1  # pk_storage dies on a turn error: red, not aborted

    def test_each_checkpoint_is_whole_json_with_no_temp_left_behind(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.json"
        run_main(
            BreakingSandbox(at=99, exc=None, json_out=out),
            cases_dir,
            "--yes",
            "--json-out",
            str(out),
        )
        assert [p.name for p in tmp_path.iterdir() if p.name.startswith("out")] == ["out.json"]


class TestBreakOff:
    def test_error_on_third_pair_of_five(self, cases_dir: Path, tmp_path: Path) -> None:
        out = tmp_path / "out.json"
        sandbox = BreakingSandbox(at=3, exc=RuntimeError("provider boom"))
        rc, stdout, stderr = run_main(sandbox, cases_dir, "--yes", "--json-out", str(out))
        records = json.loads(out.read_text("utf-8"))
        assert [(r["case"], r["network"]) for r in records] == PAIRS[:2]
        first = stdout.splitlines()[
            stdout.splitlines().index(
                next(line for line in stdout.splitlines() if line.startswith("ПРОГІН ПЕРЕРВАНО"))
            )
        ]
        assert "зіграно 2 з 5" in first and "не зіграно 3" in first
        assert "Зелёних кейсів: 2/2" in stdout  # the report of the played part
        assert rc == rg.EXIT_ABORTED
        assert rc not in (0, 1, 2)
        assert "RuntimeError" in stderr and "provider boom" in stderr  # the cause is not swallowed

    def test_user_interrupt_and_cancel_also_report_and_are_not_silent(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        for exc in (KeyboardInterrupt(), asyncio.CancelledError()):
            out = tmp_path / f"{type(exc).__name__}.json"
            rc, stdout, stderr = run_main(
                BreakingSandbox(at=2, exc=exc), cases_dir, "--yes", "--json-out", str(out)
            )
            assert rc == rg.EXIT_ABORTED
            assert "зіграно 1 з 5" in stdout
            assert type(exc).__name__ in stderr
            assert len(json.loads(out.read_text("utf-8"))) == 1

    def test_abort_counts_repeats(self, cases_dir: Path) -> None:
        rc, stdout, _ = run_main(
            BreakingSandbox(at=4, exc=RuntimeError("x")), cases_dir, "--yes", "--repeat", "2"
        )
        assert rc == rg.EXIT_ABORTED
        assert "зіграно 3 з 10" in stdout

    def test_abort_without_json_out_still_reports(self, cases_dir: Path, tmp_path: Path) -> None:
        rc, stdout, _ = run_main(BreakingSandbox(at=3, exc=RuntimeError("x")), cases_dir, "--yes")
        assert rc == rg.EXIT_ABORTED and "ПРОГІН ПЕРЕРВАНО" in stdout
        assert list(tmp_path.glob("*.json")) == []


class TestAtomicWrite:
    def test_failed_write_leaves_the_previous_file_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cases_dir: Path
    ) -> None:
        target = tmp_path / "out.json"
        target.write_text('["previous"]', encoding="utf-8")
        results = asyncio.run(
            rg.run_all(
                played_runs(cases_dir)[0][:1],
                ScriptedSandbox(SCRIPT).make_agent,
                ScriptedSandbox(SCRIPT).play_turn,
            )
        )
        real = rg.Path.write_text

        def torn(self: Path, data: str, *a: Any, **k: Any) -> int:
            real(self, data[: len(data) // 2], *a, **k)  # half of it reaches the disk
            raise OSError("disk full")

        monkeypatch.setattr(rg.Path, "write_text", torn)
        with pytest.raises(OSError, match="disk full"):
            rg.write_json_atomic(target, results)
        assert json.loads(target.read_text("utf-8")) == ["previous"]
        assert [p.name for p in tmp_path.glob("out*")] == ["out.json"]

    def test_failed_checkpoint_warns_and_the_run_goes_on(
        self, cases_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def broken(path: Path, results: list[rg.RunResult]) -> None:
            calls.append(len(results))
            if len(calls) == 1:
                raise OSError("no space")

        monkeypatch.setattr(rg, "write_json_atomic", broken)
        rc, _, stderr = run_main(
            ScriptedSandbox(SCRIPT), cases_dir, "--yes", "--json-out", str(tmp_path / "o.json")
        )
        assert calls[:3] == [1, 2, 3] and "no space" in stderr
        assert rc == 1


class TestFullRunIsUnchanged:
    def test_file_equals_results_to_json_and_code_is_as_before(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        out = tmp_path / "out.json"
        rc, stdout, _ = run_main(
            ScriptedSandbox(SCRIPT), cases_dir, "--yes", "--json-out", str(out)
        )
        sandbox = ScriptedSandbox(SCRIPT)
        runs, _ = played_runs(cases_dir)
        results = asyncio.run(rg.run_all(runs, sandbox.make_agent, sandbox.play_turn))
        assert json.loads(out.read_text("utf-8")) == json.loads(
            json.dumps(rg.results_to_json(results), default=str)
        )
        assert rc == rg.exit_code(results) == 1
        assert "ПРОГІН ПЕРЕРВАНО" not in stdout

    def test_no_json_out_writes_nothing_and_run_all_without_handler_works(
        self, cases_dir: Path, tmp_path: Path
    ) -> None:
        run_main(ScriptedSandbox(SCRIPT), cases_dir, "--yes")
        assert list(tmp_path.glob("*.json")) == []
        assert not list(tmp_path.glob("*.tmp"))

    def test_on_result_sees_every_pair_in_order(self, cases_dir: Path) -> None:
        sandbox = ScriptedSandbox(SCRIPT)
        runs, _ = played_runs(cases_dir)
        seen: list[int] = []
        asyncio.run(
            rg.run_all(
                runs, sandbox.make_agent, sandbox.play_turn, on_result=lambda d: seen.append(len(d))
            )
        )
        assert seen == [1, 2, 3, 4, 5]
