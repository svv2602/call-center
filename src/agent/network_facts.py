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

from src.agent import delivery_quote as dq
from src.agent.network_policy import BANK_LABELS, SERVICE_LABELS, payment_method_labels
from src.agent.promotions import (
    GRANT_EXTENDED_WARRANTY,
    GRANT_FREE_DELIVERY,
    _brand_keys,
    _named_brands,
)
from src.agent.prompts import _SERVICE_NOT_PROVIDED_PHRASE

if TYPE_CHECKING:
    from src.agent.network_policy import NetworkPolicy
    from src.agent.promotions import PromoGrant, PromoOverrides

logger = logging.getLogger(__name__)

#: A service of ``SERVICE_LABELS`` the network does not offer («запишете на
#: шиномонтаж?», «дві машини на шиномонтаж», «купити і щоб одразу поставили»
#: on Про Колесо): R03/R07.3 — on gpt-4.1-mini the model asked for the city
#: instead of saying the scope frame's «не надаємо», so the code says it.
#: Over the whole enum (default-deny): a key without word forms here gets a
#: pattern from its label. The tyre sale goes on — the model's turn is not cut.
SERVICE = "service"
SERVICE_REFUSAL_PREFIX = "На жаль, ми не надаємо"
#: A size other than the car's own («225/45 R17 замість 205/55 R16?»): the
#: owner's rule is «нештатний розмір підбирає спеціаліст». Network-agnostic,
#: said by the code like the network's terms — the prompt rule held in only
#: one TШ goldset run of three.
NON_STOCK_SIZE = "non_stock_size"
NON_STOCK_SIZE_PHRASE = "Нештатний розмір підбирає спеціаліст — можу передати ваш запит менеджеру."
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
    SERVICE,
    NON_STOCK_SIZE,
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


#: Words that ask for another size on their own: «ширші», «інший діаметр»,
#: «нештатний», «більший радіус».
_SIZE_CHANGE = re.compile(
    _B + r"(?:нештатн\w*|нестандартн\w*|ширш\w*|шире|вужч\w*"
    r"|інш\w*\s+(?:розмір\w*|діаметр\w*|радіус\w*|профіл\w*)"
    r"|друг\w*\s+(?:размер\w*|диаметр\w*|радиус\w*|профил\w*)"
    r"|(?:більш\w*|больш\w*|менш\w*|збільш\w*|увелич\w*|зменш\w*|уменьш\w*)\s+"
    r"(?:\w+\s+){0,1}(?:розмір\w*|размер\w*|діаметр\w*|диаметр\w*|радіус\w*|радиус\w*))"
)
#: «замість / вместо» — a size change only between two different sizes
#: («225/45 R17 замість 205/55 R16», «215 вместо 205»), not «шини замість
#: старих» or «доставку замість самовивозу».
_INSTEAD = re.compile(_B + r"(?:замість|замiсть|вместо)(?![\w'])")
#: A tyre width said as a number.
_WIDTH_NUMBER = re.compile(r"(?<!\d)(?:1[3-9]\d|2\d\d|3[0-5]\d)(?!\d)")
#: One parameter said with two values: «55 профіль стоїть, а я поставлю 60»,
#: «16-й радіус, можна 17-й?». The word names the parameter, the range the
#: numbers that can be its values (so in «225/55 R17, профіль…» 17 is no profile).
_PROFILE_WORD = re.compile(_B + r"(?:профіл\w*|профил\w*)")
_RADIUS_WORD = re.compile(_B + r"(?:радіус\w*|радиус\w*|діаметр\w*|диаметр\w*)")
#: Fitting or choosing the other value — without it «профіль 55 чи 65?» is the
#: caller unsure of their own size, which the forced search must still serve.
_PUT = re.compile(
    _B + r"(?:постав\w*|став\w*|взят\w*|взя\w*|візьм\w*|возьм\w*|брат\w*|бра\w*"
    r"|замість|замiсть|вместо)(?![\w'])"
)
#: The caller's «what if»: «якщо 55, а 60…», «если поставлю…», «різниця».
_WHAT_IF = re.compile(_B + r"(?:якщо|якшо|если|різниц\w*|разниц\w*|скаж\w*)(?![\w'])")
#: «якщо 55, а 60 взяти?» — two profile values with no parameter word.
_IF_A_THEN_B = re.compile(
    _B + r"(?:якщо|якшо|если)\s+(\d{2})(?!\d)[\s,]+(?:а|або|или)\s+(\d{2})(?!\d)"
)
_TWO_DIGIT = re.compile(r"(?<!\d)\d{2}(?!\d)")


def _profile_value(n: int) -> bool:
    return 25 <= n <= 85 and n % 5 == 0


def _radius_value(n: int) -> bool:
    return 12 <= n <= 24


