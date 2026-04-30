---
status: Ready
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-04-30
tracking: https://github.com/tomcounsell/ai/issues/1212
last_comment_id: IC_kwDOEYGa088AAAABAwQnJw
revision_applied: true
---

# Memory Extraction — JSON Shrapnel & Refusal Prose Fix

## Problem

The `/memories` dashboard tab on the bridge machine shows ~173 records, and a large fraction of them are corrupt. Two distinct symptoms, one root cause: `agent/memory_extraction.py::_parse_categorized_observations` is too strict on the JSON path and too permissive on the line-based fallback path.

**Current behavior:**

1. **JSON shrapnel.** When the Haiku LLM wraps its JSON response in markdown code fences (` ```json ... ``` `) or adds a preamble, strict `json.loads` raises. The `except` falls through to a line-based parser that splits `raw_text` on `\n` and saves every line >10 chars as a separate `Memory` record. One real observation explodes into 4-5 rows holding literal JSON syntax fragments like `"tags": ["session-management", "context-handling"]`.
2. **Refusal prose stored as memories.** When `extract_observations_async` is called with input that passes the 50-char guard but isn't a real agent response (session headers, placeholders, truncated transcripts), Haiku produces refusal text ("There is no agent session response to analyze…", "Please provide the session…", "**Rationale:** The response contains no novel observations…"). The line-based fallback persists each refusal sentence as its own Memory.

