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

**Daily at 00:23 UTC** (`23 0 * * *`) — matches the retired reflection's
`every: 86400s` schedule, on a deliberately off-peak minute.

## Live Object (Claude Managed Agent — the substrate actually deployed)

The live cloud object is a **Claude Managed Agent (CMA) deployment**, created
2026-07-23 via the Anthropic API (`anthropic-beta: managed-agents-2026-04-01`)
with the account's `ANTHROPIC_API_KEY`. The claude.ai Routines `/schedule`
surface originally assumed here was not available (the CLI has no such
command, and the claude.ai scheduled-task trigger API had no repo/env/secret
binding), so the pilot landed on CMA — same machine-independent daily cloud
run, fully agent-creatable via API. See `/build-agent`
(`.claude/skills-global/build-agent/`) for the CMA primitives.

| Primitive | ID |
|-----------|-----|
| Agent (`claude-sonnet-5`) | `agent_013P25uKBwjywqddQQihyKkE` |
| Environment (limited networking: `*.sentry.io`, GitHub hosts, package managers) | `env_01UUEk4oPftG3rdFFABnnGtp` |
| Vault | `vlt_011CdJEdWxaQUiX9nUccfdDX` |
| Deployment (cron `23 0 * * *` UTC) | `depl_019ymjsGn1fwdLzGA8m8yrZt` |
| Verification session (graded outcome: `satisfied`, 2026-07-23) | `sesn_014xU2hhJG8XcATGKgd7qbyY` |

The agent's system prompt delegates to `run_sentry_triage()` exactly as the
[Prompt](#prompt) section specifies (preflight on `SENTRY_AUTH_TOKEN`/`GH_TOKEN`
presence, `SENTRY_TRIAGE_APPLY=1 COWORK_ROUTINE=1 GH_REPO=tomcounsell/ai
SENTRY_ORG_SLUG=yudame`, report to `/mnt/session/outputs/triage-report.md`).

`run_sentry_triage()` fetches unresolved issues org-wide, then filters to an
owned-project allowlist before classifying or filing anything (see
[`sentry-triage.md#ownership-filter-project-id-allowlist`](../features/sentry-triage.md#ownership-filter-project-id-allowlist)).
The deployment's env config MAY set `SENTRY_TRIAGE_PROJECT_IDS` to override
the allowlist but does not NEED to — the default (`4511091961888768`, the
`ai` Sentry project) already covers this repo, so the pilot deployment
listed above does not set it.
Each deployment run replays a graded `user.define_outcome` (live-API run,
recipe-only filing, no secret echo, report present; `max_iterations: 3`).

Audit run history via `GET /v1/deployment_runs?deployment_id=depl_019ymjsGn1fwdLzGA8m8yrZt`
or the Console — remember a failed run and a quiet run are indistinguishable
from the filed-issue surface alone.

## GitHub Access

The repo is cloned into each run's sandbox via a `github_repository` session
resource (`authorization_token` = the operator's `gh` token; `SDLC_AGENT_GH_TOKEN`
was empty in the vault on this machine). Class-C filing uses `gh issue create`
with the vault-injected `GH_TOKEN` env credential (egress-scoped to
`api.github.com`/`github.com`); dedup via `_issue_already_filed`'s
strongly-consistent `gh issue list --state open` exact-title match (fails
closed — see [`docs/features/sentry-triage.md`](../features/sentry-triage.md#duplicate-issue-dedup-tier-c)).

## Sentry Auth

**Confirmed mechanism: vault-injected `SENTRY_AUTH_TOKEN` environment-variable
credential** (egress-scoped to `*.sentry.io`; the agent never sees the raw
value). The Sentry MCP connector was deliberately NOT used: interactively
authenticated connectors are routinely absent in headless scheduled runs,
which would reintroduce the silent-files-nothing failure mode on exactly the
runs that matter.

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
notification-seam note below).

**The routine's environment MUST also set `GH_REPO` to the target repo for
issue filing** (e.g. `GH_REPO=tomcounsell/ai`). The `PROJECT_ROOT` default
above means `gh issue create` runs from the cloned repo root with no
`--repo` flag, so without `GH_REPO` every Sentry project's Class-C issue —
including projects whose slug has no `projects.json` match in the cloud —
files into whichever repo the routine happens to have cloned. A
multi-project routine variant needs per-project `GH_REPO` resolution before
it exists; the single-repo pilot sets it statically. (See also the
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
seen-ID set above — lists open issues via strongly-consistent
`gh issue list --state open --json title` and does an exact-title match
before every `gh issue create` call (fails closed on any `gh` error). It is
this function, not the seen-set, that prevents the routine from re-filing
the same Class-C issue on a subsequent daily run.
The practical consequence is simple even though the mechanism is not what
it looks like at a glance: "no new actionable Sentry issue → no new GitHub
issue → no notification." Do not describe `_issue_already_filed` as
"replacing" or "reproducing" the seen-set's delta behavior — they are
independent mechanisms that happen to produce a similar-looking outcome
(only notify on something new).

## Status

**LIVE as of 2026-07-23.** The cutover is complete, in order:

- `reflections/sentry_triage.py`'s Class-C cloud guard landed with a headless
  unit test (`COWORK_ROUTINE=1` routes filing to `PROJECT_ROOT` instead of
  `[SKIP]`) — see `tests/unit/test_sentry_triage_apply.py`.
- The verification run (`sesn_014xU2hhJG8XcATGKgd7qbyY`) passed its graded
  outcome against live Sentry and filed real Class-C issues via the recipe's
  own path (first-run backlog flush; `_issue_already_filed` dedups subsequent
  runs). Unlike the claude.ai Routines surface assumed at authoring time, the
  CMA substrate made creation and verification fully agent-executable via API.
- The vault registry (`~/Desktop/Valor/reflections.yaml`, the actual firing
  path) had its `sentry-issue-triage` entry removed post-verification, leaving
  the pointer comment; the anchored gate
  `grep -cE '^\s*-\s*name:\s*sentry-issue-triage'` returns `0` and the cutover
  guard test now runs (not skips) and passes. No parallel run, no coverage gap.
- The daily CMA deployment (`depl_019ymjsGn1fwdLzGA8m8yrZt`) is the sole
  scheduled triage; the local `/sentry` on-demand path is unchanged.

## See Also

- [`docs/features/cowork-tasks.md`](../features/cowork-tasks.md) — the reusable pattern and local-reflection-vs-Cowork decision rule
- [`.claude/skills/sentry/SKILL.md`](../../.claude/skills/sentry/SKILL.md) — the on-demand triage recipe the routine delegates to
- [`docs/features/reflections.md`](../features/reflections.md) — the local reflection scheduler this task moved off of
- [`docs/features/sentry-triage.md`](../features/sentry-triage.md) — the unchanged A–E classification rubric and apply-gate mechanics this routine invokes
