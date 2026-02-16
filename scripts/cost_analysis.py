#!/usr/bin/env python3
"""Call Center AI — Cost Analysis Script.

Calculates per-call and monthly costs broken down by component:
STT (Google vs Whisper), LLM (Claude), TTS (Google), infrastructure.

Usage:
    python scripts/cost_analysis.py [--calls-per-day 500] [--avg-duration 90]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

# ========== Pricing constants (as of Feb 2026) ==========

# Google Cloud STT v2: $0.006 per 15-second interval
GOOGLE_STT_PER_15S = 0.006

# Faster-Whisper GPU server: ~$150/month (e.g. A10G spot instance)
WHISPER_SERVER_MONTHLY = 150.0

# Claude Sonnet: $3/1M input, $15/1M output tokens
CLAUDE_SONNET_INPUT_PER_1M = 3.0
CLAUDE_SONNET_OUTPUT_PER_1M = 15.0

# Google Cloud TTS Neural2: $16/1M characters
# Google Cloud TTS Standard: $4/1M characters
GOOGLE_TTS_PER_1M_CHARS = 4.0

# Infrastructure costs (monthly)
SIP_TRUNK_MONTHLY = 50.0        # SIP provider
VPS_APP_SERVER_MONTHLY = 80.0   # App server (4 vCPU, 8GB)
VPS_DB_SERVER_MONTHLY = 60.0    # PostgreSQL + Redis
MONITORING_MONTHLY = 20.0       # Grafana Cloud / Prometheus


# ========== Average call parameters ==========

@dataclass
class CallProfile:
    """Average call parameters for cost estimation."""
    duration_seconds: int = 90          # avg call duration
    stt_intervals_15s: int = 6          # ~90s of audio
    llm_input_tokens: int = 2500        # system prompt + context + tools
    llm_output_tokens: int = 400        # bot responses
    llm_turns: int = 4                  # avg turns per call
    tts_characters: int = 600           # bot speech characters
    tts_cache_hit_rate: float = 0.30    # 30% of phrases are cached


@dataclass
class CostResult:
    """Cost analysis result."""
    # Per-call costs
    stt_google_per_call: float = 0.0
    stt_whisper_per_call: float = 0.0
    llm_per_call: float = 0.0
    tts_per_call: float = 0.0
    total_google_per_call: float = 0.0
    total_whisper_per_call: float = 0.0

    # Monthly costs (at given volume)
    calls_per_day: int = 0
    calls_per_month: int = 0
    stt_google_monthly: float = 0.0
    stt_whisper_monthly: float = 0.0
    llm_monthly: float = 0.0
    tts_monthly: float = 0.0
    infrastructure_monthly: float = 0.0
    total_google_monthly: float = 0.0
    total_whisper_monthly: float = 0.0

    # Savings
    whisper_savings_monthly: float = 0.0
    whisper_savings_percent: float = 0.0

    # Comparison with operator
    operator_cost_per_call: float = 0.0
    operator_monthly: float = 0.0
    roi_vs_operator_google: float = 0.0
    roi_vs_operator_whisper: float = 0.0


def calculate_costs(
    calls_per_day: int = 500,
    avg_duration: int = 90,
    operator_hourly_rate: float = 5.0,
) -> CostResult:
    """Calculate cost breakdown for given call volume."""

    profile = CallProfile(
        duration_seconds=avg_duration,
        stt_intervals_15s=max(1, avg_duration // 15 + 1),
    )

    calls_per_month = calls_per_day * 30
    result = CostResult(calls_per_day=calls_per_day, calls_per_month=calls_per_month)

    # --- STT costs ---
    # Google: per 15-second interval
    result.stt_google_per_call = profile.stt_intervals_15s * GOOGLE_STT_PER_15S

    # Whisper: amortized GPU server cost
    result.stt_whisper_per_call = WHISPER_SERVER_MONTHLY / calls_per_month if calls_per_month > 0 else 0

    # --- LLM costs ---
    total_input = profile.llm_input_tokens * profile.llm_turns
    total_output = profile.llm_output_tokens * profile.llm_turns
    result.llm_per_call = (
        total_input / 1_000_000 * CLAUDE_SONNET_INPUT_PER_1M
        + total_output / 1_000_000 * CLAUDE_SONNET_OUTPUT_PER_1M
    )

    # --- TTS costs ---
    billable_chars = profile.tts_characters * (1 - profile.tts_cache_hit_rate)
    result.tts_per_call = billable_chars / 1_000_000 * GOOGLE_TTS_PER_1M_CHARS

    # --- Per-call totals ---
    result.total_google_per_call = result.stt_google_per_call + result.llm_per_call + result.tts_per_call
    result.total_whisper_per_call = result.stt_whisper_per_call + result.llm_per_call + result.tts_per_call

    # --- Monthly costs ---
    result.stt_google_monthly = result.stt_google_per_call * calls_per_month
    result.stt_whisper_monthly = WHISPER_SERVER_MONTHLY
    result.llm_monthly = result.llm_per_call * calls_per_month
    result.tts_monthly = result.tts_per_call * calls_per_month

    result.infrastructure_monthly = (
        SIP_TRUNK_MONTHLY + VPS_APP_SERVER_MONTHLY + VPS_DB_SERVER_MONTHLY + MONITORING_MONTHLY
    )

    result.total_google_monthly = (
        result.stt_google_monthly + result.llm_monthly + result.tts_monthly + result.infrastructure_monthly
    )
    result.total_whisper_monthly = (
        result.stt_whisper_monthly + result.llm_monthly + result.tts_monthly + result.infrastructure_monthly
    )

    # --- Whisper savings ---
    result.whisper_savings_monthly = result.total_google_monthly - result.total_whisper_monthly
    if result.total_google_monthly > 0:
        result.whisper_savings_percent = result.whisper_savings_monthly / result.total_google_monthly * 100

    # --- Operator comparison ---
    avg_call_minutes = avg_duration / 60
    result.operator_cost_per_call = operator_hourly_rate * avg_call_minutes / 60
    result.operator_monthly = result.operator_cost_per_call * calls_per_month

    if result.operator_monthly > 0:
        result.roi_vs_operator_google = (
            (result.operator_monthly - result.total_google_monthly) / result.total_google_monthly * 100
        )
        result.roi_vs_operator_whisper = (
            (result.operator_monthly - result.total_whisper_monthly) / result.total_whisper_monthly * 100
        )

    return result


def print_report(result: CostResult) -> None:
    """Print human-readable cost report."""
    print("=" * 60)
    print("  CALL CENTER AI — COST ANALYSIS")
    print("=" * 60)
    print(f"\n  Volume: {result.calls_per_day} calls/day ({result.calls_per_month:,} calls/month)")

    print("\n--- Per-Call Cost Breakdown ---")
    print(f"  STT (Google):    ${result.stt_google_per_call:.4f}")
    print(f"  STT (Whisper):   ${result.stt_whisper_per_call:.4f}")
    print(f"  LLM (Claude):    ${result.llm_per_call:.4f}")
    print(f"  TTS (Google):    ${result.tts_per_call:.4f}")
    print("  ---")
    print(f"  Total (Google STT):  ${result.total_google_per_call:.4f}")
    print(f"  Total (Whisper STT): ${result.total_whisper_per_call:.4f}")

    print("\n--- Monthly Cost Breakdown ---")
    print(f"  STT (Google):        ${result.stt_google_monthly:>8.2f}")
    print(f"  STT (Whisper):       ${result.stt_whisper_monthly:>8.2f}")
    print(f"  LLM (Claude):        ${result.llm_monthly:>8.2f}")
    print(f"  TTS (Google):        ${result.tts_monthly:>8.2f}")
    print(f"  Infrastructure:      ${result.infrastructure_monthly:>8.2f}")
    print("  ---")
    print(f"  Total (Google STT):  ${result.total_google_monthly:>8.2f}/month")
    print(f"  Total (Whisper STT): ${result.total_whisper_monthly:>8.2f}/month")

    print("\n--- Whisper Savings ---")
    print(f"  Monthly savings:     ${result.whisper_savings_monthly:>8.2f}")
    print(f"  Savings percent:     {result.whisper_savings_percent:.1f}%")

    print("\n--- vs Operator ($5/hr) ---")
    print(f"  Operator per call:   ${result.operator_cost_per_call:.4f}")
    print(f"  Operator monthly:    ${result.operator_monthly:>8.2f}")
    print(f"  ROI (Google STT):    {result.roi_vs_operator_google:.0f}%")
    print(f"  ROI (Whisper STT):   {result.roi_vs_operator_whisper:.0f}%")

    target_low, target_high = 0.15, 0.30
    google_ok = target_low <= result.total_google_per_call <= target_high
    whisper_ok = target_low <= result.total_whisper_per_call <= target_high
    print("\n--- Target: $0.15-0.30 per call ---")
    print(f"  Google STT: ${result.total_google_per_call:.4f} {'OK' if google_ok else 'OUTSIDE TARGET'}")
    print(f"  Whisper STT: ${result.total_whisper_per_call:.4f} {'OK' if whisper_ok else 'OUTSIDE TARGET'}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Call Center AI cost analysis")
    parser.add_argument("--calls-per-day", type=int, default=500, help="Average calls per day")
    parser.add_argument("--avg-duration", type=int, default=90, help="Average call duration (seconds)")
    parser.add_argument("--operator-rate", type=float, default=5.0, help="Operator hourly rate (USD)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = calculate_costs(
        calls_per_day=args.calls_per_day,
        avg_duration=args.avg_duration,
        operator_hourly_rate=args.operator_rate,
    )

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print_report(result)


if __name__ == "__main__":
    main()
