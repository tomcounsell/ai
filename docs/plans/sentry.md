---
tracking: https://github.com/tomcounsell/ai/issues/3674
slug: sentry
type: feature
appetite: Small
status: Ready to build
created: 2026-10-30
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-10-30T15:46:21Z
critique_rounds: 2 (round 1 and round 2 fixes applied)
---

# Sentry: every exception reported, and a nightly review

**Ask.** Tom, 2026-10-30: "add back our old Sentry integration, so we can
track any and all exceptions and have an extra routine for reviewing Sentry
issues". Rule 2: the old system had both (`old-system:monitoring/sentry_config.py`,
`old-system:reflections/sentry_triage.py`). The 2026-10-30 outage
(`text = smallint` in `intake.root_of`, chat down on all four Macs, verify
dead eight nights) reached no exception tracker. This adds a capability and
no governance: nothing is checked, gated or refused.

**Freshness.** Baseline aee0336e5; no commit on `core/`, `bridges/`,
`tools/`, `routines/` since the issue was filed. No process imports
`sentry_sdk` today. #3645 and #3636 stay open. `c19-sentry-route.md` (turns'
`sentry-cli` login) is done and is what the routine uses.

**Starting point** (reuse, rule 10): a draft in the planning session's
scratchpad, ported from `sentry_config.py` without its noise filters,
hibernation hook and environment guessing, none of which has a cause here
yet: `core/sentry.py` with `init(component)`, which reads the DSN from
`settings.sentry_dsn_file`, returns False with no file or an empty file,
and otherwise calls `sentry_sdk.init(dsn, release=<HEAD>,
server_name=settings.machine, environment="production")` and tags
`component`; `Settings.sentry_dsn_file` beside `openai_keyfile`
(`~/.config/valor-kernel/sentry-dsn`); `sentry-sdk>=2.73.0` in
`pyproject.toml` and `uv.lock`. The draft's pytest skip is dropped: no
test calls a process's `main`, so no test reaches `init`. No handling is
added for a malformed DSN or Sentry's rate limit; neither has happened. The DSN never enters the repo, a plan or an issue: the repo is public.

**Build.**
1. Land the draft. `init` runs only in the resident processes:
   `sentry.init("kernel")` first in `_serve()` in `core/__main__.py` (not
   `main`, which dispatches 35 CLI subcommands), `sentry.init(channel)` inside the
   `run` branch of each `bridges/{telegram,slack,email,local}/__main__.py`
   (not `login`, `keys` or `--plist`), and `tools/voice.py` on its `run`
   verb (`"voice"`). The default `LoggingIntegration` turns the bridges'
   and voice's `log.exception` / `log.error` (13 sites) into events with no
   further code.
2. `core/serve.py` prints its handled exceptions with `_log` (stderr), which
   Sentry never sees. Each `except` whose exception is only printed calls
   `sentry_sdk.capture_exception(exc)` beside its `_log`, a no-op without
   `init`. Verified at aee0336e5, 16 sites: 339, 373, 381, 622, 627, 631,
   638, 643, 671, 737, 954, 965, 1030, 1122 (the router's re-raise, so
   every step failure), 1261, 1300. At 670-671 and 1029-1030 (done
   callbacks) no `exc` is in scope: the condition becomes
   `if not job.cancelled() and (e := job.exception()) is not None:`, then
   `_log(...)`, then `sentry_sdk.capture_exception(e)` (`exception()`
   raises `CancelledError` on a cancelled task); 1031's
   `self.parked[task_id] = latest` stays in that branch. Left out: exceptions that
   already become ledger rows reported to Tom (`_failed` for rollouts,
   `workspace.failed` at 1087, and setup's at 399, which lands in a
   `rollout.ended` row). 622, 631, 737 and 1300 sit in loops that
   retry each tick, so a lasting fault sends an event every tick from each
   Mac. That volume is accepted: no filter or rate limit is added (none is
   granted), and Sentry groups the events into one issue.
3. `routines/sentry/routine.toml`: `runner = "task"`, `project = "valor"`,
   `branch = "main"`, `ceiling = "act"`, `model = "frontier"`, nightly at
   06:00 (02-05 are taken), `mission_item` and `need` citing Tom's ask and
   the outage, `created = 2026-10-30`. Its instruction carries the old
   triage's A-E scheme as prose, since `sentry-cli` in the turn replaces
   the old 1026-line API client (the turn checks `sentry-cli issues --help`
   for the verbs and flags; the plan asserts none). List unresolved `valor`
   issues; resolve stale ones (no events in 30 days), and resolve each whose `[VALOR-N]` GitHub issue is closed by a merged
   fix (Sentry reopens it as a regression if it fires again). One issue
   with a high event count from a retry loop is one defect: one GitHub
   issue, one fix task. For a real defect, run
   `gh issue list --repo tomcounsell/ai --state all --search 'VALOR-N in:title' --json number,title`;
   a match counts only when a title contains the literal `[VALOR-N]`. With
   no match, file an issue titled `[VALOR-N] <Sentry title>` and
   start a fix task. Stop with `Done:` listing each issue filed and task
   started; with neither, stop with `Done: nothing new. Send Tom nothing.`
   (`Send Tom nothing.` is how `routines/conditions/routine.toml` keeps a
   quiet run silent). No apply switch: resolving and ignoring are live from the first
   night on purpose (the old `SENTRY_TRIAGE_APPLY` dry-run gate is not
   ported); a wrong resolve reopens on the next event.
4. `tests/test_sentry.py`: `init` returns False and calls nothing with the
   key file absent or empty; with a file (`sentry_sdk.init` patched) it passes the DSN and tags the component; a
   step that raises in the kernel calls `capture_exception` (patched) once.
   The routine toml loads through `core/routines.py` like the others.
5. Docs: `docs/rollout.md` step 5 adds `sentry-dsn` to the copied keys;
   `routines/README.md` names `sentry` beside `invoices` as a `task`
   routine.

**Rollout, after merge** (Claude does it, over SSH as `valorengels@`).
Fetch the DSN once from `GET /api/0/projects/yudame/valor/keys/` with
`SENTRY_AUTH_TOKEN` from Tom's `~/Desktop/Valor/.env`, never printing it;
write it to `~/.config/valor-kernel/sentry-dsn` (mode 600) on the Cowboy,
then copy it from the Cowboy to the Pirate, the Captain and the Bald as step
5 does. `/update` each Mac so the kernel, bridges and voice restart with
`init`. Print and bootstrap `com.valor.routine.sentry` on the Mac the
`valor` project is placed on (`python -m core routine sentry --plist`).

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `uv run pytest -q -n auto -p no:cacheprovider` | exit code 0 |
| Lint | `uv run ruff check .` | exit code 0 |
| Every printed handler captures | `grep -c "sentry_sdk.capture_exception" core/serve.py` | output = 16 |
| Each process inits | `grep -l "sentry.init(" core/__main__.py bridges/*/__main__.py tools/voice.py \| wc -l` | output = 6 |
| No DSN committed | `git grep -nE "https://[0-9a-f]{32}@" -- . ':!uv.lock'` | exit code 1 |

**Done** (rule 5): a forced exception from the kernel, a bridge and voice
on the Cowboy appears in `valor`, the key file is on all four Macs, and the
routine's first night has triaged the backlog; each recorded with its date.
