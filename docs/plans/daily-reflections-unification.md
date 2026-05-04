---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1276
last_comment_id:
---

# Daily Reflections Unification — Per-Project, Machine-Gated, Single Telegram Delivery

## Problem

After PR #1264 (merged at `871a45c5`), the daily activity story is fragmented across two reflections that both fire daily and both deliver to the same Telegram chat (`Dev: Valor`):

- `daily-log-review` — text summary of yesterday's logs (file sizes, error counts, regression markers)
- `daily-report-and-notify` — vault Markdown + audio brief of yesterday's activity (sessions, commits, telegram, memory, crashes)

The structural intent of #1263 was consolidation — replace the noisy old daily report with one canonical daily-log story. The merged result instead **adds a second daily reflection** that runs alongside the existing one, doubles Telegram noise, runs system-wide instead of per-project, and re-implements machinery (`pm-audio-briefing` already solved per-project + machine-gated + slot-poll cleanly).

**Current behavior (with PR #1264 merged):**

1. Two reflections fire daily and both post to `Dev: Valor`. Noise multiplies.
2. `daily-report-and-notify` lives under `# --- Pipeline callables ---` in `config/reflections.yaml:262`, separate from the auditing siblings (`daily-log-review`, `documentation-audit`, `skills-audit`) at lines 194–218. **Cosmetic / code-organization issue only — see Freshness Check #1 for correction.**
3. `reflections/daily_report.py` imports from `reflections.pm_audio_briefing.builder` but is a parallel callable with its own collection, delivery, and outbox key. Not a unified code path.
4. `daily_report.run()` aggregates **system-wide** into a single vault file `daily-logs/{date}.md` and one voice note. `pm-audio-briefing` and `skills-audit` both fan out via `load_local_projects()` and produce one artifact per project.
5. Cadence is true daily UTC (`interval: 86400`) — no per-project HH:MM slot logic, so it can't honor a project's local timezone or delivery hour.
6. **Neither** `daily-log-review` **nor** `daily-report-and-notify` filter on machine ownership. Every machine running the worker fires them; `pm-audio-briefing` filters via `_resolve_machine()`.
7. 24 orphan stub files `logs/reflections/report_2026-*.md` remain on disk from the pre-vault implementation (gitignored, but unused and confusing).

**Desired outcome:**

One canonical daily activity story per project per day, delivered once to that project's PM Telegram chat in the project's local timezone, running on exactly one machine (the one that owns the project). No double-posting. Old orphan stubs cleaned up. The dashboard renders per-project rows under the `audits` group.

## Freshness Check

**Baseline commit:** `d1468da9132441846254c7878726032147f15dfc`
**Issue filed at:** 2026-05-04T09:53:06Z (today, ~10 minutes before plan)
**Disposition:** Minor drift

**File:line references re-verified:**

- `config/reflections.yaml:262` — `# --- Pipeline callables ---` section header — still holds; `daily-report-and-notify` block is at lines 280–286.
- `config/reflections.yaml:194` — `# --- Auditing group ---` — still holds; `daily-log-review`, `documentation-audit`, `skills-audit` at lines 196–218.
- `reflections/daily_report.py:1072` — `load_local_projects()` call — drifted to line ~1072 in `_send_audio_brief()`, still holds; ALSO called at line 615 in `_collect_day_activity()`.
- `reflections/pm_audio_briefing/__init__.py:32-46` — `_resolve_machine()` — confirmed at lines 32–43 (uses `scutil --get ComputerName`, returns empty on failure).
- `reflections/pm_audio_briefing/__init__.py:240-254` — fan-out + `pm_briefing.enabled` filter + machine match — confirmed at lines 240–249.
- `reflections/auditing.py:639` — `load_local_projects()` in `run_skills_audit` — still holds.
- `logs/reflections/report_2026-*.md` orphan stubs — confirmed: 24 files exist on disk.

**Cited sibling issues/PRs re-checked:**

- #1263 — OPEN; this plan's parent issue. Status unchanged.
- PR #1264 — MERGED at `871a45c5` (between issue creation and plan creation). **Pre-requisite satisfied.**
- #1188 / PR #1230 — Closed/merged; established `daily-log-review` as a local Telegram-posting reflection.
- #1197 / PR #1237 — Closed/merged; introduced `pm-audio-briefing` as the canonical per-project pattern.
- #1187 / `docs/plans/per-project-audit-reflections.md` — `status: docs_complete`; documents the `run_per_project_audit()` helper + `mark_completed(projects=)` extension. **Critical reference — see Architectural Impact below.**

**Commits on main since issue was filed (touching referenced files):** None. Issue filed at 09:53Z; HEAD has not advanced.

**Active plans in `docs/plans/` overlapping this area:**

- `docs/plans/per-project-audit-reflections.md` (#1187) — same per-project + machine-ownership pattern, status `docs_complete`. Coordination signal: this plan must reuse `run_per_project_audit()` from `reflections/utils.py` (already implemented) and assume the `mark_completed(projects=)` extension (documented but **not yet in code**). Decision in Open Question #4 below.
- `docs/plans/daily-log-overhaul.md` (#1263) — parent plan; will be marked superseded by this one.

**Notes:**

- **Major correction to issue #1276 claim #2 (wrong dashboard group):** Verified `ui/data/reflections.py:62` — `"daily-report-and-notify": GROUP_AUDITS` is **already correctly assigned**. Dashboard groups are 100% Python-driven via the `REFLECTION_GROUPS` dict, not parsed from YAML section comments. The YAML section comment is purely organizational and has zero dashboard impact. The cosmetic mismatch (YAML "Pipeline callables" comment vs Python `GROUP_AUDITS` assignment) should still be fixed for code-reading consistency, but the dashboard already shows it in the right place.
- **`pm-audio-briefing` is in `GROUP_AGENTS`, not `GROUP_AUDITS`.** Verified at `ui/data/reflections.py:28`. So "make it like pm-audio-briefing's group" is the wrong framing — the audits group is correct for daily-log review/report; pm-audio-briefing is the *pattern* to copy, not the *group* to join.

## Prior Art

- **PR #1237 / #1197** — Daily PM audio briefing reflection — introduced the canonical per-project, machine-gated, slot-poll pattern. The collector / builder / delivery split, the `_resolve_machine()` filter, the `Reflection.get_or_create(f"pm-audio-briefing-{slug}")` per-project record, and the SETNX-per-project-per-day idempotency lock all live in `reflections/pm_audio_briefing/`. **This is the reference implementation.**
- **PR #1230 / #1188** — Rebuilt `daily-log-review` as a local reflection sending a text summary to Telegram. Per-project iteration via `for project in load_local_projects()` (no machine gate, no per-project enabled flag). Posts a single aggregated message to `Dev: Valor`.
- **PR #1264 / #1263** — Daily log overhaul; vault archival + audio brief. System-wide collection, single delivery to first PM chat. **The PR this plan is the follow-up to.**
- **Plan #1187 / `docs/plans/per-project-audit-reflections.md`** — Documents the `run_per_project_audit()` helper (now in `reflections/utils.py:70-150`) and the proposed `mark_completed(duration, error=None, projects=None)` signature extension + scheduler `result.get("projects", [])` forwarding + dashboard per-project row template. Status `docs_complete`; helper is implemented but the `mark_completed` extension is not yet in code.
- **PR #561** — `run_pr_review_audit` — the original per-project audit pattern that #1187 generalized. Reference for findings prefix `[{slug}]`.

## Research

No relevant external findings — proceeding with codebase context. This work is purely internal: refactoring Python callables in `reflections/`, possibly extending `models/reflection.py` and `agent/reflection_scheduler.py`, updating `config/reflections.yaml` and `ui/data/reflections.py`. No external libraries are involved.

## Spike Results

No spikes were dispatched — every assumption was validated by direct code-read during reconnaissance:

### spike-1 (code-read): Where do dashboard groups come from?
- **Assumption**: "YAML section comments drive dashboard grouping."
- **Finding**: **False.** `ui/data/reflections.py:34-65` — `REFLECTION_GROUPS` is a hard-coded Python dict. YAML section comments are organizational only.
- **Confidence**: high
- **Impact on plan**: Removes acceptance criterion "move daily-report-and-notify to Auditing group on dashboard" — already there. YAML section comment fix is cosmetic (still worth doing).

### spike-2 (code-read): Does the per-project rendering plumbing exist?
- **Assumption**: "Adding `daily-report-and-notify` to `_PREFIX_EXPANDED_REFLECTIONS` is sufficient for per-project rows."
- **Finding**: **Partially.** The tuple at `ui/data/reflections.py:74` controls per-project row rendering, BUT the per-project records must exist as `Reflection.get_or_create(f"{prefix}-{slug}")` for the dashboard to surface them. The `mark_completed(projects=)` extension from plan #1187 is documented but NOT yet implemented in `models/reflection.py:85-120`. Per-project rows on the dashboard depend on the per-project Reflection records being created in the callable, not on the mark_completed extension.
- **Confidence**: high
- **Impact on plan**: Per-project rendering works as long as we create per-project Reflection records (matching the `pm-audio-briefing-{slug}` pattern). We do NOT need to wait for #1187's `mark_completed(projects=)` extension to ship.

### spike-3 (code-read): Does daily_report have any per-project structure today?
- **Assumption**: "daily_report calls `load_local_projects()` only for delivery chat lookup."
- **Finding**: True. Confirmed at `reflections/daily_report.py:1072` (delivery only). Also called at line 615 inside `_collect_day_activity()` for git/gh per-project enrichment, but the aggregated brief is single-system.
- **Confidence**: high
- **Impact on plan**: Refactor must add per-project structure at every layer (collection, build, delivery, idempotency). Existing git/gh per-project enrichment can be reused as data-collection scaffolding.

### spike-4 (code-read): Is the `run_per_project_audit()` helper usable as-is?
- **Assumption**: "We can reuse `reflections/utils.py:run_per_project_audit()` for the consolidation."
- **Finding**: True. The helper at `reflections/utils.py:70-150` accepts `(audit_one, *, skip_if=None, name)`, iterates `load_local_projects()`, wraps `skip_if` and `audit_one` in try/except, prefixes findings with `[{slug}]`, and returns `{status, findings, summary, projects: [{slug, status, duration, findings_count, error}]}`. **Does not** include machine-ownership filter — that must be done in the caller (mirroring `pm_audio_briefing.__init__.py:240-249`).
- **Confidence**: high
- **Impact on plan**: Use `run_per_project_audit()` for the `audit_one` body but pre-filter projects by machine ownership before passing them in (or wrap the helper).

## Data Flow

For the **target consolidated reflection** (one Telegram delivery per project per day):

1. **Entry point**: `reflection_scheduler.execute_function_reflection` invokes the registered callable (target name TBD per Open Question #1).
2. **`run()`** calls `_resolve_machine()` → `load_local_projects()` → filters to `(project.get("daily_report") or {}).get("enabled") is True` AND `project.get("machine") == this_machine`.
3. **Per-project loop**: For each owned-and-enabled project:
   1. **Slot match** (only if Open Question #2 → slot-poll): check current local time matches `daily_report.schedule` (HH:MM) within 5-minute window.
   2. **Idempotency lock**: SETNX `daily-report-lock:{slug}:{today_iso}` (mirrors pm-audio-briefing pattern).
   3. **Collect**: `_collect_day_activity_for_project(project, target_date)` — extracts the project's slice of yesterday's activity (sessions, commits, telegram messages, memories, crashes, reflections) using existing collectors but scoped per project.
   4. **Build text** (replaces `daily-log-review`'s text path): render the per-project Markdown summary.
   5. **Build audio**: `pm_audio_briefing.builder.build()` produces per-project transcript + TTS payload.
   6. **Deliver**: enqueue ONE Telegram message (text + voice, or just voice) to the project's PM chat — `telegram:outbox:daily-report-{slug}-{date}`.
   7. **Vault write** (system-wide, deferred to step 4): per-project section appended to `~/work-vault/.../daily-logs/{date}.md`.
   8. **`Reflection.get_or_create(f"daily-report-{slug}").mark_completed(duration, error=None)`** — per-project record for dashboard rendering.
4. **Vault aggregation (post-loop)**: assemble all per-project Markdown blocks into a single `daily-logs/{date}.md` for the work vault. (Vault is shared across machines via iCloud, so only ONE machine writes it — pick the owner of the "ai" project, or skip from non-owners.)
5. **Aggregate result**: `{status: "ok"|"partial"|"error", projects: {slug: {status, duration, error}}, summary: {considered, succeeded, failed}}` returned to the scheduler.

**Key boundary:** the legacy `daily-log-review` callable is REMOVED (or its logic merged into the consolidated callable — see Open Question #1). Either way, the user receives **one** Telegram delivery per project per day, not two.

## Architectural Impact

- **New dependencies**: None. All scaffolding (`load_local_projects()`, `_resolve_machine()`, `run_per_project_audit()`, `pm_audio_briefing.builder`) already exists.
- **Interface changes**:
  - New per-project enable flag in `projects.json`: `daily_report.enabled` (bool) + optional `daily_report.schedule` (HH:MM string) + optional `daily_report.target_group` (string, falls back to first PM Telegram group).
  - Reflection name suffix pattern: `daily-report-{slug}` — requires adding `"daily-report"` (or chosen prefix) to `_PREFIX_EXPANDED_REFLECTIONS` tuple in `ui/data/reflections.py:74`.
  - Outbox key changes from `telegram:outbox:daily-report-and-notify-{date}` to `telegram:outbox:daily-report-{slug}-{date}`.
- **Coupling**: Reduces coupling. Today the daily activity story is split across `reflections/auditing.py::run_log_review` and `reflections/daily_report.py::run`. After consolidation, one canonical entry point owns the daily-per-project story.
- **Data ownership**: Per-project Reflection records (`daily-report-{slug}`) replace the single global `daily-report-and-notify` and `daily-log-review` records. Old records remain in Redis history but stop receiving new run entries.
- **Reversibility**: Mostly reversible. The hard part is per-project Reflection records — once we start writing them, the dashboard depends on them. Rolling back means deleting the per-project records and re-enabling the global ones. Vault file format is forward/backward compatible (per-project sections concatenate cleanly).
- **Relationship to plan #1187**: This plan is independent — it does NOT require #1187's `mark_completed(projects=)` extension to ship first. Per-project rows render via the `_PREFIX_EXPANDED_REFLECTIONS` mechanism, which already works. If/when #1187 ships, this reflection can opt into per-project sub-rows in the modal additionally.

## Appetite

**Size:** Medium

**Team:** Solo dev + plan critique + code review

**Interactions:**
- PM check-ins: 2–3 (decisions on Open Questions #1–#4 before build; mid-build sanity check after the per-project skeleton lands)
- Review rounds: 1–2 (PR review, possibly a follow-up for the cadence/slot-poll mechanism if Open Question #2 picks slot-poll)

This is structural refactoring of an existing reflection plus deletion of a sibling reflection — communication overhead dominates code time. The mechanical changes are bounded; the open questions are where the time goes.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #1264 merged | `gh pr view 1264 --json state -q .state` returns `MERGED` | Satisfied at `871a45c5` |
| Open Questions #1–#4 answered | Manual: human reviews this plan and answers the four questions | Direction-defining decisions; do-build cannot proceed without them |
| `pm-audio-briefing` is healthy | `python -c "from reflections.pm_audio_briefing import _resolve_machine; print(_resolve_machine())"` returns non-empty | We're copying its pattern; if it's broken, we should fix it first |

## Solution

### Key Elements

- **Single consolidated daily reflection**: one callable, per-project fan-out, machine-gated, single Telegram delivery per project per day.
- **Per-project enable flag**: opt-in via `projects.json` so projects without daily-report config silently skip.
- **Per-project Reflection records**: `daily-report-{slug}` pattern — drives dashboard per-project rendering via `_PREFIX_EXPANDED_REFLECTIONS`.
- **Per-project SETNX lock**: idempotency per project per day, scoped exactly like pm-audio-briefing.
- **Vault file**: still one `daily-logs/{date}.md` per machine, written by the project owner of "ai" (or skipped on non-owner machines) — vault is shared via iCloud.
- **Removal**: delete (or fold into the consolidated callable) `daily-log-review`. No more two reflections.
- **Cleanup**: delete the 24 orphan `logs/reflections/report_2026-*.md` stubs.

### Flow

**Worker tick (every 5 min)** → registry check on `daily-report-and-notify` interval → `run()` → `_resolve_machine()` + filter projects by ownership AND `daily_report.enabled` → for each owned project: lock → collect activity → build per-project transcript → enqueue ONE Telegram message → `Reflection.mark_completed(daily-report-{slug})` → after loop: assemble vault file (if this machine owns "ai") → return aggregate.

### Technical Approach

The shape of the work depends on three structural decisions that the human must make (Open Questions #1–#3 below). Two implementation skeletons follow.

**Branch A — "Single consolidated callable" (recommended path if Q1=consolidate, Q2=slot-poll, Q3=nest-under-pm_audio_briefing):**

- Move `reflections/daily_report.py` → `reflections/pm_audio_briefing/daily_log.py`.
- Rename callable from `daily-report-and-notify` to `pm-daily-log` (or similar — naming TBD per Q1).
- Reuse `pm_audio_briefing.__init__._resolve_machine()` and the schedule-slot helper directly.
- New per-project schema field: `pm_briefing.daily_log` sub-dict (`enabled: bool, schedule: "HH:MM", angles: [...]`) — keeps all "PM-facing daily content" config in one place.
- Delete `reflections/auditing.py::run_log_review` (the callable, not the file). Remove `daily-log-review` from `config/reflections.yaml` and `ui/data/reflections.py:REFLECTION_GROUPS`.
- Add `"pm-daily-log"` (or chosen name) to `_PREFIX_EXPANDED_REFLECTIONS`.

**Branch B — "Two reflections, disjoint deliverables" (fallback if Q1=keep-two):**

- Keep `daily-log-review` but remove its Telegram delivery — convert it to a vault-only audit. It writes findings to a vault file (e.g. `~/work-vault/.../daily-logs/{date}-log-audit.md`). No more posting to `Dev: Valor`.
- Refactor `daily-report-and-notify` to per-project + machine-gated as above (Branch A's per-project skeleton).
- Net effect: one Telegram message per project per day (the audio brief), one vault file, one separate vault audit file.

**Mechanical changes shared by both branches:**

- Add `_resolve_machine()` and machine-ownership filter to whichever callable owns Telegram delivery.
- Add per-project SETNX lock: `daily-report-lock:{slug}:{today_iso}`.
- Switch outbox key from global `daily-report-and-notify-{date}` to per-project `daily-report-{slug}-{date}`.
- Switch from `Reflection.get_or_create("daily-report-and-notify")` to per-project `Reflection.get_or_create(f"daily-report-{slug}")`.
- Add the prefix to `_PREFIX_EXPANDED_REFLECTIONS` in `ui/data/reflections.py:74`.
- Move the `daily-report-and-notify` block in `config/reflections.yaml` from `# --- Pipeline callables ---` to `# --- Auditing group ---` (cosmetic; dashboard already classifies correctly).
- Delete the 24 `logs/reflections/report_2026-*.md` orphan stubs.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Per-project iteration must wrap each project's body in try/except — one project's failure must not abort the loop. `run_per_project_audit()` already does this; if writing the loop directly, mirror the pattern.
- [ ] SETNX failure (Redis down) must log a warning and skip the project, not crash the run.
- [ ] `_resolve_machine()` returning empty string must result in zero deliveries (defensive — current pm-audio-briefing behavior).
- [ ] TTS failure for one project must not abort the loop or block other projects' deliveries.

### Empty/Invalid Input Handling
- [ ] If `load_local_projects()` returns empty (no projects configured) → returns `{status: "ok", projects: {}, summary: {considered: 0, succeeded: 0, failed: 0}}` without crashing.
- [ ] If a project has `daily_report.enabled: false` → silently skipped, not counted as failure.
- [ ] If a project has `daily_report.enabled: true` but `machine` doesn't match this host → silently skipped.
- [ ] If a project's activity collector returns zero data for the day → either (a) skip delivery with `skip_when_empty: true` per-project, OR (b) deliver "Nothing happened yesterday" — decision per Open Question #5.
- [ ] If `_send_audio_brief()` is invoked with empty transcript → already raises `BriefingNumbersDetectedError` or skips per existing audio guards (preserve PR #1264's defensive checks).

### Error State Rendering
- [ ] Per-project failures must surface in the aggregate result dict (`projects: {slug: {status: "error", error: "..."}}`) and in `Reflection.mark_completed(error=...)` for the per-project record.
- [ ] Dashboard modal must render the per-project status (already handled by `_PREFIX_EXPANDED_REFLECTIONS` machinery if records exist).
- [ ] Test: one project fails TTS, two succeed → all three deliveries either complete or are isolated; dashboard shows 1 failed + 2 succeeded.

## Test Impact

- [ ] `tests/integration/reflections/test_daily_report_integration.py` — REPLACE: rewrite for per-project fan-out. Old test asserts single global vault write + single audio outbox; new test asserts one outbox payload per eligible project + one vault file containing per-project sections.
- [ ] `tests/unit/reflections/test_daily_report_aggregator.py` — UPDATE: collectors must accept a project parameter (or be wrapped). System-wide collectors stay; per-project enrichment is the new layer.
- [ ] `tests/unit/reflections/test_daily_report_renderer.py` — UPDATE: renderer produces per-project Markdown blocks instead of single global block.
- [ ] `tests/unit/reflections/test_daily_report_audio_guard.py` — KEEP: guards are reflection-agnostic; no change.
- [ ] `tests/unit/test_reflections_auditing.py` (`run_log_review` tests) — DELETE (Branch A) or UPDATE (Branch B): if `daily-log-review` is removed, drop its tests; if kept as vault-only, update tests to assert vault write + assert NO Telegram outbox enqueue.
- [ ] **NEW** `tests/integration/reflections/test_daily_report_per_project.py` — CREATE: end-to-end test that with three configured projects (one disabled, one owned-by-other-machine, one enabled-and-owned), exactly one delivery is enqueued and one per-project Reflection record is updated.
- [ ] **NEW** `tests/unit/reflections/test_daily_report_machine_gate.py` — CREATE: unit tests for the machine-ownership filter (mirrors pm_audio_briefing's tests if any exist; otherwise greenfield).

## Rabbit Holes

- **Don't generalize `pm_audio_briefing` into a "reflection framework" right now.** Tempting to extract `_resolve_machine()`, the slot-poll helper, and `run_per_project_audit()` into a shared `ReflectionBase` class. Don't. The two reflections share enough to copy a few patterns; framework abstraction is premature and would block this plan on architecture debate.
- **Don't fix plan #1187's `mark_completed(projects=)` extension as part of this plan.** That's a separate piece of work (already documented). This plan's per-project rendering works without it via `_PREFIX_EXPANDED_REFLECTIONS`. If we touch `models/reflection.py`, scope creeps.
- **Don't redesign the vault Markdown format.** PR #1264 ships a format; this plan only adds per-project sections to it. Reformatting belongs in a separate plan if the existing format is wrong.
- **Don't try to preserve historical run records for `daily-log-review` and `daily-report-and-notify`.** They become obsolete; the dashboard already handles missing/old records gracefully. Migration of old run history is not worth the code.
- **Don't add a per-project timezone field if we're picking true daily.** The slot-poll branch needs it; the true-daily branch doesn't. Pick one (Open Question #2) before adding schema.

## Risks

### Risk 1: Per-project Reflection records pollute the dashboard
**Impact:** If three projects are configured, the dashboard now shows three new rows under `audits` (`daily-report-ai`, `daily-report-popoto`, `daily-report-psyoptimal`). Plus the parent `daily-report` parent row. Visual clutter for users with many projects.
**Mitigation:** Mirror pm-audio-briefing's exact rendering — per-project rows expand only under the parent's modal, not as top-level rows. Verify in `ui/data/reflections.py` rendering logic before shipping.

### Risk 2: Vault file write race across machines
**Impact:** Two machines both think they own "ai" (config bug or stale state) → both write `daily-logs/{date}.md` → iCloud sync produces a conflict-copy file.
**Mitigation:** Vault write is gated by "this machine owns the project named in `vault_owner_project`" config (default: `ai`). If unset, only the project-owning machine for the project containing the vault path writes. Documented explicitly in the plan; integration test asserts non-owners produce zero vault writes.

### Risk 3: Loss of audit findings if `daily-log-review` is deleted (Branch A)
**Impact:** Branch A removes the log-scan audit (file sizes, error counts, regression markers in `~/src/{project}/logs/`). If users relied on the Telegram summary for ops awareness, they lose it.
**Mitigation:** Verify with the human (Open Question #1) whether the log-scan content is essential. If yes, fold it INTO the per-project daily-report audio/text brief. If no, drop it.

### Risk 4: Per-project enable flag schema collision with #1187
**Impact:** Plan #1187 adds per-project iteration to five other audits and may introduce its own per-project enable conventions. If we pick a different schema name (e.g. `daily_report.enabled` vs the convention #1187 picks), we get inconsistency.
**Mitigation:** Open Question #4 — coordinate schema with #1187 before shipping. If #1187 hasn't shipped, this plan sets the convention.

## Race Conditions

### Race 1: Two machines both pass the machine filter for the same project
**Location:** `reflections/{daily_report}.py::run()` machine-ownership filter
**Trigger:** `projects.json` mistakenly assigns a project to two machine names, OR `_resolve_machine()` returns the same string on two machines (e.g. both `Valor the Cowboy`).
**Data prerequisite:** `projects[slug].machine` must be set to exactly one machine name; `_resolve_machine()` must return distinct values per host.
**State prerequisite:** Single-machine ownership invariant from `docs/features/single-machine-ownership.md`.
**Mitigation:** SETNX per-project-per-day lock prevents double-delivery even if filter is bypassed. The `daily-report-lock:{slug}:{today}` key is the durable safety net. Lock TTL = end of day in project's TZ.

### Race 2: Per-project Reflection record created concurrently with another machine
**Location:** `Reflection.get_or_create(f"daily-report-{slug}")`
**Trigger:** Race 1 happens AND both machines pass the SETNX (impossible if SETNX works, but defensive).
**Data prerequisite:** `Reflection.get_or_create()` must be atomic (it is — uses Popoto's atomic create-if-not-exists).
**Mitigation:** Atomic create-if-not-exists in Popoto; both machines see the same record; `mark_completed()` updates are serialized through Redis.

### Race 3: Vault file write race
**Location:** Vault assembly step (post-loop in `run()`)
**Trigger:** Two machines both decide they own the vault writer.
**Mitigation:** See Risk 2 — gated by `vault_owner_project` config + integration test.

## No-Gos (Out of Scope)

- **Not extending `pm_audio_briefing` to a generic "PM digest framework"** — keep this plan tactical.
- **Not implementing plan #1187's `mark_completed(projects=)` extension** — orthogonal.
- **Not migrating historical Reflection run records** for the soon-to-be-defunct `daily-log-review` / `daily-report-and-notify` records.
- **Not redesigning the vault Markdown format** — additive per-project sections only.
- **Not adding new per-project audits** beyond the daily activity story — that's #1187's territory.
- **Not changing `pm-audio-briefing` itself** — it's the reference; touching it widens the blast radius.
- **Not fixing the YAML section comment misnaming for OTHER reflections** — only fix `daily-report-and-notify`'s placement.

## Update System

This change does not require update-skill or update-script changes. The new per-project schema fields (`daily_report.enabled`, optional `daily_report.schedule`, optional `daily_report.target_group`) live in `~/Desktop/Valor/projects.json`, which is iCloud-synced — no update-script propagation needed. The reflection registry is in-repo (`config/reflections.yaml`), shipped via normal git pull.

**However:** users on machines that have iCloud-synced their `projects.json` from before this plan will have NO `daily_report` block in their projects → all projects silently skip → zero daily deliveries until they add the schema. This is the intended fail-closed behavior, but it should be documented in the PR body and `docs/features/reflections.md` so users know to opt in.

## Agent Integration

No agent integration changes required — this is a worker-internal reflection change. The agent does not invoke daily reflections directly; the worker's reflection scheduler does. Telegram delivery still flows through the standard outbox → bridge relay path.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` — replace `daily-log-review` and `daily-report-and-notify` entries with the consolidated reflection's entry; document the per-project enable flag schema and machine-ownership behavior.
- [ ] Update `docs/features/README.md` index if reflection naming changes (e.g. if the consolidated reflection gets a new name like `pm-daily-log`).
- [ ] Add a "superseded by" note to `docs/plans/daily-log-overhaul.md` pointing to this plan.
- [ ] Add an entry to `docs/features/single-machine-ownership.md` mentioning the new per-project enable flag schema (`daily_report.enabled`).

### Inline Documentation
- [ ] Docstring on the consolidated reflection's `run()` explaining the per-project + machine-gated + slot-poll model and why it's structured this way (reference pm_audio_briefing as the pattern source).
- [ ] Update inline comments in `config/reflections.yaml` so the section header matches the dashboard group classification.

### Code Cleanup
- [ ] Delete the 24 orphan `logs/reflections/report_2026-*.md` stubs.
- [ ] Remove the `logs/reflections/` gitignore entry if no other code writes there post-cleanup (verify with `grep -rn "logs/reflections" .` first).

## Success Criteria

- [ ] Open Questions #1–#5 answered (in this plan doc) before any code change.
- [ ] After one daily run: each eligible project's PM Telegram chat receives **exactly one** delivery — verified by integration test.
- [ ] Non-owner machines produce **zero** Telegram deliveries for projects they don't own — verified by integration test.
- [ ] Disabled projects (`daily_report.enabled: false`) produce zero deliveries — verified by integration test.
- [ ] Dashboard renders per-project rows under the parent reflection in the `audits` group — verified manually via `curl localhost:8500/dashboard.json`.
- [ ] All 24 `logs/reflections/report_2026-*.md` orphan stubs deleted.
- [ ] `docs/features/reflections.md` reflects the consolidated design.
- [ ] `docs/plans/daily-log-overhaul.md` has a superseded note pointing here.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No double-delivery: `valor-telegram read --chat "Dev: Valor" --since "1 day ago"` shows one daily activity message per project, not two.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (consolidation)**
  - Name: `daily-report-consolidator`
  - Role: Refactor `daily_report.py` (or relocate to `pm_audio_briefing/daily_log.py`) per Branch A or B; add machine gate, per-project loop, SETNX, per-project Reflection records.
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: `legacy-log-review-cleaner`
  - Role: Remove (Branch A) or de-Telegram-ify (Branch B) `daily-log-review`; delete orphan stubs; update `config/reflections.yaml` section comment.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `daily-report-test-eng`
  - Role: Write the new per-project integration tests; update existing tests per Test Impact.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `daily-report-validator`
  - Role: Verify per-project fan-out, machine gate, no double-delivery; check dashboard rendering manually via curl.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `daily-report-doc`
  - Role: Update `docs/features/reflections.md`, add superseded note to `daily-log-overhaul.md`, update single-machine-ownership doc.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

**Note:** Tasks below assume **Branch A (consolidate)** as the recommended path. If Open Question #1 selects Branch B, the lead replaces task `build-consolidate` with `build-disjoint-deliverables` (refactor daily_report.py per-project + remove daily-log-review's Telegram delivery, keep its callable).

### 1. Refactor daily_report.py for per-project + machine gate
- **Task ID**: build-consolidate
- **Depends On**: none
- **Validates**: `tests/integration/reflections/test_daily_report_per_project.py`, `tests/unit/reflections/test_daily_report_machine_gate.py`
- **Informed By**: spike-1 (dashboard groups Python-driven), spike-2 (per-project rendering), spike-4 (run_per_project_audit reusable)
- **Assigned To**: daily-report-consolidator
- **Agent Type**: builder
- **Parallel**: false
- Add `_resolve_machine()` import (or copy) and machine-ownership filter at top of `run()`.
- Add per-project enable flag check `(p.get("daily_report") or {}).get("enabled") is True`.
- Refactor `_send_audio_brief()` to accept a `project` parameter and deliver per-project (one outbox payload per call).
- Add per-project SETNX lock `daily-report-lock:{slug}:{today_iso}` mirroring `pm_audio_briefing.__init__.py` lock pattern.
- Switch from `Reflection.get_or_create("daily-report-and-notify")` to per-project `Reflection.get_or_create(f"daily-report-{slug}")`.
- Add `"daily-report"` to `_PREFIX_EXPANDED_REFLECTIONS` in `ui/data/reflections.py:74`.

### 2. Remove daily-log-review (Branch A) or de-Telegram-ify it (Branch B)
- **Task ID**: build-cleanup-legacy
- **Depends On**: build-consolidate
- **Validates**: `tests/unit/test_reflections_auditing.py` (updated or deleted per Test Impact)
- **Assigned To**: legacy-log-review-cleaner
- **Agent Type**: builder
- **Parallel**: false
- Branch A: Delete `run_log_review` from `reflections/auditing.py`; remove `daily-log-review` block from `config/reflections.yaml`; remove `"daily-log-review": GROUP_AUDITS` from `ui/data/reflections.py:53`; delete `tests/unit/test_reflections_auditing.py` cases for `run_log_review`.
- Branch B: Strip Telegram delivery from `run_log_review`; redirect output to a vault file `~/work-vault/.../daily-logs/{date}-log-audit.md`; update tests accordingly.
- Move the `daily-report-and-notify` block in `config/reflections.yaml` from `# --- Pipeline callables ---` to `# --- Auditing group ---` (cosmetic).
- Delete the 24 orphan `logs/reflections/report_2026-*.md` stubs (`rm logs/reflections/report_2026-*.md`).
- Verify `logs/reflections/` is no longer written by any code (`grep -rn "logs/reflections" --include='*.py' .`); remove gitignore entry if safe.

### 3. Write/update tests
- **Task ID**: test-per-project
- **Depends On**: build-consolidate, build-cleanup-legacy
- **Assigned To**: daily-report-test-eng
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/reflections/test_daily_report_per_project.py` per Test Impact (three projects: disabled, foreign-machine, owned-and-enabled → exactly one delivery).
- Create `tests/unit/reflections/test_daily_report_machine_gate.py` per Test Impact.
- Update `tests/integration/reflections/test_daily_report_integration.py` per REPLACE in Test Impact.
- Update `tests/unit/reflections/test_daily_report_aggregator.py` and `test_daily_report_renderer.py` per UPDATE in Test Impact.
- Run `pytest tests/unit/reflections/ tests/integration/reflections/ -x -q`.

### 4. Validate
- **Task ID**: validate-consolidation
- **Depends On**: test-per-project
- **Assigned To**: daily-report-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full reflections test suite and assert all pass.
- Manually trigger the consolidated callable in dry-run mode and inspect the output structure (`{status, projects: {slug: {...}}, summary}`).
- Check `curl -s localhost:8500/dashboard.json | jq '.reflections[] | select(.group == "audits")'` shows the consolidated reflection with per-project sub-rows.
- Read the most recent vault file `~/work-vault/.../daily-logs/{today}.md` and confirm per-project sections exist.
- Confirm zero entries in Telegram for non-owner machines (run a fake projects.json on a different machine name and assert zero outbox payloads enqueued).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-consolidation
- **Assigned To**: daily-report-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` per Documentation section.
- Add superseded note to `docs/plans/daily-log-overhaul.md`.
- Update `docs/features/single-machine-ownership.md` with new per-project enable flag.
- Update inline docstring on the consolidated `run()` callable.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: daily-report-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows below.
- Confirm all Success Criteria checkboxes can be ticked.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/reflections/ tests/integration/reflections/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/ ui/data/reflections.py config/reflections.yaml` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No orphan stubs | `ls logs/reflections/report_2026-*.md 2>/dev/null \| wc -l` | output is `0` |
| Reflection registered | `grep -c 'daily-report' config/reflections.yaml` | output > 0 |
| Per-project prefix | `grep '"daily-report"' ui/data/reflections.py` | exit code 0 |
| Branch A: legacy gone | `grep -c 'daily-log-review' config/reflections.yaml` | output is `0` |
| Branch A: legacy callable gone | `grep -c 'def run_log_review' reflections/auditing.py` | output is `0` |
| Plan superseded note | `grep -c 'superseded by' docs/plans/daily-log-overhaul.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

These are direction-defining decisions that must be made before `/do-build` runs. The issue explicitly required `/do-plan` not to pick silently.

### Q1: Consolidate to one reflection, or keep two with disjoint deliverables?

**Branch A — Consolidate (recommended).** One callable owns the daily activity story. `daily-log-review` is deleted; if its log-scan content (file sizes, error counts, regression markers) is valuable, it's folded INTO the per-project daily-report brief (one extra section in the audio + text). One Telegram delivery per project per day.

**Branch B — Keep two, but de-Telegram daily-log-review.** `daily-log-review` keeps its log-scan logic but writes findings to a vault file instead of posting to Telegram. `daily-report-and-notify` is the only daily Telegram delivery. Still one Telegram message per project per day, but two reflections continue to run.

**My recommendation:** Branch A. The log-scan content is diagnostic, not narrative — fits naturally as a "[ai] yesterday saw 3 errors in worker.log, max log file 12MB" line in the daily brief. Two reflections is more code surface for the same user-facing outcome.

### Q2: Slot-poll cadence (interval: 300 + per-project HH:MM) or true daily UTC (interval: 86400)?

**Slot-poll (recommended if going per-project).** Mirrors `pm-audio-briefing`. Each project gets a `daily_report.schedule: "20:00"` (or whatever) in its local timezone. The reflection runs every 5 minutes, picks projects whose local time matches the slot. Trade-off: more wake-ups, but per-project TZ awareness.

**True daily UTC.** Simpler. Runs once per UTC day at the configured time. All projects deliver at the same UTC moment regardless of their local time.

**My recommendation:** Slot-poll. If we're going per-project, project owners almost certainly want their daily summary at "end of my day" not "midnight UTC."

### Q3: Standalone module, or nest under `reflections/pm_audio_briefing/`?

**Nest (recommended if Q1=consolidate AND Q2=slot-poll).** Move `reflections/daily_report.py` to `reflections/pm_audio_briefing/daily_log.py`. Reuse `_resolve_machine()`, slot-poll helper, `pm_briefing.timezone` directly. One package owns "PM-facing audio content"; the morning brief and evening recap are siblings. Possibly rename the package to something more general like `pm_briefings/` (with morning + evening modules).

**Keep standalone.** `reflections/daily_report.py` stays at top level. Imports from `pm_audio_briefing.builder` (already does). Slight code duplication of `_resolve_machine()` and slot-helper, but cleaner separation.

**My recommendation:** Nest, with rename to `reflections/pm_briefings/`. The shared infrastructure is the whole point; living together makes refactors cheaper.

### Q4: Coordinate per-project enable flag schema with plan #1187?

Plan #1187 (`per-project-audit-reflections.md`, status `docs_complete`) generalizes five other audit reflections to per-project iteration. It hasn't shipped. If it picks a different convention (e.g. `audits.daily_log.enabled` vs this plan's `daily_report.enabled`), we end up with inconsistent project schema.

**Option A:** Pick `daily_report.enabled` now; #1187 follows. **Option B:** Defer this plan until #1187 ships and reuse its convention. **Option C:** Ship together as one larger PR.

**My recommendation:** Option A. This plan is shorter and the schema name is intuitive. #1187 can adopt the same shape.

### Q5: Skip-when-empty behavior — deliver "Nothing happened" or skip silently?

If a project had zero activity yesterday (no commits, no sessions, no telegram, no memories, no crashes, no reflection runs), should we still deliver an audio brief saying "Nothing of note happened yesterday for [project]," or skip delivery entirely?

`pm-audio-briefing` has a `pm_briefing.skip_when_empty: bool` toggle. We could add `daily_report.skip_when_empty: bool` (default `true`).

**My recommendation:** Add the toggle, default `true` (skip). Empty days are usually weekends/vacations; an audio brief saying "nothing happened" is noise.
