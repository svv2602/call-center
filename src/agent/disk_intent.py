"""Wheel (disk) intent in the caller's words, and the redirect to `search_disks`.

Goldset №2 (2026-09-28): «потрібні литі диски шістнадцятий радіус на Шкоду
Октавію» and «диски тринадцятий радіус на Таврію є?» went to
`get_vehicle_tire_sizes` / `search_tires` / `search_knowledge_base` in all four
runs — the prompt rule «диски → одразу search_disks» does not hold under
attention dilution, so the redirect is made by code.

`has_disk_intent` reads one customer utterance in Ukrainian or Russian (a
one-language vocabulary reads the other language as «клієнт промовчав»).
Brake and clutch discs are not wheels; «шини на дисках» / «шини під литі диски»
are about tyres, not a wheel purchase — those phrases are cut out before the
check, so «гальмівні диски і литі диски» still counts.

`DiskToolRedirect` is per-turn state shared by both agent loops
(`StreamingAgentLoop._execute_one_tool` and `LLMAgent._execute_one`): under
`sales_enabled`, while the customer's last utterance asks for wheels and
`search_disks` is offered but not yet called this turn, a tyre-side tool call is
not run — `search_disks` runs in its place (the same `ToolRouter.execute`, so
the same audit row and metrics), with the diameter from the utterance or the
tyre call and the car from `get_vehicle_tire_sizes` arguments, and the model
gets its result marked «замість <tool> виконано search_disks». No diameter →
the result asks for it instead of calling `search_disks` with none. Once per
turn; the second such call runs (a substitution repeated every round would
leave the model no way out).

Goldset №3 (2026-09-28) is why it is a substitution and not a refusal: after
the refusal hint (`fff0ad8`) the model went to the knowledge base and answered
«потрібні розболтовка, виліт…» without ever calling `search_disks`.

The car of a substitution not made from `get_vehicle_tire_sizes` is the
caller's own words: the utterance goes to `search_disks` as ``vehicle_text``
(internal, not in the LLM schema) and `StoreClient.resolve_vehicle_text` reads
the brand / model / year out of it with the catalogue. `vehicle_words` and
`word_forms` are its pure half: the words left once wheel, tyre, size and
filler words are dropped, and the nominative forms of a word heard in another
case («Шкоду» → «шкода», «Октавію» → «октавія» / «октавия»).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agent.disk_fitment import to_int
from src.agent.parsers.tire_query import parse_tire_size

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

DISK_ASK_DIAMETER = "Уточни в клієнта діаметр дисків (радіус)."

# A wheel's seat diameter in inches; anything outside is not a wheel size.
_DIAMETER_MIN = 10
_DIAMETER_MAX = 30

# Tools the model reaches for instead of `search_disks` (goldset №2).
DISK_REDIRECTED_TOOLS = frozenset(
    {"get_vehicle_tire_sizes", "search_tires", "search_knowledge_base"}
)

# диск / диска / диску / диском / диске / диски / дисків / дисков / дискам /
# дисками / дисках — one stem, UA and RU case endings.
_DISK = r"диск(?:а|у|ом|е|і|и|ів|ов|ам|ами|ах)?"

# Wheel adjectives that name a wheel on their own: литі / литые / литий / литой,
# штамповані / штампованные / штамповка, ковані / кованые, лиття / литьё.
_WHEEL_WORDS = re.compile(
    r"\b(?:" + _DISK + r"|лит(?:і|ий|а|е|их|им|ими|у|ої|ою|ые|ых|ым|ыми|ой|ую|ая|ое)"
    r"|лиття|литье|литво"
    r"|штампован\w*|штамповк\w*"
    r"|кован(?:і|ий|а|е|их|им|ими|у|ої|ою|ые|ых|ым|ыми|ой|ую|ая|ое)"
    r")\b",
    re.IGNORECASE,
)

# Phrases where «диск» is not a wheel the customer wants to buy.
_NOT_WHEEL = re.compile(
    r"\b(?:гальмівн\w*|тормозн\w*)\s+" + _DISK + r"\b"
    r"|\b" + _DISK + r"\s+(?:\w+\s+)?(?:гальм\w*|тормоз\w*|зчеплен\w*|сцеплен\w*)"
    # «шини на дисках», «шини під литі диски», «без дисків» — tyres on rims.
    r"|\b(?:на|без|під|под)\s+(?:\w+\s+)?" + _DISK + r"\b",
    re.IGNORECASE,
)


def has_disk_intent(text: str | None) -> bool:
    """True when the utterance asks about wheels (disks), UA or RU."""
    if not text:
        return False
    low = text.lower().replace("ё", "е")
    low = _NOT_WHEEL.sub(" ", low)
    return _WHEEL_WORDS.search(low) is not None


def last_customer_text(history: list[dict[str, Any]]) -> str:
    """The caller's latest free-text utterance (tool_result turns skipped)."""
    for msg in reversed(history):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            texts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            joined = " ".join(t for t in texts if t.strip())
            if joined:
                return joined
    return ""


# ── The car in the caller's words ────────────────────────────────────────

