---
name: sync-claude-config
description: Re-sync this repo's Claude Code config (.claude/agents, .claude/commands, settings.json hooks/permissions) into OpenCode's .opencode/ layout, idempotently. Use after any change to .claude/agents/, .claude/commands/, .claude/settings.json, or .claude/hooks/.
---

# Sync Claude Code config -> OpenCode

OpenCode already discovers `.claude/skills/*/SKILL.md` natively, so skills need no migration.
But agents, commands, permissions, and hooks live in Claude-only locations, so they must be
generated into `.opencode/`. This skill runs the generator that does that — idempotently.

## When to use this skill
- After editing any file in `.claude/agents/` (add/remove/rename an agent, change its tools/model).
- After editing `.claude/commands/` (role/command changes).
- After editing `.claude/settings.json` (hook list, permission allow-list, skillOverrides).
- After adding/removing a validator in `.claude/hooks/` (these are ported into the plugin).
- Any time you suspect `.opencode/` has drifted from `.claude/`.

## How to run (idempotent)
```bash
python scripts/sync_claude_to_opencode.py
```
The script:
1. Reads the source of truth from `.claude/`.
2. Rewrites only the `.opencode/` files whose source changed (compared via sha256 in
   `.opencode/SYNC_MANIFEST.json`).
3. Stamps every generated file with the sync date and a `from .claude/...` provenance comment.
4. Advances `generated_on` in the manifest to today.

Re-running it is safe and produces no churn when sources are unchanged.

## What it generates
- `.opencode/opencode.json` — `permission` block translated from `settings.json`
  (`Bash(spec)` -> `permission.bash` globs, `Skill(name)` -> `permission.skill`,
  `skillOverrides` off -> skill deny). Plus `instructions: ["CLAUDE.md"]` and model defaults.
- `.opencode/agents/*.md` — one per `.claude/agents/*.md`. `tools: ['*']` -> full access,
  explicit `tools:` lists -> `permission` allow/deny, `disallowedTools` -> `edit`/`write` deny,
  `model: sonnet|haiku` -> `anthropic/claude-*-4-5`. `builder` becomes `mode: all`; rest `subagent`.
- `.opencode/commands/*.md` — `commands/roles/*.md` (excluding `_`-prefixed includes),
  with `agent: build` and `$ARGUMENTS` preserved.
- `.opencode/plugins/valor-bridge.ts` — a single OpenCode plugin that re-dispatches the SAME
  `.claude/hooks/*.py` validators on `tool.execute.before` / `tool.execute.after` and the nearest
  session-lifecycle events (`session.created`/`session.idle`/`session.compacted`). Blocking
  validators throw to reject; best-effort (`|| true`) ones never block. `CLAUDE_PROJECT_DIR` is
  injected from the project directory so validators behave exactly as under Claude Code.

## Drift detection
`.opencode/SYNC_MANIFEST.json` records `generated_on` and the sha256 of every consumed source
file (agents, commands, hooks). To check for drift without changing anything:
```bash
python - <<'PY'
import hashlib, json, pathlib
m = json.loads(pathlib.Path(".opencode/SYNC_MANIFEST.json").read_text())
root = pathlib.Path(".")
for rel, h in m["agents"].items() | m["commands"].items() | m["hooks"].items():
    cur = hashlib.sha256((root/rel).read_bytes()).hexdigest()
    if cur != h:
        print("DRIFT:", rel)
print("generated_on:", m["generated_on"])
PY
```
Any `DRIFT` line means that source changed and you should re-run the sync.

## Verify after a sync
- `opencode debug config` shows the agents, skills, and resolved permissions.
- Invoke a subagent (e.g. `@validator`) — it should be read-only.
- A `git commit` with a bad message should be blocked by `valor-bridge.ts`.
