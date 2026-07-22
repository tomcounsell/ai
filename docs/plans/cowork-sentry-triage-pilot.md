---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2067
last_comment_id:
---

# Pilot: migrate sentry-issue-triage to Claude Cowork + reusable Cowork pattern & repo skill

## Problem

Several audit reflections are pure LLM-judgment tasks whose entire job is "read a cloud API,
decide what's actionable, file a GitHub issue." Encoding that as a local Python `function`
callable is awkward: the judgment lives in prompt-shaped code, it burns the local worker's
budget, and it can only run on the single machine that owns `project_key: valor`. Claude Code
**Routines** (the cloud scheduled-agent capability the issue calls "Cowork") are built exactly
for this — but we have **no established pattern** for defining, scheduling, reviewing, or
maintaining such a task in this repo, so each migration would be a bespoke one-off.

**Current behavior:** `sentry-issue-triage` runs daily as a local reflection
(`reflections.sentry_triage.run_sentry_triage`, `config/reflections.yaml:149`, `project_key: valor`).
It pulls unresolved Sentry issues, classifies them A–E, files GitHub issues for Class C, auto-actions
A/B/E, and sends a delta-based Telegram summary only when a genuinely new actionable issue appears.
All its inputs (Sentry API, GitHub) and its output (a filed issue) are cloud-reachable — nothing about
it needs the local worker or Redis.

