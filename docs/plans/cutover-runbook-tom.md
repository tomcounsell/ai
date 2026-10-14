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

1. **Writes the cutover date** at the top of
   [cutover-runbook.md](cutover-runbook.md), on its `Cutover date` line.
2. **Keeps the push token's reach to Valor's repositories** (adding a
   collaborator is each repository owner's grant, and Tom owns these;
   the account is Valor's own, [machine.md](../machine.md), Keychain): `valorengels`, whose token is `GITHUB_PUSH_TOKEN`, is a
   collaborator with write access on `tomcounsell/ai`, `tomcounsell/popoto`,
   `yudame/psyoptimal`, and `yudame/cuttlefish`, and on no repository Valor
   must not write. Each project spec's `merge_url` and branch are where its
   merges land (the cuttlefish branch is assumed `main`; if its deploy
   branch differs, the spec carries that one). No task waits on this.
3. **Says yes to making the rebuild branch `main`** (G6), to one plain
   message; Valor then does it, after step 4 has printed its checks. First, the four open pull requests against `main` (#3607, #3598,
   #3596, #3593) are closed or relabeled as aimed at the old system, since
   they would show diverged diffs. Then the old tip is kept
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
   with current code". Afterwards no old Mac may run `/update
   --full`, whose pull falls back to a rebase and leaves that checkout
   mid-rebase. Tom tells whoever holds each of the Captain, the Bald, and
   the Pirate (by message, one line: "do not run /update --full"). Their
   update jobs stay enabled, since the cron path is safe.
4. **Keeps ruleset 24370170 disabled**, as it is today
   ([rebuild-handoff.md](rebuild-handoff.md)). It restricts updates to
   `main` with only the repository admin role able to bypass, and
   `valorengels` (push, not admin) is not, so an active ruleset would
   refuse every merge Valor lands on `main`, and a merge may land on `main`
   (Tom, 2026-10-11). What bounds Valor's merges is the token's reach
   (item 2) and the ledger's record of each one. Check:

   ```
   gh api repos/tomcounsell/ai/rulesets/24370170 --jq .enforcement
   ```

   prints `disabled`.
5. **Nothing on the Captain** (G11). psyoptimal and cuttlefish are placed
   on the Captain with their chats and wait for its own install; which Mac
   hosts bridges is Valor's to choose ([rebuild-handoff.md](rebuild-handoff.md)),
   so stopping the Captain's old bridge, worker, and email bridge goes with
   that install. Until then the Captain's old system keeps their groups,
   and its old email bridge keeps polling Tom's mailbox for the cuttlefish
   contacts list.
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
  the specs name `main` and the old Macs keep pulling it.
- **Open pull requests closed or relabeled before the force.** Reason: they
  would show diverged diffs against the rebuild.
- **No `/update --full` on an old Mac after the force.** Reason: its pull
  falls back to a rebase on divergence and never aborts it. The note goes
  to each old Mac's holder by message; the cron update path is safe and
  stays on.
- **The ruleset stays disabled.** Reason: Valor's account is not on its
  bypass list (`current_user_can_bypass: never`), so an active ruleset
  would refuse the kernel's merges to `main` along with an old Mac's push.

The old Redis, its data, and `~/Desktop/Valor` stay running and in place;
Tom removes them on a later day, once the way back is not wanted.
