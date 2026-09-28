"""Goldset for the voice bot: customer lines + expectations, played in both networks.

A case is a short dialogue (``turns``) with expectations per turn. Every case
is played through the real LLM loop with mock tools — the sandbox agent from
``src/sandbox/agent_runner.py``, which assembles the prompt, tools and tenant
overrides the way a live call does — once per network it names. ``network:
both`` is the default for anything a network's terms can change: the two
networks share one prompt, so the condition of one leaks into the other.

What decides a case is the whole case, not a share of assertions: the report
prints «зелёних кейсів N/M» over (case × network) runs and breaks the failures
down by assertion.

Assertions (per turn, see ``tests/goldset/schema.json``):

- ``must_contain`` / ``must_not_contain`` — regex, case-insensitive; write the
  ua and ru forms as alternation, the caller may speak either.
- ``tool_called`` / ``tool_not_called`` — by name, optionally with argument
  regexes: ``{name: transfer_to_operator, args: {reason: non_fitting_scope}}``.
- ``transfer_reason`` — shorthand for ``transfer_to_operator`` with that reason.
- ``max_sentences`` — the reply is spoken; long monologues lose the caller.
- ``network_leak`` — on by default for every turn: a phrase that belongs to
  another network (its name, its delivery terms, its services) must not be
  heard in this one.

Cases tied to a wave that has not landed carry ``pending: <checklist>`` and are
skipped with that reason in the report, never silently.

Usage (inside the call-processor container: DB for the prompt, provider keys):

    python -m scripts.run_goldset --validate            # schema only, free
    python -m scripts.run_goldset --network both        # prints the cost, stops
    python -m scripts.run_goldset --network prokoleso --case 'delivery_*' --yes

A paid run happens only with ``--yes``; without it the script prints the plan
and the cost estimate and exits.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import fnmatch
import json
import re
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NETWORKS: tuple[str, ...] = ("tvoya-shina", "prokoleso")

CASES_DIR = Path(__file__).resolve().parent.parent / "tests" / "goldset" / "cases"

# Phrases that belong to ONE network: heard in any other network's call they
# are a leak. Static until wave 1-A lands.
# TODO(wave-1-A NetworkPolicy): build this map from `NetworkPolicy` for each
# tenant (name, delivery terms, services, extended warranty) instead of the
# literal list; `foreign_phrases` is the single place to switch.
NETWORK_ONLY_PHRASES: dict[str, tuple[str, ...]] = {
    "tvoya-shina": (
        # The network's own name.
        r"тво[яєїюе]\w*\s+шин",
        # Free delivery is Tvoya Shina's term; Pro Koleso ships at carrier rates.
        r"безкоштовн\w*\s+(?:\w+\s+){0,3}доставк|доставк\w*\s+(?:\w+\s+){0,3}безкоштовн",
        r"бесплатн\w*\s+(?:\w+\s+){0,3}доставк|доставк\w*\s+(?:\w+\s+){0,3}бесплатн",
        # Extended Bridgestone warranty is Tvoya Shina only. Only the offer is a
        # leak: «розширеної гарантії немає» is a correct Pro Koleso answer.
        r"(?<!\w)(?:є|діє|надаємо|пропонуємо|оформ\w*)\s+(?:\w+\s+){0,2}розширен\w*\s+гаранті",
        r"розширен[аі]\s+гаранті[яю]\s+(?:\w+\s+){0,3}(?:до|на)\s+\d",
        r"(?<!\w)(?:есть|действует|предоставляем|оформ\w*)\s+(?:\w+\s+){0,2}расширенн\w*\s+гарант",
        # Fitting and storage are Tvoya Shina services: offering them is a leak.
        r"запи(?:шу|сую|шемо|сати|сатися|суємо)\s+(?:\w+\s+){0,2}на\s+(?:шино)?монтаж",
        r"(?:залиш|здат|прийм|зберіга)\w*\s+(?:\w+\s+){0,2}на\s+зберіганн",
    ),
    "prokoleso": (
        # The network's own name.
        r"про\s*колес",
        # Carrier tariffs are Pro Koleso's delivery term; Tvoya Shina is free.
        r"тариф\w*\s+перевізник|тариф\w*\s+перевозчик",
        # «Не надаємо» for fitting/storage is Pro Koleso's line; Tvoya Shina has both.
        r"(?:монтаж|зберіганн|хранени)\w*\s+(?:\w+\s+){0,3}не\s+(?:надаєм|надаем|предоставля)",
        r"не\s+(?:надаєм|надаем|предоставля)\w*\s+(?:\w+\s+){0,2}(?:шино)?(?:монтаж|зберіганн|хранени)",
    ),
}

ASSERTION_KEYS: tuple[str, ...] = (
    "must_contain",
    "must_not_contain",
    "tool_called",
    "tool_not_called",
    "transfer_reason",
    "max_sentences",
    "network_leak",
)
CASE_KEYS = frozenset(
    {"id", "title", "network", "sales_enabled", "pending", "source", "notes", "mocks", "turns"}
)
TURN_KEYS = frozenset({"user", "expect", "expect_by_network"})
_ID_RE = re.compile(r"^[a-z0-9_]+$")
_PENDING_RE = re.compile(r"^wave-\d+-[A-Z]-[a-z0-9-]+: \S")
_SENTENCE_END_RE = re.compile(r"[.!?…]+(?=\s|$)")
_FLAGS = re.IGNORECASE | re.UNICODE


# ── Case model ────────────────────────────────────────────────────────────


@dataclass
class Turn:
    user: str
    expect: dict[str, Any] = field(default_factory=dict)
    expect_by_network: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class Case:
    id: str
    networks: tuple[str, ...]
    sales_enabled: bool
    turns: list[Turn]
    pending: str | None = None
    source: str = ""
    mocks: dict[str, Any] = field(default_factory=dict)
    title: str = ""


class CaseError(ValueError):
    """A case file does not match the schema."""


def _check_expect(where: str, expect: Any) -> None:
    if not isinstance(expect, dict):
        raise CaseError(f"{where}: expect must be a mapping")
    unknown = set(expect) - set(ASSERTION_KEYS)
    if unknown:
        raise CaseError(f"{where}: unknown assertion(s) {sorted(unknown)}")
    for key in ("must_contain", "must_not_contain", "tool_not_called"):
        if key in expect and not (
            isinstance(expect[key], list) and all(isinstance(x, str) for x in expect[key])
        ):
            raise CaseError(f"{where}: {key} must be a list of strings")
    for key in ("must_contain", "must_not_contain"):
        for pattern in expect.get(key, []):
            try:
                re.compile(pattern, _FLAGS)
            except re.error as exc:
                raise CaseError(f"{where}: bad regex {pattern!r}: {exc}") from exc
    for spec in expect.get("tool_called", []) or []:
        if isinstance(spec, str):
            continue
        if not isinstance(spec, dict) or "name" not in spec or set(spec) - {"name", "args"}:
            raise CaseError(f"{where}: tool_called item must be a name or {{name, args}}")
    if "max_sentences" in expect and not (
        isinstance(expect["max_sentences"], int) and expect["max_sentences"] > 0
    ):
        raise CaseError(f"{where}: max_sentences must be a positive integer")
    if "network_leak" in expect and not isinstance(expect["network_leak"], bool):
        raise CaseError(f"{where}: network_leak must be true/false")
    if "transfer_reason" in expect and not isinstance(expect["transfer_reason"], str):
        raise CaseError(f"{where}: transfer_reason must be a string")


def parse_case(raw: Any) -> Case:
    """Validate one case mapping and build a ``Case``. Raises ``CaseError``."""
    if not isinstance(raw, dict):
        raise CaseError("case must be a mapping")
    cid = raw.get("id")
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        raise CaseError(f"bad id {cid!r}")
    unknown = set(raw) - CASE_KEYS
    if unknown:
        raise CaseError(f"{cid}: unknown key(s) {sorted(unknown)}")
    if not isinstance(raw.get("sales_enabled"), bool):
        raise CaseError(f"{cid}: sales_enabled (true/false) is required")

    network = raw.get("network", "both")
    if network == "both":
        networks = NETWORKS
    elif isinstance(network, list) and network and all(n in NETWORKS for n in network):
        networks = tuple(dict.fromkeys(network))
    else:
        raise CaseError(f"{cid}: network must be 'both' or a list from {NETWORKS}")

    pending = raw.get("pending")
    if pending is not None and not (isinstance(pending, str) and _PENDING_RE.match(pending)):
        raise CaseError(f"{cid}: pending must read '<wave-N-X-checklist>: <reason>'")

    mocks = raw.get("mocks") or {}
    if not isinstance(mocks, dict):
        raise CaseError(f"{cid}: mocks must be a mapping tool -> result")

    raw_turns = raw.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise CaseError(f"{cid}: turns must be a non-empty list")
    turns: list[Turn] = []
    for n, t in enumerate(raw_turns, 1):
        where = f"{cid} turn {n}"
        if not isinstance(t, dict) or not isinstance(t.get("user"), str) or not t["user"].strip():
            raise CaseError(f"{where}: user text is required")
        if set(t) - TURN_KEYS:
            raise CaseError(f"{where}: unknown key(s) {sorted(set(t) - TURN_KEYS)}")
        expect = t.get("expect") or {}
        _check_expect(where, expect)
        by_net = t.get("expect_by_network") or {}
        if not isinstance(by_net, dict) or set(by_net) - set(networks):
            raise CaseError(f"{where}: expect_by_network keys must be among {networks}")
        for net, e in by_net.items():
            _check_expect(f"{where} [{net}]", e)
        turns.append(Turn(user=t["user"], expect=expect, expect_by_network=by_net))

    return Case(
        id=cid,
        networks=networks,
        sales_enabled=raw["sales_enabled"],
        turns=turns,
        pending=pending,
        source=str(raw.get("source") or ""),
        mocks=mocks,
        title=str(raw.get("title") or ""),
    )


def load_cases(cases_dir: Path = CASES_DIR) -> list[Case]:
    """Load every ``*.yaml`` under ``cases_dir``; each file holds a list of cases."""
    import yaml  # PyYAML ships with uvicorn[standard]; local venv may lack it

    cases: list[Case] = []
    seen: set[str] = set()
    for path in sorted(cases_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except yaml.YAMLError as exc:
            raise CaseError(f"{path.name}: {exc}") from exc
        if not isinstance(data, list):
            raise CaseError(f"{path.name}: top level must be a list of cases")
        for raw in data:
            try:
                case = parse_case(raw)
            except CaseError as exc:
                raise CaseError(f"{path.name}: {exc}") from exc
            if case.id in seen:
                raise CaseError(f"{path.name}: duplicate id {case.id}")
            seen.add(case.id)
            cases.append(case)
    return cases


# ── Assertions ────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class TurnObservation:
    """What the bot did on one turn: the spoken text and the tool calls."""

    response_text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AssertionResult:
    name: str
    passed: bool
    detail: str = ""


def foreign_phrases(network: str) -> list[str]:
    """Phrases of every network other than ``network`` — a leak if heard here."""
    return [p for net, phrases in NETWORK_ONLY_PHRASES.items() if net != network for p in phrases]


def count_sentences(text: str) -> int:
    body = text.strip()
    if not body:
        return 0
    ends = len(_SENTENCE_END_RE.findall(body))
    tail = _SENTENCE_END_RE.split(body)[-1].strip()
    return ends + (1 if tail else 0)


def _call_matches(call: ToolCall, spec: str | dict[str, Any]) -> bool:
    if isinstance(spec, str):
        return call.name == spec
    if call.name != spec["name"]:
        return False
    for key, pattern in (spec.get("args") or {}).items():
        value = call.args.get(key)
        if value is None or not re.search(str(pattern), str(value), _FLAGS):
            return False
    return True


def check_must_contain(patterns: list[str], obs: TurnObservation) -> AssertionResult:
    missing = [p for p in patterns if not re.search(p, obs.response_text, _FLAGS)]
    return AssertionResult("must_contain", not missing, f"missing {missing}" if missing else "")


def check_must_not_contain(patterns: list[str], obs: TurnObservation) -> AssertionResult:
    found = [p for p in patterns if re.search(p, obs.response_text, _FLAGS)]
    return AssertionResult("must_not_contain", not found, f"found {found}" if found else "")


def check_tool_called(specs: list[Any], obs: TurnObservation) -> AssertionResult:
    missing = [s for s in specs if not any(_call_matches(c, s) for c in obs.tool_calls)]
    detail = f"missing {missing}; called {[c.name for c in obs.tool_calls]}" if missing else ""
    return AssertionResult("tool_called", not missing, detail)


def check_tool_not_called(names: list[str], obs: TurnObservation) -> AssertionResult:
    hit = sorted({c.name for c in obs.tool_calls if c.name in names})
    return AssertionResult("tool_not_called", not hit, f"called {hit}" if hit else "")


def check_transfer_reason(reason: str, obs: TurnObservation) -> AssertionResult:
    spec = {"name": "transfer_to_operator", "args": {"reason": f"^{re.escape(reason)}$"}}
    ok = any(_call_matches(c, spec) for c in obs.tool_calls)
    got = [c.args.get("reason") for c in obs.tool_calls if c.name == "transfer_to_operator"]
    return AssertionResult("transfer_reason", ok, "" if ok else f"want {reason}, got {got}")


def check_max_sentences(limit: int, obs: TurnObservation) -> AssertionResult:
    n = count_sentences(obs.response_text)
    return AssertionResult("max_sentences", n <= limit, f"{n} > {limit}" if n > limit else "")


def check_network_leak(network: str, obs: TurnObservation) -> AssertionResult:
    found = [p for p in foreign_phrases(network) if re.search(p, obs.response_text, _FLAGS)]
    return AssertionResult("network_leak", not found, f"{network}: {found}" if found else "")


def effective_expect(turn: Turn, network: str) -> dict[str, Any]:
    """Common expectations merged with this network's: lists add up, scalars override."""
    merged = copy.deepcopy(turn.expect)
    for key, value in (turn.expect_by_network.get(network) or {}).items():
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = merged[key] + value
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def evaluate_turn(
    expect: dict[str, Any], network: str, obs: TurnObservation
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    if obs.error:
        results.append(AssertionResult("turn_error", False, obs.error))
    if "must_contain" in expect:
        results.append(check_must_contain(expect["must_contain"], obs))
    if "must_not_contain" in expect:
        results.append(check_must_not_contain(expect["must_not_contain"], obs))
    if "tool_called" in expect:
        results.append(check_tool_called(expect["tool_called"], obs))
    if "tool_not_called" in expect:
        results.append(check_tool_not_called(expect["tool_not_called"], obs))
    if "transfer_reason" in expect:
        results.append(check_transfer_reason(expect["transfer_reason"], obs))
    if "max_sentences" in expect:
        results.append(check_max_sentences(expect["max_sentences"], obs))
    if expect.get("network_leak", True):
        results.append(check_network_leak(network, obs))
    return results


# ── Runner ────────────────────────────────────────────────────────────────


@dataclass
class Run:
    case: Case
    network: str


@dataclass
class TurnReport:
    user: str
    response: str
    tools: list[str]
    results: list[AssertionResult]


@dataclass
class RunResult:
    case_id: str
    network: str
    turns: list[TurnReport]
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def passed(self) -> bool:
        return all(r.passed for t in self.turns for r in t.results)

    def failures(self) -> list[AssertionResult]:
        return [r for t in self.turns for r in t.results if not r.passed]


AgentFactory = Callable[[str, Case], Awaitable[Any]]
TurnPlayer = Callable[
    [Any, str, list[dict[str, Any]]], Awaitable[tuple[TurnObservation, list[dict[str, Any]]]]
]


def expand_runs(
    cases: list[Case],
    network: str = "both",
    case_patterns: list[str] | None = None,
    include_pending: bool = False,
) -> tuple[list[Run], list[tuple[str, str]]]:
    """One ``Run`` per (case, network); pending cases go to ``skipped`` with the reason."""
    wanted = NETWORKS if network == "both" else (network,)
    runs: list[Run] = []
    skipped: list[tuple[str, str]] = []
    for case in cases:
        if case_patterns and not any(fnmatch.fnmatch(case.id, p) for p in case_patterns):
            continue
        if case.pending and not include_pending:
            skipped.append((case.id, f"pending {case.pending}"))
            continue
        for net in case.networks:
            if net in wanted:
                runs.append(Run(case=case, network=net))
    return runs, skipped


async def run_case(run: Run, make_agent: AgentFactory, play_turn: TurnPlayer) -> RunResult:
    """Play every turn of the case in order, carrying the history, in ``run.network``."""
    agent = await make_agent(run.network, run.case)
    history: list[dict[str, Any]] = []
    result = RunResult(case_id=run.case.id, network=run.network, turns=[])
    for turn in run.case.turns:
        obs, history = await play_turn(agent, turn.user, history)
        result.input_tokens += obs.input_tokens
        result.output_tokens += obs.output_tokens
        checks = evaluate_turn(effective_expect(turn, run.network), run.network, obs)
        result.turns.append(
            TurnReport(
                user=turn.user,
                response=obs.response_text,
                tools=[c.name for c in obs.tool_calls],
                results=checks,
            )
        )
    return result


async def run_all(
    runs: list[Run], make_agent: AgentFactory, play_turn: TurnPlayer
) -> list[RunResult]:
    results = []
    for run in runs:
        res = await run_case(run, make_agent, play_turn)
        mark = "OK  " if res.passed else "FAIL"
        print(f"  {mark} {res.case_id} [{res.network}]", file=sys.stderr)
        results.append(res)
    return results


def format_report(results: list[RunResult], skipped: list[tuple[str, str]]) -> str:
    """«Зелёних кейсів N/M» over (case × network) runs + failure breakdown by assertion."""
    green = sum(1 for r in results if r.passed)
    lines = [f"Зелёних кейсів: {green}/{len(results)} (кейс × мережа)"]
    for net in NETWORKS:
        net_runs = [r for r in results if r.network == net]
        if net_runs:
            ok = sum(1 for r in net_runs if r.passed)
            lines.append(f"  {net}: {ok}/{len(net_runs)}")
    by_case: dict[str, bool] = {}
    for r in results:
        by_case[r.case_id] = by_case.get(r.case_id, True) and r.passed
    lines.append(f"Кейсів, зелених у всіх своїх мережах: {sum(by_case.values())}/{len(by_case)}")

    breakdown = Counter(f.name for r in results for f in r.failures())
    if breakdown:
        lines.append("Провали за асертами (runs × turns):")
        lines.extend(f"  {name}: {n}" for name, n in breakdown.most_common())
    failed = [r for r in results if not r.passed]
    if failed:
        lines.append("Червоні:")
        for r in failed:
            for t in r.turns:
                for f in (x for x in t.results if not x.passed):
                    lines.append(
                        f"  {r.case_id} [{r.network}] «{t.user[:50]}» {f.name}: {f.detail}"
                    )
                    lines.append(f"      бот: {t.response[:160]!r} tools={t.tools}")
    if skipped:
        lines.append(f"Пропущено (pending): {len(skipped)}")
        lines.extend(f"  {cid}: {why}" for cid, why in skipped)
    return "\n".join(lines)


# ── Cost ──────────────────────────────────────────────────────────────────

# $/1M tokens (input, output). Estimate only; the authority is `llm_model_pricing`.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "gemini-2.5-flash": (0.30, 2.50),
}
# One voice turn = full prompt + tool schemas (~107K chars at 871b47b, ≈ 37K
# tokens of mostly Cyrillic text) resent on each of ~1.5 LLM rounds. Prompt
# caching lowers the real bill; the estimate is the uncached ceiling.
DEFAULT_INPUT_TOKENS_PER_TURN = 55_000
DEFAULT_OUTPUT_TOKENS_PER_TURN = 200


