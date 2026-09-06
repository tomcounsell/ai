"""FEATURE_MAP marker-regression guard (#3010).

Fails when a test file's FEATURE_MAP marker does not resolve as intended, so a
mistag surfaces as a red check on the pull request that introduces it rather
than as coverage that quietly stopped running. See
docs/features/feature-map-marker-guard.md for the three mistag mechanisms and
what the three rules below can and cannot see.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from tests.marker_map import (
    FEATURE_MAP,
    KNOWN_MISTAGS,
    check_r1,
    check_r2,
    check_r3,
    format_violations,
    iter_test_files,
    resolve_marker,
    resolve_marker_whole_token,
    run_audit,
)


def test_no_violation_outside_known_mistags():
    """The audit's core assertion: no new mistag, and no stale exemption.

    A failure here lists every offending path, its resolved marker, its
    expected marker, and the FEATURE_MAP key responsible, so the remediation
    is: rename the file, reorder or extend FEATURE_MAP, or add a
    KNOWN_MISTAGS entry with a reason.
    """
    violations, new_mistags, stale_exemptions = run_audit()

    if new_mistags:
        new_violations = sorted(
            (v for v in violations if v["path"] in new_mistags),
            key=lambda v: (v["path"], v["rule"]),
        )
        pytest.fail(
            "New FEATURE_MAP mistag(s) not covered by KNOWN_MISTAGS:\n"
            + format_violations(new_violations)
            + "\n\nRemediation: rename the file, reorder or extend FEATURE_MAP, "
            "or add a KNOWN_MISTAGS entry with a reason."
        )

    if stale_exemptions:
        pytest.fail(
            "Stale KNOWN_MISTAGS entrie(s) with no matching violation "
            "(the fix landed but the baseline entry was not deleted): "
            + ", ".join(sorted(stale_exemptions))
        )


def test_known_mistags_are_all_tracked_paths():
    tracked = set(iter_test_files())
    bad = sorted(path for path in KNOWN_MISTAGS if path not in tracked)
    assert not bad, f"KNOWN_MISTAGS references untracked path(s): {bad}"


def test_known_mistags_all_carry_a_prose_reason():
    bad = sorted(
        path
        for path, reason in KNOWN_MISTAGS.items()
        if not isinstance(reason, str) or len(reason.strip()) < 20
    )
    assert not bad, f"KNOWN_MISTAGS entrie(s) missing a real prose reason: {bad}"


# ---------------------------------------------------------------------------
# Stem fidelity: the two files that move if the stem expression is
# re-derived (removeprefix/removesuffix/anchored regex) instead of copied
# verbatim. These are the cheapest possible tripwire for mechanism 3 (#3184).
# ---------------------------------------------------------------------------


def test_stem_fidelity_test_judge():
    assert resolve_marker("test_test_judge.py") == (None, None)


def test_stem_fidelity_validate_test_impact():
    assert resolve_marker("test_validate_test_impact.py") == (None, None)


def test_youtube_transcription_retagged_to_tools():
    assert resolve_marker("test_youtube_transcription.py") == ("tools", "youtube")


# ---------------------------------------------------------------------------
# Empty / invalid input handling
# ---------------------------------------------------------------------------


def test_resolve_marker_empty_string():
    assert resolve_marker("") == (None, None)


def test_resolve_marker_test_dot_py():
    assert resolve_marker("test_.py") == (None, None)


def test_resolve_marker_no_underscores():
    # A basename with no underscores that happens to equal a FEATURE_MAP key.
    assert resolve_marker("test_sdlc.py") == ("sdlc", "sdlc")


def test_resolve_marker_exact_key_match():
    assert resolve_marker("test_config.py") == ("config", "config")


def test_feature_map_is_a_non_empty_dict():
    assert isinstance(FEATURE_MAP, dict)
    assert len(FEATURE_MAP) > 0


def test_empty_feature_map_raises():
    with patch("tests.marker_map.FEATURE_MAP", {}):
        with pytest.raises(RuntimeError):
            run_audit()


def test_empty_file_enumeration_raises():
    with patch("tests.marker_map.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with pytest.raises(RuntimeError):
            iter_test_files()


# ---------------------------------------------------------------------------
# Synthetic mistag proofs -- one per rule. Without these, the audit could
# pass while reaching nothing: a green real-world audit alone proves only
# that today's tree happens not to trip it, not that the rule functions work.
# ---------------------------------------------------------------------------


def test_audit_reports_a_synthetic_mistag_r1():
    """R1: a file whose package name resolves, but whose basename disagrees.

    Reuses spike-1's headline ordering-collision example (not a real tracked
    file): ``worktree_manager`` sits at insertion index 63, after the more
    generic ``config`` at index 45, so a file named this way inside the
    ``worktree_manager`` package resolves to "config" instead of the
    package's own "git".
    """
    synthetic_files = [
        "tests/unit/worktree_manager/test_worktree_manager_creation.py",
        "tests/unit/worktree_manager/test_worktree_manager_config.py",
    ]
    violations = check_r1(synthetic_files)
    paths = {v["path"] for v in violations}
    assert "tests/unit/worktree_manager/test_worktree_manager_config.py" in paths
    (v,) = [v for v in violations if v["path"].endswith("_config.py")]
    assert v["rule"] == "R1"
    assert v["expected"] == "git"
    assert v["resolved"] == "config"


def test_audit_reports_a_synthetic_mistag_r2():
    """R2: a package with no directory-level intent, one sibling drifts."""
    synthetic_files = [
        "tests/unit/synthetic_pkg/test_alpha_synthetic.py",
        "tests/unit/synthetic_pkg/test_beta_synthetic.py",
        "tests/unit/synthetic_pkg/test_gamma_synthetic.py",
        "tests/unit/synthetic_pkg/test_delta_sdlc.py",  # lone sibling: resolves to "sdlc"
    ]
    violations = check_r2(synthetic_files)
    assert len(violations) == 1
    (v,) = violations
    assert v["path"] == "tests/unit/synthetic_pkg/test_delta_sdlc.py"
    assert v["rule"] == "R2"
    assert v["resolved"] == "sdlc"
    assert v["expected"] is None  # majority of the other 3 all resolve to no marker


def test_audit_reports_a_synthetic_mistag_r3():
    """R3: a fragment match with no package signal to contradict it."""
    # "checkpoint" is a prefix of "checkpointing" -- a genuine fragment match,
    # not a whole-token match.
    synthetic_files = ["tests/unit/test_long_task_checkpointing_synthetic.py"]
    violations = check_r3(synthetic_files)
    assert len(violations) == 1
    (v,) = violations
    assert v["rule"] == "R3"
    assert v["resolved"] == "validation"
    assert v["key"] == "checkpoint"


def test_audit_reports_stale_exemption():
    """A KNOWN_MISTAGS entry with no matching violation fails the guard."""
    synthetic_files = [
        "tests/unit/some_pkg/test_only_file.py",  # single file, R2 trivially passes
    ]
    stale_baseline = {
        "tests/unit/some_pkg/test_only_file.py": "synthetic stale exemption for this test"
    }
    with patch("tests.marker_map.iter_test_files", return_value=synthetic_files):
        with patch("tests.marker_map.KNOWN_MISTAGS", stale_baseline):
            _violations, new_mistags, stale_exemptions = run_audit()
    assert not new_mistags
    assert stale_exemptions == {"tests/unit/some_pkg/test_only_file.py"}


def test_whole_token_single_token_stem_matches():
    # The single-token case is the boundary where a naive tokenizer goes
    # wrong: the whole stem IS the key, with no surrounding tokens.
    assert resolve_marker_whole_token("test_sdlc.py") == ("sdlc", "sdlc")


def test_whole_token_rejects_fragment_at_single_token():
    # "checkpoint" must not whole-token-match inside the single token
    # "checkpointing".
    assert resolve_marker_whole_token("test_checkpointing.py")[1] != "checkpoint"


def test_r2_single_file_package_passes_trivially():
    violations = check_r2(["tests/unit/lonely_pkg/test_only_one.py"])
    assert violations == []


# ---------------------------------------------------------------------------
# R2's tie branch: a 2-vs-2 package must report every file as ambiguous,
# order-independently. Task 4's real-package mutation only ever exercises
# 1-vs-N, so this branch has no coverage without a synthetic fixture.
# ---------------------------------------------------------------------------


def test_r2_tie_reports_every_file_as_ambiguous():
    synthetic_files = [
        "tests/unit/tie_pkg/test_one_sdlc.py",  # resolves "sdlc"
        "tests/unit/tie_pkg/test_two_sdlc_route.py",  # resolves "sdlc" (2 total)
        "tests/unit/tie_pkg/test_three_plain.py",  # resolves None
        "tests/unit/tie_pkg/test_four_plain.py",  # resolves None (2 total: a tie)
    ]
    violations = check_r2(synthetic_files)
    paths = {v["path"] for v in violations}
    assert paths == set(synthetic_files)
    assert all(v["rule"] == "R2-ambiguous" for v in violations)


def test_r2_tie_is_order_independent():
    synthetic_files = [
        "tests/unit/tie_pkg/test_one_sdlc.py",
        "tests/unit/tie_pkg/test_two_sdlc_route.py",
        "tests/unit/tie_pkg/test_three_plain.py",
        "tests/unit/tie_pkg/test_four_plain.py",
    ]
    forward = {v["path"] for v in check_r2(synthetic_files)}
    reversed_files = list(reversed(synthetic_files)) + list(reversed(synthetic_files))
    backward = {v["path"] for v in check_r2(reversed_files)}
    assert forward == backward == set(synthetic_files)
