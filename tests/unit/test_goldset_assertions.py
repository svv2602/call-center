"""Goldset harness: assertions on recorded replies, network fan-out, case schema.

No LLM is called: every reply below is a recorded or hand-written bot turn.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest

from scripts import run_goldset as rg

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "goldset" / "schema.json").read_text())

# Recorded Tvoya Shina reply to a delivery question (the good answer there).
TS_DELIVERY = "Доставка по Україні безкоштовна, Новою поштою, зазвичай один-три дні."
# The same sentence heard in a Pro Koleso call — the leak the harness exists for.
PK_DELIVERY_LEAK = TS_DELIVERY
PK_DELIVERY_OK = "Доставка Новою поштою за тарифами перевізника, зазвичай від одного до трьох днів."


def obs(text: str, *calls: tuple[str, dict[str, Any]]) -> rg.TurnObservation:
    return rg.TurnObservation(response_text=text, tool_calls=[rg.ToolCall(n, a) for n, a in calls])


def failed(results: list[rg.AssertionResult]) -> set[str]:
    return {r.name for r in results if not r.passed}


def case(**over: Any) -> rg.Case:
    raw: dict[str, Any] = {
        "id": "c1",
        "network": "both",
        "sales_enabled": True,
        "turns": [{"user": "скільки коштує доставка?", "expect": {"must_contain": ["доставк"]}}],
    }
    raw.update(over)
    return rg.parse_case(raw)


# ── phrase assertions ─────────────────────────────────────────────────────


class TestPhraseAssertions:
    def test_must_not_contain_catches_a_recorded_bad_reply(self) -> None:
        bad = obs("Добре, ваше замовлення підтверджено, чекайте дзвінка.")
        res = rg.check_must_not_contain([r"замовлення\s+(?:\w+\s+){0,2}підтверджен"], bad)
        assert not res.passed
        assert "підтверджен" in res.detail

    def test_must_not_contain_passes_a_clean_reply(self) -> None:
        good = obs("Заявку прийнято, менеджер вам зателефонує.")
        assert rg.check_must_not_contain([r"замовлення\s+(?:\w+\s+){0,2}підтверджен"], good).passed

    def test_must_contain_needs_every_pattern(self) -> None:
        reply = obs("Можна оплатити частинами через monobank.")
        assert rg.check_must_contain(["частинами", "monobank"], reply).passed
        res = rg.check_must_contain(["частинами", "приват"], reply)
        assert not res.passed and "приват" in res.detail

    def test_patterns_ignore_case(self) -> None:
        assert rg.check_must_contain(
            ["bridgestone"], obs("Розширена гарантія — на Bridgestone.")
        ).passed

    def test_ru_and_ua_forms_via_alternation(self) -> None:
        pattern = ["бесплатн|безкоштовн"]
        assert rg.check_must_contain(pattern, obs("Доставка безкоштовна.")).passed
        assert rg.check_must_contain(pattern, obs("Доставка бесплатная.")).passed

    def test_max_sentences(self) -> None:
        three = obs("Так. Можна частинами! Ще питання?")
        assert rg.count_sentences(three.response_text) == 3
        assert rg.check_max_sentences(3, three).passed
        assert not rg.check_max_sentences(2, three).passed

    def test_sentence_without_final_stop_counts(self) -> None:
        assert rg.count_sentences("Доставка безкоштовна. Оплата частинами") == 2
        assert rg.count_sentences("   ") == 0

    def test_decimal_point_is_not_a_sentence_end(self) -> None:
        assert rg.count_sentences("Ширина 7.5 дюйма підходить.") == 1


# ── tool assertions ───────────────────────────────────────────────────────


class TestToolAssertions:
    def test_tool_called_by_name(self) -> None:
        assert rg.check_tool_called(["search_tires"], obs("", ("search_tires", {}))).passed
        assert not rg.check_tool_called(["search_tires"], obs("", ("get_fitting_price", {}))).passed

    def test_tool_called_with_argument_regex(self) -> None:
        spec = [{"name": "search_tires", "args": {"width": "^155$", "diameter": "^13$"}}]
        right = obs("", ("search_tires", {"width": 155, "profile": 70, "diameter": 13}))
        wrong = obs("", ("search_tires", {"width": 165, "profile": 70, "diameter": 13}))
        missing_arg = obs("", ("search_tires", {"width": 155}))
        assert rg.check_tool_called(spec, right).passed
        assert not rg.check_tool_called(spec, wrong).passed
        assert not rg.check_tool_called(spec, missing_arg).passed

    def test_tool_not_called(self) -> None:
        res = rg.check_tool_not_called(
            ["transfer_to_operator"], obs("", ("transfer_to_operator", {}))
        )
        assert not res.passed
        assert rg.check_tool_not_called(
            ["transfer_to_operator"], obs("", ("search_tires", {}))
        ).passed

    def test_transfer_reason_is_exact(self) -> None:
        call = ("transfer_to_operator", {"reason": "non_fitting_scope"})
        assert rg.check_transfer_reason("non_fitting_scope", obs("", call)).passed
        other = obs("", ("transfer_to_operator", {"reason": "customer_request"}))
        res = rg.check_transfer_reason("non_fitting_scope", other)
        assert not res.passed and "customer_request" in res.detail
        # A reason that merely contains the wanted one is not it.
        longer = obs("", ("transfer_to_operator", {"reason": "non_fitting_scope_v2"}))
        assert not rg.check_transfer_reason("non_fitting_scope", longer).passed

    def test_no_transfer_is_not_a_transfer_reason(self) -> None:
        assert not rg.check_transfer_reason("non_fitting_scope", obs("Шукаю шини.")).passed


# ── network leak ──────────────────────────────────────────────────────────


class TestNetworkLeak:
    def test_free_delivery_in_prokoleso_is_a_leak(self) -> None:
        res = rg.check_network_leak("prokoleso", obs(PK_DELIVERY_LEAK))
        assert not res.passed
        assert "prokoleso" in res.detail

    def test_free_delivery_in_tvoya_shina_is_not_a_leak(self) -> None:
        assert rg.check_network_leak("tvoya-shina", obs(TS_DELIVERY)).passed

    def test_carrier_tariffs_in_tvoya_shina_is_a_leak(self) -> None:
        assert not rg.check_network_leak("tvoya-shina", obs(PK_DELIVERY_OK)).passed
        assert rg.check_network_leak("prokoleso", obs(PK_DELIVERY_OK)).passed

    @pytest.mark.parametrize(
        "reply",
        [
            "Ми працюємо як Твоя Шина вже десять років.",
            "У Твоїй Шині можна записатися.",
            "Є розширена гарантія на Bridgestone.",
            "Розширена гарантія до 5 років на Bridgestone.",
            "Можу записати вас на шиномонтаж на завтра.",
            "Шини можна залишити на зберігання до весни.",
        ],
    )
    def test_tvoya_shina_terms_leak_into_prokoleso(self, reply: str) -> None:
        assert not rg.check_network_leak("prokoleso", obs(reply)).passed
        assert rg.check_network_leak("tvoya-shina", obs(reply)).passed

    @pytest.mark.parametrize(
        "reply",
        [
            "Дякуємо, що звернулися в Про Колесо.",
            "Шиномонтаж ми не надаємо.",
            "На жаль, ми не надаємо послуги зберігання.",
        ],
    )
    def test_prokoleso_terms_leak_into_tvoya_shina(self, reply: str) -> None:
        assert not rg.check_network_leak("tvoya-shina", obs(reply)).passed
        assert rg.check_network_leak("prokoleso", obs(reply)).passed

    def test_denying_extended_warranty_is_not_a_leak(self) -> None:
        reply = "Розширеної гарантії немає, діє стандартна гарантія виробника."
        assert rg.check_network_leak("prokoleso", obs(reply)).passed

    def test_every_network_has_phrases_and_sees_only_the_others(self) -> None:
        for net in rg.NETWORKS:
            assert rg.NETWORK_ONLY_PHRASES[net], net
            own = set(rg.NETWORK_ONLY_PHRASES[net])
            foreign = set(rg.foreign_phrases(net))
            assert foreign and not (own & foreign)
            others = {p for n in rg.NETWORKS if n != net for p in rg.NETWORK_ONLY_PHRASES[n]}
            assert foreign == others

    def test_leak_is_checked_on_every_turn_by_default(self) -> None:
        res = rg.evaluate_turn({}, "prokoleso", obs(PK_DELIVERY_LEAK))
        assert failed(res) == {"network_leak"}

    def test_leak_check_can_be_switched_off_per_turn(self) -> None:
        res = rg.evaluate_turn({"network_leak": False}, "prokoleso", obs(PK_DELIVERY_LEAK))
        assert res == []


class TestEvaluateTurn:
    def test_turn_error_fails_the_turn(self) -> None:
        o = rg.TurnObservation(response_text="", error="timeout")
        assert "turn_error" in failed(rg.evaluate_turn({}, "tvoya-shina", o))

    def test_every_assertion_key_is_evaluated(self) -> None:
        expect = {
            "must_contain": ["нема такого"],
            "must_not_contain": ["доставк"],
            "tool_called": ["search_tires"],
            "tool_not_called": ["transfer_to_operator"],
            "transfer_reason": "customer_request",
            "max_sentences": 1,
            "network_leak": True,
        }
        reply = obs(PK_DELIVERY_LEAK + " Ще щось.", ("transfer_to_operator", {"reason": "x"}))
        assert failed(rg.evaluate_turn(expect, "prokoleso", reply)) == set(rg.ASSERTION_KEYS)

    def test_network_expectations_merge_over_common(self) -> None:
        turn = rg.Turn(
            user="x",
            expect={"must_not_contain": ["якому місті"], "max_sentences": 4},
            expect_by_network={"prokoleso": {"must_not_contain": ["\\d+ грн"], "max_sentences": 2}},
        )
        pk = rg.effective_expect(turn, "prokoleso")
        ts = rg.effective_expect(turn, "tvoya-shina")
        assert pk["must_not_contain"] == ["якому місті", "\\d+ грн"]
        assert pk["max_sentences"] == 2
        assert ts == turn.expect
        assert turn.expect["must_not_contain"] == ["якому місті"]  # not mutated by the merge


# ── case schema ───────────────────────────────────────────────────────────


class TestParseCase:
    def test_both_expands_to_every_network(self) -> None:
        assert case().networks == rg.NETWORKS

    def test_default_network_is_both(self) -> None:
        raw = {"id": "c", "sales_enabled": False, "turns": [{"user": "так"}]}
        assert rg.parse_case(raw).networks == rg.NETWORKS

    def test_list_network(self) -> None:
        assert case(network=["prokoleso"]).networks == ("prokoleso",)

    @pytest.mark.parametrize(
        ("over", "msg"),
        [
            ({"id": "Bad-Id"}, "bad id"),
            ({"network": ["rozetka"]}, "network"),
            ({"sales_enabled": None}, "sales_enabled"),
            ({"pending": "later"}, "pending"),
            ({"colour": "red"}, "unknown key"),
            ({"turns": []}, "turns"),
            ({"turns": [{"user": " "}]}, "user text"),
            ({"turns": [{"user": "так", "expect": {"must_include": ["x"]}}]}, "unknown assertion"),
            ({"turns": [{"user": "так", "expect": {"must_contain": ["("]}}]}, "bad regex"),
            ({"turns": [{"user": "так", "expect": {"max_sentences": 0}}]}, "max_sentences"),
            (
                {"turns": [{"user": "так", "expect": {"tool_called": [{"args": {}}]}}]},
                "tool_called",
            ),
            (
                {
                    "network": ["prokoleso"],
                    "turns": [{"user": "т", "expect_by_network": {"tvoya-shina": {}}}],
                },
                "expect_by_network",
            ),
        ],
    )
    def test_rejects(self, over: dict[str, Any], msg: str) -> None:
        with pytest.raises(rg.CaseError, match=msg):
            case(**over)

    def test_pending_needs_checklist_and_reason(self) -> None:
        ok = case(pending="wave-3-G-sales-scope-switch: until the switch")
        assert ok.pending.startswith("wave-3-G")
        with pytest.raises(rg.CaseError):
            case(pending="wave-3-G-sales-scope-switch")


class TestSchemaFileMatchesCode:
    def test_case_keys(self) -> None:
        assert set(SCHEMA["properties"]) == set(rg.CASE_KEYS)

    def test_turn_keys(self) -> None:
        assert set(SCHEMA["properties"]["turns"]["items"]["properties"]) == set(rg.TURN_KEYS)

    def test_assertion_keys(self) -> None:
        assert set(SCHEMA["definitions"]["expect"]["properties"]) == set(rg.ASSERTION_KEYS)

    def test_networks(self) -> None:
        by_net = SCHEMA["properties"]["turns"]["items"]["properties"]["expect_by_network"]
        assert set(by_net["properties"]) == set(rg.NETWORKS)

    def test_pending_pattern(self) -> None:
        assert SCHEMA["properties"]["pending"]["pattern"] == rg._PENDING_RE.pattern


# ── runner wiring ─────────────────────────────────────────────────────────


class Recorder:
    """Agent factory + turn player that replay recorded replies per network."""

    def __init__(self, replies: dict[str, list[rg.TurnObservation]]) -> None:
        self.replies = replies
        self.built: list[tuple[str, str]] = []
        self.played: list[tuple[str, str, int]] = []

    async def make_agent(self, network: str, c: rg.Case) -> Any:
        self.built.append((network, c.id))
        agent = MagicMock(spec=["network", "turn"])
        agent.network = network
        agent.turn = 0
        return agent

    async def play_turn(
        self, agent: Any, user: str, history: list[dict[str, Any]]
    ) -> tuple[rg.TurnObservation, list[dict[str, Any]]]:
        self.played.append((agent.network, user, len(history)))
        o = self.replies[agent.network][agent.turn]
        agent.turn += 1
        return o, [
            *history,
            {"role": "user", "content": user},
            {"role": "assistant", "content": o.response_text},
        ]


def delivery_case() -> rg.Case:
    return rg.parse_case(
        {
            "id": "delivery_cost",
            "network": "both",
            "sales_enabled": True,
            "turns": [
                {"user": "привіт"},
                {
                    "user": "скільки коштує доставка?",
                    "expect_by_network": {
                        "tvoya-shina": {"must_contain": ["безкоштовн"]},
                        "prokoleso": {"must_contain": ["тариф"]},
                    },
                },
            ],
        }
    )


class TestRunnerWiring:
    def test_both_runs_the_case_once_per_network(self) -> None:
        runs, skipped = rg.expand_runs([delivery_case()])
        assert [r.network for r in runs] == list(rg.NETWORKS)
        assert skipped == []

    def test_every_network_gets_its_own_agent_and_history(self) -> None:
        rec = Recorder(
            {
                "tvoya-shina": [obs("Вітаю."), obs(TS_DELIVERY)],
                "prokoleso": [obs("Вітаю."), obs(PK_DELIVERY_OK)],
            }
        )
        runs, _ = rg.expand_runs([delivery_case()])
        results = asyncio.run(rg.run_all(runs, rec.make_agent, rec.play_turn))
        assert sorted(rec.built) == sorted((n, "delivery_cost") for n in rg.NETWORKS)
        assert {(r.network, r.passed) for r in results} == {(n, True) for n in rg.NETWORKS}
        # History is carried within a run and starts empty in each network.
        for net in rg.NETWORKS:
            assert [h for n, _, h in rec.played if n == net] == [0, 2]

    def test_network_expectation_and_leak_decide_per_network(self) -> None:
        # Both networks answer with the Tvoya Shina sentence.
        rec = Recorder(
            {
                "tvoya-shina": [obs("Вітаю."), obs(TS_DELIVERY)],
                "prokoleso": [obs("Вітаю."), obs(PK_DELIVERY_LEAK)],
            }
        )
        runs, _ = rg.expand_runs([delivery_case()])
        results = {
            r.network: r for r in asyncio.run(rg.run_all(runs, rec.make_agent, rec.play_turn))
        }
        assert results["tvoya-shina"].passed
        assert {f.name for f in results["prokoleso"].failures()} == {"must_contain", "network_leak"}

    def test_network_filter(self) -> None:
        runs, _ = rg.expand_runs([delivery_case()], network="prokoleso")
        assert [r.network for r in runs] == ["prokoleso"]

    def test_single_network_case_is_not_played_elsewhere(self) -> None:
        c = case(id="pk_only", network=["prokoleso"])
        assert [r.network for r in rg.expand_runs([c])[0]] == ["prokoleso"]
        assert rg.expand_runs([c], network="tvoya-shina")[0] == []

    def test_pending_is_skipped_with_its_reason(self) -> None:
        c = case(pending="wave-2-D-order-finish-is-a-request: not merged")
        runs, skipped = rg.expand_runs([c])
        assert runs == []
        assert skipped == [("c1", "pending wave-2-D-order-finish-is-a-request: not merged")]
        runs, skipped = rg.expand_runs([c], include_pending=True)
        assert len(runs) == len(rg.NETWORKS) and skipped == []

    def test_case_filter_glob(self) -> None:
        cases = [case(id="pk_storage"), case(id="ts_storage"), case(id="delivery")]
        runs, _ = rg.expand_runs(cases, case_patterns=["*_storage"])
        assert {r.case.id for r in runs} == {"pk_storage", "ts_storage"}


class TestReport:
    def _results(self) -> list[rg.RunResult]:
        ok = rg.AssertionResult("must_contain", True)
        bad = rg.AssertionResult("network_leak", False, "prokoleso: [...]")
        return [
            rg.RunResult("a", "tvoya-shina", [rg.TurnReport("u", "r", [], [ok])]),
            rg.RunResult("a", "prokoleso", [rg.TurnReport("u", "r", [], [ok, bad])]),
            rg.RunResult("b", "tvoya-shina", [rg.TurnReport("u", "r", [], [ok])]),
        ]

    def test_counts_green_runs_not_assertions(self) -> None:
        text = rg.format_report(self._results(), [("c", "pending wave-3-G-x: y")])
        assert "Зелёних кейсів: 2/3" in text
        assert "tvoya-shina: 2/2" in text and "prokoleso: 0/1" in text
        assert "зелених у всіх своїх мережах: 1/2" in text
        assert "network_leak: 1" in text
        assert "Пропущено (pending): 1" in text and "wave-3-G-x" in text


class TestCost:
    def test_estimate_scales_with_turns(self) -> None:
        runs, _ = rg.expand_runs([delivery_case()])
        turns, usd = rg.estimate_cost(
            runs, "gpt-4.1-mini", input_per_turn=1_000_000, output_per_turn=0
        )
        assert turns == 4
        assert usd == pytest.approx(4 * 0.40)

    def test_unknown_model_has_no_price(self) -> None:
        runs, _ = rg.expand_runs([delivery_case()])
        assert rg.estimate_cost(runs, "mystery-model")[1] is None


class TestPaidRunNeedsYes:
    def _patch(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        import sqlalchemy.ext.asyncio as sa_async
        from sqlalchemy.ext.asyncio import AsyncEngine

        import src.llm.router as router_mod

        engine = create_autospec(AsyncEngine, instance=True)
        router = create_autospec(router_mod.LLMRouter, instance=True)
        monkeypatch.setattr(sa_async, "create_async_engine", lambda *a, **k: engine)
        monkeypatch.setattr(router_mod, "LLMRouter", lambda: router)

        async def tenants(_engine: Any) -> dict[str, dict[str, Any]]:
            return {n: {"id": n, "slug": n, "config": {}} for n in rg.NETWORKS}

        monkeypatch.setattr(rg, "_load_tenants", tenants)
        run_all = create_autospec(rg.run_all, return_value=[])
        monkeypatch.setattr(rg, "run_all", run_all)
        return run_all

    def _opts(self, yes: bool) -> argparse.Namespace:
        return argparse.Namespace(provider=None, yes=yes, input_tokens_per_turn=1000, json_out="")

    def test_without_yes_nothing_is_played(
        self, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        run_all = self._patch(monkeypatch)
        runs, _ = rg.expand_runs([delivery_case()])
        assert asyncio.run(rg._live(runs, [], self._opts(yes=False))) == 0
        run_all.assert_not_called()
        assert "--yes" in capsys.readouterr().out

    def test_with_yes_every_run_is_played(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run_all = self._patch(monkeypatch)
        runs, _ = rg.expand_runs([delivery_case()])
        asyncio.run(rg._live(runs, [], self._opts(yes=True)))
        run_all.assert_called_once()
        assert run_all.call_args.args[0] == runs


class TestSandboxAgentFactory:
    def test_sales_flag_and_case_mocks_reach_the_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import src.sandbox.agent_runner as runner
        from src.agent.agent import ToolRouter

        agent = MagicMock(spec=["tool_router"])
        agent.tool_router = create_autospec(ToolRouter, instance=True)
        seen: dict[str, Any] = {}

        async def fake_create(engine: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return agent

        monkeypatch.setattr(runner, "create_sandbox_agent", fake_create)
        tenants = {
            "tvoya-shina": {"id": "t1", "config": {"agent_provider_override": "openai-gpt41-mini"}},
            "prokoleso": {"id": "t2", "config": {}},
        }
        router = MagicMock(spec=["providers"])
        factory = rg.SandboxAgentFactory(engine=None, tenants=tenants, router=router, provider=None)
        c = case(mocks={"search_tires": {"items": []}})

        asyncio.run(factory("tvoya-shina", c))
        assert seen["tenant"]["config"]["sales_enabled"] is True
        assert seen["tenant_id"] == "t1"
        assert seen["provider_override"] == "openai-gpt41-mini"
        assert seen["tool_mode"] == "mock"
        assert tenants["tvoya-shina"]["config"] == {"agent_provider_override": "openai-gpt41-mini"}
        name, handler = agent.tool_router.register.call_args.args
        assert name == "search_tires"
        assert asyncio.run(handler(width=205)) == {"items": []}

        asyncio.run(factory("prokoleso", c))
        assert seen["tenant_id"] == "t2"
        assert seen["provider_override"] is None


# ── the corpus itself ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def cases() -> list[rg.Case]:
    pytest.importorskip("yaml")  # the local venv lacks PyYAML; the container has it
    return rg.load_cases()


class TestCorpus:
    def test_corpus_loads_with_unique_ids(self, cases: list[rg.Case]) -> None:
        assert len({c.id for c in cases}) == len(cases)

    def test_every_case_asserts_something(self, cases: list[rg.Case]) -> None:
        empty = [c.id for c in cases if not any(t.expect or t.expect_by_network for t in c.turns)]
        assert empty == []

    def test_no_phone_numbers_in_customer_lines(self, cases: list[rg.Case]) -> None:
        phone = re.compile(r"\b0\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}\b|\+?380\d{9}")
        leaks = [(c.id, t.user) for c in cases for t in c.turns if phone.search(t.user)]
        assert leaks == []

    def test_network_specific_expectations_only_in_multi_network_cases(
        self, cases: list[rg.Case]
    ) -> None:
        # expect_by_network in a one-network case is just `expect` misfiled.
        odd = [
            c.id
            for c in cases
            if len(c.networks) == 1 and any(t.expect_by_network for t in c.turns)
        ]
        assert odd == []
