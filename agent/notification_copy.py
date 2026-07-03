"""Single source of truth for user-facing session-lifecycle copy.

Every Telegram message the worker sends about a session's *lifecycle* (as
opposed to the session's actual work product) is defined here as a module
constant. Send sites import these names; they never inline the literal string.
A copy change is therefore a single-file edit that ripples to every send site
and every copy-asserting test at once (issue #1877).

Constants:
  * ``INTERRUPT_RESUME`` — the session was interrupted but the worker will
    re-queue and resume it automatically; the user need do nothing. This is the
    historical default preserved verbatim so the resume case behaves exactly as
    before.
  * ``INTERRUPT_NO_RESUME`` — the session was stopped by a killer that finalized
    it to a terminal, non-resumable status (deadline kill, health-check kill,
    exhausted recovery attempts). Nothing will resume automatically.
  * ``FAILURE_NOTICE`` — the session crashed (running -> failed) from an uncaught
    exception. The error was logged; the request did not complete.
"""

from __future__ import annotations

# Interrupted, worker WILL resume automatically (unchanged historical copy).
INTERRUPT_RESUME = "I was interrupted and will resume automatically. No action needed."

# Interrupted, worker will NOT resume automatically.
INTERRUPT_NO_RESUME = (
    "I was stopped and won't resume automatically. Send a new message if you'd like me to continue."
)

# Session crashed (running -> failed) from an uncaught exception.
FAILURE_NOTICE = "Something went wrong and I couldn't finish that. I've logged the error."
