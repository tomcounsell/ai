"""Granite Operator Interactive TUI PoC — kernel validation container.

This package is the PoC for the granite operator driving a real interactive
Claude Code session via PTY (issue #1546). It is additive to the existing
headless harness (`agent/claude_session.py` + `agent/sdk_client.py`) and
does not modify those files. The PoC's substrate is the interactive TUI;
the headless harness remains the production session runner until the
production cutover (a separate, follow-on issue) lands.

Module surface:
    pty_driver:        thin pexpect-backed PTY driver (this PoC's substrate).
    startup_parser:    startup-phase pattern matcher (login, update, trust-folder, ...).
    granite_classifier: reduced 3-tool granite classifier (classify + 2x translate).
    container:         the steady-state loop that owns two PTYs and the granite calls.

CLI entry point: `tools.granite_interactive_tui_poc.cli` -> `valor-granite-loop`.
"""
