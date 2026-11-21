# Restart what the 2026-11-19..21 usage-limit outages left unhandled

Operations task 0b976535bda8, no code change. The valor project's own
losses are covered elsewhere and are out of scope.

## What the survey already shows (observed 2026-11-21 12:38 UTC)

`task.ended` rows since 2026-11-19 15:00 UTC that name a usage limit,
exhausted credits, or ENOSPC (each Mac's kernel database, port 5433 on
the Captain, Pirate and Bald, 5432 on the Cowboy; psql prints Asia/Bangkok):

- **Captain**: 37 endings. These include the six that 709b0d649ddb restarted,
  of which five were delivered and plan 399d9fc62dc6 ended again. The
  Captain also hit the shared weekly model limit from about 11-19 23:30
  UTC to 11-21 07:00 UTC. That ended team-chat sessions on two
  projects, email-thread sessions, and two email-started tasks.
- **Pirate**: 3 team-chat sessions ended on "out of usage credits", the
  last at 06:28 UTC on 11-21.
- **Bald**: 4 team-chat sessions ended on the weekly limit.
- **Cowboy**: only the valor project's own sessions (out of scope).
- **The 709b0d649ddb fix never merged** (it died in review on the limit).
  All four Macs run fcb4edd48. So an inherited unanswered message is
  still dropped at the second ended session, and a report to an ended
  session is still stranded.

## What will be done, in order

1. **Limits first (the unknown).** On each Mac, through Valor.app (a
   one-off `gui/` launchd job running `valor-run`, as docs/rollout.md
   step 4), check the Claude login's state with one minimal call. The
   Pirate's "out of usage credits" may still hold, and only Tom can
   clear it. If it holds, nothing is restarted there and it is reported
   as a blocker.
2. **Inventory, per Mac and project.** Classify each ending:
   - a task that was never restarted or delivered;
   - a restarted task that failed again;
   - a chat session whose latest person's message has no reply since,
     checking every later session of the same chat;
   - a report or question addressed to a session that had already
     ended.

   An item that a later session or a later task already handled is
   marked handled, with that row's id, and is not restarted.
3. **Restart through the kernel's own path.**
   - A task gets `python -m core feedback TASK "<what cut it off; finish
     and commit>"`, then `run`, so it keeps its workspace, Brief and
     parent.
   - An unanswered person's message gets the ended-session hand-on (the
     `ended` notice in `core/notices.py`), so that chat's own session
     answers.
   - Where the workspace was reclaimed, the work starts fresh from the
     same instruction under the same project and chat, marked as a
     fresh start.
   - A hand-on is confirmed by a `message.sent` reply row. Because the
     fix is not live, a second failure means handing it on again by
     hand.
4. **Pacing.** At most two running per Mac (the kernel's cap). Wait for
   one turn to end before the next; people waiting go first, then tasks.
   Stop starting new work on a Mac whose limit refuses a turn.
5. **The Captain's disk** (13 GB free of 228 GB at 12:38 UTC).
   - Measure `~/valor-tasks` (61 entries, `cache`, `specialists`),
     `~/src` worktrees, Docker, local models, Postgres test databases,
     and the Homebrew cache, with sizes.
   - Remove only Valor's own workspaces of tasks that ended, were
     delivered or merged and are past the reclaim window, through
     `python -m core workspace remove`, plus caches the kernel would
     reclaim anyway.
   - Everything else is listed for Tom with its size and a
     recommendation.
6. **Report** in `.valor/done.md`:
   - per Mac and project: what was unhandled, what was restarted and its
     state now, and what could not be restarted and why;
   - anyone still waiting for an answer (name, chat, since when);
   - the disk figures before and after, and the defects found.

## Out of scope

Any code change, including landing the unmerged 709b0d649ddb fix. It is
named as a defect in the report, unless it blocks a restart. Also out of
scope: the valor project's losses, any message to a person, and any new
check or gate.

## How it is shown to work (no code, so ledger evidence)

- Each restarted task: a new `turn.started` after the feedback, and its
  state at report time.
- Each hand-on: a reply row in that chat after the person's message.
- Each limit check: the call's result.
- Disk: `df` before and after, and each removal named with its size.
- Cases checked beyond the obvious:
  - a chat a later session already answered (no duplicate reply);
  - a session that held only notices, not a person's message (left
    alone);
  - a session that inherited a message as `child.reported`;
  - an email-thread session;
  - a reclaimed workspace;
  - a restarted task that ended again (399d9fc62dc6);
  - a Mac whose account is still refused (the Pirate).
