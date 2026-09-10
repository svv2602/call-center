"""Wave 15: Krok 8 confirmation detector — customer approves the summary."""

import pytest

from src.agent.confirm_detect import (
    asked_for_confirmation,
    is_confirmation,
    is_yes_no_question,
)


@pytest.mark.parametrize(
    "text",
    [
        # Root case, call 7462c08b 2026-09-07 turn 43. The old whole-string
        # regex accepted one word only, so this missed and the bot claimed a
        # booking it never made.
        "так підтверджує",
        "так",
        "так, підтверджую",
        "да да",
        "ага давайте",
        "вірно",
        "правильно",
        "згодна",
        "ок",
        "підтверджую",
        "таки так",
        "добре, записуйте",
    ],
)
def test_accepts_pure_agreement(text):
    assert is_confirmation(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ні",
        "не так",
        "ні, не підтверджую",
        "так, але давайте на пʼятницю",
        "коричневий",
        "а скільки це коштує",
        "перепрошую не розчула",
    ],
)
def test_rejects_everything_else(text):
    assert not is_confirmation(text)


def test_negation_poisons_the_whole_utterance():
    """Every token must be affirmative — one «не» is enough to bail out."""
    assert not is_confirmation("не підтверджую")
    assert not is_confirmation("так ні")


def test_long_answers_carry_new_information():
    assert not is_confirmation("так добре давайте записуйте будь ласка на завтра")


def test_stt_person_endings_are_tolerated():
    """STT picks the wrong verb ending constantly — match the stem."""
    for variant in ("підтверджую", "підтверджує", "підтверджено", "подтверждаю"):
        assert is_confirmation(variant), variant


def test_detects_the_krok8_question():
    assert asked_for_confirmation(
        ["Наталя, перевіримо: 11 вересня о 11:00, коричневий Жигулі. Підтверджуєте?"]
    )


def test_detects_the_reask_wording():
    """Call 7462c08b turn 42 — the re-ask does not repeat «Підтверджуєте»."""
    reask = 'Перепрошую, не розчула. Скажіть, будь ласка, "так" щоб підтвердити або "ні" щоб змінити.'
    assert asked_for_confirmation([reask])


def test_reask_then_question_still_counts():
    """Newest-first: the filler re-ask must not hide the real question."""
    assert asked_for_confirmation(
        [
            "Перепрошую, не розчула.",
            "Наталя, перевіримо: 11 вересня о 11:00. Підтверджуєте?",
        ]
    )


@pytest.mark.parametrize(
    "utterances",
    [
        [],
        [""],
        ["На яку дату записуємо?"],
        ["Яка марка вашого авто?", "Назвіть, будь ласка, колір автомобіля."],
    ],
)
def test_no_open_question(utterances):
    assert not asked_for_confirmation(utterances)


class TestConfirmationOutsideKrok8:
    """`is_yes_no_question` — which of the bot's questions a bare «так» answers.

    Krok 8 is not the only place the bot invites a yes. It scatters
    confirmations through the whole checklist, and the FSM charges a failed
    answer to the state it happens to be *in*, so a cooperating caller loses an
    attempt for answering the question that was actually asked. Every «yes» case
    below is a phrasing taken off the 2026-09-10 prod log; every «no» case is a
    question the bot asks just as often where «так» really is a non-answer.
    """

    @pytest.mark.parametrize(
        "utterance",
        [
            # `fe1857ba` / `cf43d623` — asked while the FSM waited for station_id.
            "Записуємо туди?",
            "Гаразд, записуємо сюди?",
            # `b034315e` — asked while the FSM waited for a time.
            "Пропоную понеділок, чотирнадцяте вересня. Підходить?",
            "Ваш номер 0671234567, вірно?",
            "Правильно?",
            "Правильно розумію, вам потрібен монтаж на завтра?",
            "Ви ще на лінії?",
            "Ви маєте на увазі Zeekr?",
            # Krok 8 markers are included on purpose — same treatment.
            "Наталя, перевіримо: 11 вересня о 11:00. Підтверджуєте?",
            'Скажіть, будь ласка, "так" щоб підтвердити або "ні" щоб змінити.',
        ],
    )
    def test_a_bare_yes_answers_these(self, utterance):
        assert is_yes_no_question(utterance)

    @pytest.mark.parametrize(
        "utterance",
        [
            "",
            # Open questions — «так» answers none of them.
            "Яка марка вашого авто?",
            "На яку дату записуємо?",
            "У якому районі зручніше?",
            "Який час зручний?",
            "Назвіть, будь ласка, колір автомобіля.",
            "Як до вас звертатися?",
            # Two-option questions: the answer is A or B, and «так» is neither.
            "У вас легковий чи позашляховик?",
            "Шини привозите свої з собою чи ті, що у нас на зберіганні?",
        ],
    )
    def test_a_bare_yes_answers_none_of_these(self, utterance):
        assert not is_yes_no_question(utterance)

    def test_the_choice_veto_outranks_the_marker(self):
        """`fe1857ba` said «так» twice and only the first should be forgiven.

        The second came after the two-option storage question, which answers
        nothing and has to keep costing an attempt. The marker list already
        excludes that exact phrasing; the veto is what makes it hold when the
        LLM rephrases a *listed* question into a choice.
        """
        assert is_yes_no_question("Записуємо туди?")
        assert not is_yes_no_question("Записуємо туди чи пошукаємо інший пункт?")
        assert not is_yes_no_question("Підтверджуєте чи хочете змінити дату?")

    @pytest.mark.parametrize(
        "quoted",
        [
            "Скасувати? Скажіть «так» або «ні».",
            'Скасувати? Скажіть "так" або "ні".',
            "Скасувати? Скажіть так або ні.",
            "Скасувати? Скажіть ʼтакʼ або ʼніʼ.",
        ],
    )
    def test_a_question_that_spells_its_own_answers_out(self, quoted):
        """`4a687e9a` turn 6, the first live cancellation.

        The cancel sub-flow had no phrasing on the allow-list, so «так так»
        was charged to CITY. Adding «скасувати?» would have fixed this one call
        and broken the multi-booking case below, so the marker is the part of
        the sentence that is a rule rather than a phrasing.

        Parametrized over the quote glyphs because the LLM picks a different
        one from turn to turn, and a rule that depends on which one it picked
        is not a rule.
        """
        assert is_yes_no_question(quoted)

    def test_picking_which_booking_to_cancel_is_not_a_yes_no(self):
        """Same sub-flow, and «так» answers nothing in it.

        A caller with two bookings is asked which to cancel. That turn has to
        keep costing an attempt — this is why «скасувати?» is not the marker.
        """
        assert not is_yes_no_question("Який запис скасувати?")
        assert not is_yes_no_question("Скасувати запис на 14 вересня чи на 15?")

    def test_записуємо_alone_is_agreement(self):
        """`cf43d623` turn 12 — «записуємо» as the answer, not the question.

        It went in the word list, not the marker list: as an answer it is a
        plain yes, and «На яку дату записуємо?» is why the marker carries its
        object («записуємо туди») and this word does not.
        """
        assert is_confirmation("записуємо")
        assert is_confirmation("так, записуємо")
        assert not is_yes_no_question("На яку дату записуємо?")
