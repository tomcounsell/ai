# Routine Spec: sentry-issue-triage (Cowork Pilot)

> **This is a PILOT.** `sentry-issue-triage` is the first reflection→cloud
> migration in this repo, exercising the pattern documented in
> [`docs/features/cowork-tasks.md`](../features/cowork-tasks.md). Issue #2068
> (migrating the remaining cloud-API-audit reflections) depends on this
> pattern working. This file is an infra doc — it is **not archived**; it
> stays as the permanent versioned record of the live Claude Code Routine so
> the cloud object is auditable and reconstructable from the repo.

## Summary

`sentry-issue-triage` used to run as a local reflection
(`reflections.sentry_triage.run_sentry_triage`, registered in
`config/reflections.yaml`, gated to the machine that owns `project_key:
valor`). It has been migrated to a Claude Code Routine so it runs daily on
Anthropic's cloud infrastructure, independent of any single machine being
online. The underlying Python callable and its A–E classification rubric are
**unchanged** — only the trigger moved.

## Prompt

The routine's prompt delegates to the existing on-demand triage recipe. It
does **not** re-encode the A–E rubric — that logic lives in
`run_sentry_triage` (`reflections/sentry_triage.py`) and is the single
source of truth for classification.

```
Run /sentry --apply
```

The recipe itself is defined in [`.claude/skills/sentry/SKILL.md`](../../.claude/skills/sentry/SKILL.md),
which wraps `run_sentry_triage()` with `SENTRY_TRIAGE_APPLY=1` for live
writes (files GitHub issues for Class C, updates Sentry state for A/B/E).

## Cadence

**Daily** — matches the retired reflection's `every: 86400s` schedule.

