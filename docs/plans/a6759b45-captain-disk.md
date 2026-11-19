# The Captain's disk, 2026-11-19: the low-disk warning reached no one

## What happened (observed on the Captain, 2026-11-20)

- Free space at each 03:00 update: 23 GB (11-13), 13, 15, 17, 15, 10, 8 (11-19).
  It hit zero at 18:42 UTC on 11-19; Postgres and the kernel went down.
- The update routine wrote its low-disk warning on 11-10, 11-12, 11-14, 11-18,
  11-19 and 11-20, every one `quiet` on the `updates` stream: the Captain has no
  session to hear it (`intake.operator_session` is None there, since Tom's DM
  is served by the Cowboy). So Tom never heard any of them.
- Workspace reclaim ran (231 removals in the kernel log, failing only while the
  disk was full). The 11-10 fix (6908ea3c1) held: no check directories pile up.
- What grew: a client task's Docker VM (`~/.colima`, 14.5 GB, rebuilt 11-17 after
  it was removed on 11-10, stopped since), and an evening burst of 12 tasks
  provisioned 16:24–17:18 UTC, on top of about 17 GB of workspaces that finished
  in the last 24 h and are kept that long by design.

## The fix

The warning moves to the `watch` routine, which already reads each watched Mac's
`free_gb` every 30 minutes over ssh (`core health`) and reports through Tom's DM on
the Cowboy. It replaces the warning in `update_runner`, so there is still one
mechanism. It is the same warning with the same 15 GB threshold (Tom, 2026-10-22),
not a new check.

- `routines/watch/runner.py`: on each run, for each target whose health was read
  (healthy or broken) and for this Mac (`shutil.disk_usage(Path.home())`), when
  free is under `DISK_LOW_GB`, `notices.request(STREAM, kind="rollout",
  about_key=f"disk:{host}:{date}")`, so each Mac gets at most one report a day.
  It calls `notices.request`, not `_tell`: a Mac with no session to hear it (the
  Captain watching the Cowboy) keeps a quiet row and sends no Sentry event,
  because the Cowboy reports its own disk.
- `core/__main__.py`: remove the disk notice and `DISK_LOW_GB` from
  `update_runner`. The summary keeps "N GB free".
- Docs: `docs/routines.md` (update and watch entries) and the watch docstring.

## Tests (tests/test_health.py, tests/test_rollout.py)

1. A watched Mac under 15 GB, whether healthy or broken, gets one report a day
   with its name and GB. A second run that day adds nothing, and the next day
   adds a new report.
2. A Mac at or above 15 GB, an unreachable Mac, and a health report with no
   `free_gb` (an older kernel) get no disk report.
3. This Mac's own low disk is reported under its own name.
4. A Mac with no session to hear it gets a quiet row and no Sentry event.
5. The update routine still prunes and logs the free space, and writes no disk
   report (update the existing test that asserts it does).

## Operations (build stage, on the Captain, no code)

- Remove merged workspaces now with `python -m core workspace remove` (about
  3.6 GB), after confirming each branch is merged and no job is running there.
  Clear rebuildable caches (`brew cleanup -s`, npm, pnpm store). Workspaces of
  ended tasks stay: several hold deliverables kept only there, or were cut short.
- Outage check: each served chat's own history (Slack, Telegram, email) from
  each service's actual down time to its restart, against the kernel's received
  rows, so a message never fetched shows too; each received one got a reply. Tasks ended 18:20–19:55 UTC by ENOSPC or by the 5-hour usage
  limit (about 10, including chat sessions) are listed, and each one whose work
  was cut short and that nobody has reopened is reopened with `core feedback`
  naming the cause; its delivery then reaches its own room, as normal. Check that
  73b03a6d8923 is moving.
- Headroom: merged removals and caches leave about 8–10 GB; the rest comes from
  the kernel's own 24 h reclaim of about 17 GB of finished workspaces (by about
  02:00 UTC 11-21) and Tom's call on the Docker VM. The delivery says so.
- For Tom: the Docker VM (14.5 GB, recommend delete), local models 11.2 GB,
  `~/src` projects, apps (Xcode 4.8, ChatGPT and its Homebrew copy, AnythingLLM),
  and the Hugging Face cache, each with its size. Free space at the end.

## Out of scope

The 24 h reclaim delay, the Captain's other machine-level notices (call profile)
that also go quiet there (named in the delivery), any quota or new gate, and
deleting anything that isn't Valor's.
