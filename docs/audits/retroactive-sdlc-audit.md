# Retroactive SDLC Audit — Triage Report

**Generated:** 2026-04-10
**Scope:** 86 audit items (68 auditable with recovered plan content, 18 unauditable)
**Tracking Issue:** #444

## Executive Summary

The audit covered 86 merged PRs and deleted plan files from the post-SDLC-enforcement window
(2026-03-24 onward, anchored to issue #443 closure). Of the 68 auditable items:

- **4 relevant findings** remain after relevance filtering and deduplication
- **0 high severity** (missing feature documentation)
- **4 medium severity** (missing test files, missing scripts)
- **0 low severity** (stale references, minor gaps)
- **16 findings purged** as no longer relevant (feature removed, file was intentionally deleted, etc.)

The vast majority of shipped features have their docs and tests in order. The 4 remaining findings
are all missing test files — no high-severity documentation gaps remain. Many apparent gaps were
correctly resolved upon closer inspection: issue_poller was removed in #565, the coach module
was intentionally deleted, chat-dev-session-architecture is superseded by pm-dev-session-architecture.md,
and the deployment doc exists at the correct path `docs/features/deployment.md`.

## High Severity Findings

Missing feature documentation — these files were committed to in plan Documentation sections
but do not exist at HEAD.

| # | Feature | Missing File | Merged PR | Evidence |
|---|---------|-------------|-----------|---------|

## Medium Severity Findings

Missing test files and scripts — referenced in plan Test Impact or Success Criteria sections
but not found at HEAD.

| # | Feature | Missing File | Type | Merged PR |
|---|---------|-------------|------|-----------|
| 1 | #600 Remove arbitrary MSG_MAX_CHARS constants | `tests/unit/test_observer_telemetry.py` | missing_test | #612 |
| 2 | #600 Remove arbitrary MSG_MAX_CHARS constants | `tests/unit/test_monitoring_telemetry.py` | missing_test | #612 |
| 3 | #477 Unified Web UI: infrastructure, reflecti | `tests/unit/test_reflection_model.py` | missing_test | #511 |
| 4 | #543 Worker loop exits without picking up pen | `tests/integration/test_job_queue_race.py` | missing_test | #553 |

## Low Severity Findings

Minor gaps — stale references, files that were supposed to be deleted but state is unclear.

| # | Feature | File | Type | Notes |
|---|---------|------|------|-------|

## Purged Findings

16 findings were purged as no longer relevant:

- **#500 `models/finding.py`**: cross-agent-knowledge-relay feature evolved — Finding model concept absorbed into Memory/Subconscious system
- **#564 `docs/features/issue-poller.md`**: issue_poller feature was removed in PR #565 — finding obsolete
- **#564 `tests/test_issue_poller.py`**: issue_poller feature was removed in PR #565 — finding obsolete
- **#564 `tests/test_issue_poller.py`**: issue_poller feature was removed in PR #565 — finding obsolete
- **#564 `tests/test_issue_poller.py`**: issue_poller feature was removed in PR #565 — finding obsolete
- **#541 `docs/features/chat-dev-session-architecture.md`**: pm-dev-session-architecture.md supersedes this — covers same content under the current naming convention
- **#570 `tests/test_issue_poller.py`**: issue_poller feature was removed in PR #565 — finding obsolete
- **#570 `scripts/issue_poller.py`**: issue_poller feature was removed in PR #565 — finding obsolete
- **#638 `docs/features/chat-dev-session-architecture.md`**: pm-dev-session-architecture.md supersedes this — covers same content under the current naming convention
- **#508 `docs/deployment.md`**: doc exists at docs/features/deployment.md — plan used wrong path
- **#654 `tests/unit/test_coach.py`**: File no longer exists at HEAD (was deleted as planned): tests/unit/test_coach.py
- **#654 `bridge/coach.py`**: coach module was intentionally deleted — missing files are expected
- **#654 `tests/unit/test_coach.py`**: coach module was intentionally deleted — missing files are expected
- **#600 `tests/unit/test_observer_telemetry.py`**: File no longer exists at HEAD (was deleted as planned): tests/unit/test_observer_telemetry.py
- **#444 `docs/features/retroactive-plan-audit.md`**: this audit's deliverable is docs/audits/retroactive-sdlc-audit.md — different path, same purpose
- **#444 `data/plan_audit_triage.json`**: Data artifacts in data/ are gitignored — not expected in repo

## Fix Plan

### Category 1: Missing Feature Documentation (0 items — all resolved)

All documentation gaps were resolved during relevance filtering:
- `docs/features/chat-dev-session-architecture.md` → superseded by `docs/features/pm-dev-session-architecture.md`
- `docs/deployment.md` → exists at `docs/features/deployment.md` (plan referenced wrong path)
- `docs/features/issue-poller.md` → issue_poller feature removed in #565, doc not needed
- `docs/features/retroactive-plan-audit.md` → this report (`docs/audits/retroactive-sdlc-audit.md`) is the deliverable

**No action needed.**

### Category 2: Missing Test Coverage (4 items)

These test files were planned but never created:

- **`tests/unit/test_observer_telemetry.py`** — for issue #600 (Remove arbitrary MSG_MAX_CHARS constants and max_l)
- **`tests/unit/test_monitoring_telemetry.py`** — for issue #600 (Remove arbitrary MSG_MAX_CHARS constants and max_l)
- **`tests/unit/test_reflection_model.py`** — for issue #477 (Unified Web UI: infrastructure, reflections dashbo)
- **`tests/integration/test_job_queue_race.py`** — for issue #543 (Worker loop exits without picking up pending jobs )

**Action:** Create a single PR adding the missing test files. Each test file should cover
the integration scenarios described in the original plan's Test Impact section.

### Category 3: Stale References / Other (0 items)


## Unauditable Items (18)

These 18 items from the #823 list had no recoverable plan files. They were audited using
issue body as context only, which is insufficient for file-assertion checking. These items
should be treated as already-addressed unless there is specific evidence of gaps.

- Issue #749: feat(steering): externalize session steering via queued_stee
- Issue #764: Unify AgentSession parent field on parent_agent_session_id (
- Issue #781: Fix: valor_session create triggers worker immediately via Re
- Issue #784: fix(worker): trigger session pickup immediately via Redis pu
- Issue #787: Fix watchdog UTC duration: _to_timestamp treats naive dateti
- Issue #789: fix(worker): exit code 1 on SIGTERM so launchd respects Thro
- Issue #790: Dashboard UI fixes: status layout, reflections redesign
- Issue #793: fix: resolve AgentSession status index corruption (ghost run
- Issue #794: feat(hooks): wire Skill tool invocations into PipelineStateM
- Issue #796: Purge ChatSession/DevSession vocabulary; use AgentSession wi
- Issue #801: fix(queue): prevent duplicate worker spawns per chat_id via 
- Issue #802: fix(sdlc): enforce CRITIQUE and REVIEW gates in PM persona a
- Issue #803: fix(tests): update stale format_duration assertions (#799)
- Issue #807: feat: add AI semantic evaluator step to /do-build pipeline (
- Issue #812: fix: valor-session kill uses finalize_session for terminal t
- Issue #813: fix: local session type now reflects SESSION_TYPE env var
- Issue #815: feat(sdlc): propagation check, Implementation Note field, an
- Issue #819: fix(timestamp): add explicit UTC labels to all timestamp dis

## Verification Checklist

After fix PRs are merged:

- [x] `docs/features/pm-dev-session-architecture.md` exists and covers parent-child session flow — CONFIRMED
- [x] `docs/features/deployment.md` exists — CONFIRMED (plan #508 referenced wrong path `docs/deployment.md`)
- [ ] Missing test files added: `tests/unit/test_observer_telemetry.py`, `tests/unit/test_monitoring_telemetry.py`, `tests/unit/test_reflection_model.py`, `tests/integration/test_job_queue_race.py`
- [x] Zero `still_relevant: true` + `severity: high` findings — CONFIRMED (0 high severity findings)

## Methodology

1. **Audit set**: 70 deleted plans recovered from git history via `git log --diff-filter=D --after='2026-03-24'` + 18 explicit #823 issues
2. **Per-item audit**: Checked Documentation section, Test Impact section, and Success Criteria for each plan against HEAD file existence
3. **Relevance filter**: Purged findings where the underlying feature was removed (issue_poller), the deletion was intentional (coach module), or data artifacts are gitignored by design
4. **Deduplication**: Merged duplicate references to the same file (e.g., tests/test_issue_poller.py appeared 3x)

## Next Steps

1. **Medium priority**: Ship tests PR for 4 missing test files:
   - `tests/unit/test_observer_telemetry.py` — telemetry coverage for remove-msg-max-chars (#600, PR #612)
   - `tests/unit/test_monitoring_telemetry.py` — monitoring telemetry coverage (#600, PR #612)
   - `tests/unit/test_reflection_model.py` — reflection model unit tests for Unified Web UI (#477, PR #511)
   - `tests/integration/test_job_queue_race.py` — race condition test for worker pending drain (#543, PR #553)
2. **No high priority docs work needed** — all doc gaps were either resolved (superseded by newer docs) or confirmed present at correct paths
3. **No stale reference cleanup needed** — no low severity stale_ref findings remain after purging
