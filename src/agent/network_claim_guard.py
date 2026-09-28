"""Network claim guard — the last word on what a network promises, before TTS.

Both networks share one prompt, and the «Умови мережі» block is only a
request: under attention dilution the model still says what the other network
offers («доставка безкоштовна» in a Про Колесо call, «записую на шиномонтаж»
where fitting is not provided, «замовлення підтверджено» where the order is a
request a manager confirms). This module refuses those sentences on the text
path, derived from `NetworkPolicy` rather than from network names.

Rules (each is default-deny over its whole set):

- ``free_delivery``   — a free-delivery promise unless ``delivery_mode == "free"``
  (``unknown`` included: an unconfigured network promises nothing);
- ``extended_warranty`` — an extended-warranty promise that names none of
  ``extended_warranty_brands`` (an empty set blocks every such promise);
- ``service_offer``   — any mention of a service in `SERVICE_LABELS` that is
  not in ``policy.services`` (iterates the whole label set);
- ``order_confirmed`` — «замовлення підтверджено/оформлено»: the order is a
  request a manager calls back about (``order_finish``), so always.

``free_delivery`` and ``extended_warranty`` run only while ``policy.sales_enabled``.
``service_offer`` runs while the policy is ``configured`` (a ``network_policy``
dict is written in ``tenants.config``) or sales are on: only a written policy's
empty ``services`` means "not provided". Unwritten (both networks in production
on 2026-09-28) it means "not configured" — enforcing it would cut «записала вас
на шиномонтаж» out of every Твоя Шина fitting call.
``order_confirmed`` depends on no configured field and runs always.

Negations pass: a clause holding «не / ні / немає / нема / нет / ни» around the
claim is a correct refusal («доставка не безкоштовна», «на жаль, не надаємо
шиномонтаж», «розширеної гарантії немає»).

A refused sentence is *replaced*, never dropped: every rule has a neutral
sentence built from the policy. An emptied turn is not silence here — the loop
retries the LLM on an empty response and then falls back to «Перепрошую, не
почула», which is worse than a correct refusal. The filter drops only a second
copy of a replacement it already spoke this round.

Promotions (``PromoOverrides``, sales on only) beat the standard conditions,
each one only as far as it reaches — an exemption can clear a rule, never add
one, and ``promos=None`` behaves exactly as without promotions:

- ``free_delivery``: a live free-delivery promotion clears the rule for its
  own brands only (``mention_brands``; none = every brand): the sentence must
  name a brand of some scope, in Latin or Cyrillic. A sentence naming no
  brand, while every live scope is a brand list, stays refused (default-deny);
- ``extended_warranty``: brands of a live warranty promotion join the policy's,
  matched in Latin or Cyrillic («Мішлен» → michelin) — Matador's promotion
  does not cover a Michelin promise. The policy's own brands are matched the
  same way («гарантія на Бріджстоун»), with or without promotions;
- ``service_offer``: a partner promotion's service clears the rule only in a
  sentence that names the partner network («шиномонтаж у Твоя Шина») — a bare
  «записую вас на шиномонтаж» in Про Колесо is still the network's own false
  offer;
- ``order_confirmed``: no promotion covers it.

A cleared rule is reported as ``Verdict.promo_exempt`` (logged by the filters).

Switch: env ``NETWORK_CLAIM_GUARD_ENABLED`` (default on; ``0/false/no/off``
disables), read at call time — rollback is removing an env var.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.agent.network_policy import SERVICE_LABELS, NetworkPolicy
from src.agent.parsers.tire_query import extract_tire_brands
from src.core.sentence_buffer import SentenceReady
from src.monitoring.metrics import network_claim_blocked_total

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from src.agent.promotions import PromoOverrides
    from src.core.sentence_buffer import BufferEvent

logger = logging.getLogger(__name__)

ENV_FLAG = "NETWORK_CLAIM_GUARD_ENABLED"

PASS = "pass"
DROP = "drop"
REPLACE = "replace"

RULE_FREE_DELIVERY = "free_delivery"
RULE_EXTENDED_WARRANTY = "extended_warranty"
RULE_SERVICE_OFFER = "service_offer"
RULE_ORDER_CONFIRMED = "order_confirmed"

RULES: tuple[str, ...] = (
    RULE_ORDER_CONFIRMED,
    RULE_SERVICE_OFFER,
    RULE_FREE_DELIVERY,
    RULE_EXTENDED_WARRANTY,
)


@dataclass(frozen=True)
class Verdict:
    action: str  # PASS | DROP | REPLACE
    rule: str | None = None
    replacement: str | None = None
    #: Rules a live promotion cleared on the way to this verdict.
    promo_exempt: tuple[str, ...] = ()


_PASS = Verdict(PASS)

# ── Patterns (ua + ru, oblique cases via \w* stems) ─────────────────────

_W = r"(?:\s+\S+)"  # one intervening word

_FREE = r"(?:безкоштовн|безплатн|бесплатн)\w*"
_DELIVERY = r"(?:достав|відправк|отправк|пересилк)\w*"
_FREE_DELIVERY = re.compile(
    rf"\b{_FREE}{_W}{{0,2}}?\s+{_DELIVERY}|\b{_DELIVERY}{_W}{{0,3}}?\s+{_FREE}",
    re.IGNORECASE,
)

_EXTENDED_WARRANTY = re.compile(
    r"\b(?:розширен|додатков|расширенн|дополнительн|продовжен|продленн)\w*"
    r"(?:\s+\S+){0,1}?\s+гарант\w*",
    re.IGNORECASE,
)

#: Past participles and past verbs only: «Підтверджуєте замовлення?» asks the
#: caller, and «оформлення замовлення» names a step — neither claims a result.
_ORDER_CONFIRMED = re.compile(
    r"\b(?:замовленн|заказ)\w*(?:\s+\S+){0,2}?\s+"
    r"(?:підтвердже(?:но|не|ний|на)|подтвержд[её]н(?:о|а|ы)?|оформлен(?:о|е|ий|а|ы)?)\b"
    r"|\b(?:підтвердил|підтвердив|оформил|оформив|подтвердил)\w*"
    r"(?:\s+\S+){0,1}?\s+(?:замовленн|заказ)\w*",
    re.IGNORECASE,
)
#: «Замовлення буде підтверджено менеджером» is the policy, not a claim.
_ORDER_PENDING = re.compile(r"\b(?:буде|будет|менеджер\w*|після|после)\b", re.IGNORECASE)

#: Word forms per service key. A key of `SERVICE_LABELS` missing here still
#: gets a pattern from its label (see `_service_pattern`) — default-deny.
_SERVICE_FORMS: dict[str, str] = {
    "fitting": (
        r"\b(?:шино)?монтаж\w*|\bперевзу\w*|\bпереобу\w*|\bбалансу?ван\w*"
        r"|\bбалансировк\w*|\bшиномонтажн\w*"
    ),
    "storage": (
        r"\bзберіганн\w*|\bсезонн\w*\s+зберіг\w*|\bхранени\w*|\bшинн\w*\s+готел\w*"
        r"|\bзберіг(?:ати|аємо|аєте)\b"
    ),
}

#: A refusal of a service is often topicalised across a comma — «Шиномонтаж,
#: на жаль, ми не надаємо» — so for `service_offer` a refusal verb anywhere in
#: the sentence clears it, not only one in the same clause.
_SERVICE_REFUSAL = re.compile(
    r"(?<!\w)(?:не|ні)\s+(?:\S+\s+)?"
    r"(?:нада|предоставл|займа|пропону|предлага|робим|делаем|маєм|имеем)\w*"
    r"|(?<!\w)(?:немає|нема|нет)(?!\w)",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"(?<!\w)(?:не|ні|немає|нема|нет|ни|жодн\w*|никак\w*)(?!\w)", re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(r"[,;:—–()]|\s-\s|\bале\b|\bно\b|\bпроте\b|\bоднак\b", re.IGNORECASE)


def _service_pattern(key: str) -> re.Pattern[str]:
    forms = _SERVICE_FORMS.get(key)
    if forms is None:
        stem = SERVICE_LABELS[key].split()[0][:-2]
        forms = rf"\b{re.escape(stem)}\w*"
    return re.compile(forms, re.IGNORECASE)


# ── Helpers ─────────────────────────────────────────────────────────────


def guard_enabled() -> bool:
    raw = os.environ.get(ENV_FLAG)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _normalize(text: str) -> str:
    return " ".join(text.replace("ʼ", "'").replace("’", "'").replace("ё", "е").split())


def _clauses(text: str) -> list[str]:
    return [c for c in _CLAUSE_SPLIT.split(text) if c and c.strip()]


def _affirmed(pattern: re.Pattern[str], text: str) -> list[str]:
    """Clauses where ``pattern`` matches and no negation sits in the clause."""
    return [c for c in _clauses(text) if pattern.search(c) and not _NEGATION.search(c)]


def _partner_pattern(label: str) -> re.Pattern[str] | None:
    """The partner network's name in any case form: «Твоя Шина» → «Твоїй Шині»."""
    words = _normalize(label).split()
    if not words:
        return None
    stems = [re.escape(w[: max(3, len(w) - 2)]) + r"\w*" for w in words]
    return re.compile(r"(?<!\w)" + r"\s+".join(stems), re.IGNORECASE)


