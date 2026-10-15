---
tracking: none
slug: c18-yudame-plugins
type: build
status: planned
critique_rounds: 0
review_rounds: 0
---

# C18: turns drop safe mode, read the project's own instructions, and carry the yudame plugins

Stakes: critique 1, review 1. From Tom's ruling of 2026-10-15
([valor-rebuild-feedback.md](valor-rebuild-feedback.md), "Tom's ruling on
the yudame plugins"). It answers part of the open item "Skill system
requirements": the shared skills come from `yudame/skills`, the same
marketplace every person on the team installs. It adds no check, gate,
hook, validator, round, review step, refusal, or approval; it gives turns
instruction text, and authority stays in `core/`.

## The problem

Every Claude Code turn runs with `--safe-mode` and `--setting-sources ""`
(`harnesses/claude_code.py`, [docs/harnesses.md](../harnesses.md)). Safe
mode drops installed plugins, so neither `yudame` nor `yudame-dev` reaches
a turn, though both are installed on the machine. Safe mode still loads a
plugin named by `--plugin-dir` (`claude --help`, `--safe-mode`).

## Safe mode goes

`--safe-mode` was in the first harness commit (55ddc09c2, 2026-10-01) with
"bounded authority" as its reason and no incident. Prompt text holds no
authority in this design: the sandbox profile, the allowlisted
environment, the broker, and the gateway do. Its cost is real: it drops
the project's own `CLAUDE.md`, `.claude/skills/`, and `.claude/agents/`,
so a turn on cuttlefish, psyoptimal, or popoto never reads the
instructions those repositories keep for their own work. What safe mode
was said to keep out is already kept out by other means: the turn has its
own `CLAUDE_CONFIG_DIR`, so nothing user-level from this machine loads;
`--setting-sources ""` reads no settings file, so no hook, permission, or
environment from any `.claude/settings.json` loads; `--strict-mcp-config`
loads no MCP server.

0. **Drop `--safe-mode`** from `workspace_turn` and from fresh sessions,
   and from the emulator's tool-less calls only if their recorded runs do
   not change (`docs/plans/m1-5-emulator.md`). `--setting-sources ""` and
   `--strict-mcp-config` stay: the first is what keeps a candidate from
   pointing its turn's model calls past the gateway.

## What changes

1. **A kernel-held snapshot.** At a task's provisioning the kernel
   fetches `https://github.com/yudame/skills.git` into a kernel mirror and
   writes the fetched commit's `plugins/yudame` and `plugins/yudame-dev` to
   a read-only snapshot directory named by that commit, under the kernel's
   data directory. Only each plugin's `.claude-plugin/plugin.json` and
   `skills/` are written: a plugin's hooks, MCP servers, agents, or
   settings never reach a turn, whatever the repository later holds. A
   fetch that fails reuses the newest snapshot present and records why.
2. **The commit is the task's.** The commit is recorded in the task's
   Brief at start, so every turn of one task reads the same skills, and a
   new task reads the newest. "Kept recent" is per task, never mid-task.
3. **Working-session turns load it.** `claude_code.py` adds
   `--plugin-dir <snapshot>/yudame --plugin-dir <snapshot>/yudame-dev` to
   a working session's command. Fresh sessions (critique, review, the
   blind verifier) do not: their job is one verdict on `verdict.md`, and a
   pipeline skill pulls against that. Pi carries none (per-harness skill
   rendering is still left out, milestone 3).
4. **Profiles read it.** Every turn profile allows reading the snapshot
   directory and denies writing it, as with the browser cache.
5. **The stage file governs.** `skills/sdlc/channel.md` gains one
   sentence: the stage file in the Brief is the work; a plugin skill is a
   way to do it, and a plugin's pipeline (`do-sdlc`, `do-build`,
   `do-merge`) is never a route to a merge, which is the kernel's.
6. **Recorded.** `turn.started` carries the plugins' commit beside
   `persona_sha256`, so an emulator item that changes can be traced to a
   skill change.

## Done

- A turn on cuttlefish names its `CLAUDE.md` among the instructions it
  read (transcript), and no model call from it leaves except through the
  gateway (the gateway's rows match the transcript's calls), with Claude
  Code free to read the Keychain once safe mode is off.

- A test: a working turn's command names both plugin directories at the
  Brief's commit; a fresh session's names none.
- A test: a snapshot holds only `plugin.json` and `skills/`, given a
  source plugin with a `hooks/` directory and an `.mcp.json`.
- A test: a failed fetch provisions with the newest snapshot and records
  the failure.
- One real turn on the Pirate lists `/yudame:house-style` among its skills
  (read from its transcript's init event), and `turn.started` shows the
  commit.
- `docs/harnesses.md` (the `--safe-mode` row), `docs/workspace.md` and
  `docs/machine.md` say where the snapshot lives and who writes it.

## Threats

- A bad commit to `yudame/skills` reaches every new task at once. The
  emulator sweep, which reads the recorded commit, is how it is seen.
- `yudame-dev`'s pipeline skills duplicate the kernel's. Point 5 is the
  answer; if a turn still runs one, the fix is the sentence, not a refusal.
