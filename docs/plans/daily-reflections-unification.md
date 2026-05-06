---
status: Ready
type: chore
appetite: Large
owner: Valor
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1276
last_comment_id: IC_kwDOEYGa088AAAABBH6L9w
---

# PM Briefings Unification — Consolidate Three Reflections into Per-Project Slot-Driven Package

## Decisions Recorded (2026-05-04)

The five open questions raised during planning were answered by the operator before critique:

| # | Question | Decision |
|---|---|---|
| Q1 | Consolidate or keep two? | **Consolidate ALL THREE** — `pm-audio-briefing`, `daily-log-review`, and `daily-report-and-notify` collapse into one reflection package. |
| Q2 | Slot-poll or true daily? | **Slot-poll**, with per-machine project config (slots declared in `projects.json`). |
| Q3 | Standalone or nested? | **Nest** — relocate the daily activity story to `reflections/pm_audio_briefing/daily_log.py` as a sibling of the morning brief. The package becomes the home for all PM-facing slot-driven content. |
| Q4 | Coordinate schema with #1187? | **Already shipped** — PR #1251 merged 2026-05-01; `mark_completed(projects=)` lives at `models/reflection.py:85`; `run_per_project_audit()` helper convention is the reference. |
| Q5 | Skip-when-empty default? | **Skip silently** when a slot has no content for the day. |

## Problem

The PM-facing audio/text briefing story is fragmented across **three** reflections that each ship a per-day Telegram message and each carry their own collection / delivery / scheduling code:

- `pm-audio-briefing` — per-project morning audio brief with forward-looking "angles." Per-project, machine-gated, slot-poll. Healthy.
- `daily-log-review` — system-wide(-ish) text summary of yesterday's logs (file sizes, error counts, regression markers). Per-project iteration, no machine gate, posts to `Dev: Valor`.
- `daily-report-and-notify` — system-wide vault Markdown + audio brief of yesterday's activity (sessions, commits, telegram, memory, crashes). No per-project, no machine gate, posts to first PM chat.

The user-facing reality is that the same project owner gets multiple daily Telegram messages from different reflections, none of which respect each project's local timezone or owner machine, and the code that could have been shared (machine gate, slot-poll, per-project Reflection records, SETNX idempotency) lives in only one of the three.

