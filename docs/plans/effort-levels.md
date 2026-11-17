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
   `low|medium|high|xhigh|max`; empty to start. The Claude Code builder adds
   `--effort LEVEL` when a turn has a level and nothing when it has none, so an
   empty table is today's argv. Where the level comes from, per seat, since
   the Brief stores a model id and `chat` and `reviewer` share one:
   - *Task turns (plan, build, patch):* `Brief.effort`, written at start from
     the seat that picked the model: `intake` (the `frontier` task start,
     intake.py:708) and `core start --model SEAT` write `EFFORT[seat]`;
     `core start --effort LEVEL` writes LEVEL; a bare model id with no
     `--effort` writes none. Changing `EFFORT["frontier"]` reaches new tasks
     only; running ones keep the level they started with.
   - *Chat turns:* `EFFORT["chat"]`, resolved when the turn runs, beside
     `tasks.turn_model`, so it reaches every chat session, resumed turns too.
   - *Critique, docs, review:* `EFFORT[fresh.SEATS[stage]]`, resolved when the
     turn runs.
   - *No level, unchanged:* the call worker (call.py:229), routines
     (routines.py:233), the advisor's Claude fallback (fresh.py:318), the chat
     start at intake.py:909 (its turns take `EFFORT["chat"]` anyway) and
     `gateway.ask` (gateway.py:130). None is tested here.
2. **The replays**, with no new driver and away from every chat. They run on a
   scratch kernel database (`VALOR_DB=valor_effort_scratch`, made with
   `core migrate`), started with `core start` and driven with `core run`, so no
   serve loop or bridge reads them: `notices.hears` (notices.py:127) finds no
   chat in that database and nothing reaches Tom or his DM session. Their
   spend is metered there, and the builder reads it from there. If `core run`
   cannot drive a task from inside a turn, the builder says so and stops at
   the setting, and the test isn't faked another way.
3. **A scratch spec**, outside the repo, named `effort-scratch`: Valor's spec
   with `repo` and `merge_url` pointing at a local bare clone made for the test,
   and with no `knowledge_base`, `chats`, `rooms`, `mentions`, `board` or
   `email_cc`. So a reload by name (tasks.py:353, record.py:68, session.py:307)
   reads the scratch spec and never the real one, and merges land in the
   throwaway clone.
4. **Each replay is told it is scratch.** Its instruction opens: "Replay for a
   test. Send, post, email and push nothing; record no person facts; deliver
   in your workspace only." Then come the original instruction and every
   answer the original task was given, so no question should arise. If one
   does, the builder answers it with `core answer` in the scratch database,
   from the original's ledger, and counts it as a round.
5. **Three tasks**, from Valor's own repo, each based on the commit before the
   original's first commit:
   - *Bug fix:* the memory bench result-glob fix (plan a5995bc79, fix 09a50c824).
   - *Feature with a plan:* one merged in the last 30 days, with one plan,
     under ~300 changed lines and its instruction in the ledger. The builder
     names it.
   - *Written answer (proxy for chat):* task 8d98f8bafffd, the model review,
     judged against its delivered note.
6. **Arms per task:** Fable at low, medium and high, plus Opus 5.5 with no
   `--effort` (its default, recorded at the gateway from the request). They
   run cheapest first, with the feature last. If budget remains after the
   twelve, Opus one level below its default runs on the bug fix and the
   written answer: the only evidence that could change `frontier`. At $60
   metered, the most expensive run not yet started is dropped and named.
7. **Judging.** A fresh subagent gets the instruction, the original merged
   result, and the arms' results labelled A to D in shuffled order. It scores
   each 0 to 5 on fidelity, correctness and simplicity (`docs/mission.md`'s
   scale) and says whether it meets the original's acceptance. Spend, wall
   time, the suite's verdict and revision rounds (critique and review
   send-backs, patches, questions) come from the scratch ledger.
8. **Decision**, by Tom's rule. Two arms tie when both have the same suite
   verdict and the same acceptance verdict, and **each** of the three scores
   is within 1 point. The lowest tying level wins. A shortfall a revision pass
   covers counts as a tie, and the pass is named: `yudame:de-slop` or content
   editing for wording, the existing review or patch round for code. Merged
   on a clear result:
   - `EFFORT["chat"]`, set from the written-answer task;
   - `EFFORT["frontier"]`, only if the lower Opus arms ran and tied;
   - `reviewer` stays unset, since it isn't tested.

   No seat's model changes. The Fable-vs-Opus numbers go in the report for
   Tom.
9. **Results** in `docs/research/effort-levels.md`: the table, the measured
   defaults, the test's own spend, and the one line that undoes each seat
   changed.

## As built (2026-11-17)

- Critique, docs and review take `EFFORT` by their stage names, not their
  seats, so `EFFORT["frontier"]` reaches a task's own turns only and the
  revision rounds stay at medium unless named.
- Set on the result: chat unset (medium). It was set to low, then put back
  on feedback (2026-11-17): on the chat stand-in Fable low stated a wrong
  fact where medium was right. Tom kept Opus at medium "all the
  time" (2026-11-17): `MODEL_EFFORT` passes medium on every Claude Code call
  on Opus that names no level, the call worker, routines, the advisor's
  fallback and `gateway.ask` included.
- `core run` needs the kernel's Claude login, which a turn does not hold, so
  each replay was one `claude -p` plan-and-build run in a throwaway clone on
  this turn's gateway (`docs/research/effort-levels.md`); revision rounds
  were not measured. Opus low ran on all three tasks.

## Out of scope

Changing any seat's model; levels for the reviewer, call worker, routines,
advisor or `gateway.ask`; more than one run per arm; replaying client work.

## Tests

- The argv carries `--effort LEVEL` when a turn has a level, and no `--effort`
  when it has none; with an empty `EFFORT`, the argv is today's.
- A task started on seat `frontier` gets `EFFORT["frontier"]` on its Brief;
  one started with a bare model id and no `--effort` gets none; `--effort`
  wins over the seat's level. The argument parser refuses an unknown level.
- A task started from Tom's message (`intake._start`) or by a chat
  (`TaskStart`) gets `EFFORT["frontier"]` on its Brief.
- A chat turn, first and resumed, carries `EFFORT["chat"]` whatever its Brief
  says.
- Critique, docs and review turns carry their own stage's entry
  (`EFFORT["critique"]`, `["docs"]`, `["review"]`), not the task's level or a
  seat's.
- With `EFFORT["frontier"]` and `["chat"]` set, a call worker's turn carries no
  `--effort`, and a routine's objective and run start with none.

No check, gate or limit is added. The setting passes a flag Claude Code
already takes, and an empty `EFFORT` is today's behaviour.

Stakes: every turn's command line changes, but an unset seat is byte-for-byte
today's argv and a set one is undone by deleting its line.
