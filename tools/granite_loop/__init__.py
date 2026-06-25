"""Operator CLI for the granite interactive-TUI session runner.

`valor-granite-loop` is the operator's direct invocation path for
the container. It takes a user message, runs the container
end-to-end, and writes the results JSON to a path callers can render.

The CLI is intentionally narrow: it does not wire to the bridge,
does not dispatch child sessions, and does not invoke /sdlc. It
is the operator's standalone driver for the container.

Exit codes:
  0  - PM emitted [/complete] (clean exit)
  1  - PM reached max_turns without [/complete] (safety cap)
  2  - PM or Dev hung (await_idle timeout)
  3  - startup_unresolved (startup window passed without both
      PTYs reaching idle)
  4  - exception during the run
  5  - empty user message
"""
