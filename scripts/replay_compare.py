r"""Compare replay results by the structural signature of every turn. Free: no LLM, no DB.

``scripts/replay_scenarios.py`` writes one JSON line per (scenario × network ×
run). This script reads such files and sorts the pairs into classes. It only
names the class — whether a difference matters is for the reader to decide:
two passes over the same code differ on their own, so a class is not a verdict.

Two modes:

    python -m scripts.replay_compare before.jsonl after.jsonl
    python -m scripts.replay_compare --within runs.jsonl

- two files: pairs are matched by the key ``(id, network, run)``, never by the
  line number — a result file is appended to, and after a resumed run its lines
  come in another order;
- ``--within``: the runs of one (scenario, network) inside a single file — how
  many of them gave the same signature.

Classes of a pair:

- ``identical`` — the signature and the reply texts are equal;
- ``text_only`` — the signature is equal, the wording differs;
- ``structural`` — the signature differs; the report names the field;
- ``error`` — a turn of either side ended with an LLM error;
- ``missing`` — the key is in one file only (``--within``: a single run).

The signature is what the code and the model decide, not how it is worded:

- ``tools`` — tool names in call order;
- ``tool_args`` — the arguments of every call, in call order;
- ``transfer_reason`` — the reason of ``transfer_to_operator``, if it was called;
- ``turn_count``, ``user``, ``error`` — the number of turns, the customer lines
  and the presence of an error.

``--ignore FIELD`` (repeatable) drops a noisy field from the signature:
``tools``, ``tool_args``, ``transfer_reason``, ``tool_args.<tool>`` or
``tool_args.<tool>.<arg>``. ``turn_count``, ``user`` and ``error`` cannot be
dropped. An unknown name, or a tool / argument that occurs nowhere in the
compared files, is an input error — a typo must not pass as an exclusion. The
report names the dropped fields in its header and in its last line.

Arguments are normalised before they are compared — key order and runs of
whitespace — and never lose a value: ``205/55 R16`` and ``205/55R16`` are two
different arguments.

The reader also takes the ``--json-out`` file of ``scripts/run_goldset.py``
(``case`` / ``repeat`` instead of ``id`` / ``run``).

Exit code: 0 — compared, whatever the classes; 2 — input error.

This module imports nothing from ``src``: it runs on a workstation over files
copied out of the container. Code shared with ``replay_scenarios`` lives here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IDENTICAL, TEXT_ONLY, STRUCTURAL, ERROR, MISSING = (
    "identical",
    "text_only",
    "structural",
    "error",
    "missing",
)
CLASSES: tuple[str, ...] = (IDENTICAL, TEXT_ONLY, STRUCTURAL, ERROR, MISSING)

F_TURN_COUNT, F_USER, F_ERROR = "turn_count", "user", "error"
F_TOOLS, F_TOOL_ARGS, F_TRANSFER = "tools", "tool_args", "transfer_reason"
#: Part of every signature: dropping one would compare things that are not comparable.
LOCKED_FIELDS: tuple[str, ...] = (F_TURN_COUNT, F_USER, F_ERROR)
OPTIONAL_FIELDS: tuple[str, ...] = (F_TOOLS, F_TOOL_ARGS, F_TRANSFER)

TRANSFER_TOOL = "transfer_to_operator"
#: The name of a failed turn in the goldset ``--json-out`` (``run_goldset.TURN_ERROR``).
GOLDSET_TURN_ERROR = "turn_error"

EXIT_OK, EXIT_INPUT = 0, 2

Key = tuple[str, str, int]

_TOOL_ARG_FIELD_RE = re.compile(r"^tool_args\.([A-Za-z0-9_]+)(?:\.([A-Za-z0-9_]+))?$")


class InputError(ValueError):
    """The input (a file, a line, an option) cannot be used; nothing was compared or played."""


# ── Result records ────────────────────────────────────────────────────────


def record_key(record: dict[str, Any]) -> Key:
    """``(id, network, run)`` — what identifies a played pair in a result file."""
    return (record["id"], record["network"], record["run"])


def transfer_reason(tool_calls: list[dict[str, Any]]) -> str | None:
    """The reason of the first ``transfer_to_operator`` call; ``None`` when there was none."""
    for call in tool_calls:
        if call.get("name") == TRANSFER_TOOL:
            return str((call.get("args") or {}).get("reason") or "")
    return None


def _normalize_turn(where: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InputError(f"{where}: хід має бути об'єктом")
    calls = raw.get("tool_calls") or []
    if not isinstance(calls, list) or not all(
        isinstance(c, dict) and isinstance(c.get("name"), str) for c in calls
    ):
        raise InputError(f"{where}: tool_calls має бути списком {{name, args}}")
    error = raw.get("error")
    if not error:
        # The goldset report has no `error` field: a failed turn is an assertion by that name.
        failed = [f for f in raw.get("failed") or [] if isinstance(f, dict)]
        error = next(
            (
                f.get("detail") or GOLDSET_TURN_ERROR
                for f in failed
                if f.get("name") == GOLDSET_TURN_ERROR
            ),
            None,
        )
    return {
        **raw,
        "user": str(raw.get("user") or ""),
        "response": str(raw.get("response") or ""),
        "tool_calls": [{"name": c["name"], "args": c.get("args") or {}} for c in calls],
        "error": error or None,
    }


def normalize_record(where: str, raw: Any) -> dict[str, Any]:
    """Validate one result record; ``case`` / ``repeat`` of the goldset report are accepted."""
    if not isinstance(raw, dict):
        raise InputError(f"{where}: запис має бути об'єктом")
    rid = raw.get("id", raw.get("case"))
    run = raw.get("run", raw.get("repeat"))
    network = raw.get("network")
    if not isinstance(rid, str) or not rid:
        raise InputError(f"{where}: немає id")
    if not isinstance(network, str) or not network:
        raise InputError(f"{where}: немає network")
    if not isinstance(run, int) or isinstance(run, bool) or run < 1:
        raise InputError(f"{where}: run має бути цілим числом від 1")
    turns = raw.get("turns")
    if not isinstance(turns, list):
        raise InputError(f"{where}: turns має бути списком")
    return {
        **raw,
        "id": rid,
        "network": network,
        "run": run,
        "turns": [_normalize_turn(f"{where}, хід {n}", t) for n, t in enumerate(turns, 1)],
    }


def read_results(path: Path) -> list[dict[str, Any]]:
    """Every record of a result file, in file order. Raises ``InputError`` with the line number.

    A file is JSON Lines; a file that starts with ``[`` is read as one JSON
    array (the goldset ``--json-out``). A key met twice is an input error.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"{path}: не вдалося прочитати файл ({exc})") from exc
    numbered: list[tuple[str, Any]] = []
    if text.lstrip().startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InputError(f"{path}: зіпсований JSON ({exc})") from exc
        numbered = [(f"{path}: запис {n}", raw) for n, raw in enumerate(data, 1)]
    else:
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                numbered.append((f"{path}: рядок {number}", json.loads(line)))
            except json.JSONDecodeError as exc:
                raise InputError(f"{path}: рядок {number}: зіпсований JSON ({exc})") from exc
    records: list[dict[str, Any]] = []
    seen: dict[Key, str] = {}
    for where, raw in numbered:
        record = normalize_record(where, raw)
        key = record_key(record)
        if key in seen:
            raise InputError(f"{where}: ключ {key} вже є ({seen[key]})")
        seen[key] = where
        records.append(record)
    return records


