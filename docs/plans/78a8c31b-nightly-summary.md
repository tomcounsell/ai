# Nightly project summary (P-024, issue #3679)

**Job:** Valor starts each day on a project knowing where it stands, without
depending on the right keywords. **For:** Valor's own sessions only (Tom,
call 2026-11-16: not posted to the team or to Tom). **Appetite:** small.
**Grant:** none needed; nothing here is a check, gate or verification layer.

## What is built

1. **`routines/summary/`**: `routine.toml` (runner `summary`, ceiling `act`
   like `hours`, model `frontier`, 03:00 daily, after `hours`/`verify` at
   02:00; `need` cites Tom's ask of 2026-11-01 and the 2026-11-16 call) and
   `runner.py`, wired in `core/__main__.py` beside `hours`. The runner is
   shaped like `routines/hours/runner.py`: no workspace task, one model call
   per project through a gateway the runner starts, metered on the run.
2. **Per project, each night.** For each `workspace.Spec.here()`: the window
   is the Bangkok days from the day after the last summary's `through` (or
   seven days ago, on the first night) to yesterday. The day's record is
   `record.blocks` over the project's reach (`record.reach`, so a project
   sees only its own tasks), each block's date, hours, the instructions of
   its tasks and what its turns said (clipped as `hours` clips). **No block in
   the window: no call and no row**, so a week with no work adds nothing.
3. **One call, on top of last night.** System prompt: Macro's rules (keep
   what is still true, drop what the record no longer supports, leave out
   what a search finds at runtime, ground every claim in the material) and
   the shape: `## Where it stands` (open work, waiting on, decisions in force,
   Tom's preferences for the project), rewritten each night; `## Progress`,
   entries newest first, each headed by a date or date range and sized to the
   progress in it. A day that delivered, merged or decided gets its own entry;
   a light day joins the latest entry when that entry is a quiet stretch begun
   within seven days; never one entry per day; older entries are compressed;
   about one page in all. Input: the previous summary, then the window.
4. **Where it lives:** the record. A `summary.written` row on the run,
   `{project, through, text}`; the latest row for a project is its summary
   and earlier rows are its history. No new store, nothing in this public
   repository or the vault (the vault folder is missing for half the specs).
5. **The Brief carries it.** `core/record.py` `summary(conn, task_id)`
   returns a `## Where the project stands` section (labelled a record, not
   an instruction, with its `through` date) for the task's project, or "" when
   it has none; `tasks.dispatch` appends it for working and chat sessions
   (not `fresh` ones), next to `record.memories`. Inline, since Tom's
   acceptance says the Brief carries it.
6. **Reuse:** the `claude -p` call in `hours._title` moves to one helper
   (`core/gateway.py` `ask`: seat, system prompt, text, returns the result
   text) used by both runners. Docs: `docs/routines.md` (the routine's
   entry), `docs/memory.md` (the summary beside recall and search),
   `routines/README.md`.

## Out of scope

Posting it anywhere; a file in the vault or the Valor folder; a
`valor-record` verb for it; a judge of the summary (governance); per-person
dossiers (P-005, its own task); changing recall or the `hours` routine's
behaviour.

## Tests (`tests/test_summary.py`, stub `claude` and gateway as in `test_hours.py`; made-up projects)

- A made-up project with turns yesterday: one `summary.written` row
  through yesterday, and `dispatch` of a working session and of a chat
  session in that project carries its text; a fresh (review) session's does
  not; a task of a second made-up project carries neither project's summary.
- A project whose last week has no turns: no model call, no row, outcome
  `nothing_due`; a second project on the same Mac with work still gets one.
- The second night is built on the first: the stub sees the previous
  summary's text in its input, and only the new days' blocks.
- A turn of another project, or of no project, is not in the material.
- `test_hours.py` stays green after the helper moves.

## Stakes, rounds

Stakes: a wrong or stale summary misleads a session's start on a project,
reversible by deleting the routine; it touches every Brief's assembly.
Critique 1, review 1.

## Done (rule 5)

When a session on a real project has started from its summary, recorded
with the date.