Both symptoms are reproducible on the local machine — see Recon Summary in [#1212](https://github.com/tomcounsell/ai/issues/1212) for the cited record IDs (`5be7da58…`, `76dbd772…`, `1540c270…`, `796e1429…`, `c219982fbf9746f9a3ff9b09b042faa6`).

**Desired outcome:**

- The parser tolerates code fences and preamble — extracts the JSON payload, parses it, and short-circuits the line-based fallback whenever ≥1 valid observation is recovered.
- Empty / placeholder / refusal input never reaches the LLM, and refusal text never reaches the Memory store even if it does.
- Existing junk records (~50-100 estimated based on dashboard observations) are marked `superseded_by="cleanup-junk-extraction"` by a one-shot script, not deleted.
- New extraction runs do not regenerate the junk pattern.

## Freshness Check

**Baseline commit:** `32bb1f5297d254c9203e828934422a9e6bcaafe5`
**Issue filed at:** `2026-04-29T16:19:19Z`
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/memory_extraction.py:288` — `_parse_categorized_observations` definition — still holds.
- `agent/memory_extraction.py:298` — `data = json.loads(raw_text)` — still holds (strict parse).
- `agent/memory_extraction.py:320-321` — `except (json.JSONDecodeError, TypeError): pass` — still holds (silent fall-through).
- `agent/memory_extraction.py:323-349` — line-based fallback — still holds (splits on `\n`, saves lines >10 chars).
- `agent/memory_extraction.py:190` — `len(response_text.strip()) < 50` short-response guard — still holds.
- `agent/memory_extraction.py:249` — `agent_id=f"extraction-{session_id}"` — still holds (gives cleanup script a precise selector).
- `tests/unit/test_memory_extraction.py:264` — `test_json_array_parsing` — exists, passes for clean JSON.
- `tests/unit/test_memory_extraction.py:307` — `test_json_malformed_falls_back_to_line_parser` — exists, but only asserts no crash, not that the fallback rejects refusal text.

**Cited sibling issues/PRs re-checked:**
- PR #1198 (dashboard `/memories` tab) — merged 2026-04-28T20:09:10Z. Made the bug visible, otherwise unrelated.
- PR #1056 (event loop hotfix) — merged 2026-04-20T02:47:50Z. Centralized AsyncAnthropic call site (`_llm_call`). Unrelated to parsing logic.
- PR #1117 / #1120 (AsyncAnthropic refactor + semaphore) — both merged. Don't touch the parser.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-29T16:19:19Z" -- agent/memory_extraction.py tests/unit/test_memory_extraction.py docs/features/subconscious-memory.md` returned empty.

**Active plans in `docs/plans/` overlapping this area:** None. Searched `grep -r "memory_extraction\|memory-extraction" docs/plans/` — no live plans touching this module.

**Notes:** The issue's claims and file:line references are all accurate. No drift.

**Comment-driven update (`IC_kwDOEYGa088AAAABAwQnJw`, 2026-04-29 by tomcounsell):**
- **Live reproduction confirmed**: a single Haiku call on a real-shaped session response produced **10 separate Memory rows** of literal JSON lines. JSON-shrapnel symptom is reliably reproducible end-to-end on the live LLM path.
- **50-char pre-LLM check IS firing correctly**: empty-string input returns 0 observations as expected. The existing junk records (`5be7da58…`, etc.) came from sessions with **low-content but above-threshold** input where the LLM produced refusal prose despite passing the length check. The fix must NOT weaken the 50-char check.
- **Corpus state**: 11 test-pollution records were deleted during verification (6 by memory_id prefix + 5 by `agent_id="extraction-test-real-session*"`). Corpus is back to 173 records — same figure cited in the issue body.
- **Tom's recommendation on refusal handling**: of three options — (a) raise the length threshold, (b) add a substantive-content heuristic, (c) post-LLM refusal-pattern filtering — **option (c) is the strongest** and should be the primary defense. (a) and (b) are supporting layers, not primary.
- **Sibling issues filed during verification**: #1213 (recall has no relevance threshold), #1214 (embedding directory leaks orphan `.npy` files). Both out of scope for this plan; tracked for future work.

## Prior Art

Searched `gh issue list --state closed --search "memory extraction parsing"` and `gh pr list --state merged --search "memory extraction"`. Findings:

- **PR #1056 (Memory extraction hotfix — Layers 1+2, merged 2026-04-20)**: Fixed event-loop blocking by replacing sync `anthropic.Anthropic` with async, added double-timeout. Touched the same file but the parsing bug is orthogonal.
- **PR #1117 (centralize AsyncAnthropic client, merged 2026-04-22)**: Extracted the `_llm_call` helper. Did not touch the parser.
- **PR #1120 (shared AsyncAnthropic semaphore, merged 2026-04-22)**: Concurrency budget. Orthogonal.
- **PR #1114 (Popoto v1.5.0 integration, merged 2026-04-22)**: Added "used" outcome to `_judge_outcomes_llm`. Orthogonal.
- **PR #584 / #593 (memory retrieval enhancements, 2026-03)**: Earlier metadata-aware recall. Did not touch extraction parser.
- **PR #524 (intentional memory saves, 2026-03)**: Added `tools.memory_search save`. Orthogonal — that path doesn't go through `_parse_categorized_observations`.
- **#583, #613 (memory retrieval / outcome tracking)**: Closed. Orthogonal.

No prior PR has touched `_parse_categorized_observations`'s parsing logic since the function was written. The parser is original code — there is no failed-fix history to study. The bug is a first-time fix, not a regression.

## Research

**Queries used:**
- "extract JSON from LLM output strip markdown code fences python 2026"

**Key findings:**
- The `llm-json` library ([github.com/altryne/llm-json](https://github.com/altryne/llm-json)) ignores extraneous text before/after code fences and parses the first valid snippet. Equivalent functionality can be implemented in ~20 lines without adding a dependency.
- The `json_repair` library ([github.com/mangiucugna/json_repair](https://github.com/mangiucugna/json_repair)) repairs malformed JSON (missing quotes, commas, brackets, stray prose, truncated values). More robust than what we need — for our case, the LLM output is well-formed JSON wrapped in fences or preamble, not malformed JSON.
- Industry consensus (DEV Community 2026 article on LLM Structured Output): "stop parsing JSON with regex" — use the provider's native JSON mode. This is captured under "Out of Scope" in the issue (re-architecting to structured-output API is deferred).
- Manual approach (canonical pattern): strip ` ``` ` / ` ```json ` markers, slice to outermost `[...]` or `{...}`, then `json.loads`. This is the pattern we'll implement — no new dependency.

**How findings inform the plan:** We will implement the manual extract-then-parse pattern inside `_parse_categorized_observations` rather than adding `llm-json` or `json_repair` as a dependency. The change is ~15 lines of code, fully testable, and stays within the issue's "no re-architecture" boundary.

## Data Flow

1. **Entry point:** Worker finishes a session → `agent/session_executor.py` calls `run_post_session_extraction(session_id, response_text, project_key)` (line 805).
2. **Pre-LLM guard** (line 190): `extract_observations_async` rejects responses < 50 chars (`return []`). **GAP:** does not catch responses ≥ 50 chars that are placeholder/header text rather than real session output.
3. **LLM call** (lines 207-217): Haiku is called with `EXTRACTION_PROMPT` + truncated response. Returns `raw_text`.
4. **Empty-response check** (line 228): `if raw_text.upper() == "NONE" or not raw_text: return []`. **GAP:** does not catch refusal prose like "There is no agent session response…".
5. **Parse** (line 233): `_parse_categorized_observations(raw_text)`.
   - **JSON path** (lines 297-321): `json.loads(raw_text)` strict. Returns 3-tuples on success. **GAP:** fails on code-fenced or preamble-wrapped JSON.
   - **Line-based fallback** (lines 323-349): splits `raw_text.split("\n")`, keeps lines >10 chars, saves each. **GAP:** persists JSON syntax lines and refusal sentences as Memory records.
6. **Save** (lines 246-262): `Memory.safe_save(agent_id=f"extraction-{session_id}", content=obs_content[:500], importance=…)`. Records persist in Redis with `superseded_by=""` (active).
7. **Output:** Memories surface in dashboard `/memories` tab and recall via `agent/memory_hook.py`.

The fix targets steps 2 (raise threshold + refusal pattern check pre-LLM), 4 (refusal pattern check post-LLM), and 5 (tolerant JSON extraction → no fall-through if JSON path yielded ≥1 result). Step 6's `agent_id` prefix is the cleanup script's selector for existing junk.

## Architectural Impact

- **New dependencies:** None. Manual JSON extraction pattern, no new libraries.
- **Interface changes:** None. `_parse_categorized_observations` signature unchanged. `extract_observations_async` signature unchanged.
- **Coupling:** Unchanged.
- **Data ownership:** Unchanged. Cleanup script writes to existing `superseded_by` field used by `memory-dedup`.
- **Reversibility:** High. Cleanup uses `superseded_by="cleanup-junk-extraction"` — clearing that field re-activates records. Parser changes are localized to one function and one pre-LLM guard, easy to revert.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (issue is well-scoped, recon resolved both open questions)
- Review rounds: 1 (standard PR review)

The change is bounded: one parser function (~15 lines added/modified), one pre-LLM guard (~10 lines added), one new test class (~6 tests), one new cleanup script (~80 lines), one doc edit. No spikes — the parsing pattern is well-documented and the cleanup pattern follows `scripts/memory_consolidation.py`.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Anthropic API key | `python -c "from utils.api_keys import get_anthropic_api_key; assert get_anthropic_api_key()"` | Real-API integration test for parser robustness against actual Haiku output |
| Redis with Memory records | `python -m tools.memory_search status` | Cleanup script needs a populated Memory store to exercise apply mode |

Run all checks: `python scripts/check_prerequisites.py docs/plans/memory-extraction-shrapnel-fix.md`

## Solution

### Key Elements

**Per the comment on issue #1212 (`IC_kwDOEYGa088AAAABAwQnJw`), the primary defense for refusal-prose handling is post-LLM refusal-pattern filtering (option c). Pre-LLM substantive-content guards (option b) and threshold tuning (option a) are supporting layers, not the primary fix.** The 50-char check is empirically working and must remain unchanged.

- **Tolerant JSON extractor (`_extract_json_payload`)**: A new helper that strips markdown code fences and slices `raw_text` to the outermost `[...]` or `{...}` before passing to `json.loads`. Returns the cleaned string or `None` if no JSON-shaped substring exists. Pure function, easily unit-tested. **This is the primary fix for JSON-shrapnel.**
- **Post-LLM refusal-pattern filter (PRIMARY for refusal-prose, option c)**: After the Haiku call returns, before parsing, check `_looks_like_refusal(raw_text)`. If true, return `[]` immediately — no parse, no save. Also applied per-line inside the line-based fallback as a second-tier filter. This is the strongest defense because it catches refusal prose regardless of what the input looked like — including the "low-content but above-threshold" case Tom identified in the comment, where input passed the 50-char check but the LLM still returned refusal text.
- **Refusal-pattern constants (`_REFUSAL_PATTERNS` + `_looks_like_refusal` predicate)**: A short list of case-insensitive substring patterns ("there is no agent session", "no agent session response", "please provide the session", "**rationale:**", "no novel observations", "no agent session was provided", "session was initialized with empty input") plus a single-line JSON-syntax regex (`^"[a-z_]+"\s*:\s*.*,?\s*$`). **Implementation Note (concern: hard-coded substrings will drift as Haiku rephrases its refusals over time):** the patterns must live as a module-scope tuple constant (not buried in a function body) so they are trivial to extend, and each entry must carry an inline comment citing the originating Memory ID from issue #1212 (e.g., `"there is no agent session",  # 5be7da58 / 76dbd772 / 796e1429`). When a new refusal shape appears in the wild, the response is to add a pattern to the constant — not to re-architect. This is acceptable maintenance because (a) the failure mode is silent rejection of refusal text, never silent acceptance of legitimate text, and (b) the recurring `memory-dedup` reflection plus the on-demand cleanup script give us a follow-up safety net.
- **Pre-LLM substantive-content guard (SUPPORTING, options a+b)**: In `extract_observations_async`, after the existing 50-char check, also reject input that matches refusal patterns OR is dominated by whitespace/punctuation. Returns `[]` and skips the LLM call. This saves a Haiku call but is NOT the primary defense — the LLM can still return refusal prose for inputs that pass this guard, which is exactly why post-LLM filtering (above) is required.
- **JSON-path short-circuit hardening**: Re-confirm the existing `if results: return results` behavior at line 318 is preserved AFTER the tolerant extractor runs. Once the extractor pulls clean JSON out of fences, the JSON path will succeed and never fall through.
- **One-shot cleanup script (`scripts/cleanup_memory_extraction_junk.py`)**: Modeled on `scripts/memory_consolidation.py`. Iterates `Memory.query.filter(...)` for records where `agent_id` starts with `extraction-` AND content matches a refusal pattern OR JSON-line regex. Default `--dry-run` mode prints the IDs and content samples it would supersede. `--apply` mode sets `superseded_by="cleanup-junk-extraction"` and `superseded_by_rationale="auto-cleanup: refusal/json-shrapnel from issue #1212"`. Uses Popoto ORM only (per CLAUDE.md "never use raw Redis on Popoto-managed keys").

### Flow

Worker finishes session → `extract_observations_async(session_id, response_text)` → check len ≥ 50 (UNCHANGED, empirically working) → [supporting] check substantive content (no refusal patterns, not whitespace-dominant) → Haiku call → **[PRIMARY] check raw_text not refusal-shaped — if refusal, return `[]` immediately** → `_extract_json_payload(raw_text)` strips fences/slices to JSON → `json.loads` → returns 3-tuples → `Memory.safe_save` → done. Line-based fallback runs only if `_extract_json_payload` returned `None` (no JSON-shaped substring) AND the raw text is not refusal — and even then, it filters out lines matching refusal/JSON-shrapnel patterns.

The post-LLM refusal check is the **load-bearing** filter for the refusal-prose symptom. The pre-LLM check is a cost optimization (skip Haiku when input is obviously bad), not the primary defense.

### Technical Approach

- **`_extract_json_payload(raw_text: str) -> str | None`**: New module-level helper. Implementation:
  1. Strip leading/trailing whitespace.
  2. If the string starts with ` ``` ` (with optional `json` language tag), find the matching closing ` ``` ` and slice.
  3. Find the first `[` or `{` and the last `]` or `}` of matching type. Slice to that range.
  4. Return the result if non-empty, else `None`.
- **`_REFUSAL_PATTERNS: tuple[str, ...]`**: Module constant. Eight short substrings (lowercased), checked via `pattern in text.lower()`.
- **`_JSON_SHRAPNEL_RE: re.Pattern`**: Module constant. `re.compile(r'^"[a-z_]+"\s*:\s*.*,?\s*$')`.
- **`_looks_like_refusal(text: str) -> bool`**: Returns True if any refusal pattern matches OR if `_JSON_SHRAPNEL_RE` matches the stripped line.
- **`_parse_categorized_observations` modifications**:
  1. Call `_extract_json_payload(raw_text)`. If non-None, replace `raw_text` for the `json.loads` call; otherwise keep original.
  2. JSON path unchanged after extraction (still wraps bare dicts, still validates `observation` length, still returns 3-tuples).
  3. Line-based fallback: filter each line through `not _looks_like_refusal(line)` before keeping.
- **`extract_observations_async` modifications**:
  1. After the existing `len(response_text.strip()) < 50` guard, add: if `_looks_like_refusal(response_text)` OR `len(re.sub(r'\s+', '', response_text)) / len(response_text) < 0.3` (whitespace-dominant), `return []`.
  2. After the LLM call (after line 217, before parse), add: if `_looks_like_refusal(raw_text)`, log debug `"refusal text from extractor — skipping save"`, return `[]`.
  - **Implementation Note (concern: the `0.3` whitespace-dominance threshold is a magic constant with no test calibration):** define the threshold as a named module-scope constant `_MIN_NON_WHITESPACE_RATIO = 0.3` rather than inlining `0.3` at the call site. This (a) makes the guard testable against boundary inputs, (b) lets us tune the threshold from a single edit if production data later shows false positives, and (c) flags to future readers that the value is empirical rather than load-bearing. The unit test at `test_whitespace_dominant_input_skips_llm_call` MUST exercise both sides of the boundary: an input with exactly 25% non-whitespace (rejected) and 35% non-whitespace (accepted), so the constant is locked in by the test. The post-LLM refusal check is intentionally redundant with the pre-LLM check — refusal can emerge from inputs the pre-check missed (above 50 chars, above 30% non-whitespace, no refusal substring), so dual-filter is by design, not over-engineering.
- **Cleanup script structure** (`scripts/cleanup_memory_extraction_junk.py`):
  ```python
  # Iterate via Memory.query — never raw Redis (CLAUDE.md rule).
  candidates = [m for m in Memory.query.all()
                if str(m.agent_id).startswith("extraction-")
                and (m.superseded_by or "") == ""
                and (_looks_like_refusal(m.content) or _is_json_line(m.content))]
  if dry_run:
      for m in candidates: print(f"would supersede {m.memory_id}: {m.content[:80]}")
  else:
      blocked = 0
      for m in candidates:
          m.superseded_by = "cleanup-junk-extraction"
          m.superseded_by_rationale = "auto-cleanup: refusal/json-shrapnel from issue #1212"
          result = m.save()
          if result is False:
              blocked += 1
              logger.warning(f"[cleanup] WriteFilter blocked superseded_by write for {m.memory_id}")
      print(f"WriteFilter blocked {blocked} records (already-superseded race or filter veto)")
  print(f"Total: {len(candidates)} records {'would be' if dry_run else 'were'} superseded")
  ```
  CLI: `python scripts/cleanup_memory_extraction_junk.py [--dry-run|--apply]`. Default is `--dry-run`. Argparse-based.

  **Implementation Note (concern: `Memory.save()` is governed by `WriteFilterMixin` and can return `False` silently — verified at `scripts/memory_consolidation.py:298-301`):** `record.save()` may return `False` when the WriteFilter vetoes the write (e.g., a concurrent write already touched the record, or the filter's idempotency check decides the change is a no-op). The script MUST capture the return value, count blocked writes, log a warning per blocked record (matching the `memory-dedup` pattern), and report the blocked count alongside the success count in the final summary. The PR description's "count documented" acceptance criterion must report **superseded count** AND **blocked count** AND **total candidate count** — three numbers, not one. This makes the operator-facing result honest about partial success and prevents a "we superseded N records" claim that's actually `N - blocked`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/memory_extraction.py:282` — outer `except Exception as e: logger.warning(...); _record_extraction_error(...); return []` already wraps the entire `extract_observations_async` body. New code paths added inside this try block are protected. **Test:** existing `test_never_crashes` covers this.
- [ ] `_extract_json_payload` is a pure function — no IO, no exceptions raised. **Test:** unit test asserts it returns `None` on garbage input rather than raising.
- [ ] Cleanup script wraps each `m.save()` in try/except with per-record logging — failure on one record doesn't block the rest. **Test:** unit test mocks one save to raise and asserts the loop continues.

### Empty/Invalid Input Handling
- [ ] `_extract_json_payload("")` returns `None`. Unit test.
- [ ] `_extract_json_payload("   ")` returns `None`. Unit test.
- [ ] `_extract_json_payload("not json at all")` returns `None`. Unit test.
- [ ] `_looks_like_refusal("")` returns `False` (no patterns match empty). Unit test.
- [ ] `extract_observations_async` with whitespace-only 100-char input returns `[]` and does NOT call Haiku. Unit test with mocked `_llm_call`.
- [ ] `_parse_categorized_observations` with refusal-only line-fallback input returns `[]`. Unit test.

### Error State Rendering
- [ ] No user-visible UI for this fix. Memory records are internal. The `/memories` dashboard already renders cleanly — verifying junk no longer appears is a manual post-deploy check, not an automated test.

## Test Impact

- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations::test_fallback_uncategorized` — UPDATE: input "The deployment uses blue-green strategy for zero downtime" should still produce one uncategorized result, since it's NOT refusal text. Verify the new refusal filter doesn't accidentally catch it. No code change needed — assertion stays the same — but re-run to confirm.
- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations::test_json_malformed_falls_back_to_line_parser` — UPDATE: existing input `'[{"category": "correction", broken json'` is malformed JSON; with the new tolerant extractor, `_extract_json_payload` will return the same broken string (no clean slice possible), `json.loads` will still fail, fallback still runs. The fallback now filters refusal-shaped lines, but this input isn't refusal — assertion stays the same. Re-run to confirm.
- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction::test_short_response_skips` — UPDATE: covers the existing 50-char check. Add a sibling test for the new refusal-pattern pre-LLM guard.
- [ ] `tests/unit/test_memory_extraction.py::TestParseCategorizedObservations` — ADD new test cases (counts as REPLACE for the class scope):
  - `test_extracts_json_from_code_fence` — input `"```json\n[{\"category\": \"correction\", \"observation\": \"...\"}]\n```"` produces 1 valid 3-tuple, NOT 4 line-based shrapnel rows.
  - `test_extracts_json_from_prose_preamble` — input `"Here are the observations:\n[{\"category\": \"decision\", ...}]"` produces 1 valid 3-tuple.
  - `test_refusal_text_returns_empty` — input `"There is no agent session response to analyze."` returns `[]` (line fallback rejects it).
  - `test_json_shrapnel_line_rejected` — input `'"tags": ["session-management", "context-handling"]'` returns `[]`.
  - `test_json_path_short_circuits_after_extract` — verify a code-fenced JSON with 2 valid items returns exactly 2 tuples (no fall-through to line fallback adding ghost rows).
- [ ] `tests/unit/test_memory_extraction.py::TestRunPostSessionExtraction` — ADD new tests:
  - `test_refusal_input_skips_llm_call` — input ≥ 50 chars matching refusal pattern, with a mocked `_llm_call` that raises if called. Asserts `_llm_call` was never invoked.
  - `test_whitespace_dominant_input_skips_llm_call` — input with > 70% whitespace, mocked `_llm_call` raises if called.
  - `test_refusal_output_not_saved` — input is real-looking response (≥ 50 chars, no refusal), but mocked `_llm_call` returns refusal text. Asserts no `Memory.safe_save` call.
- [ ] `tests/unit/test_cleanup_memory_extraction_junk.py` — NEW file with tests for the cleanup script:
  - `test_dry_run_does_not_modify` — mocks `Memory.query.all()`, asserts no `m.save()` called.
  - `test_apply_marks_superseded` — mocks query, asserts `superseded_by` set on matching records.
  - `test_skips_already_superseded` — records with non-empty `superseded_by` are not re-touched.
  - `test_skips_non_extraction_agent_id` — records with `agent_id` not starting with `extraction-` are not touched (e.g., `post-merge`, human saves).
  - `test_per_record_save_failure_does_not_block` — one mocked save raises; loop continues.

## Rabbit Holes

- **Replacing `json.loads` with `json_repair` or `llm-json`.** Tempting because they handle even more cases, but adds a dependency for a small marginal gain. Manual extraction handles ≥99% of code-fenced/preamble cases. Defer to a future PR if real-world data shows we need it.
- **Switching to Anthropic's structured-output mode.** Explicitly out of scope per the issue. Would require a different prompt template, schema validation, and a different SDK call shape — much larger blast radius. Defer.
- **Promoting the cleanup script to a recurring nightly reflection.** Tempting for symmetry with `memory-dedup`, but unnecessary if the parser is fixed at the source. The Recon Summary already explained why: a recurring guard would only matter if pollution returns, and we'd know that from `memory.extraction` analytics. Defer until evidence justifies it.
- **Categorizing or re-categorizing surviving records.** The cleanup is binary (junk vs not), not a re-classification effort. Defer category audits to a separate plan if needed.
- **Building a regex-based JSON validator.** The existing `json.loads` after extraction is already the validator. No need to write a parser.

## Risks

### Risk 1: False positives in refusal-pattern detection
**Impact:** A legitimate observation containing the substring "no agent session" (e.g., a memory about debugging session shutdowns) is incorrectly dropped.
**Mitigation:** Patterns are deliberately narrow (full phrases like "there is no agent session", not just "no agent session"). Unit tests include positive cases that use "session" in legitimate contexts. The pattern list lives at module scope as a constant — easy to adjust if a false positive is reported.
**Implementation Note (concern: the unit-test list must explicitly include adversarial near-miss inputs to prove narrowness, not just typical legitimate inputs):** beyond the existing `test_fallback_uncategorized` regression check, add a dedicated `test_legitimate_text_with_session_substring` test in `TestParseCategorizedObservations` whose input is a real-shaped observation that contains the bare substring `"session"` and the bare substring `"no novel"` separately (e.g., `"The dev session ended cleanly with no novel observations to flag — verified at session_executor.py:805"`). This must NOT be rejected. The test locks in the narrowness property and prevents future pattern additions from accidentally widening to bare-keyword matches. If this test ever fails after a pattern edit, the editor knows their addition was too broad.

### Risk 2: Cleanup script supersedes a record that's actually valuable
**Impact:** A real observation that happens to look like a JSON line (e.g., someone literally pasted JSON into a corrected memory) gets superseded by mistake.
**Mitigation:** Default `--dry-run` mode forces a human to review the candidate list before applying. `superseded_by` is reversible (clearing the field re-activates the record). The script never deletes. Cleanup count is documented in the PR description per acceptance criterion.

### Risk 3: `_extract_json_payload` mishandles a corner case (e.g., nested code fences in the LLM output)
**Impact:** A specific Haiku output shape produces a worse parse than the current strict path — e.g., the extractor pulls out a nested fence that contains malformed inner JSON, and the line fallback then runs on the partial extract.
**Mitigation:** The extractor is purely additive — if it returns `None`, behavior is unchanged from today. If it returns a string but `json.loads` fails, behavior is also unchanged from today (line fallback runs). The worst case is "no improvement on this specific shape," not regression. Unit tests explicitly cover the nested-fence case.

### Risk 4: Pre-LLM whitespace-dominance heuristic rejects valid input
**Impact:** A short, terse but real session response (e.g., "Done. PR #1234 merged.") is misclassified as whitespace-dominant and skipped.
**Mitigation:** The heuristic is `< 30% non-whitespace`, which is permissive. "Done. PR #1234 merged." has > 80% non-whitespace. Unit tests cover terse-but-real inputs.

## Race Conditions

No race conditions identified. `_parse_categorized_observations` and `_extract_json_payload` are pure synchronous functions with no shared state. `extract_observations_async` is async but each call has its own `session_id` and `response_text` — no cross-call mutation. The cleanup script is a one-shot CLI tool, not a service; if run concurrently with the worker, both processes use Popoto's `safe_save`/`save` which serialize on Redis. The worst case is a record being marked superseded between the time a recall query loaded it and the time the recall filter checks `superseded_by` — recall would briefly include a junk record that's about to be filtered, which is harmless (it gets filtered on the next call).

## No-Gos (Out of Scope)

- Changing the extraction prompt itself.
- Re-architecting to Anthropic structured-output API.
- Promoting the cleanup script to a recurring reflection.
- Re-categorizing or merging surviving observations (that's `memory-dedup`'s job).
- Backporting to older Memory records that pre-date the `agent_id="extraction-…"` convention.
- Running the cleanup against a different `project_key` than the worker's default.

## Update System

No update system changes required. The fix is a pure code change to `agent/memory_extraction.py`, a new test file, a new script under `scripts/`, and a doc edit. No new dependencies, no config files, no new services. The cleanup script is meant to be run **once per machine** post-deploy by a human — not auto-deployed. After this lands and gets pulled via `/update`, each machine's owner runs `python scripts/cleanup_memory_extraction_junk.py --dry-run` then `--apply` if the dry-run looks correct.

## Agent Integration

No agent integration required — this is a worker-internal change. The agent doesn't directly invoke `_parse_categorized_observations` or the cleanup script via tools. The cleanup script is a human-operated CLI; a future plan could expose it via `python -m tools.memory_search cleanup` if recurring use becomes a thing, but that's deferred.

The bridge does not need to import the new code. The worker already calls `extract_observations_async` (via `run_post_session_extraction`) — the parser changes are transparent to the worker.

## Documentation

### Feature Documentation
- [x] Update `docs/features/subconscious-memory.md`:
  - Add a paragraph in the "Flow 3: Post-Session Extraction" section describing the tolerant JSON parsing (code fence stripping, payload slicing) and the refusal-pattern filter (pre-LLM guard + post-parse filter).
  - Document the `cleanup_memory_extraction_junk.py` script: when to run it, what it does, why `superseded_by` is used instead of deletion. Cross-reference `memory-dedup` for the same convention.
- [x] No new entry in `docs/features/README.md` — this is a hardening of an existing feature, not a new one.

### Inline Documentation
- [ ] Docstring on `_extract_json_payload` explaining the strip-fences, slice-to-outermost-brackets pattern.
- [ ] Module-level comment near `_REFUSAL_PATTERNS` explaining each pattern's origin (cite the Memory IDs from issue #1212).
- [ ] Docstring on `cleanup_memory_extraction_junk.py` matching the format of `scripts/memory_consolidation.py` (rationale, safety rails, manual invocation).

## Success Criteria

- [ ] `_parse_categorized_observations` strips code fences and slices to JSON before parsing (acceptance criterion 1 from issue).
- [ ] Successful JSON parse short-circuits the line-based fallback when ≥1 valid observation is recovered (acceptance criterion 2 from issue — already true; new tests confirm it survives the tolerant-extractor change).
- [ ] Empty/insufficient input is rejected before the LLM call via substantive-content threshold and refusal-pattern check (acceptance criterion 3 from issue).
- [ ] Refusal-pattern lines are rejected post-parse so the line fallback never persists them (acceptance criterion 4 from issue).
- [ ] Cleanup script runs in dry-run, prints candidate count and sample IDs; runs in apply, supersedes records; PR description documents the count removed (acceptance criterion 5 from issue).
- [ ] Unit tests cover code-fenced JSON, JSON with prose preamble, empty input, refusal-style output, JSON-shrapnel input that should NOT be saved (acceptance criterion 6 from issue).
- [x] `docs/features/subconscious-memory.md` updated with parser hardening and refusal filter description (acceptance criterion 7 from issue).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `python scripts/cleanup_memory_extraction_junk.py --dry-run` runs successfully and prints a candidate count.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

- **Builder (parser hardening)**
  - Name: `parser-builder`
  - Role: Implement `_extract_json_payload`, refusal patterns, modifications to `_parse_categorized_observations` and `extract_observations_async`.
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup script)**
  - Name: `cleanup-builder`
  - Role: Create `scripts/cleanup_memory_extraction_junk.py` modeled on `scripts/memory_consolidation.py`. Use Popoto ORM exclusively.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `parser-test-engineer`
  - Role: Add the new test cases listed in Test Impact. Verify existing tests still pass.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `memory-doc-writer`
  - Role: Update `docs/features/subconscious-memory.md` and inline docstrings.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `parser-validator`
  - Role: Run all unit tests, run the cleanup script in dry-run, verify the candidate count is non-zero on a populated machine, confirm no false positives in the candidate list.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build parser hardening
- **Task ID**: build-parser
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_extraction.py` (new test cases listed in Test Impact)
- **Informed By**: Recon Summary (root cause at line 288), Research (manual extract pattern preferred over `json_repair` dep)
- **Assigned To**: parser-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_extract_json_payload(raw_text: str) -> str | None` helper at module scope.
- Add `_REFUSAL_PATTERNS` tuple constant and `_JSON_SHRAPNEL_RE` regex constant.
- Add `_MIN_NON_WHITESPACE_RATIO = 0.3` named constant for the whitespace-dominance threshold.
- Add `_looks_like_refusal(text: str) -> bool` predicate.
- Modify `_parse_categorized_observations`: call `_extract_json_payload` before `json.loads`; filter line-based fallback through `not _looks_like_refusal`.
- Modify `extract_observations_async`: add refusal-pattern + whitespace-dominance pre-LLM guard; add post-LLM refusal check before parse.
- Run `python -m ruff format agent/memory_extraction.py` (per user global rule: black formatting only, no linting).
- **Implementation Note (concern: do not weaken the existing 50-char check while adding the new guards — Tom's comment on issue #1212 confirmed the 50-char check IS firing correctly for true empties):** the new pre-LLM guard is **additive**, not a replacement. Order of checks at the top of `extract_observations_async`: (1) existing `len(response_text.strip()) < 50` → return `[]`, (2) NEW `_looks_like_refusal(response_text)` → return `[]`, (3) NEW whitespace-dominance ratio check → return `[]`, (4) only then call Haiku. The 50-char check stays first because it is the cheapest filter and was empirically verified by Tom to work. Removing or relaxing it would re-introduce a regression on truly empty inputs.

### 2. Build cleanup script
- **Task ID**: build-cleanup
- **Depends On**: none
- **Validates**: `tests/unit/test_cleanup_memory_extraction_junk.py` (new file)
- **Informed By**: Recon Summary (use `agent_id="extraction-*"` selector + `superseded_by` convention from `scripts/memory_consolidation.py`)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/cleanup_memory_extraction_junk.py` with argparse for `--dry-run` (default) and `--apply`.
- Use `Memory.query.all()` filter — never raw Redis (per CLAUDE.md rule, enforced by `.claude/hooks/validators/validate_no_raw_redis_delete.py`).
- Filter logic: `agent_id.startswith("extraction-")` AND `superseded_by == ""` AND (`_looks_like_refusal(content)` OR JSON-syntax line regex match).
- Apply mode: set `superseded_by="cleanup-junk-extraction"` and `superseded_by_rationale="auto-cleanup: refusal/json-shrapnel from issue #1212"`.
- Per-record try/except so one failure doesn't block the rest.
- Print `Total: N records {would be|were} superseded` at end.
- Run `python -m ruff format scripts/cleanup_memory_extraction_junk.py`.

### 3. Add unit tests
- **Task ID**: test-parser-cleanup
- **Depends On**: build-parser, build-cleanup
- **Assigned To**: parser-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add the new test methods listed in **Test Impact** to `tests/unit/test_memory_extraction.py`.
- Create `tests/unit/test_cleanup_memory_extraction_junk.py` with the cleanup script tests.
- Run `pytest tests/unit/test_memory_extraction.py tests/unit/test_cleanup_memory_extraction_junk.py -v`. All pass.

### 4. Validate parser + cleanup
- **Task ID**: validate-parser-cleanup
- **Depends On**: test-parser-cleanup
- **Assigned To**: parser-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_extraction.py tests/unit/test_cleanup_memory_extraction_junk.py -q` — exit 0.
- Run `python scripts/cleanup_memory_extraction_junk.py --dry-run` — exit 0, prints candidate count > 0 (this machine has known junk records).
- Inspect first 5 candidate IDs against `python -m tools.memory_search inspect --id <ID>` — confirm each is genuinely junk (refusal or JSON shrapnel), no false positives.
- Run `python -m tools.memory_search search "deployment"` afterward — confirm legitimate Memory records still appear.

### 5. Apply cleanup
- **Task ID**: apply-cleanup
- **Depends On**: validate-parser-cleanup
- **Assigned To**: parser-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/cleanup_memory_extraction_junk.py --apply`. Capture output (count of records superseded, count of WriteFilter-blocked writes, total candidates).
- Document **all three counts** in the PR description per acceptance criterion 5 (superseded / blocked / total).
- Run `python -m tools.memory_search status` — verify `Superseded` count increased by the superseded count from the apply run (NOT just "decreased totals" — these are reversible records that still exist, just filtered).
- **Implementation Note (concern: apply mode has no per-batch checkpoint or sample-spot-check before fully running — once it touches a few hundred records, an unnoticed false-positive class becomes hard to triage):** before invoking `--apply`, the validator MUST first capture the dry-run candidate list to a file (`/tmp/cleanup_candidates_1212.txt`) by running the dry-run mode and saving its output. Spot-check **5 random IDs** from that file using `python -m tools.memory_search inspect --id <ID>` and confirm each is genuinely junk (refusal text or single-line JSON shrapnel). Only after the spot-check passes does the validator invoke `--apply`. If any spot-checked record is legitimate, abort, report the false positive, and route back to a parser-builder pass. `superseded_by` is reversible by clearing the field, so even a bad apply is recoverable — but the spot-check makes the recovery unnecessary.

### 6. Update documentation
- **Task ID**: document-feature
- **Depends On**: validate-parser-cleanup
- **Assigned To**: memory-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md`:
  - In "Flow 3: Post-Session Extraction", add a paragraph about tolerant JSON parsing and the refusal filter.
  - Add a subsection (or add to existing "Memory Consolidation" section) documenting `cleanup_memory_extraction_junk.py`.
- Verify inline docstrings on `_extract_json_payload`, `_REFUSAL_PATTERNS`, and the cleanup script are present.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: apply-cleanup, document-feature
- **Assigned To**: parser-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -q` — exit 0 (full unit suite).
- Run `python -m ruff format --check .` — exit 0.
- Verify all Success Criteria checked.
- Generate final report including the cleanup count for the PR description.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New unit tests pass | `pytest tests/unit/test_memory_extraction.py tests/unit/test_cleanup_memory_extraction_junk.py -q` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -q` | exit code 0 |
| Format clean | `python -m ruff format --check agent/memory_extraction.py scripts/cleanup_memory_extraction_junk.py tests/unit/test_memory_extraction.py tests/unit/test_cleanup_memory_extraction_junk.py docs/features/subconscious-memory.md` | exit code 0 |
| Cleanup dry-run works | `python scripts/cleanup_memory_extraction_junk.py --dry-run` | exit code 0 |
| No raw Redis in cleanup script | `grep -E '\bredis\.|r\.delete\|r\.hget\|r\.scan_iter' scripts/cleanup_memory_extraction_junk.py` | exit code 1 |
| Doc updated | `grep -c "tolerant JSON\|refusal" docs/features/subconscious-memory.md` | output > 0 |

## Critique Results

<!-- Single critique pass returned READY TO BUILD (with concerns); concerns folded below as Implementation Notes per SDLC dispatch table Row 4b. Comment IC_kwDOEYGa088AAAABAwQnJw (2026-04-29 by tomcounsell) folded as final row. revision_applied: true set in frontmatter. CONCERNs remain acknowledged risks/clarifications, NOT defects. -->

| Severity | Critic | Concern | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | `_REFUSAL_PATTERNS` is a hard-coded substring list — Haiku will rephrase its refusals over time and the patterns will drift. What's the maintenance plan? | Solution → Key Elements (refusal-pattern filter description) | Module-scope tuple constant required; each entry carries an inline comment citing originating Memory ID from #1212; failure mode is silent rejection of refusal text only (never silent acceptance of legitimate text); `memory-dedup` reflection is the recurring safety net. |
| CONCERN | Operator | The `0.3` whitespace-dominance threshold is a magic constant with no test calibration — risk of false positives on terse but real responses. | Solution → Technical Approach (`extract_observations_async` modifications) | Define as named module-scope `_MIN_NON_WHITESPACE_RATIO = 0.3`; unit test must exercise both sides of the boundary (25% rejected, 35% accepted); dual filter (pre-LLM + post-LLM) is by design — refusal can emerge from inputs that pass the pre-check, so redundancy is intentional. |
| CONCERN | Adversary | Unit tests don't include adversarial near-miss inputs that contain bare refusal substrings in legitimate context (e.g., a memory about debugging session shutdowns). Without these, future pattern additions could silently widen to bare-keyword matches. | Risks → Risk 1 | Added dedicated `test_legitimate_text_with_session_substring` test that asserts an observation containing both `"session"` and `"no novel"` substrings (in legitimate context) is NOT rejected. Locks in narrowness as a regression boundary. |
| CONCERN | Archaeologist | Cleanup script doesn't handle `Memory.save()` returning `False` due to `WriteFilterMixin` — pattern verified at `scripts/memory_consolidation.py:298-301`. Without capturing the return value, the "count superseded" claim in the PR description is dishonest about partial success. | Solution → Cleanup script structure | Capture `result = m.save()`, count `blocked` writes per `memory-dedup` precedent, log warning per blocked record, report **three counts** in PR description: superseded / blocked / total candidates. |
| CONCERN | Archaeologist | Tom's comment on issue #1212 confirmed the existing 50-char check IS working correctly for true empties — the bug is for inputs ≥ 50 chars that aren't real session output. Builder must not weaken the 50-char check while adding the new guards. | Step by Step Tasks → Step 1 (build-parser) | New pre-LLM guard is **additive**, not a replacement. Explicit check ordering documented: (1) 50-char check, (2) refusal patterns, (3) whitespace dominance, (4) Haiku call. |
| CONCERN | User | Apply mode has no per-batch checkpoint or sample-spot-check — once it touches hundreds of records, an unnoticed false-positive class becomes hard to triage. | Step by Step Tasks → Step 5 (apply-cleanup) | Validator MUST capture dry-run candidate list to a file before `--apply`, spot-check 5 random IDs via `tools.memory_search inspect`, abort on any false positive. Reversibility (clearing `superseded_by`) is a backstop, not the primary safety. |
| COMMENT-FOLD | tomcounsell (`IC_kwDOEYGa088AAAABAwQnJw`) | Of three options for refusal-prose handling — (a) raise length threshold, (b) substantive-content heuristic, (c) post-LLM refusal-pattern filtering — option (c) is the strongest. Live repro confirmed JSON-shrapnel symptom (1 LLM call → 10 Memory rows). 50-char check confirmed working; existing junk records came from "low-content but above-threshold" inputs. | Solution → Key Elements; Solution → Flow; Open Questions Q1 | Plan reframed: post-LLM refusal-pattern filter is now the load-bearing defense (PRIMARY); pre-LLM guard is a cost optimization (SUPPORTING). 50-char check explicitly preserved unchanged. |

---

## Open Questions

The two open questions in the issue body have been resolved by the Recon Summary and the comment on issue #1212:

1. **Q1 (is the short-response check firing?):** Confirmed YES by Tom in `IC_kwDOEYGa088AAAABAwQnJw` — empty-string input returns 0 observations as expected. The bug is for inputs ≥ 50 chars that aren't real session output (refusal prose from "low-content but above-threshold" sessions). The 50-char check stays UNCHANGED. Fix elevates **post-LLM refusal-pattern filtering** (option c) as the primary defense, with pre-LLM substantive-content guard as a supporting layer.
2. **Q2 (one-shot vs recurring cleanup?):** One-shot script, modeled on `scripts/memory_consolidation.py`. Recurring is unnecessary because the parser fix prevents re-pollution.

No further open questions for the supervisor at this time.
