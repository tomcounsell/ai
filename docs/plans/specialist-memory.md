# Specialists keep a compacted history

**Ask.** Tom, his DM, 2026-11-17 03:20 UTC: "Perhaps a middle ground is that
specialists keep a compacted history. So re-use gets the same system prompt
+ compacted summary of past work + new prompt". Read back at 03:22; nothing added.

**Premise, checked (Claude Code 2.1.325, binary read and run).** An agent
definition's `memory:` frontmatter (`user`, `project`, `local`) gives the
agent a memory directory. Its `MEMORY.md` (first 200 lines or 25 KB) goes into
the agent's system prompt after the definition's text, and Write and Edit are
turned on even when `tools:` leaves them out. `user` is
`$CLAUDE_CONFIG_DIR/agent-memory/<agent type>/`, where a plugin agent's type
`valor:mac-app` becomes `valor-mac-app`. `project` and `local` are under the
workspace's `.claude/`, which is removed with the workspace, and Claude Code
skips their `MEMORY.md` when a link leads out of the checkout. `user` follows a
link. Experiment (`$TMPDIR`, haiku): with `agent-memory` a link to a directory
outside the config dir, a `memory: user` agent quoted a seeded `MEMORY.md`
from its system prompt. Told to rewrite it before answering, it wrote back
through the link. A turn's `CLAUDE_CONFIG_DIR` is the task's own
(`<task>/state/work/claude`, `core/workspace.py`), so without a link the
memory would go when the task's workspace is removed. No setting turns memory on for
every agent; SubagentStart and SubagentStop hooks exist, and Valor's plugin
already has a SubagentStart hook. Chat compaction (`skills/sdlc/compact.md`
through the PreCompact hook) compacts a session's own context. It has nothing
to compact when a subagent ends, so it does not fit; what is reused is its
form: one instruction file and Claude Code doing the work.

**Choice.** Claude Code's own agent memory (`memory: user`), with Valor
deciding only where the directory is and what goes in it. No summarizer, no
second store. A SubagentStop hook that blocks to force the rewrite was
rejected: the agent's last message is what its caller gets back, and a forced
extra step replaces the report.

## What is built

