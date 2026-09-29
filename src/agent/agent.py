"""LLM agent: Claude API with tool calling.

Manages conversation flow, tool routing, and context window.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import anthropic

from src.agent.disk_fit_claim_guard import (
    DiskFitClaimState,
    collect_disk_verdict,
    drop_fit_claims_text,
)
from src.agent.disk_intent import (
    KB_TOOL,
    DiskToolRedirect,
    disk_consult_args,
    is_disk_consult,
    run_disk_substitution_raw,
)
from src.agent.history_compressor import summarize_old_messages
from src.agent.network_claim_guard import guard_text
from src.agent.network_facts import already_said_note, turn_facts
from src.agent.network_policy import NetworkPolicy, render_network_block
from src.agent.promotions import turn_network_overrides, turn_promotions_block
from src.agent.prompts import (
    ERROR_TEXT,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_system_prompt_with_context,
    detect_scenario_from_text,
)
from src.agent.tire_search_gate import (
    SEARCH_TOOL,
    ForcedTireSearch,
    accumulate_query,
    forced_search_messages,
    forced_tool_messages,
    last_assistant_text,
    run_forced_search,
    run_forced_tool,
)
from src.agent.tool_result_compressor import (
    compress_tool_result,
    repeat_call_note,
    tire_caveat_phrase,
)
from src.agent.tools import ALL_TOOLS, filter_tools_by_state
from src.agent.vague_tyre_buy import VagueBuyGate
from src.agent.vehicle_lookup_gate import (
    LOOKUP_TOOL,
    VehicleLookupGate,
    drop_stud_questions_text,
)
from src.llm.router import llm_call_id_var
from src.monitoring.metrics import (
    history_compression_mode,
    history_messages_count,
    llm_stop_reason_total,
    system_prompt_chars,
    tool_audit_write_failures_total,
    tool_call_errors_total,
    tool_rounds_exhausted_total,
    tool_rounds_per_turn,
)

if TYPE_CHECKING:
    from src.agent.promotions import ActivePromotion, PromoOverrides
    from src.llm.router import LLMRouter
    from src.logging.pii_vault import PIIVault

logger = logging.getLogger(__name__)

# Limits
MAX_TOOL_CALLS_PER_TURN = 5
MAX_HISTORY_MESSAGES = 25
_TOOL_TIMEOUT_SEC = 15  # Per-tool execution timeout


class ToolRouter:
    """Routes tool_use calls to concrete implementations.

    Tool handlers are registered as async callables.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}
        self._on_execute: Any = (
            None  # optional async callback(name, args, result, duration_ms, success)
        )

    def set_execute_hook(self, callback: Any) -> None:
        """Set an async callback invoked after each tool execution."""
        self._on_execute = callback

    def register(self, name: str, handler: Any) -> None:
        """Register a handler for a tool name."""
        self._handlers[name] = handler

    async def _record_cancelled(self, name: str, args: dict[str, Any], start: float) -> None:
        """Write the row for a handler the call ended under.

        A hangup — or the channel torn down by the transfer this very tool asked
        for — cancels the call task while the handler is still awaiting, and the
        result never reaches the hook below. The row is written anyway; the
        result is unknown, and it says so instead of guessing.
        """
        if self._on_execute is None:
            return
        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            await asyncio.shield(
                self._on_execute(
                    name,
                    args,
                    {"cancelled": "the call ended while the tool was running"},
                    duration_ms,
                    False,
                )
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            self._report_audit_failure(name, path="cancelled")

    @staticmethod
    def _report_audit_failure(name: str, *, path: str) -> None:
        """Make a lost `call_tool_calls` row visible: metric + ERROR log.

        The audit row is the single source of truth for "did this tool actually
        run" — call reviews read it, and a missing row is read as "the tool was
        never called". Swallowing the write failure silently made that reading
        unsound (see 37fb2d0: suppress + DEBUG log cost 3 of 3 bookings).

        `path` says WHICH of the two call sites in `execute()` lost the row:
        "result" — the tool succeeded, "error" — the tool raised. One shared
        message for both would mask half of the defect.

        Never raises: the call must survive a broken audit write. This trades
        "lost silently" for "lost loudly", not for "call dropped".
        """
        tool_audit_write_failures_total.labels(tool_name=name, path=path).inc()
        call_id = llm_call_id_var.get(None) or "unknown"
        logger.exception(
            "Tool audit write FAILED (path=%s): tool=%s call_id=%s — "
            "call_tool_calls row is lost, this call's audit is incomplete",
            path,
            name,
            call_id,
            extra={"call_id": str(call_id)},
        )

    async def execute(self, name: str, args: dict[str, Any]) -> Any:
        """Execute a tool by name. Returns the result dict."""
        handler = self._handlers.get(name)
        if handler is None:
            logger.warning("Unknown tool: %s", name)
            return {"error": f"Unknown tool: {name}"}

        start = time.monotonic()
        try:
            try:
                result = await handler(**args)
            except asyncio.CancelledError:
                await self._record_cancelled(name, args, start)
                raise
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "Tool %s executed in %dms",
                name,
                duration_ms,
            )
            # Persist tool_call log even if the outer task is cancelled
            # (e.g. transfer_to_operator triggers AMI redirect → AudioSocket
            # disconnect → streaming_loop cancellation). Without shield the
            # DB write races with cancellation and is silently dropped, so
            # transferred calls appear in `calls` but not `call_tool_calls`.
            if self._on_execute is not None:
                try:
                    await asyncio.shield(self._on_execute(name, args, result, duration_ms, True))
                except asyncio.CancelledError:
                    # BaseException, not Exception: cancellation of the call
                    # is not an audit failure and must propagate.
                    raise
                except Exception:
                    self._report_audit_failure(name, path="result")
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("Tool %s failed after %dms", name, duration_ms)
            tool_call_errors_total.labels(tool_name=name, error_type="exception").inc()
            if self._on_execute is not None:
                try:
                    await asyncio.shield(
                        self._on_execute(name, args, {"error": str(exc)}, duration_ms, False)
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._report_audit_failure(name, path="error")
            return {"error": str(exc)}


class LLMAgent:
    """Claude-based conversational agent with tool calling.

    Sends messages to Claude API, handles tool_use responses by
    routing them through ToolRouter, and manages conversation context.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-5-20250929",
        tool_router: ToolRouter | None = None,
        pii_vault: PIIVault | None = None,
        tools: list[dict[str, Any]] | None = None,
        llm_router: LLMRouter | None = None,
        system_prompt: str | None = None,
        prompt_version_name: str | None = None,
        provider_override: str | None = None,
        few_shot_context: str | None = None,
        safety_context: str | None = None,
        promotions_context: str | None = None,
        network_policy: NetworkPolicy | None = None,
        promo_overrides: PromoOverrides | None = None,
        is_modular: bool = False,
        agent_name: str | None = None,
        promotions: list[ActivePromotion] | None = None,
    ) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model
        self._tool_router = tool_router or ToolRouter()
        self._pii_vault = pii_vault
        self._tools = tools or list(ALL_TOOLS)
        self._llm_router = llm_router
        self._system_prompt = system_prompt or SYSTEM_PROMPT
        self._prompt_version_name = prompt_version_name or PROMPT_VERSION
        self._provider_override = provider_override
        self._few_shot_context = few_shot_context
        self._safety_context = safety_context
        self._promotions_context = promotions_context
        # Sales on: today's live promotions, filtered per turn by
        # ``turn_promotions_block`` — only a relevant one reaches the prompt.
        # None (sales off) keeps the static ``promotions_context`` string.
        self._promotions = promotions
        self._network_policy = network_policy
        self._promo_overrides = promo_overrides
        self._is_modular = is_modular
        self._agent_name = agent_name
        # Sales on (wave 2-D): what this call has touched so far, as the live
        # pipeline keeps it in the session — scenarios detected in caller
        # turns and tools called. A caller that passes nothing (the sandbox,
        # goldset) still gets the on-demand fitting module. One agent per call.
        self._call_scenarios: set[str] = set()
        self._call_tools: set[str] = set()
        # The caller's tyre request, as the live pipeline keeps it in
        # ``session.tire_query`` (`merge_tire_query`, sales scope only).
        self._tire_query: dict[str, Any] = {}
        # The code's own car lookups and the factory size they put into
        # ``_tire_query`` (`vehicle_lookup_gate`, sales scope only).
        self._vehicle_gate = VehicleLookupGate(
            sales_enabled=bool(network_policy is not None and network_policy.sales_enabled)
        )
        # «купити резину» with no car and no size: the code's question is the
        # whole reply, once per call (`vague_tyre_buy`, sales scope only).
        self._vague_gate = VagueBuyGate(
            sales_enabled=bool(network_policy is not None and network_policy.sales_enabled)
        )
        # Accumulated usage from last process_message call (all LLM rounds)
        self.last_input_tokens: int = 0
        self.last_output_tokens: int = 0
        self.last_cached_input_tokens: int = 0
        self.last_provider_key: str = ""
        # Last error message (if LLM call failed) — consumed by sandbox
        self.last_error: str | None = None

    @property
    def tool_router(self) -> ToolRouter:
        return self._tool_router

    async def process_message(
        self,
        user_text: str,
        conversation_history: list[dict[str, Any]],
        caller_phone: str | None = None,
        order_id: str | None = None,
        pattern_context: str | None = None,
        order_stage: str | None = None,
        caller_history: str | None = None,
        storage_context: str | None = None,
        customer_profile: str | None = None,
        fitting_booked: bool = False,
        tools_called: set[str] | None = None,
        scenario: str | None = None,
        active_scenarios: set[str] | None = None,
        selected_station: dict[str, Any] | None = None,
        selected_slot: dict[str, str] | None = None,
        offered_slots: list[dict[str, str]] | None = None,
        fitting_progress: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Process a user message and return the agent's text response.

        Args:
            user_text: The user's transcribed speech.
            conversation_history: List of previous messages (mutated in place).
            caller_phone: CallerID phone number (if available).
            order_id: Current order draft ID (if in progress).
            pattern_context: Optional pattern injection text for system prompt.
            order_stage: Current order stage (None, "draft", "delivery_set", "confirmed").
            caller_history: Formatted caller history section.
            storage_context: Formatted storage contracts section.
            customer_profile: Formatted customer profile section.
            fitting_booked: Whether a fitting has already been booked this call.
            tools_called: Set of tool names invoked during this call (for module expansion).
            scenario: Current IVR scenario (for module expansion).
            active_scenarios: All detected scenarios (for topic switching).

        Returns:
            Tuple of (response_text, updated_conversation_history).
        """
        sales_on = bool(self._network_policy is not None and self._network_policy.sales_enabled)
        if sales_on:
            # As the live pipeline: detect on this turn's text BEFORE the
            # prompt is built, so «записатися на шиномонтаж» gets the fitting
            # module on the turn it is first said.
            detected = detect_scenario_from_text(user_text)
            if detected:
                self._call_scenarios.add(detected)
            active_scenarios = set(active_scenarios or ()) | self._call_scenarios
            tools_called = set(tools_called or ()) | self._call_tools
            # A live sales call always has a scenario (`main._default_scenario`);
            # `assemble_prompt` maps None to the same `sales` bundle.
            scenario = scenario or "sales"
            self._tire_query = accumulate_query(
                self._tire_query, user_text, conversation_history, tools_called
            )
            self._vehicle_gate.note_turn(user_text, self._tire_query)
            self._tire_query = self._vehicle_gate.apply(self._tire_query) or {}

        # Mask PII before sending to LLM
        if self._pii_vault is not None:
            user_text = self._pii_vault.mask(user_text)

        # Add user message (skip if already present — pipeline may pre-add)
        if not (
            conversation_history
            and conversation_history[-1]["role"] == "user"
            and conversation_history[-1].get("content") == user_text
        ):
            conversation_history.append({"role": "user", "content": user_text})

        # Claude API requires first message to be user role.
        # The greeting (assistant) may be first — prepend a synthetic turn.
        if conversation_history and conversation_history[0]["role"] != "user":
            conversation_history.insert(0, {"role": "user", "content": "(початок дзвінка)"})

        # Compress/summarize old messages to save tokens (BEFORE trim so
        # early context like customer name / topic is captured in the summary).
        # See streaming_loop.py for the rationale behind these thresholds.
        pre_len = len(conversation_history)
        conversation_history[:] = summarize_old_messages(
            conversation_history,
            summary_threshold=9,
            keep_recent=7,
        )
        post_len = len(conversation_history)

        # Record compression mode metric
        if post_len < pre_len and post_len > 0 and conversation_history[0].get("content", "").startswith("(Резюме"):
            history_compression_mode.labels(mode="summarize").inc()
        elif post_len < pre_len:
            history_compression_mode.labels(mode="compress").inc()
        else:
            history_compression_mode.labels(mode="none").inc()

        # Safety-net trim: if history is still too long after summarization
        if len(conversation_history) > MAX_HISTORY_MESSAGES:
            conversation_history[:] = (
                conversation_history[:1] + conversation_history[-(MAX_HISTORY_MESSAGES - 1) :]
            )

        # Build system prompt with caller context (mask caller phone)
        masked_phone = caller_phone
        if self._pii_vault is not None and caller_phone:
            masked_phone = self._pii_vault.mask(caller_phone)
        system = build_system_prompt_with_context(
            self._system_prompt,
            is_modular=self._is_modular,
            order_stage=order_stage,
            safety_context=self._safety_context,
            few_shot_context=self._few_shot_context,
            promotions_context=(
                turn_promotions_block(self._promotions, user_text)
                if self._promotions is not None
                else self._promotions_context
            ),
            network_policy_context=render_network_block(
                self._network_policy,
                turn_network_overrides(self._promotions, user_text, None, self._promo_overrides),
            ),
            caller_phone=masked_phone,
            order_id=order_id,
            pattern_context=pattern_context,
            agent_name=self._agent_name,
            customer_profile=customer_profile,
            caller_history=caller_history,
            storage_context=storage_context,
            tools_called=tools_called,
            scenario=scenario,
            active_scenarios=active_scenarios,
            selected_station=selected_station,
            selected_slot=selected_slot,
            offered_slots=offered_slots,
            fitting_progress=fitting_progress,
            enabled_tools={t["name"] for t in self._tools},
            network_policy=self._network_policy,
        )

        # Record prompt and history metrics
        system_prompt_chars.observe(len(system))
        history_messages_count.observe(len(conversation_history))

        # Filter tools by conversation state (remove irrelevant tools)
        tools = filter_tools_by_state(
            self._tools, order_stage=order_stage, fitting_booked=fitting_booked
        )

        response_text = ""
        sales_enabled = bool(self._network_policy is not None and self._network_policy.sales_enabled)
        caveats: list[str] = []
        # Text-path twin of the streamed `search_disks` redirect (`disk_intent`);
        # a wheel consultation is the knowledge base's, not the redirect's.
        # Calls run successfully this turn (repeat stopper, sales scope).
        done_this_turn: set[str] = set()
        disk_redirect = DiskToolRedirect(
            sales_enabled=sales_enabled, tools=tools, consult=is_disk_consult(user_text)
        )
        # Fit verdicts of this turn's `search_disks` results (sales scope).
        disk_fit_state = DiskFitClaimState()
        tool_call_count = 0
        # Text-path twin of the spoken network facts (`network_facts`): the
        # code's phrases open the reply, and the model is told they were said.
        facts = turn_facts(
            user_text, self._network_policy, self._promo_overrides, self._tire_query
        )
        if facts:
            system += already_said_note(facts)
        # Text-path twin of the spoken first question of a tyre pick
        # (`vague_tyre_buy`): the code's question is the whole reply, no round.
        vague_phrase = self._vague_gate.plan(
            user_text,
            query=self._tire_query,
            history=conversation_history,
            tools_called=tools_called,
            last_bot_text=last_assistant_text(conversation_history),
            in_fitting="fitting" in (active_scenarios or ()) or scenario == "fitting",
        )
        if vague_phrase is not None:
            conversation_history.append(
                {"role": "assistant", "content": [{"type": "text", "text": vague_phrase}]}
            )
            self.last_input_tokens = 0
            self.last_output_tokens = 0
            self.last_cached_input_tokens = 0
            self.last_provider_key = ""
            self.last_error = None
            return vague_phrase, conversation_history
        # A wheel consultation: the code searches the knowledge base first.
        consult_args = disk_consult_args(user_text, sales_enabled=sales_enabled, tools=tools)
        if consult_args is not None:
            consult_raw = await run_forced_tool(
                KB_TOOL, consult_args, self._tool_router.execute, timeout=_TOOL_TIMEOUT_SEC
            )
            self._call_tools.add(KB_TOOL)
            consult_content = compress_tool_result(
                KB_TOOL, consult_raw, sales_enabled=sales_enabled, args=consult_args
            )
            if self._pii_vault is not None:
                consult_content = self._pii_vault.mask(consult_content)
            conversation_history.extend(
                forced_tool_messages(KB_TOOL, consult_args, consult_content)
            )
            tool_call_count += 1
        # Text-path twin of the streamed forced car lookup (`vehicle_lookup_gate`):
        # the caller named a car — its factory sizes before the first round,
        # and the one that fits the request goes into it before the search gate.
        lookup_args = self._vehicle_gate.plan(
            user_text, last_assistant_text(conversation_history), tools
        )
        if lookup_args is not None:
            if self._pii_vault is not None:
                lookup_args = self._pii_vault.restore_in_args(lookup_args)
            lookup_raw = await run_forced_tool(
                LOOKUP_TOOL, lookup_args, self._tool_router.execute, timeout=_TOOL_TIMEOUT_SEC
            )
            self._call_tools.add(LOOKUP_TOOL)
            shown_args = self._vehicle_gate.settle(lookup_raw)
            if shown_args is not None:
                lookup_content = compress_tool_result(
                    LOOKUP_TOOL, lookup_raw, sales_enabled=sales_enabled, args=shown_args
                )
                if self._pii_vault is not None:
                    lookup_content = self._pii_vault.mask(lookup_content)
                conversation_history.extend(
                    forced_tool_messages(LOOKUP_TOOL, shown_args, lookup_content)
                )
                done_this_turn.add(LOOKUP_TOOL + ":" + json.dumps(shown_args, sort_keys=True))
                tool_call_count += 1
            self._tire_query = self._vehicle_gate.apply(self._tire_query) or {}
        # Text-path twin of the streamed forced search (`tire_search_gate`):
        # the request is complete, so the code searches before the first round.
        forced_args = ForcedTireSearch(sales_enabled=sales_enabled, tools=tools).plan(
            self._tire_query, user_text, conversation_history
        )
        if forced_args is not None:
            forced_raw = await run_forced_search(
                forced_args, self._tool_router.execute, timeout=_TOOL_TIMEOUT_SEC
            )
            self._call_tools.add(SEARCH_TOOL)
            forced_phrase = tire_caveat_phrase(forced_raw, forced_args)
            if forced_phrase:
                caveats.append(forced_phrase)
            forced_content = compress_tool_result(
                SEARCH_TOOL, forced_raw, sales_enabled=sales_enabled, args=forced_args
            )
            if self._pii_vault is not None:
                forced_content = self._pii_vault.mask(forced_content)
            conversation_history.extend(forced_search_messages(forced_args, forced_content))
            tool_call_count += 1
        stop_reason = "end_turn"
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.last_cached_input_tokens = 0
        self.last_provider_key = ""
        self.last_error = None

        while tool_call_count < MAX_TOOL_CALLS_PER_TURN:
            start = time.monotonic()

            # Process response content blocks
            assistant_content: list[dict[str, Any]] = []
            tool_uses: list[dict[str, Any]] = []

            if self._llm_router is not None:
                # Router path: multi-provider with fallback
                try:
                    from src.llm.format_converter import llm_response_to_anthropic_blocks
                    from src.llm.models import LLMTask

                    llm_response = await self._llm_router.complete(
                        LLMTask.AGENT,
                        conversation_history,
                        system=system,
                        tools=tools,
                        max_tokens=1024,
                        provider_override=self._provider_override,
                    )

                    latency_ms = int((time.monotonic() - start) * 1000)
                    self.last_input_tokens += llm_response.usage.input_tokens
                    self.last_output_tokens += llm_response.usage.output_tokens
                    self.last_cached_input_tokens += llm_response.usage.cached_input_tokens
                    self.last_provider_key = llm_response.provider
                    stop_reason = llm_response.stop_reason or "end_turn"
                    logger.info(
                        "LLM response: provider=%s, stop=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
                        llm_response.provider,
                        llm_response.stop_reason,
                        latency_ms,
                        llm_response.usage.input_tokens,
                        llm_response.usage.output_tokens,
                    )

                    if llm_response.text:
                        if response_text:
                            response_text += "\n\n"
                        response_text += llm_response.text

                    # Convert LLMResponse to Anthropic content blocks for history
                    assistant_content = llm_response_to_anthropic_blocks(llm_response)
                    for tc in llm_response.tool_calls:
                        tool_uses.append(
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                except Exception as exc:
                    logger.exception("LLM router error: %s", exc)
                    self.last_error = f"LLM router: {exc}"
                    return ERROR_TEXT, conversation_history
            else:
                # Legacy path: direct Anthropic SDK
                try:
                    response = await self._client.messages.create(
                        model=self._model,
                        max_tokens=1024,
                        system=system,
                        tools=tools,  # type: ignore[arg-type]
                        messages=conversation_history,  # type: ignore[arg-type]
                    )
                except anthropic.APIStatusError as exc:
                    logger.exception("Claude API error: %s", exc)
                    self.last_error = f"Claude API: {exc.status_code} {exc.message}"
                    return ERROR_TEXT, conversation_history
                except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
                    logger.exception("Claude API connection/timeout error: %s", exc)
                    self.last_error = f"Claude API: {type(exc).__name__}"
                    return ERROR_TEXT, conversation_history
                except Exception as exc:
                    logger.exception("Claude API unexpected error: %s", exc)
                    self.last_error = f"Claude API: {exc}"
                    return ERROR_TEXT, conversation_history

                latency_ms = int((time.monotonic() - start) * 1000)
                self.last_input_tokens += response.usage.input_tokens
                self.last_output_tokens += response.usage.output_tokens
                self.last_cached_input_tokens += response.usage.cached_input_tokens
                stop_reason = response.stop_reason or "end_turn"
                logger.info(
                    "Claude response: stop=%s, latency=%dms, tokens_in=%d, tokens_out=%d",
                    response.stop_reason,
                    latency_ms,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )

                for block in response.content:
                    if block.type == "text":
                        if response_text:
                            response_text += "\n\n"
                        response_text += block.text
                        assistant_content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        tool_uses.append(
                            {
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )
                        assistant_content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )

            # Add assistant response to history
            conversation_history.append({"role": "assistant", "content": assistant_content})

            # If no tool calls, we're done
            if not tool_uses:
                break

            # Deduplicate tool calls (same name + same args → skip)
            seen_keys: set[str] = set()
            unique_tool_uses: list[dict[str, Any]] = []
            for tu in tool_uses:
                dedup_key = tu["name"] + ":" + json.dumps(tu["input"], sort_keys=True)
                if dedup_key in seen_keys:
                    logger.warning(
                        "Skipping duplicate tool call: %s(%s)",
                        tu["name"],
                        json.dumps(tu["input"], ensure_ascii=False)[:200],
                    )
                    continue
                seen_keys.add(dedup_key)
                unique_tool_uses.append(tu)
            disk_redirect.note_round(tu["name"] for tu in unique_tool_uses)

            # Execute tool calls in parallel (with per-tool timeout)
            async def _execute_one(tu: dict[str, Any]) -> dict[str, Any]:
                args = tu["input"]
                if self._pii_vault is not None:
                    args = self._pii_vault.restore_in_args(args)
                # Text-path twin of the streamed repeat stopper (sales scope).
                repeat_key = tu["name"] + ":" + json.dumps(args, sort_keys=True)
                if sales_enabled and repeat_key in done_this_turn:
                    logger.warning(
                        "Tool %s already ran this turn with the same arguments — not run again",
                        tu["name"],
                    )
                    return {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": repeat_call_note(tu["name"]),
                    }
                disk_sub = disk_redirect.check(tu["name"], args, conversation_history)
                if disk_sub is not None:
                    disk_content, disk_raw = await run_disk_substitution_raw(
                        disk_sub,
                        self._tool_router.execute,
                        timeout=_TOOL_TIMEOUT_SEC,
                        sales_enabled=sales_enabled,
                    )
                    if sales_enabled and disk_raw is not None:
                        collect_disk_verdict(disk_raw, disk_fit_state, caveats)
                    if self._pii_vault is not None:
                        disk_content = self._pii_vault.mask(disk_content)
                    return {"type": "tool_result", "tool_use_id": tu["id"], "content": disk_content}
                if sales_enabled:
                    self._call_tools.add(tu["name"])
                try:
                    raw = await asyncio.wait_for(
                        self._tool_router.execute(tu["name"], args),
                        timeout=_TOOL_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    logger.error("Tool %s timed out after %ds", tu["name"], _TOOL_TIMEOUT_SEC)
                    tool_call_errors_total.labels(tool_name=tu["name"], error_type="timeout").inc()
                    raw = {"error": "Сервіс тимчасово не відповідає, спробуйте ще раз"}
                if not (isinstance(raw, dict) and raw.get("error")):
                    done_this_turn.add(repeat_key)
                if sales_enabled and tu["name"] == "search_tires":
                    phrase = tire_caveat_phrase(raw, args)
                    if phrase and phrase not in caveats:
                        caveats.append(phrase)
                if sales_enabled and tu["name"] == "search_disks":
                    collect_disk_verdict(raw, disk_fit_state, caveats)
                if tu["name"] == LOOKUP_TOOL:
                    self._vehicle_gate.note_model_call(args, raw)
                content = compress_tool_result(
                    tu["name"], raw, sales_enabled=sales_enabled, args=args
                )
                if self._pii_vault is not None:
                    content = self._pii_vault.mask(content)
                return {"type": "tool_result", "tool_use_id": tu["id"], "content": content}

            tool_results = list(
                await asyncio.gather(*[_execute_one(tu) for tu in unique_tool_uses])
            )
            tool_call_count += len(tool_results)

            conversation_history.append({"role": "user", "content": tool_results})

            # If we hit the tool call limit, break
            if tool_call_count >= MAX_TOOL_CALLS_PER_TURN:
                logger.warning("Max tool calls reached (%d)", MAX_TOOL_CALLS_PER_TURN)
                break

        # Record per-turn metrics
        tool_rounds = tool_call_count  # each round may have multiple parallel calls
        tool_rounds_per_turn.observe(tool_rounds)
        llm_stop_reason_total.labels(reason=stop_reason if stop_reason else "end_turn").inc()

        # Fallback: if max tool rounds exhausted with no text, ask LLM for summary
        if not response_text.strip() and tool_call_count >= MAX_TOOL_CALLS_PER_TURN:
            tool_rounds_exhausted_total.inc()
            response_text = await self._request_summary_fallback(
                system, conversation_history
            )

        # The text-mode twin of the streamed claim guard: sales networks only.
        # With sales off the text path stays as it was — the live streamed
        # path already guards every network, and promotions exist only with
        # sales on. The caveat below is the code's own text, not judged.
        if sales_enabled:
            response_text = guard_text(
                response_text,
                self._network_policy,
                llm_call_id_var.get(None) or "unknown",
                site="text_path",
                promos=self._promo_overrides,
            )
            # Every offered wheel `cannot_confirm` → the model's «підходять»
            # goes; the verdict below is the code's own, not judged.
            response_text = drop_fit_claims_text(
                response_text, disk_fit_state, llm_call_id_var.get(None) or "unknown"
            )
            # Studs exist only on winter tyres: out of season the model's
            # «шиповані чи липучка?» goes (`vehicle_lookup_gate`).
            response_text = drop_stud_questions_text(
                response_text, self._tire_query, llm_call_id_var.get(None) or "unknown"
            )

        # The network facts and a relaxed tyre search's caveat come first, said
        # by the code — the text-mode twin of the spoken phrases in
        # StreamingAgentLoop, added after the guard so it never judges them.
        if facts or caveats:
            response_text = " ".join([*facts, *caveats, response_text]).strip()

        return response_text, conversation_history

    async def _request_summary_fallback(
        self,
        system: str,
        conversation_history: list[dict[str, Any]],
    ) -> str:
        """Ask LLM to summarize tool results when max tool rounds exhausted.

        Returns a short customer-facing summary or a static fallback.
        """
        _SUMMARY_TIMEOUT_SEC = 5  # noqa: N806
        _SUMMARY_PROMPT = (  # noqa: N806
            "Ти вичерпав ліміт викликів інструментів. "
            "Підсумуй для клієнта те, що вдалося дізнатися, "
            "в 1-2 реченнях українською. Не використовуй інструменти."
        )
        _FALLBACK_TEXT = (  # noqa: N806
            "Перепрошую, мені потрібно трохи більше часу. "
            "Спробуйте, будь ласка, уточнити ваше питання."
        )

        summary_history = [*conversation_history, {"role": "user", "content": _SUMMARY_PROMPT}]

        try:
            if self._llm_router is not None:
                from src.llm.models import LLMTask

                llm_resp = await asyncio.wait_for(
                    self._llm_router.complete(
                        LLMTask.AGENT,
                        summary_history,
                        system=system,
                        tools=[],
                        max_tokens=256,
                    ),
                    timeout=_SUMMARY_TIMEOUT_SEC,
                )
                if llm_resp.text and llm_resp.text.strip():
                    logger.info("Summary fallback produced text via LLM router")
                    return llm_resp.text.strip()
            else:
                resp = await asyncio.wait_for(
                    self._client.messages.create(
                        model=self._model,
                        max_tokens=256,
                        system=system,
                        messages=summary_history,
                    ),
                    timeout=_SUMMARY_TIMEOUT_SEC,
                )
                for block in resp.content:
                    if hasattr(block, "text") and block.text.strip():
                        logger.info("Summary fallback produced text via Anthropic")
                        return block.text.strip()
        except TimeoutError:
            logger.warning("Summary fallback LLM timed out (%ds)", _SUMMARY_TIMEOUT_SEC)
        except Exception:
            logger.warning("Summary fallback LLM failed", exc_info=True)

        return _FALLBACK_TEXT

    @property
    def prompt_version_name(self) -> str:
        """Return the current prompt version name."""
        return self._prompt_version_name
