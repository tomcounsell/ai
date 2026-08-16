"""Locking gate for PM evidence-bearing progress updates (#2664).

The fix for #2664 is entirely instructional: the promise gate is correct and
stays byte-for-byte unchanged. What changed is that the PM role doc now teaches
*when* it may speak mid-flight and *how* to phrase it, and the persona ethos now
distinguishes a banned promise from an allowed statement of observed fact.

Two things are locked here.

1. **Prompt-text anchors** (following ``test_resume_reverification.py``): the
   guidance is text loaded into every headless PM turn, so a CI gate is the only
   thing that keeps an edit from silently deleting it.

2. **Promise-gate fallback characterization**: the phrasings the role doc teaches
   must survive the *deterministic* heuristic branch
   (``_evaluate_promise_heuristic``), which is what actually decides the verdict
   whenever the LLM is unavailable (no API key, SDK exception, timeout). Without
   this, someone tightening ``_FORWARD_DEFERRAL_PATTERNS`` with an innocuous-looking
   ``\\bstill\\s+running\\b`` would start blocking the exact sentences the role doc
   tells the PM to send, and only on the fallback path, which is the hardest
   place to notice a regression.

Deliberately NOT asserted: the live-LLM verdict. The LLM layer was measured
directly against these phrasings on 2026-08-08 (see the table in
``.claude/commands/roles/prime-pm-role.md``). Every phrasing this file locks was
stable at 8/8 there, but two facts make an LLM assertion the wrong instrument:

- ``"dev opened PR #102 (14 files) - still running tests."`` (the phrasing the
  issue's own table records as a clean allow) measured 6/8 allow, i.e. flaky. It
  is locked here as an ANTI-example: the role doc steers away from it.
- The heuristic and the LLM genuinely disagree in the other direction too:
  ``"Still working on this."`` is blocked 8/8 by the LLM but ALLOWED by the
  heuristic, because no regex covers it. So "bare reassurance blocks" is not a
  property of the deterministic layer and cannot honestly be asserted as one.

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

# The two shapes the role doc teaches as safe. Both must clear the deterministic
# fallback branch.
TAUGHT_ALLOWED = [
    # Shape 1: present fact, no forward-looking clause, no artifact needed.
    "Scope check: what read as a one-line config change is 14 files across "
    "tools/ and config/. That is why this is taking a while.",
    # Shape 1 with an artifact, still no forward clause.
    "This turned out bigger than the ask implied: dev rewrote 14 files across "
    "tools/ and config/ and opened PR #102.",
    # Shape 2: work in flight, rescued by a full PR URL.
    "dev opened PR https://github.com/example/repo/pull/102 (14 files), still running tests.",
]

# Explicit forward-deferral the role doc routes to ``expectation-add`` instead. The
# heuristic must keep catching this on the fallback path.
TAUGHT_BLOCKED = "dev opened PR #102. I'll report back when tests finish."


def _read_nonempty(path: Path) -> str:
    """Read a required prompt file, failing loudly if absent or empty."""
    assert path.exists(), f"Required prompt file is missing: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"Required prompt file is empty: {path}"
    return text


class TestPMRoleGuidance:
    """The PM role doc must carry the four things #2664 asks it to teach."""

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

    def test_requires_evidence_with_both_a_passing_and_a_blocked_example(self):
        text = _read_nonempty(PM_ROLE_PATH)
        assert "Still working on this." in text, (
            "The role doc must show a concrete BLOCKED example so the PM can "
            "recognise the shape it must avoid."
        )
        # The doc renders paths and URLs in backticks; the gate sees plain text.
        # Compare on a backtick-stripped view so markdown emphasis alone never
        # breaks the doc/test agreement this test exists to enforce.
        plain = text.replace("`", "")
        for allowed in TAUGHT_ALLOWED:
            head = allowed.split(".")[0]
            assert head in plain, (
                f"The role doc must show the passing example beginning {head!r}. "
                "The test locks the same strings the doc teaches; if you reword the "
                "doc, update TAUGHT_ALLOWED here so the two stay in agreement."
            )

    def test_names_the_actual_discriminator(self):
        low = _read_nonempty(PM_ROLE_PATH).lower()
        assert "not the presence of evidence" in low, (
            "The role doc must state the measured rule: the gate keys on a "
            "forward-looking clause, NOT on the presence of evidence. Teaching "
            "'just add evidence' produces messages that block."
        )
        assert "pull/" in low, (
            "The role doc must tell the PM to cite a full PR URL rather than a bare "
            "#N. The URL is the only autonomous-delivery reference both gate "
            "layers recognise."
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
    """Characterization: the deterministic branch must not block what we teach.

    This is the regression guard the acceptance criteria asked for, retargeted at
    the layer where a deterministic assertion is actually truthful.
    """

    @pytest.mark.parametrize("message", TAUGHT_ALLOWED)
    def test_taught_phrasing_clears_the_heuristic(self, message):
        verdict = _evaluate_promise_heuristic(message)
        assert verdict.action == "allow", (
            "The promise-gate fallback branch now blocks a phrasing that "
            "prime-pm-role.md instructs the PM to send:\n"
            f"  message: {message}\n"
            f"  verdict: {verdict.action} ({verdict.class_}: {verdict.reason})\n"
            "Either revert the pattern change or update the role doc's taught "
            "phrasings to match. Silently diverging leaves the PM emitting "
            "messages that die on the fallback path."
        )

    def test_explicit_forward_deferral_still_blocks(self):
        verdict = _evaluate_promise_heuristic(TAUGHT_BLOCKED)
        assert verdict.action == "block", (
            "The heuristic must keep catching an explicit 'I'll report back' even "
            "when substantive evidence is present. Evidence does not rescue a "
            "forward-deferral, only a scheduled-delivery reference does."
        )
        assert verdict.class_ == "forward_deferral"

    def test_bare_pr_number_with_ongoing_clause_is_not_taught(self):
        """The issue's flagship example measured 6/8 allow against the LLM.

        It is excluded from TAUGHT_ALLOWED on purpose. This test pins that
        exclusion so a future edit cannot quietly promote a coin-flip phrasing
        into the role doc's recommended set.
        """
        flaky = "dev opened PR #102 (14 files) - still running tests."
        assert flaky not in TAUGHT_ALLOWED
        role_text = _read_nonempty(PM_ROLE_PATH)
        assert "unreliable, 6/8 allow" in role_text, (
            "The role doc must keep recording that the bare-#N + ongoing-clause "
            "phrasing is unreliable, so nobody re-derives it as a good example "
            "from the issue's original table."
        )
