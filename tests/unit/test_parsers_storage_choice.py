"""`storage_choice_parser` — two lists of markers, and why they must not merge.

The wide legacy list («тобою», «за собою», «не маю») feeds an LLM nudge: a
false positive there is cheap, because the LLM asks anyway. The narrow
self-evident list is grounds to **skip a state**: a false positive there means
the Krok 2 question was silently never asked. Merging them brings back «STORAGE
skipped because the caller said „тобою“».

The separation is expressed as confidence against the single threshold (§3.6):

    self-evident own / contract          1.0   may skip STORAGE
    wide list, bot did ask Krok 2        0.9   applies inside STORAGE only
    wide list, bot did not ask           0.5   unresolved — nudge, skips nothing

`TestTwoLevelsDoNotCollapse` is the mutation target named by the spec: a test
that stays green when the wide list is graded like the narrow one proves
nothing at all.

`TestLegacyEquivalence` compares the two copies that are alive in the tree
right now — `src/core/pipeline.py` and this module — on a corpus built from
their own marker lists plus the phrases quoted in their comments.
**`pytest.importorskip` is banned here on purpose:** when Wave 6-B deletes the
`pipeline.py` copy, this file has to fail with a readable message rather than
skip and lose the coverage without anyone noticing.

Corpus provenance
-----------------
Every phrase below is lifted from a comment in `pipeline.py` / the parser, with
the call it came off:

* «тобою», «з тобою», «шины будут любую» — call 2026-08-03 14:56;
* «за собою», «за собой», «приложишь с собой» — calls 2026-09-03 10:37/10:38
  (Kusaeva);
* «нема сина зберіга» — call 2026-08-03, the mangled «немає жодного зберігання»
  that drove a repeat loop.
"""

from __future__ import annotations

import pytest

from src.agent.parsers import ParseContext
from src.agent.parsers.base import APPLY_THRESHOLD
from src.agent.parsers.storage_choice_parser import (
    _ASKED_CONFIDENCE,
    _SELF_EVIDENT_CONFIDENCE,
    _UNASKED_CONFIDENCE,
    PARSER,
)

#: The Krok 2 question as the bot actually phrases it — it carries two of the
#: three markers in `_STORAGE_ASKING_MARKERS`.
STORAGE_QUESTION = "Шини привозите свої, чи у вас зберігання у нас?"


def ctx(text: str, *, bot: str = "") -> ParseContext:
    return ParseContext(customer_text=text, last_bot_utterance=bot)


