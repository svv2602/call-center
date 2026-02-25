"""Tests for LLM pricing catalog sync from LiteLLM."""

from __future__ import annotations

from src.tasks.pricing_sync import (
    PROVIDER_MAP,
    _generate_display_name,
    _parse_litellm_json,
)


class TestGenerateDisplayName:
    def test_simple_model(self) -> None:
        result = _generate_display_name("gpt-5-mini")
        assert result == "Gpt 5 Mini"

    def test_with_version(self) -> None:
        assert _generate_display_name("gemini-2.5-flash") == "Gemini 2.5 Flash"

    def test_strip_provider_prefix(self) -> None:
        result = _generate_display_name("openai/gpt-5-mini")
        assert result == "Gpt 5 Mini"

    def test_anthropic_prefix(self) -> None:
        assert _generate_display_name("anthropic/claude-sonnet-4-5") == "Claude Sonnet 4 5"

    def test_deepseek_prefix(self) -> None:
        assert _generate_display_name("deepseek/deepseek-chat") == "Deepseek Chat"

    def test_underscores(self) -> None:
        assert _generate_display_name("some_model_name") == "Some Model Name"


class TestParseLitellmJson:
    def test_filters_chat_mode_only(self) -> None:
        data = {
            "gpt-5-mini": {
                "mode": "chat",
                "litellm_provider": "openai",
                "input_cost_per_token": 2.5e-7,
                "output_cost_per_token": 2e-6,
                "max_input_tokens": 1000000,
                "max_output_tokens": 100000,
            },
            "dall-e-3": {
                "mode": "image_generation",
                "litellm_provider": "openai",
                "input_cost_per_token": 0.0,
                "output_cost_per_token": 0.0,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 1
        assert rows[0]["model_key"] == "gpt-5-mini"

    def test_filters_supported_providers(self) -> None:
        data = {
            "gpt-5-mini": {
                "mode": "chat",
                "litellm_provider": "openai",
                "input_cost_per_token": 2.5e-7,
                "output_cost_per_token": 2e-6,
            },
            "cohere-command": {
                "mode": "chat",
                "litellm_provider": "cohere_chat",
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 1
        assert rows[0]["provider_type"] == "openai"

    def test_skips_fine_tuned(self) -> None:
        data = {
            "ft:gpt-5-mini:custom": {
                "mode": "chat",
                "litellm_provider": "openai",
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 0

    def test_price_conversion(self) -> None:
        data = {
            "test-model": {
                "mode": "chat",
                "litellm_provider": "openai",
                "input_cost_per_token": 3e-7,
                "output_cost_per_token": 1.5e-5,
                "max_input_tokens": 128000,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 1
        assert rows[0]["input_price_per_1m"] == 0.3
        assert rows[0]["output_price_per_1m"] == 15.0

    def test_skips_missing_prices(self) -> None:
        data = {
            "no-price-model": {
                "mode": "chat",
                "litellm_provider": "openai",
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 0

    def test_gemini_via_vertex(self) -> None:
        data = {
            "gemini-2.5-flash": {
                "mode": "chat",
                "litellm_provider": "vertex_ai-language-models",
                "input_cost_per_token": 3e-7,
                "output_cost_per_token": 2.5e-6,
                "max_input_tokens": 1000000,
                "max_output_tokens": 65536,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 1
        assert rows[0]["provider_type"] == "gemini"

    def test_zero_prices_allowed(self) -> None:
        data = {
            "free-model": {
                "mode": "chat",
                "litellm_provider": "openai",
                "input_cost_per_token": 0.0,
                "output_cost_per_token": 0.0,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 1
        assert rows[0]["input_price_per_1m"] == 0.0

    def test_multiple_providers(self) -> None:
        data = {
            "gpt-5-mini": {
                "mode": "chat",
                "litellm_provider": "openai",
                "input_cost_per_token": 2.5e-7,
                "output_cost_per_token": 2e-6,
            },
            "claude-sonnet-4-5": {
                "mode": "chat",
                "litellm_provider": "anthropic",
                "input_cost_per_token": 3e-6,
                "output_cost_per_token": 1.5e-5,
            },
            "deepseek-chat": {
                "mode": "chat",
                "litellm_provider": "deepseek",
                "input_cost_per_token": 2.7e-7,
                "output_cost_per_token": 1.1e-6,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 3
        providers = {r["provider_type"] for r in rows}
        assert providers == {"openai", "anthropic", "deepseek"}

    def test_ignores_non_dict_entries(self) -> None:
        data = {
            "sample_spec": "some string value",
            "gpt-5-mini": {
                "mode": "chat",
                "litellm_provider": "openai",
                "input_cost_per_token": 2.5e-7,
                "output_cost_per_token": 2e-6,
            },
        }
        rows = _parse_litellm_json(data)
        assert len(rows) == 1


class TestProviderMap:
    def test_all_supported_providers_mapped(self) -> None:
        assert "openai" in PROVIDER_MAP
        assert "anthropic" in PROVIDER_MAP
        assert "deepseek" in PROVIDER_MAP
        assert "vertex_ai-language-models" in PROVIDER_MAP

    def test_gemini_mapping(self) -> None:
        assert PROVIDER_MAP["vertex_ai-language-models"] == "gemini"
