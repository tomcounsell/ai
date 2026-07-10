# OpenCode Config Sync

`scripts/sync_claude_to_opencode.py` ports this repo's Claude Code configuration into
[OpenCode](https://opencode.ai)'s layout, so OpenCode sessions in this repo run with the
same agents, commands, permissions, and safety validators as Claude Code sessions.

## What it generates

| Source (`.claude/`) | Artifact (`.opencode/`) |
|---------------------|-------------------------|
| `agents/*.md` | `agents/*.md` (frontmatter translated: model ids, tool permissions, color palette) |
| `commands/roles/*.md` (excluding `_`-prefixed includes) | `commands/*.md` with `agent: build` |
| `settings.json` permissions | `opencode.json` `permission` block |
| `settings.json` hooks + `hooks/*.py` | `plugins/valor-bridge.ts` (hook dispatch plugin) |
| all of the above | `SYNC_MANIFEST.json` (source sha256 index + `generator_version`) |

Skills are NOT migrated — OpenCode discovers `.claude/skills/*/SKILL.md` natively.

## Guarantees

- **Committed sources only.** The generator reads only the committed
  `.claude/settings.json`. The gitignored, machine-local `.claude/settings.local.json`
  is deliberately excluded, so regeneration is reproducible on every machine and
  one-off local grants never leak into committed artifacts.
- **Selective rewrite.** Before writing each artifact, the source file's sha256 is
  compared against the entry in `.opencode/SYNC_MANIFEST.json`; unchanged sources are
  skipped. Generated headers carry a stable, date-free provenance comment
  (`<!-- opencode-sync: generated from .claude/... -->`), so re-running the script
  against unchanged sources produces zero churn on any day. A content byte-compare
  backstops every write. `generated_on` in the manifest only advances when a source
  actually changed.
- **Generator versioning.** The manifest records `generator_version`. When the
  script's templates change, bumping `GENERATOR_VERSION` invalidates the hash-skip and
  forces a full content-compare pass, so stale artifacts from older templates refresh.
- **Orphan removal.** After syncing, any file in `.opencode/agents/` or
  `.opencode/commands/` whose `.claude/` source no longer exists is deleted (and its
  manifest entry dropped), with each deletion logged loudly. Only files carrying the
  generator's provenance stamp are eligible — hand-written files in those directories,
  the skills directory, and the plugin are never touched.
- **Loud hook accounting.** `parse_hooks` registers combined matchers (e.g.
  `Bash|Write`) in every matching group and prints a stderr WARNING listing any hook
  command it cannot map to an OpenCode event — safety validators are never dropped
  silently.
- **Prefix-permission fidelity.** Claude's `Bash(gh pr:*)` prefix form matches both
  `gh pr` and `gh pr <args>`, so it emits both the `"gh pr"` and `"gh pr *"` OpenCode
  glob keys.

## The `valor-bridge.ts` plugin

OpenCode has no PreToolUse/PostToolUse/Stop hook runner, so the generated plugin
re-dispatches the same Python validators on `tool.execute.before` / `tool.execute.after`
and the nearest session-lifecycle events, injecting `CLAUDE_PROJECT_DIR` so validators
behave exactly as under Claude Code.

Blocking honors both of Claude Code's hook protocols:

1. **stdout JSON** — a validator printing `{"decision": "block", "reason": "..."}` and
   exiting 0 (how every PreToolUse validator in this repo blocks). The plugin parses
   validator stdout and throws with the reason.
2. **non-zero exit** — how the PostToolUse plan validators block (`sys.exit(2)`). The
   plugin throws with the stderr tail.

Best-effort validators (`|| true` in `settings.json`) never block. OpenCode reports
lowercase tool ids (`bash`), while the validators fast-path on Claude's canonical
casing (`Bash`); the plugin maps ids back before dispatch.

## Limits and caveats

- **Hook semantics are approximate.** OpenCode's event surface differs from Claude
  Code's: `UserPromptSubmit` maps to `session.created`, `Stop`/`SubagentStop` to
  `session.idle`, `PostCompact` to `session.compacted`. Payloads carry only
  `command`/`file_path` — validators relying on other fields (e.g. `session_id`,
  tool responses) degrade to their fail-open paths.
- **Path-scoped permission grants** like `Write(.claude/hooks/**)` are not translated;
  the template already allows edit/write by default, and collapsing the scope would
  over-grant if the default ever tightens.
- **The plugin requires Bun** (OpenCode's plugin runtime) and a working `python` on
  PATH pointing at an environment that can run the validators.
- **Drift is detectable, not self-healing.** Editing anything under `.claude/` requires
  re-running the sync (see the `sync-claude-config` skill in
  `.opencode/skills/sync-claude-config/SKILL.md` for the drift-check snippet).

## Usage

```bash
python scripts/sync_claude_to_opencode.py
```

Tests: `tests/unit/test_sync_claude_to_opencode.py` (hook grouping, permission
translation, selective rewrite, block-decision plumbing with a real validator
subprocess).
