"""Single source of truth for pytest FEATURE_MAP marker resolution.

`tests/conftest.py::pytest_collection_modifyitems` auto-applies a feature marker
to every collected test by taking the module basename, stripping a leading
``test_`` and a trailing ``.py``, and substring-matching the remainder against
``FEATURE_MAP`` in insertion order, first hit wins. That coupling is silent: a
test file can be renamed, moved, or split and land under the wrong marker (or
none) with the collection total unchanged and the suite staying green.

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

See docs/features/feature-map-marker-guard.md for the mistag mechanisms (two
live; the mangled stem was fixed by #3184), what the three rules below can and
cannot see, and how to respond when the guard goes red.
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
    # "checkpoint" already matches inside "checkpointing" as a fragment, which
    # is the R3 violation this key clears. Its position is free: both keys map
    # to "validation", so whichever wins the first-hit scan, resolve_marker and
    # resolve_marker_whole_token agree and R3 stays silent. It outranks nothing,
    # so moving it to a position that "looks more deliberate" gains nothing and
    # risks nothing -- leave it here, next to the key it disambiguates.
    "checkpointing": "validation",
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
# silent hole of exactly the kind #3031 warns about.
#
# #3175 drained this baseline from 24 entries to 2. The two survivors are R2
# policy entries, not unaddressed defects: each names a file that resolves to a
# marker which is correct for it, and R2 fires on the *siblings'* absence of a
# marker rather than on that file's presence of one. A third entry earns its
# place only under the same standard -- a stated policy choice, not a deferral.
# The baseline can still only shrink, never grow: rule 2 below forces a stale
# entry (one whose violation was fixed without deleting the entry) to fail
# loudly rather than rot.
KNOWN_MISTAGS: dict[str, str] = {
    # R2 (sibling uniformity), and both entries are a POLICY CHOICE rather than
    # an unaddressed defect (#3175 acceptance criterion 1). Neither file is
    # mistagged: each resolves to a marker that is correct for it. R2 fires on
    # the *siblings'* absence of a marker, not on this file's presence of one.
    # The remedies are to mark 21 sibling files that nobody has asked to select,
    # or to rename a correctly-named file to hide from the rule. Both are worse
    # than the finding. Re-open if either package acquires a package-level
    # marker of its own.
    "tests/unit/session_runner/test_schema_routing.py": (
        "R2 POLICY: lone sibling (1 of 18) resolving to 'messaging' via "
        "'routing', against 17 unmarked siblings in tests/unit/session_runner/, "
        "which does not itself resolve. 'messaging' is correct for this file: "
        "it tests schema routing. Kept deliberately (#3175)."
    ),
    "tests/unit/hooks/test_pre_tool_use_foreground_subagents.py": (
        "R2 POLICY: lone sibling (1 of 5) resolving to 'sdlc' via "
        "'pre_tool_use', against 4 unmarked siblings in tests/unit/hooks/, "
        "which does not itself resolve. 'sdlc' is correct for this file: the "
        "PreToolUse hook is an SDLC surface. Kept deliberately (#3175)."
    ),
}


def _stem(basename: str) -> str:
    """Reduce a test module basename to the string FEATURE_MAP is matched against.

    The one implementation of the stem expression. `resolve_marker` and
    `resolve_marker_whole_token` both call this, so the two resolvers cannot
    drift apart.
    """
    # Anchored strip: "test_" only at the front, ".py" only at the end (#3184).
    # A global str.replace ate every occurrence, so a basename carrying "test_"
    # a second time lost it out of the middle -- test_test_judge.py stemmed to
    # "judge" and test_conftest_isolation_guards.py to "confisolation_guards",
    # missing FEATURE_MAP keys written for those exact files. Measured over all
    # tracked test files, anchoring moves exactly two: tests/tools/
    # test_test_judge.py gains "tools" and tests/unit/test_validate_test_impact.py
    # gains "validation". Nothing loses a marker and the audit stays at 0 new,
    # 0 stale. Keep it anchored; `removeprefix`/`removesuffix` are exact, are
    # no-ops on a miss, and need no `re` import, which keeps this module
    # runnable on a bare interpreter.
    return basename.removeprefix("test_").removesuffix(".py")


def resolve_marker(basename: str) -> tuple[str | None, str | None]:
    """Resolve one test module basename to (marker, matched FEATURE_MAP key).

    This is Path A and Path B's single point of truth: the pytest collection
    hook and this guard both call this function, so they cannot disagree.
    """
    stem = _stem(basename)
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
    stem = _stem(basename)
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
    """Group files by parent directory *path*, excluding KNOWN_ROOT_DIRS parents.

    Keyed on the repo-relative directory path, never on the bare directory
    name: two same-named packages in different trees (``tests/unit/helpers/``
    and ``tests/integration/helpers/``) are two independent packages with two
    independent intents. Merging them on the shared name pools their files
    into one group, which invents R2 violations for whichever tree is in the
    minority of the pooled set.
    """
    packages: dict[str, list[str]] = {}
    for path in files:
        parts = Path(path).parts
        if len(parts) < 2:
            continue
        if parts[-2] in KNOWN_ROOT_DIRS:
            continue
        packages.setdefault(str(Path(path).parent.as_posix()), []).append(path)
    return packages


def check_r1(files: list[str]) -> list[dict[str, str | None]]:
    """Directory intent: a package whose own name resolves declares that marker."""
    violations: list[dict[str, str | None]] = []
    for pkg_path, paths in _partition_packages(files).items():
        dir_marker, _dir_key = resolve_marker(Path(pkg_path).name)
        if dir_marker is None:
            continue  # no declared intent; R2's territory, not R1's
        for path in paths:
            marker, key = resolve_marker(Path(path).name)
            if marker != dir_marker:
                violations.append(
                    {
                        "path": path,
                        "resolved": marker,
                        "expected": dir_marker,
                        "key": key,
                        "rule": "R1",
                    }
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
    for pkg_path, paths in _partition_packages(files).items():
        dir_marker, _dir_key = resolve_marker(Path(pkg_path).name)
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
                    {
                        "path": path,
                        "resolved": marker,
                        "expected": None,
                        "key": key,
                        "rule": "R2-ambiguous",
                    }
                )
            continue
        majority_marker = largest_markers[0]
        for marker, members in groups.items():
            if marker == majority_marker:
                continue
            for path in members:
                _majority_dummy, key = resolve_marker(Path(path).name)
                violations.append(
                    {
                        "path": path,
                        "resolved": marker,
                        "expected": majority_marker,
                        "key": key,
                        "rule": "R2",
                    }
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
    group.add_argument(
        "--audit", action="store_true", help="Exit non-zero on any mistag or stale exemption."
    )
    group.add_argument(
        "--report", action="store_true", help="Print 'path<TAB>marker' for every tracked test file."
    )
    group.add_argument(
        "--count", action="store_true", help="Print the number of tracked test files."
    )
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
