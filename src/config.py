"""Application configuration loaded from environment variables."""

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


class StoreAPISettings(BaseSettings):
    url: str = "http://localhost:3000/api/v1"
    key: str = ""
    timeout: int = 5

    model_config = {"env_prefix": "STORE_API_"}


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


class AdminSettings(BaseSettings):
    jwt_secret: str = "change-me-in-production"
    username: str = "admin"
    password: str = "admin"

    model_config = {"env_prefix": "ADMIN_"}


class Settings(BaseSettings):
    """Root settings — aggregates all sub-settings."""

    audio_socket: AudioSocketSettings = AudioSocketSettings()
    google_stt: GoogleSTTSettings = GoogleSTTSettings()
    google_tts: GoogleTTSSettings = GoogleTTSSettings()
    anthropic: AnthropicSettings = AnthropicSettings()
    store_api: StoreAPISettings = StoreAPISettings()
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    ari: ARISettings = ARISettings()
    logging: LoggingSettings = LoggingSettings()
    celery: CelerySettings = CelerySettings()
    quality: QualitySettings = QualitySettings()
    admin: AdminSettings = AdminSettings()
    prometheus_port: int = 8080

    model_config = {"env_prefix": ""}


def get_settings() -> Settings:
    """Create and return application settings."""
    return Settings()
