"""Tests for cost optimization components."""

from __future__ import annotations

import pytest

from src.agent.model_router import ModelRouter
from src.monitoring.cost_tracker import CostBreakdown


class TestModelRouter:
    """Tests for LLM model routing."""

    def setup_method(self) -> None:
        self.router = ModelRouter(
            haiku_model="claude-haiku",
            sonnet_model="claude-sonnet",
            enabled=True,
        )

    def test_order_status_with_order_word_routes_to_sonnet(self) -> None:
        """'замовлення' matches COMPLEX_PATTERNS (замов*), so routes to Sonnet."""
        model = self.router.select_model("Де мій замовлення номер 12345?")
        assert model == "claude-sonnet"

    def test_simple_availability_routes_to_haiku(self) -> None:
        model = self.router.select_model("Чи є в наявності шини 205/55 R16?")
        assert model == "claude-haiku"

    def test_simple_greeting_routes_to_haiku(self) -> None:
        model = self.router.select_model("Добрий день")
        assert model == "claude-haiku"

    def test_simple_yes_routes_to_haiku(self) -> None:
        model = self.router.select_model("Так")
        assert model == "claude-haiku"

    def test_operator_request_routes_to_haiku(self) -> None:
        model = self.router.select_model("Переключіть на оператора")
        assert model == "claude-haiku"

    def test_complex_comparison_routes_to_sonnet(self) -> None:
        model = self.router.select_model("Яка різниця між Michelin і Continental?")
        assert model == "claude-sonnet"

    def test_complex_order_creation_routes_to_sonnet(self) -> None:
        model = self.router.select_model("Хочу замовити шини")
        assert model == "claude-sonnet"

    def test_complex_fitting_routes_to_sonnet(self) -> None:
        model = self.router.select_model("Хочу записатися на шиномонтаж")
        assert model == "claude-sonnet"

    def test_complex_technical_routes_to_sonnet(self) -> None:
        model = self.router.select_model("Які характеристики цих шин?")
        assert model == "claude-sonnet"

    def test_active_order_always_sonnet(self) -> None:
        model = self.router.select_model("Так", has_active_order=True)
        assert model == "claude-sonnet"

    def test_many_turns_always_sonnet(self) -> None:
        model = self.router.select_model("Так", turn_count=5)
        assert model == "claude-sonnet"

    def test_unrecognized_defaults_to_sonnet(self) -> None:
        model = self.router.select_model("абракадабра фыва олдж")
        assert model == "claude-sonnet"

    def test_disabled_routing_always_sonnet(self) -> None:
        router = ModelRouter(enabled=False)
        model = router.select_model("Де мій замовлення?")
        assert "sonnet" in model

    def test_classify_order_scenario(self) -> None:
        assert self.router.classify_scenario("Хочу замовити шини") == "order"

    def test_classify_fitting_scenario(self) -> None:
        assert self.router.classify_scenario("Записатися на шиномонтаж") == "fitting"

    def test_classify_availability_scenario(self) -> None:
        assert self.router.classify_scenario("Чи є в наявності?") == "availability"

    def test_classify_consultation_scenario(self) -> None:
        assert self.router.classify_scenario("Порівняйте ці шини") == "consultation"

    def test_classify_tire_search_scenario(self) -> None:
        assert self.router.classify_scenario("Шукаю шини 205/55") == "tire_search"

    def test_classify_other_scenario(self) -> None:
        assert self.router.classify_scenario("Привіт") == "other"


class TestCostBreakdown:
    """Tests for call cost calculation."""

    def test_empty_cost(self) -> None:
        cost = CostBreakdown()
        assert cost.total_cost == 0.0

    def test_stt_google_cost(self) -> None:
        cost = CostBreakdown()
        cost.add_stt_usage(30.0, provider="google")  # 30 seconds
        assert cost.stt_cost > 0
        # 30s = 2 intervals of 15s + 1 = 3 intervals * $0.006 = $0.018
        assert cost.stt_cost == pytest.approx(0.018)

    def test_stt_whisper_cost(self) -> None:
        cost = CostBreakdown()
        cost.add_stt_usage(30.0, provider="whisper")
        assert cost.stt_cost == pytest.approx(0.01)  # flat rate per call

    def test_llm_sonnet_cost(self) -> None:
        cost = CostBreakdown()
        cost.add_llm_usage(1000, 200, provider_key="anthropic-sonnet")
        # input: 1000/1M * $3.00 = 0.003
        # output: 200/1M * $15.00 = 0.003
        assert cost.llm_cost == pytest.approx(0.006)

    def test_llm_haiku_cost(self) -> None:
        cost = CostBreakdown()
        cost.add_llm_usage(1000, 200, provider_key="anthropic-haiku")
        # input: 1000/1M * $1.00 = 0.001
        # output: 200/1M * $5.00 = 0.001
        assert cost.llm_cost == pytest.approx(0.002)

    def test_llm_gemini_flash_cost(self) -> None:
        cost = CostBreakdown()
        cost.add_llm_usage(1000, 200, provider_key="gemini-2.5-flash")
        # input: 1000/1M * $0.30 = 0.0003
        # output: 200/1M * $2.50 = 0.0005
        assert cost.llm_cost == pytest.approx(0.0008)

    def test_haiku_much_cheaper_than_sonnet(self) -> None:
        sonnet = CostBreakdown()
        sonnet.add_llm_usage(1000, 200, provider_key="anthropic-sonnet")

        haiku = CostBreakdown()
        haiku.add_llm_usage(1000, 200, provider_key="anthropic-haiku")

        assert haiku.llm_cost < sonnet.llm_cost * 0.5  # Haiku is ~3x cheaper

    def test_tts_cost(self) -> None:
        cost = CostBreakdown()
        cost.add_tts_usage(500, cached=False)
        # 500 chars / 1000 * $0.004 = $0.002
        assert cost.tts_cost == pytest.approx(0.002)

    def test_tts_cached_is_free(self) -> None:
        cost = CostBreakdown()
        cost.add_tts_usage(500, cached=True)
        assert cost.tts_cost == 0.0

    def test_total_cost(self) -> None:
        cost = CostBreakdown()
        cost.add_stt_usage(30.0)
        cost.add_llm_usage(1000, 200)
        cost.add_tts_usage(500)
        assert cost.total_cost == cost.stt_cost + cost.llm_cost + cost.tts_cost

    def test_to_dict(self) -> None:
        cost = CostBreakdown()
        cost.add_stt_usage(30.0, provider="google")
        cost.add_llm_usage(1000, 200, provider_key="anthropic-sonnet")
        cost.add_tts_usage(500)

        d = cost.to_dict()
        assert "stt_cost" in d
        assert "llm_cost" in d
        assert "tts_cost" in d
        assert "total_cost" in d
        assert d["stt_provider"] == "google"
        assert d["llm_model"] == "anthropic-sonnet"
        assert d["llm_input_tokens"] == 1000
        assert d["llm_output_tokens"] == 200
        assert d["tts_characters"] == 500

    def test_whisper_savings(self) -> None:
        """Verify Whisper provides cost savings over Google STT."""
        google_cost = CostBreakdown()
        google_cost.add_stt_usage(60.0, provider="google")  # 1 min call

        whisper_cost = CostBreakdown()
        whisper_cost.add_stt_usage(60.0, provider="whisper")

        assert whisper_cost.stt_cost < google_cost.stt_cost
