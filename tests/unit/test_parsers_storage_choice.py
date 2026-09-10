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

`TestLegacyNudgeUnchanged` is what Wave 6-B left in place of Wave 6-A's
`TestLegacyEquivalence`. There is no second copy left to compare against —
`src/core/pipeline.py` now imports from this module — so the guarantee is
carried by a **frozen golden**: the same corpus, with the answers captured off
the old `pipeline.py` implementation before it was deleted. A golden
recomputed from the code under test would be a tautology; these literals are
not.

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
    _STORAGE_OWN_HINTS,
    _STORAGE_OWN_HINTS_WHEN_ASKED,
    _UNASKED_CONFIDENCE,
    PARSER,
    _bot_is_asking_storage,
    detect_own_tires,
    detect_storage_choice,
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
        "с тобой",  # call 4b6c4653, 2026-09-10 — same mangle, Russian spelling
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

    @pytest.mark.parametrize("text", ["с тобой", "тобой"])
    def test_the_russian_spelling_of_the_same_mangle(self, text: str) -> None:
        """Call 4b6c4653, 2026-09-10 — the inversion this list exists to prevent.

        Only the Ukrainian «-ою» spellings were listed, so «с тобой» matched
        nothing, `parse` returned NOT_MENTIONED, and the LLM asked «потрібно, щоб
        ми доставили ваші шини зі зберігання?» — the opposite of what the caller
        had just said. The call then died at STORAGE with no booking. A silent
        NOT_MENTIONED is what makes this shape dangerous rather than merely
        unhelpful, so pin the value, not just «not None».
        """
        outcome = PARSER.parse(ctx(text, bot=STORAGE_QUESTION))
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


class TestLegacyNudgeUnchanged:
    """Wave 6-B: `TestLegacyEquivalence` is gone, and this is what replaced it.

    The old class compared `src/core/pipeline.py`'s copy of the detector with
    this module's. Wave 6-B deleted that copy and repointed the pipeline here,
    so there is nothing left to compare — and its own `assert` message said to
    delete it in the same commit rather than skip it.

    What must not be lost with it is the guarantee it stood for: with
    `FSM_ENABLED=false` the storage nudge behaves **exactly** as before. So the
    corpus stays and the expected answers are frozen as literals, captured from
    `pipeline.detect_own_tires` *before* the move. A golden recomputed from the
    code under test proves nothing; these numbers came from the old
    implementation and are now the contract.

    The phrases are the STT mangles quoted in the marker lists with the calls
    they came off (2026-08-03 14:55/14:56, 2026-09-03 10:37/10:38), plus the
    negative controls.
    """

    #: phrase → (own when the bot just asked, own when it did not, choice)
    GOLDEN: tuple[tuple[str, bool, bool, str | None], ...] = (
        ("шины будут любую", True, True, None),
        ("приложишь с собой", True, True, "own"),
        ("в мене нема сина зберігає", True, True, None),
        ("Шини будуть з собою", True, True, "own"),
        ("ТОБОЮ", True, True, None),
        ("за собою", True, True, None),
        ("с собою", True, True, None),
        ("Свої привезу, чи те що у вас на зберіганні?", True, True, "contract"),
        ("привезу з собою", True, True, "own"),
        ("везу свої", True, True, None),
        ("свій комплект", True, True, "own"),
        ("мої шини", True, True, None),
        ("нема зберігання", True, True, None),
        ("ніколи не здавав", True, True, None),
        ("не здавали на зберігання", True, True, None),
        # Context-scoped: these four are «own» only right after the question.
        ("в мене нема", True, False, None),
        ("у мене немає", True, False, None),
        ("не маю", True, False, None),
        ("нема у мене", True, False, None),
        ("зі зберігання", False, False, "contract"),
        ("у вас на зберіганні", False, False, "contract"),
        ("зі складу", False, False, "contract"),
        ("на зберіганні", False, False, "contract"),
        ("", False, False, None),
        ("   ", False, False, None),
        ("білий Nissan", False, False, None),
        ("завтра о 14:00", False, False, None),
        ("Оболонь", False, False, None),
        ("хочу записатися", False, False, None),
    )

    #: bot utterance → does it count as the Krok 2 storage question?
    ASKING_GOLDEN: tuple[tuple[str, bool], ...] = (
        ("Шини привозите з собою чи вони у нас на зберіганні?", True),
        ("Чи є у вас зберігання?", True),
        ("Ви привозите свої шини?", True),
        ("Шини з собою чи у нас?", True),
        ("Зберігання у вас є?", True),
        ("У якому місті вам зручно?", False),
        ("Який колір вашого авто?", False),
    )

    def test_the_pipeline_no_longer_carries_its_own_copy(self) -> None:
        from src.core import pipeline

        for name in (
            "_STORAGE_OWN_HINTS",
            "_STORAGE_OWN_HINTS_WHEN_ASKED",
            "_STORAGE_ASKING_MARKERS",
            "_STORAGE_SELF_EVIDENT_OWN",
            "_STORAGE_SELF_EVIDENT_CONTRACT",
        ):
            assert name not in vars(pipeline), (
                f"{name} is back in pipeline.py — two copies drift, that is the "
                "whole reason Wave 6-A wrote an equivalence test"
            )

    def test_the_pipeline_calls_this_module(self) -> None:
        from src.core import pipeline

        # Identity, not equality: a re-implementation that merely agrees today
        # is exactly what the move was meant to make impossible.
        assert pipeline.detect_own_tires is detect_own_tires
        assert pipeline.detect_storage_choice is detect_storage_choice
        assert pipeline._bot_is_asking_storage is _bot_is_asking_storage

    @pytest.mark.parametrize(("phrase", "asked", "unasked", "choice"), GOLDEN)
    def test_the_nudge_answers_exactly_as_before(
        self, phrase: str, asked: bool, unasked: bool, choice: str | None
    ) -> None:
        from src.core import pipeline

        assert pipeline.detect_own_tires(phrase, asking_storage=True) is asked
        assert pipeline.detect_own_tires(phrase, asking_storage=False) is unasked
        assert pipeline.detect_storage_choice(phrase) == choice

    @pytest.mark.parametrize(("utterance", "expected"), ASKING_GOLDEN)
    def test_the_krok_2_gate_answers_exactly_as_before(
        self, utterance: str, expected: bool
    ) -> None:
        from src.core import pipeline

        assert pipeline._bot_is_asking_storage(utterance) is expected

    def test_the_golden_exercises_both_answers(self) -> None:
        """A corpus on which everything returns False proves nothing."""
        assert {row[1] for row in self.GOLDEN} == {True, False}
        assert {row[2] for row in self.GOLDEN} == {True, False}
        assert {row[3] for row in self.GOLDEN} == {"own", "contract", None}
        assert {row[1] for row in self.ASKING_GOLDEN} == {True, False}

    def test_the_context_scoped_phrases_are_the_only_difference(self) -> None:
        """asked-but-not-unasked == exactly `_STORAGE_OWN_HINTS_WHEN_ASKED`.

        The two lists exist because «в мене нема» is a storage denial after the
        Krok 2 question and nothing in particular before it. If the split ever
        collapses, this is where it shows.
        """
        only_when_asked = {row[0] for row in self.GOLDEN if row[1] and not row[2]}
        assert only_when_asked == {"в мене нема", "у мене немає", "не маю", "нема у мене"}
        assert all(
            any(h in phrase for h in _STORAGE_OWN_HINTS_WHEN_ASKED)
            for phrase in only_when_asked
        )
        assert not any(
            any(h in phrase for h in _STORAGE_OWN_HINTS) for phrase in only_when_asked
        )


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
