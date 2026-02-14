"""Prometheus metrics definitions.

All metrics for the Call Center AI system.
Exposed via /metrics endpoint on the API server (port 8080).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

# --- Call metrics ---

active_calls = Gauge(
    "callcenter_active_calls",
    "Number of currently active calls",
)

call_duration_seconds = Histogram(
    "callcenter_call_duration_seconds",
    "Call duration in seconds",
    buckets=[10, 30, 60, 120, 180, 300, 600],
)

calls_total = Counter(
    "callcenter_calls_total",
    "Total number of calls processed",
    ["status"],  # completed, transferred, error
)

# --- Latency metrics ---

stt_latency_ms = Histogram(
    "callcenter_stt_latency_ms",
    "STT recognition latency in milliseconds",
    buckets=[50, 100, 200, 300, 500, 700, 1000],
)

llm_latency_ms = Histogram(
    "callcenter_llm_latency_ms",
    "LLM response latency in milliseconds (TTFT)",
    buckets=[100, 300, 500, 800, 1000, 1500, 2000, 3000],
)

tts_latency_ms = Histogram(
    "callcenter_tts_latency_ms",
    "TTS synthesis latency in milliseconds",
    buckets=[50, 100, 200, 300, 400, 600, 1000],
)

total_response_latency_ms = Histogram(
    "callcenter_total_response_latency_ms",
    "End-to-end response latency in milliseconds",
    buckets=[500, 1000, 1500, 2000, 2500, 3000, 5000],
)

# --- Tool call metrics ---

tool_call_duration_ms = Histogram(
    "callcenter_tool_call_duration_ms",
    "Tool call duration in milliseconds",
    ["tool_name"],
    buckets=[50, 100, 200, 500, 1000, 2000, 5000],
)

# --- Store API metrics ---

store_api_errors_total = Counter(
    "callcenter_store_api_errors_total",
    "Store API errors by status code",
    ["status_code"],
)

store_api_circuit_breaker_state = Gauge(
    "callcenter_store_api_circuit_breaker_state",
    "Circuit breaker state: 0=closed, 1=open, 2=half-open",
)

# --- Transfer metrics ---

transfers_to_operator_total = Counter(
    "callcenter_transfers_to_operator_total",
    "Transfers to operator by reason",
    ["reason"],
)

# --- TTS cache metrics ---

tts_cache_hits_total = Counter(
    "callcenter_tts_cache_hits_total",
    "TTS cache hit count",
)

tts_cache_misses_total = Counter(
    "callcenter_tts_cache_misses_total",
    "TTS cache miss count",
)


def get_metrics() -> bytes:
    """Generate Prometheus metrics output."""
    return generate_latest()
