"""Free delivery by brand: a promotion's ``mention_brands`` bound its exemption.

Про Колесо's free-delivery promotions cover named brands only (Doublestar and
Rydanz; twelve brands for the other two); Твоя Шина's «Доставимо безплатно»
names none, i.e. every brand. Promotions are built the way production builds
them: rows → ``load_active_promotions`` → ``promo_overrides``, from the specs
``scripts/migrate_promotions.KNOWN_PROMOTIONS`` writes.

Invariants:
- a free-delivery promise passes only when some live scope is «every brand» or
  the sentence names a brand of some scope (Latin or Cyrillic);
- a brand scope never clears a promise for another brand, nor a brandless one
  (default-deny) — the refusal is the one without promotions;
- without promotions ``free_delivery`` depends on the policy alone, as before;
- ``extended_warranty``: the policy's brands are matched in Cyrillic too
  («гарантія на Бріджстоун» in Твоя Шина), and every clause that passed before
  (a policy brand spelled as written) still passes.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest

from scripts.configure_tenants import PROKOLESO_CONFIG_PATCH, TVOYA_SHINA_CONFIG_PATCH
from scripts.migrate_promotions import _FREE_DELIVERY_BRANDS_12, KNOWN_PROMOTIONS
from src.agent import promotions
from src.agent.network_claim_guard import (
    _EXTENDED_WARRANTY,
    PASS,
    RULE_EXTENDED_WARRANTY,
    RULE_FREE_DELIVERY,
    _affirmed,
    check_sentence,
)
from src.agent.network_policy import NetworkPolicy
from src.agent.promotions import (
    ActivePromotion,
    PromoOverrides,
    load_active_promotions,
    promo_overrides,
)

_TODAY = date(2026, 10, 15)
_PK_TENANT = "11111111-1111-1111-1111-111111111111"
_TS_TENANT = "22222222-2222-2222-2222-222222222222"

PK_ON = NetworkPolicy.from_tenant_config({**PROKOLESO_CONFIG_PATCH, "sales_enabled": True})
TS_ON = NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": True})
TS_OFF = NetworkPolicy.from_tenant_config({**TVOYA_SHINA_CONFIG_PATCH, "sales_enabled": False})
PROD_TODAY = NetworkPolicy.from_tenant_config({})

_PK_SPECS, _TS_SPECS = KNOWN_PROMOTIONS[:6], KNOWN_PROMOTIONS[6:]
_DOUBLESTAR_SPEC = _PK_SPECS[0]


# ── Production shape: rows → live list → overrides ──────────────────────


class _Row:
    def __init__(self, data: dict[str, Any]) -> None:
        self._mapping = data


class _Conn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: Any, params: dict[str, Any] | None = None) -> list[_Row]:
        assert "FROM promotions" in str(query)
        return [_Row(r) for r in self._rows]


class _Engine:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def begin(self) -> _Conn:
        return _Conn(self.rows)


def _row(spec: Any, tenant: str) -> dict[str, Any]:
    return {
        "tenant_id": tenant,
        "title": spec.title_prefix,
        "bot_text": spec.bot_text,
        "valid_from": _TODAY - timedelta(days=10),
        "valid_to": _TODAY + timedelta(days=10),
        "overrides": dict(spec.overrides),
        "mention_brands": list(spec.mention_brands),
        "active": True,
    }


@pytest.fixture(autouse=True)
def _clear_promo_cache() -> Any:
    promotions._cache.clear()
    promotions._cache_ts = 0.0
    yield
    promotions._cache.clear()


def _live(specs: Any, tenant: str) -> PromoOverrides:
    promotions._cache.clear()
    rows = [_row(s, tenant) for s in specs]
    loaded = asyncio.run(load_active_promotions(_Engine(rows), tenant, today=_TODAY))  # type: ignore[arg-type]
    return promo_overrides(loaded)


def _rule(sentence: str, policy: NetworkPolicy, promos: PromoOverrides | None) -> str | None:
    verdict = check_sentence(sentence, policy, promos)
    return None if verdict.action == PASS else verdict.rule


_TWELVE = frozenset(_FREE_DELIVERY_BRANDS_12)


class TestOverrides:
    def test_pk_scopes_are_the_migrated_brands(self) -> None:
        promos = _live(_PK_SPECS, _PK_TENANT)
        assert sorted(promos.free_delivery_brand_scopes, key=sorted) == sorted(
            [frozenset({"doublestar", "rydanz"}), _TWELVE, _TWELVE], key=sorted
        )
        assert promos.free_delivery is True

    def test_ts_scope_is_every_brand(self) -> None:
        promos = _live(_TS_SPECS, _TS_TENANT)
        assert promos.free_delivery_brand_scopes == (frozenset(),)
        assert promos.free_delivery is True

    def test_brands_of_other_promotions_make_no_scope(self) -> None:
        # Matador warranty and Bridgestone discount have mention_brands too.
        others = [s for s in _PK_SPECS if s.overrides.get("free_delivery") is not True]
        assert any(s.mention_brands for s in others)
        promos = _live(others, _PK_TENANT)
        assert promos.free_delivery_brand_scopes == ()
        assert promos.free_delivery is False

    def test_brands_are_lower_cased(self) -> None:
        promo = ActivePromotion(
            "А", "т", _TODAY, overrides={"free_delivery": True}, mention_brands=(" Goodyear ",)
        )
        assert promo_overrides([promo]).free_delivery_brand_scopes == (frozenset({"goodyear"}),)


# ── free_delivery under brand scopes ────────────────────────────────────

PK_PROMOS = _live(_PK_SPECS, _PK_TENANT)
DOUBLESTAR_ONLY = _live([_DOUBLESTAR_SPEC], _PK_TENANT)
_NO_PROMO_REPLACEMENT = check_sentence("Доставка безкоштовна.", PK_ON, None).replacement


class TestFreeDeliveryByBrand:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Доставка шин Doublestar безкоштовна.",
            "На шини Rydanz доставка безкоштовна.",
            "Безкоштовна доставка на Goodyear.",
            "Безкоштовна доставка на шини Гудієр.",
            "Доставимо Піреллі безкоштовно.",
            "Доставка шин Sailun безплатна.",
        ],
    )
    def test_covered_brand_passes(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, None) == RULE_FREE_DELIVERY
        verdict = check_sentence(sentence, PK_ON, PK_PROMOS)
        assert verdict.action == PASS
        assert verdict.promo_exempt == (RULE_FREE_DELIVERY,)

    @pytest.mark.parametrize(
        "sentence",
        [
            "Безкоштовна доставка на Michelin.",
            "Доставка шин Мішлен безкоштовна.",
            "Доставимо Bridgestone безкоштовно.",
        ],
    )
    def test_uncovered_brand_is_refused_as_without_promotion(self, sentence: str) -> None:
        verdict = check_sentence(sentence, PK_ON, PK_PROMOS)
        assert verdict.rule == RULE_FREE_DELIVERY
        assert verdict.replacement == _NO_PROMO_REPLACEMENT

    @pytest.mark.parametrize(
        "sentence",
        ["Безкоштовна доставка на Goodyear.", "Доставимо Піреллі безкоштовно."],
    )
    def test_brand_promotion_does_not_cover_another_brand(self, sentence: str) -> None:
        # Goodyear / Pirelli are in the twelve, not in Doublestar's promotion.
        assert _rule(sentence, PK_ON, DOUBLESTAR_ONLY) == RULE_FREE_DELIVERY

    @pytest.mark.parametrize(
        "sentence",
        ["Доставка безкоштовна.", "Доставимо безкоштовно по всій Україні.", "Доставка бесплатная."],
    )
    def test_brandless_promise_under_brand_promotions_is_refused(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, PK_PROMOS) == RULE_FREE_DELIVERY
        assert _rule(sentence, PK_ON, DOUBLESTAR_ONLY) == RULE_FREE_DELIVERY

    @pytest.mark.parametrize(
        "sentence", ["Доставка безкоштовна.", "Безкоштовна доставка на Michelin."]
    )
    def test_every_brand_scope_clears_any_promise(self, sentence: str) -> None:
        every = PromoOverrides(free_delivery=True, free_delivery_brand_scopes=(frozenset(),))
        mixed = PromoOverrides(
            free_delivery=True,
            free_delivery_brand_scopes=(frozenset({"doublestar"}), frozenset()),
        )
        assert _rule(sentence, PK_ON, every) is None
        assert _rule(sentence, PK_ON, mixed) is None

    def test_flag_without_scopes_clears_nothing(self) -> None:
        # The guard reads scopes only; a bare flag is not a wildcard.
        bare = PromoOverrides(free_delivery=True)
        assert _rule("Доставка безкоштовна.", PK_ON, bare) == RULE_FREE_DELIVERY

    @pytest.mark.parametrize(
        "sentence", ["Доставка безкоштовна.", "Безкоштовна доставка на Michelin."]
    )
    def test_no_promotions_depends_on_policy_only(self, sentence: str) -> None:
        assert _rule(sentence, PK_ON, None) == RULE_FREE_DELIVERY
        assert _rule(sentence, PK_ON, PromoOverrides()) == RULE_FREE_DELIVERY
        assert _rule(sentence, TS_ON, None) is None  # delivery_mode == "free"
        assert _rule(sentence, TS_OFF, None) is None  # sales off: rule not run
        assert _rule(sentence, PROD_TODAY, None) is None


# ── extended_warranty: the policy's brands in Cyrillic ──────────────────


class TestPolicyWarrantyBrands:
    @pytest.mark.parametrize(
        "sentence",
        [
            "Розширена гарантія на Бріджстоун.",
            "Розширена гарантія діє на шини Бріджстоун.",
            "Додаткова гарантія на Бриджстоун.",
            "Розширена гарантія на Bridgestone.",
        ],
    )
    def test_tvoya_shina_policy_brand_passes(self, sentence: str) -> None:
        assert _rule(sentence, TS_ON, None) is None

    @pytest.mark.parametrize(
        "sentence", ["Розширена гарантія на Мішлен.", "Розширена гарантія на Matador."]
    )
    def test_other_brand_still_refused(self, sentence: str) -> None:
        assert _rule(sentence, TS_ON, None) == RULE_EXTENDED_WARRANTY

    def test_network_without_brands_refuses_bridgestone(self) -> None:
        assert _rule("Розширена гарантія на Бріджстоун.", PK_ON, None) == RULE_EXTENDED_WARRANTY

    @pytest.mark.parametrize(
        "sentence",
        [
            "Розширена гарантія на Bridgestone.",
            "Розширена гарантія на BRIDGESTONE Turanza.",
            "Додаткова гарантія на шини bridgestone діє 3 роки.",
            "Розширена гарантія, як і раніше, на Bridgestone.",
            "Розширена гарантія на Мішлен.",
            "Розширена гарантія на Бріджстоун.",
        ],
    )
    def test_what_passed_before_passes_now(self, sentence: str) -> None:
        # The incumbent rule (35eca08): a clause passes iff it holds a policy
        # brand as written. Whatever it passed must still pass.
        clauses = _affirmed(_EXTENDED_WARRANTY, " ".join(sentence.split()))
        old_pass = all(
            any(b.lower() in c.lower() for b in TS_ON.extended_warranty_brands) for c in clauses
        )
        if old_pass:
            assert _rule(sentence, TS_ON, None) is None
