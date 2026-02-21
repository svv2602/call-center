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

# --- Streaming pipeline metrics ---

time_to_first_audio_ms = Histogram(
    "callcenter_time_to_first_audio_ms",
    "Time from turn start to first audio chunk sent (streaming pipeline)",
    buckets=[100, 200, 300, 500, 700, 1000, 1500, 2000, 3000, 5000],
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

celery_task_failures_total = Counter(
    "callcenter_celery_task_failures_total",
    "Celery task failures by task name",
    ["task_name"],
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


# --- Admin WebSocket metrics ---

admin_websocket_connections_active = Gauge(
    "callcenter_admin_websocket_connections_active",
    "Number of active admin WebSocket connections",
)

admin_websocket_messages_sent_total = Counter(
    "callcenter_admin_websocket_messages_sent_total",
    "Total WebSocket messages sent to admin clients",
)

# --- Auth metrics ---

jwt_logouts_total = Counter(
    "callcenter_jwt_logouts_total",
    "Total JWT token logouts (blacklisted tokens)",
)

# --- Rate limiting metrics ---

rate_limit_exceeded_total = Counter(
    "callcenter_rate_limit_exceeded_total",
    "Rate limit exceeded count",
    ["endpoint", "ip"],
)


# --- Whisper STT metrics ---

stt_whisper_latency_seconds = Histogram(
    "callcenter_stt_whisper_latency_seconds",
    "Whisper STT transcription latency in seconds",
    buckets=[0.1, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0],
)

stt_whisper_errors_total = Counter(
    "callcenter_stt_whisper_errors_total",
    "Whisper STT errors (triggering fallback to Google)",
    ["error_type"],  # connection_error, timeout, model_error
)

stt_whisper_fallback_total = Counter(
    "callcenter_stt_whisper_fallback_total",
    "Number of times Whisper STT fell back to Google STT",
)

stt_provider_requests_total = Counter(
    "callcenter_stt_provider_requests_total",
    "Total STT requests by provider",
    ["provider"],  # google, whisper
)

stt_provider_accuracy = Histogram(
    "callcenter_stt_provider_accuracy",
    "STT provider transcription confidence score (0-1) for A/B comparison",
    ["provider"],  # google, whisper
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)


# --- LLM multi-provider routing metrics ---

llm_requests_total = Counter(
    "callcenter_llm_requests_total",
    "Total LLM requests by provider and task",
    ["provider", "task"],
)

llm_errors_total = Counter(
    "callcenter_llm_errors_total",
    "Total LLM errors by provider and task",
    ["provider", "task"],
)

llm_provider_latency_ms = Histogram(
    "callcenter_llm_provider_latency_ms",
    "LLM response latency per provider in milliseconds",
    ["provider"],
    buckets=[100, 300, 500, 800, 1000, 1500, 2000, 3000, 5000],
)

llm_fallbacks_total = Counter(
    "callcenter_llm_fallbacks_total",
    "LLM fallback activations (primary failed, using alternative)",
    ["from_provider", "to_provider", "task"],
)

# --- Barge-in metrics ---

barge_in_total = Counter(
    "callcenter_barge_in_total",
    "Barge-in interruptions detected (caller spoke while agent was speaking)",
)


def get_metrics() -> bytes:
    """Generate Prometheus metrics output."""
    return generate_latest()