class TestSelfEvident:
    """Narrow list — enough on its own to skip the state."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("з собою", "own"),
            ("привезу з собою", "own"),
            ("свої шини", "own"),
            ("власні шини", "own"),
            ("свій комплект", "own"),
            ("с собой", "own"),
            ("на зберіганні", "contract"),
            ("зі зберігання", "contract"),
            ("у вас на зберіганні", "contract"),
            ("зі складу", "contract"),
        ],
    )
    def test_value_at_full_confidence(self, text: str, expected: str) -> None:
        outcome = PARSER.parse(ctx(text))
        assert outcome.status == "value"
        assert outcome.value == expected
        assert outcome.confidence == _SELF_EVIDENT_CONFIDENCE

    def test_no_bot_question_is_needed(self) -> None:
        """Context-free by definition — that is what «self-evident» means."""
        assert PARSER.parse(ctx("з собою")).confidence == _SELF_EVIDENT_CONFIDENCE
        assert (
            PARSER.parse(ctx("з собою", bot=STORAGE_QUESTION)).confidence
            == _SELF_EVIDENT_CONFIDENCE
        )


class TestTwoLevelsDoNotCollapse:
    """The mutation target (spec §3.9). «тобою» is wide-only.

    It is in `_STORAGE_OWN_HINTS` and **not** in `_STORAGE_SELF_EVIDENT_OWN`,
    so as a nudge it passes and as grounds to skip STORAGE it must not.
    """

    def test_wide_only_phrase_without_the_question_is_unresolved(self) -> None:
        outcome = PARSER.parse(ctx("тобою"))
        assert outcome.value == "own"
        assert outcome.confidence == _UNASKED_CONFIDENCE
        assert outcome.status == "unresolved"

    def test_wide_only_phrase_with_the_question_applies_but_is_not_self_evident(
        self,
    ) -> None:
        outcome = PARSER.parse(ctx("тобою", bot=STORAGE_QUESTION))
        assert outcome.value == "own"
        assert outcome.confidence == _ASKED_CONFIDENCE
        assert outcome.status == "value"
        assert outcome.confidence < _SELF_EVIDENT_CONFIDENCE, (
            "the wide list must never reach the level that skips a state"
        )

    def test_the_three_levels_are_strictly_ordered(self) -> None:
        assert _UNASKED_CONFIDENCE < APPLY_THRESHOLD <= _ASKED_CONFIDENCE
        assert _ASKED_CONFIDENCE < _SELF_EVIDENT_CONFIDENCE

    def test_the_gap_is_visible_on_real_utterances(self) -> None:
        """Same three phrases, three different consequences."""
        wide_unasked = PARSER.parse(ctx("тобою"))
        wide_asked = PARSER.parse(ctx("тобою", bot=STORAGE_QUESTION))
        narrow = PARSER.parse(ctx("з собою"))

        assert wide_unasked.confidence < wide_asked.confidence < narrow.confidence
        assert {wide_unasked.status, wide_asked.status, narrow.status} == {
            "unresolved",
            "value",
        }

    #: In `_STORAGE_OWN_HINTS` and carrying no `_STORAGE_SELF_EVIDENT_OWN`
    #: substring — the phrases the split exists for. Verified as wide-only by
    #: `test_the_sample_really_is_wide_only` below, so the list cannot silently
    #: acquire a self-evident phrase and start passing for the wrong reason.
    WIDE_ONLY = (
        "тобою",  # call 2026-08-03 14:56
        "з тобою",
        "шины будут любую",  # STT for «шини будуть з собою»
        "мої шини",
        "везу свої",
        "нема зберігання",
        "не здавав на зберігання",
        "нема сина зберіга",  # call 2026-08-03
    )

    def test_the_sample_really_is_wide_only(self) -> None:
        from src.agent.parsers import storage_choice_parser as moved

        for phrase in self.WIDE_ONLY:
            assert any(h in phrase for h in moved._STORAGE_OWN_HINTS), phrase
            assert not any(h in phrase for h in moved._STORAGE_SELF_EVIDENT_OWN), phrase

    @pytest.mark.parametrize("phrase", WIDE_ONLY)
    def test_wide_only_phrases_never_reach_the_self_evident_level(self, phrase: str) -> None:
        for bot in ("", STORAGE_QUESTION):
            outcome = PARSER.parse(ctx(phrase, bot=bot))
            assert outcome.confidence < _SELF_EVIDENT_CONFIDENCE, (phrase, bot)


class TestSttMangles:
    """Real STT output, quoted verbatim from the comments."""

    @pytest.mark.parametrize(
        "text",
        [
            "шины будут любую",  # «шини будуть з собою», call 2026-08-03 14:56
            "тобою",
            "з тобою",
            "будут с собой",
        ],
    )
    def test_wide_list_mangles_are_recognised_as_own(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text, bot=STORAGE_QUESTION))
        assert outcome.value == "own"

    @pytest.mark.parametrize(
        "text",
        [
            "приложишь с собой",  # call 2026-09-03 10:38
            "за собою",  # calls 10:37 / 10:38, Kusaeva
            "прикладу з собою",
        ],
    )
    def test_mangles_that_also_carry_a_self_evident_substring(self, text: str) -> None:
        """«приложишь с собой» contains «с собой» — self-evident by construction."""
        outcome = PARSER.parse(ctx(text))
        assert outcome.value == "own"


class TestContextScopedDenials:
    """«не маю» / «в мене нема» are own **only** right after Krok 2."""

    @pytest.mark.parametrize(
        "text",
        ["не маю", "в мене нема", "у мене немає", "нема у мене"],
    )
    def test_only_counts_when_the_bot_asked(self, text: str) -> None:
        assert PARSER.parse(ctx(text)).status == "not_mentioned"

        asked = PARSER.parse(ctx(text, bot=STORAGE_QUESTION))
        assert asked.status == "value"
        assert asked.value == "own"
        assert asked.confidence == _ASKED_CONFIDENCE

    def test_an_unrelated_bot_turn_does_not_open_the_scope(self) -> None:
        outcome = PARSER.parse(ctx("не маю", bot="Яка марка вашого авто?"))
        assert outcome.status == "not_mentioned"

    @pytest.mark.parametrize(
        "bot,opens",
        [
            ("Шини привозите свої, чи у вас зберігання у нас?", True),
            ("Чи є у вас зберігання?", True),
            ("Ви привозите свої шини?", True),
            ("Шини з собою чи у нас?", True),
            ("У якому місті вам зручно?", False),
            ("", False),
        ],
    )
    def test_the_asking_markers_are_what_opens_it(self, bot: str, opens: bool) -> None:
        outcome = PARSER.parse(ctx("не маю", bot=bot))
        assert (outcome.status == "value") is opens


class TestAmbiguityIsNotACoinFlip:
    @pytest.mark.parametrize(
        "text",
        [
            "свої привезу, чи те що у вас на зберіганні?",
            "з собою чи зі складу?",
            "тобою або на зберіганні",
        ],
    )
    def test_both_readings_yield_no_value(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text, bot=STORAGE_QUESTION))
        assert outcome.status == "unresolved"
        assert outcome.value is None

    def test_the_wide_list_also_counts_towards_ambiguity(self) -> None:
        """§3.6 extends the ambiguity rule from the narrow lists to the wide one."""
        outcome = PARSER.parse(ctx("тобою або на зберіганні"))
        assert outcome.value is None


class TestNotMentioned:
    @pytest.mark.parametrize("text", ["", "   ", "білий Nissan", "завтра о 14:00", "Оболонь"])
    def test_nothing_about_storage(self, text: str) -> None:
        outcome = PARSER.parse(ctx(text, bot=STORAGE_QUESTION))
        assert outcome.status == "not_mentioned"
        assert outcome.value is None


class TestLegacyEquivalence:
    """Two copies are alive in the tree. They must behave identically.

    `pytest.importorskip` is deliberately not used: when Wave 6-B removes the
    `pipeline.py` copy, these tests must fail with the message below rather
    than skip. A skipped equivalence test is a silently lost guarantee.
    """

    @staticmethod
    def _legacy():
        from src.core import pipeline

        assert hasattr(pipeline, "detect_own_tires"), (
            "src/core/pipeline.py no longer exports detect_own_tires. If Wave 6-B "
            "removed the legacy copy on purpose, delete TestLegacyEquivalence in "
            "the same commit that proves the nudge stayed identical with "
            "FSM_ENABLED=false — do not silence this by skipping."
        )
        return pipeline

    @staticmethod
    def _corpus() -> list[str]:
        """Every marker from both copies, plus the quoted STT mangles."""
        from src.agent.parsers import storage_choice_parser as moved

        phrases: list[str] = []
        for group in (
            moved._STORAGE_OWN_HINTS,
            moved._STORAGE_OWN_HINTS_WHEN_ASKED,
            moved._STORAGE_SELF_EVIDENT_OWN,
            moved._STORAGE_SELF_EVIDENT_CONTRACT,
            moved._STORAGE_ASKING_MARKERS,
        ):
            phrases.extend(group)

        phrases.extend(
            [
                # Quoted in the comments, with the calls they came off.
                "шины будут любую",
                "приложишь с собой",
                "в мене нема сина зберігає",
                "Шини будуть з собою",
                "ТОБОЮ",
                "Свої привезу, чи те що у вас на зберіганні?",
                # Negative controls.
                "",
                "   ",
                "білий Nissan",
                "завтра о 14:00",
                "Оболонь",
            ]
        )
        return phrases

    def test_the_five_marker_lists_are_identical(self) -> None:
        from src.agent.parsers import storage_choice_parser as moved

        legacy = self._legacy()
        for name in (
            "_STORAGE_OWN_HINTS",
            "_STORAGE_OWN_HINTS_WHEN_ASKED",
            "_STORAGE_ASKING_MARKERS",
            "_STORAGE_SELF_EVIDENT_OWN",
            "_STORAGE_SELF_EVIDENT_CONTRACT",
        ):
            assert getattr(legacy, name) == getattr(moved, name), name

    def test_detect_own_tires_agrees_on_the_whole_corpus(self) -> None:
        from src.agent.parsers import storage_choice_parser as moved

        legacy = self._legacy()
        for phrase in self._corpus():
            for asking in (True, False):
                assert legacy.detect_own_tires(
                    phrase, asking_storage=asking
                ) == moved.detect_own_tires(phrase, asking_storage=asking), (
                    phrase,
                    asking,
                )

    def test_detect_storage_choice_agrees_on_the_whole_corpus(self) -> None:
        from src.agent.parsers import storage_choice_parser as moved

        legacy = self._legacy()
        for phrase in self._corpus():
            assert legacy.detect_storage_choice(phrase) == moved.detect_storage_choice(phrase), (
                phrase
            )

    def test_bot_is_asking_storage_agrees_on_the_whole_corpus(self) -> None:
        from src.agent.parsers import storage_choice_parser as moved

        legacy = self._legacy()
        utterances = [
            *self._corpus(),
            STORAGE_QUESTION,
            "Чи є у вас зберігання?",
            "Ви привозите свої шини?",
            "Шини з собою чи у нас?",
            "У якому місті вам зручно?",
        ]
        for utterance in utterances:
            assert legacy._bot_is_asking_storage(utterance) == moved._bot_is_asking_storage(
                utterance
            ), utterance

    def test_the_corpus_actually_exercises_both_answers(self) -> None:
        """A corpus on which everything returns False proves nothing."""
        from src.agent.parsers import storage_choice_parser as moved

        results = {moved.detect_own_tires(p, asking_storage=True) for p in self._corpus()}
        assert results == {True, False}
        choices = {moved.detect_storage_choice(p) for p in self._corpus()}
        assert choices == {"own", "contract", None}


class TestContract:
    def test_registry_identity(self) -> None:
        assert PARSER.name == "storage_choice_parser"
        assert PARSER.field_name == "storage_choice"

    def test_no_aresolve(self) -> None:
        assert PARSER.aresolve is None

    def test_all_three_statuses_are_reachable(self) -> None:
        statuses = {
            PARSER.parse(ctx(text, bot=bot)).status
            for text, bot in (
                ("з собою", ""),
                ("тобою", ""),
                ("білий Nissan", ""),
            )
        }
        assert statuses == {"value", "unresolved", "not_mentioned"}