def index_by_key(records: list[dict[str, Any]]) -> dict[Key, dict[str, Any]]:
    return {record_key(record): record for record in records}


# ── Signature ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ignore:
    """What ``--ignore`` drops from the signature."""

    fields: frozenset[str] = frozenset()
    tools: frozenset[str] = frozenset()  # tool_args.<tool>
    args: frozenset[tuple[str, str]] = frozenset()  # tool_args.<tool>.<arg>

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                [
                    *self.fields,
                    *(f"{F_TOOL_ARGS}.{t}" for t in self.tools),
                    *(f"{F_TOOL_ARGS}.{t}.{a}" for t, a in self.args),
                ]
            )
        )

    @property
    def label(self) -> str:
        return ", ".join(self.names) if self.names else "немає"


NO_IGNORE = Ignore()


def parse_ignore(names: list[str]) -> Ignore:
    """``--ignore`` values as an ``Ignore``. A locked or an unknown name is an ``InputError``."""
    fields: set[str] = set()
    tools: set[str] = set()
    args: set[tuple[str, str]] = set()
    for name in names:
        if name in LOCKED_FIELDS:
            raise InputError(f"поле {name!r} виключити не можна")
        if name in OPTIONAL_FIELDS:
            fields.add(name)
            continue
        match = _TOOL_ARG_FIELD_RE.match(name)
        if not match:
            known = [*OPTIONAL_FIELDS, f"{F_TOOL_ARGS}.<tool>", f"{F_TOOL_ARGS}.<tool>.<arg>"]
            raise InputError(f"невідоме поле підпису {name!r}; відомі: {', '.join(known)}")
        if match.group(2):
            args.add((match.group(1), match.group(2)))
        else:
            tools.add(match.group(1))
    return Ignore(frozenset(fields), frozenset(tools), frozenset(args))


