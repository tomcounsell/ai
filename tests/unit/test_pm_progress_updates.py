"""Locking gate for PM evidence-bearing progress updates (#2664 / #3027).

The original #2664 fix taught the PM a phrasebook of measured-safe phrasings
against the promise gate. #3027 deletes that phrasebook: it trained the PM to
hunt for wording that clears the gate (e.g. asking permission it does not
need, "say the word and I'll re-run that"), and it was measured against a
gate that never ran on the PM's actual delivery path. Grading grammar can
only ever be satisfied by better grammar — so the fix is not a better table,
it is a different discriminator entirely: whether the obligation behind a
forward-looking statement is *durably recorded* (a Job inbound expectation,
a `schedule_id`, or a PR URL), not how the sentence is phrased.

Deleting the phrasebook carelessly trades over-claiming for silence, which is
the worse failure (the original symptom this section exists to prevent). So
the honest core — say only what is already true, stated as present fact —
must survive verbatim into the replacement.

Three things are locked here.

1. **Prompt-text anchors** (following ``test_resume_reverification.py``): the
   guidance is text loaded into every headless PM turn, so a CI gate is the
   only thing that keeps an edit from silently deleting it.

2. **No phrasing-workaround language survives.** The deleted phrasebook's
   measured-verdict table and "two ways to stay on the allowed side" list
   must not reappear under any name.

3. **Promise-gate fallback characterization**: illustrative phrasings that
   mirror what the rewritten section teaches (present fact, no forward
   clause) must still clear the *deterministic* heuristic branch
   (``_evaluate_promise_heuristic``), which is what actually decides the
   verdict whenever the LLM is unavailable (no API key, SDK exception,
   timeout). Without this, someone tightening ``_FORWARD_DEFERRAL_PATTERNS``
   with an innocuous-looking ``\\bstill\\s+running\\b`` would start blocking
   the exact shape of sentence the role doc tells the PM to send, and only on
   the fallback path, which is the hardest place to notice a regression. An
   explicit forward-deferral ("I'll report back...") must still block on that
   same deterministic path — evidence never rescues it, only a recorded
   obligation does, and the heuristic itself has no notion of "recorded".

Failure-path strategy: every read asserts the file exists and is non-empty
BEFORE inspecting content, so a missing or truncated file fails loudly rather
than passing vacuously.
"""

from pathlib import Path

import pytest

from bridge.promise_gate import _evaluate_promise_heuristic

pytestmark = [pytest.mark.unit, pytest.mark.sdlc]

REPO_ROOT = Path(__file__).resolve().parents[2]
PM_ROLE_PATH = REPO_ROOT / ".claude" / "commands" / "roles" / "prime-pm-role.md"
WORK_PATTERNS_PATH = REPO_ROOT / "config" / "personas" / "segments" / "work-patterns.md"

PM_SECTION_HEADER = "# Progress updates when the work overruns the ask"

# Illustrative phrasings mirroring what the rewritten section teaches: present
# fact, no forward-looking clause. These are not required to appear verbatim
# in the prompt (the old phrasebook's literal-example table is gone by
# design) — they characterize the *shape* of message the guidance produces,
# and must keep clearing the deterministic fallback branch.
TAUGHT_ALLOWED = [
    # Present fact, no forward-looking clause, no artifact needed.
    "Scope check: what read as a one-line config change is 14 files across "
    "tools/ and config/. That is why this is taking a while.",
    # Present fact with an artifact, still no forward clause.
    "This turned out bigger than the ask implied: dev rewrote 14 files across "
    "tools/ and config/ and opened PR #102.",
    # Work in flight, named via a full PR URL rather than a bare number.
    "dev opened PR https://github.com/example/repo/pull/102 (14 files), still running tests.",
]

# Explicit forward-deferral the role doc routes to `expectation-add` instead
# of wording around. The heuristic must keep catching this on the fallback
# path regardless of how much evidence accompanies it.
TAUGHT_BLOCKED = "dev opened PR #102. I'll report back when tests finish."

