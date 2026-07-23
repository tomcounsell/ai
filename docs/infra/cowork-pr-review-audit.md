# Routine Spec: pr-review-audit (Cowork Second Migration)

> **Status: CODE-COMPLETE, NOT YET DEPLOYED.** This is the second
> reflection→cloud migration in this repo (issue #2068), following the
> `sentry-issue-triage` pilot documented in
> [`docs/infra/cowork-sentry-triage.md`](cowork-sentry-triage.md). The cloud
> guards and a committed recipe entrypoint exist and are unit-tested
> (`tests/unit/test_pr_review_audit.py`, 34+ passing tests), but **no CMA has
> been created, no cloud cron exists, and no live filing has occurred.** The
> local `pr-review-audit` reflection is **still enabled** in both
> `config/reflections.yaml` and the runtime vault copy
> (`~/Desktop/Valor/reflections.yaml`) — it has **not** been cut over, and
> continues to run daily in dry-run mode exactly as before (`COWORK_ROUTINE`
> is unset locally, so all four cloud guards are inert). Do not read this
> document as describing a live routine; it is the descriptor an operator will
> use to deploy one, and the record of what remains before cutover.

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

**Daily** — matches the reflection's `every: 86400s` in `config/reflections.yaml`.
No cron expression has been chosen yet; when the CMA is deployed, pick an
off-peak minute (following the sentry pilot's `23 0 * * *` convention) and
record it here.

## CMA Primitive IDs

**PENDING — not yet created.** No agent, environment, vault entry, or
deployment exists for `pr-review-audit`. When the CMA is deployed via
`/build-agent` (`.claude/skills-global/build-agent/`), reusing the pilot's
primitives and shape, record the real IDs here in the same table form the
sentry descriptor uses:

| Primitive | ID |
|-----------|-----|
| Agent | PENDING |
| Environment | PENDING |
| Vault | PENDING |
| Deployment (cron) | PENDING |
| Verification session (graded outcome) | PENDING |

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

1. **Local-filing-smoke URL: PENDING — not yet run.** Before any CMA
   deployment, the recipe must be run locally with `COWORK_ROUTINE=1`,
   `GH_REPO=<throwaway/test repo>`, and a window covering a seeded reviewable
   finding, confirming it reaches `gh issue create` and returns a real
   `https://github.com/.../issues/<n>` URL. This decouples "does the
   filing/guard logic work?" from "does the cloud CMA deploy work?" — neither
   question has been answered yet for this migration.
2. **Cloud-graded-run URL: PENDING — CMA not yet deployed.** After the local
   smoke passes, the CMA deployment and a graded `define_outcome` verification
   run (live-API run, recipe-only filing, no secret echo, report present) must
   produce a real filed issue, evidenced by `gh issue list --repo <target>
   --label pr-review-audit --search 'unaddressed review findings'`. A
   `[DRY RUN] Would file …` log line never qualifies as this artifact.

## Remaining Operator Steps

In order, none of which have happened yet:

1. Run the local filing smoke test (`COWORK_ROUTINE=1`, `GH_REPO=<throwaway/test
   repo>`) against a seeded reviewable finding to get a real filed-issue URL.
2. Deploy the CMA (agent, environment, vault, deployment) per this descriptor,
   reusing the `sentry-issue-triage` pilot's shape via `/build-agent`.
3. Verify a live cloud run files a real GitHub issue (the graded verification
   session), and record both artifact URLs above.
4. Only then remove the `pr-review-audit` entry from both
   `config/reflections.yaml` and `~/Desktop/Valor/reflections.yaml` — the
   actual ordered cutover. Until step 4, the local reflection keeps running,
   still dry-run, as the sole thing exercising this audit.

## See Also

- [`docs/features/cowork-tasks.md`](../features/cowork-tasks.md) — the reusable pattern, the Candidate Re-Triage table, and the Redis-bypass shim generalization this migration required
- [`docs/infra/cowork-sentry-triage.md`](cowork-sentry-triage.md) — the pilot this migration reuses (LIVE, for comparison)
- [`docs/features/reflections.md`](../features/reflections.md) — the local reflection scheduler `pr-review-audit` still runs under today
- `reflections/audits/pr_review_audit.py` — the guarded callable and CLI recipe entrypoint
- `tests/unit/test_pr_review_audit.py` — unit coverage for the four cloud guards
