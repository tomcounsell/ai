---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2068
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-23T07:31:52Z
---

# Migrate remaining cloud-API-audit reflections to Claude Cowork (follows #2067)

## Problem

#2067 (PR #2209) migrated `sentry-issue-triage` off the local reflection scheduler and onto a
cloud scheduled agent, and shipped a reusable pattern (`docs/features/cowork-tasks.md`), a
routine-spec descriptor (`docs/infra/cowork-sentry-triage.md`), and a global `cowork` skill —
all explicitly marked **PROVISIONAL**, with a cleanup trigger that fires "if #2068 (the first
real second migration) stalls or the pattern proves wrong on first reuse." This issue is that
first reuse.

The issue's premise was that a batch of clean "read a cloud API → file a GitHub issue → zero
local state" reflections remains, ready for template-fill migration. **Plan-time recon
(see the issue's Recon Summary) shows that premise is materially optimistic.** None of the
remaining candidates is as clean as sentry:

- Three of the six "strong" candidates (`tech-debt-scan`, `task-backlog-check`,
  `principal-staleness`) **file no GitHub issue at all** — they return read-only findings, so
  the pilot's entire "filed issue = notification" seam does not apply to them.
- `tech-debt-scan` is a **local filesystem `grep`** over each project's working directory, not a
  cloud-API audit.
- `principal-staleness` reads `config/PRINCIPAL.md` **`st_mtime`**, which in a fresh clone equals
  checkout time — the >90-day check never fires.
- The only remaining issue-**filers** (`skills-audit`, `pr-review-audit`, `session-intelligence`)
  each carry a **local-Redis** entanglement (streak state, run watermark, or the local
  `AgentSession`/`BridgeEvent` records that are their *inputs*).

So the honest scope is not "migrate six clean candidates." It is: (1) capture the corrected
re-triage as durable knowledge so future audits are classified correctly, and (2) prove the
pattern generalizes by migrating the **single genuinely-viable next candidate end-to-end** —
`pr-review-audit` — resolving its one local dependency. Everything else gets an explicit,
recorded disposition (defer / stays-local / needs-recipe-first).

**Current behavior:** the candidate reflections run as local `execution_type: function` entries in
`config/reflections.yaml`, gated (for the filers) to the single `project_key: valor` machine,
consuming local worker budget.

**Desired outcome:** `pr-review-audit` runs as a scheduled cloud agent (CMA, per the pilot's real
substrate) on the same cadence, files the same issues, and is cleanly cut over (removed from both
`reflections.yaml` copies, no parallel run). The pattern docs are updated with the corrected
triage so the boundary "which reflections can never migrate, and why the clean ones are rare" is
captured. Remaining candidates carry recorded dispositions.

## Freshness Check

**Baseline commit:** `3c0fc7ee1` (HEAD at plan time).
**Issue filed at:** 2026-07-13T10:09:45Z. **Blocker #2067 closed:** 2026-07-23 (PR #2209 merged).
**Disposition: Major drift on the issue's premise — proceeding on a revised, narrower premise** (recorded in the issue's Recon Summary and Problem above).

**File:line references re-verified against `main`:**
- Pilot artifacts present: `docs/features/cowork-tasks.md`, `docs/infra/cowork-sentry-triage.md`,
  `.claude/skills-global/cowork/SKILL.md`, `.claude/skill-context/cowork.md`.
- Reference guard `reflections/sentry_triage.py` — `COWORK_ROUTINE=1` → `proj_wd = str(PROJECT_ROOT)`
  present and load-bearing.
- `load_local_projects()` (`reflections/utilities.py:83-116`) returns `[]` in a fresh clone (vault
  `projects.json` absent + per-project `working_directory` absent on disk) — confirmed.
- Candidate callables all still registered in `config/reflections.yaml` at the cited paths.
- Read-only (non-filing) confirmed for `tech_debt_scan.py`, `task_backlog_check.py`,
  `principal_staleness.py` (no `gh issue create` / `_file_*_issue`).
- `principal_staleness.py:38` uses `principal_path.stat().st_mtime` — clone-mtime hazard confirmed.
- The load-bearing `pr_review_audit.py` citations re-verified against current `main`: `dry_run = True`
  hardcode at `:160`; `PRReviewAudit.last_successful_run()` at `:167`; `is_audited(comment_key)` at `:288`
  (unconditional read); `mark_audited(...)` at `:295`, gated by `if not dry_run:` at `:294` (so flipping
  guard 2 newly exposes it — exactly as the Data Flow states). No material drift; the three-touchpoint
  bypass and filing-enablement guards target the correct lines.