def _partner_covered(key: str, text: str, promos: PromoOverrides | None) -> bool:
    """A partner promotion offers ``key`` and the sentence names that partner."""
    if promos is None:
        return False
    for entry in promos.partner_services:
        if entry.get("service") != key:
            continue
        pattern = _partner_pattern(entry.get("network_label") or "")
        if pattern is not None and pattern.search(text):
            return True
    return False


def _names_brand(text: str, brands: frozenset[str] | set[str]) -> bool:
    """``text`` names one of ``brands`` (lower-case): as a slug through the
    tyre-brand parser («Бріджстоун» → bridgestone) or spelled as written."""
    if not brands:
        return False
    low = text.lower()
    named = set(extract_tire_brands(text) or ())
    return any(b in named or b in low for b in brands)


def _free_delivery_covered(text: str, promos: PromoOverrides | None) -> bool:
    """A live free-delivery promotion reaches this sentence: one scope covers
    every brand, or the sentence names a brand of some scope."""
    if promos is None:
        return False
    scopes = promos.free_delivery_brand_scopes
    if any(not scope for scope in scopes):
        return True
    return any(_names_brand(text, scope) for scope in scopes)


def _brand_label(name: str) -> str:
    return name[:1].upper() + name[1:]


# ── Replacements (built from the policy; each passes `check_sentence`) ──