def estimate_cost(
    runs: list[Run],
    model: str,
    input_per_turn: int = DEFAULT_INPUT_TOKENS_PER_TURN,
    output_per_turn: int = DEFAULT_OUTPUT_TOKENS_PER_TURN,
) -> tuple[int, float | None]:
    """(turns, USD or None when the model has no price here)."""
    turns = sum(len(r.case.turns) for r in runs)
    price = next((p for key, p in PRICES.items() if key in model), None)
    if price is None:
        return turns, None
    usd = turns * (input_per_turn * price[0] + output_per_turn * price[1]) / 1_000_000
    return turns, usd


# ── Live wiring (paid) ────────────────────────────────────────────────────


async def _play_sandbox_turn(
    agent: Any, user_text: str, history: list[dict[str, Any]]
) -> tuple[TurnObservation, list[dict[str, Any]]]:
    from src.sandbox.agent_runner import process_sandbox_turn

    res = await process_sandbox_turn(agent, user_text, history, is_mock=True)
    obs = TurnObservation(
        response_text=res.response_text or "",
        tool_calls=[ToolCall(tc.tool_name, dict(tc.tool_args or {})) for tc in res.tool_calls],
        error=res.error,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
    )
    return obs, res.updated_history


def _const_handler(result: Any) -> Callable[..., Awaitable[Any]]:
    async def _handler(**_kwargs: object) -> Any:
        return copy.deepcopy(result)

    return _handler


