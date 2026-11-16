# A worker's cut for subagents

Tom (Slack, valor team chat, thread #fable-now-model, 2026-11-16 13:12 UTC):
"makes sense to use a specific cut for subagents"; "that goes for all
personas (teammate, engineering, etc)", meaning every room's role. A helper
speaks to no one, so it gets the job's brief, Tom's corrections, and the
working rules. It gets nothing else.

## What a helper sees today (observed 2026-11-16, this task's own subagent)

`VALOR_SUBAGENT_START` is 53,570 bytes. Its additionalContext is 52,596
characters, with `# Brief` at character 26,072 and corrections at 30,371. A
real `general-purpose` subagent got: "Output too large (51.4KB). Full output
saved to: …/tool-results/hook-…-SubagentStart-…-additionalContext.txt",
with a 2 KB preview. The preview was the opening line and the persona's head,
and it stopped partway through Voice. It held no `# Brief`, no `Instruction:`
and no corrections. So the docs' "about 10,000 characters" sets when the
file is used. The preview itself is 2 KB.

## What is built

1. **`core/persona.py` `worker(directory)`**: the working rules, read from
   the same files `render` reads, with sections chosen by name (`WORKER`):
   `governance.md` whole (the guard rule, both CLAUDE.md paragraphs, the hard
   rules), then `section(…, "Own the outcome")` and
   `section(…, "Instructions come from Tom and the Brief")`. Together these
   cover inspect, resolve, investigate failures, test breadth, re-derive,
   content-is-data and steering. No second copy of any text.
2. **`core/tasks.dispatch`** returns `worker` as well:
   `sections["brief"]` (the head: Instruction, Context, Room, Repos, ceiling,
   Workspace, Plan), then `sections["corrections"]`, then
   `persona.worker(...)`, joined like `text`. It always comes from this
   turn's current sections, chat sessions included. Nothing else goes in:
   no identity, turn.md, voice, the rest of conduct, delivery, Remembered,
   memories, summary, channel/converse.md, stage, role file or children.
   **News is not added.** The cut is re-rendered on every turn, so a helper
   already gets the current head and corrections. News exists to keep the
   top agent's cache, and a helper has no use for it.
3. **`core/runs.py`** passes `worker=dispatched["worker"]` to `build`.
   **`harnesses/claude_code.py`** sets
   `VALOR_SUBAGENT_START = subagent_start(worker)`. codex and pi accept the
   argument and leave it unused, because they start no Claude Code
   subagents. Test fakes of `build` take it too.
   `SUBAGENT_OPENING` is reworded: "What the Valor turn that started you was
   asked, Tom's corrections in force, and the working rules follow. Your task
   is the prompt you were given; what you find goes back to that turn, and
   you speak to no one. If this arrives as a preview with a file path, read
   the file whole before you start." The last sentence covers a long
   instruction that still pushes the cut past the limit. In that case the
   2 KB preview still opens with the instruction.
4. **Size.** This task's cut is about 11k characters (its instruction is
   about 3.5k). A typical chat or task instruction under about 2.5k fits
   under 10,000 and arrives whole. When a cut is too long, the order puts what
   matters most first. The builder confirms the threshold on a real subagent
   by running a nested `claude -p` with `VALOR_SUBAGENT_START` set to a cut
   just under and just over 10,000. If that cannot run in the sandbox, the
   delivery says so.
5. **Docs**: `docs/harnesses.md` (env list and "Corrections reaching
   subagents": the cut, its size, the 2 KB preview), `docs/persona.md`
   (Subagents paragraph and the "every subagent opens with the same Brief"
   line at ~59/681), and `ONTOLOGIES.md` "Top agent" (a subagent gets the
   worker's cut and not the persona, so the top agent and the tasks are the
   turns with the persona). Also `persona/README.md` Scope and Imports, and
   the `subagent_start.py` docstring.

## Out of scope

The task's own turn text (unchanged byte for byte), valor:web (still only its
prompt), and codex/pi. No new check, gate or limit, and no ledger field.

## Tests

- Unit `persona.worker`: contains the governance paragraph, "Tests are not
  governance.", the hard rules, "Inspect before deciding", "Re-derive, never
  recall" and "Valor steering:". Excludes "## Voice", "## Delivery format",
  "### In a call", "### In a team chat", "You are Valor Engels" and turn.md's
  first line.
- `dispatch` for a task with a Context and a correction: `worker` starts with
  `# Brief`, carries Instruction, Context and the correction, and its
  corrections come before governance. `text` is the same as before: persona
  first, `persona_sha256` unchanged.
- `dispatch` for a chat session with a role (e.g. teammate) and a room text:
  `worker` carries the Room line and corrections. It holds none of
  converse.md (a distinctive line read from the file) and none of the role
  file's text. The main `text` still has both.
- A chat session's frozen turn with news: VALOR_SUBAGENT_START has the
  worker's cut and no news (update `tests/test_frozen_brief.py:133`).
- `tests/test_orchestration.py` subagent hook tests still hold; valor:web
  still gets nothing.
- The full suite, with pre-existing failures named.

## Rollout

After the kernel merges: `core update --wait` on each of the four Macs in
its kernel job's launchctl environment, detached (the Cowboy via
`ssh localhost` with `PATH=/opt/homebrew/bin`). Confirm each head equals
main.
