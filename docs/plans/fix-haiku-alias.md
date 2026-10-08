---
tracking: none
slug: fix-haiku-alias
type: build
status: planned
critique_rounds: 0
review_rounds: 1
---

# No bare model alias reaches the gateway

## Incident

On 2026-10-08 the `claude` CLI on Valor's Mac auto-updated to 2.1.293
(now 2.1.294) and began resolving the alias `haiku` to
`claude-haiku-5-5`. The gateway's price table has no price for that id,
so a call asking for `haiku` is refused (`400 model claude-haiku-5-5 has
no price`). Real work resolves seats to pinned ids and is unaffected, but
three defaults still name the bare alias: `Brief.model` in
`core/tasks.py`, and `model` of `claude_code.turn` and
`claude_code.workspace_turn`. The harness contract tests pass `haiku` too:
eight claude_code tests fail and the stop-mid-call test waits forever for
a call that never comes, stalling the suite.

A second failure in the same file is not the alias:
`test_a_checkout_claude_settings_file_sets_nothing_in_the_session[turn]`
ends `failed` with the CLI's result "Failed to authenticate: OAuth session
expired and could not be refreshed", before any model call, with or
without the checkout's settings file. The test-only `turn` builder drops
Claude Code's own variables and so falls back to the machine user's
interactive login in `~/.claude`, which has expired. `workspace_turn`
never depends on that login: it carries the placeholder `TURN_TOKEN`,
which the gateway replaces with the kernel's credential.

## Fix

- The three defaults become the light seat's pinned id,
  `resolve_model("light")` (`claude-haiku-4-5`), so a CLI alias change
  cannot route a call to an unpriced model. No price is added.
- `claude_code.turn` carries `CLAUDE_CODE_OAUTH_TOKEN=TURN_TOKEN` like
  `workspace_turn`, so it no longer reads the machine user's login; the
  gateway supplies the credential. `tests/test_live_turn.py` gives its
  gateways the kernel's `ClaudeLogin()`, as `core serve` does.
- The harness contract test's claude_code model is the light seat.
- `docs/harnesses.md` and `harnesses/README.md` say both builders carry the
  placeholder and default to the light seat.

## Tests

`tests/test_harness_contract.py`, `tests/test_emulator_metering.py`, and
every test file touching `core/tasks.py` defaults or `claude_code.turn`
(`test_session`, `test_credentials`, `test_judgement`, `test_persona`,
`test_corrections`, `test_tasks`, `test_live_turn` skipped without
`VALOR_LIVE`). A test asserts both builders default to the light seat's id
and that `turn` carries the placeholder credential.

## Left out

- No price for `claude-haiku-5-5`: it is not checked against the pricing
  page and no seat uses it.
- Historical plan files that mention the alias stay as written.
- Pinning or disabling the CLI's auto-update on the Mac.
- `tests/test_container.py` (owned by the lead).