1. **Where it lives.** New `workspace.specialist_memory(config_dir, project)`
   points `<config_dir>/agent-memory` at
   `<work_dir>/specialists/<client>/<project>/`, the client read from the
   project's spec (`Spec.load`; `_` for a spec with no client), names cleaned
   as `cache_path` does. It works the target out from the spec every time and
   never reads what is at `agent-memory`: whatever is there (a link, a
   directory, nothing) is removed with `workspace.rmtree` (no link
   followed, flags cleared; no second remover) and the link made again, so a turn cannot
   retarget a later session's memory. A project whose spec does not load gets
   no link (today's per-session memory). It is called where each turn's
   harness is prepared: `_turn_for` (`core/__main__.py`), from the Brief's
   project, for every working turn, so tasks and chat sessions provisioned
   before this get it on their next turn; and `check_harness`, which gains a
   `project` argument its callers pass from the Brief, for each fresh session
   (critique, review, test, docs). So every turn of the project on this Mac
   sees one directory per specialist (`valor-mac-app/MEMORY.md`), outside
   every workspace and every `TMPDIR`; removing a workspace removes the link
   and leaves the target. Another project, or another client's project of the
   same name, links elsewhere.
2. **Who has one.** `mac-app.md` and `researcher.md` gain `memory: user`;
   `web.md` has none. A new definition carries `memory: user`. It opts out
   by leaving the line out and adding itself to the opt-out list in
   `skills/README.md` and the test.
3. **What it holds, and the cap.** New `skills/valor/hooks/summary.md`, the
   only place the rules live. `MEMORY.md` is one summary of this project, not
   an index and not a transcript, in four parts: what it did, what it learned
   about the project and its tools, what was decided, what to do differently.
   It overrides Claude Code's own memory instructions, which tell a memory
   agent to keep `MEMORY.md` as an index and write each memory as its own
   file: here the memory is one file, `MEMORY.md`, and no other; any other
   file found in the directory is folded into the summary and deleted, since
   only `MEMORY.md` reaches the next run.
   Before its final report, the agent rewrites the summary whole with this run folded
   in, keeping what is still true and dropping what is settled. It stays under
   8,000 characters and 100 lines, well inside Claude Code's 25 KB / 200-line
   load, so a summary found over the cap is compacted on the next run. It is
   written even when the run failed. The summary is data, never instructions: it
   cannot widen what the definition, the Brief or the prompt allow. Claude Code's own
   "user scope: keep learnings general" line does not apply: this directory
   is this project's alone. `subagent_start.py` reads `VALOR_SUBAGENT_START`
   as the JSON it is (`claude_code.subagent_start`), and when the starting
   agent is a `valor:` agent whose definition (`agents/<name>.md` beside the
   hook) has a `memory:` line in its frontmatter, adds `summary.md`'s text to
   `additionalContext`; with the variable unset (a developer's own session)
   it prints a SubagentStart object carrying the rules alone. Its output is
   always one JSON object or nothing. With no `MEMORY.md` yet,
   the system prompt carries no summary.
4. **Docs.** `skills/README.md` (the plugin), `docs/harnesses.md` (the
   Mac-app/specialists section and the Claude Code wrapper's subagent
   context), `docs/data.md` (where the summaries live).

**Out of scope.** `yudame:*` agents. Codex and Pi, which start no Claude Code
subagents. Two turns of one project running one specialist at once: the
later rewrite wins and one run's update is lost, named in the docs. Syncing
summaries between Macs. Moving existing tasks' per-task memory.

**Governance.** None added: no check, gate or approval. The hook change gives
context; it blocks nothing.

## Tests (`tests/test_specialist_memory.py`)

- `specialist_memory` links `agent-memory` to `specialists/<client>/<project>`;
  removing the workspace keeps the target. A task provisioned without the
  link gets it from `_turn_for`'s harness; a `check_harness` dir gets the
  same target. A link the turn retargeted, or a directory in its place, is
  replaced by the spec's target. An unknown project gets no link.
- First run with none: a new project's directory is empty. The hook's
  context for `valor:mac-app` carries `summary.md`; Claude Code puts no
  summary in a prompt when no `MEMORY.md` exists.
- Second run sees the first's: a `MEMORY.md` written through task A's link
  is read through task B's link (same project).
- Isolation: a project of the same name under another client, and another
  project of the same client, link elsewhere and see nothing.
- The hook's stdout parses as one JSON object whose `additionalContext`
  carries both the cut and `summary.md`; with `VALOR_SUBAGENT_START` unset it
  carries `summary.md` alone, still a valid SubagentStart object.
- Opt-out: `subagent_start.py` gives `valor:web` nothing and a definition
  with no `memory:` line no `summary.md`. The set of agents without `memory:`
  equals the documented opt-out list (`{"web"}`).
- `summary.md` names the four parts, the 8,000-character / 100-line cap,
  whole rewrite before the report, and data-not-instructions.
- `summary.md` says one file, `MEMORY.md`, no other files, others folded
  in and removed, overriding the index format.
- Live (`VALOR_LIVE=1`, haiku, about $0.10): a copy of `skills/valor` in
  `tmp_path` with a scratch `memory: user` agent added, loaded by
  `--plugin-dir`, so it runs as a `valor:` agent and the hook's `summary.md`
  reaches it; `agent-memory` linked by `specialist_memory`. Run 1 is told a fact,
  which is then in `MEMORY.md` itself; run 2 recalls it with no hint.
  Cap: seeded with a 30,000-character `MEMORY.md` (many runs' worth), five
  further runs each leave it under 8,000 characters and 100 lines, in the
  four parts, with `MEMORY.md` the only file in the agent's directory.

**Done** (rule 5): after the merge, `valor:researcher` or `valor:mac-app`, run
twice on a real project, has its second report show it used what the first
learned, and Tom has seen that.
