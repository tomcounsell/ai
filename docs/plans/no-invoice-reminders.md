# Remove the weekly invoice reminders

Tom, Slack DM 2026-11-02 03:29 UTC: "No, we should not be sending weekly Email
reminders. I did not approve that. And it sounds like this feature isn't worth the
maintenance cost because I'm not going to be running over to a machine to change keys
every week or so." This removes the feature. Governance: none is added. The diff takes
out a feature Tom rejected, and the update change below only makes each Mac's launchd
schedule follow the routines its checkout holds.

## What is built

1. **The routine goes.** Delete `routines/invoices/`.
2. **`remind` goes from the skill.** In `skills/valor/skills/mercury-invoicing/mercury.py`
   delete `remind`, `reminders_sent`, `gws`, `EVERY_DAYS`, `MOST`, the `remind`
   subcommand, and the reminder branch of `email` (it builds the notice only; the
   `reminder`/`today` parameters go). `MARK` stays: `create` sets it and `unpaid` shows it.
   `create`, `notice`, `pdf`, `unpaid`, `accounts`, `received` are untouched.
3. **`wakes` goes.** Routine `wakes` exists only for `invoices` (commit 75e413f67).
   Remove the `Routine.wakes` field, `routines.wakes()`, and make
   `notices._routine_result` "the task is a routine's work": every routine result is
   quiet again, as before 75e413f67. Nothing else sets it, so keeping it is dead code
   (rule 8).
4. **Off every Mac's schedule, by the update.** Each update runs
   `permissions.ensure`, which today *reloads* any `com.valor.*` plist that is in place
   but not loaded, so the installed `com.valor.routine.invoices` (present on this Mac)
   would keep firing and fail weekly, and a hand bootout would be undone. `ensure` gains:
   a `com.valor.routine.<name>.plist` whose `<name>` is not in `routines.names()` is
   booted out and its plist deleted, reported in `fixed` as
   `com.valor.routine.<name>: removed, no such routine`. When `settings.routines_dir`
   is not a directory nothing is removed (a misconfigured checkout must not wipe every
   routine). Plists are printed, never committed, so a deletion is redone by
   `routine NAME --plist`. The bootout is injected like `reload`/`loaded` for tests.
   The merge touches `core/`, so the rollout restarts the kernel and `ensure` runs on
   every Mac as it rolls; the old `emulator` routine's plist goes too where one lingers.
5. **Docs and prompts.** Remove every mention of the reminders, the routine, `wakes` and
   the 45-day token use: `skills/valor/skills/mercury-invoicing/SKILL.md` (description,
   the token paragraph's 45-day sentence, step 4, "eligible for reminders"),
   `skills/README.md`, `docs/routines.md` (the `wakes` row, the credential example, the
   `task` runner's example becomes `sentry`), `routines/README.md`, `docs/rollout.md`
   step 6, `docs/performers.md`'s Mercury row, `core/notices.py` docstring,
   `skills/sdlc/converse.md` (the "routine whose result wakes you" sentence), and
   `docs/machine.md`/`core/permissions.py` docstrings for the new `ensure` step.

## Out of scope

- `create`, `notice`, `unpaid` and the rest of the skill: Tom is being asked separately
  whether to remove them; this task is steered with his answer.
- Historical plans (`docs/plans/mercury-invoicing.md`, `one-voice*.md`): records, unedited.
- Past `routine.registered` rows: `routines.reports` keeps listing `invoices` as a past
  routine, as it does `emulator`.

## Stakes

No email reaches a client any more from this path, but the new `ensure` step deletes
launchd jobs on every Mac on each update, so a wrong name match would silently stop a
live routine (the nightly `update` included).

## Tests

- `tests/test_permissions.py`: `ensure` with a routines dir holding `update` and agents
  holding `com.valor.routine.update`, `com.valor.routine.invoices`, `com.valor.kernel`,
  `com.valor.backup`: only `invoices` is booted out and its plist deleted, reported once;
  a second `ensure` returns `{}`; with `routines_dir` absent nothing is removed; the real
  `routines/` keeps every committed routine's plist.
- `tests/test_mercury_invoicing.py`: delete the reminder and routine tests; the notice
  test calls the new `email` signature; `main(["remind"])` exits 2 (argparse refuses it).
- `tests/test_one_voice.py`: the wakes test asserts every routine result is quiet
  (`invoices` cases dropped); the end-to-end PDF test becomes a *task* that drafts an
  invoice notice (`notice`) with its PDF, its report wakes the session, the session's
  `email.send` attaches the PDF from the task's workspace and SMTP delivers its bytes, and
  a foreign file is refused, so the one remaining send-with-PDF path stays covered.
- Full suite and `tests/test_bloat.py` green, under 5 minutes.
