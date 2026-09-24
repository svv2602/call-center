"""Offline LLM A/B for the voice agent: replay imported calls through each model.

Takes the customer lines of sandbox conversations imported from real calls
(tag ``imported``) and replays them through every variant with the same
system prompt and mock tools. Each LLM round goes through
``LLMRouter.complete_stream`` — the path live calls take — so time to first
event is measured the way a caller would feel it, not as the full blocking
round the sandbox normally times.

What decides the verdict is what sank gpt-5-mini on 2026-08-14:

- ``ttft`` — time to the first streamed event. A reasoning model spends its
  thinking before this; on "low" gpt-5-mini sat 7-20 s here.
- ``tool rate`` — share of turns with at least one tool call. On "minimal"
  gpt-5-mini kept the latency but stopped calling tools.
- ``phantom`` — turns that announce a lookup («шукаю», «перевіряю»…) without
  making any tool call: the bot pretending to search.

A variant is ``provider_key`` or ``provider_key:effort``; the second form
clones the provider with that ``reasoning_effort``, so one run can sweep the
effort levels of a new model. A level the model does not accept shows up as
errors, not as a crash.

Every variant replays its own history, so from the second turn on the
conversations drift apart. Compare the aggregates, not turn N against turn N.
The replay does not run the fitting FSM or the streaming-loop guards — it
measures the model, not the whole pipeline.

Usage (inside the call-processor container, which has DB + OpenAI key):

    docker exec call-center-call-processor-1 python -m scripts.llm_ab_replay \\
        --variant openai-gpt41-mini \\
        --variant openai-gpt6-luna:none \\
        --variant openai-gpt6-luna:minimal \\
        --variant openai-gpt6-luna:low \\
        --limit 20 --output /tmp/llm_ab.csv
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import get_settings
from src.llm.models import (
    DEFAULT_ROUTING_CONFIG,
    LLMResponse,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallStart,
    Usage,
)
from src.llm.router import LLMRouter
from src.sandbox.agent_runner import create_sandbox_agent, process_sandbox_turn

# $/1M tokens: input, cached input, output. Only for the cost column — the
# authoritative prices live in `llm_model_pricing`. Luna's cached rate is the
# ~90% discount reported at launch, not a number from a price sheet.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
}

# The bot saying it is about to look something up. With no tool call in the
# same turn that is the gpt-5-mini "minimal" failure.
_LOOKUP_RE = re.compile(
    r"шука|перевір|подивл|зачекай|хвилинк|секунд|ищу|провер|посмотр",
    re.IGNORECASE,
)

TTFT_GATE_MS = 1500


@dataclass
class Round:
    ttft_ms: int | None
    first_text_ms: int | None
    total_ms: int
    provider_key: str


@dataclass
class TurnRow:
    variant: str
    conversation_id: str
    turn: int
    customer: str
    response: str
    latency_ms: int
    ttft_ms: int | None
    rounds: int
    tool_calls: list[str]
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    error: str | None
    phantom: bool


@dataclass
class Variant:
    name: str
    provider_key: str
    model: str
    rows: list[TurnRow] = field(default_factory=list)


def _parse_variant(spec: str, config: dict[str, Any]) -> tuple[str, str]:
    """Register the variant in ``config`` and return (name, provider_key)."""
    base, _, effort = spec.partition(":")
    if base not in config["providers"]:
        sys.exit(f"Unknown provider '{base}'. Known: {sorted(config['providers'])}")
    if not effort:
        config["providers"][base]["enabled"] = True
        return spec, base
    key = f"{base}@{effort}"
    cfg = copy.deepcopy(config["providers"][base])
    cfg["enabled"] = True
    cfg["reasoning_effort"] = effort
    config["providers"][key] = cfg
    return spec, key


class _ReplayRouter(LLMRouter):
    """Router with a fixed config, whose `complete` streams and times rounds."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self._fixed_config = config
        self.rounds: list[Round] = []

    async def _load_config(self, redis: Any = None) -> dict[str, Any]:  # type: ignore[override]
        return self._fixed_config

    async def complete(  # type: ignore[override]
        self,
        task: Any,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 1024,
        provider_override: str | None = None,
    ) -> LLMResponse:
        start = time.monotonic()
        ttft: int | None = None
        first_text: int | None = None
        parts: list[str] = []
        names: dict[str, str] = {}
        args: dict[str, str] = {}
        done: StreamDone | None = None

        async for event in self.complete_stream(
            task,
            messages,
            system=system,
            tools=tools,
            max_tokens=max_tokens,
            provider_override=provider_override,
        ):
            elapsed = int((time.monotonic() - start) * 1000)
            if ttft is None and not isinstance(event, StreamDone):
                ttft = elapsed
            if isinstance(event, TextDelta):
                if first_text is None and event.text.strip():
                    first_text = elapsed
                parts.append(event.text)
            elif isinstance(event, ToolCallStart):
                names[event.id] = event.name
                args[event.id] = ""
            elif isinstance(event, ToolCallDelta):
                args[event.id] = args.get(event.id, "") + event.arguments_chunk
            elif isinstance(event, StreamDone):
                done = event

        self.rounds.append(
            Round(
                ttft,
                first_text,
                int((time.monotonic() - start) * 1000),
                done.provider_key if done else "",
            )
        )
        tool_calls = [
            ToolCall(id=cid, name=name, arguments=json.loads(args[cid] or "{}"))
            for cid, name in names.items()
        ]
        return LLMResponse(
            text="".join(parts),
            tool_calls=tool_calls,
            stop_reason=done.stop_reason if done else "end_turn",
            usage=done.usage if done else Usage(0, 0),
            provider=done.provider_key if done else "",
        )


