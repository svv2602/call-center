"""Network claim guard — two goldset №4 fixes (wave 2-F, 2026-09-28).

1. ``service_offer`` refused a *descriptive* mention of a service: Про Колесо's
   «Повернути можна лише шини без експлуатації та слідів монтажу.» was replaced
   by «На жаль, шиномонтаж наша мережа не надає.» (warranty_return_same_terms).
   A service named as a circumstance («без / слідів / після / під час» + the
   service) is not an offer; every real offer still is — over the whole
   ``SERVICE_LABELS`` enum.
2. ``promo_denied``: Твоя Шина said «Розширеної гарантії на Michelin немає…»
   while «Сервісна програма MICHELIN» was live (promo_ts_michelin_service_program).
   A denial of a condition a live promotion grants for the named brand is
   replaced by that promotion's own ``bot_text``; the code composes no terms.

Every assertion names the rule that fired, not just the action: a neighbouring
rule answering first would otherwise hide a missing or broken one.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, ClassVar

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from scripts.migrate_promotions import KNOWN_PROMOTIONS
from src.agent.network_claim_guard import (
    PASS,
    PROMO_RULES,
    REPLACE,
    RULE_PROMO_DENIED,
    RULE_SERVICE_OFFER,
    RULES,
    blocked_rules,
    check_sentence,
    guard_text,
)
from src.agent.network_policy import SERVICE_LABELS, NetworkPolicy
from src.agent.promotions import (
    GRANT_EXTENDED_WARRANTY,
    GRANT_FREE_DELIVERY,
    ActivePromotion,
    PromoGrant,
    PromoOverrides,
    promo_overrides,
)

_TODAY = date(2026, 10, 15)


def _policy(patch: dict[str, Any], *, sales: bool) -> NetworkPolicy:
    return NetworkPolicy.from_tenant_config({**patch, "sales_enabled": sales})


PK_ON = _policy(PROKOLESO_CONFIG_PATCH, sales=True)
PK_OFF = _policy(PROKOLESO_CONFIG_PATCH, sales=False)
TS_ON = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=True)
TS_OFF = _policy(TVOYA_SHINA_CONFIG_PATCH, sales=False)


def _promo(spec: Any) -> ActivePromotion:
    """A known promotion as production loads it — ``mention_brands`` included."""
    return ActivePromotion(
        spec.title_prefix,
        spec.bot_text,
        _TODAY,
        overrides=dict(spec.overrides),
        mention_brands=tuple(spec.mention_brands),
    )


_PK_LIST = [_promo(s) for s in KNOWN_PROMOTIONS[:6]]
_TS_LIST = [_promo(s) for s in KNOWN_PROMOTIONS[6:]]
PK_PROMOS = promo_overrides(_PK_LIST)
TS_PROMOS = promo_overrides(_TS_LIST)

# The goldset fixture (tests/goldset/cases/promotions.yaml, promo_ts_michelin_service_program).
MICHELIN_TEXT = (
    "Сервісна програма MICHELIN: при покупці в торговому центрі «Твоя Шина» і встановленні "
    "там комплекту від 4 шин MICHELIN, вироблених після 01.01.2022, пошкоджену шину, яку не "
    "можна відремонтувати (прокол, розрив чи здуття боковини), безкоштовно замінять на таку "
    "саму. Не діє на шини, куплені в кредит або частинами."
)
MICHELIN = promo_overrides(
    [
        ActivePromotion(
            "Сервісна програма MICHELIN",
            MICHELIN_TEXT,
            date(2026, 12, 31),
            overrides={"extended_warranty_brands": ["michelin"]},
            mention_brands=("michelin",),
        )
    ]
)

# Goldset №4 replies, verbatim (report.txt / network_claim_blocked log line).
PK_RETURN = "Повернути можна лише шини без експлуатації та слідів монтажу."
TS_MICHELIN = (
    "Розширеної гарантії на Michelin немає у відкритій базі знань, але діє сервісна "
    "програма Michelin до 31 грудня 2026 року: якщо комплект шин куплений і встановлений."
)


def _rule(sentence: str, policy: NetworkPolicy | None, promos: PromoOverrides | None = None):
    verdict = check_sentence(sentence, policy, promos)
    return None if verdict.action == PASS else verdict.rule


# ── 1. Descriptive service mention ──────────────────────────────────────


class TestDescriptiveService:
    def test_goldset_return_sentence_passes(self) -> None:
        for policy in (PK_ON, PK_OFF, TS_ON):
            assert _rule(PK_RETURN, policy) is None
            assert _rule(PK_RETURN, policy, PK_PROMOS) is None

    def test_goldset_reply_keeps_the_return_answer(self) -> None:
        reply = "Гарантія на шини – від виробника на заводський брак. " + PK_RETURN
        assert guard_text(reply, PK_ON) == reply

    @pytest.mark.parametrize(
        "sentence",
        [
            "Гарантія покриває заводський брак, окрім пошкоджень під час монтажу.",
            "Повернути можна шини без експлуатації та монтажу.",
            "Сліди шиномонтажу на диску — підстава відмовити в поверненні.",
            "Після монтажу перевірте тиск через п'ятдесят кілометрів.",
            "Шини після неправильного монтажу гарантія покриває частково.",
            "Возврат возможен только без следов монтажа.",
            "Гума старіє швидше без зберігання в темряві.",
        ],
    )
    def test_descriptive_mention_passes(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON) is None

    @pytest.mark.parametrize(
        "sentence",
        [
            "Записую вас на шиномонтаж.",
            "Можемо без черги зробити монтаж.",
            "Після покупки запишу на монтаж.",
            "Після покупки монтаж безкоштовний.",
            "Без запису монтаж зробимо за годину.",
            "Після оплати зробимо шиномонтаж.",
            "Вам слід записатися на монтаж.",
            "Вам слід монтаж замовити у нас.",  # modal «слід», not the noun
            "Запишу вас до шиномонтажу.",
            "Після безкоштовного шиномонтажу отримаєте знижку.",
            "Після нашого монтажу гарантія зберігається.",
            "Можемо без черги прийняти шини на зберігання.",
            "Після сезону залишите шини на зберігання.",
        ],
    )
    def test_offer_still_replaced(self, sentence: str) -> None:
        verdict = check_sentence(sentence, PK_ON)
        assert (verdict.action, verdict.rule) == (REPLACE, RULE_SERVICE_OFFER), sentence

    # One descriptive and one offering sentence per service key: the fix is
    # applied over the whole enum, not a named subset.
    DESCRIPTIVE: ClassVar[dict[str, str]] = {
        "fitting": "Без слідів монтажу шини приймаємо назад.",
        "storage": "Гума старіє швидше без зберігання в темряві.",
    }
    OFFER: ClassVar[dict[str, str]] = {
        "fitting": "Приїжджайте на монтаж.",
        "storage": "Приймаємо шини на зберігання.",
    }

    def test_enum_is_covered(self) -> None:
        assert set(self.DESCRIPTIVE) == set(SERVICE_LABELS) == set(self.OFFER)

    @pytest.mark.parametrize("key", sorted(SERVICE_LABELS))
    def test_each_service(self, key: str) -> None:
        assert _rule(self.DESCRIPTIVE[key], PK_ON) is None
        verdict = check_sentence(self.OFFER[key], PK_ON)
        assert verdict.rule == RULE_SERVICE_OFFER
        assert SERVICE_LABELS[key] in (verdict.replacement or "")

    def test_cut_is_per_service(self) -> None:
        """A descriptive «монтаж» does not clear a real storage offer beside it."""
        verdict = check_sentence(
            "Повернути можна шини без слідів монтажу, а зберігання шин ми пропонуємо.", PK_ON
        )
        assert verdict.rule == RULE_SERVICE_OFFER
        assert verdict.replacement == "На жаль, зберігання шин наша мережа не надає."

    def test_provided_service_untouched(self) -> None:
        assert _rule("Записую вас на шиномонтаж.", TS_ON) is None


# ── 2. promo_denied ─────────────────────────────────────────────────────


class TestPromoDenied:
    def test_rule_is_promo_only(self) -> None:
        assert RULE_PROMO_DENIED in PROMO_RULES
        assert RULE_PROMO_DENIED not in RULES

    def test_goldset_sentence_replaced_by_promotion_text(self) -> None:
        verdict = check_sentence(TS_MICHELIN, TS_ON, MICHELIN)
        assert (verdict.action, verdict.rule) == (REPLACE, RULE_PROMO_DENIED)
        assert verdict.replacement == MICHELIN_TEXT
        assert "Сервісна програма MICHELIN" in verdict.replacement

    def test_goldset_expectations_hold_after_guard(self) -> None:
        out = guard_text(TS_MICHELIN, TS_ON, promos=MICHELIN)
        assert re.search(r"програм", out, re.IGNORECASE)
        assert re.search(r"торгов\w*\s+(?:\w+\s+){0,1}центр|\bТЦ\b", out, re.IGNORECASE)
        assert not re.search(
            r"(?:michelin|мішлен|мишлен)\w*\s+(?:\w+\s+){0,3}(?:немає|нема|нет)\b",
            out,
            re.IGNORECASE,
        )

    @pytest.mark.parametrize(
        "sentence",
        [
            "Розширеної гарантії на Michelin немає.",
            "Розширеної гарантії на Мішлен немає.",
            "На Michelin, на жаль, розширеної гарантії немає.",
            "Додаткової гарантії на шини Michelin мережа не надає.",
            "Расширенной гарантии на Мишлен нет.",
        ],
    )
    def test_denial_of_a_granted_brand(self, sentence: str) -> None:
        verdict = check_sentence(sentence, TS_ON, MICHELIN)
        assert (verdict.rule, verdict.replacement) == (RULE_PROMO_DENIED, MICHELIN_TEXT)

    @pytest.mark.parametrize(
        "sentence",
        [
            "Розширеної гарантії на Continental немає.",
            "Розширеної гарантії мережа не надає, крім акцій: Michelin.",
            "Розширеної гарантії немає, але на Michelin діє сервісна програма.",
            "Розширеної гарантії немає крім Michelin.",
            "Розширеної гарантії немає окрім Michelin.",
            "Розширеної гарантії немає.",
            # The clause names its own brand: an earlier one is not its subject.
            "Щодо Michelin і Continental: розширеної гарантії на Continental немає.",
            "Безкоштовної доставки на Michelin немає.",  # condition not granted
            "Розширена гарантія на Michelin діє в торговому центрі.",
        ],
    )
    def test_denial_stays(self, sentence: str) -> None:
        assert _rule(sentence, TS_ON, MICHELIN) is None

    @pytest.mark.parametrize("sentence", [TS_MICHELIN, "Розширеної гарантії на Michelin немає."])
    def test_without_promotions_old_verdict(self, sentence: str) -> None:
        assert _rule(sentence, TS_ON) is None
        assert _rule(sentence, TS_ON, PromoOverrides()) is None
        assert _rule(sentence, TS_OFF, MICHELIN) is None  # sales off: no promo rules

    def test_picks_the_promotion_of_the_brand(self) -> None:
        verdict = check_sentence("Розширеної гарантії на Matador немає.", PK_ON, PK_PROMOS)
        assert verdict.rule == RULE_PROMO_DENIED
        assert verdict.replacement == KNOWN_PROMOTIONS[3].bot_text

    def test_free_delivery_denial_of_a_promotion_brand(self) -> None:
        verdict = check_sentence("Безкоштовної доставки на Doublestar немає.", PK_ON, PK_PROMOS)
        assert verdict.rule == RULE_PROMO_DENIED
        assert verdict.replacement == KNOWN_PROMOTIONS[0].bot_text

    @pytest.mark.parametrize(
        "sentence",
        [
            "Доставка шин Michelin не безкоштовна.",
            "Доставка не безкоштовна.",  # every PK scope is a brand list
        ],
    )
    def test_free_delivery_denial_outside_scope_stays(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, PK_PROMOS) is None

    def test_brandless_free_delivery_promotion_covers_every_brand(self) -> None:
        verdict = check_sentence("Доставка не безкоштовна.", TS_ON, TS_PROMOS)
        assert verdict.rule == RULE_PROMO_DENIED
        assert verdict.replacement == KNOWN_PROMOTIONS[6].bot_text

    @pytest.mark.parametrize(
        ("policy", "promos", "texts"),
        [
            (PK_ON, PK_PROMOS, [p.bot_text for p in _PK_LIST]),
            (TS_ON, TS_PROMOS, [p.bot_text for p in _TS_LIST]),
            (TS_ON, MICHELIN, [MICHELIN_TEXT]),
        ],
        ids=["pk", "ts", "ts-goldset"],
    )
    def test_promotion_text_is_not_denied_by_itself(
        self, policy: NetworkPolicy, promos: PromoOverrides, texts: list[str]
    ) -> None:
        for bot_text in texts:
            assert RULE_PROMO_DENIED not in blocked_rules(bot_text, policy, promos), bot_text


# ── PromoOverrides.grants ───────────────────────────────────────────────


class TestGrants:
    def test_grants_carry_the_promotion_words(self) -> None:
        assert MICHELIN.grants == (
            PromoGrant(
                GRANT_EXTENDED_WARRANTY,
                frozenset({"michelin"}),
                "Сервісна програма MICHELIN",
                MICHELIN_TEXT,
            ),
        )

    def test_free_delivery_scope(self) -> None:
        pk = [g for g in PK_PROMOS.grants if g.condition == GRANT_FREE_DELIVERY]
        assert len(pk) == 3
        assert all(g.brands for g in pk)
        ts = [g for g in TS_PROMOS.grants if g.condition == GRANT_FREE_DELIVERY]
        assert [g.brands for g in ts] == [frozenset()]

    def test_empty_warranty_list_grants_nothing(self) -> None:
        promos = promo_overrides(
            [
                ActivePromotion("t", "text", _TODAY, overrides={"extended_warranty_brands": []}),
                ActivePromotion("d", "text", _TODAY, overrides={"discount": True}),
            ]
        )
        assert promos.grants == ()
        assert _rule("Розширеної гарантії на Michelin немає.", TS_ON, promos) is None

    def test_brand_names_lower_cased(self) -> None:
        promos = promo_overrides(
            [
                ActivePromotion(
                    "t", "текст", _TODAY, overrides={"extended_warranty_brands": ["Michelin"]}
                )
            ]
        )
        assert promos.grants[0].brands == frozenset({"michelin"})