def check_ignore_seen(ignore: Ignore, records: list[dict[str, Any]]) -> None:
    """A ``tool_args.<tool>[.<arg>]`` exclusion must name something the files contain."""
    seen_tools: set[str] = set()
    seen_args: set[tuple[str, str]] = set()
    for record in records:
        for turn in record["turns"]:
            for call in turn["tool_calls"]:
                seen_tools.add(call["name"])
                seen_args.update((call["name"], arg) for arg in call["args"])
    for tool in sorted(ignore.tools - seen_tools):
        raise InputError(
            f"поле {F_TOOL_ARGS}.{tool}: такого tool у файлах немає "
            f"(є: {', '.join(sorted(seen_tools)) or 'жодного'})"
        )
    for tool, arg in sorted(ignore.args - seen_args):
        known = sorted(a for t, a in seen_args if t == tool)
        raise InputError(
            f"поле {F_TOOL_ARGS}.{tool}.{arg}: такого аргументу у файлах немає "
            f"(у {tool} є: {', '.join(known) or 'жодного'})"
        )


def _squeeze(value: Any) -> Any:
    """Runs of whitespace in strings become one space; nothing else changes."""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return {str(k): _squeeze(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_squeeze(v) for v in value]
    return value


def canonical_args(args: dict[str, Any]) -> str:
    """Arguments as one string: key order and whitespace runs normalised, values kept."""
    return json.dumps(
        _squeeze(args), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )


def turn_signature(turn: dict[str, Any], ignore: Ignore = NO_IGNORE) -> dict[str, Any]:
    """What was decided on the turn, field by field; the wording of the reply is not here."""
    calls = turn["tool_calls"]
    sig: dict[str, Any] = {F_USER: _squeeze(turn["user"]), F_ERROR: bool(turn["error"])}
    if F_TOOLS not in ignore.fields:
        sig[F_TOOLS] = [c["name"] for c in calls]
    if F_TOOL_ARGS not in ignore.fields:
        sig[F_TOOL_ARGS] = [
            None
            if c["name"] in ignore.tools
            else canonical_args(
                {k: v for k, v in c["args"].items() if (c["name"], k) not in ignore.args}
            )
            for c in calls
        ]
    if F_TRANSFER not in ignore.fields:
        sig[F_TRANSFER] = transfer_reason(calls)
    return sig


def record_signature(record: dict[str, Any], ignore: Ignore = NO_IGNORE) -> list[dict[str, Any]]:
    return [turn_signature(turn, ignore) for turn in record["turns"]]


def has_error(record: dict[str, Any]) -> bool:
    return any(turn["error"] for turn in record["turns"])


def _texts(record: dict[str, Any]) -> list[str]:
    return [_squeeze(turn["response"]) for turn in record["turns"]]


@dataclass(frozen=True)
class Divergence:
    """One field of one turn that differs; ``turn`` is 0 for ``turn_count``."""

    field: str
    turn: int
    a: Any
    b: Any


def diff_signatures(
    a: dict[str, Any], b: dict[str, Any], ignore: Ignore = NO_IGNORE
) -> list[Divergence]:
    """Every (turn, field) where the two records' signatures differ."""
    sig_a, sig_b = record_signature(a, ignore), record_signature(b, ignore)
    if len(sig_a) != len(sig_b):
        return [Divergence(F_TURN_COUNT, 0, len(sig_a), len(sig_b))]
    return [
        Divergence(name, number, turn_a[name], turn_b[name])
        for number, (turn_a, turn_b) in enumerate(zip(sig_a, sig_b, strict=True), 1)
        for name in turn_a
        if turn_a[name] != turn_b[name]
    ]


# ── Two files ─────────────────────────────────────────────────────────────


@dataclass
class PairResult:
    key: Key
    cls: str
    divergences: tuple[Divergence, ...] = ()
    #: For ``missing``: the side that has the record, "A" or "B".
    present_in: str = ""

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(d.field for d in self.divergences))


