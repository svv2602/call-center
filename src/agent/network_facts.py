"""Network facts the code says itself when the caller asks about them (sales).

Goldset №4–№10 (2026-09-28): «сколько стоит доставка во Львов?» got «У якому
місті вас цікавить доставка?», «а як я дізнаюсь де посилка?» a clarifying
question, «а можно взять в рассрочку?» «я допомагаю з підбором шин…» — every
time with the right line sitting in the «Умови мережі» block. A prompt line
loses to attention dilution in every third run, so the fact is said by code.

``fact_topics(text)`` reads one caller utterance (UA and RU, case forms) into
topics of ``TOPICS`` — the whole enum; ``fact_sentences`` renders the phrase of
each topic from the network's ``NetworkPolicy`` fields, or from the ``bot_text``
of a live promotion that changes that condition for a brand the caller named.
Nothing is composed: a topic whose field is empty says nothing (default-deny),
and the model answers it as before («це уточнить менеджер»).

Both loops call ``turn_facts`` before the first LLM round. The voice loop
speaks the phrases before the stream (``_speak_code_phrase``), the text loop
puts them first in the reply after ``guard_text`` — like the tyre caveat, the
network-claim guard never judges the code's own words. ``already_said_note``
goes at the end of that turn's system prompt, so the model knows the caller
has heard it and goes on from there.

Loop-breakers: one phrase per topic per turn, at most ``MAX_FACTS_PER_TURN``
phrases; a topic is said again only when the caller asks it again (detection is
per utterance, nothing carries over between turns). With ``sales_enabled`` off
nothing is said.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from src.agent.network_policy import BANK_LABELS, payment_method_labels
from src.agent.promotions import (
    GRANT_EXTENDED_WARRANTY,
    GRANT_FREE_DELIVERY,
    _brand_keys,
    _named_brands,
)

if TYPE_CHECKING:
    from src.agent.network_policy import NetworkPolicy
    from src.agent.promotions import PromoGrant, PromoOverrides

logger = logging.getLogger(__name__)

DELIVERY_COST = "delivery_cost"
DELIVERY_ETA = "delivery_eta"
TRACKING = "tracking"
PICKUP = "pickup"
INSTALLMENTS = "installments"
COD = "cod"
PAYMENT = "payment"
EXTENDED_WARRANTY = "extended_warranty"
WARRANTY = "warranty"
RETURNS = "returns"

#: The whole enum, in the order phrases are said (and cut at the cap).
TOPICS: tuple[str, ...] = (
    DELIVERY_COST,
    DELIVERY_ETA,
    TRACKING,
    PICKUP,
    INSTALLMENTS,
    COD,
    PAYMENT,
    EXTENDED_WARRANTY,
    WARRANTY,
    RETURNS,
)

MAX_FACTS_PER_TURN = 2

_B = r"(?<![\w'])"

#: доставка / доставку / доставки / доставкою / доставите / доставляєте / доставят.
_DELIVERY = re.compile(_B + r"достав\w*")
#: Cost words that are a question on their own («безкоштовна?» has no «?» in STT).
_COST = re.compile(
    _B + r"(?:кошту\w*|стоит\w*|стоят\w*|стоимост\w*|вартіст\w*|вартує\w*|цін[аиуі]\w*"
    r"|цен[аыуе]\w*|платн\w*|безкоштовн\w*|бесплатн\w*|безплатн\w*|тариф\w*)"
)
_HOW_MUCH = re.compile(_B + r"(?:скільк\w*|скольк\w*)")
_ETA = re.compile(
    _B + r"(?:швидк\w*|быстр\w*|скоро|коли|когда|термін\w*|строк\w*|срок\w*|довго|долго"
    r"|днів|дней|дня|дні|діб|суток|доби)"
)
#: ТТН / накладна (the waybill, not «накладений платіж») / трек / відстежити.
_TRACK = re.compile(
    _B + r"(?:ттн|накладн(?:а|ої|у|ую|ой|і)|трек\w*|відстеж\w*|відслідк\w*|отслед\w*"
    r"|посилк\w*|посылк\w*)(?![\w'])"
)
_PICKUP = re.compile(
    _B + r"(?:самовив\w*|самовыв\w*)"
    r"|" + _B + r"(?:забра\w*|заберу|заберем\w*|забер[еі]\w*)\s+(?:\w+\s+){0,2}"
    r"(?:сам|сама|самі|сами|самому|самим|самостійно|самостоятельно)(?![\w'])"
    r"|" + _B + r"(?:сам|сама|самі|сами|самому|самостійно|самостоятельно)\s+"
    r"(?:\w+\s+){0,1}(?:забра\w*|заберу|заберем\w*)"
)
_INSTALLMENTS = re.compile(
    _B + r"(?:розстрочк\w*|розсрочк\w*|рассрочк\w*|рострочк\w*|частинами|частями|частинк\w*)"
)
_COD = re.compile(
    _B + r"(?:наложк\w*|наложен\w*|накладен\w*|післяплат\w*|послеоплат\w*|накладн\w*\s+плат\w*"
    r"|оплат\w*\s+(?:\w+\s+){0,1}(?:при|після|после)\s+(?:отриманн\w*|получени\w*))"
)
_PAYMENT = re.compile(
    _B + r"(?:оплат\w*|оплач\w*|заплат\w*|розрахув\w*|рассчит\w*|платит\w*|платите"
    r"|карт(?:ою|кою|ой|очкою|очкой|ку|ка)|передоплат\w*|предоплат\w*)"
)
_WARRANTY = re.compile(_B + r"гарант\w*")
#: «розширена гарантія», «гарантія від проколів / порізів» — the extended kind.
_EXTENDED = re.compile(
    _B + r"(?:розширен\w*|расширен\w*|продовжен\w*|продлен\w*|додатков\w*|дополнительн\w*"
    r"|прокол\w*|поріз\w*|пореж\w*|порез\w*|пошкодж\w*|поврежд\w*)"
)
_RETURNS = re.compile(
    _B + r"(?:поверн\w*|поверта\w*|верну\w*|вернут\w*|возврат\w*|возвращ\w*|обмін\w*"
    r"|обміня\w*|обмен\w*|обменя\w*)"
)
#: «я вам повернусь / передзвоню», «вернуться к вопросу» — not a return of goods.
_NOT_RETURNS = re.compile(
    _B + r"(?:поверну(?:сь|ся)|повернут(?:ися|ись)|повернемо(?:сь|ся)|повернімо(?:сь|ся)"
    r"|вернусь|вернуться|вернемся|вернёмся|вернутся)"
)
#: The warranty/returns terms are the tyres'; a fitting or a wheel is another matter.
_NOT_TYRE_TERMS = re.compile(
    _B + r"(?:\w*монтаж\w*|балансув\w*|балансир\w*|диск\w*|лит(?:і|ий|их|ые|ых|ой|ого)"
    r"|штампован\w*|штамповк\w*|кован\w*)"
)
#: The utterance asks (STT drops «?», so the words carry it).
_ASK = re.compile(
    _B + r"(?:як\w*|как\w*|чим|чем|чи|ли|можна|можно|є|есть|де|где|куди|куда|коли|когда"
    r"|скільк\w*|скольк\w*|що|что|шо|хіба|разве|підкаж\w*|подскаж\w*|розкаж\w*|расскаж\w*"
    r"|цікав\w*|интерес\w*|дізна\w*|узна\w*|приймає\w*|принима\w*|працює\w*|работае\w*"
    r"|буває|бывает|надаєте|предоставля\w*|даєте|даете|реально|можлив\w*|возмож\w*)(?![\w'])"
)


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace("’", "'").replace("ʼ", "'")


def fact_topics(text: str | None) -> list[str]:
    """Topics of ``TOPICS`` the utterance asks about, in ``TOPICS`` order."""
    if not text:
        return []
    low = _normalize(text)
    asks = _ASK.search(low) is not None
    found: set[str] = set()

    if _DELIVERY.search(low):
        eta = _ETA.search(low) is not None
        if _COST.search(low) or (_HOW_MUCH.search(low) and not eta):
            found.add(DELIVERY_COST)
        if eta:
            found.add(DELIVERY_ETA)
    if asks and _TRACK.search(low):
        found.add(TRACKING)
    if asks and _PICKUP.search(low):
        found.add(PICKUP)
    if asks and _INSTALLMENTS.search(low):
        found.add(INSTALLMENTS)
    if asks and _COD.search(low):
        found.add(COD)
    # A general payment question only when no specific way was asked about.
    if asks and _PAYMENT.search(low) and not found & {INSTALLMENTS, COD}:
        found.add(PAYMENT)

    tyre_terms = not _NOT_TYRE_TERMS.search(low)
    if asks and tyre_terms and _WARRANTY.search(low):
        found.add(EXTENDED_WARRANTY if _EXTENDED.search(low) else WARRANTY)
    if asks and tyre_terms and _RETURNS.search(low) and not _NOT_RETURNS.search(low):
        found.add(RETURNS)
    return [t for t in TOPICS if t in found]


def _sentence(clause: str) -> str:
    clause = clause.strip().rstrip(".")
    return clause[:1].upper() + clause[1:] + "."


def _grant_applies(grant: PromoGrant, text: str | None, tire_query: dict[str, Any] | None) -> bool:
    """The promotion covers what the caller asks about: all brands, or one they named."""
    if not grant.brands:
        return True
    named = _named_brands(text, tire_query)
    low = _normalize(text or "")
    for brand in grant.brands:
        if _brand_keys(brand) & named:
            return True
        # A brand the parser does not know («Rydanz») counts when spelled as the promotion does.
        if re.search(rf"(?<![\w']){re.escape(brand)}(?![\w'])", low):
            return True
    return False


def _promo_text(
    condition: str,
    promos: PromoOverrides | None,
    text: str | None,
    tire_query: dict[str, Any] | None,
) -> str | None:
    if promos is None:
        return None
    for grant in promos.grants:
        if grant.condition == condition and _grant_applies(grant, text, tire_query):
            return grant.bot_text
    return None


def _brand_list(names: Any) -> str:
    return ", ".join(n[:1].upper() + n[1:] for n in sorted(names))


def _phrase(
    topic: str,
    policy: NetworkPolicy,
    promos: PromoOverrides | None,
    text: str | None,
    tire_query: dict[str, Any] | None,
) -> str | None:
    """The phrase of one topic, from the policy (or a live promotion), or ``None``."""
    carriers = ", ".join(policy.delivery_carriers)
    if topic == DELIVERY_COST:
        promo = _promo_text(GRANT_FREE_DELIVERY, promos, text, tire_query)
        if promo:
            return promo
        if policy.delivery_mode == "free":
            return f"Доставка безкоштовна{f', перевізник — {carriers}' if carriers else ''}."
        if policy.delivery_mode == "carrier_tariff":
            # Never a sum: the carrier's tariff is all the network promises.
            return f"Доставка — за тарифами перевізника{f' {carriers}' if carriers else ''}."
        return None
    if topic == DELIVERY_ETA:
        return f"Доставка — {policy.delivery_eta_text}." if policy.delivery_eta_text else None
    if topic == TRACKING:
        return _sentence(policy.tracking_text) if policy.tracking_text else None
    if topic == PICKUP:
        return "Самовивіз є." if policy.pickup_available else None
    if topic == INSTALLMENTS:
        if "installments" not in policy.payment_methods:
            return None
        banks = " або ".join(BANK_LABELS[b] for b in policy.installment_banks)
        return f"Оплата частинами є{f' — через {banks}' if banks else ''}."
    if topic == COD:
        if "cod" not in policy.payment_methods:
            return None
        fee = f", комісія — {policy.cod_fee_text}" if policy.cod_fee_text else ""
        return f"Накладений платіж є{fee}."
    if topic == PAYMENT:
        labels = payment_method_labels(policy)
        return f"Оплатити можна так: {'; '.join(labels)}." if labels else None
    if topic == EXTENDED_WARRANTY:
        promo = _promo_text(GRANT_EXTENDED_WARRANTY, promos, text, tire_query)
        if promo:
            return promo
        if policy.extended_warranty_brands:
            return (
                "Розширена гарантія мережі діє на шини "
                f"{_brand_list(policy.extended_warranty_brands)}."
            )
        own_grants = promos is not None and any(
            g.condition == GRANT_EXTENDED_WARRANTY for g in promos.grants
        )
        # A live promotion for another brand: the model answers with its terms.
        if policy.configured and not own_grants:
            return "Розширеної гарантії мережа не надає."
        return None
    if topic == WARRANTY:
        return _sentence(policy.warranty_text) if policy.warranty_text else None
    if topic == RETURNS:
        return _sentence(policy.returns_text) if policy.returns_text else None
    return None


def fact_sentences(
    topics: list[str],
    policy: NetworkPolicy | None,
    promos: PromoOverrides | None = None,
    *,
    text: str | None = None,
    tire_query: dict[str, Any] | None = None,
) -> list[str]:
    """The phrases of ``topics`` (one per topic, at most ``MAX_FACTS_PER_TURN``)."""
    if policy is None or not policy.sales_enabled:
        return []
    out: list[str] = []
    for topic in TOPICS:
        if topic not in topics:
            continue
        phrase = _phrase(topic, policy, promos, text, tire_query)
        if phrase and phrase not in out:
            out.append(phrase)
        if len(out) >= MAX_FACTS_PER_TURN:
            break
    return out


def turn_facts(
    text: str | None,
    policy: NetworkPolicy | None,
    promos: PromoOverrides | None = None,
    tire_query: dict[str, Any] | None = None,
) -> list[str]:
    """What the code says first on this turn (both loops call this before round 1).

    Sales off → nothing (``fact_sentences`` is the one sales check).
    """
    topics = fact_topics(text)
    if not topics:
        return []
    phrases = fact_sentences(topics, policy, promos, text=text, tire_query=tire_query)
    if phrases:
        logger.warning(
            "network_facts topics=%s phrases=%d last_customer_text=%r",
            ",".join(topics),
            len(phrases),
            (text or "")[:120],
        )
    return phrases


def already_said_note(phrases: list[str]) -> str:
    """The system-prompt tail telling the model what the caller has just heard."""
    if not phrases:
        return ""
    quoted = " ".join(f"«{p}»" for p in phrases)
    return (
        "\n\n## Вже сказано клієнту в цьому ході\n"
        f"Код уже сказав клієнту: {quoted} Не повторюй цього, відповідай далі по суті."
    )