def _one_parameter_two_values(low: str) -> bool:
    """«55 профіль стоїть, а я поставлю 60» — one parameter, two values, fitted."""
    if not _PUT.search(low):
        return False
    numbers = {int(n) for n in _TWO_DIGIT.findall(low)}
    if _PROFILE_WORD.search(low) and len({n for n in numbers if _profile_value(n)}) >= 2:
        return True
    if _RADIUS_WORD.search(low) and len({n for n in numbers if _radius_value(n)}) >= 2:
        return True
    pair = _IF_A_THEN_B.search(low)
    return bool(
        pair
        and pair.group(1) != pair.group(2)
        and all(_profile_value(int(g)) for g in pair.groups())
    )


def is_non_stock_size_question(text: str | None) -> bool:
    """Does the caller ask about fitting a size other than the car's own?"""
    if not text:
        return False
    low = _normalize(text)
    if (_ASK.search(low) or _WHAT_IF.search(low)) and _one_parameter_two_values(low):
        return True
    if not _ASK.search(low):
        return False
    if _SIZE_CHANGE.search(low):
        return True
    if not _INSTEAD.search(low):
        return False
    from src.agent.parsers.tire_query import parse_tire_size

    sizes = {(s.width, s.aspect, s.diameter) for s in parse_tire_size(text) or [] if s.width}
    return len(sizes) >= 2 or len(set(_WIDTH_NUMBER.findall(low))) >= 2


#: Word forms per ``SERVICE_LABELS`` key, UA and RU, every case.
_SERVICE_FORMS: dict[str, re.Pattern[str]] = {
    "fitting": re.compile(_B + r"(?:\w*монтаж\w*|перевзу\w*|переобу\w*|балансув\w*|балансир\w*)"),
    "storage": re.compile(
        _B + r"(?:зберіганн\w*|хранени\w*|зберігаєте|храните|шинн\w*\s+готел\w*"
        r"|сезонн\w*\s+(?:зберіг\w*|хран\w*))"
    ),
}
#: «купити і щоб одразу поставили», «а можна у вас одразу поставити?» — a
#: fitting asked with no service word. Without «одразу / щоб» a «поставити»
#: is the caller's own size choice («можна поставити 225/45 R17?»).
_FIT_PUT = re.compile(_B + r"постав\w*")
_FIT_PUT_FOR_ME = re.compile(
    _B + r"(?:одразу|відразу|зразу|сразу|заодно|на\s+місці|на\s+месте)(?![\w'])"
    r"|" + _B + r"(?:щоб|щоби|чтоб|чтобы)\s+(?:\w+\s+){0,2}постав\w*"
)
#: The caller fits the tyres himself.
_FIT_PUT_NOT_US = re.compile(
    _B + r"(?:поставлю|поставим\w*|сам|сама|самі|сами|самостійно|самостоятельно)(?![\w'])"
)
#: The service word as a circumstance, not asked for: «гарантія після
#: шиномонтажу», «пошкодження під час монтажу», «без монтажу», «шини для
#: шиномонтажу на СТО» (the caller is a fitting shop buying tyres — a sale,
#: not a fitting request), «я з шиномонтажу». «з монтажем» (with fitting) is
#: a request: after «з / с» only the genitive is a circumstance.
_SERVICE_LEAD = re.compile(
    r"(?<![\w'])(?:після|после|під\s+час|во\s+время|без|для|від|от)\s+(?:\w+\s+)?$"
)
_SERVICE_FROM = re.compile(r"(?<![\w'])(?:з|із|зі|с|со)\s+(?:\w+\s+)?$")
_INSTRUMENTAL = re.compile(r"(?:ем|ом|ам|ами|ою|ой)$")
#: «доставка на шиномонтаж до знайомого» — a delivery address, not a booking.
_SERVICE_DESTINATION = re.compile(r"(?<![\w'])(?:на|до|в|у)\s+(?:\w+\s+)?$")
_BOOK = re.compile(_B + r"(?:запис\w*|запиш\w*|послуг\w*|услуг\w*)")
#: «монтаж не потрібен», «шиномонтаж не треба».
_NO_NEED = re.compile(_B + r"(?:не|ні)\s+(?:\w+\s+)?(?:треба|потріб\w*|нуж\w*|надо)(?![\w'])")
#: «як правильно зберігати шини», «термін зберігання шин» — tyre care (the
#: knowledge base), not the network's storage service.
_STORAGE_CARE = re.compile(
    _B + r"(?:правильн\w*|вдома|дома|гараж\w*|балкон\w*|умов\w*|термін\w*|срок\w*"
    r"|строк\w*|годност\w*|придатн\w*)"
)


def _service_forms(key: str) -> re.Pattern[str]:
    forms = _SERVICE_FORMS.get(key)
    if forms is not None:
        return forms
    stem = SERVICE_LABELS[key].split()[0][:-2]
    return re.compile(_B + re.escape(stem) + r"\w*")