# Strings that belonged to the deleted #2664 phrasebook. None of these may
# reappear in the rewritten section under any name.
PHRASING_WORKAROUND_STRINGS = [
    "two ways to stay on the allowed side",
    "measured against the live gate",
    "8/8",
    "unreliable, 6/8 allow",
]


def _read_nonempty(path: Path) -> str:
    """Read a required prompt file, failing loudly if absent or empty."""
    assert path.exists(), f"Required prompt file is missing: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"Required prompt file is empty: {path}"
    return text


class TestPMRoleGuidance:
    """The PM role doc must carry what #3027 asks it to teach, and none of
    what it asks removed."""

    def test_section_exists(self):
        text = _read_nonempty(PM_ROLE_PATH)
        assert PM_SECTION_HEADER in text, (
            f"prime-pm-role.md must carry the {PM_SECTION_HEADER!r} section. It "
            "is the only place the PM learns it may speak mid-flight at all."
        )

    def test_trigger_is_scope_relative_not_a_timer(self):
        low = _read_nonempty(PM_ROLE_PATH).lower()
        assert "there is no timer here and none is wanted" in low, (
            "The role doc must state explicitly that the trigger is NOT a timer. "
            "An unconditional interval is the exact shape that got INTERRUPT_RESUME "
            "deleted in #1937."
        )
        assert "category change" in low, (
            "The trigger must be keyed to a category change between implied and "
            "actual scope, not to elapsed time."
        )

    def test_explains_turn_boundary_constraint_and_bounded_dispatch(self):
        text = _read_nonempty(PM_ROLE_PATH)
        low = text.lower()
        assert "turn boundaries" in low, (
            "The role doc must explain that the PM only has a voice at turn "
            "boundaries. A PM blocked inside a foreground Agent call holds no "
            "execution (#2420)."
        )
        assert "#2420" in text, "The turn-boundary constraint must cite its source issue (#2420)."
        assert "sendmessage" in low and "same dev" in low, (
            "The role doc must instruct bounding the dev dispatch while continuing "
            "the SAME dev agent via SendMessage. Bounding must never be read as "
            "licence to spawn a second dev."
        )

    def test_present_fact_norm_survives(self):
        text = _read_nonempty(PM_ROLE_PATH)
        assert "say only what is already true" in text, (
            "The literal present-fact reporting norm must survive the phrasebook "
            "deletion verbatim — it is the honest core of the deleted section, "
            "and losing it trades over-claiming for silence, the worse failure."
        )

    def test_names_the_actual_discriminator(self):
        text = _read_nonempty(PM_ROLE_PATH)
        low = text.lower()
        assert "recorded" in low and "not how it is phrased" in low, (
            "The role doc must state the real discriminator: whether the "
            "obligation is durably recorded, NOT how the sentence is phrased. "
            "Teaching 'find better wording' reproduces the evasion #3027 removes."
        )
        for anchor in ("expectation-add", "schedule_id", "pr url"):
            assert anchor in low, (
                f"The role doc must name {anchor!r} as one of the durable "
                "recording mechanisms behind an honest forward-looking statement."
            )

    def test_teaches_dispatch_you_can_execute_you_execute(self):
        low = _read_nonempty(PM_ROLE_PATH).lower()
        assert "a dispatch you can execute, you execute" in low, (
            "The role doc must state the rule directly: work the PM can already "
            "re-dispatch within its own turn, it dispatches, rather than asking "
            "the human's permission for a call it does not need permission for."
        )
        assert "say the word" in low, (
            "The role doc must name the specific evasion phrasing being removed "
            "('say the word and I'll re-run that') so it reads as banned, not as "
            "an example of good practice."
        )

    def test_expectation_add_is_the_one_way_to_commit(self):
        low = _read_nonempty(PM_ROLE_PATH).lower()
        assert "expectation-add" in low and "one way to commit" in low, (
            "The role doc must state that `expectation-add` is the ONE way to "
            "commit to a follow-up — recording on the Job, not phrasing in the "
            "message, is what makes a promise durable."
        )

    def test_teaches_ask_coverage_authoring(self):
        text = _read_nonempty(PM_ROLE_PATH)
        low = text.lower()
        assert "ask_coverage" in text, (
            "The role doc must teach how to author `ask_coverage` on the structured route output."
        )
        for disposition in ("delivered", "blocked", "declined", "not_started"):
            assert disposition in low, (
                f"The role doc must name the {disposition!r} disposition as one "
                "every clause of the human's ask must be assigned."
            )
        assert "evidence" in low, (
            "The role doc must state that a `delivered` disposition requires "
            "evidence naming the concrete artifact."
        )

    def test_no_phrasing_workaround_language_survives(self):
        low = _read_nonempty(PM_ROLE_PATH).lower()
        for banned in PHRASING_WORKAROUND_STRINGS:
            assert banned not in low, (
                f"The deleted phrasebook content {banned!r} must not reappear in "
                "prime-pm-role.md. #3027 deletes the phrasebook because it "
                "taught wording workarounds against the gate, not because it was "
                "misplaced — bringing any of it back reintroduces that training."
            )


