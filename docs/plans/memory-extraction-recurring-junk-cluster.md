---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2016
last_comment_id:
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
- **Interface changes**: none to public signatures. `_parse_categorized_observations` gains an internal per-observation filter in its JSON branch; `_dup_check` gains a closed-issue-window check. Both are internal.
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

- **Per-record save-time filter (JSON branch)**: In `_parse_categorized_observations`, run every parsed observation value through `_looks_like_refusal` (and keep the existing `_is_scoping_boilerplate` check) before appending — the exact predicate the audit applies per-record. This makes save-time and audit-time agree, closing the shrapnel-in-valid-JSON vector.
- **Closed-issue suppression window (audit dup-check)**: In `_dup_check`, treat a matching-title-prefix issue that is OPEN **or** CLOSED within a bounded window (default 30 days) as a duplicate, suppressing re-filing. This makes "close the issue to stop re-filing" actually true for the window, while still re-surfacing a genuinely persistent anomaly afterward.
- **Truthful guidance text**: update the audit's issue-body template so its "close to suppress" note matches the new behavior (names the window).

### Flow

Session ends → Haiku extraction → **JSON branch filters each observation with `_looks_like_refusal`** → shrapnel dropped, only real observations saved → audit finds nothing to supersede from this vector → no issue filed.

Independently: audit detects a real anomaly → **dup-check consults open + recently-closed issues** → if acknowledged (closed) within the window, suppress; else file.

### Technical Approach

- **Fix A** (`agent/memory_extraction.py`, JSON branch ~626-643): add `if _looks_like_refusal(observation): continue` alongside the existing length and `_is_scoping_boilerplate` guards. Frame it as the invariant "all parse paths apply the audit's per-record predicate"; the line-based fallback already satisfies it.
- **Fix B** (`reflections/memory/memory_quality_audit.py` `_dup_check` ~558-586): change the `gh issue list` to `--state all` (or add a second closed-state query), parse `closedAt`, and return a duplicate hit when a matching-prefix issue is open, or closed within `CLUSTER_REFILE_SUPPRESSION_DAYS` (new constant, default 30). Preserve the `-1` failure sentinel semantics.
- **Fix B text** (`memory_quality_audit.py` issue-body template ~114): reword the "close to suppress" line to state the window explicitly.
- Keep both changes narrow and behavior-preserving outside the targeted vectors (no new false-drops of legitimate observations; no suppression of genuinely-new distinct anomalies).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_dup_check` already wraps `gh` in try/except returning the `-1` sentinel — add/keep a test asserting the sentinel path still suppresses filing when `gh` fails after the `--state all` change.
- [ ] The JSON-branch filter addition introduces no new exception handler; `_looks_like_refusal` is pure and total. State "no new exception handlers in scope" for Fix A.

### Empty/Invalid Input Handling
- [ ] Confirm `_looks_like_refusal("")` returns False (existing behavior) so the new JSON-branch guard never drops empty-but-already-length-checked values incorrectly; the `len(observation) < 10` guard runs first regardless.
- [ ] Add a test: valid JSON with a shrapnel-shaped observation value → JSON branch returns it filtered out (empty or remaining real observations only).

### Error State Rendering
- [ ] No user-visible UI surface. The observable outputs are (a) a Memory record not being saved and (b) a GitHub issue not being filed — both asserted directly in tests. State "no user-facing render path" and rely on the record-count / filing assertions.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` (~381+) — UPDATE: add a case asserting a valid-JSON observation whose value matches `_JSON_SHRAPNEL_RE` (e.g. `"category": "decision"`) is dropped by the JSON branch; add a case asserting a valid-JSON observation containing a refusal phrase is dropped. Keep existing passing cases as regression guards (legitimate observations must still parse).
- [ ] `tests/unit/test_reflections_memory.py::test_files_new_issue_when_no_open_dup` (line 1171) — UPDATE: the dup-check now queries `--state all`; adjust the mock so this "no dup" case has no matching-prefix issue in any state, preserving the assertion that a new issue is filed.
- [ ] `tests/unit/test_reflections_memory.py` (dup-check suite) — ADD (via this plan's build): `test_suppresses_refile_when_recently_closed` (closed within window → no file) and `test_refiles_when_closed_beyond_window` (closed older than window → files). Not a disposition on an existing test, but recorded here for the builder.

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
**Impact:** If a different real regression reuses the same title prefix, a 30-day window could mask it.
**Mitigation:** The window is bounded (re-surfaces after 30 days if still occurring); Fix A independently removes the producing vector so no new junk should arise; the dup-check keys on the audit-controlled title prefix which is specific to one agent_id cluster.

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
- [ ] Comment the `_dup_check` window logic and the new constant, referencing #2016 and the #1497/#1786/#1931 recurrence history.

## Success Criteria

- [ ] JSON-branch of `_parse_categorized_observations` drops shrapnel-shaped and refusal-phrase observation values (new unit tests pass).
- [ ] A legitimate observation that resembles code/config still parses and is saved (regression test passes).
- [ ] `_dup_check` suppresses re-filing when a matching-prefix issue was closed within `CLUSTER_REFILE_SUPPRESSION_DAYS`; re-files when closed beyond the window (new unit tests pass).
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
- In `_parse_categorized_observations` JSON branch (~626-643), add `if _looks_like_refusal(observation): continue` beside the existing length and `_is_scoping_boilerplate` guards.
- Add unit cases: shrapnel-shaped observation value dropped; refusal-phrase observation value dropped; legitimate code-ish observation preserved.
- Comment the guard referencing #2016 and the asymmetry it closes.

### 2. Fix B — closed-issue dedup window
- **Task ID**: build-audit-dedup
- **Depends On**: none
- **Validates**: `tests/unit/test_reflections_memory.py` dup-check suite
- **Assigned To**: `junk-cluster-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `CLUSTER_REFILE_SUPPRESSION_DAYS = 30` constant.
- Change `_dup_check` `gh issue list` to `--state all`, request `closedAt`, and return a dup hit for open issues or issues closed within the window; preserve the `-1` sentinel on `gh` failure.
- Update the issue-body "close to suppress" guidance text to name the window.
- Update `test_files_new_issue_when_no_open_dup` mock for `--state all`; add `test_suppresses_refile_when_recently_closed` and `test_refiles_when_closed_beyond_window`.

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

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Version-skew (residual uncertainty from recon):** could the producing dev machine be running an older `_REFUSAL_PATTERNS`/parser than the auditing machine, such that the audit supersedes records the local extractor legitimately couldn't recognize? Fix A closes the structural gap regardless, so the plan does not depend on the answer — but if version-skew is real, an additional "audit and producer share a version" invariant might be worth a separate note. Confirm we are content to ship Fix A + Fix B without chasing this.
2. **Suppression window length:** is 30 days the right `CLUSTER_REFILE_SUPPRESSION_DAYS`? Shorter (e.g. 14) re-surfaces faster if a fix regresses; longer stays quieter. Default proposed: 30.
3. **Scope confirmation:** should this plan own both the producing-defect fix (A) and the churn fix (B), or split B into its own issue? Recommendation: keep both — they are small, related, and B alone would keep the store dirty while A alone would leave the churn mechanism latent for any future/legacy producer.
