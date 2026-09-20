"""Restart gate for scripts/remote-update.sh (issue #3528).

Usage::

    python -m scripts.update.restart_gate --process worker|bridge \\
        [--before <sha>] [--after <sha>]

Exit codes:

- ``0`` — restart this process now.
- ``1`` — do not restart.

Answers exactly one question — "is the running process behind HEAD on the code
it actually loads?" — through the SAME classifier the terminal release verify
uses (:func:`scripts.update.service.classify_process` over
:data:`scripts.update.service.PROCESS_RELEVANT_PATHS`). That sharing is the
whole point of the module.

Before #3528 the shell gates hand-rolled ``git diff $BEFORE_SHA $AFTER_SHA --
<paths>`` — the CURRENT CYCLE'S PULL DELTA. Whenever a restart was skipped on
the cycle that pulled the relevant commits (drain timeout, the #3164
self-ancestor refusal, a failed kickstart), every later cron cycle pulled
nothing, so ``BEFORE_SHA == AFTER_SHA`` and the gate reported "no relevant
changes" forever. The verifier, diffing ``boot_sha..HEAD``, kept reporting
``release verify FAILED`` every 30 minutes with no actuator able to clear it.

Fallback: when the classifier returns ``unknown`` — no beacon, no running
process, orphaned beacon, or an unresolvable boot SHA — there is nothing to
classify, so the gate falls back to the pull-delta diff. That keeps a fresh
install (no beacon yet, services not started) behaving exactly as it did
before. Consistent with the #1898 posture that ``unknown`` never escalates,
an ``unknown`` classification never *invents* a restart on a no-op cycle.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.update import git, service  # noqa: E402

# Resolved through the module at call time, never bound at import: the gate
# must observe the same `service.get_*_pid` the release verifier does, even
# when one of them is swapped out (tests, future probe changes).
PID_GETTERS = {
    "bridge": lambda: service.get_bridge_pid(),
    "worker": lambda: service.get_worker_pid(),
}


def pull_delta_touches_paths(
    project_dir: Path, before: str, after: str, relevant_paths: list[str]
) -> bool:
    """True when ``before..after`` touches any of ``relevant_paths``.

    The pre-#3528 gate logic, retained only as the ``unknown``-classification
    fallback. An empty or equal SHA pair means no pull delta → no restart.
    """
    if not before or not after or before == after:
        return False
    result = service.run_cmd(
        ["git", "diff", "--name-only", before, after, "--", *relevant_paths],
        cwd=project_dir,
    )
    if result.returncode != 0:
        # Unresolvable SHAs (shallow clone, history rewrite) — inconclusive,
        # and inconclusive must not kill a live worker's in-flight sessions.
        return False
    return bool(result.stdout.strip())


def decide(project_dir: Path, process_name: str, before: str, after: str) -> tuple[bool, str]:
    """Return ``(restart_needed, human_readable_reason)`` for one process."""
    relevant_paths = service.PROCESS_RELEVANT_PATHS[process_name]

    try:
        head_sha = git.get_short_sha(project_dir)
        info = service.classify_process(
            project_dir, head_sha, process_name, PID_GETTERS[process_name](), relevant_paths
        )
        classification = info["classification"]
    except Exception as exc:  # noqa: BLE001 - any probe failure degrades to the pull delta
        classification = "unknown"
        head_sha = "unknown"
        info = {"boot_sha": None}
        print(f"[restart-gate] {process_name}: classification failed ({exc})", file=sys.stderr)

    if classification == "stale":
        return True, (
            f"{process_name} running {info['boot_sha']} is behind HEAD {head_sha} "
            f"on {process_name}-relevant paths"
        )
    if classification == "matches":
        return False, f"{process_name} already running current {process_name}-relevant code"

    # unknown → nothing to classify; fall back to this cycle's pull delta.
    if pull_delta_touches_paths(project_dir, before, after, relevant_paths):
        return True, f"{process_name} release unknown; pulled commits touch {process_name} paths"
    return False, f"{process_name} release unknown; no {process_name}-relevant changes pulled"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.update.restart_gate",
        description="Decide whether remote-update.sh should restart a service (#3528).",
    )
    parser.add_argument("--process", required=True, choices=sorted(PID_GETTERS))
    parser.add_argument(
        "--before", default="", help="SHA before this cycle's pull (fallback diff base)."
    )
    parser.add_argument(
        "--after", default="", help="SHA after this cycle's pull (fallback diff head)."
    )
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    restart_needed, reason = decide(args.project_dir, args.process, args.before, args.after)
    print(f"[restart-gate] {'RESTART' if restart_needed else 'SKIP'}: {reason}")
    return 0 if restart_needed else 1


if __name__ == "__main__":
    sys.exit(main())
