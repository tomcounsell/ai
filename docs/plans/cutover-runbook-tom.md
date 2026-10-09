---
tracking: none
type: plan
status: draft
---

# Cutover runbook: Tom's part

What only Tom does in [cutover-runbook.md](cutover-runbook.md), and how the
move of `main` was decided. Shell variables are those of the runbook's shell
setup.

## What Tom alone does

The only places a person is in the path; none is a tap on work Valor started.

1. **Writes the cutover date** at the top of this file.
2. **Grants the merge targets** (the command is always his):

   ```
   $PY -m core merge-target add https://github.com/tomcounsell/ai.git main --note "cutover"
   $PY -m core merge-target add https://github.com/tomcounsell/popoto.git main --note "cutover"
   $PY -m core merge-target add https://github.com/yudame/psyoptimal.git main --note "cutover"
   $PY -m core merge-target add https://github.com/yudame/cuttlefish.git main --note "cutover"
   ```

   The cuttlefish branch is assumed `main`; if its deploy branch differs,
   the spec and the grant carry that one. This is a grant of authority, not
   a tap on a held action: `merge-target add` is always Tom's, and no
   task waits on it once it is written.
3. **Makes the rebuild branch `main`** (G6), after step 4 has printed its
   checks. First, the four open pull requests against `main` (#3607, #3598,
   #3596, #3593) are closed or relabeled as aimed at the old system, since
   they are Tom's and would show diverged diffs. Then the old tip is kept
   under a tag:

   ```
   git push origin $(cat $CUT/old-main.txt):refs/tags/old-system
   git push origin $REB:main --force-with-lease=main:$(cat $CUT/old-main.txt)
   ```

   The first line tags the old `main` commit; the second makes `main` the
   rebuild branch's tip (the histories differ, so it is a forced update)
   and fails if `main` moved since. It is not a merge commit: a merge
   would let every old Mac fast-forward onto the rebuild and lose its
   bridge. As a forced update it is inert there: the old cron update does
   `merge --ff-only`, which fails on diverged history and logs "continuing
   with current code". His `gh` login, not Valor's, since the ruleset
   restricts updates to `main`. Afterwards no old Mac may run `/update
   --full`, whose pull falls back to a rebase and leaves that checkout
   mid-rebase. Tom tells whoever holds each of the Captain, the Bald, and
   the Pirate (by message, one line: "do not run /update --full"). Their
   update jobs stay enabled, since the cron path is safe.
4. **Sets ruleset 24370170 to active** (admin only), after item 3:

   ```
   gh api -X PUT repos/tomcounsell/ai/rulesets/24370170 -f enforcement=active
   ```

   After this, pushes to `main` go only through the kernel's merge
   performer, under the grant in item 2. To undo, `enforcement=disabled`.
   Valor's account is not a bypass actor: `gh api
   repos/tomcounsell/ai/rulesets/24370170` reports `current_user_can_bypass:
   never` for it (the ruleset's actor list itself is hidden from Valor's
   token, so Tom can confirm it in the ruleset's page).
5. **Stops the Captain's old bridge, worker, and email bridge** (G11), or
   says which person or session holds that Mac, so psyoptimal and
   cuttlefish chats can be added to their specs. On the Captain, as
   `valorengels`, the same lines as step 4 with that Mac's labels. Until
   then those two projects run by `core start --project`. The Captain's old
   email bridge also polls Tom's mailbox for the cuttlefish contacts list;
   until it stops, it marks his mail seen.
6. **Any governance grant** a diff in the final merges carries
   (`python -m core grant TASK_ID INSTANCE --note TEXT`), one per instance.
   Nothing else is held for him: an `act`-class effect from a task he
   started runs without a tap.
7. **Uses the work.** When the first task from his backlog merges, Valor
   sends one report saying what shipped and where to use it; his use is
   recorded with `python -m core used TASK --by tom`.

## Decided by default

The way back's decisions are in [cutover-runbook-back.md](cutover-runbook-back.md). For the move of `main`:

- **A forced update with the `old-system` tag, not a merge commit or a new
  default branch.** Reason: a merge commit lets every old Mac fast-forward
  onto the rebuild, whose tree has no old bridge or update script, so the
  old system dies on every Mac; a new default branch buys nothing, since
  the ruleset, the grants, and the specs all name `main` and the old Macs
  keep pulling it.
- **Open pull requests closed or relabeled before the force.** Reason: they
  would show diverged diffs against the rebuild.
- **No `/update --full` on an old Mac after the force.** Reason: its pull
  falls back to a rebase on divergence and never aborts it. The note goes
  to each old Mac's holder by message; the cron update path is safe and
  stays on.
- **The ruleset's bypass list was read.** Valor's account is not on it
  (`current_user_can_bypass: never`), so the ruleset tells an old Mac's
  push from the kernel's push.

The old Redis, its data, and `~/Desktop/Valor` stay running and in place;
Tom removes them on a later day, once the way back is not wanted.
