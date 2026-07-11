---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2016
last_comment_id:
revision_applied: true
---

# Memory Extraction: Recurring Junk Cluster (agent-id-cluster ebe79d3b)

## Problem

The memory quality audit has filed the **same** anomaly issue four times in seven
weeks for the identical agent_id cluster `extraction-local-ebe79d3b-…` (#1497 →
#1786 → #1931 → #2016). Two prior closes claimed the root cause was fixed; the
audit re-filed anyway. There are two distinct defects behind this.

**Current behavior:**
1. A long-lived direct-CLI Claude Code session produces Haiku extraction output
   containing JSON-shrapnel-shaped observation values (e.g. a string like
   `"category": "decision"`). These pass every **save-time** guard because the
   whole-text refusal check's `_JSON_SHRAPNEL_RE` is anchored `^…$` and cannot
   match a multi-line JSON response. They are persisted as Memory records, then
   the daily audit's **per-record** `_looks_like_refusal(m.content)` catches and
   supersedes them (Layer 1). The gap: the JSON-parse branch of
   `_parse_categorized_observations` does not filter each observation value with
   the same predicate the audit uses, while the line-based fallback does.
2. When ≥10 such records are superseded in one run from one agent_id, the audit
   files a GitHub issue. Its dup-check queries only `--state open`, so once the
   issue is **closed**, the next daily run re-files a fresh one. The issue body's
   own advice — "close the issue with a comment so future audits don't re-file" —
   is false under the current code.

**Desired outcome:**
- Save-time parse paths filter each observation with the exact predicate the
  audit applies per-record, so junk never reaches the store and the audit
  supersedes nothing from this vector.
- Closing an acknowledged cluster issue suppresses re-filing for a bounded
  window, so a known/acknowledged anomaly stops churning new issues while still
  re-surfacing if it genuinely persists past the window.

## Freshness Check

**Baseline commit:** `711b26f2`
**Issue filed at:** 2026-07-11T04:02:56Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/memory_extraction.py:526` — `agent_id = f"extraction-{session_id}"` producing path — still holds.
- `agent/memory_extraction.py:626-643` — JSON-parse branch saves `observation` values without `_looks_like_refusal` — confirmed present.
- `agent/memory_extraction.py:662` — line-based fallback filters each line with `_looks_like_refusal` — confirmed (asymmetry with the JSON branch).
- `agent/memory_extraction.py:109,226` — `_JSON_SHRAPNEL_RE` anchored `^…$`, used in `_looks_like_refusal` — confirmed.
- `agent/memory_extraction.py:414` — `#1831` trivial-session gate (`turn_count <= 1 AND not is_conversational`) — confirmed; never fires for a multi-turn session.
- `reflections/memory/memory_quality_audit.py:565-566` — dup-check `--state open` only — confirmed.
- `.claude/hooks/stop.py:169` — legacy `local-{session_id}` sidecar reconstruction — confirmed.

**Cited sibling issues/PRs re-checked:**
- #1497, #1786, #1931 — all CLOSED as COMPLETED; each closure claim falsified by the next filing.
- #1822 / PR #1831 — merged 2026-06-30 (`0f68f09e`); did not stop the recurrence.
- #1212, PR #1217 — merged; original JSON-shrapnel/refusal hardening (foundation this builds on).
- #1829 — OPEN (LLM refusal-detector complement); related, not a blocker.
- #1925 — OPEN (harness migration); not a blocker.

**Commits on main since issue was filed (touching referenced files):** none. Last extractor commit `ffed9ba0` (2026-07-11 00:50) predates the 04:02Z filing.

**Active plans in `docs/plans/` overlapping this area:** none. (`grep` hits are all under `docs/plans/completed/`.)

**Notes:** All issue claims verified against current `main`. No drift.

## Prior Art

- **#1212 / PR #1217**: "Memory extraction stores JSON shrapnel and refusal prose as observations" — introduced `_REFUSAL_PATTERNS`, `_JSON_SHRAPNEL_RE`, `extract_json_payload`, and the line-based per-line filter. Succeeded for the line-based path; left the JSON-parse branch unfiltered.
- **#1231 / PR #1252**: "3-layer memory health audit" — created the audit that supersedes junk (Layer 1) and files anomaly issues (Layer 2/3), including this dup-check.
- **#1822 / PR #1831**: "close three systematic extraction noise sources + GC tier" — appended 7 refusal phrases, added the `turn_count <= 1` trivial-session gate, added a decay-prune GC tier. **Failed to stop this cluster** (see below).
- **#1497 (closed "one-off"), #1786 (closed "resolved by #1831"), #1931 (closed "owned by #1829")**: three prior filings of this exact cluster, each closed on an incorrect or incomplete premise.
- **#1829 (OPEN)**: "LLM-based refusal-detector complement to `_REFUSAL_PATTERNS`" — the intended long-term vocabulary-independent detector; complements but does not block this structural fix.

## Research

No relevant external findings — this is purely internal (Python parsing logic, the `gh` CLI dup-check, and this repo's Popoto/Memory model). Proceeding with codebase context and training data.

## Data Flow

1. **Entry point**: A direct-CLI Claude Code session ends → `.claude/hooks/stop.py` `_run_memory_extraction(session_id, …)` fires with `session_id = local-ebe79d3b-…`.
2. **Haiku call**: `extract_observations_async` calls Haiku; `raw_text` is a multi-line JSON array of observations.
3. **Whole-text guard**: `_looks_like_refusal(raw_text)` — `_JSON_SHRAPNEL_RE.match` is anchored and cannot match multi-line text → returns False → **passes**.
4. **Parse (JSON branch)**: `_parse_categorized_observations` → `extract_json_payload` slices, `json.loads` succeeds → loops observations checking only length + `_is_scoping_boilerplate`. A shrapnel-shaped observation value (e.g. `"category": "decision"`) is **appended unfiltered**.
5. **Save**: `Memory.safe_save(agent_id="extraction-local-ebe79d3b-…", content=<shrapnel>, …)` persists the record.
6. **Audit (next day)**: `memory_quality_audit` Layer 1 runs `_looks_like_refusal(m.content)` per record → the per-record `_JSON_SHRAPNEL_RE.match` **matches** → supersedes; counts ≥10 from one agent_id → **files an issue** (dup-check open-only).
7. **Output**: GitHub issue `#2016`; on close, step 6 re-files next run.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1217 (#1212) | Added `_JSON_SHRAPNEL_RE` + refusal patterns; filtered the **line-based** fallback per line | Left the **JSON-parse branch** unfiltered — observation values from valid JSON were never checked. |
| PR #1831 (#1822) | Appended 7 refusal phrases; added `turn_count <= 1` trivial-session gate | Appending phrases can't help: save and audit share the same tuple, so a phrase miss is missed by both. The surviving class is **shrapnel-shaped** values caught only by the anchored regex per-record. The trivial-session gate targets `turn_count <= 1`; the producing session is multi-turn, so the gate never fires. |
| #1786 close comment | Diagnosed "refusal rephrased beyond vocabulary; save missed / audit caught" | **Self-inconsistent** — same tuple both sides. Misdiagnosis led to the ineffective PR #1831 remedy. |
| #1497 / #1931 closes | Closed as "one-off" / "owned elsewhere" | The dup-check's `--state open` filter guarantees re-filing after close, so closing without a code fix cannot stop recurrence. |

**Root cause pattern:** two independent structural gaps — (a) save-time parse paths do not enforce the **same per-record predicate** the audit uses, creating a whole-text-vs-per-record asymmetry; (b) the audit's dedup ignores closed issues, so acknowledgement never suppresses re-filing.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none to public signatures. `_parse_categorized_observations` gains an internal type guard + per-observation filter in its JSON branch; `_find_open_audit_issue` (renamed `_find_recent_audit_issue`) gains a closed-issue-window check and returns open-OR-recently-closed hits. Both are internal; the caller contract (positive-int/`-1` ⇒ suppress, `None` ⇒ file) is unchanged.
- **Coupling**: reduces the latent coupling/asymmetry between save-time and audit-time junk predicates by making them agree.
- **Data ownership**: unchanged; still Popoto `Memory` records.
- **Reversibility**: fully reversible — both changes are localized guard additions; revert restores prior behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev, plus one review round.

**Interactions:**
- PM check-ins: 1-2 (confirm scope covers both the save-time filter and the churn fix; resolve the version-skew open question).
- Review rounds: 1 (code review of the two guard changes and their tests).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Anthropic API key (for existing extraction tests that stub it) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Extraction tests reference the key path (calls are mocked; presence keeps the guard branches consistent). |
| `gh` CLI authenticated | `gh auth status` | The dup-check and its tests exercise `gh issue list`. |

## Solution

### Key Elements

- **Type guard + per-record save-time filter (JSON branch)**: In `_parse_categorized_observations`, first coerce-guard each observation value with `if not isinstance(observation, str): continue` (a dict/list value would otherwise raise `AttributeError` inside `_is_scoping_boilerplate`/`_looks_like_refusal`, which the surrounding `except (JSONDecodeError, TypeError)` does **not** catch — crashing the whole batch and losing every real observation in it). Then run every parsed observation value through `_looks_like_refusal` (keeping the existing `_is_scoping_boilerplate` check) before appending — the exact predicate the audit applies per-record. This makes save-time and audit-time agree, closing the shrapnel-in-valid-JSON vector. A `logger.debug` line records each Fix-A drop (category + preview) so an over-drop of a legitimate observation is diagnosable rather than silent.
- **Closed-issue suppression window (audit dedup)**: `_find_open_audit_issue` is restructured to scan **all** matching-title-prefix issues (open and closed) and return a positive issue number when any match is OPEN **or** CLOSED within a bounded window (default **14 days**), so acknowledging (closing) the cluster issue suppresses re-filing for the window. When the only matches are closed **beyond** the window, it returns `None` (⇒ file), so a genuinely persistent anomaly re-surfaces after the window. The `-1` `gh`-failure sentinel is preserved. Caller contract at `_file_anomaly_issue` (L605-610) is unchanged and already correct: a positive int **or** `-1` suppresses filing; `None` files.
- **Truthful guidance text**: update the audit's issue-body template so its "close to suppress" note matches the new behavior (names the 14-day window).

**Why Fix B is justified (grounded, not speculative):** the basis is the demonstrated 3× wasted-filing history — #1497 → #1786 → #1931 were each closed in good faith, and the open-only dedup re-filed anyway. That is a real closed-issue-dedup defect with concrete past harm, not a hypothetical "future/legacy producer" concern. Note explicitly that Fix B changes re-file behavior for **every** memory-audit signal that flows through `_find_open_audit_issue`/`_file_anomaly_issue`, not only the ebe79d3b cluster — acknowledging (closing) any memory-audit issue now suppresses its re-file for 14 days. This is intended and desirable (closing an audit issue should mean something), and it is safe because the title prefix is per-signal.

### Flow

Session ends → Haiku extraction → **JSON branch type-guards then filters each observation with `_looks_like_refusal`** → shrapnel dropped (with a debug-log breadcrumb), only real observations saved → audit finds nothing to supersede from this vector → no issue filed.

Independently: audit detects a real anomaly → **`_find_open_audit_issue` consults open + recently-closed issues** → if acknowledged (closed) within the 14-day window, return the number (suppress); else return `None` (file).

### Technical Approach

- **Fix A** (`agent/memory_extraction.py`, JSON branch, loop at L626-643, `observation = item.get("observation", "")` at L630):
  1. Immediately after L630, add `if not isinstance(observation, str): continue` — **before** the `len(observation) < 10` check at L631. This closes a pre-existing latent crash: a JSON observation value that is a dict/list (not a string) reaches `_is_scoping_boilerplate(observation)` (`.lower()`) and, after this fix, `_looks_like_refusal(observation)` (`.strip()`), both of which raise `AttributeError`. That is **not** caught by the surrounding `except (JSONDecodeError, TypeError)` at L651, so the whole batch aborts and every real observation is lost. The type guard makes the plan's "`_looks_like_refusal` is pure and total" claim actually hold.
  2. After the `_is_scoping_boilerplate` guard (L635-636) and before the `results.append(...)` at L643, add:
     ```python
     if _looks_like_refusal(observation):
         logger.debug(
             "Fix A (#2016) dropped JSON-branch observation: category=%s preview=%r",
             category, observation[:60],
         )
         continue
     ```
     `_parse_categorized_observations(raw_text)` takes only `raw_text` — `agent_id`/`session_id` are **not** in scope here (verified against the signature at L589), so the log records category + a 60-char preview only, not the agent_id. Frame it as the invariant "all parse paths apply the audit's per-record predicate"; the line-based fallback (L662) already satisfies it. The debug line makes an over-drop of a legitimate observation diagnosable (this silent-drop blind spot is what kept the cluster misdiagnosed four times).
- **Fix B** (`reflections/memory/memory_quality_audit.py` `_find_open_audit_issue`, L541-586) — **loop restructuring, not a one-line `--state` swap.** The current loop returns the FIRST title-prefix match and short-circuits on `--state open`, so a naive "change `--state all` + read `closedAt`" would return a stale-closed sibling (#1497/#1786/#1931) and suppress filing **forever** (a silent 5th recurrence, strictly worse than today). Required end-state:
  1. Add module constant `CLUSTER_REFILE_SUPPRESSION_DAYS = 14`.
  2. Change `gh issue list --state open` → `--state all`; extend `--json` from `number,title` to `number,title,state,closedAt` (`state` is required because `gh` emits an empty/zero `closedAt` for open issues).
  3. Scan **all** returned issues (no early return on first match). For each title-prefix match:
     - If `state == "OPEN"` (or `closedAt` empty/missing) → this is an open dup → return `issue["number"]` (positive int ⇒ suppress).
     - If closed: guard empty `closedAt`; parse tz-aware with `datetime.fromisoformat(closed_at.replace("Z", "+00:00"))` and compare against `datetime.now(timezone.utc)` (**never** naive `datetime.now()`). If `(now - closed_at) <= timedelta(days=CLUSTER_REFILE_SUPPRESSION_DAYS)` → in-window closed dup → return `issue["number"]` (positive int ⇒ suppress).
     - Otherwise (closed beyond window) → not a suppressing dup; keep scanning.
  4. After the loop, if no open-or-in-window match was found → return `None` (⇒ caller files). An in-window closed match MUST return a **positive int**, never `None` (which files) and never `-1` (which misroutes to the gh-failure branch).
  5. Preserve the `-1` sentinel on `gh` failure. Caller contract (`_file_anomaly_issue` L605-610) already handles positive-int-or-`-1` ⇒ suppress, `None` ⇒ file — no caller change needed.
  - **Naming decision:** rename `_find_open_audit_issue` → `_find_recent_audit_issue`, since it now returns open-OR-recently-closed hits and the old name is semantically stale. Update the single caller at L605 and the test anchors that patch it (`test_reflections_memory.py` L1148, L1171, L1210, L1258). (If the builder judges the rename churn not worth it, keeping the old name is acceptable **only** with an updated docstring; the behavioral contract above is the hard requirement.)
- **Fix B text** (`memory_quality_audit.py` issue-body template ~L114): reword the "close to suppress" line to state the 14-day window explicitly ("closing this issue suppresses re-filing for 14 days; it will re-surface if the anomaly persists past then").
- Keep both changes narrow and behavior-preserving outside the targeted vectors (no new false-drops of legitimate observations; no suppression of genuinely-new distinct anomalies).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_find_open_audit_issue`/`_find_recent_audit_issue` already wraps `gh` in try/except returning the `-1` sentinel — add/keep a test asserting the sentinel path still suppresses filing when `gh` fails after the `--state all` change.
- [ ] Fix A's `_looks_like_refusal(observation)` and the existing `_is_scoping_boilerplate(observation)` are only reached **after** the new `isinstance(observation, str)` type guard, so no `AttributeError` can escape the JSON branch. With the type guard in place, `_looks_like_refusal` is pure and total for its input, so Fix A adds no new exception handler. Add a test: a JSON item whose `observation` value is a dict/list → the batch does not raise and the non-string item is skipped while sibling string observations survive.

### Empty/Invalid Input Handling
- [ ] Confirm `_looks_like_refusal("")` returns False (existing behavior) so the new JSON-branch guard never drops empty-but-already-length-checked values incorrectly; the `isinstance` guard and the `len(observation) < 10` guard both run first regardless.
- [ ] Add a test: valid JSON with a shrapnel-shaped observation value → JSON branch returns it filtered out (empty or remaining real observations only).
- [ ] Add a test: valid JSON with a non-string `observation` value (dict/list) alongside a legitimate string observation → no exception, non-string dropped, string kept.

### Error State Rendering
- [ ] No user-visible UI surface. The observable outputs are (a) a Memory record not being saved and (b) a GitHub issue not being filed — both asserted directly in tests. State "no user-facing render path" and rely on the record-count / filing assertions.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` (~381+) — UPDATE: add a case asserting a valid-JSON observation whose value matches `_JSON_SHRAPNEL_RE` (e.g. `"category": "decision"`) is dropped by the JSON branch; add a case asserting a valid-JSON observation containing a refusal phrase is dropped; add a case asserting a valid-JSON item whose `observation` value is a dict/list is skipped without raising while a sibling string observation survives (Concern 1 type-guard). Keep existing passing cases as regression guards (legitimate observations must still parse).
- [ ] `tests/unit/test_reflections_memory.py::test_files_new_issue_when_no_open_dup` (line 1171) — **NO CHANGE / regression guard only.** This test **patches `_find_open_audit_issue` itself** (the function is replaced with a mock returning `None`), so the internal `--state all`/`closedAt` change is invisible to it and there is **no `gh` mock to adjust**. It continues to assert that a `None` return ⇒ a new issue is filed. If the function is renamed to `_find_recent_audit_issue`, update the patch **target path** at the test's anchors (L1148, L1171, L1210, L1258) to the new name; otherwise leave untouched.
- [ ] `tests/unit/test_reflections_memory.py` (dedup suite) — ADD (via this plan's build): `test_suppresses_refile_when_recently_closed` (matching-prefix issue closed within 14 days → returns positive int ⇒ no file) and `test_refiles_when_closed_beyond_window` (closed older than 14 days → returns `None` ⇒ files). These must **patch `asyncio.create_subprocess_exec`** (not `_find_open_audit_issue`) so the real branch runs — the mock returns JSON containing `number`, `title`, `state`, and `closedAt` so the window comparison is exercised end-to-end. Not a disposition on an existing test, but recorded here for the builder.

No other existing tests are affected — the changes are additive guards; legitimate-observation parsing and open-dup suppression behavior are preserved, which the retained existing cases assert.

## Rabbit Holes

- **Rewriting refusal detection as an LLM call** — that is #1829's scope and depends on the #1925 harness migration. Do NOT pull it in; the structural per-record filter is vocabulary-independent and sufficient here.
- **Chasing the exact junk phrasings** from the ebe79d3b session — the records are gone (per-machine Redis) and the mechanism is structural, not phrase-specific. Do not add new `_REFUSAL_PATTERNS` entries for this issue.
- **Redesigning session-id derivation** in `stop.py` so long-lived CLI sessions get fresh ids — tempting (it would break the cluster aggregation) but it is a much larger behavioral change to session identity with broad blast radius. The junk should be filtered regardless of how it aggregates; leave id derivation alone.
- **Making the suppression window configurable per-signal / adding a persistent ack store** — over-engineering. A single module-level constant is enough.

## Risks

### Risk 1: Over-broad JSON-branch filter drops legitimate observations
**Impact:** A real observation that coincidentally matches `_JSON_SHRAPNEL_RE` (a line shaped `"word": value`) or contains a refusal phrase substring would be silently dropped.
**Mitigation:** Reuse the **exact** predicate the audit already applies per-record — parity means we drop only what the audit would supersede anyway (no net new loss). The patterns are narrow, full-phrase, and guarded by existing narrowness tests (`TestRefusalPatternsNarrowness`, `TestScopingMarkersNarrowness`). Add a regression case with a legitimate code-ish observation that must survive.

### Risk 2: Closed-issue window hides a genuinely-recurring distinct problem
**Impact:** If a different real regression reuses the same title prefix, or Fix A itself regresses, the suppression window could mask it for the window's duration.
**Mitigation:** The window is **14 days**, deliberately set **below** the cluster's own observed inter-arrival cadence (4 filings in 7 weeks ≈ one per ~2 weeks). This is a historical basis, not the overconfident "Fix A removed the vector so we can suppress blindly" premise that mis-closed #1786/#1931 — a regressed Fix A re-surfaces within one natural cycle rather than being hidden longer than the anomaly's own period. The window is bounded (re-surfaces after 14 days if still occurring); the dedup keys on the audit-controlled title prefix, which — verified against `memory_quality_audit.py:350`, `signal_name = f"agent-id-cluster-{aid_suffix}"` — is specific to one agent_id cluster, so a distinct cluster carries a distinct prefix and is not masked.

## Race Conditions

The audit already documents a memory-dedup race for Layer 1 supersede (re-reads `superseded_by` before write). Neither Fix A (pure parse-path filter, no shared state) nor Fix B (read-only `gh` query feeding a filing decision) introduces new shared-mutable-state hazards. The dup-check + create sequence is inherently best-effort and idempotent-by-title; a concurrent audit run on another machine could still double-file in the same minute, which is pre-existing and out of scope. No new race conditions identified.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1829] LLM-based refusal-detector complement to `_REFUSAL_PATTERNS` — already filed; depends on the #1925 harness migration.
- [SEPARATE-SLUG #1925] Removing `claude_code_sdk` / harness migration — already filed; unrelated infrastructure.

## Update System

No update system changes required. Both edits are internal Python guard logic in already-deployed modules (`agent/memory_extraction.py`, `reflections/memory/memory_quality_audit.py`); they propagate through the normal `/update` git pull with no new dependencies, config files, or migrations. No Popoto schema change (Memory model untouched).

## Agent Integration

No agent integration required. This is a bridge-and-reflection-internal change: the memory extractor runs inside the Stop hook / worker, and the audit runs as a scheduled reflection. No new CLI entry point, no `.mcp.json` change, no bridge import. The agent's existing `memory_search` / `memory_get` MCP tools are unaffected (they read the same Memory store, which will simply contain fewer junk records).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` — in the memory-extraction / consolidation section, document the per-record save-time filter invariant (all parse paths apply the audit's `_looks_like_refusal` predicate) and the audit's closed-issue re-file suppression window.
- [ ] Update `docs/features/reflections.md` (or the memory-quality-audit subsection) to note the `CLUSTER_REFILE_SUPPRESSION_DAYS` behavior so operators know closing a cluster issue now suppresses re-filing for the window.

### Inline Documentation
- [ ] Comment the new JSON-branch guard tying it to this issue and the whole-text-vs-per-record asymmetry it closes.
- [ ] Comment the `_find_open_audit_issue` (renamed `_find_recent_audit_issue`) window logic and the new constant, referencing #2016 and the #1497/#1786/#1931 recurrence history.

## Success Criteria

- [ ] JSON-branch of `_parse_categorized_observations` drops shrapnel-shaped and refusal-phrase observation values (new unit tests pass).
- [ ] A legitimate observation that resembles code/config still parses and is saved (regression test passes).
- [ ] `_find_open_audit_issue` (renamed `_find_recent_audit_issue`) suppresses re-filing when a matching-prefix issue was closed within `CLUSTER_REFILE_SUPPRESSION_DAYS` (14 days); re-files when closed beyond the window (new unit tests pass).
- [ ] Audit issue-body "close to suppress" guidance text matches the implemented window behavior.
- [ ] Existing `TestParseCategorizedObservations`, refusal/scoping narrowness tests, and `test_files_new_issue_when_no_open_dup` still pass.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Lead agent orchestrates; deploys a builder + validator pair and a documentarian. Small enough that one builder handles both fixes sequentially in one branch.

### Team Members

- **Builder (extraction + audit)**
  - Name: `junk-cluster-builder`
  - Role: Implement Fix A (JSON-branch per-record filter) and Fix B (closed-issue dedup window + text) with unit tests.
  - Agent Type: builder
  - Domain: memory/data (Popoto Memory model, refusal predicates)
  - Resume: true

- **Validator**
  - Name: `junk-cluster-validator`
  - Role: Verify both fixes against Success Criteria, run the targeted test modules, confirm no legitimate-observation regressions.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `junk-cluster-doc`
  - Role: Update `docs/features/subconscious-memory.md` and `docs/features/reflections.md`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Fix A — JSON-branch per-record filter
- **Task ID**: build-extraction-filter
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations`
- **Assigned To**: `junk-cluster-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `_parse_categorized_observations` JSON branch, after `observation = item.get("observation", "")` (L630) and **before** the `len < 10` check, add `if not isinstance(observation, str): continue` (Concern 1 — prevents an `AttributeError` on a dict/list value that the surrounding `except (JSONDecodeError, TypeError)` would not catch, which would crash the whole batch).
- After the `_is_scoping_boilerplate` guard and before `results.append(...)`, add `if _looks_like_refusal(observation): continue` preceded by a `logger.debug("Fix A (#2016) dropped JSON-branch observation: category=%s preview=%r", category, observation[:60])` (Concern 5 — the drop must not be silent; `agent_id`/`session_id` are not in scope in this function, so log category + preview only).
- Add unit cases: shrapnel-shaped observation value dropped; refusal-phrase observation value dropped; non-string (dict/list) observation value skipped without raising while a sibling string survives; legitimate code-ish observation preserved.
- Comment the guards referencing #2016 and the whole-text-vs-per-record asymmetry they close.

### 2. Fix B — closed-issue dedup window
- **Task ID**: build-audit-dedup
- **Depends On**: none
- **Validates**: `tests/unit/test_reflections_memory.py` dedup suite
- **Assigned To**: `junk-cluster-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `CLUSTER_REFILE_SUPPRESSION_DAYS = 14` constant (Concern 2 — below the ~2-week observed inter-arrival so a regressed Fix A re-surfaces within one cycle).
- Restructure `_find_open_audit_issue` (rename to `_find_recent_audit_issue`; update caller L605 and test patch anchors L1148/1171/1210/1258): change `gh issue list` to `--state all`, extend `--json` to `number,title,state,closedAt`, scan **all** matches (no early return on first), and return a **positive int** when a match is open (`state == "OPEN"` or empty `closedAt`) OR closed within the window; return `None` when the only matches are closed beyond the window; preserve `-1` on `gh` failure. Guard empty `closedAt`; parse tz-aware (`datetime.fromisoformat(s.replace("Z","+00:00"))`) and compare to `datetime.now(timezone.utc)`. (BLOCKER — a naive first-match return would suppress filing forever on a stale-closed sibling.)
- Update the issue-body "close to suppress" guidance text to name the 14-day window.
- Add `test_suppresses_refile_when_recently_closed` and `test_refiles_when_closed_beyond_window`, both patching `asyncio.create_subprocess_exec` (returning JSON with `state`+`closedAt`) to exercise the real branch (Concern 3). Leave `test_files_new_issue_when_no_open_dup` behavior unchanged — it patches the function itself and needs no `gh` mock; only update its patch target path if the function is renamed.

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-extraction-filter, build-audit-dedup
- **Assigned To**: `junk-cluster-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_extraction.py tests/unit/test_reflections_memory.py -q`.
- Confirm all Success Criteria; confirm no legitimate-observation regression.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: `junk-cluster-doc`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` and `docs/features/reflections.md` per the Documentation section.

### 5. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: `junk-cluster-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run full targeted suite + ruff; verify docs updated; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Extraction tests pass | `pytest tests/unit/test_memory_extraction.py -q` | exit code 0 |
| Audit/dedup tests pass | `pytest tests/unit/test_reflections_memory.py -q` | exit code 0 |
| JSON branch filters observations | `grep -n "_looks_like_refusal(observation)" agent/memory_extraction.py` | output contains _looks_like_refusal(observation) |
| Dedup queries all states | `grep -n "state.*all\|CLUSTER_REFILE_SUPPRESSION_DAYS" reflections/memory/memory_quality_audit.py` | output contains CLUSTER_REFILE_SUPPRESSION_DAYS |
| Lint clean | `python -m ruff check agent/memory_extraction.py reflections/memory/memory_quality_audit.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/memory_extraction.py reflections/memory/memory_quality_audit.py` | exit code 0 |
| No new stale xfails | `grep -rn 'xfail' tests/unit/test_memory_extraction.py tests/unit/test_reflections_memory.py` | exit code 1 |

## Critique Results

**Verdict: NEEDS REVISION** (1 blocker, 5 concerns, 1 nit) — FULL war room (Risk & Robustness, Scope & Value, History & Consistency), run 2026-07-11. **Revision applied 2026-07-11** — all findings resolved (see below); `revision_applied: true`.

| Severity | Critic | Finding | Resolution |
|----------|--------|---------|------------|
| BLOCKER | Risk & Robustness (Adversary) | `--state all` first-match return would suppress filing forever on a stale-closed sibling (#1497/#1786/#1931). | Technical Approach Fix B rewritten as a full loop restructuring: scan **all** matches; return a positive int only when open OR closed-within-window; return `None` when only closed-beyond-window; add `state`+`closedAt` to `--json`; guard empty `closedAt`; parse tz-aware and compare to `datetime.now(timezone.utc)`. Caller contract documented. |
| CONCERN | Risk & Robustness (Skeptic) | Non-string `observation` value → `AttributeError` uncaught by `except (JSONDecodeError, TypeError)` → whole batch lost. | Fix A adds `if not isinstance(observation, str): continue` immediately after `observation = item.get(...)`, before the length check. Test + Failure-Path coverage added. |
| CONCERN | History & Consistency (Archaeologist) | 30-day window exceeds the cluster's ~2-week recurrence cadence; a regressed Fix A hidden longer than its natural period. | `CLUSTER_REFILE_SUPPRESSION_DAYS` set to **14** everywhere (Solution, Technical Approach, Risks, Steps). Resolves Open Question #2. |
| CONCERN | History & Consistency (Consistency) | Test Impact told the builder to adjust a `gh` mock on `test_files_new_issue_when_no_open_dup`, but that test patches the function itself — no mock to adjust. | Test Impact corrected: that test needs no `--state` change; new coverage routed to `test_suppresses_refile_when_recently_closed` / `test_refiles_when_closed_beyond_window`, which patch `asyncio.create_subprocess_exec`. |
| CONCERN | History & Consistency (Consistency) | Plan named `_dup_check`; real symbol is `_find_open_audit_issue`. | Global replace to `_find_open_audit_issue` in all symbol references; explicit rename decision to `_find_recent_audit_issue` recorded, with caller (L605) and test anchors (L1148/1171/1210/1258). In-window closed match returns a positive int. |
| CONCERN | Risk & Robustness (Operator) | Fix A's silent `continue` drop emits no log — the blind spot behind four misdiagnoses. | Fix A adds a `logger.debug` (category + preview) before the `continue`; `agent_id`/`session_id` confirmed out of scope in `_parse_categorized_observations(raw_text)`, so log category/preview only. |
| NIT | Scope & Value (User) | Fix B leaned on speculative "future/legacy producer" framing. | Reframed around the demonstrated 3× wasted-filing history (#1497/#1786/#1931); noted explicitly that Fix B changes re-file behavior for **every** memory-audit signal, not only ebe79d3b. |

*Investigated and cleared (not a defect):* Scope & Value flagged that Fix B might mask a distinct new cluster if `signal_name` were a shared signal-type. Verified against `memory_quality_audit.py:350` — `signal_name = f"agent-id-cluster-{aid_suffix}"` is cluster-specific, so the title prefix uniquely identifies one agent_id cluster.

### Re-Critique Addendum (2026-07-11, FULL war room)

**Verdict: READY TO BUILD (with concerns)** — 0 blockers, 2 concerns, 1 nit. The prior revision's resolutions all hold; these two concerns are narrower edge cases the earlier round did not cover. `plan_revising` was intentionally NOT set (the plan already carries `revision_applied: true`); the builder must honor these Implementation Notes during BUILD.

| Severity | Critic | Finding | Implementation Note (builder must apply) |
|----------|--------|---------|------------------------------------------|
| CONCERN | Risk & Robustness (Adversary) | Fix A's `isinstance(observation, str)` guard protects only `observation`. The sibling line `category = item.get("category", "").lower()` runs **before** it (memory_extraction.py L629), so a `{"category": null, ...}` or non-string-category item raises the same uncaught `AttributeError` that crashes the whole batch — falsifying the plan's "no `AttributeError` can escape the JSON branch" claim. | Fetch category raw first and type-guard it before `.lower()`: `category_raw = item.get("category", "")`; `if not isinstance(category_raw, str): continue`; then `category = category_raw.lower()`. Add a test: a JSON item with `"category": null` (or dict/list) alongside a legitimate sibling — batch must not raise, malformed item skipped, sibling survives. |
| CONCERN | Risk & Robustness (Adversary) | Fix B Technical Approach step 3 bullet 1 ORs "`state == "OPEN"`" with "`closedAt` empty/missing" **unconditionally**, so a `CLOSED` issue with an empty/null `closedAt` (gh data anomaly) routes to the "return positive int, suppress forever" path — reproducing the permanent-suppression failure the resolved BLOCKER guarded against, via a data edge instead of a naive first-match bug. | Use non-overlapping branches keyed on state: `if state == "OPEN": return issue["number"]`; `elif state == "CLOSED" and closed_at: <tz-aware window compare>`; `else: continue` — a `CLOSED` issue with falsy `closed_at` falls through to "keep scanning" (non-suppressing), never to permanent suppression. |
| NIT | Risk & Robustness (Operator) | Fix A's drop breadcrumb uses `logger.debug`, typically off in production, so the observability gap behind four historical misdiagnoses may persist in practice. | Consider `logger.info` for Fix A drops, or note in docs that debug logging must be enabled when investigating this signal. (NIT — non-blocking.) |

*Scope & Value* and *History & Consistency* returned **No findings** on re-critique — both independently re-verified all cited file:line anchors against source with no drift, and confirmed Fix A closes the whole-text-vs-per-record asymmetry and Fix B avoids the prior BLOCKER's first-match trap.

---

## Resolved Decisions

All prior open questions are decided; nothing blocks build.

1. **Version-skew (residual recon uncertainty):** **Resolved — ship Fix A + Fix B without chasing it.** Fix A closes the structural gap regardless of producer version, and Fix B on the auditor stops the churn regardless of which version produced the records. No "audit and producer share a version" invariant is added; a cross-machine version-skew concern, if it ever proves real, is a separate issue.
2. **Suppression window length:** **Resolved — `CLUSTER_REFILE_SUPPRESSION_DAYS = 14`.** Chosen on a historical basis: below the cluster's observed ~2-week inter-arrival (4 filings in 7 weeks), so a regressed Fix A re-surfaces within one natural cycle rather than being hidden.
3. **Scope (own both A and B, or split):** **Resolved — keep both in this plan.** They are small and related; B alone leaves the store dirty, A alone leaves the churn mechanism latent. The demonstrated 3× wasted-filing history is sufficient standalone justification for B.
