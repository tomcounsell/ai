# Granite fault-injection fixtures

Recorded / synthetic TUI frame streams that the Substrate A replay-and-mutate
injectors (`tests/granite_faults/scenarios.py`, consumed by
`tests/unit/granite_container/test_fault_injection.py`) feed into the real
granite seams. Committing them makes Substrate A the **always-on** deterministic
gate — it runs with no ollama, no model, and no network, so it holds even when
the ollama-backed Substrate B self-skips.

## Format

Each file is a small, reviewable **plain-text frame stream** representing what
the Claude Code TUI paints to the PTY over one phase of a session. Streams are
stored ANSI-light (a few glyphs, no raw CSI/OSC escapes) because
`agent.granite_container.pty_driver._strip_ansi` is applied by
`read_until_idle` anyway and idempotent on plain text — keeping the bytes
readable in a PR diff is the point.

The injectors replay a fixture through a fake pexpect child and then **mutate**
it (remove or rename the idle bar, drop the login phrase). Substrate A asserts
against the *mutation*, never against exact bytes, so a new `claude` release
that reflows spacing does not break these tests — only a real regression in the
seam does.

## Files

| File | Consumed by | Baseline shape | Load-bearing tokens |
|------|-------------|----------------|---------------------|
| `idle_settled.frames` | class 1 (turn-detection wedge), class 6 (silent tail) | a settled PM turn: spinner ran, response printed, then the idle bar + prompt glyph painted | `bypass ... permissions` bar, `❯` glyph, `esc to interrupt` spinner evidence, >400 stripped chars (clears the content floor) |
| `working_no_idle.frames` | class 6 (silent no-progress tail) | an active turn that emits progress frames then goes quiet **without** ever painting the idle bar | spinner verbs, **no** bypass bar → `saw_idle` must stay `False` |
| `startup_login_prompt.frame` | class 2 (startup / `/login` wedge) | a first-run login frame | `Select login method`, `Sign in to continue` → `StartupEvent.LOGIN_PROMPT` |

## Regenerating (Wave 2)

These are a hand-authored deterministic **seed** set. Wave 2 adds a
golden-recorder tool that runs a real (ollama-backed) Substrate B session and
captures fresh frame streams here. Until then, edit the files directly and keep
them small — the mutation-based assertions tolerate cosmetic drift, so fixtures
only need to carry the load-bearing tokens in the table above.
