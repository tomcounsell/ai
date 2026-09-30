"""FEATURE_MAP marker-regression guard (#3010).

Fails when a test file's FEATURE_MAP marker does not resolve as intended, so a
mistag surfaces as a red check on the pull request that introduces it rather
than as coverage that quietly stopped running. See
docs/features/feature-map-marker-guard.md for the mistag mechanisms (two live;
the mangled stem was fixed by #3184) and what the three rules below can and
cannot see.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from tests.marker_map import (
    FEATURE_MAP,
    KNOWN_MISTAGS,
    _partition_packages,
    _stem,
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


def test_known_mistags_holds_only_policy_entries():
    """#3175 acceptance criterion 1, asserted rather than asserted-in-prose.

    The baseline was drained from 24 entries to 2. The issue allows a non-empty
    baseline only when "every remaining entry has a reason that is a deliberate
    policy choice rather than an unaddressed defect", so this pins the marker
    that says so. An entry deferring its own fix -- the "Drain tracked by #N"
    shape the drained entries carried -- fails here, which is the point: the
    next person to add an exemption has to state a policy or not add it.
    """
    missing = sorted(path for path, reason in KNOWN_MISTAGS.items() if "POLICY" not in reason)
    assert not missing, (
        "KNOWN_MISTAGS entrie(s) whose reason is not marked POLICY: "
        f"{missing}. An exemption is a stated policy choice, not a deferral."
    )


def test_checkpointing_key_position_is_free():
    """`checkpointing` and `checkpoint` both map to `validation`, so order cannot matter.

    R3 compares `resolve_marker` against `resolve_marker_whole_token`. Both keys
    carry the same value, so whichever wins the first-hit scan the two resolvers
    return the same marker and R3 stays silent. This is what makes the key safe
    to add without re-introducing the ordering sensitivity #3010 exists to
    catch -- it outranks nothing. Asserted by rebuilding FEATURE_MAP with the
    key at each end and checking the resolution is identical both ways.
    """
    assert FEATURE_MAP["checkpointing"] == FEATURE_MAP["checkpoint"] == "validation"

    basename = "test_long_task_checkpointing.py"
    front = {
        "checkpointing": "validation",
        **{k: v for k, v in FEATURE_MAP.items() if k != "checkpointing"},
    }
    back = {
        **{k: v for k, v in FEATURE_MAP.items() if k != "checkpointing"},
        "checkpointing": "validation",
    }

    for label, ordering in (("front", front), ("back", back)):
        with patch("tests.marker_map.FEATURE_MAP", ordering):
            assert resolve_marker(basename)[0] == "validation", label
            assert resolve_marker_whole_token(basename)[0] == "validation", label
            assert check_r3([basename]) == [], label


# ---------------------------------------------------------------------------
# Stem fidelity: these pin the anchored strip shipped by #3184 -- `test_` comes
# off the front only and `.py` off the end only, so a basename carrying
# `test_` a second time keeps it. All five tracked basenames of that shape are
# covered. The two below resolve to a FEATURE_MAP key written for them; the
# three further down resolve to no marker either way, so they assert `_stem`
# directly rather than a vacuously-green `resolve_marker` tuple.
# ---------------------------------------------------------------------------


def test_stem_fidelity_test_judge():
    assert resolve_marker("test_test_judge.py") == ("tools", "test_judge")


def test_stem_fidelity_validate_test_impact():
    assert resolve_marker("test_validate_test_impact.py") == ("validation", "validate_test_impact")


def test_stem_unmangled_conftest_autouse_monkeypatch_order():
    assert _stem("test_conftest_autouse_monkeypatch_order.py") == (
        "conftest_autouse_monkeypatch_order"
    )


def test_stem_unmangled_conftest_isolation_guards():
    assert _stem("test_conftest_isolation_guards.py") == "conftest_isolation_guards"


def test_stem_unmangled_test_redis_server_resolution():
    assert _stem("test_test_redis_server_resolution.py") == "test_redis_server_resolution"


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
    # "config" is a prefix of "configured" -- a genuine fragment match, not a
    # whole-token match. This synthetic used to ride on "checkpoint" inside
    # "checkpointing"; #3175 cleared that one by adding a "checkpointing" key
    # of equal value, so the fixture moved to a fragment that is still live.
    synthetic_files = ["tests/unit/test_synthetic_slots_configured.py"]
    violations = check_r3(synthetic_files)
    assert len(violations) == 1
    (v,) = violations
    assert v["rule"] == "R3"
    assert v["resolved"] == "config"
    assert v["key"] == "config"


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
    # "checkpointing". Since the explicit "checkpointing" key was added, this
    # stem does get a whole-token hit, so what the assertion proves is narrower
    # than "nothing matched": under first-hit-wins, the earlier-positioned
    # "checkpoint" would have been the key returned had it matched, and it was
    # not. That rests on "checkpoint" preceding "checkpointing" in FEATURE_MAP,
    # an order test_checkpointing_key_position_is_free deliberately leaves free
    # -- so read this as a fragment-rejection check at the current order, not an
    # order-independent one.
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


# ---------------------------------------------------------------------------
# Package identity: two same-named directories in different trees are two
# packages. Keying the partition on the bare directory name pooled them into
# one group and invented R2 violations for whichever tree fell in the
# minority of the pooled set.
# ---------------------------------------------------------------------------

TWO_HELPERS_PACKAGES = [
    "tests/unit/helpers/test_alpha_sdlc.py",
    "tests/unit/helpers/test_beta_sdlc.py",
    "tests/unit/helpers/test_gamma_sdlc.py",
    "tests/integration/helpers/test_delta_plain.py",
    "tests/integration/helpers/test_epsilon_plain.py",
]


def test_same_named_packages_in_different_trees_stay_separate():
    packages = _partition_packages(TWO_HELPERS_PACKAGES)
    assert set(packages) == {"tests/unit/helpers", "tests/integration/helpers"}
    assert packages["tests/unit/helpers"] == TWO_HELPERS_PACKAGES[:3]
    assert packages["tests/integration/helpers"] == TWO_HELPERS_PACKAGES[3:]


def test_r2_does_not_merge_same_named_packages():
    """Each `helpers/` package is internally uniform, so R2 has nothing to say.

    Pooled on the shared name they read as 3 "sdlc" against 2 unmarked, and
    the two unmarked files are reported as minority drift that does not exist.
    """
    assert check_r2(TWO_HELPERS_PACKAGES) == []


def test_r1_does_not_merge_same_named_packages():
    """R1 still reads directory intent from the package's own name, not its path."""
    files = [
        "tests/unit/worktree_manager/test_worktree_manager_creation.py",
        "tests/integration/worktree_manager/test_worktree_manager_config.py",
    ]
    violations = check_r1(files)
    assert [v["path"] for v in violations] == [
        "tests/integration/worktree_manager/test_worktree_manager_config.py"
    ]
    assert violations[0]["expected"] == "git"