#: Spoken instead of an uncovered free-delivery promise while a brand-bound
#: free-delivery promotion of the network runs.
PROMO_DELIVERY_NEUTRAL = "Умови доставки для цих шин уточнить менеджер."


def _replacement(rule: str, policy: NetworkPolicy, services: list[str]) -> str | None:
    if rule == RULE_ORDER_CONFIRMED:
        return "Я оформлю заявку, а менеджер передзвонить вам і все узгодить."
    if rule == RULE_SERVICE_OFFER:
        labels = " і ".join(SERVICE_LABELS[s] for s in services)
        return f"На жаль, {labels} наша мережа не надає."
    if rule == RULE_FREE_DELIVERY:
        if policy.delivery_mode == "carrier_tariff":
            return "Доставка — за тарифами перевізника."
        return "Умови доставки уточнить менеджер."
    if rule == RULE_EXTENDED_WARRANTY:
        if policy.extended_warranty_brands:
            brands = ", ".join(_brand_label(b) for b in sorted(policy.extended_warranty_brands))
            return f"Розширена гарантія діє тільки на шини {brands}."
        return "Розширеної гарантії мережа не надає."
    return None


# ── The predicate ───────────────────────────────────────────────────────


def check_sentence(
    sentence: str,
    policy: NetworkPolicy | None,
    promos: PromoOverrides | None = None,
) -> Verdict:
    """Judge one sentence against the network's policy and live promotions.

    Pure; never raises. ``promos`` only ever clears a rule; ``None`` is the
    behaviour without promotions.
    """
    if not sentence or not sentence.strip():
        return _PASS
    policy = policy or NetworkPolicy()
    text = _normalize(sentence)
    exempt: list[str] = []

    if policy.order_finish == "request_manager_callback" and any(
        not _ORDER_PENDING.search(c) for c in _affirmed(_ORDER_CONFIRMED, text)
    ):
        return _verdict(RULE_ORDER_CONFIRMED, policy, [])

    # `service_offer` needs to know the services are *real*, not the sales
    # switch: a configured policy's empty set is "not provided" even while
    # sales are off (Про Колесо offered fitting in 9 of 26 turns). Without a
    # written policy it keeps the old `sales_enabled` gate — unchanged.
    if policy.configured or policy.sales_enabled:
        missing = [
            key
            for key in SERVICE_LABELS
            if key not in policy.services and _affirmed(_service_pattern(key), text)
        ]
        if missing and _SERVICE_REFUSAL.search(text):
            missing = []
        partner = [key for key in missing if _partner_covered(key, text, promos)]
        if partner:
            exempt.append(RULE_SERVICE_OFFER)
            missing = [key for key in missing if key not in partner]
        if missing:
            return _verdict(RULE_SERVICE_OFFER, policy, missing)

    if not policy.sales_enabled:
        return _pass(exempt)

    if policy.delivery_mode != "free" and _affirmed(_FREE_DELIVERY, text):
        if not _free_delivery_covered(text, promos):
            if (
                promos is not None
                and promos.free_delivery_brand_scopes
                and not extract_tire_brands(text)
            ):
                # A brand-bound free-delivery promotion runs and the sentence names
                # no brand: it may be about a promotion brand, so the carrier-tariff
                # line could contradict the truth. Promise nothing either way.
                return Verdict(REPLACE, RULE_FREE_DELIVERY, PROMO_DELIVERY_NEUTRAL)
            return _verdict(RULE_FREE_DELIVERY, policy, [])
        exempt.append(RULE_FREE_DELIVERY)

    policy_brands = {b.lower() for b in policy.extended_warranty_brands}
    promo_brands = {b.lower() for b in promos.extended_warranty_brands} if promos else set()
    for clause in _affirmed(_EXTENDED_WARRANTY, text):
        # The bot says brands in Cyrillic («на Бріджстоун», «на Мішлен»): both
        # the policy's and the promotions' brands are matched by slug through
        # the tyre-brand parser, Latin spelling too.
        if _names_brand(clause, policy_brands):
            continue
        if not _names_brand(clause, promo_brands):
            return _verdict(RULE_EXTENDED_WARRANTY, policy, [])
        if RULE_EXTENDED_WARRANTY not in exempt:
            exempt.append(RULE_EXTENDED_WARRANTY)

    return _pass(exempt)


