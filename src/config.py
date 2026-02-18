"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from pydantic_settings import BaseSettings


class AudioSocketSettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 9092

    model_config = {"env_prefix": "AUDIOSOCKET_"}


class GoogleSTTSettings(BaseSettings):
    language_code: str = "uk-UA"
    alternative_languages: str = "ru-RU"

    model_config = {"env_prefix": "GOOGLE_STT_"}

    @property
    def alternative_language_list(self) -> list[str]:
        return [lang.strip() for lang in self.alternative_languages.split(",") if lang.strip()]


class GoogleTTSSettings(BaseSettings):
    voice: str = "uk-UA-Standard-A"
    speaking_rate: float = 1.0

    model_config = {"env_prefix": "GOOGLE_TTS_"}


class AnthropicSettings(BaseSettings):
    api_key: str = ""
    model: str = "claude-sonnet-4-5-20250929"

    model_config = {"env_prefix": "ANTHROPIC_"}


class OpenAISettings(BaseSettings):
    api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    model_config = {"env_prefix": "OPENAI_"}


class StoreAPISettings(BaseSettings):
    url: str = "http://localhost:3000/api/v1"
    key: str = ""
    timeout: int = 5

    model_config = {"env_prefix": "STORE_API_"}


class OneCSettings(BaseSettings):
    url: str = "http://192.168.11.9"
    username: str = ""
    password: str = ""
    timeout: int = 10
    sync_interval_minutes: int = 5
    stock_cache_ttl: int = 300  # Redis TTL for stock cache (seconds)

    model_config = {"env_prefix": "ONEC_"}


class DatabaseSettings(BaseSettings):
    url: str = "postgresql+asyncpg://callcenter:callcenter_dev_pass@localhost:5432/callcenter_dev"

    model_config = {"env_prefix": "DATABASE_"}


class RedisSettings(BaseSettings):
    url: str = "redis://localhost:6379/0"
    session_ttl: int = 1800

    model_config = {"env_prefix": "REDIS_"}


class ARISettings(BaseSettings):
    url: str = "http://localhost:8088/ari"
    user: str = "ari_user"
    password: str = "ari_password"

    model_config = {"env_prefix": "ARI_"}


class LoggingSettings(BaseSettings):
    level: str = "INFO"
    format: str = "json"

    model_config = {"env_prefix": "LOG_"}


class CelerySettings(BaseSettings):
    broker_url: str = "redis://localhost:6379/1"
    result_backend: str = "redis://localhost:6379/1"

    model_config = {"env_prefix": "CELERY_"}


class QualitySettings(BaseSettings):
    llm_model: str = "claude-haiku-4-5-20251001"
    score_threshold: float = 0.5

    model_config = {"env_prefix": "QUALITY_"}


class WhisperSettings(BaseSettings):
    model_size: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "uk"

    model_config = {"env_prefix": "WHISPER_"}


class FeatureFlagSettings(BaseSettings):
    stt_provider: str = "google"  # "google" or "whisper"
    llm_routing_enabled: bool = False
    whisper_rollout_percent: int = 0  # 0-100 gradual rollout

    model_config = {"env_prefix": "FF_"}


class AdminSettings(BaseSettings):
    jwt_secret: str = "change-me-in-production"
    jwt_ttl_hours: int = 24
    jwt_blacklist_ttl: int = 0  # seconds; 0 = use jwt_ttl_hours * 3600
    username: str = "admin"
    password: str = "admin"

    model_config = {"env_prefix": "ADMIN_"}

    @property
    def effective_blacklist_ttl(self) -> int:
        """Return blacklist TTL in seconds (defaults to JWT expiry time)."""
        return self.jwt_blacklist_ttl if self.jwt_blacklist_ttl > 0 else self.jwt_ttl_hours * 3600


class SMTPSettings(BaseSettings):
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = "callcenter@example.com"
    report_recipients: str = ""

    model_config = {"env_prefix": "SMTP_"}

    @property
    def recipient_list(self) -> list[str]:
        return [r.strip() for r in self.report_recipients.split(",") if r.strip()]


class BackupSettings(BaseSettings):
    backup_dir: str = "/var/backups/callcenter"
    retention_days: int = 7

    model_config = {"env_prefix": "BACKUP_"}


class DeepSeekSettings(BaseSettings):
    api_key: str = ""

    model_config = {"env_prefix": "DEEPSEEK_"}


class GeminiSettings(BaseSettings):
    api_key: str = ""

    model_config = {"env_prefix": "GEMINI_"}


