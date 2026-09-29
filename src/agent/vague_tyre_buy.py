"""«Купити резину» with no car and no size: the code asks the first question.

Goldset №8 (2026-09-29), ProKoleso: «там мені купити треба резину» and «нужны
шины» were answered with the menu question of `_MOD_CORE_TAIL` — «Підбір та
замовлення шин чи є питання щодо товарів або послуг?» — to a caller who had
just said what they want (prod call `ee8d68a6`; red in goldsets №4, №6, №8
every other run). A prompt edit does not hold (the plateau of three
goldsets), so the first question of a tyre pick is the code's.

The turn is the code's, not a phrase in front of the model's: the phrase is
the whole reply and no LLM round runs. A phrase before the stream plus an
«already asked» note would still leave the model a round to say something —
the menu again, or a second question — and the caller would hear two
questions in a row; a post-guard would have to replace a reply that is
already partly spoken. Nothing is lost by skipping the round: with no car
and no size there is no tool to run (`search_tires` needs a size, the forced
car lookup a car), and the predicate is default-deny about everything else.

`vague_tyre_buy(...)` — the predicate — fires only when ALL hold:

- the utterance says the caller wants tyres: a want/buy word (UA + RU:
  «купити / купить / треба / потрібні / нужны / надо / підібрати /
  подобрать / хочу / шукаю…») and a tyre noun («шини / резина / гума /
  покришки / колеса»);
- it is not a fitting request (`pipeline._FITTING_REQUEST_RE`), not about
  wheels (`disk_intent.has_disk_intent`), not a service («поміняти»,
  «ремонт», «проколов»), not a negation («не треба»), and asks nothing else
  (delivery, payment, warranty, returns, an order, an operator — and no
  `network_facts.fact_topics` topic, whose phrases the code says itself);
- it names no size or diameter (`parse_tire_size`) and no car
  (any word `vehicle_lookup_gate.car_words` / `named_car_words` keep, other
  than the generic «моє авто» — «Тойота Камрі, шини потрібні» names a car
  with no «на / для» in front of it);
- the request has no ``sizes`` / ``diameter`` yet, no car was looked up in
  the call (``get_vehicle_tire_sizes`` in the history or among the call's
  tools), no tyre search ran, and the bot's last line did not already ask
  for the car or the size;
- the call is not in a fitting booking.

`VagueBuyGate` holds the loop-breaker: once per call. If the caller answers
the question with neither a car nor a size, the model takes it from there.

Sales scope only: with ``sales_enabled`` off the gate is never armed and
both loops run byte for byte as before.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from src.agent.disk_intent import has_disk_intent
from src.agent.network_facts import fact_topics
from src.agent.parsers.tire_query import parse_tire_size
from src.agent.vehicle_lookup_gate import LOOKUP_TOOL, car_words, named_car_words

logger = logging.getLogger(__name__)

#: The code's question — the whole reply of the turn. It asks for the car
#: («авто») and ends with «?», so on the next turn the car lookup gate reads
#: the caller's answer as the car (`vehicle_lookup_gate._bot_asked_car`).
ASK_CAR_OR_SIZE = "Підкажіть, на яке авто підбираємо шини — або розмір з боковини шини?"

_SEARCH_TOOL = "search_tires"

_B = r"(?<![\w'])"
_E = r"(?![\w'])"

#: A tyre noun, UA + RU. «шиномонтаж» is not one (and is a fitting request).
_TYRE = r"(?:шин(?!омонт)\w*|резин\w*|гум[аиуі]|покришк\w*|покрышк\w*|колес\w*|коліс\w*)"
_TYRE_NOUN = re.compile(_B + _TYRE + _E)

#: The caller wants tyres: to buy, to have picked, needs them.
_BUY = (
    r"(?:купит[иь]|купить|куплю|купуват\w*|купуєм\w*|купляти|покупа\w*|придбат\w*"
    r"|придбаю|приобрест\w*|приобрет\w*"
    r"|підібрат\w*|підберіт\w*|підбер[уе]\w*|підбір|подобрат\w*|подберит\w*|подбер[уе]\w*"
    r"|подбор\w*)"
)
_WANT = re.compile(
    _B
    + r"(?:"
    + _BUY
    + r"|треба|требо|потрібн\w*|нужн\w*|надо|шукаю|шукаєм\w*|ищу|ищем)"
    + _E
    # «хочу» wants only what follows it: «хочу шини», «хочу купити» — not
    # «хотел уточнить», «хочемо узнати ціну».
    + r"|"
    + _B
    + r"(?:хочу|хочем\w*|хотів|хотіла|хотіли|хотел\w*)\s+(?:\w+\s+){0,2}?"
    r"(?:" + _BUY + r"|" + _TYRE + r")" + _E
)

#: «не треба», «не нужны», «не буду купувати», «вже купив».
_NEGATED = re.compile(
    _B + r"(?:не|ні|нет)\s+(?:\w+\s+){0,1}(?:купит[иь]|купить|куплю|купуват\w*|треба|потрібн\w*"
    r"|нужн\w*|надо|хочу|хочем\w*|підбир\w*|подбир\w*)"
    + _E
    + r"|"
    + _B
    + r"(?:вже|уже)\s+(?:\w+\s+){0,1}(?:купи\w*|придба\w*|замови\w*|заказа\w*)"
)

#: Anything else in the utterance → the model's turn, not this gate's.
_OTHER_TOPIC = re.compile(
    _B + r"(?:достав\w*|оплат\w*|оплач\w*|заплат\w*|розстроч\w*|рассроч\w*|частинами|частями"
    r"|кредит\w*|гарант\w*|поверн\w*|поверта\w*|возврат\w*|верну\w*|обмін\w*|обмен\w*"
    r"|замовля\w*|замовил\w*|замовлен\w*|заказыва\w*|заказал\w*|заказ|заказа|статус\w*"
    r"|відправ\w*|отправ\w*|ттн|накладн\w*|самовив\w*|самовыв\w*"
    r"|оператор\w*|менеджер\w*|консультант\w*|людин\w*|человек\w*"
    r"|помін\w*|поменя\w*|замін\w*|замен\w*|ремонт\w*|прокол\w*|пробив\w*|пробил\w*"
    r"|вулкан\w*|накач\w*|підкач\w*|подкач\w*|латк\w*|перебор\w*|перебурт\w*"
    # to find something out, a call back — a question, not a pick
    r"|дізна\w*|узна\w*|уточн\w*|спита\w*|запита\w*|спрос\w*|поцікав\w*|перевір\w*"
    r"|провер\w*|дзвон\w*|звон\w*|передзвон\w*|перезвон\w*)" + _E
)

#: Car words that name no car: «шини для мого авто», «на нашу машину».
_GENERIC_CAR = re.compile(
    r"(?:мо[єйюяг]\w*|мо[её]\w*|мою|мой|мого|наш\w*|ваш\w*|свою|свій|свой|авто\w*"
    r"|машин\w*|автомобіл\w*|автомобил\w*|тачк\w*|собі|себе|нов\w*)"
)

#: The bot's last line already asked for the car or the size.
_BOT_ASKED = re.compile(
    _B + r"(?:авто\w*|машин\w*|марк[аиуі]|модел\w*|розмір\w*|размер\w*|діаметр\w*"
    r"|диаметр\w*|радіус\w*|радиус\w*|боковин\w*)" + _E
)

#: A longer utterance carries more than the want; leave it to the model.
_MAX_WORDS = 16


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е").replace("’", "'").replace("ʼ", "'")


def says_wants_tyres(text: str | None) -> bool:
    """The utterance itself: wants tyres, and nothing the gate must leave alone."""
    if not text or not text.strip():
        return False
    from src.core.pipeline import _FITTING_REQUEST_RE

    low = _normalize(text)
    if len(low.split()) > _MAX_WORDS:
        return False
    if not _TYRE_NOUN.search(low) or not _WANT.search(low):
        return False
    if _NEGATED.search(low) or _OTHER_TOPIC.search(low) or fact_topics(text):
        return False
    if _FITTING_REQUEST_RE.search(low) or has_disk_intent(text):
        return False
    return not parse_tire_size(text)


def names_a_car(text: str, last_bot_text: str = "") -> bool:
    """A car word in the utterance other than the generic «моє авто».

    Default deny: any word the car readers keep counts, with or without a
    «на / для» marker in front of it.
    """
    words = car_words(text)[0] + named_car_words(text, last_bot_text)[0]
    return any(not _GENERIC_CAR.fullmatch(w) for w in words)


def _history_tools(history: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for msg in history:
        content = msg.get("content")
        if msg.get("role") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names.add(str(block.get("name") or ""))
    return names


def vague_tyre_buy(
    user_text: str,
    *,
    query: dict[str, Any] | None,
    history: list[dict[str, Any]],
    tools_called: set[str] | frozenset[str] | None,
    last_bot_text: str,
    in_fitting: bool,
) -> bool:
    """The predicate: this turn's reply is the code's question (see module doc)."""
    if in_fitting:
        return False
    if not says_wants_tyres(user_text):
        return False
    q = query or {}
    if q.get("sizes") or q.get("diameter"):
        return False
    done = set(tools_called or ()) | _history_tools(history)
    if LOOKUP_TOOL in done or _SEARCH_TOOL in done:
        return False
    if _BOT_ASKED.search(_normalize(last_bot_text or "")):
        return False
    return not names_a_car(user_text, last_bot_text)


class VagueBuyGate:
    """Per call: the code's first question of a tyre pick, at most once."""

    def __init__(self, *, sales_enabled: bool) -> None:
        self._armed = sales_enabled
        self._fired = False

    def plan(
        self,
        user_text: str,
        *,
        query: dict[str, Any] | None,
        history: list[dict[str, Any]],
        tools_called: set[str] | frozenset[str] | None,
        last_bot_text: str,
        in_fitting: bool,
    ) -> str | None:
        """The phrase that is this turn's whole reply, or ``None``."""
        if not self._armed or self._fired:
            return None
        if not vague_tyre_buy(
            user_text,
            query=query,
            history=history,
            tools_called=tools_called,
            last_bot_text=last_bot_text,
            in_fitting=in_fitting,
        ):
            return None
        self._fired = True
        logger.info(
            "Vague tyre buy: the code asks the car or the size, text=%r", (user_text or "")[:120]
        )
        return ASK_CAR_OR_SIZE
