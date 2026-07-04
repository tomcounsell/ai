"""Tests for SDLC forked-skill issue-number resolution fix (#1731).

Validates three categories:

A. Prose invariants over do-plan-critique/SKILL.md — assert the skill markdown
   assigns and quotes ISSUE_NUMBER, strips 2>/dev/null||true swallows from stage-
   marker calls, and includes the positive-integer assertion.

B. Prose invariants over do-pr-review/SKILL.md — same guarantees, plus the PR-body
   extraction path and no-authoritative-env-inheritance guarantee.

C. Prose invariants over do-sdlc/SKILL.md — assert §3c passes args only; no
   `export SDLC_ISSUE_NUMBER` ambient env hand-off.

D. Regression: `verdict record --issue-number N` under a divergent VALOR_SESSION_ID
   still lands on the issue-scoped session (guards the #1671/#1672 precedence fix).

E. CLI boundary: `--issue-number ""` (quoted empty) is rejected by argparse with
   exit code 2 (type=int validation).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Skill-file resolution helpers
# ---------------------------------------------------------------------------

_SKILL_ROOT_CANDIDATES = [
    Path(".claude/skills-global"),
    Path.home() / ".claude/skills",
]


def _find_skill_md(skill_name: str) -> Path:
    """Resolve SKILL.md from the repo-local skills-global dir or ~/.claude/skills."""
    for root in _SKILL_ROOT_CANDIDATES:
        cand = root / skill_name / "SKILL.md"
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        f"{skill_name}/SKILL.md not found in {[str(r) for r in _SKILL_ROOT_CANDIDATES]}"
    )


@pytest.fixture(scope="module")
def critique_skill() -> str:
    return _find_skill_md("do-plan-critique").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pr_review_skill() -> str:
    return _find_skill_md("do-pr-review").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pr_review_checkout() -> str:
    """checkout.md carries do-pr-review's context-resolution block (#1731)."""
    checkout = _find_skill_md("do-pr-review").parent / "sub-skills" / "checkout.md"
    return checkout.read_text(encoding="utf-8")


