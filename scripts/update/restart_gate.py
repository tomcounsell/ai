"""Restart gate for scripts/remote-update.sh (issue #3528).

Usage::

    python -m scripts.update.restart_gate --process worker|bridge \\
        [--before <sha>] [--after <sha>]

Exit codes:

- ``0`` — restart this process now.
- ``1`` — do not restart. An unexpected failure also exits 1 (skipping is the
  safe verdict — never SIGKILL a live worker's sessions on a bad probe) but
  prints a distinct ``GATE ERROR:`` line so it is never mistaken for a
  confident "no relevant changes".

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


def pull_delta_touches_paths(
    project_dir: Path, before: str, after: str, relevant_paths: list[str]
) -> bool:
    """True when ``before..after`` touches any of ``relevant_paths``.

    The pre-#3528 gate logic, retained only as the ``unknown``-classification
    fallback. An empty or equal SHA pair means no pull delta → no restart.
    """
    if not before or not after or before == after:
        return False
    try:
        result = service.run_cmd(
            ["git", "diff", "--name-only", before, after, "--", *relevant_paths],
            cwd=project_dir,
        )
    except Exception as exc:  # noqa: BLE001 - a hung/broken git must not decide a restart
        print(f"WARNING: restart gate pull-delta diff failed ({exc})")
        return False
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
        if not head_sha:
            # `git rev-parse --short HEAD` returned empty (unreadable HEAD).
            # Classifying against "" would build the range `{boot_sha}..`, which
            # git silently resolves to `boot_sha..HEAD` — right answer, blank SHA
            # in the operator line. Treat it as unclassifiable instead.
            raise ValueError("git rev-parse --short HEAD returned empty output")
        info = service.classify_process(
            project_dir,
            head_sha,
            process_name,
            service.PROCESS_PID_GETTERS[process_name](),
            relevant_paths,
        )
        classification = info["classification"]
    except Exception as exc:  # noqa: BLE001 - any probe failure degrades to the pull delta
        classification = "unknown"
        print(f"WARNING: restart gate could not classify {process_name} ({exc})")

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
    parser.add_argument("--process", required=True, choices=sorted(service.PROCESS_RELEVANT_PATHS))
    parser.add_argument(
        "--before", default="", help="SHA before this cycle's pull (fallback diff base)."
    )
    parser.add_argument(
        "--after", default="", help="SHA after this cycle's pull (fallback diff head)."
    )
    parser.add_argument("--project-dir", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        restart_needed, reason = decide(args.project_dir, args.process, args.before, args.after)
    except Exception as exc:  # noqa: BLE001 - see the GATE ERROR note below
        # Exit 1 is the shell's "skip", and an uncaught traceback would exit 1
        # too — the caller would then print its confident "no relevant changes
        # detected" line for what was actually a crash. That silent-wrong-verdict
        # shape is the #3528 bug itself, so name the failure explicitly. Skipping
        # remains the right verdict: an inconclusive gate must never SIGKILL a
        # live worker's in-flight sessions, and the terminal release verify
        # still escalates a genuinely stale process on the same cycle.
        #
        # `ERROR:` and the `WARNING:` lines above are not free-form prose: they
        # are two of the four line-anchored prefixes bridge/update.py's
        # _LEGACY_WARNING_PREFIXES scans for, which is how a diagnostic reaches
        # the Telegram /update report and spawns a fix session. Everything the
        # gate prints on a degraded path goes to STDOUT with one of those
        # prefixes; a stderr line, or one starting "[restart-gate]", is parsed
        # as nothing and the report reads a confident green.
        print(f"ERROR: {args.process} restart gate failed ({exc}) — not restarting")
        return 1
    print(f"[restart-gate] {'RESTART' if restart_needed else 'SKIP'}: {reason}")
    return 0 if restart_needed else 1


if __name__ == "__main__":
    sys.exit(main())
