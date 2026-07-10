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
1. Reads the source of truth from the **committed** `.claude/` files only —
   the machine-local `.claude/settings.local.json` is deliberately excluded so
   committed artifacts stay reproducible on every machine.
2. Rewrites only the `.opencode/` files whose source changed: each source's sha256 is
   compared against its entry in `.opencode/SYNC_MANIFEST.json` and unchanged sources
   are skipped (a byte-compare backstops every write).
3. Stamps every generated file with a stable, date-free `generated from .claude/...`
   provenance comment, so unchanged sources produce zero churn on any day.
4. Advances `generated_on` in the manifest only when something actually changed.

Re-running it is safe and produces no churn when sources are unchanged. If you change the
generator script itself (templates, headers), bump `GENERATOR_VERSION` in it — a version
mismatch in the manifest forces a full refresh pass.

## What it generates
- `.opencode/opencode.json` — `permission` block translated from the committed `settings.json`
  (`Bash(cmd:*)` -> both `"cmd"` and `"cmd *"` glob keys, `Skill(name)` -> `permission.skill`,
  `skillOverrides` off -> skill deny). Plus `instructions: ["CLAUDE.md"]` and model defaults.
- `.opencode/agents/*.md` — one per `.claude/agents/*.md`. `tools: ['*']` -> full access,
  explicit `tools:` lists -> `permission` allow/deny, `disallowedTools` -> `edit`/`write` deny,
  `model: sonnet|haiku` -> `anthropic/claude-*-4-5`. `builder` becomes `mode: all`; rest `subagent`.
- `.opencode/commands/*.md` — `commands/roles/*.md` (excluding `_`-prefixed includes),
  with `agent: build` and `$ARGUMENTS` preserved.
- `.opencode/plugins/valor-bridge.ts` — a single OpenCode plugin that re-dispatches the SAME
  `.claude/hooks/*.py` validators on `tool.execute.before` / `tool.execute.after` and the nearest
  session-lifecycle events (`session.created`/`session.idle`/`session.compacted`).
  Blocking honors both Claude Code protocols: stdout JSON `{"decision": "block", ...}` with
  exit 0 (all PreToolUse validators) AND non-zero exit codes (the PostToolUse plan validators'
  `sys.exit(2)`). Lowercase OpenCode tool ids are mapped back to Claude casing (`bash` -> `Bash`)
  before dispatch. Best-effort (`|| true`) validators never block. `CLAUDE_PROJECT_DIR` is
  injected from the project directory so validators behave exactly as under Claude Code.
  Hooks whose matcher maps to no OpenCode event are reported with a loud stderr WARNING,
  never dropped silently.

See `docs/features/opencode-sync.md` for guarantees and limits (hook-semantics caveats).

## Drift detection
`.opencode/SYNC_MANIFEST.json` records `generated_on`, `generator_version`, and the sha256 of
every consumed source file (settings.json, agents, commands, hooks). To check for drift without
changing anything:
```bash
python - <<'PY'
import hashlib, json, pathlib
m = json.loads(pathlib.Path(".opencode/SYNC_MANIFEST.json").read_text())
root = pathlib.Path(".")
for section in ("settings", "agents", "commands", "hooks"):
    for rel, h in m.get(section, {}).items():
        cur = hashlib.sha256((root/rel).read_bytes()).hexdigest()
        if cur != h:
            print("DRIFT:", rel)
print("generated_on:", m["generated_on"], "generator_version:", m.get("generator_version"))
PY
```
Any `DRIFT` line means that source changed and you should re-run the sync. This covers
`.claude/settings.json` too — permission or hook edits are detectable drift.

## Verify after a sync
- `opencode debug config` shows the agents, skills, and resolved permissions.
- Invoke a subagent (e.g. `@validator`) — it should be read-only.
- A `git commit` with a bad message (e.g. a co-author trailer) is blocked by
  `valor-bridge.ts`: the validator prints `{"decision": "block", ...}` to stdout and the
  plugin throws with the reason.
- Unit tests: `pytest tests/unit/test_sync_claude_to_opencode.py -n0`.
