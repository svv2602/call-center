"""Requests only a manager can settle: the code transfers the call itself.

Owner decision (R01, R02, 2026-10-27): a military (УБД) discount, an invoice
for a company with VAT, a company code (ЄДРПОУ), and — at Твоя шина — buying
tyres and having them fitted in the same call go to an operator, at once
(R04), with what the call has collected so far in the transfer's ``summary``
so the operator does not ask again.

The transfer is made by the code (`run_forced_tool` → ``ToolRouter.execute``),
not asked of the model through a prompt line (R08): a prompt rule is the
model's to forget, and the model's own ``transfer_to_operator`` is refused by
the default-deny transfer guard unless the caller's words carry evidence —
«УБД» and «ПДВ» are not on its lists. The guard judges the model's calls only;
this predicate is the evidence for the code's.

Sales scope only (`NetworkPolicy.sales_enabled`, R11): with sales off nothing
here is armed. At most once per call (`OperatorOnlyGate`): a transfer refused
after hours would be refused again, and the turns after it are the callback
the refusal asked for — a continuation, not a repeat to re-transfer.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from src.agent.network_policy import NetworkPolicy

logger = logging.getLogger(__name__)

TRANSFER_TOOL = "transfer_to_operator"
_SEARCH_TOOL = "search_tires"

MILITARY = "military"
VAT_INVOICE = "vat_invoice"
COMPANY_CODE = "company_code"
ORDER_PLUS_FITTING = "order_plus_fitting"
INTENTS = (MILITARY, VAT_INVOICE, COMPANY_CODE, ORDER_PLUS_FITTING)

#: What the caller hears before the redirect — the only sentence a
#: transferred caller hears (AMI tears the channel down right after).
PHRASES: dict[str, str] = {
    MILITARY: "Знижку для військових оформлює менеджер — з'єдную вас з ним.",
    VAT_INVOICE: "Рахунок на підприємство виставляє менеджер — з'єдную вас з ним.",
    COMPANY_CODE: "Оформлення на підприємство веде менеджер — з'єдную вас з ним.",
    ORDER_PLUS_FITTING: ("Замовлення шин разом із монтажем оформлює менеджер — з'єдную вас з ним."),
}

#: The operator-facing label of each intent in the ``summary``.
_LABELS: dict[str, str] = {
    MILITARY: "знижка для військових (УБД)",
    VAT_INVOICE: "рахунок на підприємство / з ПДВ",
    COMPANY_CODE: "оформлення на підприємство (ЄДРПОУ)",
    ORDER_PLUS_FITTING: "купівля шин разом із монтажем",
}

_SEASONS = {"winter": "зимові", "summer": "літні", "all_season": "всесезонні", "any": "будь-який"}

_B = r"(?<![\w'])"

# ── military ─────────────────────────────────────────────────────────
_UBD = re.compile(_B + r"(?:убд|у\.?\s?б\.?\s?д\.?|убдш\w*|убедеш\w*)(?![\w'])")
_COMBAT = re.compile(
    _B + r"(?:бойов\w*\s+ді\w*|боев\w*\s+дейст\w*|учасник\w*\s+бойов\w*|участник\w*\s+боев\w*)"
)
_MILITARY_WORD = r"(?:військов\w*|военн\w*|зсу|всу|захисник\w*|ветеран\w*|атошник\w*|атовц\w*)"
_FOR_MILITARY = re.compile(_B + r"(?:для|для\s+наших)\s+" + _MILITARY_WORD)
_MILITARY = re.compile(_B + _MILITARY_WORD)
_DISCOUNT = re.compile(_B + r"(?:знижк\w*|скидк\w*|скидоч\w*|пільг\w*|льгот\w*|дисконт\w*)")

# ── VAT / invoice / company ──────────────────────────────────────────
_VAT = re.compile(_B + r"(?:пдв|ндс|п\s?д\s?в)(?![\w'])")
_WITH_VAT = re.compile(_B + r"(?:з|із|зі|с|со)\s+(?:пдв|ндс)(?![\w'])")
_INVOICE = re.compile(
    _B + r"(?:рахун(?:о|к)\w*|сч[её]т\w*|фактур\w*|видатков\w*|расходн\w*|безгот\w*|безнал\w*)"
)
_COMPANY = re.compile(
    _B + r"(?:фірм\w*|фирм\w*|підприєм\w*|предприят\w*|компані\w*|компани\w*|організац\w*|"
    r"организац\w*|юр\.?\s?особ\w*|юрособ\w*|юр\.?\s?лиц\w*|юрлиц\w*|тов|ооо|фоп|флп|"
    r"платник\w*\s+пдв|плательщик\w*\s+ндс)(?![\w'])"
)
#: «ПДВ включено в ціну?» — a question about the retail price, not an invoice.
_PRICE_QUESTION = re.compile(
    _B + r"(?:включ\w*|входить|входит|врахован\w*|учтен\w*|цін\w*|цен\w*|вартіст\w*|"
    r"стоимост\w*|кошту\w*|стоит)"
)

# ── company code ─────────────────────────────────────────────────────
_EDRPOU = re.compile(_B + r"(?:[єе]дрпо\w*|[єе]грпо\w*|[єе]\s?д\s?р\s?п\s?о\s?у)(?![\w'])")
_COMPANY_CODE = re.compile(
    _B
    + r"(?:код\w*)\s+(?:фірм\w*|фирм\w*|підприєм\w*|предприят\w*|компані\w*|компани\w*|організац\w*|организац\w*)"
)

# ── order + fitting ──────────────────────────────────────────────────
#: Buying tyres. «купив» (bought elsewhere) is a fitting request, and
#: «замовити шиномонтаж» is a booking — «замов…» counts only next to tyres.
_BUY = re.compile(
    _B
    + r"(?:купит\w*|купить|куплю|купимо|купим|придбат\w*|придбаю|придбаєм\w*|приобрест\w*|приобрету)(?![\w'])"
)
_ORDER = re.compile(_B + r"(?:замов\w*|закаж\w*|заказ\w*|оформ\w*)")
#: «шиномонтаж» is the service, not the tyres.
_TYRE = re.compile(
    _B + r"(?:шин(?!омонт)\w*|резин\w*|гум\w*|покришк\w*|покрышк\w*|колес\w*|скат\w*)"
)
_TYRE_STRICT = re.compile(_B + r"(?:шин(?!омонт)\w*|резин\w*|гум\w*|покришк\w*|покрышк\w*)")
#: Having them fitted. «поставлю» is the caller fitting them himself
#: («55 профіль, а я поставлю 60» — a size, wave 1-B).
_FIT = re.compile(
    _B + r"(?:постав(?:ити|ить|ите|те|ьте|или|ять|лять)|встанов\w*|установ(?:ит\w*|ите|ка|ку|ки)|"
    r"монтаж\w*|шиномонтаж\w*|перевзу\w*|переобу\w*|перебортир\w*|перебортув\w*)(?![\w'])"
)


def _norm(text: str) -> str:
    return (text or "").lower().replace("’", "'").replace("ʼ", "'").replace("`", "'")


def _military(low: str) -> bool:
    if _UBD.search(low) or _COMBAT.search(low) or _FOR_MILITARY.search(low):
        return True
    return bool(_MILITARY.search(low) and _DISCOUNT.search(low))


def _vat_invoice(low: str) -> bool:
    if _INVOICE.search(low) and _COMPANY.search(low):
        return True
    if not _VAT.search(low):
        return False
    if _INVOICE.search(low) or _COMPANY.search(low):
        return True
    # «з ПДВ» as a requirement; «ціна з ПДВ?» / «ПДВ включено?» is the price's.
    return bool(_WITH_VAT.search(low) and not _PRICE_QUESTION.search(low))


def _company_code(low: str) -> bool:
    return bool(_EDRPOU.search(low) or _COMPANY_CODE.search(low))


def _buys_tyres(low: str) -> bool:
    if _BUY.search(low) and _TYRE.search(low):
        return True
    return bool(_ORDER.search(low) and _TYRE_STRICT.search(low))


#: Fitting the tyres just offered: «ці шини / їх / одразу / заодно поставити».
_THESE_TYRES = re.compile(
    _B + r"(?:ці|цих|ці\s+ж|їх|эти|этих|их|одразу|відразу|зразу|сразу|заодно|разом|вместе)(?![\w'])"
)
#: The caller's own tyres — a booking, not an order: «шини свої», «на зберіганні».
_OWN_TYRES = re.compile(
    _B + r"(?:сво[їиюея]\w*|власн\w*|мо[їи]\s+(?:шин|резин|колес)\w*|зберіган\w*|хранени\w*)"
)


def _order_plus_fitting(low: str, *, searched: bool, in_fitting: bool) -> bool:
    buys = _buys_tyres(low) or bool(_BUY.search(low) and searched)
    fits = bool(_FIT.search(low))
    if buys and fits:
        return True
    # Across turns: tyres were already offered and now «а можна одразу і
    # поставити?», or a fitting booking is under way and now «а шини у вас
    # купити можна?». A fitting request after a search is NOT enough by
    # itself: «запишіть на перевзування, шини свої» is the bot's own booking
    # (wave 2-D acceptance: the bare ``fits and searched`` sent every caller
    # who had asked a price to the operator instead of the booking).
    refers_to_offer = bool(_THESE_TYRES.search(low)) and not _OWN_TYRES.search(low)
    return (fits and searched and refers_to_offer) or (buys and in_fitting)


def detect_operator_only_intent(
    text: str,
    policy: NetworkPolicy | None,
    *,
    searched: bool = False,
    in_fitting: bool = False,
) -> str | None:
    """The operator-only intent of one caller turn, or ``None``.

    ``searched`` — ``search_tires`` already ran in this call; ``in_fitting`` —
    a fitting booking is under way. Order-plus-fitting needs a network that
    fits tyres (``"fitting" in policy.services``) — elsewhere the refusal of
    the service is `network_facts`'. Sales off → ``None``.
    """
    if policy is None or not policy.sales_enabled:
        return None
    low = _norm(text)
    if not low.strip():
        return None
    if _military(low):
        return MILITARY
    if _company_code(low):
        return COMPANY_CODE
    if _vat_invoice(low):
        return VAT_INVOICE
    if "fitting" in policy.services and _order_plus_fitting(
        low, searched=searched, in_fitting=in_fitting
    ):
        return ORDER_PLUS_FITTING
    return None


def _history_tools(history: Iterable[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for msg in history:
        content = msg.get("content")
        if msg.get("role") != "assistant" or not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names.add(str(block.get("name") or ""))
    return names


def transfer_summary(intent: str, user_text: str, tire_query: dict[str, Any] | None) -> str:
    """What the operator reads: the request and what the call collected.

    Built only from the caller's words and the call's ``tire_query``
    (``session.tire_query`` / `merge_tire_query`) — nothing invented.
    """
    q = tire_query or {}
    parts = [f"Клієнт: {_LABELS[intent]}."]
    sizes = [str(s) for s in q.get("sizes") or [] if s]
    if sizes:
        size = ", ".join(sizes)
        if q.get("rear_size"):
            size += f" (задня вісь {q['rear_size']})"
        parts.append(f"Розмір: {size}.")
    elif q.get("diameter"):
        parts.append(f"Діаметр: R{q['diameter']}.")
    if q.get("season"):
        season = str(q["season"])
        parts.append(f"Сезон: {_SEASONS.get(season, season)}.")
    brands = [str(b) for b in q.get("brands") or [] if b]
    if brands:
        parts.append(f"Бренд/модель: {', '.join(brands)}.")
    if q.get("quantity"):
        parts.append(f"Кількість: {q['quantity']}.")
    words = " ".join((user_text or "").split())[:200]
    if words:
        parts.append(f"Слова клієнта: «{words}».")
    return " ".join(parts)


class OperatorOnlyGate:
    """Per call: the code's transfer of an operator-only request, at most once."""

    def __init__(self, policy: NetworkPolicy | None) -> None:
        self._policy = policy
        self._armed = bool(policy is not None and policy.sales_enabled)
        self._fired = False

    def plan(
        self,
        user_text: str,
        *,
        tools: Iterable[dict[str, Any]],
        history: Iterable[dict[str, Any]],
        tools_called: Iterable[str] | None,
        in_fitting: bool,
    ) -> str | None:
        """This turn's operator-only intent, or ``None``; arms the once-per-call stop."""
        if not self._armed or self._fired:
            return None
        if not any(t.get("name") == TRANSFER_TOOL for t in tools):
            return None
        searched = _SEARCH_TOOL in set(tools_called or ()) | _history_tools(history)
        intent = detect_operator_only_intent(
            user_text, self._policy, searched=searched, in_fitting=in_fitting
        )
        if intent is None:
            return None
        self._fired = True
        logger.info("Operator-only intent=%s text=%r", intent, (user_text or "")[:120])
        return intent

    @staticmethod
    def args_for(intent: str, user_text: str, tire_query: dict[str, Any] | None) -> dict[str, Any]:
        """The ``transfer_to_operator`` arguments of the code's transfer."""
        return {
            "reason": "complex_question",
            "summary": transfer_summary(intent, user_text, tire_query),
        }


def transferred(result: Any) -> bool:
    """The executor accepted the redirect (positive marker, not «did not raise»)."""
    return isinstance(result, dict) and result.get("status") == "transferring"