class TestWorkPatternsScopeClarification:
    """The ethos must be clarified without being weakened."""

    def test_ethos_line_survives(self):
        text = _read_nonempty(WORK_PATTERNS_PATH)
        assert "Do or do not — there is no try." in text, (
            "The ethos sentence must stay verbatim. #2664 is a clarification of "
            "its scope, not a reversal."
        )
        assert "I never narrate the attempt." in text, (
            "The ban on narrating the attempt must survive the clarification."
        )

    def test_promise_versus_observed_fact_distinction_present(self):
        text = _read_nonempty(WORK_PATTERNS_PATH)
        low = text.lower()
        assert "observed fact" in low, (
            "work-patterns.md must distinguish a banned promise from an allowed "
            "statement of observed fact, or the PM reads the ethos as an absolute "
            "gag and stays silent (#2664)."
        )
        assert "reassuring without evidence" in low, (
            "The clarification must restate that evidence-free reassurance is "
            "still banned, so it cannot be read as a general licence to chat."
        )


class TestPromiseGateFallbackAllowsTaughtPhrasings:
    """Characterization: the deterministic branch must not block the shape of
    message the rewritten section teaches.

    This is the prompt-to-gate lock #3027 preserves: it stops a future
    ``_FORWARD_DEFERRAL_PATTERNS`` tightening from silently starting to block
    a present-fact, no-forward-clause message on the fallback path.
    """

    @pytest.mark.parametrize("message", TAUGHT_ALLOWED)
    def test_taught_phrasing_clears_the_heuristic(self, message):
        verdict = _evaluate_promise_heuristic(message)
        assert verdict.action == "allow", (
            "The promise-gate fallback branch now blocks a phrasing shape "
            "prime-pm-role.md teaches the PM to send:\n"
            f"  message: {message}\n"
            f"  verdict: {verdict.action} ({verdict.class_}: {verdict.reason})\n"
            "Either revert the pattern change or update the role doc's guidance "
            "to match. Silently diverging leaves the PM emitting messages that "
            "die on the fallback path."
        )

    def test_explicit_forward_deferral_still_blocks(self):
        verdict = _evaluate_promise_heuristic(TAUGHT_BLOCKED)
        assert verdict.action == "block", (
            "The heuristic must keep catching an explicit 'I'll report back' even "
            "when substantive evidence is present. Evidence does not rescue a "
            "forward-deferral, only a recorded obligation does — and the "
            "deterministic heuristic has no notion of 'recorded', by design."
        )
        assert verdict.class_ == "forward_deferral"
