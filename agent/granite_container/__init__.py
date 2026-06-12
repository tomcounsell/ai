"""Granite interactive-TUI session runner — production container.

This package is the production execution path for the granite operator
driving a real interactive Claude Code session via PTY. It is the session
runner for bridge-originated sessions under the standalone worker, running
alongside the headless harness (`agent/claude_session.py` +
`agent/sdk_client.py`).

Module surface:
    pty_driver:        thin pexpect-backed PTY driver (the substrate).
    startup_parser:    startup-phase pattern matcher (login, update, trust-folder, ...).
    granite_classifier: reduced 3-tool granite classifier (classify + 2x translate).
    container:         the steady-state loop that owns two PTYs and the granite calls.

CLI entry point: `tools.granite_loop.cli` -> `valor-granite-loop`.
"""
