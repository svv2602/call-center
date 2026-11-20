r"""Replay scenarios — customer lines with no expectations — through the sandbox agent.

A scenario is a short list of customer lines. Every (scenario × network × run)
is played through the real LLM loop with mock tools — the agent the goldset
uses (``scripts/run_goldset.py``, ``src/sandbox/agent_runner.py``) — and what
the bot did on every turn is appended to the result file as one JSON line.
Nothing is asserted here: two result files are compared afterwards, for free,
by ``scripts/replay_compare.py``.

Scenario file, JSON Lines (the tshina ``tireconsultant:simulate`` format):

    {"id": "s001", "locale": "ua", "turns": ["перша репліка", "друга репліка"]}

``id`` is unique, ``locale`` is ``ua`` or ``ru``, ``turns`` is a non-empty list
of non-empty strings; a truthy ``skip`` leaves the scenario out; any other field
is ignored. A broken line stops the run before it starts, with the line number.

Usage (inside the call-processor container: DB for the prompt, provider keys;
the container has no ``tests/`` — copy the set in and name it):

    python -m scripts.replay_scenarios --scenarios /tmp/set.jsonl --out /tmp/a.jsonl \
        --network tvoya-shina --sales-enabled on --cap-usd 5 --validate   # free, no DB
    python -m scripts.replay_scenarios ... --cap-usd 5          # the plan and the cost, stops
    python -m scripts.replay_scenarios ... --cap-usd 5 --yes    # the paid run

- ``run`` in a result line is the number of the pass over the set (``--runs N``),
  from 1. It is not the ``Run`` of the goldset harness (a case × network pair).
- The result file is appended to; a pair whose key ``(id, network, run)`` is
  already there is skipped, so the same command continues an interrupted run.
- The cap: the run stops before the next pair once the spend — the lines already
  in the file included — reaches 80 % of ``--cap-usd``. The spend is an estimate
  by ``PRICES`` of the goldset harness over the tokens the turns report; prompt
  caching is not subtracted, so it is a ceiling, not the provider's bill.
- A pair in which a turn ended with an LLM error is NOT written — it would
  poison the comparison — and is played again by the next call. Several such
  pairs in a row (``--max-consecutive-errors``) stop the run.
- The result file holds the customer lines. For a set made of real calls it must
  live outside the repository; a path inside the repository is refused.

Exit codes: 0 — every pair is in the file (or nothing was to be paid for);
2 — input error; 3 — stopped by the cap; 4 — pairs were lost to LLM errors.
When several apply, the first of 2, 3, 4 wins.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.replay_compare import InputError, Key, read_results, record_key, transfer_reason
from scripts.run_goldset import (
    DEFAULT_INPUT_TOKENS_PER_TURN,
    DEFAULT_OUTPUT_TOKENS_PER_TURN,
    NETWORKS,
    PRICES,
    AgentFactory,
    Case,
    Turn,
    TurnObservation,
    TurnPlayer,
)

LOCALES: tuple[str, ...] = ("ua", "ru")
REPO_ROOT = Path(__file__).resolve().parent.parent

EXIT_OK, EXIT_INPUT, EXIT_CAP, EXIT_LLM = 0, 2, 3, 4
#: The run stops before the next pair once the spend reaches this share of the cap.
CAP_STOP_SHARE = 0.8
DEFAULT_MAX_CONSECUTIVE_ERRORS = 3


# ── Scenarios ─────────────────────────────────────────────────────────────


@dataclass
class Scenario:
    id: str
    locale: str
    turns: list[str]


def load_scenarios(path: Path) -> tuple[list[Scenario], list[str]]:
    """(scenarios to play, ids skipped by ``skip``). Raises ``InputError`` with the line number."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"{path}: не вдалося прочитати файл ({exc})") from exc
    scenarios: list[Scenario] = []
    skipped: list[str] = []
    seen: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        where = f"{path}: рядок {number}"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError(f"{where}: зіпсований JSON ({exc})") from exc
        if not isinstance(raw, dict):
            raise InputError(f"{where}: сценарій має бути об'єктом")
        sid = raw.get("id")
        if not isinstance(sid, str) or not sid.strip():
            raise InputError(f"{where}: немає id")
        if sid in seen:
            raise InputError(f"{where}: дубль id {sid!r} (вперше — рядок {seen[sid]})")
        seen[sid] = number
        if raw.get("skip"):
            skipped.append(sid)
            continue
        locale = raw.get("locale")
        if locale not in LOCALES:
            raise InputError(f"{where}: невідома локаль {locale!r}, потрібна одна з {LOCALES}")
        turns = raw.get("turns")
        if not isinstance(turns, list) or not turns:
            raise InputError(f"{where}: turns має бути непорожнім списком реплік")
        if not all(isinstance(t, str) and t.strip() for t in turns):
            raise InputError(f"{where}: кожна репліка в turns — непорожній рядок")
        scenarios.append(Scenario(id=sid, locale=locale, turns=list(turns)))
    if not scenarios:
        raise InputError(f"{path}: жодного сценарію для програвання")
    return scenarios, skipped