**Cited sibling issues/PRs re-checked:**
- #2067 — CLOSED/COMPLETED (PR #2209 merged 2026-07-23). Its infra descriptor records the real
  substrate was a **Claude Managed Agent (CMA)** deployed via the Anthropic API, not the human-gated
  claude.ai `/schedule` Routines surface (which was unavailable). This materially changes the
  deployment step for #2068: it is **agent-executable**, not `[EXTERNAL]`.

**Commits on main since the issue was filed touching candidate files:** two docs-site commits
(`cf2d190d3`, `dfb781ca5`) — irrelevant to the reflection callables.

**Active overlapping plans in `docs/plans/`:** none touching the reflections-audit area.

## Prior Art

- **#2067 / PR #2209** — the pilot this plan reuses. Its plan doc is at
  `docs/plans/completed/cowork-sentry-triage-pilot.md`; its per-candidate migration checklist,
  `COWORK_ROUTINE` guard, ordered-cutover gate, and CMA substrate are the template.
- **`/build-agent`** (`.claude/skills-global/build-agent/`) — the CMA primitives (agent, environment,
  vault, deployment) the pilot actually used; reused here for the `pr-review-audit` deployment.
- **`/sentry` skill** — the model for "a committed on-demand recipe the routine prompt delegates to."
  `pr-review-audit` has no such recipe yet; building one is a prerequisite (see Technical Approach).
- No closed issues/PRs attempt a second reflection→cloud migration — this is the first.

## Research

No external WebSearch performed: the authoritative Routines/CMA references and the concrete
substrate decision are already captured in the landed pilot artifacts
(`docs/features/cowork-tasks.md`, `docs/infra/cowork-sentry-triage.md`,
`.claude/skills-global/cowork/SKILL.md`). This migration is internal — it reuses those, and the
Anthropic-API CMA path is documented in the pilot's infra descriptor. Proceeding on codebase
context.

## Candidate Re-Triage (durable output of this plan)

The corrected per-candidate verdict, to be committed into the pattern docs so future audits are
classified correctly:

| Candidate | Callable | Verdict | Reason |
|-----------|----------|---------|--------|
| `pr-review-audit` | `reflections.auditing.run_pr_review_audit` | **MIGRATE (this plan), contingent on enabling filing** | Genuinely GitHub-API-driven and *capable* of filing, but currently a **no-op**: `dry_run` is hardcoded `True` (`reflections/audits/pr_review_audit.py:160`), so it files nothing today. Migration value depends on a deliberate first-fire decision to enable filing (Guard 2) plus bypassing **three** local-Redis touchpoints — watermark, `is_audited`, `mark_audited` (Guard 3). If filing is not to be enabled, migrating a no-op has no value → Open Question 1 Option C. |
| `skills-audit` | `reflections.auditing.run_skills_audit` | **DEFER** | Files issues, but streak/dedup state lives in local Redis; needs a state shim before it can run clean in the cloud. Candidate for a follow-up after `pr-review-audit` proves the shim pattern. |
| `session-intelligence` | `reflections.session_intelligence.run` | **STAYS-LOCAL (inputs)** | Its inputs are local Redis `AgentSession`/`BridgeEvent` + on-disk session logs; the cloud half has nothing to read without an export pipeline. Out of scope. |
| `docs-auditor` | `reflections.docs_auditor.run_docs_auditor` | **STAYS-DISABLED** | Currently disabled for noise; heavy local deps (Redis rotation/locks, Anthropic, vault, `valor-telegram`, git push). Re-enable/migrate is its own issue. |
| `tech-debt-scan` | `reflections.maintenance.run_legacy_code_scan` | **STAYS-LOCAL (no seam)** | Local filesystem grep, files no issue — no cloud-API audit and no notification seam. |
| `task-backlog-check` | `reflections.task_management.run_task_management` | **STAYS-LOCAL (no seam)** | GitHub-API read-only, files no issue — no notification seam. |
| `principal-staleness` | `reflections.task_management.run_principal_staleness` | **STAYS-LOCAL (mtime)** | `st_mtime`-based, breaks in a clone, files no issue. |
| `hooks-audit` | `reflections.auditing.run_hooks_audit` | **STAYS-LOCAL (log)** | Reads local `logs/hooks.log`; only the settings.json half is portable. |
| `merged-branch-cleanup` / `do-docs-branch-sweeper` / `stale-branch-cleanup` | (branch hygiene) | **STAYS-LOCAL (branches)** | Delete local `session/*`/worktree branches tied to this machine's checkout. |

