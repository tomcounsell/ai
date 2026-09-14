"""Single source of truth for user-facing session-lifecycle copy.

Every Telegram message the worker sends about a session's *lifecycle* (as
opposed to the session's actual work product) is defined here as a module
constant. Send sites import these names; they never inline the literal string.
A copy change is therefore a single-file edit that ripples to every send site
and every copy-asserting test at once (issue #1877).

Constants:
  * ``INTERRUPT_NO_RESUME`` — the session was stopped by a killer that finalized
    it to a terminal, non-resumable status (deadline kill, health-check kill,
    exhausted recovery attempts). Nothing will resume automatically.
  * ``FAILURE_NOTICE`` — the session crashed (running -> failed) from an uncaught
    exception. The error was logged; the request did not complete.
  * ``TERMINAL_PROMISE_FALLBACK_MESSAGE`` — the promise gate withheld a held final
    reply at terminal-flush time, and no live agent remained to redraft it. Says
    only that the message was withheld.
"""

from __future__ import annotations

# Interrupted, worker will NOT resume automatically.
INTERRUPT_NO_RESUME = (
    "I was stopped and won't resume automatically. Send a new message if you'd like me to continue."
)

# Session crashed (running -> failed) from an uncaught exception.
FAILURE_NOTICE = "Something went wrong and I couldn't finish that. I've logged the error."

# Promise gate withheld a held final reply at terminal-flush time (#2423). There is
# no live agent left to self-draft a rewrite, so the flush substitutes rather than
# suppresses (suppression would reintroduce the #1796 swallowed-reply class).
#
# Three constraints on this text. Two are from #3135: it must itself pass the promise
# heuristic (`bridge.promise_gate._evaluate_promise_heuristic` must return "allow"),
# and every clause must be true given ONLY "the gate withheld the message" — a block
# verdict carries no information about whether any work completed, so the text may not
# claim failure or invent a pending request. The third is from #3290: this reaches the
# human in Telegram, so it is Valor speaking in the first person and names no internal
# mechanism. Do not reintroduce the words "filter", "session", or "gate" here.
#
# "It wasn't good enough." is deliberately verdict-shaped rather than content-shaped.
# An earlier draft said "It made a commitment I couldn't back up", which is false for
# the behavioral-change block class: that class fires on bare acknowledgments
# ("you're right", "good point", "makes sense") containing no commitment at all.
TERMINAL_PROMISE_FALLBACK_MESSAGE = (
    "I didn't send my last message here. It wasn't good enough. "
    "If you were waiting on something from me, just ask again."
)