def _pass(exempt: list[str]) -> Verdict:
    return Verdict(PASS, promo_exempt=tuple(exempt)) if exempt else _PASS


def _verdict(rule: str, policy: NetworkPolicy, services: list[str]) -> Verdict:
    replacement = _replacement(rule, policy, services)
    if replacement is None:
        return Verdict(DROP, rule)
    return Verdict(REPLACE, rule, replacement)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def blocked_rules(
    text: str, policy: NetworkPolicy | None, promos: PromoOverrides | None = None
) -> list[str]:
    """Rules a whole reply breaks, sentence by sentence (for offline harnesses)."""
    return [
        v.rule
        for s in _SENTENCE_SPLIT.split(text or "")
        if (v := check_sentence(s, policy, promos)).action != PASS and v.rule
    ]


def _log_promo_exempt(verdict: Verdict, call_id: str, site: str, text: str) -> None:
    for rule in verdict.promo_exempt:
        logger.info(
            "network_claim_promo_exempt: call=%s, site=%s, rule=%s, text=%r",
            call_id,
            site,
            rule,
            text[:200],
        )


def guard_text(
    text: str,
    policy: NetworkPolicy | None,
    call_id: str = "unknown",
    site: str = "summary_fallback",
    promos: PromoOverrides | None = None,
) -> str:
    """The same verdicts on a whole reply that has no sentence buffer.

    Used where LLM text is synthesised directly (`_request_summary_fallback`).
    A refused sentence is replaced, a repeated replacement is dropped — the
    first one stays, so a non-empty reply never comes back empty. Replacements
    pass `check_sentence` themselves, so one pass is final (no re-judging).
    """
    if not guard_enabled() or not text or not text.strip():
        return text
    out: list[str] = []
    spoken_replacements: set[str] = set()
    changed = False
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        verdict = check_sentence(sentence, policy, promos)
        if verdict.action == PASS:
            _log_promo_exempt(verdict, call_id, site, sentence)
            out.append(sentence)
            continue
        changed = True
        network_claim_blocked_total.labels(rule=verdict.rule).inc()
        logger.warning(
            "network_claim_blocked: call=%s, site=%s, rule=%s, action=%s, text=%r",
            call_id,
            site,
            verdict.rule,
            verdict.action,
            sentence[:200],
        )
        if verdict.action == REPLACE and verdict.replacement not in spoken_replacements:
            spoken_replacements.add(verdict.replacement)
            out.append(verdict.replacement)
    return " ".join(out) if changed else text