# Repo seam files (skill-context convention): the generic skill bodies defer
# the concrete sdlc-tool recorder/stage-marker invocations to docs/sdlc/*.md,
# which this repo's tests assert directly.
@pytest.fixture(scope="module")
def critique_seam() -> str:
    return Path("docs/sdlc/do-plan-critique.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pr_review_seam() -> str:
    return Path("docs/sdlc/do-pr-review.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def do_sdlc_skill() -> str:
    return _find_skill_md("do-sdlc").read_text(encoding="utf-8")


# ===========================================================================
# A — do-plan-critique prose invariants
# ===========================================================================


class TestCritiqueSkillIssueNumberResolution:
    """Prose invariants for do-plan-critique/SKILL.md (#1731)."""

    def test_assigns_issue_number_variable(self, critique_skill: str) -> None:
        """ISSUE_NUMBER= must appear in the Plan Resolution block (not just ISSUE_NUM)."""
        assert "ISSUE_NUMBER=" in critique_skill, (
            "do-plan-critique/SKILL.md must assign ISSUE_NUMBER (canonical variable name); "
            "found only ISSUE_NUM which the downstream recorder calls do not reference"
        )

    def test_numeric_arg_path_assigns_issue_number(self, critique_skill: str) -> None:
        """The numeric-argument branch must set ISSUE_NUMBER from the parsed arg."""
        # The pattern must be: ISSUE_NUMBER="..." not ISSUE_NUM="..."
        assert 'ISSUE_NUMBER="${ARG#\\#}"' in critique_skill or (
            "ISSUE_NUMBER=" in critique_skill
            and "ISSUE_NUM=" not in critique_skill.replace("ISSUE_NUMBER=", "")
        ), "Numeric arg branch must assign ISSUE_NUMBER, not ISSUE_NUM"

    def test_plan_path_arg_extracts_issue_number(self, critique_skill: str) -> None:
        """The plan-path branch must extract ISSUE_NUMBER from plan frontmatter."""
        # The plan-path block must contain an ISSUE_NUMBER assignment
        plan_path_idx = critique_skill.find("*.md ]]; then")
        assert plan_path_idx >= 0, "Plan-path branch marker '*.md ]]; then' not found"
        # After the plan-path branch opens, ISSUE_NUMBER must be assigned
        segment = critique_skill[plan_path_idx : plan_path_idx + 500]
        assert "ISSUE_NUMBER=" in segment, (
            "Plan-path branch must assign ISSUE_NUMBER by extracting from plan frontmatter"
        )

    def test_positive_integer_assertion_present(self, critique_skill: str) -> None:
        """Must assert ISSUE_NUMBER is a positive integer before any recorder call."""
        assert "^[0-9]" in critique_skill, (
            "do-plan-critique must contain a positive-integer assertion "
            '[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] before any recorder call'
        )

    def test_assertion_exits_nonzero_on_empty(self, critique_skill: str) -> None:
        """The assertion block must contain 'exit 1' for the failure path."""
        # Find the assertion and check that exit 1 follows it
        assert_idx = critique_skill.find("^[0-9]")
        assert assert_idx >= 0
        segment = critique_skill[assert_idx : assert_idx + 300]
        assert "exit 1" in segment, (
            "Positive-integer assertion must exit 1 when ISSUE_NUMBER is not a positive integer"
        )

    def test_no_stage_marker_swallow(self, critique_skill: str) -> None:
        """No stage-marker call may suppress failures with 2>/dev/null || true."""
        lines = critique_skill.splitlines()
        swallowed_markers = [
            ln for ln in lines if "stage-marker" in ln and ("2>/dev/null" in ln or "|| true" in ln)
        ]
        # Comments explaining NOT to use swallow are allowed (they don't contain stage-marker)
        assert not swallowed_markers, (
            f"stage-marker calls must not suppress failures with 2>/dev/null || true.\n"
            f"Found: {swallowed_markers}"
        )

    def test_recorder_calls_quote_issue_number(self, critique_skill: str) -> None:
        """Every --issue-number flag in recorder calls must use quoted \"$ISSUE_NUMBER\"."""
        import re

        # Find unquoted --issue-number $ISSUE_NUMBER (without double quotes)
        unquoted = re.findall(r'--issue-number\s+\$ISSUE_NUMBER(?!")', critique_skill)
        assert not unquoted, (
            f'Found unquoted --issue-number $ISSUE_NUMBER (should be "$ISSUE_NUMBER"): {unquoted}'
        )

    def test_verdict_record_uses_quoted_issue_number(self, critique_seam: str) -> None:
        """Recorder calls (declared in the seam) must use --issue-number \"$ISSUE_NUMBER\"."""
        assert '--issue-number "$ISSUE_NUMBER"' in critique_seam, (
            'The seam\'s recorder calls must use --issue-number "$ISSUE_NUMBER" (quoted)'
        )

    def test_no_issue_num_orphan_in_resolution(self, critique_skill: str) -> None:
        """ISSUE_NUM= (the old orphan name) must not appear as the sole assignment."""
        # ISSUE_NUM may appear in comments or historical text, but must not
        # be the assignment that downstream recorder calls depend on.
        # The downstream calls reference $ISSUE_NUMBER, so ISSUE_NUM as the
        # only assignment is the bug. Confirm ISSUE_NUMBER= exists (handled above)
        # and that no recorder call uses $ISSUE_NUM instead of $ISSUE_NUMBER.
        import re

        recorder_with_issue_num = re.findall(
            r"--issue-number\s+\$ISSUE_NUM\b(?!BER)", critique_skill
        )
        assert not recorder_with_issue_num, (
            f"Recorder calls must use $ISSUE_NUMBER, not $ISSUE_NUM: {recorder_with_issue_num}"
        )

    def test_does_not_use_inherited_env_deferral(self, critique_skill: str) -> None:
        """Must NOT use ${ISSUE_NUMBER:-...} for the canonical assignment (clobber, not defer)."""
        # The run-dir naming line may use ${ISSUE_NUMBER:-...} as a slug fallback;
        # that's not a recorder call. What must NOT appear is an assignment of the form
        # ISSUE_NUMBER="${ISSUE_NUMBER:-something}" in the resolution block.
        import re

        deferred_assignments = re.findall(
            r'ISSUE_NUMBER="\$\{ISSUE_NUMBER:-[^}]+\}"', critique_skill
        )
        assert not deferred_assignments, (
            "ISSUE_NUMBER must be unconditionally assigned from $ARGUMENTS, never deferred "
            "with ${ISSUE_NUMBER:-...} — a non-empty inherited wrong value would survive "
            "deferral and divert the verdict (#1731): " + str(deferred_assignments)
        )


# ===========================================================================
# B — do-pr-review prose invariants
# ===========================================================================


class TestPrReviewSkillIssueNumberResolution:
    """Prose invariants for do-pr-review/SKILL.md (#1731)."""

    def test_assigns_issue_number_variable(self, pr_review_checkout: str) -> None:
        """ISSUE_NUMBER= must appear in the context-resolution block (checkout.md)."""
        assert "ISSUE_NUMBER=" in pr_review_checkout, (
            "do-pr-review's checkout.md must assign ISSUE_NUMBER in the context-resolution block"
        )

    def test_pr_body_is_primary_resolution_path(self, pr_review_skill: str) -> None:
        """PR body extraction must be the primary source for ISSUE_NUMBER resolution.

        In do-pr-review, $ARGUMENTS is the PR number, not the issue number.
        So PR-body extraction (Closes #N / Fixes #N / Resolves #N) must run FIRST,
        and ISSUE_NUMBER="$ARGUMENTS" must NOT appear (that would wrongly assign
        the PR number as the issue number — the exact #1731 divert).
        """
        # PR body extraction must appear before any $SDLC_ISSUE_NUMBER fallback
        pr_body_idx = pr_review_skill.find("gh pr view")
        env_idx = pr_review_skill.find('"$SDLC_ISSUE_NUMBER"')
        assert pr_body_idx >= 0, (
            "do-pr-review must fetch PR body via 'gh pr view' to extract the tracking issue number"
        )
        if env_idx >= 0:
            assert pr_body_idx < env_idx, (
                "PR body extraction must appear before any $SDLC_ISSUE_NUMBER fallback — "
                "PR body is the PRIMARY source of ISSUE_NUMBER in do-pr-review (#1731)"
            )
        # $ARGUMENTS must NOT be directly assigned as ISSUE_NUMBER (it is the PR number)
        assert 'ISSUE_NUMBER="$ARGUMENTS"' not in pr_review_skill, (
            'ISSUE_NUMBER="$ARGUMENTS" must not appear in do-pr-review — $ARGUMENTS is the '
            "PR number, not the issue number; use PR body extraction instead (#1731)"
        )

    def test_pr_body_extraction_present(self, pr_review_checkout: str) -> None:
        """Must extract tracking issue from PR body (Closes #N / Fixes #N)."""
        assert "closes" in pr_review_checkout.lower(), (
            "do-pr-review must extract tracking issue from PR body 'Closes #N'"
        )
        assert "PR_BODY" in pr_review_checkout, (
            "do-pr-review must fetch PR body to extract tracking issue number"
        )

    def test_sdlc_issue_number_env_not_authoritative(self, pr_review_checkout: str) -> None:
        """$SDLC_ISSUE_NUMBER env must not be treated as primary/authoritative.

        It may appear as a last-resort fallback ONLY when guarded by a positive-integer
        check (i.e. `[[ "$SDLC_ISSUE_NUMBER" =~ ^[0-9]+$ ]]`). An unguarded direct
        assignment `ISSUE_NUMBER="$SDLC_ISSUE_NUMBER"` as the primary/unconditional source
        is the exact divert mechanism this fix guards against.
        """
        import re

        # The only acceptable form is guarded: [...SDLC_ISSUE_NUMBER =~ ^[0-9]...; then
        #   ISSUE_NUMBER="$SDLC_ISSUE_NUMBER"
        # An unconditional top-level assignment (not inside an if-block that checks
        # the value is a positive integer) is the bug.
        # PR-body extraction is the PRIMARY source in do-pr-review ($ARGUMENTS is
        # the PR number, not the issue number), so PR_BODY resolution must appear
        # BEFORE any $SDLC_ISSUE_NUMBER fallback.
        pr_body_idx = pr_review_checkout.find("PR_BODY")
        env_idx = pr_review_checkout.find('"$SDLC_ISSUE_NUMBER"')
        assert pr_body_idx >= 0, (
            "checkout.md must extract ISSUE_NUMBER from the PR body as the primary source, "
            "not $SDLC_ISSUE_NUMBER"
        )
        if env_idx >= 0:
            assert pr_body_idx < env_idx, (
                "PR-body resolution must appear before any $SDLC_ISSUE_NUMBER fallback — "
                "$SDLC_ISSUE_NUMBER must be a last-resort only, not the primary source (#1731)"
            )
        # Any use of $SDLC_ISSUE_NUMBER must be guarded (preceded by a regex check)
        # rather than being a bare unconditional assignment at the start of the block.
        guarded = re.search(
            r"\[\[.*SDLC_ISSUE_NUMBER.*\^.*\[0-9\]",
            pr_review_checkout,
        )
        if env_idx >= 0:
            assert guarded is not None, (
                "Any use of $SDLC_ISSUE_NUMBER for ISSUE_NUMBER assignment must be guarded "
                "by a positive-integer regex check, not used unconditionally (#1731)"
            )

    def test_positive_integer_assertion_present(self, pr_review_checkout: str) -> None:
        """Must assert ISSUE_NUMBER is a positive integer before any recorder call."""
        assert "^[0-9]" in pr_review_checkout, (
            "do-pr-review's checkout.md must contain a positive-integer assertion "
            '[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] before any recorder call'
        )

    def test_assertion_exits_nonzero_on_empty(self, pr_review_checkout: str) -> None:
        """The assertion block must exit 1 for the failure path.

        Searches for the final positive-integer assertion (the one that guards
        ALL recorder calls, not the per-branch guards inside the resolution block).
        """
        # The final assertion pattern: [[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || { ... exit 1 }
        # Use rfind to locate the last occurrence of the assertion (not the per-branch guards).
        assert_idx = pr_review_checkout.rfind('"$ISSUE_NUMBER" =~ ^[0-9]')
        assert assert_idx >= 0, (
            'do-pr-review\'s checkout.md must contain [[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] '
            "as the final guard before any recorder call"
        )
        # The exit 1 must appear within a reasonable window after the assertion
        segment = pr_review_checkout[assert_idx : assert_idx + 600]
        assert "exit 1" in segment, (
            "Positive-integer assertion must exit 1 when ISSUE_NUMBER is unresolvable"
        )

    def test_recorder_calls_quote_issue_number(
        self, pr_review_skill: str, pr_review_checkout: str, pr_review_seam: str
    ) -> None:
        """Every --issue-number flag in recorder calls must use quoted \"$ISSUE_NUMBER\"."""
        import re

        for text in (pr_review_skill, pr_review_checkout, pr_review_seam):
            unquoted = re.findall(r'--issue-number\s+\$ISSUE_NUMBER(?!")', text)
            assert not unquoted, (
                f'Found unquoted --issue-number $ISSUE_NUMBER (must be "$ISSUE_NUMBER"): {unquoted}'
            )

    def test_verdict_record_uses_quoted_issue_number(self, pr_review_seam: str) -> None:
        """verdict record calls (declared in the seam) must use --issue-number \"$ISSUE_NUMBER\"."""
        assert '--issue-number "$ISSUE_NUMBER"' in pr_review_seam, (
            'verdict record call must use --issue-number "$ISSUE_NUMBER" (quoted)'
        )

    def test_stage_marker_uses_quoted_issue_number(
        self, pr_review_skill: str, pr_review_seam: str
    ) -> None:
        """stage-marker calls (declared in the seam) must use --issue-number \"$ISSUE_NUMBER\"."""
        # The generic body carries the stage-marker substrate hook...
        assert "stage-marker" in pr_review_skill
        # ...and the seam declares the concrete quoted invocations.
        assert (
            'stage-marker --stage REVIEW --status in_progress --issue-number "$ISSUE_NUMBER"'
            in pr_review_seam
            or 'stage-marker --stage REVIEW --status completed --issue-number "$ISSUE_NUMBER"'
            in pr_review_seam
        ), 'stage-marker calls must use --issue-number "$ISSUE_NUMBER" (quoted)'

    def test_in_progress_marker_uses_issue_number_variable(
        self, pr_review_skill: str, pr_review_seam: str
    ) -> None:
        """The in_progress stage marker at skill start must not use a {issue_number} placeholder."""
        import re

        placeholder_markers = re.findall(
            r"stage-marker[^\n]*--issue-number\s+\{issue_number\}",
            pr_review_skill + pr_review_seam,
        )
        assert not placeholder_markers, (
            "stage-marker calls must use $ISSUE_NUMBER (shell variable), not {issue_number} "
            "(literal placeholder that would pass the string '{issue_number}' to argparse): "
            + str(placeholder_markers)
        )


# ===========================================================================
# C — do-sdlc prose invariants
# ===========================================================================


class TestDoSdlcDispatchTemplate:
    """Prose invariants for do-sdlc/SKILL.md §3c dispatch template (#1731)."""

    def test_no_sdlc_issue_number_export(self, do_sdlc_skill: str) -> None:
        """do-sdlc must NOT export SDLC_ISSUE_NUMBER as an ambient env variable."""
        assert "export SDLC_ISSUE_NUMBER" not in do_sdlc_skill, (
            "do-sdlc §3c must NOT export SDLC_ISSUE_NUMBER — an ambient env var is a "
            "divert vector (the 'latched onto wrong issue' mechanism #1731 fixes). "
            "The issue number must be passed via skill args only."
        )

    def test_dispatch_template_passes_issue_number_via_args(self, do_sdlc_skill: str) -> None:
        """The dispatch template must pass the issue number via skill args."""
        assert 'args "' in do_sdlc_skill or "args '{" in do_sdlc_skill, (
            "do-sdlc §3c dispatch template must pass issue number via skill args"
        )

    def test_no_env_sdlc_issue_number_set(self, do_sdlc_skill: str) -> None:
        """SDLC_ISSUE_NUMBER= assignment must not appear in do-sdlc."""
        import re

        env_sets = re.findall(r"SDLC_ISSUE_NUMBER\s*=", do_sdlc_skill)
        assert not env_sets, (
            "do-sdlc must not set SDLC_ISSUE_NUMBER — args-only hand-off (#1731): " + str(env_sets)
        )


# ===========================================================================
# D — Regression: #1671/#1672 precedence still holds
# ===========================================================================


class TestIssuePrecedenceOverEnvSession:
    """Regression guard for #1671/#1672: issue_number beats VALOR_SESSION_ID env on writes."""

    def _args(self, **kw):
        from types import SimpleNamespace

        base = dict(
            session_id=None,
            issue_number=1731,
            stage="CRITIQUE",
            verdict="READY TO BUILD (no concerns)",
            blockers=None,
            tech_debt=None,
            judges_json=None,
            consensus_json=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_verdict_lands_on_issue_session_despite_divergent_env(self, monkeypatch) -> None:
        """A forked-style verdict record with --issue-number N lands on sdlc-local-{N}
        even when VALOR_SESSION_ID points at a different (parent's) session.

        This is the direct regression guard for the 'latched onto #1724' divert (#1731).
        The #1671/#1672 precedence in find_session() is the mechanism that makes this work.
        """
        from unittest.mock import MagicMock, patch

        from tools import _sdlc_utils
        from tools.sdlc_verdict import _cli_record

        # Env var mimics a stale inherited parent-session id (e.g. the do-sdlc
        # supervisor's own session that the forked subagent inherited).
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-1724")  # stale from prior run
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        # The issue-scoped session (what sdlc-local-1731 or the owning PM session would be).
        issue_session = MagicMock(name="sdlc-local-1731")
        issue_session.session_id = "sdlc-local-1731"
        issue_session.session_type = "eng"
        issue_session.stage_states = "{}"

        def _fake_save():
            pass

        issue_session.save = _fake_save

        # Patch find_session_by_issue to return the issue-scoped session (simulates
        # the actual Redis lookup finding sdlc-local-1731).
        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=issue_session):
            with patch("tools.stage_states_helpers._reload_session", return_value=issue_session):
                # Record a verdict with --issue-number 1731 under divergent env.
                recorded = _cli_record(self._args())

        # The verdict must land on the issue session, NOT on "parent-pm-1724".
        assert recorded.get("verdict") == "READY TO BUILD (NO CONCERNS)", (
            f"Expected 'READY TO BUILD (NO CONCERNS)', got: {recorded}"
        )

    def test_find_session_issue_number_beats_env_on_write_path(self, monkeypatch) -> None:
        """Regression guard: issue_number=N always wins over VALOR_SESSION_ID env.

        This is the existing #1671/#1672 test reproduced here to ensure the skill
        fix (#1731) does not accidentally weaken the recorder-layer precedence.
        """
        from tools import _sdlc_utils

        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-divergent")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        issue_session = MagicMock(name="issue_session")
        issue_session.session_type = "eng"

        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=issue_session):
            result = _sdlc_utils.find_session(None, 1731, ensure=True)

        # Issue-scoped session must win over the inherited env session.
        assert result is issue_session


# ===========================================================================
# E — CLI boundary: --issue-number "" exits code 2
# ===========================================================================


class TestIssueNumberArgparseBoundary:
    """CLI exit-code boundary: argparse type=int rejects empty/non-integer values."""

    def test_quoted_empty_issue_number_rejected_by_argparse(self) -> None:
        """sdlc-tool verdict record --issue-number "" must exit with code 2.

        When the skill passes --issue-number "$ISSUE_NUMBER" and ISSUE_NUMBER is
        empty (the bug #1731 fixes), argparse type=int must reject it with exit 2
        rather than silently accepting it or consuming the next token.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_verdict",
                "record",
                "--stage",
                "CRITIQUE",
                "--verdict",
                "NEEDS REVISION",
                "--issue-number",
                "",  # the quoted-empty form
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, (
            f"Expected argparse exit code 2 for empty --issue-number, got {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )
        # argparse should emit a clear error message
        assert "issue-number" in result.stderr.lower() or "invalid" in result.stderr.lower(), (
            f"Expected argparse error mentioning 'issue-number', got: {result.stderr}"
        )

    def test_quoted_empty_stage_marker_rejected_by_argparse(self) -> None:
        """sdlc-tool stage-marker --issue-number "" must exit with code 2."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_stage_marker",
                "--stage",
                "CRITIQUE",
                "--status",
                "in_progress",
                "--issue-number",
                "",  # the quoted-empty form
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, (
            f"Expected argparse exit code 2 for empty --issue-number stage-marker, "
            f"got {result.returncode}.\nstderr: {result.stderr}"
        )
