"""Pipeline-complete predicate for PM final-delivery protocol.

Pure decision function that determines whether a PM session has reached a
terminal pipeline state and is eligible for final-summary delivery. Replaces
the content-marker-based routing (`[PIPELINE_COMPLETE]`) formerly inspected
by `agent/output_router.py`.

Design (issue #1058):

- Keyed on the persisted ``psm.states`` dict rather than ``current_stage()``.
  After ``complete_stage(MERGE)`` runs, ``current_stage()`` returns None
  (it scans ``ALL_STAGES`` for an ``in_progress`` entry — see
  ``agent/pipeline_state.py`` around the ``current_stage`` implementation).
  A predicate keyed on ``current_stage`` would therefore return False exactly
  when the pipeline just finished. Reading ``states`` directly avoids this.
- Caller-provided ``pr_open`` to keep the predicate pure. The ``_check_pr_open``
  helper is a separate function that shells out to ``gh pr list``. The predicate
  itself performs no I/O.
- Call-site gating (Risk 5 / C6 in the plan): callers only invoke
  ``_check_pr_open`` for the ``DOCS-completed AND MERGE-not-completed``
  corner case. For the primary MERGE-success path, ``pr_open`` is not consulted.
  For non-terminal stages, the predicate should not be called at all.
"""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

_PR_LIST_TIMEOUT_SECONDS = 5.0


def is_pipeline_complete(
    psm_states: dict[str, str],
    outcome: str,
    pr_open: bool | None = None,
) -> tuple[bool, str]:
    """Pure predicate: has the pipeline reached a terminal state?

    Args:
        psm_states: ``PipelineStateMachine.states`` dict (stage name → status).
        outcome: Outcome of the most recent stage transition ("success",
            "fail", "partial", or any other value).
        pr_open: Optional PR-open state for the tracking issue. Only consulted
            for the DOCS-success-no-MERGE corner case. Callers should pass
            ``None`` for the MERGE-success path (not consulted) or for
            non-terminal stages (predicate returns False regardless).

    Returns:
        ``(is_complete, reason)`` tuple. ``reason`` is a stable machine-readable
        string so callers can log / telemetry-classify without parsing free text.

    Logic:
        - ``(True, "merge_success")`` when MERGE is completed AND outcome success.
          ``pr_open`` is ignored for this path — the pipeline has already
          confirmed completion by reaching MERGE success.
        - ``(True, "docs_success_no_pr")`` when DOCS is completed AND MERGE is
          NOT completed AND outcome success AND ``pr_open is False``.
          Handles legitimate non-MERGE terminal paths (e.g., docs-only changes,
          plan PRs that close on merge).
        - ``(False, "pr_state_unavailable")`` when the DOCS-no-MERGE path would
          apply but ``pr_open is None`` — conservative: never treat unknown PR
          state as "complete". The old nudge-based fallback continues to work
          as a safety net.
        - ``(False, <other reason>)`` otherwise.
    """
    merge_state = psm_states.get("MERGE")
    docs_state = psm_states.get("DOCS")

    if outcome != "success":
        return False, "outcome_not_success"

    if merge_state == "completed":
        return True, "merge_success"

    if docs_state == "completed" and merge_state != "completed":
        if pr_open is False:
            return True, "docs_success_no_pr"
        if pr_open is None:
            return False, "pr_state_unavailable"
        # pr_open is True → PR still open, not complete yet
        return False, "pr_still_open"

    return False, "stage_not_terminal"


def _check_pr_open(issue_number: int) -> bool | None:
    """Return True if an open PR exists for the given issue, False if none, None on error.

    Shells out to ``gh pr list --search "#{issue_number}" --state open`` with
    a short timeout. Failure modes (subprocess error, timeout, non-zero exit)
    all return ``None`` so callers fall back to the conservative
    "pr_state_unavailable" verdict.

    Call-site gating (plan Risk 5 / C6): callers should only invoke this when
    ``psm_states.get("DOCS") == "completed"`` AND
    ``psm_states.get("MERGE") != "completed"``. For the MERGE-success path,
    the predicate does not consult ``pr_open`` at all, so this subprocess
    is not called on the primary completion path.

    Args:
        issue_number: The tracking issue number.

    Returns:
        True if at least one open PR references the issue, False if the list
        is empty, None on subprocess error / timeout / malformed output.
    """
    if not issue_number:
        return None
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--search",
                f"#{issue_number}",
                "--state",
                "open",
                "--json",
                "number",
            ],
            capture_output=True,
            text=True,
            timeout=_PR_LIST_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("_check_pr_open(%s): subprocess error %s", issue_number, exc)
        return None

    if result.returncode != 0:
        logger.warning(
            "_check_pr_open(%s): gh exited %s stderr=%s",
            issue_number,
            result.returncode,
            (result.stderr or "").strip()[:200],
        )
        return None

    stdout = (result.stdout or "").strip()
    if not stdout:
        return False
    # gh pr list --json returns "[]" for empty and "[{...}]" otherwise.
    # Conservative: if the JSON parse fails, return None so we don't
    # accidentally claim "no open PR" on a malformed response.
    import json as _json

    try:
        prs = _json.loads(stdout)
    except _json.JSONDecodeError as exc:
        logger.warning("_check_pr_open(%s): json decode failed %s", issue_number, exc)
        return None
    if not isinstance(prs, list):
        return None
    return bool(prs)