# Model years the catalogue holds; a 4-digit number outside is a model (ВАЗ 2107).
_YEAR_MIN = 1980
_YEAR = re.compile(r"(\d{4})(?:-?(?:го|ого|й|р|г|рік|року|год|года))?")
# «16», «R16», «р16», «16.5» — a wheel size, never a car.
_SIZE_TOKEN = re.compile(r"[rр]?(\d{2})(?:[.,]\d)?")

# Words that are never a car (UA + RU): function words, fillers, the request.
_STOP_WORDS = frozenset(
    """
    на для під под до від от із из зі со за по про при над через або или чи ли та
    але но же ну ось вот ще еще теж тоже так ні нет да це это то той ця эта ці эти
    мені мне мене меня нам нас ми мы ви вы вам вас мій моя моє мої мою мой моей свою
    свій своя свое своє свої свои наш наша нашу є есть був була було была было маю
    имею треба надо можна можно можете будь ласка будь-ласка пожалуйста дякую спасибо
    добрий добрый день вечір вечер привіт привет здрастуйте здравствуйте алло щось
    что-то шо що что там тут може может мабуть наверное типу типа які какие який
    какой яка какая якісь какие-то рік року роки році років год года году лет
    авто тачка тачку тачки
    """.split()  # noqa: SIM905 — a word list reads better as text
)
_STOP_PATTERN = re.compile(
    r"(?:потрібн\w*|нужн\w*|машин\w*|автомобіл\w*|автомобил\w*|модел\w*|марк[аиуі]"
    r"|комплект\w*|штук\w*|наявн\w*|налич\w*|випуск\w*|выпуск\w*|зимн\w*|зимов\w*"
    r"|літн\w*|летн\w*|всесезон\w*|нов(?:ий|і|а|е|у|их|ый|ые|ая|ое|ую|ых)?|вживан\w*"
    r"|шин\w*|резин\w*|гум[аиуі]|покришк\w*|покрышк\w*|колес\w*|колiс|колёс|радіус\w*"
    r"|радиус\w*|дюйм\w*|розмір\w*|размер\w*|діаметр\w*|диаметр\w*|розболтовк\w*"
    r"|разболтовк\w*|\w*надцят\w*|\w*надцат\w*|двадцят\w*|двадцат\w*|підібр\w*"
    r"|подобр\w*|підбер\w*|подбер\w*|підкаж\w*|подскаж\w*|цікав\w*|интерес\w*|хоч\w*"
    r"|хоті\w*|хоте\w*|шука\w*|ищ[уе]\w*|скільк\w*|скольк\w*|кошту\w*|стоит|стоят"
    r"|цін[аиу]|цен[аыу]|вартіст\w*|стоимост\w*)"
)

# Case endings back to the nominative (UA + RU); the as-heard word is tried first.
_DECLENSION = (
    ("ією", "ія"),
    ("ией", "ия"),
    ("ію", "ія"),
    ("ию", "ия"),
    ("ії", "ія"),
    ("ии", "ия"),
    ("ою", "а"),
    ("ой", "а"),
    ("ові", ""),
    ("ом", ""),
    ("у", "а"),
    ("ю", "я"),
    ("і", "а"),
    ("и", "а"),
    ("е", "а"),
    ("ю", "ь"),
    ("я", "ь"),
    ("у", ""),
    ("а", ""),
    ("і", ""),
    ("е", ""),
)
_UK_TO_RU = str.maketrans({"і": "и", "ї": "и", "є": "е", "'": ""})
_CYRILLIC_WORD = re.compile(r"[а-яіїєґ']+")


def _is_car_word(word: str) -> bool:
    if len(word) < 2 or word in _STOP_WORDS:
        return False
    if _STOP_PATTERN.fullmatch(word) or _WHEEL_WORDS.fullmatch(word):
        return False
    size = _SIZE_TOKEN.fullmatch(word)
    return not (size and _DIAMETER_MIN <= int(size.group(1)) <= _DIAMETER_MAX)


def vehicle_words(text: str | None) -> tuple[list[str], int | None]:
    """The words of an utterance that may name a car, and the model year.

    Lower-cased, in order; wheel / tyre / size / filler words, wheel diameters
    and single letters are dropped. The year is the first 4-digit number in
    1980..this year («2018», «2018-го», «2018р»); other 4-digit numbers stay
    (ВАЗ 2107 is a model).
    """
    if not text:
        return [], None
    low = text.lower().replace("ё", "е")
    low = re.sub(r"[’ʼ`]", "'", low)
    words: list[str] = []
    year: int | None = None
    this_year = datetime.date.today().year
    for raw in re.findall(r"[\w'\-]+", low):
        word = raw.strip("'-")
        match = _YEAR.fullmatch(word)
        if match and _YEAR_MIN <= int(match.group(1)) <= this_year:
            if year is None:
                year = int(match.group(1))
            continue
        if _is_car_word(word):
            words.append(word)
    return words, year