def compare_pair(
    key: Key, a: dict[str, Any] | None, b: dict[str, Any] | None, ignore: Ignore = NO_IGNORE
) -> PairResult:
    """The class of one pair. An absent side wins over an error, an error over a difference."""
    if a is None or b is None:
        return PairResult(key, MISSING, present_in="B" if a is None else "A")
    if has_error(a) or has_error(b):
        return PairResult(key, ERROR)
    divergences = diff_signatures(a, b, ignore)
    if divergences:
        return PairResult(key, STRUCTURAL, tuple(divergences))
    if _texts(a) != _texts(b):
        return PairResult(key, TEXT_ONLY)
    return PairResult(key, IDENTICAL)


def compare_files(
    a_records: list[dict[str, Any]], b_records: list[dict[str, Any]], ignore: Ignore = NO_IGNORE
) -> list[PairResult]:
    """One ``PairResult`` per key of either file, in key order."""
    a, b = index_by_key(a_records), index_by_key(b_records)
    return [compare_pair(key, a.get(key), b.get(key), ignore) for key in sorted(set(a) | set(b))]


def _share(part: int, total: int) -> str:
    return f"{100 * part / total:.1f} %" if total else "—"


def _show(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _header(ignore: Ignore) -> str:
    kept = [f for f in OPTIONAL_FIELDS if f not in ignore.fields]
    return f"Поля підпису: {', '.join([*LOCKED_FIELDS, *kept])}; виключені поля: {ignore.label}"


def _text_lines(a: dict[str, Any], b: dict[str, Any], labels: tuple[str, str]) -> list[str]:
    lines: list[str] = []
    for number in range(1, max(len(a["turns"]), len(b["turns"])) + 1):
        turn_a = a["turns"][number - 1] if number <= len(a["turns"]) else None
        turn_b = b["turns"][number - 1] if number <= len(b["turns"]) else None
        user = (turn_a or turn_b or {}).get("user", "")
        lines.append(f"      хід {number} клієнт: {user}")
        for label, turn in zip(labels, (turn_a, turn_b), strict=True):
            lines.append(f"        {label}: {turn['response'] if turn else '(ходу немає)'}")
    return lines


def format_pair_report(
    results: list[PairResult],
    a_records: list[dict[str, Any]],
    b_records: list[dict[str, Any]],
    names: tuple[str, str] = ("A", "B"),
    ignore: Ignore = NO_IGNORE,
    show_text: bool = False,
) -> str:
    """The report of the two-file mode: classes, diverging fields, the structural pairs."""
    total = len(results)
    count = Counter(r.cls for r in results)
    lines = [f"Порівняння: A = {names[0]}; B = {names[1]}", _header(ignore), f"Пар: {total}"]
    for cls in CLASSES:
        extra = ""
        if cls == MISSING and count[cls]:
            only_a = sum(1 for r in results if r.cls == MISSING and r.present_in == "A")
            extra = f" (лише в A: {only_a}, лише в B: {count[cls] - only_a})"
        lines.append(f"  {cls}: {count[cls]} ({_share(count[cls], total)}){extra}")
    networks = sorted({r.key[1] for r in results})
    if len(networks) > 1:
        for net in networks:
            net_count = Counter(r.cls for r in results if r.key[1] == net)
            lines.append(f"  {net}: " + ", ".join(f"{cls} {net_count[cls]}" for cls in CLASSES))
    by_field = Counter(f for r in results for f in r.fields)
    if by_field:
        lines.append("Розбіжності за полями (пар):")
        lines.extend(f"  {name}: {n}" for name, n in by_field.most_common())
    a, b = index_by_key(a_records), index_by_key(b_records)
    listed = [r for r in results if r.cls == STRUCTURAL]
    if listed:
        lines.append("Пари structural:")
    for r in listed:
        sid, net, run = r.key
        lines.append(f"  {sid} [{net}] run {run}: {', '.join(r.fields)}")
        for d in r.divergences:
            turn = f"хід {d.turn} " if d.turn else ""
            lines.append(f"    {turn}{d.field}: A={_show(d.a)} B={_show(d.b)}")
        if show_text:
            lines.extend(_text_lines(a[r.key], b[r.key], ("A", "B")))
    if show_text:
        worded = [r for r in results if r.cls == TEXT_ONLY]
        if worded:
            lines.append("Пари text_only:")
        for r in worded:
            sid, net, run = r.key
            lines.append(f"  {sid} [{net}] run {run}")
            lines.extend(_text_lines(a[r.key], b[r.key], ("A", "B")))
    lines.append(
        f"Підсумок: пар {total}; structural {count[STRUCTURAL]} ({_share(count[STRUCTURAL], total)}); "
        f"text_only {count[TEXT_ONLY]}; identical {count[IDENTICAL]}; error {count[ERROR]}; "
        f"missing {count[MISSING]}; виключені поля: {ignore.label}"
    )
    return "\n".join(lines)


# ── Runs inside one file ──────────────────────────────────────────────────


@dataclass
class GroupResult:
    """The runs of one (scenario, network) inside one file."""

    scenario: str
    network: str
    cls: str
    runs: tuple[int, ...]
    #: Runs without an LLM error — only they have a signature to compare.
    clean: int
    #: The size of the largest set of clean runs with one and the same signature.
    agree: int
    #: Fields in which the other runs differ from that largest set.
    fields: tuple[str, ...] = ()


def compare_within(records: list[dict[str, Any]], ignore: Ignore = NO_IGNORE) -> list[GroupResult]:
    """One ``GroupResult`` per (scenario, network): how many runs gave the same signature."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in sorted(records, key=record_key):
        groups.setdefault((record["id"], record["network"]), []).append(record)
    results: list[GroupResult] = []
    for (sid, net), runs in groups.items():
        clean = [r for r in runs if not has_error(r)]
        by_sig = Counter(_show(record_signature(r, ignore)) for r in clean)
        agree = by_sig.most_common(1)[0][1] if by_sig else 0
        fields: list[str] = []
        if len(by_sig) > 1:
            top = by_sig.most_common(1)[0][0]
            base = next(r for r in clean if _show(record_signature(r, ignore)) == top)
            for other in clean:
                fields.extend(d.field for d in diff_signatures(base, other, ignore))
        if len(clean) < len(runs):
            cls = ERROR
        elif len(runs) < 2:
            cls = MISSING
        elif len(by_sig) > 1:
            cls = STRUCTURAL
        elif len({_show(_texts(r)) for r in clean}) > 1:
            cls = TEXT_ONLY
        else:
            cls = IDENTICAL
        results.append(
            GroupResult(
                scenario=sid,
                network=net,
                cls=cls,
                runs=tuple(r["run"] for r in runs),
                clean=len(clean),
                agree=agree,
                fields=tuple(dict.fromkeys(fields)),
            )
        )
    return results


def format_within_report(
    groups: list[GroupResult],
    records: list[dict[str, Any]],
    name: str = "A",
    ignore: Ignore = NO_IGNORE,
    show_text: bool = False,
) -> str:
    """The report of ``--within``: classes of the groups and «k з N» for the unequal ones."""
    total = len(groups)
    count = Counter(g.cls for g in groups)
    lines = [
        f"Повтори всередині файлу: {name}",
        _header(ignore),
        f"Груп (сценарій × мережа): {total}; записів: {len(records)}",
    ]
    for cls in CLASSES:
        note = " (у групі один повтор — порівнювати нема з чим)" if cls == MISSING else ""
        lines.append(
            f"  {cls}: {count[cls]} ({_share(count[cls], total)}){note if count[cls] else ''}"
        )
    by_field = Counter(f for g in groups for f in g.fields)
    if by_field:
        lines.append("Розбіжності за полями (груп):")
        lines.extend(f"  {field_name}: {n}" for field_name, n in by_field.most_common())
    by_key = index_by_key(records)
    listed = [g for g in groups if g.cls in (STRUCTURAL, ERROR, MISSING)]
    if listed:
        lines.append("Групи structural / error / missing:")
    for g in listed:
        with_error = len(g.runs) - g.clean
        lines.append(
            f"  {g.scenario} [{g.network}] {g.cls}: однаковий підпис {g.agree} з {len(g.runs)}"
            + (f"; з помилкою ходу: {with_error}" if with_error else "")
            + (f"; поля: {', '.join(g.fields)}" if g.fields else "")
        )
        if show_text:
            for run in g.runs:
                record = by_key[(g.scenario, g.network, run)]
                lines.append(f"    run {run}:")
                for number, turn in enumerate(record["turns"], 1):
                    lines.append(f"      хід {number} клієнт: {turn['user']}")
                    lines.append(f"        бот: {turn['response']}")
                    lines.append(f"        tools: {_show(turn['tool_calls'])}")
    lines.append(
        f"Підсумок: груп {total}; structural {count[STRUCTURAL]} ({_share(count[STRUCTURAL], total)}); "
        f"text_only {count[TEXT_ONLY]}; identical {count[IDENTICAL]}; error {count[ERROR]}; "
        f"missing {count[MISSING]}; виключені поля: {ignore.label}"
    )
    return "\n".join(lines)


# ── Command line ──────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="result file(s), JSON Lines")
    parser.add_argument(
        "--within", action="store_true", help="compare the runs inside one file with each other"
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="FIELD",
        help="drop a field from the signature; repeatable (see the list above)",
    )
    parser.add_argument(
        "--show-text", action="store_true", help="print the customer lines and the replies"
    )
    opts = parser.parse_args(argv)

    try:
        wanted = 1 if opts.within else 2
        if len(opts.files) != wanted:
            mode = "--within" if opts.within else "порівняння двох прогонів"
            raise InputError(f"{mode}: потрібно файлів: {wanted}, передано: {len(opts.files)}")
        ignore = parse_ignore(opts.ignore)
        loaded = [read_results(Path(name)) for name in opts.files]
        check_ignore_seen(ignore, [record for records in loaded for record in records])
    except InputError as exc:
        print(f"Помилка входу: {exc}", file=sys.stderr)
        return EXIT_INPUT

    if opts.within:
        groups = compare_within(loaded[0], ignore)
        print(format_within_report(groups, loaded[0], opts.files[0], ignore, opts.show_text))
    else:
        results = compare_files(loaded[0], loaded[1], ignore)
        print(
            format_pair_report(
                results,
                loaded[0],
                loaded[1],
                (opts.files[0], opts.files[1]),
                ignore,
                opts.show_text,
            )
        )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
