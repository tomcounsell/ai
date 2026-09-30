# Cowork Tasks: Reusable Pattern for Cloud-Scheduled Audits

> **Status: exercised twice at the code level; second migration not yet deployed.**
> The pattern shipped once as a live, verified pilot (`sentry-issue-triage`, see
> below). It has now been exercised a second time — `pr-review-audit`
> (issue #2068) — but only through the **code** stage: cloud guards and a
> committed recipe entrypoint (`python -m reflections.audits.pr_review_audit
> --apply`) are written and unit-tested (`tests/unit/test_pr_review_audit.py`),
> proving the guard-shim generalizes to a structurally different audit. Neither
> a local filing smoke test nor a CMA deployment has happened yet for
> `pr-review-audit` — see
> [`docs/infra/cowork-pr-review-audit.md`](../infra/cowork-pr-review-audit.md)
> for the exact pending steps. The `pr-review-audit` local reflection is still
> registered and enabled in both `config/reflections.yaml` and the runtime
> vault copy; it has **not** been cut over. Do not treat this pattern as fully
> proven for cloud deployment/verification until that migration's own status
> section says LIVE.

"Cowork" is this repo's shorthand for a **Claude Code Routine** — Anthropic's
cloud scheduled-agent capability (research preview). This doc defines the
reusable pattern for migrating a reflection-style audit ("read a cloud API,
decide what's actionable, file a GitHub issue") off the local worker and onto
a Routine. For the concrete, versioned spec of the first migration, see
[`docs/infra/cowork-sentry-triage.md`](../infra/cowork-sentry-triage.md).

## What a Claude Code Routine Is

A routine = **prompt + repo(s) + connectors + trigger**. Each run is a full
Claude Code session, on Anthropic-managed cloud infrastructure, that can run
shell commands, invoke skills committed to the cloned repo, and call cloud
**connectors** (Anthropic-hosted MCP integrations, e.g. GitHub, Slack,
Linear).

Key properties:

- **Runs even when the local machine is off.** The routine executes on
  Anthropic's infra, not this repo's worker — it has no dependency on
  `python -m worker` being alive.
- **Created via `/schedule`** in the Claude Code CLI, or at
  `claude.ai/code/routines`.
- **No local access.** A routine cannot read `~/Desktop/Valor/.env`, Redis,
  or any file outside its own fresh clone of the repo(s) it's attached to.
- Source: [code.claude.com/docs/en/routines](https://code.claude.com/docs/en/routines),
  [claude.com/blog/introducing-routines-in-claude-code](https://claude.com/blog/introducing-routines-in-claude-code).

## How to Define One

**Delegate to a committed skill or recipe. Do not re-implement judgment
logic in cloud config.** The routine's prompt should be a thin pointer —
"run `/some-skill --apply`" — not a re-encoding of the classification rubric,
scoring thresholds, or decision tree that already lives in the repo as
tested Python. Re-encoding logic in a cloud-only prompt creates a second
source of truth that silently drifts from the code every time the recipe
changes.

Concretely: identify the existing on-demand skill (`.claude/skills/...` or
`.claude/skills-global/...`) that already wraps the target behavior, and
point the routine's prompt at it. If no such skill exists yet, build and
land it in the repo first — the routine is a *scheduling wrapper* around
repo-committed behavior, not a place to write new logic.

## How to Schedule One

Routine creation is **human-gated**:

- Requires a Claude.ai Pro+ account and a manual OAuth step.
- Created via `/schedule` in the CLI, or the web console at
  `claude.ai/code/routines`.
- A headless build agent cannot create or verify a live routine
  autonomously — this is an operator action taken after the build lands
  everything needed (skill, docs, code changes) to do it in minutes.
- **Cadence is set at creation time** in the scheduling UI/command (e.g.
  daily, hourly, a cron expression) — it is not read from any file in the
  repo. The committed routine-spec descriptor (see below) records the
  intended cadence so the operator configures the live object to match, and
  so a future auditor can verify they still agree.

## How to Auth One

Routines have **no local access** — they cannot read `~/Desktop/Valor/.env`,
Redis, or any other machine-local secret store. Two auth mechanisms are
available:

- **Native connectors** — Anthropic-hosted MCP integrations (e.g. GitHub).
  Prefer these where available; no credential to provision or rotate.
- **Routine-scoped secrets** — a secret set at routine-creation time, visible
  to that routine's runs only. Used for services without a connector (e.g.
  Sentry, unless/until a Sentry connector exists in the catalog).
- API-trigger calls (routines invoked outside their schedule) need the beta
  header `experimental-cc-routine-2026-04-01`; secret/token values are shown
  once at creation and must be copied immediately.

Record which mechanism a given routine uses in its routine-spec descriptor
(`docs/infra/`) — the operator picks at creation time, and the descriptor is
what makes that choice auditable later.

## How It Reports Back

The most important part of this pattern is deciding **what "notify" means
when there's no local Telegram relay to reach**.

### The local-reflection-vs-Cowork decision rule

Ask: **does the task depend on live local state** — Redis, local files on
this machine, or the worker's in-process data (session queue, in-memory
caches, anything not reachable via a public API)?

- **Yes → it stays a local reflection.** Registered in `config/reflections.yaml`,
  run by `agent/reflection_scheduler.py` on the machine that owns the state.
  See [Reflections](reflections.md) and
  [Adding Reflection Tasks](adding-reflection-tasks.md).
- **No — it's a pure cloud-API-audit that reads a cloud API and files a
  GitHub issue when something's actionable → it's a Cowork/Routine
  candidate.** Nothing about its inputs (the audited API) or its output (a
  filed GitHub issue) requires this machine, Redis, or the local filesystem
  beyond a fresh clone of the repo.

If a task is a mix — reads a cloud API but also needs to consult local
state to decide what's actionable — it stays local. Splitting a single
judgment into a cloud half and a local half to satisfy this rule is a
rabbit hole; only migrate tasks that are cleanly one or the other.

### The "filed issue = notification" pattern

A cloud routine has no path back to the local Telegram bridge. The
established seam is: **the filed GitHub issue is the notification.**
GitHub already delivers its own notifications (email, mobile push, the
Issues tab) — there is no need to build a webhook or relay bridge from the
cloud back to this repo's infra just to duplicate that.

### The observability tradeoff (must read before adopting this pattern)

**"Filed issue = notification" has a real cost: a routine that silently
fails looks identical to a healthy quiet day.** If the routine hits an
expired OAuth token, a connector outage, or any other failure that prevents
it from running its check at all, it simply files nothing — indistinguishable
from "the audit ran and found nothing actionable." Real issues in the
audited system can pile up unseen for as long as the failure persists,
because there is no local process watching for the routine's absence the
way `logs/worker.log` would surface a crashed reflection.

**How an operator audits this:** periodically check the routine's run
history at `claude.ai/code/routines` (or via the API) to confirm recent runs
completed successfully, not just that issues were or weren't filed. A run
history showing "0 runs in the last N days" or repeated failed runs is the
signal a healthy-but-quiet routine cannot produce. There is no automated
local check for this yet — it is a manual audit step until/unless a
heartbeat-digest enhancement is built (see the routine-spec descriptor's
notes on deferred enhancements).

## Candidate Re-Triage (issue #2068)

The sentry pilot's premise was that a batch of clean "read a cloud API → file
a GitHub issue → zero local state" reflections remained, ready for
template-fill migration. **Recon for #2068 found that premise materially
optimistic: clean cloud candidates are rare.** Of the remaining audit-shaped
reflections, three file no GitHub issue at all (so the "filed issue =
notification" seam doesn't apply to them), one is a local filesystem grep,
one breaks on a fresh clone's mtime, and every remaining issue-*filer* carries
a local-Redis entanglement (streak state, a run watermark, or the local
`AgentSession`/`BridgeEvent` records that are its inputs) — an "entanglement
tax" this pattern's guard-shim approach can pay down one audit at a time, but
does not eliminate.

| Candidate | Callable | Verdict | Reason |
|-----------|----------|---------|--------|
| `pr-review-audit` | `reflections.auditing.run_pr_review_audit` | **MIGRATE (code-complete, deployment pending)** | Genuinely GitHub-API-driven and *capable* of filing, but was a **no-op** before this migration: `dry_run` was hardcoded `True`. Cloud guards (project synthesis from `GH_REPO`, filing enablement, a three-touchpoint Redis bypass, per-PR `gh` title-search dedup) are written and unit-tested. Deployment and live verification are still pending — see [`docs/infra/cowork-pr-review-audit.md`](../infra/cowork-pr-review-audit.md). |
| `skills-audit` | `reflections.auditing.run_skills_audit` | **DEFER** | Files issues, but streak/dedup state lives in local Redis; needs a state shim before it can run clean in the cloud. Candidate for a follow-up now that `pr-review-audit` has proven the shim pattern generalizes. |
| `session-intelligence` | `reflections.session_intelligence.run` | **STAYS-LOCAL (inputs)** | Its inputs are local Redis `AgentSession`/`BridgeEvent` + on-disk session logs; the cloud half has nothing to read without an export pipeline. Out of scope. |
| `docs-auditor` | `reflections.docs_auditor.run_docs_auditor` | **STAYS-DISABLED** | Currently disabled for noise; heavy local deps (Redis rotation/locks, Anthropic, vault, `valor-telegram`, git push). Re-enable/migrate is its own issue. |
| `tech-debt-scan` | `reflections.maintenance.run_legacy_code_scan` | **STAYS-LOCAL (no seam)** | Local filesystem grep, files no issue — no cloud-API audit and no notification seam. |
| `task-backlog-check` | `reflections.task_management.run_task_management` | **STAYS-LOCAL (no seam)** | GitHub-API read-only, files no issue — no notification seam. |
| `principal-staleness` | `reflections.task_management.run_principal_staleness` | **STAYS-LOCAL (mtime)** | `st_mtime`-based, breaks in a clone, files no issue. |
| `hooks-audit` | `reflections.auditing.run_hooks_audit` | **STAYS-LOCAL (log)** | Reads local `logs/hooks.log`; only the settings.json half is portable. |
| `merged-branch-cleanup` / `do-docs-branch-sweeper` / `stale-branch-cleanup` | (branch hygiene) | **STAYS-LOCAL (branches)** | Delete local `session/*`/worktree branches tied to this machine's checkout. |

This table is the durable must-NOT-migrate boundary: any future audit
candidate should be checked against it (or re-triaged the same way) before
assuming it's cloud-eligible.

### What the second migration required: the Redis-bypass shim

The sentry pilot's guard (default an unmatched working directory to
`PROJECT_ROOT`) did **not** port unchanged to `pr-review-audit` — the two
audits differ structurally (sentry iterates Sentry-API issues independent of
`load_local_projects()`; `pr-review-audit`'s outer loop *is*
`for project in load_local_projects()`, so an empty list means zero scans,
silently). Generalizing the pattern to `pr-review-audit` required a three-part
shim, all env-gated on `COWORK_ROUTINE=1` and inert otherwise:

1. **`GH_REPO` project synthesis** — when `load_local_projects()` returns
   nothing, synthesize a single project record from `GH_REPO=org/repo` instead
   of silently no-op'ing (fail loud if `GH_REPO` is unset/malformed).
2. **Fixed lookback window** — a named, provisional
   `PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS` constant stands in for the local
   `PRReviewAudit` watermark, which is bypassed entirely (along with the two
   other Redis touchpoints, `is_audited`/`mark_audited`) in cloud mode.
3. **Per-PR title dedup** — `gh issue list --search 'in:title "..."'` replaces
   the bypassed Redis dedup read; because filing is per-PR (not per-finding),
   the dedup key is `pr_number`, not the finding-level `comment_key`.

This is the reusable takeaway for the next candidate on the re-triage table
above: expect each remaining filer's local-state entanglement to need a
similarly-shaped, audit-specific shim, not a copy-paste of the sentry guard.

## Worked Example

The first (and, at time of writing, only) *fully live and verified* application
of this pattern is `sentry-issue-triage`'s migration from a local reflection to
a Routine — see
[`docs/infra/cowork-sentry-triage.md`](../infra/cowork-sentry-triage.md) for
the full spec: prompt, cadence, connectors, auth mechanism, and the
notification-seam trace through the (unchanged) triage code. The second
migration, `pr-review-audit`, is code-complete but not yet deployed — see
[`docs/infra/cowork-pr-review-audit.md`](../infra/cowork-pr-review-audit.md).

## See Also

- [`docs/infra/cowork-sentry-triage.md`](../infra/cowork-sentry-triage.md) — the pilot's routine-spec descriptor (LIVE)
- [`docs/infra/cowork-pr-review-audit.md`](../infra/cowork-pr-review-audit.md) — the second migration's routine-spec descriptor (code-complete, not yet deployed)
- [Reflections](reflections.md) — the local-reflection scheduler this pattern is an alternative to
- [Adding Reflection Tasks](adding-reflection-tasks.md) — when a task belongs in the local registry instead