def _asks_for(key: str, low: str) -> bool:
    """One service word of ``key`` the caller asks for (not a circumstance)."""
    delivery = _DELIVERY.search(low) is not None and not _BOOK.search(low)
    for m in _service_forms(key).finditer(low):
        before = low[: m.start()]
        if _SERVICE_LEAD.search(before):
            continue
        if _SERVICE_FROM.search(before) and not _INSTRUMENTAL.search(m.group(0)):
            continue
        if delivery and _SERVICE_DESTINATION.search(before):
            continue
        return True
    return False


def asked_services(text: str | None) -> list[str]:
    """Keys of ``SERVICE_LABELS`` the utterance asks for, in enum order."""
    if not text:
        return []
    low = _normalize(text)
    # «не треба»; the warranty / returns of tyres «після монтажу» are their terms.
    if _NO_NEED.search(low) or _WARRANTY.search(low) or _RETURNS.search(low):
        return []
    out: list[str] = []
    for key in SERVICE_LABELS:
        if key == "storage" and _STORAGE_CARE.search(low):
            continue
        asked = _asks_for(key, low)
        if not asked and key == "fitting":
            asked = bool(
                _FIT_PUT.search(low)
                and _FIT_PUT_FOR_ME.search(low)
                and not _FIT_PUT_NOT_US.search(low)
            )
        if asked:
            out.append(key)
    return out


def _partner_offers(service: str, promos: PromoOverrides | None) -> bool:
    """A live promotion names a partner for ``service`` — the model says its terms."""
    return promos is not None and any(
        e.get("service") == service and (e.get("network_label") or "").strip()
        for e in promos.partner_services
    )


def _service_refusal(
    policy: NetworkPolicy, promos: PromoOverrides | None, text: str | None
) -> str | None:
    """«На жаль, ми не надаємо …» for each asked service the network lacks."""
    # Only a configured policy's empty ``services`` means «not provided».
    if not policy.configured:
        return None
    missing = [
        _SERVICE_NOT_PROVIDED_PHRASE.get(key, SERVICE_LABELS[key])
        for key in asked_services(text)
        if key not in policy.services and not _partner_offers(key, promos)
    ]
    if not missing:
        return None
    return f"{SERVICE_REFUSAL_PREFIX} {' і '.join(missing)}."


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace("’", "'").replace("ʼ", "'")


def fact_topics(text: str | None) -> list[str]:
    """Topics of ``TOPICS`` the utterance asks about, in ``TOPICS`` order."""
    if not text:
        return []
    low = _normalize(text)
    asks = _ASK.search(low) is not None
    found: set[str] = set()
    if asked_services(text):
        found.add(SERVICE)
    if is_non_stock_size_question(text):
        found.add(NON_STOCK_SIZE)

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


def free_delivery_promo_text(
    promos: PromoOverrides | None, text: str | None, tire_query: dict[str, Any] | None
) -> str | None:
    """The live free-delivery promotion's own text when it covers this line, else ``None``."""
    return _promo_text(GRANT_FREE_DELIVERY, promos, text, tire_query)


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
    if topic == SERVICE:
        return _service_refusal(policy, promos, text)
    if topic == NON_STOCK_SIZE:
        return NON_STOCK_SIZE_PHRASE
    if topic == DELIVERY_COST:
        promo = _promo_text(GRANT_FREE_DELIVERY, promos, text, tire_query)
        if promo:
            return promo
        if dq.quote_available(policy):
            # The sum comes from `get_delivery_cost` (the tool says the
            # tariff line itself when the city or the tyres are missing); a
            # tariff line said first would contradict the quoted sum.
            return None
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
        # Pickup offered: the model answers with `get_pickup_points` (the
        # addresses) — «Самовивіз є.» said first made it ask for a district
        # instead of calling the tool (goldset №7 pickup_both_networks, both
        # networks). Only the absence of pickup is the code's to say.
        return None if policy.pickup_available else "Самовивозу немає, лише доставка."
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
    note = (
        "\n\n## Вже сказано клієнту в цьому ході\n"
        f"Код уже сказав клієнту: {quoted} Не повторюй цього, відповідай далі по суті."
    )
    if any(p.startswith(SERVICE_REFUSAL_PREFIX) for p in phrases):
        # The refusal is said; the model must not go on to book it (city, station).
        note += (
            " Цієї послуги мережа не надає: НЕ питай місто, станцію чи дату для неї, "
            "НЕ переводь на оператора, НЕ згадуй інші мережі. Якщо клієнт хоче шини — "
            "продовжуй підбір і замовлення шин; інакше запитай, чи допомогти з підбором "
            "або замовленням шин."
        )
    return note