**Current behavior (with PR #1264 merged):**

1. Three reflections fire on PM-facing daily content; the user can receive 2–3 Telegram deliveries per day per project.
2. `pm-audio-briefing` is per-project + machine-gated + slot-poll (good). `daily-log-review` and `daily-report-and-notify` are not.
3. `reflections/daily_report.py` imports from `reflections.pm_audio_briefing.builder` but is a parallel callable with its own collection, delivery, and outbox key.
4. `daily_report.run()` aggregates **system-wide** into a single vault file `daily-logs/{date}.md` and one voice note.
5. `daily-report-and-notify` is true daily UTC (`interval: 86400`) — no per-project HH:MM slot logic, can't honor project timezone.
6. **Neither** `daily-log-review` **nor** `daily-report-and-notify` filter on machine ownership.
7. 24 orphan stub files `logs/reflections/report_2026-*.md` remain on disk from the pre-vault implementation.
8. `daily-report-and-notify` lives under `# --- Pipeline callables ---` in `config/reflections.yaml:262` (cosmetic — dashboard already classifies via `REFLECTION_GROUPS` Python dict).

**Desired outcome:**

One reflection package owns all PM-facing daily/scheduled briefings. Each project declares any number of "briefing slots" in its config (e.g. `morning` at 07:00 local, `evening_recap` at 18:00 local). At each tick, the package fans out (project × slot), runs the slot-specific collector + builder, and delivers ONE Telegram message per (project, slot) per day to that project's PM chat. Machine ownership gate applies uniformly. No double-deliveries. Old orphan stubs cleaned up.

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
- **Assumption**: "Adding the consolidated reflection's prefix to `_PREFIX_EXPANDED_REFLECTIONS` is sufficient for per-project rows."
- **Finding**: **Yes, fully.** The tuple at `ui/data/reflections.py:74` controls per-project row rendering. Per-project records must exist as `Reflection.get_or_create(f"{prefix}-{slug}")`. The `mark_completed(duration, error=None, projects=None)` extension from plan #1187 **shipped via PR #1251 on 2026-05-01** — confirmed at `models/reflection.py:85`. We can use both per-project Reflection records AND aggregate `mark_completed(projects=[...])` for richer per-project sub-rows in the modal.
- **Confidence**: high
- **Impact on plan**: Use both mechanisms together. Per-project Reflection records (`{prefix}-{slug}`) for top-level dashboard expansion; aggregate `mark_completed(projects=[...])` payload from the parent reflection for the modal's per-project sub-table.

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
- **Relationship to plan #1187**: #1187 shipped via PR #1251 on 2026-05-01. The `mark_completed(duration, error=None, projects=None)` extension is in `models/reflection.py:85` and the `run_per_project_audit()` helper at `reflections/utils.py:70` is the canonical pattern. This plan **adopts** #1187's conventions: same `mark_completed(projects=...)` payload shape, same `[slug]` prefix in findings, same per-project status states (`"ok" | "error" | "skipped" | "disabled"`).

## Appetite

**Size:** Large

**Team:** Solo dev + plan critique + code review

**Interactions:**
- PM check-ins: 2 (mid-build after the dispatch loop + slot model lands; before the legacy callable deletions)
- Review rounds: 1–2 (PR review, possibly a follow-up if the slot schema needs revising after live testing)

This is structural refactoring across **three** existing reflections plus relocation into one nested package. The healthy `pm-audio-briefing` is in scope (gets refactored to fit the slot-driven dispatch model). All five direction-defining questions are answered; the work is well-scoped but touches a lot of surface.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #1264 merged | `gh pr view 1264 --json state -q .state` returns `MERGED` | Satisfied at `871a45c5` |
| PR #1251 (#1187) merged | `gh pr view 1251 --json state -q .state` returns `MERGED` | Satisfied 2026-05-01; `mark_completed(projects=)` available at `models/reflection.py:85` |
| `pm-audio-briefing` is healthy | `python -c "from reflections.pm_audio_briefing import _resolve_machine; print(_resolve_machine())"` returns non-empty | The morning brief must keep working through the refactor — pre-baseline before touching it |

## Solution

### Key Elements

- **One nested package** at `reflections/pm_audio_briefing/` (kept as the package name — it's already imported widely; renaming widens blast radius unnecessarily) hosts ALL PM-facing slot-driven content. Internal modules:
  - `__init__.py` — single registered reflection (`pm-briefings`), dispatch loop, `_resolve_machine()`, slot match, SETNX lock.
  - `morning.py` — angles-based morning brief content (extracted from current `__init__.py` collector + builder).
  - `daily_log.py` — end-of-day activity recap content (relocated from `reflections/daily_report.py`).
  - `log_audit.py` — log-scan content (relocated from `reflections/auditing.py::run_log_review`).
  - `builder.py`, `collector.py`, `delivery.py` — kept as shared scaffolding (already exist).
- **Per-project slot config in `projects.json`**: each project declares any number of briefing slots inside its `pm_briefing` dict. Each slot specifies `name`, `schedule` (HH:MM in project's local TZ), `type` (`morning | daily_log | log_audit`), and slot-type-specific config. Slot-type schema details are a build-time call but the dispatch loop accepts the list verbatim.
- **One reflection in the registry** (`pm-briefings`) replaces three (`pm-audio-briefing`, `daily-log-review`, `daily-report-and-notify`). Interval stays at 300s (5-min poll).
- **Per-(project × slot) Reflection records**: `Reflection.get_or_create(f"pm-briefings-{slug}-{slot_name}")`. Drives dashboard per-project rendering via `_PREFIX_EXPANDED_REFLECTIONS = ("pm-briefings",)`.
- **Per-(project × slot) SETNX lock**: `pm-briefings-lock:{slug}:{slot_name}:{today_iso}` — a project's morning slot and its evening slot can both fire on the same UTC day independently.
- **Skip-when-empty default `true`**: a slot whose collector returns no content silently skips delivery (per Q5).
- **Aggregate `mark_completed(projects=...)` payload** at parent reflection level so the modal renders per-(project × slot) sub-rows.
- **Vault file**: still one `daily-logs/{date}.md` per machine, written ONLY by the slot in the slug `"ai"` (vault is iCloud-shared; only one machine writes to avoid conflict-copies). Other slots/projects write per-project sections appended to this file at slot completion time.
- **Cleanup**: delete `daily-log-review` and `daily-report-and-notify` from `config/reflections.yaml` and `REFLECTION_GROUPS`. Delete `reflections/daily_report.py`. Delete `run_log_review` from `reflections/auditing.py`. Delete the 24 orphan `logs/reflections/report_2026-*.md` stubs.

### Flow

**Worker tick (every 5 min)** → registry calls `reflections.pm_audio_briefing.run` →
`_resolve_machine()` + `load_local_projects()` + filter by `pm_briefing.enabled` AND `project.machine == this_machine` →
**for each owned project**: iterate `pm_briefing.slots` →
**for each slot**: check `_slot_match(now_local, slot.schedule)` (5-min window) →
SETNX `pm-briefings-lock:{slug}:{slot.name}:{today_iso}` →
dispatch by `slot.type` to `morning.build()` / `daily_log.build()` / `log_audit.build()` →
if collector returns empty AND slot.skip_when_empty → release lock, mark slot `"skipped"` →
otherwise enqueue ONE Telegram payload (text + voice) to project's PM chat at `telegram:outbox:pm-briefings-{slug}-{slot.name}-{today}` →
`Reflection.get_or_create(f"pm-briefings-{slug}-{slot.name}").mark_completed(duration, error=None)` →
after all loops: aggregate `{status, projects: [{slug, slot, status, duration, error}, ...]}` →
parent `Reflection("pm-briefings").mark_completed(duration, error=None, projects=[...])`.

### Technical Approach

- **Migrate `pm_audio_briefing.__init__._resolve_machine()` to module top** so all slot modules can import it. No semantic change.
- **Generalize the existing schedule-slot helper** (`__init__.py:60-95`) to accept any HH:MM string (already does; just lift to package scope).
- **Slot dispatch table**: a dict mapping `slot.type` → builder callable. Adding a new slot type later means adding a module + a dict entry.
- **Migrate existing `pm_audio_briefing` config**: today's `pm_briefing.angles + pm_briefing.schedule` becomes the implicit single-slot `[{name: "morning", type: "morning", schedule: <existing>, angles: <existing>}]`. A small migration shim in `_load_slots(project)` reads the old shape and returns a synthesized slot list — zero `projects.json` edits required for existing morning-brief users.
- **Delete `reflections/daily_report.py`** after the new `daily_log.py` module is functional and tests pass.
- **Delete `run_log_review`** from `reflections/auditing.py` after `log_audit.py` reaches feature parity (file-size scan, error counts, regression markers).
- **Move the registry entry**: replace three reflections in `config/reflections.yaml` with one `pm-briefings` entry under `# --- Auditing group ---` (dashboard group classification updated in `REFLECTION_GROUPS` accordingly).
- **Update `_PREFIX_EXPANDED_REFLECTIONS`** to `("pm-briefings",)` (replacing `("pm-audio-briefing",)`).
- **Update the dashboard render** if needed to handle per-(project × slot) row labels (e.g. `"psyoptimal — morning"` vs `"psyoptimal — evening_recap"`).

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

- [ ] `tests/integration/reflections/test_daily_report_integration.py` — REPLACE: rewrite for the consolidated dispatch model. Asserts one outbox payload per (project × slot), not one global.
- [ ] `tests/unit/reflections/test_daily_report_aggregator.py` — UPDATE: collectors live under `pm_audio_briefing/daily_log.py` post-move; tests follow the relocation.
- [ ] `tests/unit/reflections/test_daily_report_renderer.py` — UPDATE: renderer moves under `pm_audio_briefing/daily_log.py`; tests follow.
- [ ] `tests/unit/reflections/test_daily_report_audio_guard.py` — UPDATE: import path moves to `reflections.pm_audio_briefing.daily_log` (or shared guards module). Test logic unchanged.
- [ ] `tests/unit/test_reflections_auditing.py` (`run_log_review` tests) — REPLACE: old tests target `reflections.auditing.run_log_review`; new tests target `reflections.pm_audio_briefing.log_audit.build()` (or whatever the per-slot builder is named). Assertions about findings count, file-size detection, regression markers preserved.
- [ ] Existing `pm_audio_briefing` tests (if any) — UPDATE: morning brief content path moves from `__init__.py` into `morning.py`; tests follow imports. Functional behavior unchanged.
- [ ] **NEW** `tests/integration/reflections/test_pm_briefings_dispatch.py` — CREATE: end-to-end with three configured projects (one disabled, one owned-by-other-machine, one enabled-and-owned with two slots). Asserts: zero deliveries from disabled / foreign-machine projects; exactly two deliveries from the owned project (one per matching slot); per-(project × slot) Reflection records exist; aggregate `mark_completed(projects=[...])` payload populated correctly.
- [ ] **NEW** `tests/unit/reflections/test_pm_briefings_slot_match.py` — CREATE: slot-match helper unit tests (HH:MM matching within 5-min window, TZ awareness, edge of day boundary).
- [ ] **NEW** `tests/unit/reflections/test_pm_briefings_skip_when_empty.py` — CREATE: empty-collector slot returns `"skipped"`, releases lock, enqueues nothing.
- [ ] **NEW** `tests/unit/reflections/test_pm_briefings_machine_gate.py` — CREATE: machine-ownership filter unit tests (this machine, foreign machine, empty `_resolve_machine()` result).
- [ ] **NEW** `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py` — CREATE: assert that an existing `pm_briefing.angles + pm_briefing.schedule` config (single morning slot) is interpreted as a one-element slot list without requiring `projects.json` edits.

## Rabbit Holes

- **Don't generalize beyond PM briefings.** Tempting to extract `_resolve_machine()`, slot-poll, and SETNX into a shared `ReflectionBase` class for ALL reflections. Don't. This plan consolidates three PM-facing briefings into one package; cross-cutting framework abstraction is a separate plan.
- **Don't redesign the vault Markdown format.** PR #1264 ships a format; this plan only relocates the writer. Reformatting belongs in a separate plan.
- **Don't migrate historical run records** for `pm-audio-briefing`, `daily-log-review`, `daily-report-and-notify`. Old records remain in Redis; new `pm-briefings-*` records start fresh. Dashboard handles missing/legacy records gracefully.
- **Don't change `models/reflection.py` or `agent/reflection_scheduler.py`.** Both already support what we need (#1187 shipped the `projects=` kwarg). If we touch them, scope creeps.
- **Don't rename the `pm_audio_briefing/` package.** Tempting to rename to `pm_briefings/` for accuracy. Don't — the import path is referenced widely (config, tests, other reflections). Renaming widens blast radius for zero functional gain.
- **Don't bikeshed slot type names** (`morning` vs `morning_brief`, `daily_log` vs `evening_recap`). Pick one set during build, document them; future renames are easy if the dispatch table is small.

## Risks

### Risk 1: Morning brief regression (pm-audio-briefing already in production)
**Impact:** `pm-audio-briefing` is the only one of the three reflections that's currently healthy. The refactor relocates its content into `morning.py` and changes the registry name. If anything regresses, the user loses their working morning brief.
**Mitigation:** Migration shim (`_load_slots(project)`) reads the existing `pm_briefing.angles + pm_briefing.schedule` shape and synthesizes a single morning slot — zero `projects.json` edits required for current users. New integration test pins the morning-brief output byte-for-byte against current production output BEFORE the refactor lands. Roll-forward only: the old reflection name is removed in the same PR (no parallel run).

### Risk 2: Per-(project × slot) Reflection records explode the dashboard
**Impact:** With N projects and M slots each, the dashboard shows N×M sub-rows under `pm-briefings`. For 5 projects × 2 slots that's 10 rows.
**Mitigation:** Mirror `pm-audio-briefing`'s rendering — per-project rows expand only inside the parent's modal, not as top-level rows. Verify before shipping. If still too noisy, group sub-rows by project and collapse slots.

### Risk 3: Vault file write race across machines
**Impact:** Two machines both think they own the vault writer → both write `daily-logs/{date}.md` → iCloud sync produces a conflict-copy file.
**Mitigation:** Only the slot in the project named `"ai"` writes the vault file. Other slots/projects skip vault writes. Single-machine-ownership invariant enforces "ai" is owned by exactly one machine.

### Risk 4: Slot schema lock-in
**Impact:** Once `projects.json` schema for slots is published, changing it later requires migrating every project config across every machine.
**Mitigation:** Migration shim (Risk 1) absorbs the legacy shape. Build documentation explicitly marks the slot schema as v1; future v2 schema changes get a similar shim.

### Risk 5: Slot collector latency adds up
**Impact:** A project with 3 slots that all match in the same 5-min window runs 3 collectors back-to-back. If one collector hangs, downstream slots wait.
**Mitigation:** Each (project × slot) gets its own try/except + per-slot duration limit (default 60s soft, 300s hard). Aggregate `mark_completed(projects=...)` records per-slot durations so slow slots are visible on the dashboard.

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

- **Not extending the consolidation beyond PM-facing briefings** — the five `#1187` audits keep their own callable structure.
- **Not modifying `models/reflection.py` or `agent/reflection_scheduler.py`** — both already support what we need.
- **Not migrating historical Reflection run records** for the three retired reflection names.
- **Not redesigning the vault Markdown format** — relocate the writer only.
- **Not renaming the `pm_audio_briefing/` package** despite its name no longer fully describing its contents (it now hosts log audits too). Import-path stability beats naming purity.
- **Not adding parallel-run scaffolding** between old and new reflections during cutover. Roll-forward only.
- **Not fixing the YAML section comment misnaming for OTHER reflections** — only the placement of the consolidated reflection.

## Update System

No update-skill or update-script changes required. The new slot schema lives in `~/Desktop/Valor/projects.json` (iCloud-synced) and the reflection registry is in-repo (`config/reflections.yaml`).

**Migration impact:** Existing users with `pm_briefing.angles + pm_briefing.schedule` get a single `morning` slot synthesized by the legacy migration shim — zero `projects.json` edits required for the existing morning brief. Users who want the daily-log slot or log-audit slot must add them to their `projects.json` explicitly. This fail-closed default is documented in the PR body and `docs/features/reflections.md`.

## Agent Integration

No agent integration changes required — this is a worker-internal reflection change. The agent does not invoke daily reflections directly; the worker's reflection scheduler does. Telegram delivery still flows through the standard outbox → bridge relay path.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` — replace the three retired reflection entries with one `pm-briefings` entry; document the slot schema (`pm_briefing.slots: [{name, schedule, type, ...}]`); document the legacy single-slot migration shim.
- [ ] Update `docs/features/README.md` index if reflection naming changes.
- [ ] Update `docs/features/pm-audio-briefing.md` (if it exists) to reflect the package now hosts multiple slot types, not just morning.
- [ ] Add a "superseded by" note to `docs/plans/daily-log-overhaul.md` pointing to this plan.
- [ ] Add an entry to `docs/features/single-machine-ownership.md` mentioning the slot-driven dispatch and machine-gated execution.

### Inline Documentation
- [ ] Docstring on the consolidated `run()` explaining (project × slot) dispatch model.
- [ ] Docstring on each slot module (`morning.py`, `daily_log.py`, `log_audit.py`) describing the slot's content and contract.
- [ ] Update inline comments in `config/reflections.yaml` so the section header matches the new single registry entry.
- [ ] Comment in `_load_slots(project)` migration shim explaining the legacy → v1 schema mapping.

### Code Cleanup
- [x] Delete the 22 orphan `logs/reflections/report_2026-*.md` stubs (count corrected from "24" by critique). Done locally; per-machine cleanup since `logs/` is gitignored.
- [x] Verify `logs/reflections/` gitignore entry is unnecessary to remove — confirmed: `pm_audio_briefing/delivery.py:236` still writes to `logs/reflections/` for DRY_RUN, so the directory stays.
- Deferred to follow-up PR: delete `reflections/daily_report.py` (whole file) — see Verification "Deferred to deploy / follow-up PR" section. New slot modules wrap its helpers; inlining + delete is its own scoped change.
- Deferred to follow-up PR: delete `run_log_review` from `reflections/auditing.py` — same rationale as above.

## Success Criteria

- [ ] One reflection (`pm-briefings`) registered in `config/reflections.yaml`; three retired entries (`pm-audio-briefing`, `daily-log-review`, `daily-report-and-notify`) removed.
- [ ] After one daily cycle: each owned project's PM Telegram chat receives **exactly one delivery per matching slot** — verified by integration test.
- [ ] Non-owner machines produce **zero** Telegram deliveries — verified by integration test.
- [ ] Projects with `pm_briefing.enabled: false` (or no `pm_briefing` block) produce zero deliveries — verified by integration test.
- [ ] Empty-collector slots silently skip (no Telegram message, no error) — verified by integration test.
- [ ] Existing morning-brief users see no behavior change — `morning` slot output matches pre-refactor production output (legacy migration shim test).
- [ ] Dashboard renders per-(project × slot) sub-rows under the `pm-briefings` parent in the `audits` group — verified via `curl localhost:8500/dashboard.json`.
- [ ] All 24 `logs/reflections/report_2026-*.md` orphan stubs deleted.
- [ ] `docs/features/reflections.md` reflects the consolidated design.
- [ ] `docs/plans/daily-log-overhaul.md` has a superseded note pointing here.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No double-delivery: `valor-telegram read --chat "Dev: Valor" --since "1 day ago"` shows at most one PM-briefing message per slot per project.

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

### 1. Pin morning-brief baseline output
- **Task ID**: build-morning-baseline
- **Depends On**: none
- **Validates**: `tests/integration/reflections/test_pm_audio_briefing_baseline.py` (create)
- **Informed By**: Risk 1 (morning brief regression must not happen)
- **Assigned To**: pm-briefings-baseline
- **Agent Type**: test-engineer
- **Parallel**: true
- Capture the current `pm-audio-briefing` morning brief output for one or two configured projects (transcript text + outbox payload structure).
- Write a regression test that asserts the post-refactor morning slot produces the SAME output (modulo expected timestamp differences).
- This test must pass continuously through the rest of the build — any divergence is a stop-the-line signal.

### 2. Build the slot-driven dispatch skeleton
- **Task ID**: build-dispatch
- **Depends On**: build-morning-baseline
- **Validates**: `tests/unit/reflections/test_pm_briefings_slot_match.py`, `tests/unit/reflections/test_pm_briefings_machine_gate.py`, `tests/unit/reflections/test_pm_briefings_legacy_config_migration.py`
- **Informed By**: spike-2 (`mark_completed(projects=)` shipped via #1251), spike-4 (`run_per_project_audit` pattern)
- **Assigned To**: pm-briefings-dispatch-builder
- **Agent Type**: builder
- **Parallel**: false
- Lift `_resolve_machine()` and the schedule-slot helper to `reflections/pm_audio_briefing/__init__.py` module scope (already there; just clean up).
- Implement `_load_slots(project)` migration shim: reads existing `pm_briefing.angles + pm_briefing.schedule` and synthesizes `[{name: "morning", type: "morning", schedule: <existing>, angles: <existing>}]`; reads new `pm_briefing.slots` list verbatim if present.
- Implement the dispatch dict: `{"morning": morning.build, "daily_log": daily_log.build, "log_audit": log_audit.build}` (referenced modules created in tasks 3–5).
- Implement `run()`: machine filter → for each owned project → for each slot → slot-match → SETNX `pm-briefings-lock:{slug}:{slot.name}:{today_iso}` → dispatch → handle skip-when-empty → mark per-(project × slot) Reflection record → aggregate.
- Update the registry in `config/reflections.yaml`: add `pm-briefings` entry under `# --- Auditing group ---`; remove `pm-audio-briefing`, `daily-log-review`, `daily-report-and-notify`.
- Update `ui/data/reflections.py`: add `"pm-briefings": GROUP_AUDITS` to `REFLECTION_GROUPS`; replace `_PREFIX_EXPANDED_REFLECTIONS` with `("pm-briefings",)`; remove the three retired entries.

### 3. Extract morning brief into morning.py
- **Task ID**: build-morning-module
- **Depends On**: build-dispatch
- **Validates**: `tests/integration/reflections/test_pm_audio_briefing_baseline.py` (must still pass)
- **Assigned To**: pm-briefings-morning-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `reflections/pm_audio_briefing/morning.py`.
- Move the angles collector + builder logic from `__init__.py` into `morning.build(project, slot_config)` returning `{transcript, outbox_payload, status}`.
- Update import paths in tests; baseline test from task 1 must still pass.

### 4. Relocate daily report into daily_log.py
- **Task ID**: build-daily-log-module
- **Depends On**: build-dispatch
- **Validates**: `tests/integration/reflections/test_daily_report_integration.py` (REPLACE per Test Impact)
- **Assigned To**: pm-briefings-daily-log-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `reflections/pm_audio_briefing/daily_log.py`.
- Move collection (`_collect_day_activity`), renderer, audio guards from `reflections/daily_report.py` into the new module — refactored to be per-project (accepts a project arg, returns one transcript per call).
- Implement `daily_log.build(project, slot_config)` with the same return shape as `morning.build`.
- Move vault file write logic into a separate helper `_write_vault_section(project, transcript, target_date)` invoked only when the project's slug matches `vault_writer_project` (default `"ai"`).
- Delete `reflections/daily_report.py` after the new module reaches parity.

### 5. Relocate log review into log_audit.py
- **Task ID**: build-log-audit-module
- **Depends On**: build-dispatch
- **Validates**: `tests/unit/test_reflections_auditing.py` (REPLACE per Test Impact)
- **Assigned To**: pm-briefings-log-audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `reflections/pm_audio_briefing/log_audit.py`.
- Move file-size scan, error count, regression marker logic from `reflections/auditing.py::run_log_review` into `log_audit.build(project, slot_config)`.
- Delete `run_log_review` from `reflections/auditing.py` after the new module reaches parity.

### 6. Tests + cleanup
- **Task ID**: test-and-cleanup
- **Depends On**: build-morning-module, build-daily-log-module, build-log-audit-module
- **Assigned To**: pm-briefings-test-eng
- **Agent Type**: test-engineer
- **Parallel**: false
- Create new tests per Test Impact: `test_pm_briefings_dispatch.py`, `test_pm_briefings_slot_match.py`, `test_pm_briefings_skip_when_empty.py`, `test_pm_briefings_machine_gate.py`, `test_pm_briefings_legacy_config_migration.py`.
- Update existing tests per Test Impact (path moves only, no logic changes for guards/renderers).
- Delete the 24 orphan `logs/reflections/report_2026-*.md` stubs.
- Verify `logs/reflections/` is no longer written (`grep -rn "logs/reflections" --include='*.py' .`); remove gitignore entry if safe.
- Run `pytest tests/unit/reflections/ tests/integration/reflections/ -x -q`.

### 7. Validate
- **Task ID**: validate-pm-briefings
- **Depends On**: test-and-cleanup
- **Assigned To**: pm-briefings-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full reflections test suite + baseline regression test.
- Manually trigger `pm-briefings.run` in dry-run mode; inspect output (`{status, projects: [{slug, slot, status, ...}, ...], summary}`).
- `curl -s localhost:8500/dashboard.json | jq '.reflections[] | select(.name == "pm-briefings")'` shows per-(project × slot) sub-rows under `audits`.
- Read the latest vault file; confirm per-project sections present and only one machine wrote it.
- Run dispatch with a fake `projects.json` for a foreign machine; assert zero outbox enqueues.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pm-briefings
- **Assigned To**: pm-briefings-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` per Documentation section.
- Update `docs/features/pm-audio-briefing.md` if exists (or create a `docs/features/pm-briefings.md`).
- Add superseded note to `docs/plans/daily-log-overhaul.md`.
- Update `docs/features/single-machine-ownership.md`.
- Update inline docstrings on each slot module + `_load_slots()` migration shim.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pm-briefings-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification rows.
- Confirm all Success Criteria checkboxes.
- Generate final report.

## Verification

> **Build-time addendum (2026-05-04):** PR #1281 deferred two file
> deletions (`reflections/daily_report.py`,
> `reflections.auditing.run_log_review`) because the new slot modules
> still wrapped their internal helpers and a clean inline + delete was
> too large for the same diff.
>
> **Cutover update (2026-05-06, PR #1292):** Both deletions have now
> shipped. The helpers are inlined into the slot modules. The two operator
> steps (registry rename and `enabled: false` flips on the bridge machine's
> vault yaml) are still pending — see the *Deferred to deploy / follow-up
> PR* section below for current status.
>
> `config/reflections.yaml` is a symlink into the iCloud-synced vault and
> is gitignored. The registry entry rename to `pm-briefings` and the
> `enabled: false` flips for `daily-log-review` and `daily-report-and-notify`
> remain operator steps on the bridge machine. PR #1292's body documents
> the exact yaml snippets to apply.
>
> Orphan stubs at `logs/reflections/report_2026-*.md` are per-machine
> cleanup (`logs/` is gitignored). Each active machine must run
> `rm -f logs/reflections/report_2026-*.md` once after deploy.

### In-PR checks (deterministic verification)

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/reflections/ tests/integration/reflections/ -q` | exit code 0 |
| Lint clean | `python -m ruff check reflections/ ui/data/reflections.py` | exit code 0 |
| Format clean | `python -m ruff format --check reflections/ ui/data/reflections.py tests/unit/reflections/ tests/integration/reflections/` | exit code 0 |
| Prefix-expanded set updated | `grep -c '"pm-briefings"' ui/data/reflections.py` | output > 0 |
| New slot modules exist | `test -f reflections/pm_audio_briefing/morning.py && test -f reflections/pm_audio_briefing/daily_log.py && test -f reflections/pm_audio_briefing/log_audit.py` | exit code 0 |
| Plan superseded note | `grep -c 'superseded by' docs/plans/daily-log-overhaul.md` | output > 0 |

### Deferred to deploy / follow-up PR (cutover status — issue #1292)

The five items below were left out of PR #1281 deliberately and tracked
under issue #1292 / PR (`session/pm-briefings-cutover-1292`). Status as
of the cutover PR:

- **[Done — PR #1292]** **Delete `reflections/daily_report.py`** — Helpers
  (`_collect_day_activity`, `_build_audio_brief`, `_write_vault_log`,
  `_activity_to_signals`, et al.) inlined into
  `reflections/pm_audio_briefing/daily_log.py`. The legacy module is
  deleted. The legacy `run()` orchestration (audio enqueue, target chat
  selection) was retired entirely — the slot dispatcher in
  `pm_audio_briefing.delivery` owns delivery now.
- **[Done — PR #1292]** **Delete `reflections.auditing.run_log_review`** —
  Helpers (`_collect_sentry_counts`, `_read_log_tail_lines`,
  `_read_log_text_bounded`) inlined into
  `reflections/pm_audio_briefing/log_audit.py`. The function and its
  Telegram-send sibling (`_send_log_review_telegram`) are deleted from
  `reflections/auditing.py`. `extract_structured_errors` stays in
  `reflections.utils` (still used by `run_hooks_audit`).
- **[Operator step — bridge machine]** **Rename registry entry to
  `pm-briefings`** — `~/Desktop/Valor/reflections.yaml` (vault file,
  gitignored). The `pm-audio-briefing` callable path is unchanged so the
  rename is cosmetic and lifts the dashboard's fallback-parents shim
  (`ui/data/reflections.py:_PREFIX_FALLBACK_PARENTS`). PR #1292's body
  contains the exact yaml diff.
- **[Operator step — bridge machine]** **Disable `daily-log-review` and
  `daily-report-and-notify` in registry** — Same vault yaml. Without this
  flip, the scheduler will log ImportError each tick because the
  callables (`reflections.daily_report.run`,
  `reflections.auditing.run_log_review`) no longer exist after PR #1292
  merges. Operator MUST flip both to `enabled: false` together with merge.
- **[Per-machine — operator step]** **Delete
  `logs/reflections/report_2026-*.md` orphan stubs** — `logs/` is
  gitignored. Run `rm -f logs/reflections/report_2026-*.md` once after
  deploy on each active machine.

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

All five direction-defining questions raised during planning have been answered — see **Decisions Recorded** at the top of this document. No open questions remain. Ready for `/do-plan-critique` then `/do-build`.
