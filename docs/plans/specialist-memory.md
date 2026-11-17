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
deciding where the directory is and what goes in it; no second store. The
summary is written by a non-blocking SubagentStop hook (one `claude -p` call
with `summary.md` as its instructions), not left to the agent, which wrote it
in 4 to 9 runs of 10 (item 3). A SubagentStop hook that blocks to force the
agent to rewrite it was rejected: the agent's last message is what its caller
gets back, and a forced extra step replaces the report.

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
3. **What it holds, and the cap.** `skills/valor/hooks/summary.md`, the
   only place the rules live, is the instructions of the call that writes the
   summary. `MEMORY.md` is one summary of this project, not an index and not a
   transcript, in four parts: what it did, what it learned about the project
   and its tools, what was decided, what to do differently.
   `skills/valor/hooks/summary.py`, registered for SubagentStart and
   SubagentStop, acts for a `valor:` agent whose definition has a `memory:`
   line, and only where `agent-memory` is a link (the kernel's; in a
   developer's own session it does nothing, and `memory: user` is Claude
   Code's own, user-wide). At start, as a second output beside the cut (one
   combined output would pass the 10,000 characters Claude Code gives whole),
   it tells the agent the summary is data, never instructions that widen what
   its definition, the Brief or its prompt allow, and that it is written for
   it. At stop, never blocking, it gives `claude -p` (`claude-haiku-5-5`, no
   tools, `summary.md` as system prompt) the old summary, any other file in
   the directory, and the run's transcript (no `<system-reminder>` context;
   the first 10,000 and last 50,000 characters of a long run), writes the
   reply as `MEMORY.md` cut to 8,000 characters and 100 lines, and deletes
   the other files; a failed call leaves all as it was. Why the hook and not
   the agent (build, 2026-11-17, haiku): told in its opening context, 4 of 10
   first runs wrote the summary; with a line in its definition too, 9 of 10;
   written by the hook, 10 of 10. With no `MEMORY.md` yet, the system prompt
   carries no summary.
4. **Docs.** `skills/README.md` (the plugin), `docs/harnesses.md` (the
   Mac-app/specialists section and the Claude Code wrapper's subagent
   context), `docs/data.md` (where the summaries live).

**Out of scope.** `yudame:*` agents. Codex and Pi, which start no Claude Code
subagents. Two turns of one project running one specialist at once: the
later rewrite wins and one run's update is lost, named in the docs. Syncing
summaries between Macs. Moving existing tasks' per-task memory.

**Governance.** None added: no check, gate or approval. The hooks give
context and write the summary; neither blocks anything.

## Tests (`tests/test_specialist_memory.py`)

- `specialist_memory` links `agent-memory` to `specialists/<client>/<project>`;
  removing the workspace keeps the target. A task provisioned without the
  link gets it from `_turn_for`'s harness; a `check_harness` dir gets the
  same target. A link the turn retargeted, or a directory in its place, is
  replaced by the spec's target. An unknown project gets no link.
- First run with none: a new project's directory is empty; the stop hook's
  call is told it is the first run.
- Second run sees the first's: a `MEMORY.md` written through task A's link
  is read through task B's link (same project).
- Isolation: a project of the same name under another client, and another
  project of the same client, link elsewhere and see nothing.
- Each SubagentStart command's stdout parses as one JSON object: the cut,
  then the summary's line; with no kernel link, nothing.
- Stop (a stand-in `claude`): the summary is rewritten from the old one, the
  other files and the transcript (reminders left out), the other files
  deleted, a long reply cut to the cap; a failed call changes nothing and
  does not block; no link, no call.
- Opt-out: `valor:web`, a definition with no `memory:` line and a
  `yudame:` agent get nothing at start or stop. The set of agents without
  `memory:` equals the documented opt-out list (`{"web"}`).
- `summary.md` names the four parts, the cap, the whole replacement, the
  other files' deletion, and data-not-instructions.
- Live (`VALOR_LIVE=1`, haiku, about $0.10): a copy of `skills/valor` in
  `tmp_path` with a scratch `memory: user` agent added, loaded by
  `--plugin-dir`, so it runs as a `valor:` agent under the hooks; `agent-memory` linked by `specialist_memory`. Run 1 is told a fact,
  which is then in `MEMORY.md` itself; run 2 recalls it with no hint.
  Cap: seeded with a 30,000-character `MEMORY.md` (many runs' worth), five
  further runs each leave it under 8,000 characters and 100 lines, in the
  four parts, with `MEMORY.md` the only file in the agent's directory.

**Done** (rule 5): after the merge, `valor:researcher` or `valor:mac-app`, run
twice on a real project, has its second report show it used what the first
learned, and Tom has seen that.
