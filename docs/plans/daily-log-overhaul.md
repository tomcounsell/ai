---
status: docs_complete
type: feature
appetite: Medium
owner: Valor
created: 2026-05-03
tracking: https://github.com/tomcounsell/ai/issues/1263
last_comment_id:
---

# Daily Log Overhaul: Comprehensive Activity Log + Vault Archival + Audio-Brief Telegram

> **Superseded by [`docs/plans/daily-reflections-unification.md`](daily-reflections-unification.md) (issue #1276).**
> The follow-up plan consolidates this work alongside `pm-audio-briefing`
> and `daily-log-review` into a slot-driven `pm-briefings` package. The
> daily-log content collector + audio-brief logic from this plan now lives
> in `reflections.pm_audio_briefing.daily_log` as a slot type. Vault file
> writing is gated by `slot.vault_writer: true` (was: implicit "ai project"
> gate).

## Problem

The current `daily-report` reflection produces a near-empty file (`logs/reflections/report_YYYY-MM-DD.md`) containing only reflection-callable durations and a "Principal Priorities" block. It records nothing about what actually happened across the system that day. Two queries are unanswerable today:

- **"What day did X happen?"** — substantive events aren't recorded, so full-text search across `logs/reflections/*.md` returns nothing.
- **"What happened on day X?"** — opening `report_2026-04-22.md` shows reflection durations, not the day's work.

Two structural defects compound the gap:

1. The report file lives in `logs/reflections/`, which is gitignored, machine-local, and not ingested by the knowledge indexer. Other machines and the agent itself can never find it.
2. The Telegram delivery is a plaintext stats line (`Reflections Report — {date}\nProject: {name}\nFindings: {N}`) — metadata about the report, not yesterday's substance.

**Current behavior:** `_collect_reflection_findings()` scans only the Reflection model. `_post_to_telegram()` ships stats text directly via Telethon (bypassing the bridge relay). The reflection is currently `enabled: false` in `config/reflections.yaml`.

**Desired outcome:**
- `~/work-vault/AI Valor Engels System/daily-logs/YYYY-MM-DD.md` lands each day with substantive entries — commits, PRs, issues, sessions, decision-bearing Telegram threads, memories, errors — using full named entities so search finds them.
- The Telegram delivery becomes a `.ogg` voice note constructed via the `/do-debrief` pattern (decisions / heads-up / FYIs, ~70 spoken words, no PR/issue numbers in audio). No text preface.
- The reflection is enabled and runs daily.

## Freshness Check

**Baseline commit:** `3706cbb4b522c51d1c1472bb66163ecdd5cd3f74`
**Issue filed at:** `2026-05-03T11:03:42Z`
**Plan written at:** `2026-05-03T11:16:39Z` (≈13 min after issue filed)
**Disposition:** Unchanged

The issue was filed within the same hour as plan creation. No commits have landed on main since (HEAD unchanged). All file:line references in the issue body and Recon Summary were re-verified against current main:

- `reflections/daily_report.py:24,27,114,163` — present, signatures match issue claims
- `reflections/pm_audio_briefing/builder.py:52,58,256` — present
- `reflections/pm_audio_briefing/delivery.py:108` — present
- `tools/tts/__init__.py:360` — present
- `config/reflections.yaml:280` — `daily-report-and-notify`, `enabled: false`, present

**Active plans overlapping this area:** None. `docs/plans/sdlc-1247.md` and `docs/plans/per-project-audit-reflections.md` touch reflections but in unrelated subsystems (per-project audit, monolith decomposition).

## Recon Summary

Two parallel Explore agents reconciled into four buckets. Full source-of-truth file:line references at the end.

### Confirmed (include in plan as-is)

- **Primary entry point:** `reflections/daily_report.py` — `_collect_reflection_findings()` (L27–61) and `_post_to_telegram()` (L163–214). Writes to `logs/reflections/report_{date}.md` (L24, L114–115).
- **Telegram entry currently uses Telethon directly** (L163–214) reading `data/valor.session` — NOT through `bridge/telegram_relay.py`. Audio delivery will require switching to the Redis outbox enqueue pattern proven in `reflections/pm_audio_briefing/delivery.py` (L108–116, RPUSH to `telegram:outbox:{session_id}`).
- **Audio brief template exists:** `reflections/pm_audio_briefing/builder.py` two-pass builder (Pass A LLM, Pass B word-count cut). Layer 2 regex `_NUMBERS_PREFIXED_RE` (L52) catches "issue 1197 / pr-363 / #1197"; Layer 3 `_NUMBERS_BARE_RE` (L58–60) catches bare 3+ digit integers, exempting "users / requests / lines / ms / seconds / %". Public API: `build(raw_signals, fallback_message, skip_when_empty, project=None) -> tuple[str, str]`.
- **TTS API:** `tools.tts.synthesize(text, output_path, voice="default", format="opus", force_cloud=False) -> dict` returning `{path, duration, backend, voice, format, error}` (`tools/tts/__init__.py:360`).
- **Reflection registry entry:** `daily-report-and-notify` in `config/reflections.yaml:280–286` is currently `enabled: false`. `daily-log-review` (L196–202) is a separate entry that stays untouched.
- **Existing voice-note payload shape** in `pm_audio_briefing/delivery.py:77–94`: `{voice_note: True, file_paths: [audio_path], cleanup_file: True}`. Reusable as-is.
- **Vault auto-indexing:** `tools/valor_ingest.py` confirms `.md` files in the work vault are picked up automatically by the knowledge indexer — no extra step needed once the file lands.
- **Crash tracker query API:** `monitoring/crash_tracker.py:136–166` — `get_recent_crashes(window_seconds=86400)` returns `list[CrashEvent]` with `timestamp, event_type, commit_sha`.
- **Per-project git aggregator pattern exists:** `reflections/pm_audio_briefing/collector.py:50` — shells out to `git log --merges --since=yesterday` per project. Will be generalized.
- **AgentSession date filter:** `models/agent_session.py` exposes `completed_at: DatetimeField`, `status: IndexedField`, `pr_url, issue_url, plan_url: Field`, `turn_count: IntField`. Usable directly.
- **TelegramMessage date filter:** `models/telegram.py:23–44` — `timestamp: SortedField(partition_by="chat_id")` enables ZRANGEBYSCORE range queries. Fields: `classification_type, classification_confidence, message_type`.
- **Reflection findings:** `models/reflection.py:40–154` — `run_history: ListField` (capped 200 entries) with `{timestamp, status, duration, error, projects: [...]}`. Filter by `datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d")`.

### Revised (modify scope)

- **Multi-repo commit aggregation does NOT exist.** The plan must add a generic per-project iterator that walks every project in `projects.json` with `repo_path` set. Reuse the `git log` pattern from `pm_audio_briefing/collector.py:50` but generalize across repos.
- **Memory model has NO `created_at` field.** `models/memory.py:88–162` partitions by `project_key`, sorts by relevance decay. To filter "memories created yesterday", three options exist (see Open Questions):
  1. Add `created_at: FloatField` (clean, touches hot model — needs migration).
  2. Use `_at_key` introspection if Popoto stores creation time internally (verified `_at_key` attribute exists in dir(); semantics need confirmation).
  3. Scan with `Memory.query.all()` and infer date from `metadata.outcome_history[0].ts` (only works for memories with outcomes; coverage gap for fresh memories).
- **TelegramMessage has no "importance" field.** The "decision-bearing" filter must be heuristic on existing fields (see Open Questions):
  - Default proposal: include where `message_type == "text"` AND `classification_type` is in a decision-bearing set (e.g., decision, correction, instruction, plan-request) AND chat is in the project's tracked chat list. Excludes pure media replies and acknowledgments.
- **Vault directory does NOT exist yet.** `~/work-vault/AI Valor Engels System/daily-logs/` is greenfield. Plan includes idempotent `mkdir -p` in the reflection itself; no migration step.
- **Backfill of existing `report_*.md`** — issue leaves to planner. Plan recommends: **start fresh going forward.** Existing files have no substance worth preserving.

### Pre-requisites (none blocking)

- `config/PRINCIPAL.md` is referenced by current `daily_report.py` but missing on disk. Loader handles gracefully (returns empty). Not a blocker; out of scope.

### Dropped (explicitly out-of-scope)

- Refactoring `pm_audio_briefing/` — we *import or copy* its helpers; no edits to its source files.
- Editing the knowledge-indexing pipeline — `.md` write to vault is sufficient; auto-pickup handles indexing.
- Consolidating `daily-log-review` and `daily-report` — recommendation: **keep distinct.** Different surface area (server logs vs. system activity), different cadence, different consumers. Document the distinction in `docs/features/reflections.md`.

## Prior Art

- **[Issue #1197 / PR #1237]**: Daily PM audio briefing reflection (per-project angles + numbers-free voice + numbered written follow-up). **Outcome:** Merged 2026-05-01. **Relevance:** Direct template — `reflections/pm_audio_briefing/{builder,collector,delivery}.py` is the proven pattern this plan models after.
- **[Issue #1188]**: Rebuild daily-log-review as a local reflection (not remote CCR). **Outcome:** Closed 2026-04-30. **Relevance:** Sibling reflection; explicitly out-of-scope per issue but informs the "keep distinct" recommendation.
- **[Issue #748 / PR #967]**: Reflections monolith extraction (3086-line monolith → `reflections/` package). **Outcome:** Merged 2026-04-14. **Relevance:** Established the package layout this plan extends.
- **[Issue #926 / PR #933]**: Reflections quality pass — scheduler placement, model split, field conventions. **Outcome:** Merged 2026-04-13. **Relevance:** Sets the field/scheduler conventions this plan follows.
- **[Issue #1033]**: Investigations from daily audit (2026-04-17). **Outcome:** Closed 2026-04-22. **Relevance:** Demonstrates the prior failure mode — daily reports surfaced raw counts but no substance, which led directly to this issue.

## Research

No relevant external findings — proceeding with codebase context. The work is purely internal: existing `pm_audio_briefing` is the validated pattern, `tools/tts` is the validated synthesizer, and the work vault auto-indexing is already proven by `tools/valor_ingest.py`.

## Spike Results

No spikes were needed. All assumptions about API shapes, file paths, and helper signatures were verified directly via parallel Explore agents during recon (see Recon Summary). The remaining ambiguities are policy decisions for the human (see Open Questions), not technical unknowns.

## Data Flow

1. **Trigger.** `agent/reflection_scheduler.py::ReflectionScheduler.tick()` checks `is_reflection_due()` for the `daily-report-and-notify` entry once per day. When due, awaits the registered callable.
2. **Aggregator entry.** `reflections/daily_report.py::run()` is invoked. It computes the target date (`utc_now() - 1 day` → "yesterday") and the vault output path (`~/work-vault/AI Valor Engels System/daily-logs/{date}.md`). Idempotent `mkdir -p` on the parent directory.
3. **Source collection (per category).** Calls a new `_collect_day_activity(target_date) -> DayActivity` that aggregates from:
   - Git: `git log --since={date}T00:00:00 --until={date}T23:59:59 --pretty=format:%H|%an|%s` per project repo (each project from `projects.json` with `repo_path`). Separates merges from non-merges.
   - GitHub: `gh pr list --state all --search "merged:{date} OR closed:{date}"` per project repo. `gh issue list --state all --search "created:{date} OR closed:{date}"`.
   - AgentSession: `Model.query.filter(status="completed")` then filter `completed_at` to target date. Keep `pr_url`, `issue_url`, `plan_url`, `turn_count`, `total_cost_usd`.
   - TelegramMessage: per project chat, `Model.query.filter(chat_id=X)` filtered by `timestamp` SortedField range, then heuristic filter on `classification_type` + `message_type`.
   - Memory: scan with `Memory.query.all()` filtered by date heuristic (Open Question 1) and `metadata.category in {decision, correction, surprise}`.
   - Crashes: `monitoring.crash_tracker.get_recent_crashes(86400)`.
   - Reflections: `Reflection.query.all()` then filter `run_history` entries where `datetime.fromtimestamp(entry["timestamp"]).date() == target_date`.
4. **Markdown render.** `_render_day_log(activity, target_date) -> str` produces the file body. H1 = `# Daily Log: {date}`. Sections in priority order: Commits & PRs, Issues, Sessions, Telegram Decisions, Memory Observations, Errors & Incidents, Reflection Findings (demoted). Each entry uses full named entities (no bare numbers).
5. **File write.** Atomic write to `~/work-vault/AI Valor Engels System/daily-logs/{date}.md`. iCloud sync + knowledge indexer pick it up with no further action.
6. **Audio brief construction.** `_build_audio_brief(activity, project) -> tuple[transcript, log_link]` — adapts `reflections/pm_audio_briefing/builder.build()`. Two-pass: Pass A (Anthropic LLM with `/do-debrief` system prompt — decisions / heads-up / FYIs), Pass B (number-guard regex Layers 2+3 from `pm_audio_briefing/builder.py:52,58`, then word-count cut to ~70 words).
7. **TTS.** `tools.tts.synthesize(transcript, audio_path, format="opus")` → `{path, duration, error}`. Local Kokoro primary, OpenAI tts-1 fallback.
8. **Telegram delivery.** Build voice-note payload via `_voice_note_payload()` (L77–94 pattern from `pm_audio_briefing/delivery.py`). RPUSH to `telegram:outbox:{session_id}` on Redis. Bridge relay drains and sends.
9. **Done.** Reflection records `last_run_at`, `findings_count`, no error.

## Architectural Impact

- **New dependencies:** None. All deps already in pyproject (gh CLI, anthropic, popoto, tools/tts, redis).
- **Interface changes:**
  - `reflections/daily_report.py::run()` signature unchanged (`async def run() -> dict`).
  - `_collect_reflection_findings()` is replaced by `_collect_day_activity()` which returns a richer `DayActivity` dataclass.
  - `_post_to_telegram()` is replaced by `_send_audio_brief()` using Redis-outbox enqueue (no longer Telethon-direct).
- **Coupling:** Slight increase — daily-report now depends on `tools.tts`, `bridge.telegram_relay` payload conventions, `monitoring.crash_tracker`. All are stable public APIs.
- **Data ownership:** Daily log file moves from gitignored `logs/reflections/` to iCloud-synced vault. The knowledge indexer becomes the durable reader; `logs/reflections/` becomes write-once (or removed entirely — see Open Question 3).
- **Reversibility:** Easy. Toggle `enabled: true → false` in `config/reflections.yaml`. Old code is replaced cleanly (no parallel-run migration per memory feedback `feedback_no_parallel_migrations.md`).

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (open question resolution, scope confirmation)
- Review rounds: 1 (code review covering aggregator correctness, audio guard, vault write idempotency)

The work is mechanical aggregation + glue to a proven audio pipeline. No new architectural primitives. The bottleneck is correctness of the aggregator queries (date boundaries, partition keys) and verifying the no-numbers regex catches edge cases.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Work vault path exists | `test -d "$HOME/work-vault/AI Valor Engels System"` | Daily log destination root |
| `gh` CLI authenticated | `gh auth status` | PR/issue queries |
| `tools.tts.synthesize` importable | `python -c "from tools.tts import synthesize"` | Audio synthesis |
| Redis reachable | `python -c "from agent.redis_client import get_redis; get_redis().ping()"` | Outbox enqueue |
| `pm_audio_briefing` regex helpers importable | `python -c "from reflections.pm_audio_briefing.builder import _NUMBERS_PREFIXED_RE, _NUMBERS_BARE_RE"` | Number-guard reuse |
| Reflection registry valid | `python -c "from reflections.utils import load_local_projects; assert load_local_projects()"` | Per-project iteration |

Run all checks: `python scripts/check_prerequisites.py docs/plans/daily-log-overhaul.md`

## Solution

### Key Elements

- **Aggregator (`_collect_day_activity`).** Queries 7 sources for a target date, returns a `DayActivity` dataclass keyed by category. Uses Popoto `Model.query.filter()` (never raw Redis per `feedback_never_raw_delete_popoto.md`).
- **Renderer (`_render_day_log`).** Produces a Markdown file with stable section order, full named entities, and durable terminology that supports text-search lookup.
- **Vault writer.** Atomic write to `~/work-vault/AI Valor Engels System/daily-logs/{date}.md` with idempotent `mkdir -p`. Replaces the old `logs/reflections/` write entirely (no dual-write).
- **Audio brief builder.** Adapts `pm_audio_briefing.builder.build()` for system-wide (not per-project) input. Reuses the Pass A LLM prompt structure and the Layer 2/3 number-guard regexes via direct import (no copy).
- **TTS + delivery.** Calls `tools.tts.synthesize()`, builds the standard voice-note payload, RPUSH to `telegram:outbox:{session_id}`. Same shape as `pm_audio_briefing/delivery.py`.
- **Registry flip.** `enabled: false → true` for `daily-report-and-notify` in `config/reflections.yaml`.

### Flow

Reflection scheduler tick → `daily_report.run()` → `_collect_day_activity()` → 7 parallel data-source queries → `_render_day_log()` → write `~/work-vault/.../daily-logs/{date}.md` → `_build_audio_brief()` (LLM + regex guard + word-count cut) → `tools.tts.synthesize()` → RPUSH voice-note payload to `telegram:outbox` → bridge relay sends `.ogg` to chosen project chat → done.

### Technical Approach

- **Reuse, don't refactor.** Import `_NUMBERS_PREFIXED_RE` and `_NUMBERS_BARE_RE` from `reflections.pm_audio_briefing.builder`. If those are private, lift them to a shared module *only if needed during build* — defer the lift decision to the builder; cross-module import of underscore-prefixed names is acceptable for sibling reflections.
- **Date boundaries.** Use UTC throughout per `feedback_timestamp_timezone.md`. `bridge.utc.utc_now() - timedelta(days=1)` → start at 00:00 UTC, end at 23:59:59 UTC. Document the choice in code comment + docs/features/reflections.md.
- **Per-project iteration.** Read `projects.json` via `reflections.utils.load_local_projects()` (already used by `pm_audio_briefing/collector.py`). For each project with a `repo_path`, run git/gh queries against that repo. Aggregate cross-project results.
- **Memory date filter.** Adopt Open Question 1's resolved approach. Default proposal: scan `Memory.query.all()` and use `_at_key` field if Popoto exposes it; otherwise fall back to `metadata.outcome_history[0].ts` and accept partial coverage. **Avoid adding `created_at` field** unless scan performance is observed to be unacceptable (run `time` once during build to measure).
- **Audio session_id.** Use the same `session_id` convention as `pm_audio_briefing` — a synthetic ID like `daily-report-{date}` so the outbox key partitions cleanly.
- **No fallback to plaintext.** Per the issue, the audio brief replaces the plaintext message entirely. If TTS fails, log the error and skip Telegram delivery for the day (the file in the vault is still authoritative). Do **not** ship a plaintext-fallback message — fits `feedback_no_parallel_migrations.md` and the issue's explicit "no text preface" requirement.
- **Daily-log-review distinction documented.** Add a "Distinct daily reflections" section to `docs/features/reflections.md` clarifying: `daily-report-and-notify` (this plan) covers system activity; `daily-log-review` (#1188) covers server-log error scanning.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `reflections/daily_report.py` currently has broad `try/except Exception` in `run()` and `_post_to_telegram()`. Each must remain narrow and log via `logger.warning` with context. Tests must assert that an aggregator failure on one source (e.g., `gh` rate-limited) does NOT abort the whole reflection — the file still gets written with a `[ERROR collecting source X]` placeholder for that section.
- [ ] TTS failure path: assert that `synthesize()` returning `{error: "..."}` causes the reflection to log a warning and skip Telegram delivery, while still completing the file write.

### Empty/Invalid Input Handling

- [ ] Empty day (no commits, no PRs, no sessions): file is still written with a `## (No system activity recorded for {date})` body. Audio brief is skipped (no point speaking nothing). Test verifies file presence + Telegram queue is empty.
- [ ] Whitespace-only commit messages, missing `pr_url`, etc. — assert the renderer omits them rather than emitting a broken bullet.
- [ ] Bridge relay outbox unreachable (Redis down): TTS still succeeds, file still written, Telegram delivery error is logged, reflection records `partial_success`.

### Error State Rendering

- [ ] When TTS fails, no `.ogg` is enqueued AND no plaintext fallback is sent (per scope). The error must surface in the reflection's `last_error` field and be visible via `Reflection.query.filter(name="daily-report-and-notify")`.
- [ ] When the vault directory is unwritable (e.g., iCloud not synced), assert the reflection logs the path and the error, and does NOT silently swallow.

## Test Impact

- [ ] `tests/unit/reflections/test_daily_report.py` (if present) — UPDATE: replace tests of `_collect_reflection_findings()` with tests of `_collect_day_activity()`. Add fixtures for each data source (mocked `gh` subprocess output, in-memory Popoto records).
- [ ] `tests/integration/reflections/test_daily_report_integration.py` (if present) — REPLACE: test the full pipeline against a real Redis + git fixture repo + mocked `gh`. Verify the file lands in a temp vault dir and the audio outbox payload is correctly shaped.
- [ ] `tests/unit/reflections/test_pm_audio_briefing_builder.py` — VERIFY UNCHANGED: this plan only imports from `pm_audio_briefing.builder`, doesn't modify it. Run the existing tests after the change to confirm no regression.
- [ ] Add a new test asserting the no-numbers regex catches "PR 1263", "issue #1263", "5 commits" (where 5 has no exempted unit). Use the same fixture set as `test_pm_audio_briefing_builder.py` for parity.
- [ ] Add a new test for date boundary correctness — feed a commit timestamped at `23:59:59 UTC` of target date and `00:00:00 UTC` of the next day; assert only the first is included.
- [ ] If no existing daily_report test files exist (likely — current implementation is sparse): create them as new files. Justification: the new behavior is rich enough to warrant unit + integration coverage; the prior plaintext stats line had no test value.

## Rabbit Holes

- **Refactoring `pm_audio_briefing/` to share helpers.** Tempting because of duplication, but explicitly out-of-scope. Direct cross-module import is good enough for two callers.
- **Adding `created_at` to Memory across the board.** Touches a hot model with bloom/embedding indexes. Defer until scan performance is observed to be a problem; even at 100k records the scan is sub-second.
- **Backfilling old `logs/reflections/report_*.md`** into the vault. They have no substance worth searching. Discard. Document this once in `docs/features/reflections.md`.
- **DAG scheduling for reflections (#968).** Out of scope per issue.
- **Multi-day rollups, weekly summaries.** Out of scope. The day file is the unit; weekly views can be added later as a derived reflection.
- **Cross-machine deduplication of daily logs** when multiple machines write to the same iCloud vault. Each machine writes its own log; if two machines run the reflection on the same day, last write wins (atomic write). If problematic in practice, address in a follow-up — not in this plan.

## Risks

### Risk 1: No-numbers regex misses a new edge case

**Impact:** A PR number leaks into the audio brief, making it unactionable when spoken. The user's prior memory `feedback_no_specific_numbers_in_prompts.md` flags this exact category as a hallucination/UX risk.

**Mitigation:** Reuse `_NUMBERS_PREFIXED_RE` (Layer 2) and `_NUMBERS_BARE_RE` (Layer 3) from `pm_audio_briefing.builder` *exactly*. Add unit tests covering: "PR 1263", "issue #1263", "issue 1263", "pr-1263", "10 commits", "5 PRs", "10ms latency" (exempted), "50%" (exempted). If a new pattern leaks during testing, add to the shared regex module rather than adding a one-off bypass.

### Risk 2: Per-project git aggregation slow on a many-repo machine

**Impact:** The reflection times out (default 1500s for similar reflections) if git log is slow across many repos. With 5+ projects, even 2s per `git log` adds up.

**Mitigation:** Run per-project queries concurrently via `asyncio.gather()` over a thread pool (`asyncio.to_thread` for subprocess calls). Add a per-source `time` log so future regressions are visible. Set a per-source timeout of 30s and degrade gracefully (write `[ERROR: timeout]` for that source).

### Risk 3: Vault path not present on dev machines

**Impact:** Reflection fails on machines that don't have iCloud / the vault synced. Currently, dev machines may not have `~/work-vault/AI Valor Engels System/`.

**Mitigation:** `mkdir -p` is idempotent. If the parent doesn't exist (no iCloud), the `mkdir -p` creates a local-only path; the daily log lands in a local directory but is not synced. Document this explicitly: "On machines without iCloud sync, the daily log is local-only; that's expected." Reflection itself does not fail.

### Risk 4: Memory date heuristic produces empty results

**Impact:** Memory section of the daily log is consistently empty because no Memory has `metadata.outcome_history` populated yet (sparse coverage).

**Mitigation:** Run a one-shot probe during build to measure `Memory.query.all()` scan cost and what fraction have any timestamp signal. If <50% coverage, escalate Open Question 1 to "add `created_at` field" and ship that as a sub-task. Worst case: section reads "Memory observations: (no timestamped memories)" and we add the field in a follow-up issue.

### Risk 5: Audio session_id collision with another reflection

**Impact:** RPUSH to `telegram:outbox:daily-report-{date}` could clobber if another reflection chose the same key.

**Mitigation:** Use a globally unique prefix `daily-report-and-notify-{date}` matching the reflection registry name. Verify no existing reflection uses this prefix via `grep -r 'telegram:outbox:' reflections/`.

## Race Conditions

### Race 1: Reflection runs while a session is mid-write to AgentSession

**Location:** `_collect_day_activity()` querying `AgentSession` while a worker session updates.

**Trigger:** The aggregator scans completed sessions for yesterday. A session that completed at 23:59 UTC yesterday might still be flushing fields when the aggregator (running shortly after midnight) reads them.

**Data prerequisite:** All `completed_at` and final field values must be persisted before the aggregator reads.

**State prerequisite:** `AgentSession.status == "completed"` is set after all other final fields are written.

**Mitigation:** Run the daily-report reflection at 00:30 UTC (not 00:01 UTC) to give a 30-minute buffer for any in-flight session writes to settle. Configurable via `config/reflections.yaml` `interval` and last-run timing. No locks needed — Popoto records are atomic per-field, and a partial read (if one occurs) will simply omit the in-flight session, which is acceptable.

### Race 2: Two machines running the same reflection write to the same vault file

**Location:** `~/work-vault/AI Valor Engels System/daily-logs/{date}.md` written from machines that both have iCloud sync.

**Trigger:** Both machines run the reflection at ~00:30 UTC; iCloud reconciles by last-write-wins or generates a conflict copy.

**Data prerequisite:** Each machine should ideally produce the same content (same data sources are queried).

**State prerequisite:** Single-machine ownership invariant per `CLAUDE.md` — only one machine should run the reflection.

**Mitigation:** Treat as out-of-scope race for v1. Per `CLAUDE.md` single-machine ownership rules, reflection scheduling is local; one designated machine runs daily-report-and-notify. If two machines run it, iCloud's conflict resolution produces `report-{date} (1).md`-style conflict copies — visible but non-fatal. Document this in `docs/features/reflections.md` as a known limitation.

### Race 3: Bridge relay drains the audio outbox before the file is fully on disk

**Location:** Voice-note payload `file_paths: [audio_path]` with `cleanup_file: true`. Bridge consumes and deletes.

**Trigger:** TTS writes the `.ogg` async; if RPUSH happens before the file is flushed, the bridge could read a partial file.

**Data prerequisite:** The `.ogg` file is fully written and `fsync`-ed before the payload is enqueued.

**State prerequisite:** N/A — local FS.

**Mitigation:** `tools.tts.synthesize()` returns synchronously after writing the file (verified in `tools/tts/__init__.py:360`). Enqueue happens after the function returns. Same pattern as `pm_audio_briefing/delivery.py` — already proven safe.

## No-Gos (Out of Scope)

- DAG scheduling for reflections (#968 — separate work).
- Refactoring `pm_audio_briefing/` (it has its own per-project lifecycle).
- New reflection categories beyond what the daily log aggregates.
- Editing the broader knowledge-indexing pipeline.
- Adding a `created_at` field to the Memory model unless scan performance is observed to be unacceptable.
- Plaintext-message fallback when TTS fails.
- Backfilling existing `logs/reflections/report_*.md` into the vault.
- Cross-machine coordination of who writes the daily log.

## Update System

- **No update script changes required.** This is purely a code change inside `reflections/daily_report.py` and `config/reflections.yaml`. Existing machines will pick it up via `git pull` during the next `/update` cycle. The reflection scheduler reloads the registry on start, so a worker restart (already part of `/update`) flips the new entry on.
- **No new dependencies.** All deps (`gh`, `anthropic`, `popoto`, `tools/tts`, `redis`) are already installed on every machine.
- **One-time manual step (per machine, post-update):** ensure `~/work-vault/AI Valor Engels System/` exists. The reflection's `mkdir -p` handles `daily-logs/` creation idempotently. Document this in the post-update verification section of `docs/features/reflections.md`.

## Agent Integration

- **No agent integration required** — this is a reflection (background scheduled job), not an agent-invokable tool. The reflection runs autonomously via `agent/reflection_scheduler.py` and delivers output through Telegram (audio) and the work vault (markdown).
- The agent indirectly *consumes* the output: when a user later asks "what happened on day X?" or "when did we ship Y?", the agent's knowledge-base search picks up the daily log file from the vault automatically (no MCP wiring needed; `tools/valor_ingest.py` handles it).
- No changes to `.mcp.json`, no new CLI entry in `pyproject.toml`, no bridge code changes.

## Documentation

### Feature Documentation

- [x] Update `docs/features/reflections.md` — describe the new daily-report behavior (vault destination, audio delivery), explicitly distinguish from `daily-log-review`. Add a "Daily reflections" subsection comparing the two.
- [x] Update `docs/features/README.md` index if the reflections entry needs a new tagline.
- [x] No new feature doc required — daily-report is a reflection, covered by `docs/features/reflections.md`.

### External Documentation Site

Not applicable — this repo doesn't use Sphinx/MkDocs.

### Inline Documentation

- [x] Docstring on `_collect_day_activity()` enumerating the 7 data sources and their date-filter contracts.
- [x] Docstring on `_render_day_log()` describing section order and entity-naming requirements (full names, not bare numbers).
- [x] Docstring on `_build_audio_brief()` referencing `/do-debrief` pattern and the no-numbers regex layers.
- [x] One-line comment on the UTC date-boundary choice in `run()` (per `feedback_timestamp_timezone.md`).

## Success Criteria

- [ ] `~/work-vault/AI Valor Engels System/daily-logs/{date}.md` is created each day with substantive activity from at least 5 of the 7 data sources (allowing 2 to be empty on quiet days).
- [ ] Full-text search `grep -r "{commit-subject}" ~/work-vault/AI\ Valor\ Engels\ System/daily-logs/` returns the day file containing that commit.
- [ ] Opening `{date}.md` and reading top-to-bottom answers "what happened on day X?" without consulting any other source.
- [ ] Telegram delivery is a `.ogg` voice note constructed via the `/do-debrief` pattern (~70 spoken words, decisions/heads-up/FYIs structure). No text preface. No plaintext fallback.
- [ ] Audio guard regex test confirms no PR/issue numbers slip into the spoken transcript across a fixture set including: "PR 1263", "issue #1263", "10 commits", "ms" exempted, "%" exempted.
- [ ] `daily-log-review` (#1188) test suite still passes — verifies no accidental breakage.
- [x] `docs/features/reflections.md` updated with new daily-log behavior + the daily-report-vs-daily-log-review distinction.
- [x] `config/reflections.yaml` `daily-report-and-notify` flipped to `enabled: true`.
- [x] Tests pass (`/do-test`).
- [x] Documentation updated (`/do-docs`).
- [ ] No raw Redis access in the new code (per `feedback_never_raw_delete_popoto.md`) — enforced by `validate_no_raw_redis_delete.py` hook.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead never builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (aggregator)**
  - Name: aggregator-builder
  - Role: Implement `_collect_day_activity()` and per-source query helpers in `reflections/daily_report.py`. Includes per-project git/gh aggregation, AgentSession/TelegramMessage/Memory queries, crash tracker integration, reflection findings collection.
  - Agent Type: builder
  - Resume: true

- **Builder (renderer + audio)**
  - Name: renderer-builder
  - Role: Implement `_render_day_log()` Markdown rendering, `_build_audio_brief()` LLM + regex-guard pipeline, vault file write, TTS + Redis-outbox delivery. Imports number-guard regexes from `reflections.pm_audio_briefing.builder`.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: daily-report-test-engineer
  - Role: Write unit + integration tests for the aggregator, renderer, audio guard, date boundaries, error paths. Verify `pm_audio_briefing` tests still pass post-change.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: daily-report-validator
  - Role: Verify the success criteria, run the reflection end-to-end against a fixture day, confirm the file lands in the vault, confirm the audio outbox payload shape matches `pm_audio_briefing/delivery.py`, confirm no raw Redis access.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: daily-report-documentarian
  - Role: Update `docs/features/reflections.md`, add the daily-report-vs-daily-log-review distinction section, update inline docstrings.
  - Agent Type: documentarian
  - Resume: true

- **Code Reviewer**
  - Name: daily-report-reviewer
  - Role: Review the diff for correctness (date boundaries, partition keys), no-numbers regex coverage, vault write idempotency, no plaintext fallback, no parallel-run migration patterns.
  - Agent Type: code-reviewer
  - Resume: true

## Step by Step Tasks

### 1. Build aggregator

- **Task ID**: build-aggregator
- **Depends On**: none
- **Validates**: tests/unit/reflections/test_daily_report_aggregator.py (create), tests/integration/reflections/test_daily_report_integration.py (create)
- **Informed By**: Recon Summary — reuse `pm_audio_briefing/collector.py:50` git pattern, `crash_tracker.get_recent_crashes()`, Popoto Model.query.filter() for AgentSession/TelegramMessage/Memory/Reflection.
- **Assigned To**: aggregator-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `DayActivity` dataclass (commits, prs, issues, sessions, telegram_decisions, memories, crashes, reflections).
- Implement per-source collectors with UTC date boundaries and 30s per-source timeout + graceful degradation.
- Use `asyncio.gather()` for per-project parallel git/gh subprocess calls.
- Resolve Memory date heuristic per Open Question 1 (default: scan + filter by `_at_key`/`metadata.outcome_history` fallback).
- Resolve Telegram decision-bearing filter per Open Question 2.

### 2. Build renderer + audio + delivery

- **Task ID**: build-renderer
- **Depends On**: build-aggregator
- **Validates**: tests/unit/reflections/test_daily_report_renderer.py (create), tests/unit/reflections/test_daily_report_audio_guard.py (create)
- **Informed By**: Recon Summary — `pm_audio_briefing/builder.py:52,58,256`, `pm_audio_briefing/delivery.py:77–116`, `tools/tts/__init__.py:360`.
- **Assigned To**: renderer-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `_render_day_log(activity, date) -> str` with stable section order and full named entities.
- Implement `_build_audio_brief(activity) -> tuple[str, str]` (transcript, log link).
- Implement Pass A (LLM) + Pass B (regex guard via direct import + word-count cut).
- Implement vault writer with idempotent `mkdir -p` and atomic file write.
- Implement TTS call + Redis-outbox enqueue.
- Replace `_post_to_telegram` with `_send_audio_brief`.
- Update `run()` to wire the new pipeline; remove old `_collect_reflection_findings` and `_post_to_telegram`.

### 3. Flip config + register reflection

- **Task ID**: build-config-flip
- **Depends On**: build-renderer
- **Validates**: tests/integration/reflections/test_daily_report_integration.py (create)
- **Assigned To**: aggregator-builder
- **Agent Type**: builder
- **Parallel**: false
- Set `daily-report-and-notify.enabled: true` in `config/reflections.yaml`.
- Verify the entry's `interval`, `priority`, `timeout` are appropriate (likely interval 86400, timeout 600s).
- Confirm callable string `reflections.daily_report.run` still resolves.

### 4. Test engineering

- **Task ID**: test-daily-report
- **Depends On**: build-renderer, build-config-flip
- **Validates**: all created test files pass; `tests/unit/reflections/test_pm_audio_briefing_builder.py` still passes (regression check).
- **Informed By**: Test Impact section.
- **Assigned To**: daily-report-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests for each per-source collector (mocked `gh` subprocess, in-memory Popoto, fixture git repo).
- Unit tests for renderer with edge cases (empty day, partial-source failure).
- Unit tests for audio guard regex (PR/issue/bare-number coverage + exempted units).
- Unit test for UTC date boundary.
- Integration test against a real Redis + git fixture + temp vault dir; assert file lands and outbox payload is correct.
- Run `pm_audio_briefing` test suite to confirm no regression.

### 5. Validation

- **Task ID**: validate-daily-report
- **Depends On**: test-daily-report
- **Assigned To**: daily-report-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -c "import asyncio; from reflections.daily_report import run; asyncio.run(run())"` against a recent date with known activity; verify the file lands in the vault.
- Inspect the file: confirm at least 5 sections have content, confirm full named entities (not bare numbers).
- Inspect the outbox: verify the payload matches `pm_audio_briefing/delivery.py:77–94` shape.
- Run `grep -rn "redis_conn.delete\|redis_conn.srem\|redis_conn.hgetall\|redis_conn.scan_iter" reflections/daily_report.py` — must be empty.
- Run `python .claude/hooks/validators/validate_no_raw_redis_delete.py reflections/daily_report.py` — must pass.

### 6. Documentation

- **Task ID**: document-daily-report
- **Depends On**: validate-daily-report
- **Assigned To**: daily-report-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` — new daily-report behavior, vault destination, audio delivery, daily-report-vs-daily-log-review distinction.
- Confirm `docs/features/README.md` index is current.
- Add the inline docstrings called out in Documentation section.
- Verify markdown linting passes.

### 7. Code review

- **Task ID**: review-daily-report
- **Depends On**: document-daily-report
- **Assigned To**: daily-report-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Review the diff for date-boundary correctness (UTC throughout), partition-key correctness on Popoto queries, no-numbers regex coverage, vault write idempotency.
- Confirm no plaintext fallback exists (per scope).
- Confirm no parallel-run migration pattern exists (per `feedback_no_parallel_migrations.md`).
- Confirm no raw Redis access (per `feedback_never_raw_delete_popoto.md`).

### 8. Final validation

- **Task ID**: validate-all
- **Depends On**: review-daily-report
- **Assigned To**: daily-report-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success criteria checks.
- Run full test suite (`pytest tests/unit/ -n auto && pytest tests/integration/`).
- Run `python -m ruff check . && python -m ruff format --check .`.
- Confirm reflection registry shows `daily-report-and-notify enabled=true`.
- Generate final PR-ready report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No raw Redis | `python .claude/hooks/validators/validate_no_raw_redis_delete.py reflections/daily_report.py` | exit code 0 |
| Reflection registered | `grep -A 6 'daily-report-and-notify' config/reflections.yaml \| grep 'enabled: true'` | exit code 0 |
| Vault writer reachable | `python -c "from reflections.daily_report import _resolve_vault_path; print(_resolve_vault_path())"` | output contains `daily-logs` |
| Audio guard imports | `python -c "from reflections.pm_audio_briefing.builder import _NUMBERS_PREFIXED_RE, _NUMBERS_BARE_RE"` | exit code 0 |
| Old API removed | `grep -n '_collect_reflection_findings\|_post_to_telegram' reflections/daily_report.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Memory date filter strategy.** Memory has no `created_at` field. Three options:
   (a) Add `created_at: FloatField` (clean, hot model — needs migration coordination).
   (b) Use Popoto's internal `_at_key` if it exposes creation timestamp (verify semantics during build).
   (c) Scan and infer date from `metadata.outcome_history[0].ts`, accepting partial coverage.
   **Plan default:** (b) with (c) fallback. Add `created_at` only if the build measures unacceptable coverage.
   **Decision needed?** Yes — confirm before build starts, or accept the default.

2. **TelegramMessage decision-bearing filter heuristic.** Plan's default proposal:
   - Include where `message_type == "text"` AND `classification_type` is in `{decision, correction, instruction, plan-request}` AND chat is in the project's tracked chat list.
   - Exclude pure media replies, pure acknowledgments, classification_confidence < 0.5.
   **Decision needed?** Yes — confirm the `classification_type` set, or accept the default.

3. **Keep or remove `logs/reflections/report_*.md` writes.** Plan proposes: stop writing to `logs/reflections/` entirely (vault-only). This matches `feedback_no_parallel_migrations.md` (no parallel-run migrations). Existing files in `logs/reflections/` are gitignored anyway and have no substance worth preserving.
   **Decision needed?** Yes — confirm "vault-only, no dual-write", or override.

4. **Daily-log-review consolidation.** Plan recommends: **keep distinct.** Different surface (server logs vs. system activity), different consumers, different cadence semantics. Document the distinction in `docs/features/reflections.md`.
   **Decision needed?** Yes — confirm "keep distinct", or instruct to consolidate.

5. **Run cadence and time-of-day.** Plan proposes 00:30 UTC daily (30-min buffer for late-night session writes to settle). Existing `daily-report-and-notify` interval is 86400.
   **Decision needed?** Yes — confirm time-of-day, or pick a different one.