def word_forms(phrase: str) -> list[str]:
    """``phrase`` as heard, then with its last word put back into the nominative
    (UA and the RU spelling): «шкоду» → «шкода», «октавію» → «октавія», «октавия»."""
    head, _, last = phrase.rpartition(" ")
    prefix = f"{head} " if head else ""
    lasts = [last]
    if _CYRILLIC_WORD.fullmatch(last):
        for suffix, repl in _DECLENSION:
            if last.endswith(suffix) and len(last) - len(suffix) >= 3:
                lasts.append(last[: -len(suffix)] + repl)
    forms: list[str] = []
    for word in lasts:
        for form in (prefix + word, (prefix + word).translate(_UK_TO_RU)):
            if form not in forms:
                forms.append(form)
    return forms


def _plausible_diameter(value: Any) -> int | None:
    d = to_int(value)
    if d is None or not _DIAMETER_MIN <= d <= _DIAMETER_MAX:
        return None
    return d


def disk_diameter(text: str, tool_args: dict[str, Any]) -> int | None:
    """The wheel diameter: the caller's words first, then the tyre call's ``diameter``."""
    sizes = parse_tire_size(text) if text else None
    if sizes:
        d = _plausible_diameter(sizes[0].diameter)
        if d is not None:
            return d
    return _plausible_diameter(tool_args.get("diameter"))


def disk_vehicle(tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any] | None:
    """The car of a `get_vehicle_tire_sizes` call, or None — never guessed."""
    if tool_name != "get_vehicle_tire_sizes":
        return None
    vehicle: dict[str, Any] = {}
    for key in ("brand", "model"):
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            vehicle[key] = value.strip()
    if not vehicle:
        return None
    year = to_int(tool_args.get("year"))
    if year is not None and year > 0:
        vehicle["year"] = year
    return vehicle


@dataclass(frozen=True)
class DiskSubstitution:
    """A tyre-side call replaced by `search_disks` (``args`` None → ask the diameter)."""

    replaced_tool: str
    args: dict[str, Any] | None

    @property
    def note(self) -> str:
        if self.args is None:
            return (
                f"({self.replaced_tool} не виконано — клієнт питає про диски.) {DISK_ASK_DIAMETER}"
            )
        return f"(замість {self.replaced_tool} виконано search_disks — клієнт питає про диски)"


async def run_disk_substitution(
    sub: DiskSubstitution,
    execute: Callable[[str, dict[str, Any]], Awaitable[Any]],
    *,
    timeout: float,
    sales_enabled: bool,
) -> str:
    """The tool_result text of a substitution: `search_disks` run, or the diameter question.

    ``execute`` is the loop's own ``ToolRouter.execute`` — the substituted call
    gets the same `call_tool_calls` row and metrics as one the model made.
    """
    from src.agent.tool_result_compressor import compress_tool_result
    from src.monitoring.metrics import tool_call_errors_total

    if sub.args is None:
        return sub.note
    try:
        raw = await asyncio.wait_for(execute("search_disks", sub.args), timeout=timeout)
    except TimeoutError:
        logger.error("Tool search_disks (substituted) timed out after %ss", timeout)
        tool_call_errors_total.labels(tool_name="search_disks", error_type="timeout").inc()
        raw = {"error": "Сервіс тимчасово не відповідає, спробуйте ще раз"}
    content = compress_tool_result("search_disks", raw, sales_enabled=sales_enabled, args=sub.args)
    return f"{sub.note}\n{content}"


class DiskToolRedirect:
    """Per-turn substitution of tyre-side tools by `search_disks`."""

    def __init__(self, *, sales_enabled: bool, tools: Iterable[dict[str, Any]]) -> None:
        self._armed = sales_enabled and any(t.get("name") == "search_disks" for t in tools)
        self._disks_called = False
        self._substituted = False

    def note_round(self, tool_names: Iterable[str]) -> None:
        """Record the tools the model asked for in this round, before they run."""
        if "search_disks" in tool_names:
            self._disks_called = True

    def check(
        self, tool_name: str, tool_args: dict[str, Any], history: list[dict[str, Any]]
    ) -> DiskSubstitution | None:
        """The substitution if this call must not run as asked, else None."""
        if not self._armed or self._disks_called or self._substituted:
            return None
        if tool_name not in DISK_REDIRECTED_TOOLS:
            return None
        last = last_customer_text(history)
        if not has_disk_intent(last):
            return None
        self._substituted = True
        args = tool_args if isinstance(tool_args, dict) else {}
        diameter = disk_diameter(last, args)
        if diameter is None:
            logger.warning(
                "disk_tool_substitute tool=%s no_diameter last_customer_text=%r",
                tool_name,
                last[:120],
            )
            return DiskSubstitution(tool_name, None)
        disk_args: dict[str, Any] = {"diameter": diameter}
        vehicle = disk_vehicle(tool_name, args)
        if vehicle is not None:
            disk_args["vehicle"] = vehicle
        else:
            # No car in the call's arguments: `search_disks` reads it from the
            # caller's words («диски на Шкоду Октавію 2018»), never a guess.
            disk_args["vehicle_text"] = last
        logger.warning(
            "disk_tool_substitute tool=%s args=%r last_customer_text=%r",
            tool_name,
            disk_args,
            last[:120],
        )
        return DiskSubstitution(tool_name, disk_args)
