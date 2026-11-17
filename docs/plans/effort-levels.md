# Effort levels: a per-seat setting, and a replay test that sets it

Tom, 2026-11-17 (Telegram DM, message 11899): "You could run a test on a past
task using different thinking levels. But keep Fable and less thinking wins a
tie. I prefer putting more into revision skills (eg content editor) than
relying on thinking to smooth out behaviors." Answers to this task's questions
(2026-11-17): test Fable for tasks against an Opus 5.5 baseline but do not move
tasks off Opus; budget $60 metered; a past written answer stands in for chat.

## Today

`core/settings.py` `SEATS`: `chat` and `reviewer` on `claude-fable-5-2`;
`frontier` (every task turn, critique, docs, calls, routines) on
`claude-opus-5-5`. No effort is set anywhere: `harnesses/claude_code.py:199`
builds the argv with `--model` and no `--effort`, and turns run with their own
`CLAUDE_CONFIG_DIR`, so each model runs at Claude Code's default. The builder
first records what that default is per model (one probe turn each, the
request's effort field read at the gateway) and writes it in the results.

## What gets built

1. **The setting.** `settings.EFFORT: dict[str, str]`, seat name to one of
   `low|medium|high|xhigh|max`; a seat absent from it passes no flag (today's
   behaviour). Starts empty. Wherever a seat is resolved for a turn (tasks'
   Brief model via `frontier`, `tasks.turn_model` for chat, `fresh.SEATS` for
   critique, docs and review), its effort is resolved beside it and reaches
   the Claude Code builder, which adds `--effort LEVEL` when one is set.
2. **A replay's own level.** `core start --effort LEVEL` stores `effort` on the
   Brief and overrides the seat's for that task's own turns (plan, build,
   patch). Critique, docs and review keep their seats, as in live work.
3. **The replays**, no new driver: `core start --project <scratch spec>
   --base <sha> --model <seat|id> --effort <level> --parent 14c9f433dd7d`. The
   scratch spec is a copy of Valor's spec, kept outside the repo, whose `repo`
   and `merge_url` are a local bare clone made for the test, so a replay's
   merge lands in the throwaway clone and its reports come to this task.
   Questions a replay asks are answered from what the original task was told.
4. **Three tasks**, from Valor's own repo, base = the commit before the
   original's first commit:
   - *Bug fix:* the memory bench result-glob fix (plan a5995bc79, fix 09a50c824).
   - *Feature with a plan:* one merged in the last 30 days, one plan, under
     ~300 changed lines, instruction in the ledger; the builder names it.
   - *Written answer (proxy for chat):* task 8d98f8bafffd, the model review,
     judged against its delivered note.
5. **Arms per task:** Fable at low, medium, high; Opus 5.5 at its default.
   Run cheapest first, the feature last. If budget remains after the twelve,
   Opus one level below its default on the bug fix and the answer, the only
   evidence for changing `frontier`. At $60 metered, the most expensive run
   not yet started is dropped and named.
6. **Judging.** A fresh subagent gets the instruction, the original merged
   result and the arms' results labelled A to D in shuffled order, and scores
   each 0 to 5 on fidelity, correctness and simplicity (the baseline's scale,
   `docs/mission.md`), plus whether it meets the original's acceptance.
   From the ledger: metered spend, wall time, the suite's verdict, and
   revision rounds (critique and review send-backs, patches, questions).
7. **Decision**, by Tom's rule. Arms tie when both pass the suite and
   acceptance alike and their judge scores are within 1 point (one run each,
   so 1 point is noise); the lowest tying level wins. A shortfall a revision
   pass covers (wording: `yudame:de-slop` / content editing; code: the
   existing review or patch round) counts as a tie, named. Merged on a clear
   result: `EFFORT["chat"]` from the answer task; `EFFORT["frontier"]` only if
   the Opus lower-level arms ran and tied; `reviewer` stays unset (untested).
   Tasks' model is not changed; the Fable-vs-Opus numbers go in the report
   for Tom.
8. **Results** in `docs/research/effort-levels.md`: the table, the measured
   defaults, the test's own spend, and the undo line for each seat changed.

## Out of scope

Changing any seat's model; testing the reviewer's or the call worker's
level; more than one run per arm; replaying client work.

## Tests

- The argv carries `--effort LEVEL` when the seat has one, and no `--effort`
  when it has none (today's argv unchanged).
- Chat turns take `EFFORT["chat"]` whatever their Brief says, as
  `turn_model` does for the model; a critique, docs and review turn take
  their seat's level; a task turn takes `frontier`'s.
- `start --effort` is stored on the Brief and wins over the seat's for the
  task's own turns, not for its critique, docs or review; an unknown level
  is refused by the argument parser.

No check, gate or limit is added: the setting passes a flag Claude Code
already takes, and an empty `EFFORT` is today's behaviour.

Stakes: every turn's command line changes, but an unset seat is byte-for-byte
today's argv and a set one is undone by deleting its line.