# ── The stream filter ───────────────────────────────────────────────────


async def guard_network_claims(
    stream: AsyncIterator[BufferEvent],
    policy: NetworkPolicy | None,
    call_id: str = "unknown",
    promos: PromoOverrides | None = None,
) -> AsyncIterator[BufferEvent]:
    """Replace sentences the network cannot promise before they reach TTS.

    Fragments are held to the end of the sentence, as in
    `drop_control_plane_prose`: `buffer_sentences` splits long sentences at
    commas, and «Шиномонтаж,» judged alone would lose the «не надаємо» that
    follows it.
    """
    if not guard_enabled():
        async for event in stream:
            yield event
        return

    held: list[SentenceReady] = []
    spoken_replacements: set[str] = set()

    def settle() -> list[SentenceReady]:
        nonlocal held
        queued = held
        held = []
        if not queued:
            return []
        text = " ".join(e.text for e in queued).strip()
        verdict = check_sentence(text, policy, promos)
        if verdict.action == PASS:
            _log_promo_exempt(verdict, call_id, "stream", text)
            return queued
        network_claim_blocked_total.labels(rule=verdict.rule).inc()
        logger.warning(
            "network_claim_blocked: call=%s, rule=%s, action=%s, text=%r",
            call_id,
            verdict.rule,
            verdict.action,
            text[:200],
        )
        if verdict.action == REPLACE and verdict.replacement not in spoken_replacements:
            spoken_replacements.add(verdict.replacement)
            return [SentenceReady(text=verdict.replacement)]
        return []

    async for event in stream:
        if isinstance(event, SentenceReady):
            held.append(event)
            if event.text.rstrip().endswith((".", "!", "?")):
                for queued in settle():
                    yield queued
            continue
        for queued in settle():
            yield queued
        yield event

    for queued in settle():
        yield queued
