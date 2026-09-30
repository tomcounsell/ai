"""Headless session runner: every session role runs as ``claude -p`` stream-json.

**Protocol, not paint.** Session state comes exclusively from machine
interfaces the CLI commits to: the stream-json event stream (``system/init``,
``result``) and the Claude Code hook envelopes (``Stop`` → turn-end,
substantive ``Notification`` / ``AskUserQuestion`` → needs-human). Nothing in
this package ever infers state from what a terminal paints on screen — no idle
heuristics, no frame scraping, no marker regexes over TUI output.

**One-way mandate.** This package replaces the granite PTY substrate outright
(plan ``docs/plans/granite-pty-teardown.md``, #1924). There is no PTY
fallback, no transport selector, and no revert path — one transport needs no
seam. Any future "fall back to the interactive TUI" branch is a violation of
the cutover decision, not a hardening opportunity.

Modules:

- :mod:`~agent.session_runner.role_driver` — ``HeadlessRoleDriver``: one
  ``claude -p`` subprocess per turn, prime loading, subscription-auth env,
  hook-edge turn-end reconciliation.
- :mod:`~agent.session_runner.hook_edge` — the hook edge channel: settings
  generation, durable cursor, NDJSON consumer, content-aware Notification
  classification (#1919).
- :mod:`~agent.session_runner.hook_forwarder` — the fail-silent hook script
  Claude Code invokes; appends envelopes to the per-session edge file.
- :mod:`~agent.session_runner.transcript_tailer` — last-assistant-text reads
  from JSONL transcripts (the dev turn-history mirror).
- :mod:`~agent.session_runner.router` — regex PM-prefix classification and
  the exit-classification tables.
- :mod:`~agent.session_runner.adapter` — executor-facing construction:
  delivery callbacks, exit summary, four-scalar resume persistence.
- :mod:`~agent.session_runner.runner` — ``SessionRunner``: the single-session
  turn loop (route → deliver → steer-preempt → resume) the executor drives.
"""

from agent.session_runner.adapter import RunSummary, SessionRunnerAdapter
from agent.session_runner.runner import ResumeContext, SessionRunner

__all__ = [
    "ResumeContext",
    "RunSummary",
    "SessionRunner",
    "SessionRunnerAdapter",
]