**Desired outcome:** `sentry-issue-triage` runs as a scheduled Claude Code Routine in the cloud on the
same daily cadence, producing the same triage-and-file behavior — and we walk away with a documented,
reusable pattern plus a repo skill so migrating the next candidate (#2068) is a template-fill, not a
research project.

## Freshness Check

**Baseline commit:** c366bdb84
**Issue filed at:** 2026-07-13T10:09:08Z
**Disposition:** Unchanged (with a load-bearing terminology revision recorded in the issue's Recon Summary)

**File:line references re-verified:**
- `reflections/sentry_triage.py::run_sentry_triage` — still present and behaves as the issue describes (fetch → classify A–E → file Class C via `gh` → auto-action A/B/E via Sentry PUT, gated by `SENTRY_TRIAGE_APPLY`).
- `config/reflections.yaml:149` (`sentry-issue-triage`) — still registered, `enabled: true`, `every: 86400s`, `project_key: valor`. Also present at `~/Desktop/Valor/reflections.yaml:149` (runtime vault registry).
- `.claude/skills/sentry/SKILL.md` — the on-demand `/sentry` skill already wraps `run_sentry_triage`.
- Local-only notification path `_send_telegram_notification` (`valor-telegram send`, `pyproject.toml:78`) confirmed; degrades gracefully (swallows `FileNotFoundError`) when `valor-telegram` is off PATH.

**Cited sibling issues/PRs re-checked:**
- #2068 (migrate remaining candidates) — OPEN; depends on this pilot's pattern.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since=2026-07-13 -- reflections/sentry_triage.py config/reflections.yaml .claude/skills/sentry/` is empty).

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** The only correction is terminology — the concrete Anthropic mechanism is **Claude Code Routines**, not a distinct "Cowork" product surface. This does not change the issue's premise; it sharpens the technical approach.

## Prior Art

- **Issue #2068**: "Migrate remaining cloud-API-audit reflections to Claude Cowork" — the explicit downstream consumer of this pilot's pattern. Blocked until this ships.
- **`/sentry` skill** (`.claude/skills/sentry/SKILL.md`): existing on-demand wrapper around `run_sentry_triage`. Its "how to run" recipe is directly reusable as the routine's behavior. No prior *migration* attempts exist — this is the first reflection→cloud cutover.
- No closed issues/PRs found attempting a reflection→cloud migration (`gh issue list --state closed --search "cowork routine reflection"` → empty).

## Research

**Queries used:**
- "Claude Cowork scheduled cloud agent Anthropic cron"
- "Anthropic Claude Cloud Routines scheduled tasks documentation how to define connectors auth"

**Key findings:**
- **Claude Code Routines** is the concrete cloud scheduled-agent mechanism (research preview). A routine = **prompt + repo(s) + connectors + trigger** (schedule / API / GitHub), created via `/schedule` in the Claude Code CLI or at `claude.ai/code/routines`. Runs on Anthropic-managed cloud infra even when the local machine is off. Source: [code.claude.com/docs/en/routines](https://code.claude.com/docs/en/routines), [claude.com/blog/introducing-routines-in-claude-code](https://claude.com/blog/introducing-routines-in-claude-code). → Informs the whole approach: the routine's behavior can be "run a committed skill against the cloned repo."
- **Execution model:** each run is a full Claude Code session that can run shell commands and use skills committed to the cloned repo, and call cloud **connectors** (Anthropic-hosted MCP, e.g. Slack/Linear/GitHub). By default Claude may push only to `claude/`-prefixed branches. → Means `/sentry`'s existing `gh`/Python recipe can run in the cloud with minimal change; notification degrades to the filed issue.
- **Creation is human-gated:** requires a Claude.ai Pro+ account and manual creation via `/schedule` or the web console (OAuth). A headless build agent cannot create or verify a live routine autonomously. → The live-routine step is an operator action; the build produces everything needed to do it in minutes.
- **Auth:** routines have no local access and cannot read our `~/Desktop/Valor/.env`. GitHub is available as a native connector; Sentry requires either a Sentry connector or a routine-scoped secret. API-trigger calls need beta header `experimental-cc-routine-2026-04-01`; tokens are shown once. Source: [code.claude.com/docs/en/routines](https://code.claude.com/docs/en/routines). → Drives the "auth from cloud" decision and the notification-seam choice.
- **Reliability caveat:** early adopters report bundled-connector failures in unattended runs (silent failures, OAuth expiry). → Feeds the "maintaining" half of the skill (failure modes, run-audit guidance).

## Data Flow

**Today (local reflection):**
1. **Trigger:** in-process reflection scheduler (`agent/reflection_scheduler.py`) fires `sentry-issue-triage` daily on the `project_key: valor` machine.
2. **Fetch:** `run_sentry_triage` → Sentry API (`SENTRY_AUTH_TOKEN` from local env/.env).
3. **Classify:** A–E in-process.
4. **Act:** Class C → `gh issue create` (per-project `working_directory`); A/B/E → Sentry PUT (gated by `SENTRY_TRIAGE_APPLY`).
5. **Notify:** delta vs. local `data/sentry_triage_seen.json` → `valor-telegram send` (local).

**Target (cloud routine):**
1. **Trigger:** Anthropic cloud cron (daily), independent of the local worker.
2. **Session start:** routine clones the ai repo; Sentry credential supplied as a routine secret/connector; GitHub via connector.
3. **Behavior:** routine prompt invokes the committed triage recipe (`/sentry --apply` → `run_sentry_triage`).
4. **Act:** Class C → `gh issue create` (native connector); A/B/E → Sentry PUT. Same rubric, unchanged Python.
5. **Notify:** the **filed GitHub issue is the notification** (we already receive GitHub notifications). `valor-telegram` is absent in the cloud and degrades silently. GitHub-issue dedup (`gh issue list --search`) makes the local `sentry_triage_seen.json` delta-state unnecessary — no new issue means no notification, which reproduces the delta behavior automatically.

## Architectural Impact

- **New dependency:** an external Anthropic Claude Code Routine (cloud object) + one credential provisioning path (Sentry secret/connector). No new Python dependency.
- **Interface changes:** none to `run_sentry_triage` — it stays as the shared callable used by both the `/sentry` on-demand skill and the routine. Only the scheduled *reflection entry* is removed.
- **Coupling:** **decreases** — removes `project_key: valor` single-machine gating and the local-worker budget cost for this audit.
- **Data ownership:** cadence/scheduling authority moves from `reflections.yaml` (local) to the cloud routine object. A committed **routine-spec descriptor** (`docs/infra/cowork-sentry-triage.md`) becomes the versioned record so the cloud object is reconstructable.
- **Reversibility:** high — re-enabling the reflection entry restores the local path; the Python callable is untouched.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (this is a pattern-setter; the notification-seam and auth decisions and skill placement warrant sign-off)
- Review rounds: 1-2 (docs + skill quality; the coupling probe / skills-audit gate)

Coding time is small (remove a reflection entry, write a doc + a skill). The appetite is dominated by getting the *pattern* right, the human-gated routine creation, and clean cutover sequencing.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Sentry token present locally (for the operator's verification run) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('SENTRY_AUTH_TOKEN')"` | Confirms the credential exists to hand to the routine |
| `gh` authenticated | `gh auth status` | Filing/dedup path used by both local and cloud runs |
| Claude.ai Pro+ account with Routines access | `echo 'MANUAL: operator confirms Routines access at claude.ai/code/routines'` | Required to create the live routine (human-gated) |

## Solution

### Key Elements

- **Routine behavior = committed recipe.** The routine's prompt stays minimal and delegates to the existing `/sentry --apply` triage recipe committed in the repo. No triage logic is re-implemented in cloud config; `run_sentry_triage` remains the single source of truth for the rubric.
- **Notification seam = the filed GitHub issue.** Class-C filing is the notification (GitHub already notifies us). This eliminates the need to reach the local Telegram relay and retires the local delta-state file for the cloud path. (Slack connector as an optional push channel is explicitly deferred — see No-Gos.)
- **Committed routine-spec descriptor** (`docs/infra/cowork-sentry-triage.md`): the versioned record of the cloud routine — its prompt, cadence, trigger, connectors, and required secrets — so the cloud object is auditable and reconstructable from the repo.
- **Reusable pattern doc** (`docs/features/cowork-tasks.md`): the general shape for "reflection-style audit as a Cowork task," including the **local-reflection-vs-Cowork decision rule**.
- **New global skill** (`skills-global/`): create / review / maintain best practices for Cowork tasks, referencing the authoritative Routines docs, with the ai-repo-specific decision rule layered in via `.claude/skill-context/`.
- **Clean cutover:** remove the `sentry-issue-triage` reflection entry (registry), keeping `reflections/sentry_triage.py` for the `/sentry` on-demand skill. No parallel run.

### Flow

Reflection scheduler (local, daily) → **[migration]** → Cloud routine (daily) → clones ai repo → runs `/sentry --apply` → Sentry API + `gh issue create` → filed Class-C issue = notification → (reflection entry removed once routine verified live)

### Technical Approach

- **Do not re-implement the rubric.** The routine invokes the committed triage recipe; `run_sentry_triage` and its A–E logic are unchanged. Corrected reference from Freshness Check: the recipe lives in `.claude/skills/sentry/SKILL.md`.
- **Auth:** Sentry via a routine-scoped secret (or Sentry connector if available in the current catalog — operator picks at creation). GitHub via the native connector / cloned-repo `gh`.
- **Cutover sequencing (ORDERED):** the reflection entry is removed in the PR, but that removal must not land until the routine is verified live, so there is neither a parallel run nor a coverage gap. The PR ships docs + skill + routine-spec + the cutover edit together; **merge is gated on the operator confirming a successful routine test run** (Class-C filed).
- **Keep the callable.** `reflections/sentry_triage.py` stays (used by `/sentry`). Only the schedule entry moves to the cloud.
- **Registry edit touches both copies:** `config/reflections.yaml` (tracked) and the runtime `~/Desktop/Valor/reflections.yaml` (vault). The build edits the tracked file and the docs instruct the operator to sync/verify the vault copy; confirm absence via `python -m reflections --dry-run`.
- **Skill placement:** global (`.claude/skills-global/`) — routine authoring is a general capability. The local-vs-cloud decision rule and any ai-repo executable references go in `.claude/skill-context/{skill}.md` behind the canonical probe sentence, satisfying the `rule_13_coupling_signals` guard in `do-skills-audit`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/sentry_triage.py` is unchanged; its existing `except`/best-effort blocks (`_send_telegram_notification` swallowing `FileNotFoundError`, `_update_sentry_issue` isolating per-issue failures) keep their current coverage in `tests/unit/test_sentry_triage_apply.py`. No new exception handlers are introduced by this work.
- [ ] Docs/skill deliverables contain no runtime exception handlers — state "No exception handlers in scope" for those files.

### Empty/Invalid Input Handling
- [ ] Registry-load path: after removing the entry, `python -m reflections --dry-run` must still load cleanly (no dangling reference). Add a check that the scheduler enumerates its remaining entries without error.
- [ ] N/A for the docs/skill deliverables (no functions receiving runtime input).

### Error State Rendering
- [ ] The notification seam's failure mode is "no GitHub issue filed" — the pattern doc must state that a routine run that files nothing is indistinguishable from a healthy run, and how the operator audits routine runs (cloud run history) to catch a silently-failing routine. This is the observability gap that replaces the old Telegram exception-delivery.

## Test Impact

- [ ] `tests/unit/test_sentry_triage_apply.py` — UNCHANGED: exercises `run_sentry_triage` internals which are not modified; verify it still passes after the registry edit.
- [ ] `tests/unit/test_reflections_yaml_migration.py`, `tests/unit/test_reflections_local_copy.py`, `tests/unit/test_ui_reflections_data.py` — VERIFY (likely UPDATE): grep each for `sentry-issue-triage` / a hardcoded reflection count. If any assert the entry's presence or a fixed number of reflections, UPDATE to reflect its removal.
- [ ] `tests/unit/test_update_hardlinks.py` — VERIFY: if a new global skill dir is added, confirm the hardlink-sync test still passes (and add a case if the suite enumerates synced skills).
- [ ] New coverage: add a test asserting `sentry-issue-triage` is **absent** from the loaded registry after cutover (guards against accidental re-add / parallel run).

## Rabbit Holes

- **Re-implementing the A–E rubric in cloud config.** Don't. The routine delegates to the committed `/sentry` recipe. Re-encoding the classification as a routine prompt duplicates logic and drifts.
- **Porting the local delta-state (`sentry_triage_seen.json`) to the cloud.** Don't. GitHub-issue dedup already suppresses re-filing; the filed issue is the notification. Persisting seen-state in the cloud (committing it back on a `claude/` branch) is a mess for zero benefit.
- **Building a bidirectional routine-sync tool** (repo descriptor ⇄ cloud object). Out of scope — the descriptor is a human-maintained record, not a live sync. Automating routine CRUD from the repo is a separate project.
- **Wiring a Telegram push channel from the cloud.** The local relay is unreachable from the cloud; chasing a webhook/relay bridge is disproportionate for the pilot. Filed issue is the notification; a Slack connector is a deferred option.
- **Generalizing the skill to every SaaS connector.** Keep the skill focused on the reflection-style-audit → routine pattern; don't try to document every connector Anthropic offers.

## Risks

### Risk 1: Coverage gap during cutover (double-file or no-file)
**Impact:** If the reflection is removed before the routine is live, no triage runs (gap). If both run, duplicate GitHub issues (parallel run, violates repo rule).
**Mitigation:** ORDERED sequencing — merge (which removes the reflection) is gated on the operator confirming a successful routine test run. GitHub dedup (`gh issue list --search`) provides belt-and-suspenders against a brief overlap.

### Risk 2: Silent routine failure goes unnoticed
**Impact:** With "filed issue = notification," a routine that fails to run (OAuth expiry, connector failure) files nothing and looks identical to a healthy quiet day. Real Sentry issues would pile up unseen.
**Mitigation:** The maintain-half of the skill documents auditing cloud run history; the pattern doc calls out this observability tradeoff explicitly. Optionally a low-frequency "heartbeat" digest is noted as a future enhancement (deferred).

### Risk 3: Sentry auth provisioning to the cloud is undecided
**Impact:** If no Sentry connector exists in the current catalog and routine secrets can't hold `SENTRY_AUTH_TOKEN`, the routine can't authenticate to Sentry.
**Mitigation:** Operator confirms the mechanism at creation time (connector vs. routine secret). The routine-spec descriptor records whichever is used. Flagged as Open Question 2.

### Risk 4: `run_sentry_triage`'s per-project `working_directory` assumption doesn't hold in the cloud
**Impact:** `_file_github_issue` resolves a per-project `working_directory` from `load_local_projects()`; the cloud clone has a single repo, so project resolution may skip filing.
**Mitigation:** The verification test run (Class-C filed) is the acceptance gate — if filing is skipped in the cloud, the descriptor/recipe is adjusted (e.g. run with `cwd` = the cloned repo) before cutover. Captured as a build-time validation step.

## Race Conditions

### Race 1: Local reflection and cloud routine both fire during cutover window
**Location:** `config/reflections.yaml` entry vs. cloud routine cron
**Trigger:** Routine goes live before the reflection entry is removed (or vice-versa) around a daily fire time.
**Data prerequisite:** GitHub open-issue list is the shared state both writers read for dedup.
**State prerequisite:** At most one active daily triage writer.
**Mitigation:** ORDERED merge gate (routine verified live → then remove reflection in the same merge). `_issue_already_filed` dedup tolerates a single-day overlap without duplicate issues.

## No-Gos (Out of Scope)

- [EXTERNAL] Creating the live Claude Code Routine and running the verification test — requires a Claude.ai Pro+ account and OAuth via `/schedule` or the web console, which a headless agent cannot perform. The build produces the routine-spec descriptor + skill so the operator does this in minutes.
- [ORDERED] Merging the reflection-removal commit — gated on the operator confirming the routine's verification run filed a Class-C issue. Blocked event: "routine verified live."
- [SEPARATE-SLUG #2068] Migrating the remaining cloud-API-audit reflections — tracked in #2068, unblocked by this pattern.
- [EXTERNAL] Adding a Slack/email connector as a secondary push notification channel — optional enhancement requiring connector provisioning; deferred, filed-issue notification is sufficient for the pilot.

## Update System

- **`/update` skill:** if a new global skill directory is added under `.claude/skills-global/`, `sync_claude_dirs()` in `scripts/update/hardlinks.py` hardlinks it to `~/.claude/skills/` automatically — no registration step. Confirm the new skill dir contains a `SKILL.md`.
- **`RENAMED_REMOVALS`:** not needed unless a skill is moved/renamed between `skills/` and `skills-global/`. This is a net-new skill, so no removal entry is required (note this explicitly in the build).
- **Vault registry:** the runtime `~/Desktop/Valor/reflections.yaml` must have the `sentry-issue-triage` entry removed on the owning machine as part of cutover; document this operator step (the tracked `config/reflections.yaml` edit is the versioned record).
- No new propagated dependency or config file beyond the skill dir.

## Agent Integration

- **No new MCP/tool surface for the agent.** `run_sentry_triage` remains reachable on-demand via the existing `/sentry` skill (unchanged). The cloud routine is an external Anthropic object, not a bridge/agent surface.
- **The new skill is agent-invocable** as `/{skill-name}` once synced to `~/.claude/skills/` — it guides create/review/maintain of routines; it does not add a callable code path in the bridge.
- **Integration test:** verify `python -m reflections --dry-run` runs clean post-cutover and that `sentry-issue-triage` is absent from its output; verify the new skill's `SKILL.md` passes the `do-skills-audit` coupling-probe gate.
- Explicit statement: no `bridge/telegram_bridge.py` import changes required.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/cowork-tasks.md` — the reusable pattern: how a Cowork/Routine task is defined, scheduled, authenticated, and how it reports back; **the local-reflection-vs-Cowork decision rule** (live-local-state → stays a reflection; cloud-API-audit-that-files-issues → Cowork candidate).
- [ ] Create `docs/infra/cowork-sentry-triage.md` — the routine-spec descriptor (prompt, cadence, trigger, connectors, required Sentry secret, notification = filed issue). Infra docs are not archived.
- [ ] Add `docs/features/cowork-tasks.md` to `docs/features/README.md` index table.
- [ ] Update `docs/features/reflections.md` / `docs/features/adding-reflection-tasks.md` with a short pointer: audits that are pure cloud-API-audit-that-files-issues belong in Cowork, per the decision rule (link the new pattern doc).

### Inline Documentation
- [ ] Replace the removed `config/reflections.yaml` entry with a one-line pointer comment: "sentry-issue-triage migrated to a Claude Code Routine — see docs/features/cowork-tasks.md" (navigational aid, not a disabled parallel-run entry). Confirm this is the agreed cutover style (Open Question 4).

### Skill Documentation
- [ ] Create `.claude/skills-global/{skill-name}/SKILL.md` — create / review / maintain best practices for Cowork tasks, referencing `code.claude.com/docs/en/routines`; carry the canonical skill-context probe sentence.
- [ ] Create `.claude/skill-context/{skill-name}.md` — the ai-repo-specific decision rule and executable references.

## Success Criteria

- [ ] `docs/features/cowork-tasks.md` exists and defines the reusable pattern including the local-reflection-vs-Cowork decision rule.
- [ ] `docs/infra/cowork-sentry-triage.md` exists as the versioned routine-spec descriptor.
- [ ] A new global skill exists under `.claude/skills-global/` covering create/review/maintain, passing `do-skills-audit` (including the coupling probe).
- [ ] `sentry-issue-triage` is removed from `config/reflections.yaml` and no longer appears in `python -m reflections --dry-run` (clean cutover, no parallel run).
- [ ] `reflections/sentry_triage.py` and the `/sentry` on-demand skill remain functional (unchanged callable).
- [ ] Operator has created the routine and a verification run filed a Class-C GitHub issue (the ORDERED merge gate). [EXTERNAL — recorded, not agent-executed]
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Lead agent orchestrates via Task tools; never builds directly.

### Team Members

- **Builder (pattern-docs)**
  - Name: docs-builder
  - Role: Author `docs/features/cowork-tasks.md` (pattern + decision rule) and `docs/infra/cowork-sentry-triage.md` (routine-spec descriptor).
  - Agent Type: documentarian
  - Resume: true

- **Builder (skill)**
  - Name: skill-builder
  - Role: Author the global skill + skill-context addendum; wire sync (verify `sync_claude_dirs` picks it up); pass `do-skills-audit`.
  - Agent Type: builder
  - Resume: true

- **Builder (cutover)**
  - Name: cutover-builder
  - Role: Remove the reflection entry (+ pointer comment), add the "absent from registry" test, update reflections docs pointers, verify `python -m reflections --dry-run`.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: pilot-validator
  - Role: Verify all success criteria (registry absence, callable intact, skill audit pass, docs present).
  - Agent Type: validator
  - Resume: true

### Available Agent Types
Tier 1 core as listed; `sentry` service agent available if a Sentry-side verification is desired.

## Step by Step Tasks

### 1. Author the reusable pattern + routine-spec docs
- **Task ID**: build-docs
- **Depends On**: none
- **Validates**: files exist; `docs/features/README.md` index updated
- **Informed By**: Research findings (Routines model, auth, notification seam)
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Write `docs/features/cowork-tasks.md`: define/schedule/auth/report-back, and the local-reflection-vs-Cowork decision rule; call out the "filed issue = notification" observability tradeoff (Risk 2).
- Write `docs/infra/cowork-sentry-triage.md`: prompt (delegates to `/sentry --apply`), daily cadence, trigger, connectors, Sentry secret mechanism, cwd note (Risk 4).
- Update `docs/features/README.md` index and add pointers in `docs/features/reflections.md` / `adding-reflection-tasks.md`.

### 2. Author the Cowork skill (+ skill-context)
- **Task ID**: build-skill
- **Depends On**: none
- **Validates**: `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py` (or the skill's audit entry) passes for the new skill; hardlink sync test green
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills-global/{skill-name}/SKILL.md` (create/review/maintain best practices; reference `code.claude.com/docs/en/routines`; canonical probe sentence).
- Create `.claude/skill-context/{skill-name}.md` (ai-repo decision rule + executable references).
- Confirm no `RENAMED_REMOVALS` entry needed (net-new skill); confirm `sync_claude_dirs` will hardlink it.

### 3. Cutover: remove reflection entry + guard test
- **Task ID**: build-cutover
- **Depends On**: build-docs (needs the pattern-doc path for the pointer comment)
- **Validates**: `python -m reflections --dry-run` (clean, no `sentry-issue-triage`); new absence test; `tests/unit/test_sentry_triage_apply.py`
- **Assigned To**: cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove the `sentry-issue-triage` block from `config/reflections.yaml`, leaving the one-line pointer comment.
- Grep and UPDATE any test asserting its presence / a fixed reflection count (`test_reflections_yaml_migration.py`, `test_reflections_local_copy.py`, `test_ui_reflections_data.py`).
- Add a test asserting `sentry-issue-triage` is absent from the loaded registry.
- Leave `reflections/sentry_triage.py` and `.claude/skills/sentry/SKILL.md` untouched.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-docs, build-skill, build-cutover
- **Assigned To**: pilot-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every Success Criterion except the two [EXTERNAL]/[ORDERED] operator gates.
- Run the Verification table commands; report pass/fail.
- Confirm the PR body records the ORDERED merge gate (routine verified live) and the operator steps.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reflection removed from registry | `python -m reflections --dry-run 2>&1 \| grep -c 'sentry-issue-triage'` | match count == 0 |
| Callable preserved | `test -f reflections/sentry_triage.py && echo ok` | output contains ok |
| On-demand skill preserved | `test -f .claude/skills/sentry/SKILL.md && echo ok` | output contains ok |
| Pattern doc exists | `test -f docs/features/cowork-tasks.md && echo ok` | output contains ok |
| Routine-spec descriptor exists | `test -f docs/infra/cowork-sentry-triage.md && echo ok` | output contains ok |
| New global skill exists | `test -f .claude/skills-global/*/SKILL.md; ls .claude/skills-global \| grep -Ei 'cowork\|routine'` | output contains cowork |
| Sentry triage unit tests pass | `pytest tests/unit/test_sentry_triage_apply.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Skill name & scope.** Proposed: a global skill named `cowork` (or `cowork-tasks` / `cloud-routine`) under `.claude/skills-global/`, with the ai-repo decision rule in `.claude/skill-context/`. Confirm the name and that global (ships everywhere) is right vs. project-only.
2. **Sentry auth from the cloud.** Provision `SENTRY_AUTH_TOKEN` as a routine-scoped secret, or use a Sentry connector if one exists in the current catalog? (Operator picks at creation; the descriptor records it.)
3. **Notification seam sign-off.** Confirm "the filed GitHub issue IS the notification" is acceptable (retiring the local Telegram delta path and `sentry_triage_seen.json` for the cloud run), with a Slack connector explicitly deferred.
4. **Cutover style.** Remove the reflection entry entirely and leave a one-line pointer comment (recommended), or remove it with no trace at all? (No disabled/tombstone entry either way — that would be a parallel-run half-migration.)