Cadence is configured in the routine's scheduling UI/command at creation
time (see [Trigger](#trigger)); this doc records the intended value so the
operator can confirm the live object matches.

## Trigger

**Schedule (cloud cron).** Created via `/schedule` in the Claude Code CLI,
or at `claude.ai/code/routines`. Creation is human-gated (Claude.ai Pro+
account + OAuth) — see [`docs/features/cowork-tasks.md#how-to-schedule-one`](../features/cowork-tasks.md#how-to-schedule-one).

## Connectors

**GitHub** — via the native GitHub connector, or the `gh` CLI against the
cloned `tomcounsell/ai` repo (whichever the routine environment provides).
Used to file Class-C issues (`gh issue create`) and to dedup via
`_issue_already_filed`'s title-search (`gh issue list --search`).

## Sentry Auth

The routine needs `SENTRY_AUTH_TOKEN` to call the Sentry API. Two
mechanisms are possible; the operator picks at creation time and this
section is updated to record whichever was chosen:

1. **Routine-scoped secret** holding `SENTRY_AUTH_TOKEN` (the default
   expectation, since no Sentry connector is confirmed to exist in the
   catalog at pilot-authoring time).
2. **Sentry connector**, if one is available in the connector catalog by
   the time the routine is created.

Both options are noted here rather than the doc asserting one as final —
confirm at creation and update this section with the mechanism actually
used, since it is directly load-bearing for anyone reconstructing this
routine later.

## Working-Directory / cwd Note (Class-C Filing Guard)

`run_sentry_triage`'s Class-C filing loop normally resolves the
GitHub-issue working directory (`proj_wd`) by matching the Sentry project's
slug against `load_local_projects()` — which reads
`~/Desktop/Valor/projects.json` (vault) or `config/projects.json`
(gitignored, untracked), filtered to entries whose `working_directory`
exists on disk. In a fresh cloud clone, **none of that exists**:
`load_local_projects()` returns `[]`, and every Class C would hit the
`[SKIP] no working directory for project {proj}` branch — filing zero
issues, every run.

The fix is an env-gated guard added to `run_sentry_triage`
(`reflections/sentry_triage.py`, in the Class-C loop): when
`load_local_projects()` yields no match for a project's slug **and**
`COWORK_ROUTINE == "1"`, `proj_wd` defaults to `PROJECT_ROOT` (the cloned
repo root) instead of skipping. The guard is inert when the env var is
unset, so the local `/sentry` on-demand path and
`tests/unit/test_sentry_triage_apply.py` are unaffected.

**The routine's environment MUST set `COWORK_ROUTINE=1`.** Without it, the
routine will run every day, classify issues correctly, and file nothing —
a silent failure mode indistinguishable from a healthy quiet day (see the
[notification-seam](#notification-seam-what-actually-fires-in-the-cloud)
section below and the observability tradeoff in
[`docs/features/cowork-tasks.md`](../features/cowork-tasks.md#the-observability-tradeoff-must-read-before-adopting-this-pattern)).

## Notification Seam: What Actually Fires in the Cloud

**The filed GitHub issue IS the notification.** GitHub's own notification
surfaces (email, mobile, Issues tab) are sufficient — there is no local
Telegram relay reachable from the cloud, so the routine does not attempt to
push a message anywhere.

This is worth tracing precisely, because `reflections/sentry_triage.py` is
**unchanged** apart from the guard above — its local delta-state path still
executes on every cloud pass, just inertly:

| Function | What it does locally | What happens in the cloud |
|----------|----------------------|----------------------------|
| `_load_seen_ids()` | Reads `data/sentry_triage_seen.json` to compute which Sentry issue IDs are new since last run | Reads a path that doesn't exist in a fresh clone → returns `None` → the run takes the first-run "seed silently" branch |
| `_save_seen_ids()` | Persists the current issue-ID set back to `data/sentry_triage_seen.json` for the next run's delta | Writes into the throwaway clone's filesystem — discarded when the cloud sandbox tears down after the run |
| `_send_telegram_notification()` | Shells out to `valor-telegram send` to push a delta summary to Telegram | `valor-telegram` is not on PATH in the cloud clone → the subprocess call raises `FileNotFoundError`, which is swallowed by the existing exception handling |

**These three functions are reachable-but-inert dead code in the cloud, not
retired.** They are still load-bearing for the local `/sentry` on-demand
path, which is untouched and continues to use them normally.

**Be precise about what actually gates duplicate filing in the cloud:**
`_issue_already_filed(title, cwd)` — a **separate** code path from the
seen-ID set above — does a GitHub title-search before every `gh issue
create` call. It is this function, not the seen-set, that prevents the
routine from re-filing the same Class-C issue on a subsequent daily run.
The practical consequence is simple even though the mechanism is not what
it looks like at a glance: "no new actionable Sentry issue → no new GitHub
issue → no notification." Do not describe `_issue_already_filed` as
"replacing" or "reproducing" the seen-set's delta behavior — they are
independent mechanisms that happen to produce a similar-looking outcome
(only notify on something new).

## Status

- Pilot. `reflections/sentry_triage.py`'s Class-C cloud guard has landed and
  is covered by a headless unit test (`COWORK_ROUTINE=1` routes filing to
  `PROJECT_ROOT` instead of `[SKIP]`) — see `tests/unit/test_sentry_triage_apply.py`.
- Creating the live routine and confirming a verification run files a
  Class-C issue is an **operator action** (Claude.ai Pro+ account, manual
  `/schedule` or web-console creation) — not something a headless build
  agent can do. See
  [`docs/features/cowork-tasks.md#how-to-schedule-one`](../features/cowork-tasks.md#how-to-schedule-one).
- The local `sentry-issue-triage` reflection entry is removed from
  `config/reflections.yaml` (and the vault copy
  `~/Desktop/Valor/reflections.yaml`, the actual firing path) only after the
  routine is verified live, per the ordered cutover sequencing in the
  originating plan — no parallel run, no coverage gap.

## See Also

- [`docs/features/cowork-tasks.md`](../features/cowork-tasks.md) — the reusable pattern and local-reflection-vs-Cowork decision rule
- [`.claude/skills/sentry/SKILL.md`](../../.claude/skills/sentry/SKILL.md) — the on-demand triage recipe the routine delegates to
- [`docs/features/reflections.md`](../features/reflections.md) — the local reflection scheduler this task moved off of
- [`docs/features/sentry-triage.md`](../features/sentry-triage.md) — the unchanged A–E classification rubric and apply-gate mechanics this routine invokes
