# Routine Spec: pr-review-audit (Cowork Second Migration)

> **Status: LIVE as of 2026-07-24.** This is the second reflection→cloud
> migration in this repo (issue #2068), following the `sentry-issue-triage`
> pilot documented in
> [`docs/infra/cowork-sentry-triage.md`](cowork-sentry-triage.md). The CMA is
> deployed (agent, environment, vault, daily cron deployment — IDs in the table
> below), a graded cloud run has filed a real GitHub issue, and the local
> `pr-review-audit` reflection entry has been removed from both
> `config/reflections.yaml` and the runtime vault copy
> (`~/Desktop/Valor/reflections.yaml`), leaving only a pointer comment. The
> anchored cutover-guard test (`tests/unit/test_reflections_yaml_migration.py::TestPrReviewAuditCutover`)
> now runs (not skips) and passes. The daily CMA deployment is the sole scheduled
> audit; there is no local parallel run. The cloud guards and committed recipe
> entrypoint remain unit-tested (`tests/unit/test_pr_review_audit.py`).

## Summary

`pr-review-audit` runs as a local reflection
(`reflections.audits.pr_review_audit.run` / registered as `pr-review-audit`
in `config/reflections.yaml`, daily). It scans recently merged PRs for
structured review findings and, historically, filed nothing: `dry_run` was
hardcoded `True`, so the audit has never filed a real GitHub issue in its
entire local history. This migration adds a cloud-mode code path (guards
below) that, once deployed as a CMA, will actually file. Until that CMA is
deployed and a live run is verified, the local reflection remains the only
thing exercising this code, and it still files nothing (still dry-run).

## Prompt

The eventual CMA prompt should delegate to the committed recipe by name, not
re-implement audit logic:

```
Run: python -m reflections.audits.pr_review_audit --apply
```

The recipe is defined in `reflections/audits/pr_review_audit.py` (CLI
entrypoint at the bottom of the file). `--apply` documents intent at the call
site; actual filing/cloud-sandbox behavior is controlled entirely by the
`COWORK_ROUTINE` and `GH_REPO` environment variables the CMA's environment
must set, not by the flag itself.

## Cadence

**Daily at 00:43 UTC** (`43 0 * * *`) — matches the retired reflection's
`every: 86400s` schedule, on a deliberately off-peak minute staggered from the
sentry pilot's `23 0 * * *` so the two cloud audits don't fire at the same
minute.

## CMA Primitive IDs

Created 2026-07-24 via the Anthropic CMA API
(`anthropic-beta: managed-agents-2026-04-01`) with the account's
`ANTHROPIC_API_KEY`, reusing the `sentry-issue-triage` pilot's shape via
`/build-agent` (`.claude/skills-global/build-agent/`):

| Primitive | ID |
|-----------|-----|
| Agent (`claude-sonnet-5`, v1) | `agent_011wzttSh7AAxkEPHDMoW6hG` |
| Environment (limited networking: GitHub hosts + package managers) | `env_01M3T5LmDfzYcL9UmBWLe1Mj` |
| Vault | `vlt_011CdLN2Ti5WNwQDKc7M3mmD` |
| Vault credential (`GH_TOKEN`, egress-scoped `api.github.com`/`github.com`) | `vcrd_012UkMe1aTCE7hecqCoc4wUZ` |
| Deployment (cron `43 0 * * *` UTC) | `depl_01A8RpQoD4F4o46WPo8ACrRL` |
| Verification session (graded outcome: `satisfied`, 2026-07-24) | `sesn_01M5ao1iHAZFrhC5grku3tyS` |
| Deployment test-fire run | `drun_01RoBPSPjdMNHLzEYKC3V4xc` |

Audit run history via
`GET /v1/deployment_runs?deployment_id=depl_01A8RpQoD4F4o46WPo8ACrRL` or the
Console — a failed run and a quiet run are indistinguishable from the
filed-issue surface alone (see the notification-seam note below).

**Build-agent reference-doc drift found during this deploy** (worth fixing in
`.claude/skills-global/build-agent/references/cma-primitives.md`): the live
vault-credential API nests `type`/`secret_name`/`networking` under an `auth`
object and takes the secret under `auth.secret_value` (not top-level
`key`/`access_token`/`allowed_hosts` as the reference shows); the session
`github_repository` resource field is `url` (not `repository_url`). Everything
else in the reference matched.

## Egress Scope

Matching the sentry pilot's shape: limited networking to GitHub hosts
(`github.com`, `api.github.com`) and package managers only. No Sentry access
needed for this audit.

## Injected Tokens

`GH_TOKEN` via the vault-injected environment-variable credential (same
mechanism the sentry pilot used for GitHub access) — the agent never sees the
raw value; egress-scoped to GitHub hosts.

## Env Vars the CMA Must Set

- **`COWORK_ROUTINE=1`** — switches on the cloud-sandbox code path (project
  synthesis, filing enablement, Redis-touchpoint bypass, `gh` title-dedup).
  Without it, the CMA would run daily, scan correctly, and file nothing — a
  silent failure mode indistinguishable from a healthy quiet day.
- **`GH_REPO=<org/repo>`** — e.g. `GH_REPO=tomcounsell/ai`. Required whenever
  `COWORK_ROUTINE=1`: in cloud mode the audit ALWAYS synthesizes its single
  project record solely from this value
  (`{"slug": ..., "working_directory": str(PROJECT_ROOT), "github": {"org": ..., "repo": ...}}`,
  `reflections/audits/pr_review_audit.py` in the `cloud_mode` branch), and
  never consults `~/Desktop/Valor/projects.json` — so a local smoke run on an
  operator machine with a populated projects.json still targets only the
  `GH_REPO` repo, never the configured production repos. If `COWORK_ROUTINE=1`
  but `GH_REPO` is unset or malformed (including extra path segments like
  `org/repo/extra`), the audit fails loud (returns `{"status": "error", ...}`)
  rather than silently scanning zero projects.

## Redis-Bypass Note

`PRReviewAudit` (`models/reflections.py`) is touched at three unconditional
points in the local path, each of which would crash a Redis-less cloud run.
`COWORK_ROUTINE=1` bypasses all three:

1. **`PRReviewAudit.last_successful_run()`** — the run watermark. In cloud
   mode this is replaced by a fixed lookback window instead.
2. **`PRReviewAudit.is_audited(comment_key)`** — the per-finding dedup read.
   Skipped entirely in cloud mode.
3. **`PRReviewAudit.mark_audited(...)`** — the per-finding dedup write.
   Skipped entirely in cloud mode.

**This is not a downgrade from a working mechanism — there wasn't one.**
`mark_audited` is `PRReviewAudit`'s sole writer, and it sits behind
`if not dry_run:`. Because `dry_run` was hardcoded `True` for this audit's
entire local history, `mark_audited` has **never fired** — the `PRReviewAudit`
table is always empty, `last_successful_run()` has always returned `None`,
and the local audit has always fallen back to the same fixed 1-day window the
cloud path now uses explicitly. So the cloud fixed-window + `gh` title-search
approach is the **first** real dedup this audit will ever have, not a
replacement for one that worked.

**Fixed-window constant:** `PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS`, defined in
`reflections/audits/pr_review_audit.py` near the top of the module
(`int(os.getenv("PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS", "1"))`), with a
grain-of-salt comment marking it provisional/tunable. Default is 1 day,
deliberately kept small so a steady-state daily cloud run only sees ~one day
of newly merged PRs; widen via env for a one-off backfill run.

**Per-PR dedup, not per-finding:** filing is per-PR (one issue aggregates all
of a PR's unaddressed findings, titled `PR #{pr_number}: unaddressed review
findings`), so the cloud-mode dedup replacement
(`_cloud_issue_already_filed`, same file) does a `gh issue list --label
pr-review-audit --search 'in:title "..."'` title-search keyed on `pr_number`
before calling `gh issue create`. This is coarser than the bypassed
per-finding `is_audited`/`mark_audited` pair (a new finding on an
already-filed PR is not re-detected) — an accepted, documented limitation,
not a bug.

## Notification Seam

Same pattern as the sentry pilot: **the filed GitHub issue is the
notification.** There is no local Telegram relay reachable from the cloud.
GitHub's own notification surfaces (email, mobile, Issues tab) are sufficient.
See the observability tradeoff in
[`docs/features/cowork-tasks.md`](../features/cowork-tasks.md#the-observability-tradeoff-must-read-before-adopting-this-pattern)
— a routine that silently fails (expired token, connector outage) looks
identical to a healthy quiet day.

## Verification Artifacts

1. **Local-filing-smoke: PASSED (2026-07-24).** The recipe was run locally with
   `COWORK_ROUTINE=1`, `GH_REPO=<ephemeral throwaway repo>`,
   `PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS=1` against a seeded merged PR carrying an
   unaddressed structured finding. It reached `gh issue create` and returned a
   real issue URL (`.../issues/3`, title `PR #2: unaddressed review findings`,
   labels `pr-review-audit`,`critical`); an immediate re-run correctly emitted
   `[SKIP] … already filed (cloud title-search dedup)`, confirming guard-4
   idempotency. This decoupled "does the filing/guard logic work?" from "does
   the cloud CMA deploy work?".
2. **Cloud-graded-run: PASSED (2026-07-24), session `sesn_01M5ao1iHAZFrhC5grku3tyS`,
   graded outcome `satisfied` on iteration 0.** In the cloud sandbox the agent
   ran `COWORK_ROUTINE=1 GH_REPO=<ephemeral throwaway repo> uv run python -m
   reflections.audits.pr_review_audit --apply` (recipe invoked by name, no audit
   logic reimplemented), which filed a real GitHub issue (`.../issues/5`, title
   `PR #4: unaddressed review findings`, label `pr-review-audit`) with no secret
   echoed and a report written to `/mnt/session/outputs/pr-review-audit-report.md`.
   A `[DRY RUN] Would file …` log line never qualifies as this artifact.

   **Why the verification target was an ephemeral throwaway repo, not
   `tomcounsell/ai`:** an earlier graded session run against the real target
   (same agent/env/vault) confirmed the full cloud recipe path works end-to-end
   (`uv sync`, `gh` install, `GH_TOKEN` present, recipe invoked by name, report
   written, `status: ok`) but filed **zero** issues because production genuinely
   had **no** unaddressed structured findings across a 1→7-day window ("20 PRs
   scanned, 0 unaddressed findings, 0 issues filed"). Rather than fabricate a
   finding or file backlog noise into `tomcounsell/ai` just to satisfy the
   "must file a real issue" gate, the deterministic cloud-filing proof was taken
   against a seeded throwaway repo. The throwaway repo was deleted after
   verification; the durable, auditable record is the graded session above (the
   Anthropic Console session transcript shows the recipe output and filed-issue
   URL). The daily deployment targets `tomcounsell/ai` with the default 1-day
   window and will file for real the first day a genuine unaddressed finding
   appears.

## Ordered Cutover — DONE (2026-07-24)

All four steps completed, in order:

1. ✅ Local filing smoke test (`COWORK_ROUTINE=1`, ephemeral throwaway repo,
   seeded finding) reached `gh issue create` and returned a real filed-issue
   URL; re-run deduped via the cloud title-search.
2. ✅ Deployed the CMA (agent, environment, vault + `GH_TOKEN` credential,
   daily-cron deployment) per this descriptor, reusing the `sentry-issue-triage`
   pilot's shape via the CMA API. A stray duplicate deployment created by a
   local response-parse retry was archived, leaving exactly one active
   deployment (`depl_01A8RpQoD4F4o46WPo8ACrRL`).
3. ✅ A graded cloud verification session (`sesn_01M5ao1iHAZFrhC5grku3tyS`,
   `satisfied`) filed a real GitHub issue; a separate graded run against
   `tomcounsell/ai` confirmed the recipe correctly scans production with zero
   false-positive filings. The deployment was manually test-fired
   (`drun_01RoBPSPjdMNHLzEYKC3V4xc`) before trusting cron.
4. ✅ Removed the `pr-review-audit` entry from both `config/reflections.yaml`
   and `~/Desktop/Valor/reflections.yaml`, leaving a pointer comment
   ("pr-review-audit removed from local scheduling …"). The anchored
   cutover-guard test now runs and passes; there is no local parallel run.

## See Also

- [`docs/features/cowork-tasks.md`](../features/cowork-tasks.md) — the reusable pattern, the Candidate Re-Triage table, and the Redis-bypass shim generalization this migration required
- [`docs/infra/cowork-sentry-triage.md`](cowork-sentry-triage.md) — the pilot this migration reuses (LIVE, for comparison)
- [`docs/features/reflections.md`](../features/reflections.md) — the local reflection scheduler `pr-review-audit` still runs under today
- `reflections/audits/pr_review_audit.py` — the guarded callable and CLI recipe entrypoint
- `tests/unit/test_pr_review_audit.py` — unit coverage for the four cloud guards
