"""`bot_is_asking` — «asked and not answered», measured against real prod turns.

`max_parser_null` claims to count one thing: how many times the caller was put
this state's question and did not answer it. That reading only holds while the
bot is actually asking it, and the bot is driven by an LLM that runs price,
cancel and storage-contract sub-flows the FSM does not model. On every turn of
those the FSM sits in a main-flow state, reads the caller's cooperation as a
failed answer, and spends the budget on it.

Every row in `CHARGED_IN_PROD` below is a real `fsm_parser_null` charge from
the 2026-09-10 window after `fc05992`, paired with the bot utterance that
immediately preceded it. Five of the ten calls in that window were transferred
and all five were `fsm_parser_null` escalations; four of them are rows here
with `charge=False`, meaning no caller in them was stuck — they were
rescheduling, asking a price, or dictating a phone number for `find_storage`.

The rows with `charge=True` matter just as much. `30dd42fa` really was asked
«У якому місті…?» twice and really did not name a workable city, and a
predicate that excused those would not be measuring anything.

Two designs were falsified while building this and are kept as cases so they
cannot come back:

* **Question mark alone** (29/30). «За містом Ізюм точок шиномонтажу не
  знайшла. Назвіть, будь ласка, інше місто для запису.» is the CITY question
  in imperative form and carries no `?`. Hence `_ASK_FORMS`.
* **Plain substring matching** (28/30). «за яким могли *здати* шини» contains
  the DATE stem «дат», which made a phone-number request read as a date
  question. Hence the `\b` anchor on every marker.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from src.agent.fitting_fsm import STATES, FsmState, bot_is_asking

# --- The prod corpus -------------------------------------------------------
# (call, state, bot utterance immediately before the charge, caller text,
#  should this turn be charged to `max_parser_null`?)
CHARGED_IN_PROD: list[tuple[str, FsmState, str, str, bool]] = [
    # bba035ff — a reschedule. The FSM never left CITY; all three charges wrong.
    (
        "bba035ff",
        FsmState.CITY,
        "Знайшла запис у Харкові на 11 вересня о 15:40, на вулиці Холодногірська, 11. "
        "Скасовуємо для перенесення?",
        "перенесли",
        False,
    ),
    (
        "bba035ff",
        FsmState.CITY,
        "Знайшла запис у Харкові на 11 вересня о 15:40, на вулиці Холодногірська, 11. "
        "Скасовуємо для перенесення?",
        "переносимо",
        False,
    ),
    (
        "bba035ff",
        FsmState.CITY,
        "Вільний час на вівторок 15 вересня: 9:00, 10:20, 11:40, 13:00, 14:20, 15:40. "
        "Який час зручний?",
        "900",
        False,
    ),
    # 39469f9f — a price question. The FSM held STATION throughout.
    (
        "39469f9f",
        FsmState.STATION,
        "Шиномонтаж R19 у місті Дніпро: легкові — 474 грн, позашляховики — 528 грн. "
        "Повертаємось до вибору точки шиномонтажу.",
        "так в Черкасах",
        False,
    ),
    (
        "39469f9f",
        FsmState.STATION,
        "Ви хочете дізнатися вартість шиномонтажу у Черкасах?",
        "так",
        False,
    ),
    ("39469f9f", FsmState.STATION, "Який діаметр коліс у вас?", "19", False),
    # 380a280d — the same shape, and the reason «вулиц» is not a STATION marker:
    # every price quote prints a street.
    ("380a280d", FsmState.STATION, "Який діаметр коліс у вас?", "12", False),
    (
        "380a280d",
        FsmState.STATION,
        "Комплексний шиномонтаж R21-22 у Харкові, на вулиці Холодногірській, 11, "
        "коштує 534 гривні за колесо. Записуємо на монтаж?",
        "а легкова часу",
        False,
    ),
    (
        "380a280d",
        FsmState.STATION,
        "Я на зв'язку. Якщо маєте запитання — я слухаю.",
        "є запитання",
        False,
    ),
    # 30dd42fa — these two CITY charges were correct. The bot asked, twice.
    (
        "30dd42fa",
        FsmState.CITY,
        "Перепрошую, Віталію! У якому місті вам зручніше записатися на шиномонтаж?",
        "места изюм",
        True,
    ),
    (
        "30dd42fa",
        FsmState.CITY,
        "За містом Ізюм точок шиномонтажу не знайшла. Назвіть, будь ласка, інше місто для запису.",
        "арки",
        True,
    ),
    # …and so was this one. `storage_choice_parser`'s blindness to the
    # accusative «на зберігання» is a separate defect and not this predicate's
    # to excuse.
    (
        "30dd42fa",
        FsmState.STORAGE,
        "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
        "у вас на зберігання",
        True,
    ),
    # 30dd42fa — the `find_storage` phone sub-flow, charged to DATE. «здати»
    # is why the markers are word-anchored.
    (
        "30dd42fa",
        FsmState.DATE,
        "Не знайшла ваш договір за цим номером. Продиктуйте, будь ласка, номер "
        "телефону, за яким могли здати шини — український мобільний, 10 цифр.",
        "936 52 18",
        False,
    ),
    (
        "30dd42fa",
        FsmState.DATE,
        "Не знайшла ваш договір за цим номером. Можливо, договір оформлено на інший "
        "номер? Назвіть, будь ласка, інший телефон або скажіть, що не пам'ятаєте номер.",
        "я номер",
        False,
    ),
    (
        "30dd42fa",
        FsmState.DATE,
        "Будь ласка, продиктуйте номер телефону, за яким могли здати шини — "
        "український мобільний, 10 цифр.",
        "095 9362 18",
        False,
    ),
    # a83655c5 — the one call in the window where the caller answered the real
    # question. Only the middle charge is legitimate, and closing it took the
    # bare-day fix in `date_parser`, not this predicate.
    (
        "a83655c5",
        FsmState.STORAGE,
        "Знайшла на вулиці Героїв Дніпра, 7 у Черкасах. Записуємо туди?",
        "еще",
        False,
    ),
    (
        "a83655c5",
        FsmState.DATE,
        "Правильно розумію: потрібно, щоб ми доставили ваші шини зі зберігання?",
        "и я привезу с собою",
        False,
    ),
    ("a83655c5", FsmState.DATE, "На яку дату записуємо?", "на 11", True),
    (
        "a83655c5",
        FsmState.DATE,
        "Ви ще на лінії?",
        "Я хочу выйти из надписью запись успешный",
        False,
    ),
]

# --- Positive controls -----------------------------------------------------
# Built from the canonical `question_template` / `silence_reprompt` /
# `fallback_question` strings and from real prod phrasings of them. If the
# predicate stops recognising these, the budget stops working entirely and no
# stuck caller is ever transferred.
CANONICAL_QUESTIONS: list[tuple[FsmState, str]] = [
    (FsmState.CITY, "У якому місті вам зручніше?"),
    (
        FsmState.STATION,
        "У Харкові є точка шиномонтажу біля Холодної Гори, на вулиці "
        "Холодногірська, 11. Записуємо туди?",
    ),
    (FsmState.STATION, "У Дніпрі є 4 точок у районах: Центр, Лівий берег. У якому вам зручніше?"),
    # The single-point form, which names a street and nothing else. Excluding
    # «вулиц» — right, because every price quote prints one — took 12 of the 69
    # real station questions in the last 10 prod days with it, and this shape
    # is the bulk of them.
    (FsmState.STATION, "Знайшла на вул. Холодногірська, 11 у Харкові. Записуємо туди?"),
    (FsmState.STATION, "на вулиці Героїв Дніпра, сім. Записуємо туди?"),
    # The LLM's own wording for the time question — 5 of 31 in the same window,
    # and it carries none of «час»/«годин»/«вільн»/«слот».
    (FsmState.TIME, "О котрій зручніше?"),
    (FsmState.STORAGE, "Шини привозите свої з собою чи ті, що у нас на зберіганні?"),
    (FsmState.DATE, "На яку дату записуємо?"),
    (FsmState.TIME, "На 15 вересня вільно: 9:00, 10:20. Який час зручніше?"),
    (FsmState.COLOR, "Назвіть, будь ласка, колір автомобіля."),
    (FsmState.COLOR, "Який колір автомобіля?"),
    (FsmState.BRAND, "Яка марка вашого авто?"),
    (
        FsmState.BRAND,
        "Уточніть, будь ласка, тип автомобіля: легкове, позашляховик (SUV), "
        "мікроавтобус чи вантажне?",
    ),
    (
        FsmState.CONFIRM,
        "Олена, перевіримо: 15 вересня о 14:20, Холодногірська 11, білий VW. Підтверджуєте?",
    ),
]


class TestProdCorpus:
    @pytest.mark.parametrize(
        "call,state,bot,customer,charge",
        CHARGED_IN_PROD,
        ids=[f"{c}-{s.value}-{t[:14]}" for c, s, _, t, _ in CHARGED_IN_PROD],
    )
    def test_charge_decision_matches_what_the_bot_asked(
        self, call: str, state: FsmState, bot: str, customer: str, charge: bool
    ) -> None:
        assert bot_is_asking(state, bot) is charge

    def test_four_of_the_five_transfers_would_not_have_been_charged_at_all(self) -> None:
        """The claim the fix is being shipped on, asserted per call.

        Every state escalates at `max_parser_null` consecutive charges. For
        these four calls the predicate must leave *zero*, not merely fewer —
        one surviving charge per call still walks the budget down at the same
        rate, just slower.
        """
        stuck_calls = ("bba035ff", "39469f9f", "380a280d")
        charged = {
            call: sum(
                bot_is_asking(state, bot) for c, state, bot, _, _ in CHARGED_IN_PROD if c == call
            )
            for call in stuck_calls
        }
        assert charged == dict.fromkeys(stuck_calls, 0)

    def test_the_phone_subflow_spends_nothing_from_the_date_budget(self) -> None:
        """`30dd42fa` separately: its CITY and STORAGE charges must survive."""
        rows = [r for r in CHARGED_IN_PROD if r[0] == "30dd42fa"]
        by_state: dict[FsmState, int] = {}
        for _, state, bot, _, _ in rows:
            by_state[state] = by_state.get(state, 0) + bot_is_asking(state, bot)
        assert by_state == {FsmState.CITY: 2, FsmState.STORAGE: 1, FsmState.DATE: 0}


class TestCanonicalQuestions:
    @pytest.mark.parametrize(
        "state,question",
        CANONICAL_QUESTIONS,
        ids=[f"{s.value}-{q[:20]}" for s, q in CANONICAL_QUESTIONS],
    )
    def test_the_states_own_question_is_always_recognised(
        self, state: FsmState, question: str
    ) -> None:
        assert bot_is_asking(state, question) is True

    @pytest.mark.parametrize(
        "state,question",
        CANONICAL_QUESTIONS,
        ids=[f"{s.value}-{q[:20]}" for s, q in CANONICAL_QUESTIONS],
    )
    def test_no_other_state_claims_it(self, state: FsmState, question: str) -> None:
        """Exactly one state may recognise any given question. Measured, not assumed.

        Cross-talk is how this fix would fail quietly: a marker that also
        matches a neighbour's question keeps charging the budget in the state
        the caller is *not* in, which is the defect being removed. The
        overlap across all eleven canonical questions is currently zero, so
        the assertion is exact — a fuzzy bound here would have hidden the
        thing it was written to catch.

        This is what keeps «вулиц» out of STATION (every price quote prints a
        street) and the city names out of CITY (booking read-backs name one).
        """
        others = [
            s.value
            for s in FsmState
            if s is not state and STATES[s].question_markers and bot_is_asking(s, question)
        ]
        assert others == []


def chargeable_states() -> list[FsmState]:
    """States where `max_parser_null` can be spent, derived the way the seam does.

    `src/core/pipeline.py:1227-1231`: the whole parser_null block sits behind
    `if own_field:`, and `own_field` is the state's own `field_name` or, when
    it has none, its successor's. Deriving it here rather than listing it by
    hand is the point — a state added to the flow shows up in this list
    without anyone remembering to update a test.
    """
    out = []
    for state in FsmState:
        cfg = STATES[state]
        own_field = cfg.field_name
        if own_field is None and cfg.next_state is not None:
            own_field = STATES[cfg.next_state].field_name
        if own_field:
            out.append(state)
    return out


class TestMarkerTableCompleteness:
    #: Chargeable, and knowingly left without markers, so `bot_is_asking`
    #: returns its `True` default and their budgets behave exactly as before.
    #: WELCOME and INTENT both borrow INTENT's «intent» field and are charged
    #: on the opening turns of every call — that is the same ground the
    #: unfixed NAME gap sits on and it is not this fix's to move. The two
    #: interrupt states are only ever entered *because* the bot asked their
    #: question, so there is nothing for the predicate to add.
    KNOWINGLY_UNMARKED: ClassVar[set[FsmState]] = {
        FsmState.WELCOME,
        FsmState.INTENT,
        FsmState.PRICE_INTERRUPT,
        FsmState.CANCEL_INTERRUPT,
    }

    def test_every_main_flow_state_has_question_markers(self) -> None:
        """The default is «True», so a new state without markers is never excused.

        Right for a state with no question to recognise, and a silent
        restoration of the old behaviour on any state that does have one —
        the hardest kind of regression to see in prod, because it shows up as
        a transfer on a call that looks ordinary.
        """
        missing = {s for s in chargeable_states() if not STATES[s].question_markers}
        assert missing == self.KNOWINGLY_UNMARKED

    def test_every_marked_state_is_one_that_can_be_charged(self) -> None:
        """The other direction: markers on a state that never spends the budget
        are dead weight that reads as protection."""
        marked = {s for s in FsmState if STATES[s].question_markers}
        assert marked <= set(chargeable_states())

    def test_markers_are_lowercase(self) -> None:
        """`bot_is_asking` lowercases the utterance and not the markers."""
        shouty = {
            state.value: [m for m in cfg.question_markers if m != m.lower()]
            for state, cfg in STATES.items()
            if any(m != m.lower() for m in cfg.question_markers)
        }
        assert shouty == {}


class TestShape:
    def test_a_state_without_markers_is_always_asking(self) -> None:
        """The side-states. The claim is «we recognise this question», and
        where there is nothing to recognise with, the budget must not change."""
        unmarked = next(s for s in FsmState if not STATES[s].question_markers)
        assert bot_is_asking(unmarked, "") is True
        assert bot_is_asking(unmarked, "будь-що") is True

    def test_no_bot_utterance_is_not_a_question(self) -> None:
        """First turn of a call, or a history the walk-back could not read.

        `False` and not `True`: with no evidence the bot asked, a charge is
        not something we can justify.
        """
        assert bot_is_asking(FsmState.CITY, "") is False

    def test_a_statement_containing_the_marker_is_not_a_question(self) -> None:
        """`bba035ff` in one line — the read-back that cost it the call."""
        assert (
            bot_is_asking(
                FsmState.CITY,
                "Знайшла запис у місті Харків на 11 вересня о 15:40.",
            )
            is False
        )

    def test_an_imperative_without_a_question_mark_still_counts(self) -> None:
        assert bot_is_asking(FsmState.CITY, "Назвіть, будь ласка, інше місто для запису.") is True


class TestPhrasesThatMustNotBeMistakenForAQuestion:
    """The near-misses that decided the marker table, measured against prod.

    Each of these shares a word with a state's question and is not that
    question. They are the reason «вулиц» is not a STATION marker and
    «записуємо» is only a marker as part of «записуємо туди» — a table built
    from the templates alone matches all three of these.
    """

    @pytest.mark.parametrize(
        "state,utterance",
        [
            # Closes a price quote. `380a280d` was escalated on this exact turn.
            (FsmState.STATION, "Це ціна за одне колесо. Записуємо на монтаж?"),
            # The CONFIRM read-back — same verb, different state.
            (
                FsmState.STATION,
                "Записуємо на 12 вересня о 11:00 на вул. Княгині Ольги, 24 А "
                "в Дніпрі, чорний Volkswagen?",
            ),
            # A price quote that names a street.
            (
                FsmState.STATION,
                "Комплексний шиномонтаж R21-22 у Харкові, на вулиці "
                "Холодногірській, 11, коштує 534 гривні за колесо.",
            ),
            # Names a city in a booking read-back. `bba035ff`, three turns.
            (
                FsmState.CITY,
                "Знайшла запис у Харкові на 11 вересня о 15:40, на вулиці "
                "Холодногірська, 11. Скасовуємо для перенесення?",
            ),
            # «здати» contains «дат». The phone sub-flow of `30dd42fa`.
            (
                FsmState.DATE,
                "Продиктуйте, будь ласка, номер телефону, за яким могли здати "
                "шини — український мобільний, 10 цифр.",
            ),
        ],
    )
    def test_a_word_in_common_is_not_a_question(self, state: FsmState, utterance: str) -> None:
        assert bot_is_asking(state, utterance) is False

    def test_the_resume_phrase_is_not_a_question(self) -> None:
        """«Повертаємось до вибору точки шиномонтажу.» carries «точк» and is a
        statement. `39469f9f` was charged for it — the ask-form check, not the
        marker, is what refuses it."""
        assert bot_is_asking(FsmState.STATION, STATES[FsmState.STATION].resume_phrase) is False