async def _load_conversations(engine: AsyncEngine, limit: int) -> list[tuple[str, list[str]]]:
    async with engine.begin() as conn:
        convs = await conn.execute(
            text("""
                SELECT id FROM sandbox_conversations
                WHERE 'imported' = ANY(tags)
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        result: list[tuple[str, list[str]]] = []
        for (conv_id,) in convs:
            turns = await conn.execute(
                text("""
                    SELECT content FROM sandbox_turns
                    WHERE conversation_id = :id AND speaker = 'customer'
                    ORDER BY turn_number, created_at
                """),
                {"id": str(conv_id)},
            )
            lines = [row.content for row in turns if row.content and row.content.strip()]
            if lines:
                result.append((str(conv_id), lines))
    return result


async def _replay(
    engine: AsyncEngine,
    router: _ReplayRouter,
    variant: Variant,
    conversations: list[tuple[str, list[str]]],
) -> None:
    for conv_id, lines in conversations:
        agent = await create_sandbox_agent(
            engine,
            tool_mode="mock",
            model=variant.provider_key,
            llm_router=router,
            provider_override=variant.provider_key,
        )
        history: list[dict[str, Any]] = []
        for n, line in enumerate(lines, 1):
            router.rounds.clear()
            result = await process_sandbox_turn(agent, line, history, is_mock=True)
            history = result.updated_history
            rounds = list(router.rounds)
            if any(r.provider_key and r.provider_key != variant.provider_key for r in rounds):
                result.error = (result.error or "") + " [served by another provider]"
            tools = [tc.tool_name for tc in result.tool_calls]
            variant.rows.append(
                TurnRow(
                    variant=variant.name,
                    conversation_id=conv_id,
                    turn=n,
                    customer=line,
                    response=result.response_text,
                    latency_ms=result.latency_ms,
                    ttft_ms=rounds[0].ttft_ms if rounds else None,
                    rounds=len(rounds),
                    tool_calls=tools,
                    input_tokens=result.input_tokens,
                    cached_tokens=agent.last_cached_input_tokens,
                    output_tokens=result.output_tokens,
                    error=result.error,
                    phantom=not tools and bool(_LOOKUP_RE.search(result.response_text or "")),
                )
            )
            print(
                f"  {variant.name} {conv_id[:8]} #{n}: "
                f"ttft={rounds[0].ttft_ms if rounds else '-'}ms "
                f"tools={tools or '-'}"
                f"{' ERROR' if result.error else ''}",
                file=sys.stderr,
            )


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _cost(variant: Variant) -> float | None:
    prices = PRICES.get(variant.model)
    if prices is None:
        return None
    inp, cached, out = prices
    total = sum(
        (r.input_tokens - r.cached_tokens) * inp + r.cached_tokens * cached + r.output_tokens * out
        for r in variant.rows
    )
    return total / 1_000_000


def _summary(variants: list[Variant]) -> None:
    baseline = variants[0]
    base_rate: float | None = None
    header = (
        f"{'variant':<28}{'turns':>6}{'err':>5}{'ttft p50':>10}{'ttft p95':>10}"
        f"{'turn p50':>10}{'out tok':>9}{'tool rate':>11}{'phantom':>9}{'$/turn':>10}  verdict"
    )
    print(header)
    print("-" * len(header))
    for v in variants:
        ok = [r for r in v.rows if not r.error]
        ttfts = [r.ttft_ms for r in ok if r.ttft_ms is not None]
        lat = [r.latency_ms for r in ok]
        out = [r.output_tokens for r in ok]
        rate = sum(1 for r in ok if r.tool_calls) / len(ok) if ok else 0.0
        phantom = sum(1 for r in ok if r.phantom)
        cost = _cost(v)
        if v is baseline:
            base_rate = rate
            verdict = "baseline"
        else:
            fails = []
            if len(ok) < len(v.rows):
                fails.append(f"{len(v.rows) - len(ok)} errors")
            p50 = _pct(ttfts, 0.5)
            if p50 is None or p50 > TTFT_GATE_MS:
                fails.append(f"ttft p50 > {TTFT_GATE_MS}")
            if base_rate is not None and rate < base_rate - 0.05:
                fails.append("fewer tool calls")
            if phantom > sum(1 for r in baseline.rows if r.phantom and not r.error):
                fails.append("more phantom lookups")
            verdict = "FAIL: " + ", ".join(fails) if fails else "pass"
        print(
            f"{v.name:<28}{len(v.rows):>6}{len(v.rows) - len(ok):>5}"
            f"{_pct(ttfts, 0.5) or '-':>10}{_pct(ttfts, 0.95) or '-':>10}"
            f"{_pct(lat, 0.5) or '-':>10}"
            f"{statistics.median(out) if out else '-':>9}"
            f"{rate:>10.0%} {phantom:>8}"
            f"{f'{cost / len(v.rows):.5f}' if cost is not None and v.rows else '-':>10}"
            f"  {verdict}"
        )


def _write_csv(path: str, variants: list[Variant]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "variant",
                "conversation_id",
                "turn",
                "customer",
                "response",
                "latency_ms",
                "ttft_ms",
                "rounds",
                "tool_calls",
                "input_tokens",
                "cached_tokens",
                "output_tokens",
                "phantom",
                "error",
            ]
        )
        for v in variants:
            for r in v.rows:
                writer.writerow(
                    [
                        r.variant,
                        r.conversation_id,
                        r.turn,
                        r.customer,
                        r.response,
                        r.latency_ms,
                        r.ttft_ms,
                        r.rounds,
                        "|".join(r.tool_calls),
                        r.input_tokens,
                        r.cached_tokens,
                        r.output_tokens,
                        r.phantom,
                        r.error or "",
                    ]
                )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--variant",
        action="append",
        required=True,
        help="provider_key or provider_key:reasoning_effort; the first one is the baseline",
    )
    parser.add_argument("--limit", type=int, default=20, help="imported conversations to replay")
    parser.add_argument("--output", default="", help="per-turn CSV path")
    opts = parser.parse_args()

    config = copy.deepcopy(DEFAULT_ROUTING_CONFIG)
    for provider in config["providers"].values():
        provider["enabled"] = False
    specs = [_parse_variant(spec, config) for spec in opts.variant]

    router = _ReplayRouter(config)
    await router.initialize()
    missing = [key for _, key in specs if key not in router.providers]
    if missing:
        sys.exit(f"Providers not initialized (API key env var missing?): {missing}")

    engine = create_async_engine(get_settings().database.url, pool_size=5)
    try:
        conversations = await _load_conversations(engine, opts.limit)
        if not conversations:
            sys.exit("No imported sandbox conversations — import calls in Admin UI → Sandbox first")
        print(
            f"Replaying {len(conversations)} conversations, "
            f"{sum(len(lines) for _, lines in conversations)} customer turns per variant",
            file=sys.stderr,
        )

        variants = [Variant(name, key, config["providers"][key]["model"]) for name, key in specs]
        for v in variants:
            await _replay(engine, router, v, conversations)

        _summary(variants)
        if opts.output:
            _write_csv(opts.output, variants)
            print(f"\nPer-turn rows: {opts.output}", file=sys.stderr)
    finally:
        await router.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