class SandboxAgentFactory:
    """Builds the sandbox agent for a network from its real tenant row.

    ``sales_enabled`` from the case goes into ``tenant["config"]``. The agent
    does not read it yet — TODO(wave-1-A/3-G): once the prompt assembly reads
    ``tenants.config.sales_enabled``, the case flag takes effect with no change
    here.
    """

    def __init__(
        self, engine: Any, tenants: dict[str, dict[str, Any]], router: Any, provider: str | None
    ):
        self._engine = engine
        self._tenants = tenants
        self._router = router
        self._provider = provider

    def provider_for(self, network: str) -> str | None:
        cfg = self._tenants[network].get("config") or {}
        return self._provider or cfg.get("agent_provider_override")

    async def __call__(self, network: str, case: Case) -> Any:
        from src.sandbox.agent_runner import create_sandbox_agent

        tenant = copy.deepcopy(self._tenants[network])
        tenant.setdefault("config", {})
        tenant["config"]["sales_enabled"] = case.sales_enabled
        provider = self.provider_for(network)
        agent = await create_sandbox_agent(
            self._engine,
            tool_mode="mock",
            model=provider,
            llm_router=self._router if provider else None,
            provider_override=provider,
            tenant=tenant,
            tenant_id=str(tenant["id"]),
        )
        for tool, result in case.mocks.items():
            agent.tool_router.register(tool, _const_handler(result))
        return agent


