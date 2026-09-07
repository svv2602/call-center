"""Wave 15: Krok 8 confirmation detector — customer approves the summary."""

import pytest

from src.agent.confirm_detect import asked_for_confirmation, is_confirmation


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
