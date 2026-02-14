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

audiosocket_to_stt_ms = Histogram(
    "callcenter_audiosocket_to_stt_ms",
    "Latency from AudioSocket packet receipt to STT feed in milliseconds",
    buckets=[5, 10, 20, 30, 50, 75, 100, 200],
)

tts_delivery_ms = Histogram(
    "callcenter_tts_delivery_ms",
    "Latency from TTS synthesis completion to AudioSocket send in milliseconds",
    buckets=[5, 10, 20, 30, 50, 75, 100, 200],
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

# --- Business metrics ---

calls_resolved_by_bot_total = Counter(
    "callcenter_calls_resolved_by_bot_total",
    "Calls resolved by bot without operator transfer",
)

orders_created_total = Counter(
    "callcenter_orders_created_total",
    "Orders created through bot",
)

fittings_booked_total = Counter(
    "callcenter_fittings_booked_total",
    "Fitting appointments booked through bot",
)

call_cost_usd = Histogram(
    "callcenter_call_cost_usd",
    "Cost of a single call in USD (STT + LLM + TTS)",
    buckets=[0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0],
)

# --- Scenario metrics ---

call_scenario_total = Counter(
    "callcenter_call_scenario_total",
    "Calls by scenario type",
    ["scenario"],  # tire_search, availability, order, fitting, consultation
)

# --- Operator queue metrics ---

operator_queue_length = Gauge(
    "callcenter_operator_queue_length",
    "Number of calls waiting in operator transfer queue",
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

# --- Celery workers metrics ---

celery_workers_online = Gauge(
    "callcenter_celery_workers_online",
    "Number of Celery workers currently responding to ping",
)

# --- Operational metrics (backup / partition management) ---

backup_last_success_timestamp = Gauge(
    "callcenter_backup_last_success_timestamp",
    "Unix timestamp of last successful backup",
    ["component"],  # postgres, redis, knowledge
)

backup_last_size_bytes = Gauge(
    "callcenter_backup_last_size_bytes",
    "Size in bytes of the last successful backup",
    ["component"],
)

backup_duration_seconds = Histogram(
    "callcenter_backup_duration_seconds",
    "Backup task duration in seconds",
    ["component"],
    buckets=[1, 5, 10, 30, 60, 120, 300],
)

backup_errors_total = Counter(
    "callcenter_backup_errors_total",
    "Total backup failures",
    ["component"],
)

partition_last_success_timestamp = Gauge(
    "callcenter_partition_last_success_timestamp",
    "Unix timestamp of last successful partition maintenance",
)

partition_created_total = Counter(
    "callcenter_partition_created_total",
    "Total partitions created",
)

partition_dropped_total = Counter(
    "callcenter_partition_dropped_total",
    "Total partitions dropped",
)

partition_errors_total = Counter(
    "callcenter_partition_errors_total",
    "Total partition management failures",
)


# --- Rate limiting metrics ---

rate_limit_exceeded_total = Counter(
    "callcenter_rate_limit_exceeded_total",
    "Rate limit exceeded count",
    ["endpoint", "ip"],
)


def get_metrics() -> bytes:
    """Generate Prometheus metrics output."""
    return generate_latest()
