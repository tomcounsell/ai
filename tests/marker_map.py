"""Single source of truth for pytest FEATURE_MAP marker resolution.

`tests/conftest.py::pytest_collection_modifyitems` auto-applies a feature marker
to every collected test by taking the module basename, stripping ``test_`` and
``.py``, and substring-matching the remainder against ``FEATURE_MAP`` in
insertion order, first hit wins. That coupling is silent: a test file can be
renamed, moved, or split and land under the wrong marker (or none) with the
collection total unchanged and the suite staying green.

This module is the single home of ``FEATURE_MAP`` and ``resolve_marker()`` so
Path A (marker assignment, at collection time, in ``tests/conftest.py``) and
Path B (the guard, at test time, in
``tests/unit/test_feature_map_markers.py``) call the *same* function. If they
diverged into two implementations, the guard could drift away from the thing
it is guarding.

It must stay import-light (standard library only, no ``pytest`` import) so it
runs on a bare interpreter with no venv:

    python tests/marker_map.py --audit
    python tests/marker_map.py --report
    python tests/marker_map.py --count

See docs/features/feature-map-marker-guard.md for the three mistag mechanisms,
what the three rules below can and cannot see, and how to respond when the
guard goes red.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# FEATURE_MAP -- moved verbatim from tests/conftest.py, plus two measured,
# marker-count-neutral additions (see docs/features/feature-map-marker-guard.md):
#
#   - "reflections": "reflections" inserted immediately before "reflection".
#     Clears 13 benign substring divergences where the plural spelling
#     ("test_reflections_main.py" and friends) matched "reflection" as a
#     fragment; both keys map to the same marker so no file's marker changes.
#   - "youtube": "tools" inserted immediately before "transcript". Corrects
#     test_youtube_transcription.py, which was matching "transcript" as a
#     fragment of "transcription" and landing on "messaging" instead of
#     "tools". This is the one intended marker change in this plan.
# ---------------------------------------------------------------------------
FEATURE_MAP: dict[str, str] = {
    "bridge": "messaging",
    "messenger": "messaging",
    "telegram": "messaging",
    "duplicate_delivery": "messaging",
    "youtube": "tools",
    "transcript": "messaging",
    "dedup": "messaging",
    "markdown": "messaging",
    "media_handling": "messaging",
    "routing": "messaging",
    "pm_channels": "messaging",
    "unthreaded": "messaging",
    "file_extraction": "messaging",
    "message_pipeline": "messaging",
    "reply_delivery": "messaging",
    "pipeline": "sdlc",
    "sdlc": "sdlc",
    "observer": "sdlc",
    "stop_hook": "sdlc",
    "stop_reason": "sdlc",
    "post_tool_use": "sdlc",
    "pre_tool_use": "sdlc",
    "skill_outcome": "sdlc",
    "skills_audit": "sdlc",
    "steering": "sdlc",
    "cross_repo_build": "sdlc",
    "session_status": "sessions",
    "session_stuck": "sessions",
    "session_watchdog": "sessions",
    "stall_detection": "sessions",
    "pending_stall": "sessions",
    "pending_recovery": "sessions",
    "escape_hatch": "sessions",
    "lifecycle": "sessions",
    "session_continuity": "sessions",
    "goal_gates": "sessions",
    "open_question": "sessions",
    "agent_session": "sessions",
    # Execution-fence family (#2494 / #2518): the (pid, create_time) identity
    # guard and the reapers that consume it. Placed after "agent_session" so
    # ``agent_session_*`` filenames keep their existing marker.
    "fence": "sessions",
    "orphan_reap": "sessions",
    "agent_session_hierarchy": "jobs",
    "agent_session_scheduler": "jobs",
    "agent_session_queue": "jobs",
    "agent_session_health": "jobs",
    "enqueue": "jobs",
    "reflections": "reflections",
    "reflection": "reflections",
    "config": "config",
    "context_modes": "context",
    "session_tags": "context",
    "auto_continue": "classifiers",
    "intake_classifier": "classifiers",
    "work_request_classifier": "classifiers",
    "message_quality": "classifiers",
    "stage_aware_auto_continue": "classifiers",
    "validate_commit": "validation",
    "validate_verification": "validation",
    "validate_test_impact": "validation",
    "validate_sdlc": "validation",
    "verification_parser": "validation",
    "features_readme": "validation",
    "build_validation": "validation",
    "checkpoint": "validation",
    "docs_auditor": "validation",
    "branch_manager": "git",
    "worktree_manager": "git",
    "git_state": "git",
    "workspace_safety": "git",
    "symlinks": "git",
    "sdk_client": "sdk",
    "sdk_permissions": "sdk",
    "workflow_sdk": "sdk",
    "code_impact": "impact",
    "doc_impact": "impact",
    "cross_repo_gh": "impact",
    "cross_wire": "impact",
    "model_relationships": "models",
    "redis_models": "models",
    "summarizer": "summarizer",
    "telemetry": "monitoring",
    "health_check": "monitoring",
    "bridge_watchdog": "monitoring",
    "connectivity": "monitoring",
    "silent_failures": "monitoring",
    "remote_update": "config",
    "benchmarks": "monitoring",
    "classifier": "classifiers",
    "code_execution": "tools",
    "link_analysis": "tools",
    "doc_summary": "tools",
    "image_analysis": "tools",
    "search": "tools",
    "test_judge": "tools",
    "ai_judge": "tools",
    "telegram_history": "tools",
}

# Parent directory names that mean "not a themed package" -- a test file
# directly under one of these carries no directory-level declaration of
# intent, so it is covered by R3 alone (see docs/features/feature-map-marker-guard.md
# for the 9.6%-of-the-suite coverage boundary this implies).
KNOWN_ROOT_DIRS = ("tests", "unit", "integration", "e2e", "tools", "performance", "ai_judge")

# Path-keyed exemptions, the only exemption mechanism (#2805: never key by
# line number, index, or ordinal position -- a line-keyed ALLOWLIST silently
# un-exempted call sites on unrelated merges). Every value is a prose reason;
# a bare path with no reason is not an acceptable entry. There is deliberately
# no whole-package exemption mechanism: the measured baseline needs none, and
# an unbracketed, unmeasured exemption keyed on an entire directory would be a
# silent hole of exactly the kind #3031 warns about. Draining this baseline is
# #3175; it can only shrink, never grow -- rule 2 below forces a stale entry
# (one whose violation was fixed without deleting the entry) to fail loudly
# rather than rot.
KNOWN_MISTAGS: dict[str, str] = {
    # R1 (directory intent): tests/unit/reflections/ resolves to "reflections"
    # via its own name, but these 18 basenames resolve to no marker at all
    # (or, for the *_configured.py and *_sdlc_*.py files below, to a wrong
    # one) because nothing in the basename matches a FEATURE_MAP key that
    # points at "reflections". Tracked for drain by #3175.
    "tests/unit/reflections/test_daily_log_aggregator.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_daily_log_audio_guard.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_daily_log_renderer.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_docs_auditor_git_surface.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to 'validation' via 'docs_auditor'. Drain tracked by #3175.",
    "tests/unit/reflections/test_expectation_reconciler.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_log_audit_sentry.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_merged_branch_cleanup.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_builder.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_collector.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_delivery.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_init.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_machine_gate.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_no_slots_configured.py": "R1 and R3: package resolves to 'reflections'; basename fragment-matches 'config' inside 'configured'. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_skip_when_empty.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_pm_briefings_slot_match.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/unit/reflections/test_sdlc_progress_check.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to 'sdlc' ('sdlc' sits ahead of 'reflection' in insertion order). Drain tracked by #3175.",
    "tests/unit/reflections/test_sdlc_upvote_lanes.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to 'sdlc' ('sdlc' sits ahead of 'reflection' in insertion order). Drain tracked by #3175.",
    "tests/unit/reflections/test_utilities_resolve_eng_group.py": "R1: package tests/unit/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    # R1: tests/integration/reflections/ resolves to "reflections" the same way.
    "tests/integration/reflections/test_pm_briefings_dispatch.py": "R1: package tests/integration/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    "tests/integration/reflections/test_pm_briefings_e2e.py": "R1: package tests/integration/reflections/ resolves to 'reflections'; basename resolves to no marker. Drain tracked by #3175.",
    # R1: tests/unit/bridge/ resolves to "messaging" via "bridge"; this file's
    # basename resolves to no marker.
    "tests/unit/bridge/test_dispatch.py": "R1: package tests/unit/bridge/ resolves to 'messaging'; basename resolves to no marker. Drain tracked by #3175.",
    # R2 (sibling uniformity): the package directory name does not itself
    # resolve, so there is no directory-level intent to compare against, but
    # each of these is the lone sibling that drifted away from its package's
    # majority marker (17 None vs 1 "messaging" in session_runner/, 4 None
    # vs 1 "sdlc" in hooks/).
    "tests/unit/session_runner/test_schema_routing.py": "R2: lone sibling (1 of 18) resolving to 'messaging' via 'routing', against 17 unmarked siblings in tests/unit/session_runner/, which does not itself resolve. Drain tracked by #3175.",
    "tests/unit/hooks/test_pre_tool_use_foreground_subagents.py": "R2: lone sibling (1 of 5) resolving to 'sdlc' via 'pre_tool_use', against 4 unmarked siblings in tests/unit/hooks/, which does not itself resolve. Drain tracked by #3175.",
    # R3 (whole-token match): a genuine fragment match with no package
    # signal to contradict it. "checkpoint" is a prefix of "checkpointing".
    "tests/unit/test_long_task_checkpointing.py": "R3: fragment match, 'checkpoint' matches inside 'checkpointing' rather than as a whole token; resolves to 'validation'. Drain tracked by #3175.",
}


def resolve_marker(basename: str) -> tuple[str | None, str | None]:
    """Resolve one test module basename to (marker, matched FEATURE_MAP key).

    This is Path A and Path B's single point of truth: the pytest collection
    hook and this guard both call this function, so they cannot disagree.
    """
    # Global str.replace of *every* occurrence, not a leading-prefix strip --
    # copied verbatim from the shipped hook
    # (tests/conftest.py::pytest_collection_modifyitems). It mangles the five
    # basenames containing "test_" twice (tracked as #3184, e.g.
    # test_test_judge.py -> "judge" instead of "test_judge"). Do NOT
    # "clean this up" with a prefix/suffix-stripping helper or a pattern
    # substitution: each of those retags tests/tools/test_test_judge.py and
    # tests/unit/test_validate_test_impact.py, silently gaining them a
    # marker and breaking the byte-identical-behavior requirement.
    stem = basename.replace("test_", "").replace(".py", "")
    for pattern, marker_name in FEATURE_MAP.items():
        if pattern in stem:
            return marker_name, pattern
    return None, None


def _whole_token_match(pattern: str, stem: str) -> bool:
    """True when `pattern` appears in `stem` as a contiguous run of `_`-tokens."""
    tokens = stem.split("_")
    pattern_tokens = pattern.split("_")
    n = len(pattern_tokens)
    if n == 0 or n > len(tokens):
        return False
    return any(tokens[i : i + n] == pattern_tokens for i in range(len(tokens) - n + 1))


def resolve_marker_whole_token(basename: str) -> tuple[str | None, str | None]:
    """Same resolution as `resolve_marker`, but requiring a whole-token match.

    Used only by rule R3 to detect a fragment match (a pattern that matched as
    a bare substring inside a longer word rather than as its own token).
    """
    stem = basename.replace("test_", "").replace(".py", "")
    for pattern, marker_name in FEATURE_MAP.items():
        if _whole_token_match(pattern, stem):
            return marker_name, pattern
    return None, None


def iter_test_files() -> list[str]:
    """Enumerate tracked test files from the git index, not the filesystem.

    Reading from `git ls-files` means an untracked scratch file is invisible
    and a deleted file cannot leave a stale expectation. Raises when the
    enumeration is empty, because an empty population would make every rule
    below pass vacuously -- the worst possible failure for a guard.
    """
    result = subprocess.run(
        ["git", "ls-files", "tests/**/test_*.py", "tests/test_*.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    files = sorted(set(result.stdout.split()))
    if not files:
        raise RuntimeError(
            "git ls-files returned zero test files; the audit is reaching nothing "
            "(wrong cwd, or a bare checkout with no test files tracked)"
        )
    return files


def _partition_packages(files: list[str]) -> dict[str, list[str]]:
    """Group files by parent directory, excluding KNOWN_ROOT_DIRS parents."""
    packages: dict[str, list[str]] = {}
    for path in files:
        parts = Path(path).parts
        if len(parts) < 2:
            continue
        parent = parts[-2]
        if parent in KNOWN_ROOT_DIRS:
            continue
        packages.setdefault(parent, []).append(path)
    return packages


def check_r1(files: list[str]) -> list[dict[str, str | None]]:
    """Directory intent: a package whose own name resolves declares that marker."""
    violations: list[dict[str, str | None]] = []
    for pkg_name, paths in _partition_packages(files).items():
        dir_marker, _dir_key = resolve_marker(pkg_name)
        if dir_marker is None:
            continue  # no declared intent; R2's territory, not R1's
        for path in paths:
            marker, key = resolve_marker(Path(path).name)
            if marker != dir_marker:
                violations.append(
                    {"path": path, "resolved": marker, "expected": dir_marker, "key": key, "rule": "R1"}
                )
    return violations


def check_r2(files: list[str]) -> list[dict[str, str | None]]:
    """Sibling uniformity: a package whose name does not resolve must be internally uniform.

    The violating set is every sibling outside the largest marker group (the
    majority is the package's de facto intent). On a tie for largest, every
    file in the package is reported, labelled ambiguous, rather than letting
    dict-iteration order silently pick a side.
    """
    violations: list[dict[str, str | None]] = []
    for pkg_name, paths in _partition_packages(files).items():
        dir_marker, _dir_key = resolve_marker(pkg_name)
        if dir_marker is not None:
            continue  # R1's territory, not R2's
        if len(paths) <= 1:
            continue  # a single file trivially passes
        groups: dict[str | None, list[str]] = {}
        for path in paths:
            marker, _key = resolve_marker(Path(path).name)
            groups.setdefault(marker, []).append(path)
        max_size = max(len(members) for members in groups.values())
        largest_markers = sorted(
            (marker for marker, members in groups.items() if len(members) == max_size),
            key=lambda m: (m is None, m),
        )
        if len(largest_markers) > 1:
            # Tie for largest: report every file in the package as ambiguous
            # rather than letting dict-iteration order break the tie.
            for path in paths:
                marker, key = resolve_marker(Path(path).name)
                violations.append(
                    {"path": path, "resolved": marker, "expected": None, "key": key, "rule": "R2-ambiguous"}
                )
            continue
        majority_marker = largest_markers[0]
        for marker, members in groups.items():
            if marker == majority_marker:
                continue
            for path in members:
                _majority_dummy, key = resolve_marker(Path(path).name)
                violations.append(
                    {"path": path, "resolved": marker, "expected": majority_marker, "key": key, "rule": "R2"}
                )
    return violations


def check_r3(files: list[str]) -> list[dict[str, str | None]]:
    """Whole-token match: the winning key must be a genuine token, not a fragment.

    Suite-wide, no intent declaration required. Catches the mechanism R1 and
    R2 cannot see, because a fragment match that happens to be a genuine
    whole-token match at the same time is indistinguishable from an ordering
    collision to this rule.
    """
    violations: list[dict[str, str | None]] = []
    for path in files:
        basename = Path(path).name
        substring_marker, substring_key = resolve_marker(basename)
        if substring_marker is None:
            continue
        whole_token_marker, _whole_token_key = resolve_marker_whole_token(basename)
        if substring_marker != whole_token_marker:
            violations.append(
                {
                    "path": path,
                    "resolved": substring_marker,
                    "expected": whole_token_marker,
                    "key": substring_key,
                    "rule": "R3",
                }
            )
    return violations


def run_audit() -> tuple[list[dict[str, str | None]], set[str], set[str]]:
    """Run all three rules and bracket the result against KNOWN_MISTAGS.

    Returns (all_violations, new_mistags, stale_exemptions). `new_mistags` is
    non-empty when a file mistags outside the recorded baseline; `stale_exemptions`
    is non-empty when a baseline entry no longer corresponds to a real violation
    (the #3031 lesson: a stale exemption is a silent hole, so fixing a file must
    demand its baseline entry be deleted).
    """
    if not FEATURE_MAP:
        raise RuntimeError("FEATURE_MAP is empty; the audit is reaching nothing")
    files = iter_test_files()
    violations = check_r1(files) + check_r2(files) + check_r3(files)
    violation_paths = {v["path"] for v in violations}
    baseline_paths = set(KNOWN_MISTAGS)
    new_mistags = violation_paths - baseline_paths
    stale_exemptions = baseline_paths - violation_paths
    return violations, new_mistags, stale_exemptions


def format_violations(violations: list[dict[str, str | None]]) -> str:
    lines = []
    for v in sorted(violations, key=lambda v: (v["path"], v["rule"])):
        lines.append(
            f"{v['path']}: resolved={v['resolved']!r} expected={v['expected']!r} "
            f"key={v['key']!r} rule={v['rule']}"
        )
    return "\n".join(lines)


def _report_lines(files: list[str]) -> list[str]:
    lines = []
    for path in files:
        marker, _key = resolve_marker(Path(path).name)
        lines.append(f"{path}\t{marker if marker is not None else 'NONE'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--audit", action="store_true", help="Exit non-zero on any mistag or stale exemption.")
    group.add_argument("--report", action="store_true", help="Print 'path<TAB>marker' for every tracked test file.")
    group.add_argument("--count", action="store_true", help="Print the number of tracked test files.")
    args = parser.parse_args(argv)

    if args.count:
        print(len(iter_test_files()))
        return 0

    if args.report:
        for line in _report_lines(iter_test_files()):
            print(line)
        return 0

    # --audit
    violations, new_mistags, stale_exemptions = run_audit()
    if not new_mistags and not stale_exemptions:
        print(f"OK: {len(violations)} known, baselined violation(s); 0 new, 0 stale.")
        return 0

    if new_mistags:
        new_violations = [v for v in violations if v["path"] in new_mistags]
        print("New mistag(s) not covered by KNOWN_MISTAGS:")
        print(format_violations(new_violations))
    if stale_exemptions:
        print("Stale KNOWN_MISTAGS entrie(s) with no matching violation (delete them):")
        for path in sorted(stale_exemptions):
            print(f"  {path}")
    print(
        "\nRemediation: rename the file, reorder or extend FEATURE_MAP, "
        "or add a KNOWN_MISTAGS entry with a reason."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