## Data Flow

**Today (`pr-review-audit` local reflection):**
1. Reflection scheduler fires `pr-review-audit` daily on the `project_key: valor` machine.
2. `run_pr_review_audit` calls `load_local_projects()`, then for each project reads merged PRs via
   the GitHub API (`gh pr list`, `gh api .../comments|reviews`).
3. Touches the local-Redis `models.reflections.PRReviewAudit` model at **three** points: the run
   watermark `last_successful_run()` (`:167`), the per-finding dedup read `is_audited()` (`:288`), and the
   dedup write `mark_audited()` (`:294`).
4. Flags unaddressed review findings — but **files nothing**: `dry_run` is hardcoded `True` (`:160`), so it
   only logs `[DRY RUN] Would file issue …`. The filing branch (`gh issue create`) is dead code today.

**Target (cloud CMA):**
1. Anthropic cloud cron fires daily, independent of the local worker.
2. CMA clones the ai repo into a fresh sandbox; GitHub + `SENTRY`-style tokens injected via the
   vault as egress-scoped env credentials.
3. Prompt delegates to the committed on-demand recipe (built in this plan) → `run_pr_review_audit`
   under `COWORK_ROUTINE=1`, which flips `dry_run` to `False` so filing is actually enabled (Guard 2).
4. **Redis is unavailable** → env-gated cloud mode bypasses all three `PRReviewAudit` touchpoints
   (watermark, `is_audited`, `mark_audited`) and audits a fixed lookback window
   (`PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS`); `gh` title-search dedup (belt-and-suspenders, as in sentry) is
   the sole cross-run dedup and prevents duplicate filings across daily runs.
5. Per-project `proj_wd` resolves to `PROJECT_ROOT` via the same `COWORK_ROUTINE` guard as sentry;
   `GH_REPO` selects the target repo. Filed issue = notification.

## Architectural Impact

- **New dependency:** one additional cloud CMA deployment + its vault/env; no new Python dependency.
- **Interface changes:** three env-gated guards inside `run_pr_review_audit` — (a) the sentry-style
  `proj_wd` → `PROJECT_ROOT` guard, (b) a `COWORK_ROUTINE`-gated flip of the hardcoded `dry_run` so the
  cloud routine actually files, (c) a `COWORK_ROUTINE`-gated bypass of **all three** `PRReviewAudit` Redis
  touchpoints (watermark + `is_audited` + `mark_audited`) with a fixed-window + `gh`-dedup fallback. All
  inert when the env var is unset, so the local reflection path is behavior-identical.
- **Coupling:** decreases for the migrated candidate (drops `project_key: valor` gating + local
  worker budget). All Redis coupling is bypassed in cloud, retained locally.
- **Data ownership:** cadence authority for `pr-review-audit` moves to the CMA deployment; a committed
  routine-spec descriptor (`docs/infra/cowork-pr-review-audit.md`) is the versioned record.
- **Reversibility:** high — re-adding the reflection entry restores the local path; the callable is
  otherwise unchanged.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer.

**Interactions:**
- PM check-ins: 2-3. The headline scope decision (below) — which now includes an explicit **enable-filing**
  approval, since the candidate is a no-op today (critique C2) — the Redis-bypass resolution, and the
  substrate confirmation (reuse CMA) each warrant sign-off; this plan proceeds on a revised premise.
- Review rounds: 1-2 (guard correctness + cutover ordering + docs quality).

The coding surface is moderate (three env-gated guards — `proj_wd`, `dry_run` filing-enablement, and the
three-touchpoint Redis bypass — + a thin on-demand recipe + a routine-spec doc + ordered cutover). The
appetite is dominated by getting the re-triage right, the deliberate first-fire filing decision, resolving
the Redis touchpoints cleanly, and the agent-executable CMA deployment + verification. **ROI caveat
(critique C2):** because the audit files nothing today, a Large appetite is only justified if the
enable-filing decision is affirmatively taken (Open Question 1); if not, prefer Option C (docs-only).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | The audit's read + filing + dedup path. |
| Anthropic API key present (for CMA create/deploy) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Required to create the CMA agent/env/deployment via API. |
| Pilot artifacts present | `test -f docs/infra/cowork-sentry-triage.md && test -f .claude/skills-global/cowork/SKILL.md` | The template this plan reuses. |

## Solution

### Key Elements