# ── Cost ──────────────────────────────────────────────────────────────────


def price_for(model: str) -> tuple[float, float] | None:
    """$/1M tokens (input, output) by ``PRICES`` of the goldset harness, matched as it does."""
    return next((p for key, p in PRICES.items() if key in model), None)


def cost_usd(price: tuple[float, float], input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


# ── Replay ────────────────────────────────────────────────────────────────


@dataclass
class Wiring:
    """What a run needs from the outside: the agent, the turn, the model of each network."""

    make_agent: AgentFactory
    play_turn: TurnPlayer
    #: network -> model name, for the price.
    models: dict[str, str]


@dataclass
class Pending:
    scenario: Scenario
    network: str
    run: int

    @property
    def key(self) -> Key:
        return (self.scenario.id, self.network, self.run)


def pending_pairs(
    scenarios: list[Scenario], networks: tuple[str, ...], runs: int, done: set[Key]
) -> list[Pending]:
    """Every pair not yet in the result file. A run is a whole pass over the set."""
    pairs = [
        Pending(scenario, network, run)
        for run in range(1, runs + 1)
        for network in networks
        for scenario in scenarios
    ]
    return [p for p in pairs if p.key not in done]


def _turn_record(user: str, obs: TurnObservation) -> dict[str, Any]:
    calls = [{"name": c.name, "args": c.args} for c in obs.tool_calls]
    return {
        "user": user,
        "response": obs.response_text,
        "tool_calls": calls,
        "transfer_reason": transfer_reason(calls),
        "error": obs.error,
        "input_tokens": obs.input_tokens,
        "output_tokens": obs.output_tokens,
    }


async def play_pair(
    pair: Pending, sales_enabled: bool, wiring: Wiring
) -> tuple[list[dict[str, Any]], str | None]:
    """Play one pair with a fresh agent and an empty history: (turn records, the LLM error).

    Stops at the first turn that ended with an error: the rest of the dialogue
    would be paid for and thrown away.
    """
    case = Case(
        id=pair.scenario.id,
        networks=(pair.network,),
        sales_enabled=sales_enabled,
        turns=[Turn(user=line) for line in pair.scenario.turns],
    )
    agent = await wiring.make_agent(pair.network, case)
    history: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    for line in pair.scenario.turns:
        obs, history = await wiring.play_turn(agent, line, history)
        turns.append(_turn_record(line, obs))
        if obs.error:
            return turns, obs.error
    return turns, None


def _append_line(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as out:
        # default=str: an argument json cannot encode must not cost a paid pair its line.
        out.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


async def replay(
    pairs: list[Pending],
    *,
    out_path: Path,
    sales_enabled: bool,
    wiring: Wiring,
    cap_usd: float,
    spent_usd: float,
    max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
    echo: Callable[[str], None] = print,
) -> int:
    """Play ``pairs`` in order, one result line per clean pair. Returns the exit code.

    ``spent_usd`` is what the result file already holds. The cost of a pair
    lost to an LLM error counts towards the cap for the rest of this call,
    though it is not written anywhere.
    """
    prices = {net: price_for(model) for net, model in wiring.models.items()}
    written = lost = in_a_row = 0
    stopped_by_cap = False
    for pair in pairs:
        if spent_usd >= CAP_STOP_SHARE * cap_usd:
            stopped_by_cap = True
            echo(
                f"Стоп за капом: витрачено ≈ ${spent_usd:.2f} — це {CAP_STOP_SHARE:.0%} "
                f"від ${cap_usd:.2f} або більше."
            )
            break
        price = prices[pair.network]
        if price is None:
            raise InputError(f"немає ціни для моделі мережі {pair.network}")
        turns, error = await play_pair(pair, sales_enabled, wiring)
        input_tokens = sum(t["input_tokens"] for t in turns)
        output_tokens = sum(t["output_tokens"] for t in turns)
        pair_usd = cost_usd(price, input_tokens, output_tokens)
        spent_usd += pair_usd
        sid, net, run = pair.key
        if error:
            lost += 1
            in_a_row += 1
            echo(f"  ЗБІЙ LLM {sid} [{net}] run {run}: {error} — пару не записано")
            if in_a_row >= max_consecutive_errors:
                echo(f"Стоп: збій LLM у {in_a_row} парах поспіль.")
                break
            continue
        in_a_row = 0
        _append_line(
            out_path,
            {
                "id": sid,
                "network": net,
                "run": run,
                "locale": pair.scenario.locale,
                "sales_enabled": sales_enabled,
                "model": wiring.models[net],
                "played_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "turns": turns,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(pair_usd, 6),
            },
        )
        written += 1
        echo(f"  OK {sid} [{net}] run {run} ≈ ${pair_usd:.4f}")
    echo(
        f"Записано пар: {written}; не записано через збій LLM: {lost}; "
        f"не зіграно: {len(pairs) - written - lost}; витрачено ≈ ${spent_usd:.2f} з ${cap_usd:.2f} "
        "(оцінка за PRICES, верхня межа)"
    )
    if stopped_by_cap:
        return EXIT_CAP
    return EXIT_LLM if lost else EXIT_OK


# ── Live wiring (paid) ────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def live_wiring(opts: argparse.Namespace, networks: tuple[str, ...]) -> AsyncIterator[Wiring]:
    """The sandbox agent of the goldset harness: DB for the prompt, router, provider keys."""
    from sqlalchemy.ext.asyncio import create_async_engine

    from scripts.run_goldset import SandboxAgentFactory, _load_tenants, _play_sandbox_turn
    from src.config import get_settings
    from src.llm.router import LLMRouter

    engine = create_async_engine(get_settings().database.url, pool_size=2)
    router = LLMRouter()
    try:
        await router.initialize()
        tenants = await _load_tenants(engine)
        factory = SandboxAgentFactory(engine, tenants, router, opts.provider)
        models: dict[str, str] = {}
        for net in networks:
            provider = factory.provider_for(net)
            if not provider:
                raise InputError(f"{net}: у мережі немає провайдера — вкажіть --provider")
            if provider not in router.providers:
                raise InputError(f"провайдер {provider!r} не ініціалізовано (ключ API в env?)")
            models[net] = (router.config.get("providers", {}).get(provider) or {}).get(
                "model", provider
            )
        yield Wiring(make_agent=factory, play_turn=_play_sandbox_turn, models=models)
    finally:
        await router.close()
        await engine.dispose()


WiringOpener = Callable[
    [argparse.Namespace, tuple[str, ...]], contextlib.AbstractAsyncContextManager[Wiring]
]


async def _plan_and_replay(
    opts: argparse.Namespace,
    pairs: list[Pending],
    networks: tuple[str, ...],
    spent_usd: float,
    open_wiring: WiringOpener,
) -> int:
    async with open_wiring(opts, networks) as wiring:
        total_usd = 0.0
        for net in networks:
            model = wiring.models[net]
            price = price_for(model)
            if price is None:
                raise InputError(
                    f"{net}: для моделі {model!r} немає ціни в PRICES — кап не може працювати"
                )
            turns = sum(len(p.scenario.turns) for p in pairs if p.network == net)
            usd = turns * cost_usd(
                price, opts.input_tokens_per_turn, DEFAULT_OUTPUT_TOKENS_PER_TURN
            )
            total_usd += usd
            print(f"{net}: модель {model}, реплік {turns}, ≈ ${usd:.2f}")
        print(
            f"Разом ≈ ${total_usd:.2f} (оцінка, верхня межа; {opts.input_tokens_per_turn} input "
            f"tok/репліку); вже у файлі ≈ ${spent_usd:.2f}; кап ${opts.cap_usd:.2f}, "
            f"стоп на {CAP_STOP_SHARE:.0%} = ${CAP_STOP_SHARE * opts.cap_usd:.2f}"
        )
        if not opts.yes:
            print("Платний прогін не запущено: додайте --yes.")
            return EXIT_OK
        return await replay(
            pairs,
            out_path=Path(opts.out),
            sales_enabled=opts.sales_enabled == "on",
            wiring=wiring,
            cap_usd=opts.cap_usd,
            spent_usd=spent_usd,
            max_consecutive_errors=opts.max_consecutive_errors,
        )


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be 1 or more, got {number}")
    return number


def _positive_usd(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not number > 0:
        raise argparse.ArgumentTypeError(f"must be above 0, got {value}")
    return number


def _check_out_path(out: Path) -> None:
    if out.resolve().is_relative_to(REPO_ROOT):
        raise InputError(
            f"{out}: файл результату містить репліки клієнтів — вкажіть шлях поза репозиторієм "
            f"({REPO_ROOT})"
        )


def main(argv: list[str] | None = None, *, open_wiring: WiringOpener = live_wiring) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenarios", required=True, help="scenario set, JSON Lines")
    parser.add_argument(
        "--out", required=True, help="result file, appended to; outside the repository"
    )
    parser.add_argument("--network", required=True, choices=[*NETWORKS, "both"])
    parser.add_argument(
        "--sales-enabled", required=True, choices=["on", "off"], help="the tenant's sales_enabled"
    )
    parser.add_argument(
        "--cap-usd",
        required=True,
        type=_positive_usd,
        help="spend cap; the run stops at 80 %% of it",
    )
    parser.add_argument(
        "--runs", type=_positive_int, default=1, metavar="N", help="passes over the set (default 1)"
    )
    parser.add_argument(
        "--max-consecutive-errors",
        type=_positive_int,
        default=DEFAULT_MAX_CONSECUTIVE_ERRORS,
        metavar="N",
        help=f"stop after N pairs in a row lost to an LLM error (default {DEFAULT_MAX_CONSECUTIVE_ERRORS})",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM router provider key (default: tenant's agent_provider_override)",
    )
    parser.add_argument("--input-tokens-per-turn", type=int, default=DEFAULT_INPUT_TOKENS_PER_TURN)
    parser.add_argument(
        "--validate", action="store_true", help="only load the set and print the plan (free, no DB)"
    )
    parser.add_argument("--yes", action="store_true", help="run the paid LLM pass")
    opts = parser.parse_args(argv)

    networks = NETWORKS if opts.network == "both" else (opts.network,)
    sales_enabled = opts.sales_enabled == "on"
    out_path = Path(opts.out)
    try:
        _check_out_path(out_path)
        scenarios, skipped = load_scenarios(Path(opts.scenarios))
        recorded = read_results(out_path) if out_path.exists() else []
        other = [r for r in recorded if r.get("sales_enabled") is not sales_enabled]
        if other:
            raise InputError(
                f"{out_path}: у файлі вже є пари з іншим sales_enabled "
                f"(напр. {record_key(other[0])}) — для іншого режиму потрібен інший файл"
            )
        done = {record_key(r) for r in recorded}
        pairs = pending_pairs(scenarios, networks, opts.runs, done)
        spent_usd = sum(float(r.get("cost_usd") or 0.0) for r in recorded)
        total = len(scenarios) * len(networks) * opts.runs
        print(
            f"Сценаріїв: {len(scenarios)}; пропущено (skip): {len(skipped)}; мережі: "
            f"{', '.join(networks)}; повторів (run): {opts.runs}; sales_enabled: {opts.sales_enabled}"
        )
        print(
            f"Пар (сценарій × мережа × run): {total}; вже у файлі: {total - len(pairs)}; "
            f"до програвання: {len(pairs)} ({sum(len(p.scenario.turns) for p in pairs)} реплік)"
        )
        if opts.validate:
            return EXIT_OK
        if not pairs:
            print("Нічого програвати: усі пари вже у файлі.")
            return EXIT_OK
        return asyncio.run(_plan_and_replay(opts, pairs, networks, spent_usd, open_wiring))
    except InputError as exc:
        print(f"Помилка входу: {exc}", file=sys.stderr)
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
