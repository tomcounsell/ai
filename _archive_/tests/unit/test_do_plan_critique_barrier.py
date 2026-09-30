"""Tests for the /do-plan-critique artifact-based roster barrier (issue #1690).

Two flavors, modeled on tests/unit/test_do_patch_ticks.py:

- **Prose invariants** (Halves A & B): grep over the real ``SKILL.md`` and
  ``CRITICS.md`` to assert the skill prose encodes the artifact barrier and the
  ordering/aggregation invariants. These enforce the same "invariant tests over
  the skill markdown" enforcement class the plan describes — the driver's
  obligation to freeze a roster, write result files, gate before aggregating,
  and never re-introduce ``run_in_background`` is asserted here.
- **Helper behavior** (Half C): exercise ``tools.critique_roster_check`` directly
  against synthetic run dirs in ``tmp_path``. The gate is plain stdlib Python over
  real files, so the regression test creates/omits result files and asserts the
  gate decision + exit code — the independently-verifiable check the critique
  demanded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import critique_roster_check as crc

# ---------------------------------------------------------------------------
# Skill-file resolution (mirror test_do_patch_ticks.py, but robust to the
# repo-vs-hardlink split: the skill lives under .claude/skills-global/ in the
# repo and is hardlinked into ~/.claude/skills/ on every machine).
# ---------------------------------------------------------------------------

_SKILL_CANDIDATES = [
    Path(".claude/skills-global/do-plan-critique"),
    Path.home() / ".claude/skills/do-plan-critique",
]


def _resolve_skill_dir() -> Path:
    for cand in _SKILL_CANDIDATES:
        if (cand / "SKILL.md").is_file():
            return cand
    raise FileNotFoundError(
        "do-plan-critique SKILL.md not found in any known location: "
        + ", ".join(str(c) for c in _SKILL_CANDIDATES)
    )


@pytest.fixture(scope="module")
def skill_dir() -> Path:
    return _resolve_skill_dir()


@pytest.fixture(scope="module")
def skill_text(skill_dir: Path) -> str:
    return (skill_dir / "SKILL.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def critics_text(skill_dir: Path) -> str:
    return (skill_dir / "CRITICS.md").read_text(encoding="utf-8")


# Repo seam file (skill-context convention): the generic skill body refers to
# the barrier CLIs abstractly ("membership-gate CLI", "resume probe"); the
# concrete repo executables (critique-roster-check / critique-resume-probe)
# are declared in docs/sdlc/do-plan-critique.md, which this repo's tests can
# assert directly.
_SEAM_PATH = Path("docs/sdlc/do-plan-critique.md")


@pytest.fixture(scope="module")
def seam_text() -> str:
    return _SEAM_PATH.read_text(encoding="utf-8")


# Fence tokens, asserted in many places.
FENCE_DELIMITER = "<<<CRITIQUE-RESULT-COMPLETE>>>"
FENCE_STATUS = "STATUS: COMPLETED"


def _section(text: str, start_marker: str, *end_markers: str) -> str:
    """Slice ``text`` from ``start_marker`` up to the first of ``end_markers``.

    Raises if ``start_marker`` is absent. If no end marker is found, returns the
    text from the start marker to EOF.
    """
    start = text.index(start_marker)
    end = len(text)
    for em in end_markers:
        idx = text.find(em, start + len(start_marker))
        if idx != -1:
            end = min(end, idx)
    return text[start:end]


# ===========================================================================
# Half A — Prose invariants over SKILL.md / CRITICS.md
# ===========================================================================


class TestProseInvariants:
    # (a) No run_in_background SPAWN DIRECTIVE in Step 3 / How-to-Spawn ----

    def test_step_3_war_room_section_has_no_background_spawn_directive(
        self, skill_text: str
    ) -> None:
        # Extract the Step 3 war-room section (up to Step 3.5). The literal token
        # legitimately appears elsewhere (version-history line; the Step 3.5
        # prohibition note), so we scope to the spawn section only.
        step3 = _section(skill_text, "### Step 3: War Room", "### Step 3.5")
        assert "run_in_background: true" not in step3

    def test_how_to_spawn_section_has_no_background_spawn_directive(
        self, critics_text: str
    ) -> None:
        # CRITICS.md "## How to Spawn" up to the next top-level break.
        how_to_spawn = _section(critics_text, "## How to Spawn", "\n---", "\n## ")
        assert "run_in_background: true" not in how_to_spawn

    # (b) Step 3a freezes a roster manifest before dispatch ----------------

    def test_step_3a_freezes_roster_manifest_before_dispatch(self, skill_text: str) -> None:
        assert "Step 3a" in skill_text
        step3a = _section(skill_text, "### Step 3a", "### Step 3:")
        assert "_roster.json" in step3a
        # A "before dispatch" / "freeze" notion must be present.
        lowered = step3a.lower()
        assert "before" in lowered and "freeze" in lowered
        # Roster content must be triage-selected (LITE or FULL), not hardcoded to 7.
        assert '"count": 7' not in step3a
        assert "LITE" in step3a or "FULL" in step3a

    # (c) result-file + two-line terminal fence mandated ------------------

    def test_step_3_mandates_result_file_terminal_fence(self, skill_text: str) -> None:
        step3 = _section(skill_text, "### Step 3: War Room", "### Step 3.5")
        assert FENCE_DELIMITER in step3
        assert FENCE_STATUS in step3
        assert ".result.md.tmp" in step3
        assert "rename" in step3.lower()

    def test_critics_md_carries_no_quote_the_fence_rule(self, critics_text: str) -> None:
        assert FENCE_DELIMITER in critics_text
        # The no-quote rule near the fence token.
        lowered = critics_text.lower()
        assert "never quote" in lowered

    # (d) Step 3.5 cap framing + CRITIQUE INCOMPLETE ----------------------

    def test_step_3_5_calls_roster_check_and_names_cap(
        self, skill_text: str, seam_text: str
    ) -> None:
        step35 = _section(skill_text, "### Step 3.5", "### Step 4")
        # The generic body invokes the barrier through the probe-guarded
        # membership-gate hook; the concrete gate CLI is a repo executable
        # declared in the seam file (skill-context convention).
        assert "membership-gate CLI" in step35
        assert "critique-roster-check" in seam_text
        assert "MAX_CRITIC_REDISPATCH" in step35
        # The "1 initial + up to 2 re-dispatches = 3 attempts" framing.
        assert "3 attempts" in step35 or "1 initial" in step35

    def test_step_3_5_records_critique_incomplete_on_still_incomplete(
        self, skill_text: str
    ) -> None:
        step35 = _section(skill_text, "### Step 3.5", "### Step 4")
        assert "CRITIQUE INCOMPLETE" in step35

    # (e) Steps 5.5 and 5.6 still present ---------------------------------

    def test_steps_5_5_and_5_6_present(self, skill_text: str) -> None:
        assert "### Step 5.5" in skill_text
        assert "### Step 5.6" in skill_text

    # (e2) Run-dir cleanup gated on complete, preserved on incomplete ------

    def test_run_dir_cleanup_gated_on_complete_and_preserved_on_incomplete(
        self, skill_text: str
    ) -> None:
        assert "complete: true" in skill_text
        # "preserve" appears (case-insensitive) on the incomplete path.
        assert "preserve" in skill_text.lower()

    # (f) Step 2b resume probe present ------------------------------------

    def test_step_2b_resume_probe_present(self, skill_text: str, seam_text: str) -> None:
        """Step 2b must exist and invoke the resume probe (concrete CLI in seam)."""
        assert "Step 2b" in skill_text
        step2b = _section(skill_text, "### Step 2b", "### Step 2.6")
        assert "resume probe" in step2b
        assert "critique-resume-probe" in seam_text

    # (g) Step 2.6 triage and force-FULL present --------------------------

    def test_step_2_6_triage_and_force_full_present(self, skill_text: str) -> None:
        """Step 2.6 must exist and list doctrine force-FULL paths."""
        assert "Step 2.6" in skill_text
        step26 = _section(skill_text, "### Step 2.6", "### Step 3a")
        assert "FULL" in step26
        assert "LITE" in step26
        # Doctrine paths that trigger force-FULL must be listed
        assert "agent/sdlc_router.py" in step26 or "doctrine" in step26.lower()

    # (h) Step 4 has no re-run directive ----------------------------------

    def test_step_4_has_no_rerun_directive(self, skill_text: str) -> None:
        """Step 4 must NOT contain the re-run directive (deleted in this plan)."""
        step4 = _section(skill_text, "### Step 4", "### Step 5")
        assert "Re-run that critic" not in step4
        # Validation-only replacement must be present
        assert "exclude" in step4.lower() or "excluded" in step4.lower()

    # (i) LITE and FULL roster shapes documented --------------------------

    def test_roster_lite_full_shapes_documented(self, skill_text: str) -> None:
        """SKILL.md must document both LITE (1 critic) and FULL (3 critics) roster shapes."""
        assert "Consolidated Critic" in skill_text
        # FULL roster names
        assert "Risk & Robustness" in skill_text
        assert "Scope & Value" in skill_text
        assert "History & Consistency" in skill_text


# ===========================================================================
# Half B — Ordering & aggregation invariants (offset comparison + grep)
# ===========================================================================


class TestOrderingAndAggregationInvariants:
    # (f) Step 3.5 / roster-check precedes Step 4 -------------------------

    def test_roster_check_precedes_step_4(self, skill_text: str, seam_text: str) -> None:
        # The gate hook (membership-gate CLI) must be invoked before Step 4;
        # the concrete critique-roster-check CLI is declared in the seam.
        assert skill_text.index("membership-gate CLI") < skill_text.index("### Step 4")
        assert skill_text.index("### Step 3.5") < skill_text.index("### Step 4")
        assert "critique-roster-check" in seam_text

    # (g) Verdict-record gated on complete: true OR CRITIQUE INCOMPLETE ----

    def test_gating_block_states_complete_gate_and_incomplete_fallback(
        self, skill_text: str
    ) -> None:
        # The gating block lives between Step 3.5 and Step 5.5.
        region = _section(skill_text, "### Step 3.5", "### Step 5.5")
        assert "complete: true" in region
        assert "CRITIQUE INCOMPLETE" in region

    # (h) Step 4 iterates the manifest, not present-files-only ------------

    def test_step_4_iterates_every_roster_member(self, skill_text: str) -> None:
        step4 = _section(skill_text, "### Step 4", "### Step 5")
        # Positive: the manifest-iteration instruction must be present. Accept
        # either "every roster member" phrasing or an "iterate" + "_roster.json"
        # combination.
        manifest_iteration = ("every roster member" in step4.lower()) or (
            "iterate" in step4.lower() and "_roster.json" in step4
        )
        assert manifest_iteration, (
            "Step 4 must instruct iterating every roster member / the _roster.json manifest"
        )
        # Negative is kept lenient: Task 1 deliberately wrote the NEGATION of the
        # present-files-only phrasing (a warning NOT to do it). We only require
        # that the manifest-iteration instruction is present (asserted above);
        # we do not fail on the warning sentence. As a sanity check, ensure the
        # manifest itself is referenced in the section.
        assert "_roster.json" in step4

    # (i) Re-dispatch block carries no run_in_background SPAWN DIRECTIVE ---

    def test_redispatch_block_forbids_background_flag(self, skill_text: str) -> None:
        # Same nuance as (a): the prohibition note legitimately quotes the token.
        # Robust choice (documented): assert the Step 3.5 section explicitly
        # FORBIDS the flag — i.e. "never"/"NEVER" appears near the token — rather
        # than trying to prove the literal token is absent (it is present, inside
        # the prohibition note). A section that forbids the flag cannot also be
        # directing its use.
        step35 = _section(skill_text, "### Step 3.5", "### Step 4")
        assert "run_in_background" in step35
        idx = step35.index("run_in_background")
        window = step35[max(0, idx - 120) : idx + 120].lower()
        assert "never" in window, (
            "Step 3.5 must explicitly forbid run_in_background in the re-dispatch block"
        )


# ===========================================================================
# Half C — Helper behavior against synthetic run dirs
# ===========================================================================


def _write_roster(run_dir: Path, roster: list[str]) -> None:
    (run_dir / "_roster.json").write_text(
        json.dumps({"roster": roster, "count": len(roster)}), encoding="utf-8"
    )


def _write_complete_result(run_dir: Path, name: str, body: str = "No findings.") -> None:
    """Write a {name}.result.md with a proper terminal two-line fence."""
    content = f"{body}\n\n{FENCE_DELIMITER}\n{FENCE_STATUS}\n"
    (run_dir / f"{name}.result.md").write_text(content, encoding="utf-8")


class TestHelperBehavior:
    def test_complete_roster_passes(self, tmp_path: Path) -> None:
        roster = ["Skeptic", "Operator", "Adversary"]
        _write_roster(tmp_path, roster)
        for name in roster:
            _write_complete_result(tmp_path, name)
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is True
        assert exit_code == 0
        assert decision["missing"] == []
        assert decision["roster_count"] == 3
        assert decision["completed_count"] == 3

    def test_missing_file_is_incomplete(self, tmp_path: Path) -> None:
        roster = ["Skeptic", "Operator", "Adversary"]
        _write_roster(tmp_path, roster)
        # Write N-1 files; omit "Adversary".
        _write_complete_result(tmp_path, "Skeptic")
        _write_complete_result(tmp_path, "Operator")
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert "Adversary" in decision["missing"]

    def test_fence_on_line_1_only_truncated_after_is_not_complete(self, tmp_path: Path) -> None:
        # NEW-B1 truncation guard: fence on line 1, then a truncated/empty body.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        # Fence at the TOP, body after — the terminal-fence check must reject it.
        content = f"{FENCE_DELIMITER}\n{FENCE_STATUS}\nSEVERITY: BLOCKER\nLOCATION: somewhere\n"
        (tmp_path / "Skeptic.result.md").write_text(content, encoding="utf-8")
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert "Skeptic" in decision["missing"]

    def test_bare_status_completed_without_delimiter_is_not_complete(self, tmp_path: Path) -> None:
        # NEW-B1b token-collision guard: body ends on a quoted bare STATUS line,
        # with NO preceding delimiter. Must NOT count as complete.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        content = (
            "SEVERITY: NIT\n"
            'LOCATION: the plan quotes "STATUS: COMPLETED" in prose\n'
            "FINDING: a critic reviewing text often ends on the bare token\n"
            f"{FENCE_STATUS}\n"
        )
        (tmp_path / "Skeptic.result.md").write_text(content, encoding="utf-8")
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert "Skeptic" in decision["missing"]

    def test_present_but_no_fence_is_not_complete(self, tmp_path: Path) -> None:
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        (tmp_path / "Skeptic.result.md").write_text(
            "No findings.\nSome trailing prose with no fence.\n", encoding="utf-8"
        )
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert "Skeptic" in decision["missing"]

    def test_no_findings_body_with_fence_is_complete(self, tmp_path: Path) -> None:
        # completed-empty disambiguation: "No findings." + proper fence ⇒ complete.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        _write_complete_result(tmp_path, "Skeptic", body="No findings.")
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is True
        assert exit_code == 0
        assert decision["present"] == ["Skeptic"]

    def test_under_dispatch_seven_five(self, tmp_path: Path) -> None:
        roster = [
            "Skeptic",
            "Operator",
            "Archaeologist",
            "Adversary",
            "Simplifier",
            "User",
            "Consistency Auditor",
        ]
        _write_roster(tmp_path, roster)
        # Only 5 of 7 result files.
        for name in roster[:5]:
            _write_complete_result(tmp_path, name)
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert decision["roster_count"] == 7
        assert decision["completed_count"] == 5
        assert set(decision["missing"]) == {"User", "Consistency Auditor"}

    def test_stray_tmp_file_is_ignored(self, tmp_path: Path) -> None:
        # A stray {name}.result.md.tmp (no canonical .result.md) ⇒ NOT complete.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        content = f"No findings.\n\n{FENCE_DELIMITER}\n{FENCE_STATUS}\n"
        (tmp_path / "Skeptic.result.md.tmp").write_text(content, encoding="utf-8")
        # No canonical Skeptic.result.md exists.
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert "Skeptic" in decision["missing"]

    def test_missing_manifest_is_error_not_complete(self, tmp_path: Path) -> None:
        # No _roster.json at all.
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert decision.get("error")

    def test_unparseable_manifest_is_error_not_complete(self, tmp_path: Path) -> None:
        (tmp_path / "_roster.json").write_text("{not valid json", encoding="utf-8")
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert exit_code != 0
        assert decision.get("error")

    def test_empty_roster_is_not_vacuously_complete(self, tmp_path: Path) -> None:
        _write_roster(tmp_path, [])
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is False
        assert decision["roster_count"] == 0
        assert exit_code != 0

    def test_cli_main_prints_json_and_returns_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Exercise the CLI entry too: complete roster ⇒ exit 0 + parseable JSON.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        _write_complete_result(tmp_path, "Skeptic")
        rc = crc.main(["--run-dir", str(tmp_path)])
        out = capsys.readouterr().out
        decision = json.loads(out)
        assert rc == 0
        assert decision["complete"] is True


# ===========================================================================
# Half D: Grounding leg (WS-A, issue #2124)
#
# When evaluate() is given a plan (via plan_path or plan_text), a member is
# complete only if it passes BOTH the terminal fence AND cites the real plan.
# A fabricated critique of a nonexistent plan carries no substring colliding
# with the real plan bytes -> treated as an incomplete (ungrounded) member.
# Omitting the plan is byte-identical to the legacy fence-only gate.
# ===========================================================================

_PLAN_TEXT = (
    "# SDLC Fork Artifact-Grounding Guards\n"
    "## Solution\n"
    "Extend critique_roster_check.evaluate() with an optional plan_path parameter "
    "and a per-member grounding leg. A result with zero verifiable citations is "
    "treated exactly like a missing critic.\n"
)


class TestGroundingLeg:
    def test_grounded_verbatim_quote_passes(self, tmp_path: Path) -> None:
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        body = (
            'GROUNDING: "Extend critique_roster_check.evaluate() with an '
            'optional plan_path parameter"\nNo findings.'
        )
        _write_complete_result(tmp_path, "Skeptic", body=body)
        decision, exit_code = crc.evaluate(str(tmp_path), plan_text=_PLAN_TEXT)
        assert decision["complete"] is True
        assert exit_code == 0
        assert decision["ungrounded"] == []

    def test_grounded_section_header_passes(self, tmp_path: Path) -> None:
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        body = "GROUNDING: I read the ## Solution section.\nNo findings."
        _write_complete_result(tmp_path, "Skeptic", body=body)
        decision, exit_code = crc.evaluate(str(tmp_path), plan_text=_PLAN_TEXT)
        assert decision["complete"] is True
        assert exit_code == 0

    def test_fenced_but_ungrounded_is_incomplete(self, tmp_path: Path) -> None:
        # A fabricated critique of a DIFFERENT plan: fence present, but nothing
        # collides with the real plan bytes.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        body = (
            "SEVERITY: BLOCKER\n"
            "LOCATION: Rocket telemetry section\n"
            "FINDING: the launch sequence lacks abort criteria.\n"
        )
        _write_complete_result(tmp_path, "Skeptic", body=body)
        decision, exit_code = crc.evaluate(str(tmp_path), plan_text=_PLAN_TEXT)
        assert decision["complete"] is False
        assert exit_code != 0
        assert "Skeptic" in decision["missing"]
        assert "Skeptic" in decision["ungrounded"]

    def test_fence_alone_does_not_ground(self, tmp_path: Path) -> None:
        # The terminal fence lines must be stripped before grounding — the fence
        # itself must never count as a plan citation.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        # Body that shares nothing with the plan except (implicitly) the fence.
        _write_complete_result(tmp_path, "Skeptic", body="Totally unrelated prose here.")
        decision, exit_code = crc.evaluate(str(tmp_path), plan_text=_PLAN_TEXT)
        assert decision["complete"] is False
        assert "Skeptic" in decision["ungrounded"]

    def test_plan_path_omitted_is_legacy_fence_only(self, tmp_path: Path) -> None:
        # No plan supplied -> byte-identical to the pre-#2124 gate: fenced-only
        # results pass, and there is NO 'ungrounded' key in the decision.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        _write_complete_result(tmp_path, "Skeptic", body="Unrelated prose, no citation.")
        decision, exit_code = crc.evaluate(str(tmp_path))
        assert decision["complete"] is True
        assert exit_code == 0
        assert "ungrounded" not in decision

    def test_unreadable_plan_path_fails_closed(self, tmp_path: Path) -> None:
        # A plan_path that cannot be read -> empty plan text -> every member
        # ungrounded (refusal direction), never a false 'complete'.
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        _write_complete_result(tmp_path, "Skeptic", body="GROUNDING: anything at all.")
        missing_plan = str(tmp_path / "does_not_exist.md")
        decision, exit_code = crc.evaluate(str(tmp_path), plan_path=missing_plan)
        assert decision["complete"] is False
        assert exit_code != 0
        assert "Skeptic" in decision["ungrounded"]

    def test_cli_accepts_plan_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        roster = ["Skeptic"]
        _write_roster(tmp_path, roster)
        _write_complete_result(
            tmp_path,
            "Skeptic",
            body='GROUNDING: "an optional plan_path parameter"\nNo findings.',
        )
        plan_file = tmp_path / "plan.md"
        plan_file.write_text(_PLAN_TEXT, encoding="utf-8")
        rc = crc.main(["--run-dir", str(tmp_path), "--plan-path", str(plan_file)])
        decision = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert decision["complete"] is True