- **Corrected re-triage as durable docs.** The Candidate Re-Triage table above is folded into
  `docs/features/cowork-tasks.md` (and pointed to from `reflections.md`) so the boundary — and the
  finding that clean cloud candidates are *rare*, not plentiful — is captured. This directly satisfies
  the issue AC "the must-NOT-migrate boundary is captured in the Cowork pattern docs."
- **One real migration: `pr-review-audit`.** Build a committed on-demand recipe, flip the hardcoded
  `dry_run` in cloud mode so filing is actually enabled (Guard 2), bypass all three Redis touchpoints via an
  env-gated cloud mode (Guard 3), add the sentry-style `proj_wd` guard (Guard 1), write the routine-spec
  descriptor, deploy the CMA (agent-executable), verify a real filed issue, and cut over.
- **Recipe = delegation, not re-implementation.** The CMA prompt invokes the committed recipe by name;
  no audit logic is re-encoded in cloud config (the pilot's hard rule).
- **Substrate = CMA, reusing the pilot.** The deployment uses the Anthropic-API CMA path the pilot
  actually landed on (`/build-agent` primitives), so the deploy + graded verification run are
  **agent-executable**, not operator-gated.
- **Ordered clean cutover.** Remove `pr-review-audit` from **both** `config/reflections.yaml` and the
  runtime vault `~/Desktop/Valor/reflections.yaml`; the removal is gated on a verified successful CMA
  run so there is neither a parallel run nor a coverage gap.

### Flow

Local `pr-review-audit` (daily) → **[build recipe + guards]** → CMA deployment (daily) → clones ai repo
→ runs recipe → GitHub API read + `gh issue create` (fixed-window, `gh`-dedup) → filed issue =
notification → (reflection entry removed from both copies once CMA verified live).

### Technical Approach

- **Build the on-demand recipe first.** `pr-review-audit` has no committed recipe to delegate to.
  Add a thin entry point (preferred: a `python -m reflections.audits.pr_review_audit --apply` CLI hook,
  or a `/pr-review-audit` skill) that runs `run_pr_review_audit` with the cloud env flags. Confirm the
  exact form at build time; the recipe is the single source of truth the CMA prompt names.
- **Three env-gated guards in `run_pr_review_audit` (`reflections/audits/pr_review_audit.py`):**
  1. **`proj_wd` guard**, byte-identical to sentry: when `load_local_projects()` yields no match and
     `COWORK_ROUTINE == "1"`, default `proj_wd = str(PROJECT_ROOT)`.
  2. **Filing-enablement guard (addresses critique B1).** `dry_run` is **hardcoded `True`** today
     (`pr_review_audit.py:160`) — the audit files nothing in any mode, so migrating it as-is ships a
     permanent no-op. Replace the constant with `dry_run = os.getenv("COWORK_ROUTINE") != "1"` so the
     cloud routine (and only the cloud routine) actually files. This is a deliberate **first-fire
     enablement** decision, not a mechanical guard — see the issue-storm mitigation below and Open
     Question 1. The local reflection stays dry-run (files nothing) unless separately enabled, so no
     local behavior changes.
  3. **Redis-touchpoint bypass (addresses critique B2 — all three touchpoints, not just the watermark).**
     `PRReviewAudit` is touched at **three** unconditional points that each crash a Redis-less cloud run:
     (a) `last_successful_run()` — the run watermark (`pr_review_audit.py:167`);
     (b) `is_audited(comment_key)` — the per-finding dedup **read** (`:288`, currently unconditional even
         in dry-run);
     (c) `mark_audited(...)` — the per-finding dedup **write** (`:294`, today gated only by `dry_run`, so
         flipping guard 2 would newly expose it).
     When `COWORK_ROUTINE == "1"`, bypass **all three**: use a fixed lookback window instead of the
     watermark (env-tunable, default provisional — a named `PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS` constant
     with a grain-of-salt comment), and skip both `is_audited`/`mark_audited` so no `PRReviewAudit` Redis
     call is reached. Cross-run dedup is delegated entirely to `gh` title-search (belt-and-suspenders, as
     in sentry), which makes re-audit of the same window idempotent without any local state.
  4. **Dedup-key-in-title requirement (the load-bearing precondition of guard 3).** Dropping
     `is_audited`/`mark_audited` moves ALL cross-run dedup onto `gh` title-search, which is only reliable
     if the filed issue **title embeds the same stable per-finding identifier** the local path keys on —
     `comment_key` (PR number + review-comment id), the exact value passed to `is_audited(comment_key)` at
     `:288`. Sentry's migration was safe here because its title carries the stable Sentry issue id; the
     current `pr_review_audit.py` filing title is **not yet verified** to encode `comment_key`
     deterministically. **Build-time gate:** confirm (and, if absent, make) the `gh issue create` title a
     pure function of `comment_key` (e.g. a `[pr-review-audit] PR #<n> comment <id>: …` prefix) so the
     `gh --search` used for dedup matches a prior filing exactly. Without this, cloud mode either re-files
     the same finding every run (title not reproducible) or suppresses distinct findings that share a
     generic title (title not unique) — the two failure directions guard 3 must foreclose. A unit test
     asserts two runs over the same finding produce one issue via the title-search branch.
  All three guards inert when `COWORK_ROUTINE` is unset — the local reflection path (dry-run, watermark,
  `is_audited`/`mark_audited`) is preserved exactly.
- **Issue-storm calibration for the first cloud fire (critique B1).** Because the audit has filed nothing
  to date, the first `dry_run=False` run over a fixed lookback window can file a burst of issues for every
  historically-unaddressed finding at once. Mitigations, resolved at build time: (a) the graded
  verification run (below) is the **first** real fire and inspects filing volume before the schedule goes
  live; (b) start `PR_REVIEW_AUDIT_CLOUD_WINDOW_DAYS` deliberately small (match, don't exceed, the daily
  cadence) so steady-state each run sees ~one day of merges; (c) `gh` title-search dedup prevents the same
  finding from re-filing on subsequent runs. If the first-fire volume is unacceptable, fall back to Open
  Question 1 Option C (docs-only) rather than shipping an issue storm.
- **Routine-spec descriptor** `docs/infra/cowork-pr-review-audit.md`: prompt, cadence (match the
  reflection's `every:`), CMA primitive IDs (agent/env/vault/deployment), egress scope, injected
  tokens, `COWORK_ROUTINE=1` + `GH_REPO` env, notification seam, and the watermark-bypass note.
- **CMA deployment (agent-executable).** Reuse the pilot's env shape (limited networking to GitHub hosts
  + package managers), vault-injected `GH_TOKEN`, cron matching the reflection cadence, and a graded
  `define_outcome` verification session (live-API run, recipe-only filing, no secret echo, report present).
- **Ordered cutover, both copies.** The scheduler resolves `REFLECTIONS_YAML` env →
  `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml`, so the **vault copy is what fires** on
  the owning machine. Remove the `pr-review-audit` entry from both; the gate is **three conjuncts**:
  (1) `grep -cE '^\s*-\s*name:\s*pr-review-audit' ~/Desktop/Valor/reflections.yaml` == 0;
  (2) green pytest; **and (3) a positive filed-issue artifact from the CMA run (critique C1)** — the graded
  verification run must have produced a real GitHub issue authored by the recipe, evidenced by
  `gh issue list --repo <target> --label pr-review-audit --search 'unaddressed review findings'` returning
  the CMA-filed issue (URL captured in the routine-spec descriptor). Green pytest alone is **not**
  sufficient — it proves the guards, not that the cloud agent actually files. If the verification window
  genuinely holds no unaddressed findings (nothing to file), the gate is met only by a recipe run that
  reaches the `gh issue create` branch and reports the empty result **plus** a seeded-finding dry-run
  proving the filing wiring works end-to-end — never by pytest alone. Leave a one-line pointer comment
  (deliberate scoped exception, as the pilot did).
- **Keep the callable for local use? No local on-demand path exists today** (unlike sentry's `/sentry`).
  Decide at a PM check-in whether the local reflection is fully retired (callable kept only for the cloud
  recipe) or a local on-demand skill is also added. Default: retire the schedule, keep the callable.
- **Refresh the PROVISIONAL banners.** Once this migration lands and verifies, downgrade the "PROVISIONAL
  / reviewable on first reuse" banners in `.claude/skills-global/cowork/SKILL.md`,
  `.claude/skill-context/cowork.md`, and `docs/features/cowork-tasks.md` to reflect that the pattern has
  now been exercised a second time (and record what needed adapting — the Redis-watermark shim).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The three new guards add no new `except` blocks; `run_pr_review_audit`'s existing per-project error
  isolation keeps its current coverage. State "no new exception handlers" for the guard edits.
- [ ] Docs / routine-spec deliverables contain no runtime exception handlers — "No exception handlers in scope."

### Empty/Invalid Input Handling
- [ ] Cloud mode with an empty `load_local_projects()` must route filing to `PROJECT_ROOT` (guard 1), not
  `[SKIP]` — asserted by a unit test.
- [ ] Cloud mode with no Redis available must not raise on **any** `PRReviewAudit` touchpoint —
  guard 3 bypasses all three (`last_successful_run`, `is_audited`, `mark_audited`); asserted by a unit
  test that runs the cloud branch without a Redis connection and reaches the filing path.
- [ ] Cloud mode must actually enable filing — guard 2 flips the hardcoded `dry_run`; a unit test asserts
  the `gh issue create` branch is reached (not the `[DRY RUN]` log) when `COWORK_ROUTINE=1`.
- [ ] Post-cutover: `python -m reflections --dry-run` must load cleanly with no dangling `pr-review-audit`.

### Error State Rendering
- [ ] The routine-spec descriptor must state the observability tradeoff: a silently-failing CMA files
  nothing and looks identical to a healthy quiet day; the operator audits CMA run history to catch it.

## Test Impact
- [ ] `tests/unit/` PR-review-audit tests (locate the existing suite for `run_pr_review_audit`) — UPDATE:
  add cases for (a) `COWORK_ROUTINE=1` + empty `load_local_projects()` → filing routed to `PROJECT_ROOT`;
  (b) `COWORK_ROUTINE=1` → all three Redis touchpoints (`last_successful_run`, `is_audited`,
  `mark_audited`) bypassed, fixed window used, no raise without Redis; (c) `COWORK_ROUTINE=1` → `dry_run`
  flipped so the `gh issue create` branch is reached; (d) env unset → local behavior preserved (dry-run,
  watermark read/write, `is_audited`/`mark_audited`, `[SKIP]` on no project).
- [ ] `tests/unit/test_reflections_yaml_migration.py`, `tests/unit/test_reflections_local_copy.py`,
  `tests/unit/test_ui_reflections_data.py` — VERIFY (likely UPDATE): grep each for `pr-review-audit` or a
  hardcoded reflection count; update if any assert its presence or a fixed total.
- [ ] New coverage — ADD: a test asserting `pr-review-audit` is **absent** from the loaded registry after
  cutover (guards against accidental re-add / parallel run), mirroring the pilot's absence test.
- [ ] New coverage — ADD: a title-dedup test (guard 4) — two cloud-mode runs over the same `comment_key`
  finding produce exactly one filed issue, proving the `gh` title-search branch matches a prior filing;
  and a title-uniqueness assertion that two distinct `comment_key`s do not collide on one title.
- [ ] `tests/unit/test_update_hardlinks.py` — VERIFY: only if a new skill dir is added for the recipe.

## Rabbit Holes

- **Migrating all six "strong" candidates.** Recon shows they are not clean; forcing them into the cloud
  (local grep, mtime, read-only-no-seam, Redis-state) is disproportionate. Record dispositions instead.
- **Re-implementing the audit logic in the CMA prompt.** Delegate to the committed recipe; do not re-encode.
- **Building a Redis-state export/sync for the watermark.** The env-gated fixed-window + `gh` dedup is
  sufficient; a cloud→local watermark sync is a separate project.
- **Building a bidirectional CMA↔repo descriptor sync tool.** The descriptor is a human-maintained
  versioned record, not a live sync target (pilot decision).
- **Splitting a mixed candidate into a cloud half + a local half** (e.g. session-intelligence). The
  pattern doc already forbids this; only migrate cleanly-one-or-the-other tasks.

## Risks

### Risk 1: Coverage gap or double-file during cutover
**Impact:** Removing the reflection before the CMA is verified live → gap; both running → duplicate issues.
**Mitigation:** Ordered gate — removal from both `reflections.yaml` copies is gated on a verified successful
CMA run; `gh` title-search dedup absorbs any single-day overlap.

### Risk 2: Fixed-window cloud mode re-files or misses PRs
**Impact:** Without the Redis watermark, a too-short window misses PRs merged during a CMA outage; a
too-long window re-scans and leans entirely on `gh` dedup.
**Mitigation:** `gh` title-search dedup prevents duplicate filings; the window is a named, env-tunable
constant marked provisional. Validate the chosen default against the reflection's actual cadence at build time.
**Precondition (see Technical Approach guard 4):** `gh` dedup only works if the filed-issue title embeds the
stable `comment_key` identifier the local path keys on. If the current filing title is generic, guard 3's
Redis-free dedup is unsound — the build must make the title a deterministic function of `comment_key` first.

### Risk 3: Silent CMA failure goes unnoticed
**Impact:** "Filed issue = notification" means a failed run (auth expiry, connector outage) files nothing,
indistinguishable from a healthy quiet day.
**Mitigation:** The routine-spec descriptor documents auditing CMA run history; same tradeoff the pilot
accepted. A heartbeat-digest enhancement remains deferred.

### Risk 4: `pr-review-audit` has no on-demand recipe today
**Impact:** The pilot's "delegate to a committed recipe" rule needs a recipe that doesn't exist.
**Mitigation:** Build a thin `python -m` (or skill) recipe as an explicit prerequisite step before deployment.

## Race Conditions

### Race 1: Local reflection and cloud CMA both fire during the cutover window
**Location:** `reflections.yaml` entry vs. CMA cron.
**Data prerequisite:** the GitHub open-issue list is the shared dedup state both writers read.
**State prerequisite:** at most one active daily writer after cutover.
**Mitigation:** ordered cutover gate (CMA verified → remove both reflection copies); `gh` dedup tolerates
a single-day overlap.

## No-Gos (Out of Scope)

- [SEPARATE] Migrating `skills-audit`, `session-intelligence`, or `docs-auditor` — recorded as
  DEFER/STAYS-LOCAL/STAYS-DISABLED in the re-triage table; each is its own follow-up if pursued.
- [SEPARATE] Adding a cloud→local Redis watermark sync — out of scope; the fixed-window + `gh` dedup suffices.
- [SEPARATE] Adding a Slack/email secondary notification channel — filed issue is the notification.
- [ORDERED] Removing the `pr-review-audit` reflection entry — gated on **all three** cutover conjuncts:
  a **positive filed-issue artifact** from the CMA run (`gh issue list … --label pr-review-audit`, critique
  C1) AND green pytest AND
  `grep -cE '^\s*-\s*name:\s*pr-review-audit' ~/Desktop/Valor/reflections.yaml` == 0 on the owning machine.
  Green pytest alone is never sufficient.

## Update System

- **`/update` skill:** if a new recipe skill dir is added under `.claude/skills-global/`,
  `sync_claude_dirs()` in `scripts/update/hardlinks.py` hardlinks it automatically — confirm it contains a
  `SKILL.md`. If the recipe is a `python -m` entry instead, no skill-sync change is needed.
- **`RENAMED_REMOVALS`:** not needed unless a skill is moved/renamed. State explicitly in the build.
- **Vault registry (part of the ORDERED cutover gate):** the runtime `~/Desktop/Valor/reflections.yaml` is
  the firing path; its `pr-review-audit` entry MUST be removed before merge (`grep -c … == 0`). The tracked
  `config/reflections.yaml` edit is only the versioned record. Build prints the config path
  `python -m reflections --dry-run` resolves against.

## Agent Integration

- **No new bridge/agent MCP surface.** `run_pr_review_audit` stays reachable via its callable; the new
  on-demand recipe (a `python -m` entry or a `/pr-review-audit` skill) is the only new agent-invocable
  surface, and it wraps the existing callable — no `bridge/telegram_bridge.py` import change.
- The CMA deployment is an external Anthropic object, not a bridge surface.
- **Integration test:** `python -m reflections --dry-run` runs clean post-cutover with `pr-review-audit`
  absent; if a new skill is added, it passes `do-skills-audit`'s coupling-probe gate.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/cowork-tasks.md`: fold in the Candidate Re-Triage table and the finding that
  clean cloud candidates are rare (each remaining filer has a local-state entanglement); downgrade the
  PROVISIONAL banner to "exercised twice" and record the Redis-watermark shim as the adaptation.
- [ ] Create `docs/infra/cowork-pr-review-audit.md` — the routine-spec descriptor (prompt, cadence, CMA
  primitive IDs, egress scope, tokens, `COWORK_ROUTINE=1`/`GH_REPO`, watermark-bypass note, notification seam).
- [ ] Update `docs/features/reflections.md` / `docs/features/adding-reflection-tasks.md` pointer to reference
  the re-triage (which audits can migrate, and why most can't).

### Inline Documentation
- [ ] Replace the removed `config/reflections.yaml` entry with a one-line pointer comment (navigational aid,
  deliberate scoped exception — as the pilot did for sentry).

### Skill Documentation
- [ ] Downgrade the PROVISIONAL banner in `.claude/skills-global/cowork/SKILL.md` and
  `.claude/skill-context/cowork.md` now that the pattern has a second, verified reuse; note the
  Redis-watermark shim as the generalization the second migration required.
- [ ] If a `/pr-review-audit` recipe skill is added, create its `SKILL.md` with the canonical probe sentence.

## Success Criteria

- [ ] `docs/features/cowork-tasks.md` contains the Candidate Re-Triage table and the "clean candidates are
  rare" finding; the must-NOT-migrate boundary is captured (issue AC).
- [ ] `docs/infra/cowork-pr-review-audit.md` exists as the versioned routine-spec descriptor.
- [ ] A committed on-demand recipe for `pr-review-audit` exists and is what the CMA prompt delegates to.
- [ ] **[AGENT-EXECUTABLE runtime signal]** Unit tests drive `run_pr_review_audit` with `COWORK_ROUTINE=1`
  and assert (a) filing routes to `PROJECT_ROOT` on empty `load_local_projects()`, (b) all three Redis
  touchpoints (`last_successful_run`, `is_audited`, `mark_audited`) are bypassed with the fixed window and
  no raise without Redis, and (c) `dry_run` is flipped so the `gh issue create` branch is reached — standing
  in for the live CMA run.
- [ ] The filed-issue title is a deterministic function of `comment_key` (guard 4), verified by a
  title-dedup test: two cloud-mode runs over the same finding yield one issue via `gh` title-search, and
  two distinct findings never share a title. This is the precondition that makes guard 3's Redis-free dedup
  sound.
- [ ] `pr-review-audit` is removed from `config/reflections.yaml` AND `~/Desktop/Valor/reflections.yaml`
  and no longer appears in `python -m reflections --dry-run` (clean cutover, no parallel run).
- [ ] Guards are inert when `COWORK_ROUTINE` is unset — the local reflection path is behavior-identical
  (still dry-run, existing PR-review-audit tests still pass).
- [ ] CMA deployment created and a graded verification run **filed a real issue via the recipe**, evidenced
  by a positive `gh issue list --repo <target> --label pr-review-audit` artifact (critique C1 — green
  pytest alone does not satisfy this). [AGENT-EXECUTABLE via Anthropic API — the pilot proved this is not
  `[EXTERNAL]`]; the ORDERED cutover is gated on this filed-issue artifact.
- [ ] Each non-migrated candidate has its disposition recorded (the re-triage table) with a reason (issue AC).
- [ ] Tests pass (`/do-test`). Docs updated (`/do-docs`).

## Open Questions

1. **Scope call (headline) — Option A is contingent on the filing-enablement decision (critique C2).**
   Recon shows no remaining candidate is as clean as sentry, and the chosen candidate `pr-review-audit`
   **files nothing today** (`dry_run` hardcoded `True`). So Option A is not "migrate a working audit" — it
   is "enable filing for the first time *and* migrate it." For a Large appetite that ROI only holds if we
   actually want this audit filing issues. Three options:
   (A) **[recommended, but gated on approving first-fire filing]** capture the corrected re-triage in the
   pattern docs + flip `dry_run` in cloud mode (Guard 2) + migrate `pr-review-audit` end-to-end as the
   pattern's verified first reuse, deferring the rest with recorded dispositions. **Only pursue A if the
   first-fire filing (issue-storm-mitigated) is desired** — otherwise A ships a no-op or an unwanted issue
   burst;
   (B) attempt a larger batch (skills-audit + pr-review-audit), accepting the Redis-state shims for both;
   (C) close #2068 as "no clean candidates remain / filing not wanted," folding only the re-triage into the
   pattern docs — the correct choice if we do **not** want `pr-review-audit` filing issues, since migrating a
   permanent no-op is negative ROI.
   Which scope? And if A: confirm we want `pr-review-audit` to start filing issues.
2. **Redis-watermark resolution for `pr-review-audit`.** Confirm the env-gated fixed-window + `gh` dedup
   approach (vs. a cloud-persisted watermark on a `claude/` branch, which the pilot's rabbit-holes warn against).
   And what default lookback window matches the reflection's real cadence?
3. **Substrate.** Confirm reusing the pilot's CMA path (agent-creatable via Anthropic API) rather than the
   human-gated `/schedule` surface — i.e. that the deployment step is treated as agent-executable.
4. **Recipe form.** A `python -m reflections.audits.pr_review_audit --apply` CLI entry, or a new
   `/pr-review-audit` skill? (Affects Update System / skill-sync.)
5. **Retire vs. keep local on-demand.** After cutover, fully retire the local `pr-review-audit` schedule
   (keep the callable only for the cloud recipe), or also add a local on-demand skill? Default: retire the schedule.