class ScraperSettings(BaseSettings):
    enabled: bool = False
    base_url: str = "https://prokoleso.ua"
    info_path: str = "/ua/info/"
    max_pages: int = 3
    request_delay: float = 2.0
    auto_approve: bool = False
    llm_model: str = "claude-haiku-4-5-20251001"
    schedule_hour: int = 6
    schedule_day_of_week: str = "monday"

    model_config = {"env_prefix": "SCRAPER_"}


@dataclass
class ValidationError:
    """A single config validation error."""

    field: str
    message: str
    hint: str


@dataclass
class ValidationResult:
    """Result of config validation."""

    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def add(self, field_name: str, message: str, hint: str = "") -> None:
        self.errors.append(ValidationError(field=field_name, message=message, hint=hint))


class Settings(BaseSettings):
    """Root settings — aggregates all sub-settings."""

    audio_socket: AudioSocketSettings = AudioSocketSettings()
    google_stt: GoogleSTTSettings = GoogleSTTSettings()
    google_tts: GoogleTTSSettings = GoogleTTSSettings()
    anthropic: AnthropicSettings = AnthropicSettings()
    openai: OpenAISettings = OpenAISettings()
    store_api: StoreAPISettings = StoreAPISettings()
    onec: OneCSettings = OneCSettings()
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    ari: ARISettings = ARISettings()
    logging: LoggingSettings = LoggingSettings()
    celery: CelerySettings = CelerySettings()
    quality: QualitySettings = QualitySettings()
    admin: AdminSettings = AdminSettings()
    whisper: WhisperSettings = WhisperSettings()
    feature_flags: FeatureFlagSettings = FeatureFlagSettings()
    smtp: SMTPSettings = SMTPSettings()
    backup: BackupSettings = BackupSettings()
    deepseek: DeepSeekSettings = DeepSeekSettings()
    gemini: GeminiSettings = GeminiSettings()
    scraper: ScraperSettings = ScraperSettings()
    prometheus_port: int = 8080

    model_config = {"env_prefix": ""}

    def validate_required(self) -> ValidationResult:
        """Validate semantic correctness of required configuration.

        Pydantic already validates types; this checks that values
        are meaningful (non-empty keys, valid URL schemes, files exist).
        """
        result = ValidationResult()

        # ANTHROPIC_API_KEY — must be non-empty
        if not self.anthropic.api_key:
            result.add(
                "ANTHROPIC_API_KEY",
                "не задан",
                "Установите: export ANTHROPIC_API_KEY=sk-ant-...",
            )

        # DATABASE_URL — must start with postgresql
        db_url = self.database.url
        if not re.match(r"^postgresql(\+\w+)?://", db_url):
            result.add(
                "DATABASE_URL",
                f"неверный формат: {db_url!r}",
                "Ожидается postgresql:// или postgresql+asyncpg://",
            )

        # REDIS_URL — must start with redis://
        redis_url = self.redis.url
        parsed_redis = urlparse(redis_url)
        if parsed_redis.scheme not in ("redis", "rediss"):
            result.add(
                "REDIS_URL",
                f"неверный формат: {redis_url!r}",
                "Ожидается redis:// или rediss://",
            )

        # STORE_API_URL — must be a valid URL with scheme
        store_url = self.store_api.url
        parsed_store = urlparse(store_url)
        if not parsed_store.scheme or not parsed_store.netloc:
            result.add(
                "STORE_API_URL",
                f"невалидный URL: {store_url!r}",
                "Ожидается http://host:port/path или https://...",
            )

        # ONEC_URL — must be a valid URL with scheme (if credentials set)
        if self.onec.username:
            onec_url = self.onec.url
            parsed_onec = urlparse(onec_url)
            if not parsed_onec.scheme or not parsed_onec.netloc:
                result.add(
                    "ONEC_URL",
                    f"невалидный URL: {onec_url!r}",
                    "Ожидается http://host:port или https://...",
                )

        # GOOGLE_APPLICATION_CREDENTIALS — file must exist (if set)
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if creds_path and not os.path.isfile(creds_path):
            result.add(
                "GOOGLE_APPLICATION_CREDENTIALS",
                f"файл не найден: {creds_path!r}",
                "Укажите путь к существующему JSON-файлу сервисного аккаунта",
            )

        # ADMIN_JWT_SECRET — must not be the default in production
        if self.admin.jwt_secret == "change-me-in-production":
            result.add(
                "ADMIN_JWT_SECRET",
                "используется значение по умолчанию",
                "Установите: export ADMIN_JWT_SECRET=<random-secret>",
            )

        return result


def get_settings() -> Settings:
    """Create and return application settings."""
    return Settings()