async def _load_tenants(engine: Any) -> dict[str, dict[str, Any]]:
    from sqlalchemy import text

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, slug, network_id, agent_name, enabled_tools, prompt_suffix, config "
                    "FROM tenants WHERE slug = ANY(:slugs)"
                ),
                {"slugs": list(NETWORKS)},
            )
        ).mappings()
        tenants = {r["slug"]: dict(r) for r in rows}
    for t in tenants.values():
        if isinstance(t.get("config"), str):
            t["config"] = json.loads(t["config"])
    missing = set(NETWORKS) - set(tenants)
    if missing:
        raise SystemExit(f"tenants not found: {sorted(missing)}")
    return tenants


async def _live(runs: list[Run], skipped: list[tuple[str, str]], opts: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.config import get_settings
    from src.llm.router import LLMRouter

    engine = create_async_engine(get_settings().database.url, pool_size=2)
    router = LLMRouter()
    try:
        await router.initialize()
        tenants = await _load_tenants(engine)
        factory = SandboxAgentFactory(engine, tenants, router, opts.provider)
        total_turns, total_usd = 0, 0.0
        for net in sorted({r.network for r in runs}):
            provider = factory.provider_for(net) or "sandbox default"
            if provider != "sandbox default" and provider not in router.providers:
                raise SystemExit(f"provider {provider!r} not initialised (API key env var?)")
            model = (router.config.get("providers", {}).get(provider) or {}).get("model", provider)
            net_runs = [r for r in runs if r.network == net]
            turns, usd = estimate_cost(net_runs, model, opts.input_tokens_per_turn)
            total_turns += turns
            print(
                f"{net}: {len(net_runs)} runs, {turns} turns, provider={provider} ({model}), "
                f"≈ {f'${usd:.2f}' if usd is not None else 'ціна невідома'}"
            )
            total_usd += usd or 0.0
        print(
            f"Разом: {total_turns} turns ≈ ${total_usd:.2f} (оцінка; {opts.input_tokens_per_turn} input tok/turn)"
        )
        if not opts.yes:
            print("Платний прогін не запущено: додайте --yes.")
            return 0
        results = await run_all(runs, factory, _play_sandbox_turn)
    finally:
        await router.close()
        await engine.dispose()

    print(format_report(results, skipped))
    if opts.json_out:
        Path(opts.json_out).write_text(
            json.dumps(
                [
                    {
                        "case": r.case_id,
                        "network": r.network,
                        "passed": r.passed,
                        "turns": [
                            {
                                "user": t.user,
                                "response": t.response,
                                "tools": t.tools,
                                "failed": [f.__dict__ for f in t.results if not f.passed],
                            }
                            for t in r.turns
                        ],
                    }
                    for r in results
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0 if all(r.passed for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--network", choices=[*NETWORKS, "both"], default="both")
    parser.add_argument("--case", action="append", default=[], help="case id or glob; repeatable")
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM router provider key (default: tenant's agent_provider_override)",
    )
    parser.add_argument("--cases-dir", default=str(CASES_DIR))
    parser.add_argument(
        "--include-pending", action="store_true", help="also run cases marked pending"
    )
    parser.add_argument(
        "--validate", action="store_true", help="only load and validate the cases (free)"
    )
    parser.add_argument("--input-tokens-per-turn", type=int, default=DEFAULT_INPUT_TOKENS_PER_TURN)
    parser.add_argument("--json-out", default="", help="per-run JSON report path")
    parser.add_argument("--yes", action="store_true", help="run the paid LLM pass")
    opts = parser.parse_args(argv)

    cases = load_cases(Path(opts.cases_dir))
    runs, skipped = expand_runs(cases, opts.network, opts.case or None, opts.include_pending)
    print(
        f"Кейсів: {len(cases)}; прогонів (кейс × мережа): {len(runs)}; pending пропущено: {len(skipped)}"
    )
    for cid, why in skipped:
        print(f"  skip {cid}: {why}")
    if opts.validate:
        return 0
    if not runs:
        print("Нічого запускати.")
        return 0
    return asyncio.run(_live(runs, skipped, opts))


if __name__ == "__main__":
    sys.exit(main())
