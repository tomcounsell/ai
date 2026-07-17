# Agent Teams: Enabled Interactive, Disabled Headless

**Status:** Shipped (July 2026)
**Decision owner:** Valor
**Review trigger:** Claude Code agent teams graduating from experimental
(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`) to GA-default — see
[Revisit criteria](#revisit-criteria).

## The decision

Claude Code's experimental **agent teams** feature is enabled fleet-wide for
**interactive** sessions and explicitly **disabled for every headless
`claude -p` spawn** the worker makes (PM/dev/teammate role sessions, message
drafter, probes, drafter-review).

| Surface | Agent teams | Mechanism |
|---------|-------------|-----------|
| Interactive Claude Code sessions (every machine) | **On** | `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS: "1"` in `~/.claude/settings.json` `env` block, seeded by `/update` (`scripts/update/hardlinks.py` `_USER_ENV_DEFAULTS`) |
| Headless spawns via the session-runner harness | **Off** | CLI `--settings` env override (`HEADLESS_ENV_OVERRIDES` in `agent/session_runner/hook_edge.py`) |

## Why headless spawns must not form teams

The SDLC pipeline runs one `claude -p` process per turn and `--resume`s the
next turn (`agent/session_runner/runner.py`). Teams collide with that
architecture in four documented ways:

1. **Teammates do not survive resume.** In-process teammates are not restored
   by session resumption; the pipeline resumes on *every* turn. Any teammate
   spawned in turn N is gone by turn N+1, and the lead may message teammates
   that no longer exist.
2. **Teammates die at process exit.** A teammate cannot outlive the lead's
   process. If a PM turn ends while teammates are mid-task, that work is
   killed silently — unlike the dev subagent, whose partial sidechain
   transcript remains the resume target.
3. **The PM→dev continuation contract is subagent-only.** The pipeline hinges
   on the `dev_agent_id` continuation handle and `subagents/agent-*.jsonl`
   sidechain files (`docs/features/headless-session-runner.md`). Teammates
   have no equivalent handle, cannot spawn nested teams, and an in-process
   teammate's own subagents are forced to the foreground (background requests
   error).
4. **Unattended permission surface.** Teammates inherit the lead's permission
   mode at spawn; a headless role session runs `bypassPermissions` with no
   human watching the approval seam, and each teammate is another
   full-permission, full-token-cost instance.

Empirically (Claude Code v2.1.204, July 2026), a headless `-p` session asked
to "spawn a teammate" silently degrades to a regular subagent and no team
forms — so today's pipeline behavior is safe *by accident*. This policy makes
it safe *by contract*: experimental-feature behavior in print mode is
undocumented and version-dependent.

## Why the disable rides `--settings`, not the environment

Verified empirically on v2.1.204:

- The `env` block in `~/.claude/settings.json` **overwrites the inherited
  process environment**. Spawning the subprocess with
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=0` in `proc_env` is silently stomped
  back to `"1"` — a plain env override in the harness does NOT work.
- A CLI `--settings` source (file path or inline JSON) outranks user
  settings, and its `env` block merges per-key: `--settings
  '{"env":{"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":"0"}}'` yields `"0"` inside
  the session while other user-settings env keys survive.
- The binary's flag parser treats only `"1"`, `"true"`, `"yes"`, `"on"` as
  enabled — `"0"` disables even though the variable is set.

## Implementation (two seams, one constant)

`HEADLESS_ENV_OVERRIDES` in `agent/session_runner/hook_edge.py` is the single
definition:

1. **Role sessions (PM/dev/teammate):** `generate_hook_settings()` writes the
   per-session `--settings` file every role turn already uses (hook edge
   channel, plan #1842) and now includes the `env` override block.
2. **All other harness consumers (drafter, probes, drafter-review):**
   `get_response_via_harness()` in `agent/session_runner/harness/claude.py`
   injects `--settings '{"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":
   "0"}}'` inline whenever no `settings_path` is supplied — so *every*
   headless spawn carries the disable, not just role sessions.

Tests: `tests/unit/session_runner/test_agent_teams_headless_disable.py`
(both seams + constant), and the golden argv test
(`tests/unit/session_runner/test_harness_argv_golden.py`) pins the inline
flag's exact position in the subprocess argv.

### Verifying on a machine

```bash
# Interactive default (expect "1"):
python3 -c "import json; print(json.load(open('$HOME/.claude/settings.json'))['env']['CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS'])"

# Headless override (expect TEAMS='0'):
claude -p --permission-mode bypassPermissions \
  --settings '{"env":{"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":"0"}}' \
  "Run with Bash and report stdout verbatim: python3 -c \"import os; print('TEAMS=' + repr(os.environ.get('CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS')))\""
```

## What interactive sessions gain

Teams stay on for local interactive work, where the limitations don't bite:
plan-critique war rooms (`/do-plan-critique`), parallel multi-lens PR review,
and competing-hypothesis debugging — the documented sweet spots for the
feature.

## Revisit criteria

Re-review this decision when **any** of the following happens:

- Agent teams ships GA / on-by-default in Claude Code (the experimental env
  var disappears or flips default) — the `--settings` override key may be
  renamed, removed, or inverted.
- Claude Code documents teammate behavior under `claude -p` (print mode) —
  today it is undocumented and teams silently degrade to subagents.
- Teammates gain resume survival or a continuation handle equivalent to
  subagent `agentId`s — the architectural objections (1)–(3) above would
  weaken, and teams inside pipeline stages could become attractive (e.g.
  parallel review lenses inside the REVIEW stage).
- A Claude CLI version bump changes settings-precedence behavior (the
  empirical findings above are pinned to v2.1.204). The golden argv test
  keeps the flag present, but precedence itself is only verifiable end to
  end — re-run the verification commands above after major CLI bumps.

If the flag goes away entirely, delete `HEADLESS_ENV_OVERRIDES`, the inline
`--settings` fallback in `harness/claude.py`, the env block in
`generate_hook_settings()`, the `_USER_ENV_DEFAULTS` entry in
`scripts/update/hardlinks.py`, and this document — no legacy remnants.

## See also

- [`headless-session-runner.md`](headless-session-runner.md) — the per-turn
  `claude -p` architecture this policy protects
- [`bridge-worker-architecture.md`](bridge-worker-architecture.md) — where
  headless spawns originate
- `scripts/update/hardlinks.py` `_USER_ENV_DEFAULTS` — the fleet-wide
  interactive enable
