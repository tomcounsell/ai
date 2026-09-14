---
status: docs_complete
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-05-09
tracking: https://github.com/tomcounsell/ai/issues/1359
last_comment_id:
---

# Consolidate filter_tool_logs Duplicate Between bridge/response.py and bridge/context.py

## Problem

`bridge/context.py:104` defines a stale, divergent copy of `filter_tool_logs` that PR #1077's
consolidation pass missed. The audit grepped for *imports* of `bridge.response.filter_tool_logs`
but never grepped for the bare function name, so the duplicate at `bridge/context.py:104` slipped
past two consecutive cleanups (modularization in `b3ca10f5` and consolidation in PR #1077).

**Current behavior:**

The two implementations have diverged in three concrete ways. `bridge/context.py:104-148` is
behaviorally weaker than `bridge/response.py:138-182`:

| Behavior | `bridge/response.py` (canonical) | `bridge/context.py` (stale) |
|----------|----------------------------------|------------------------------|
| Variation selector handling (`️?` after the emoji) | Yes — line 130 | No — line 122 |
| Backtick shell-command echo filter (`_SHELL_COMMAND_HINTS`) | Yes — lines 135, 167-170 | No |
| Length floor (`< 5 char` → `""`) | Yes — line 180 | No |

`bridge/context.py` calls its own stale copy in two places — `build_conversation_history` at
line 375 and `format_reply_chain` at line 485. The latter is the live impact path:
`format_reply_chain` is spliced into `_build_completed_resume_text` at
`bridge/telegram_bridge.py:1740` and `:2259`, so tool traces with `🛠️` (wrench + U+FE0F) leak
into resumed-session prompts. The agent then sees its own previous tool noise inside
conversation history and may interpret `🛠️ exec: ls` as instruction.

Reproducible empirically against current main (verified during freshness check):

- `"🛠️ exec: ls -la\nHere is the result.\n📖 read: file.py"` — response.py drops both tool
  lines; context.py lets the wrench-with-VS leak.
- `` "Here is the analysis.\n`ls -la /tmp`\nDone." `` — response.py drops the backtick echo;
  context.py keeps it.
- `"🛠️ exec: ls\nok"` — response.py returns `""` (length floor triggers `< 5`); context.py
  returns the leaked emoji line plus `"ok"`.

**Desired outcome:**

Single source of truth: `bridge.response.filter_tool_logs`. `bridge.context` imports it and
reuses it for `build_conversation_history` and `format_reply_chain`. A unit test asserts
identity (`bridge.context.filter_tool_logs is bridge.response.filter_tool_logs`) so any
future re-introduction of a local copy fails CI immediately.

## Freshness Check

**Baseline commit:** `53a64e7c5abbf5675ed79f3e42d2ce08a1331b9b`
**Issue filed at:** `2026-05-09T13:16:16Z` (today)
**Disposition:** Unchanged

**File:line references re-verified:**

- `bridge/response.py:138` — `def filter_tool_logs` — still holds (line 138 exact match)
- `bridge/context.py:104` — `def filter_tool_logs` — still holds (line 104 exact match)
- `bridge/context.py:375` — `content = filter_tool_logs(content)` in `build_conversation_history` — still holds
- `bridge/context.py:485` — `content = filter_tool_logs(content)` in `format_reply_chain` — still holds
- `bridge/telegram_bridge.py:1740` — `format_reply_chain(chain)` call site in `_build_completed_resume_text` — still holds
- `bridge/telegram_bridge.py:2259` — second `format_reply_chain(chain)` call site — still holds
- Behavioral diffs (variation selector, shell echo filter, length floor) re-verified against current `bridge/response.py` and `bridge/context.py` code — all three diverge exactly as the issue describes.

**Cited sibling issues/PRs re-checked:**

- #1332 — Closed `2026-05-09T13:17:03Z` (the parent investigation that spawned this issue) — resolution: spun out into #1359, no behavioral change applied yet.
- PR #1077 — Merged `2026-04-20T12:35:48Z` — performed the original consolidation but missed `bridge/context.py:104`. No follow-up commit landed.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since="2026-05-09T13:16:16Z" -- bridge/context.py bridge/response.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:** None. The two unstaged plan files visible in `git status` (`agent-session-outcome-verification.md`, `agent_browser_to_byob_skill_migration.md`) are unrelated to bridge response/context filtering.

**Notes:** No drift. All file:line targets in the issue are exact. The work is purely mechanical.

## Prior Art

- **Issue #1332**: Reliability risk — duplicate `filter_tool_logs` implementations diverge between response.py and context.py. Closed `2026-05-09T13:17:03Z`. Spun this issue (#1359) out as the actionable fix. Full investigation evidence is reused here verbatim.
- **PR #1077**: "Close out #1035 deferred scope: consolidate bridge/response.py, migrate table, add validator tests". Merged `2026-04-20T12:35:48Z`. Performed the canonical consolidation of `bridge/response.py` (the version we're keeping). Missed `bridge/context.py:104` because the audit grepped only for imports of `bridge.response.filter_tool_logs`, not for the bare function name. The plan here adds an identity assertion test as a permanent guard against the same audit miss happening again.
- **PR #680**: "Fix REACT emoji leak as literal text (#678)". Merged `2026-04-03T16:18:55Z`. Earlier emoji-handling fix in the same area; established the precedent of treating tool-trace emoji prefixes as filter targets rather than user content. Adjacent context only — no behavioral overlap with this fix.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1077 | Consolidated `bridge/response.py` and treated `bridge.response.filter_tool_logs` as canonical. Audit checked imports of the canonical path. | Audit grep was scoped to `from bridge.response import filter_tool_logs`. It never ran `grep -rn "def filter_tool_logs" bridge/`. The duplicate `def` at `bridge/context.py:104` (predating the modularization in `b3ca10f5`) was never observed by the audit. |

**Root cause pattern:** Duplicate-detection audits that check importers of the canonical path cannot detect orphan local copies that never imported anything in the first place. The permanent fix is not just deleting the duplicate but adding a compile-time identity assertion (`bridge.context.filter_tool_logs is bridge.response.filter_tool_logs`) so that any future re-divergence breaks CI before review.

## Architectural Impact

- **New dependencies**: None at the package level. `bridge/context.py` does not currently import from `bridge.response` (verified via grep). After this change, `bridge/context.py` will import `filter_tool_logs` from `bridge.response`, creating a new module-level dependency `bridge.context → bridge.response` for that one symbol.
- **Interface changes**: None. The function name, signature, and module-public access on `bridge.context` (`bridge.context.filter_tool_logs`) all remain unchanged via the re-export. External callers that already import `from bridge.context import filter_tool_logs` (none in the current tree, verified) would continue to work.
- **Coupling**: Increases coupling between `bridge.context` and `bridge.response` by one symbol. Both modules already coexist in the `bridge/` package and are co-imported by `bridge/telegram_bridge.py` (`bridge/telegram_bridge.py:95` and `:124`). The directional dependency is: response.py → (no bridge deps for filter logic) ← context.py reuses. No cycle risk.
- **Data ownership**: Unchanged. `bridge.response` remains the owner of the tool-log filtering logic.
- **Reversibility**: Trivially reversible. Restoring the duplicate is one git revert away. The new identity assertion test would need to be deleted in the revert.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (mandatory PR review per repo policy)

This is a mechanical refactor: delete 45 lines (`def filter_tool_logs` body in context.py), add one import, add three tests. The behavior of the canonical version is already documented and tested in `tests/integration/test_reply_delivery.py::TestFilterToolLogsFallback`. No design decisions remain.

## Prerequisites

No prerequisites — this work has no external dependencies. All affected files are in the local repo and editable without environment configuration.

## Solution

### Key Elements

- **`bridge/response.py`**: Unchanged. Remains canonical owner of `filter_tool_logs`.
- **`bridge/context.py`**: Loses its local `def filter_tool_logs` (lines 104-148) and the surrounding section header. Gains `from bridge.response import filter_tool_logs` near the existing top-of-file imports. Both internal call sites (`build_conversation_history` line 375, `format_reply_chain` line 485) keep their bare-name `filter_tool_logs(...)` call — the import resolves the same symbol they used before.
- **New unit test class** `tests/unit/test_context_helpers.py::TestFilterToolLogsParity` containing three tests:
  1. `test_filter_tool_logs_is_response_canonical`: asserts `bridge.context.filter_tool_logs is bridge.response.filter_tool_logs`. Single-line identity check that fails immediately if anyone re-introduces a local copy.
  2. `test_format_reply_chain_drops_variation_selector_and_backtick_echo`: builds a chain whose Valor message contains `🛠️ exec: ls` (with the U+FE0F variation selector), `📖 read: file.py`, and a backtick-shell-echo line; asserts the formatted output contains none of those three filter targets.
  3. `test_format_reply_chain_omits_messages_below_length_floor`: builds a chain whose Valor message filters down to `<5` chars after `filter_tool_logs`; asserts the message is omitted entirely from `format_reply_chain` output (because the canonical version returns `""` on the floor, and lines 486-487 already `continue` on empty content).

### Flow

`format_reply_chain` is invoked by `_build_completed_resume_text` → splices its output into the resumed-session prompt. With this change, the wrench-with-VS line that previously leaked through `bridge.context.filter_tool_logs` is now dropped by `bridge.response.filter_tool_logs` (the same symbol that already filters live agent output via `bridge.telegram_bridge.py`'s response-handling path).

### Technical Approach

- Delete `bridge/context.py:104-148` (the duplicate `def`) plus the section header at lines 99-102 ("Tool Log Filtering"). Total deletion ≈ 50 lines.
- Add `filter_tool_logs` to the existing `from bridge.response import ...` block. There is no existing `from bridge.response import` in `bridge/context.py` (verified — context.py currently has no `bridge.response` import), so add a new import line at the top of the existing import block.
- The two call sites at line 375 and line 485 use the bare name `filter_tool_logs(...)` and require no edits — the new top-of-module import binds the same name.
- The integration test reuses the test pattern in `tests/unit/test_context_helpers.py::test_format_reply_chain_uses_the_constant` for chain construction.
- The identity assertion is a one-liner: `assert bridge.context.filter_tool_logs is bridge.response.filter_tool_logs`. It runs in the unit suite and provides permanent regression protection against the exact pattern that produced this bug.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No exception handlers in scope — both `filter_tool_logs` (canonical) and the call sites in `format_reply_chain` and `build_conversation_history` are pure string transforms with no try/except blocks.

### Empty/Invalid Input Handling
- [x] Empty input is already handled by canonical `filter_tool_logs` (line 148-149: `if not response: return ""`). After consolidation, this becomes the only behavior — context.py's stale copy that lacked the length floor is gone.
- [x] The new length-floor test (Test Impact item below) explicitly exercises the `< 5 char` floor through `format_reply_chain`, asserting the message is omitted (lines 486-487 `if not content: continue`).
- [x] No silent loops involved — `format_reply_chain` is invoked synchronously per resumed-session reply chain, no agent output processing in scope.

### Error State Rendering
- [x] No user-visible output changes — this is a bridge-internal cleanup. The user-visible effect is *fewer* leaks of internal tool noise into resumed-session prompts (a quality-of-life win, not an error path). No new error rendering.

## Test Impact

- [ ] `tests/integration/test_reply_delivery.py::TestFilterToolLogsFallback` — UNCHANGED: existing tests already import from `bridge.response` (verified at lines 196, 203, 210, 217, 223). They continue to pass unchanged. (Per resolved decisions, the variation-selector + backtick-echo test moves into `tests/unit/test_context_helpers.py::TestFilterToolLogsParity` rather than this class, because it asserts behavior through `format_reply_chain` — the live impact path — not through `filter_tool_logs` directly.)
- [ ] `tests/unit/test_context_helpers.py` — UPDATE: extend with `TestFilterToolLogsParity` class containing three tests (identity assertion, variation-selector + backtick-echo through `format_reply_chain`, length-floor through `format_reply_chain`). The file is already scoped to `bridge/context.py` helpers per its module docstring at lines 1-7.
- [ ] `tests/e2e/test_message_pipeline.py:241` — UNCHANGED: this test currently calls `filter_tool_logs(raw)` imported from `bridge.response`. It exercises the canonical version. After consolidation it remains unchanged. No code edit needed; just verify it still passes.

## Rabbit Holes

- **Re-architecting the bridge's text-processing pipeline.** Tempting to push `filter_tool_logs` into a new `bridge/text_processing/` package or to add a registry of "lines to drop". This is out of scope. The fix is one `from ... import ...` line plus a deletion. Anything else is yak-shaving.
- **Adding a deprecation shim for `bridge.context.filter_tool_logs`.** Verified above: no other module imports `from bridge.context import filter_tool_logs`. The re-export via the new top-of-module import in context.py preserves the attribute access path for any unverified external reader. No deprecation period needed.
- **Hardening the canonical `filter_tool_logs` further** (e.g., extending `_SHELL_COMMAND_HINTS`, supporting more emoji ranges). Out of scope. Any behavioral change to the canonical version belongs in a separate issue and would need its own behavioral diff justification.
- **Rewriting `format_reply_chain` to filter tool traces upstream of insertion** (e.g., during chain hydration in Redis storage). Out of scope. The current call-site filter is correct; the bug is the stale duplicate, not the call site placement.

## Risks

### Risk 1: An unverified external module imports `bridge.context.filter_tool_logs`
**Impact:** Module-level `import` would fail if context.py no longer exports the symbol.
**Mitigation:** The new `from bridge.response import filter_tool_logs` at the top of `bridge/context.py` makes the symbol available as `bridge.context.filter_tool_logs` via Python's normal module attribute exposure (any name imported at module top-level becomes accessible as a module attribute). This preserves external compatibility regardless of whether any caller exists. Verified: `grep -rn "from bridge.context import" --include="*.py" .` returns no `filter_tool_logs` import.

### Risk 2: Behavioral regression in `build_conversation_history` or `format_reply_chain`
**Impact:** These two functions feed resumed-session prompts. A regression could change which historical lines reach the agent.
**Mitigation:** The "regression" is intentional and desired — the canonical version filters *more* aggressively (variation-selector emoji, backtick shell echoes, sub-5-char remainders). The new integration test in `test_reply_delivery.py` asserts the new behavior is observed. The existing unit tests (`tests/unit/test_context_helpers.py::test_format_reply_chain_uses_the_constant`, `tests/integration/test_steering.py:1073-1092`, `tests/integration/test_private_tag_ingestion.py:130, 171`) exercise `format_reply_chain` and would catch unintended structural changes (header constant, splicing, etc.).

### Risk 3: Future reintroduction of a local `filter_tool_logs` in `bridge/context.py`
**Impact:** Restores the original bug.
**Mitigation:** The identity-assertion unit test (`bridge.context.filter_tool_logs is bridge.response.filter_tool_logs`) fails the moment a direct local `def filter_tool_logs` shadows the import. Note this guard catches the exact pattern that produced this bug (local `def` redefinition) but does not catch all possible re-divergences — e.g. a third-module copy at `bridge/foo.py:filter_tool_logs` would not trigger it. Treated as "good-enough regression protection for the observed failure mode," not absolute coverage.

## Race Conditions

No race conditions identified — all operations in scope are synchronous and single-threaded. `filter_tool_logs` is a pure string transform with no I/O, no shared state, and no concurrency primitives. `build_conversation_history` and `format_reply_chain` are synchronous functions invoked from the bridge's message-handling code path.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update system changes required — this is a purely internal bridge refactor with no new dependencies, config files, or migration steps. The changed Python files (`bridge/context.py` and the new `tests/unit/test_context_module.py`) are propagated by the existing `git pull` step in `scripts/remote-update.sh`, and the existing `./scripts/valor-service.sh restart` step in the same script reloads the bridge process so the new import binding takes effect on each machine.

## Agent Integration

No agent integration required — this is a bridge-internal change. `filter_tool_logs` is an internal helper invoked only by `bridge/context.py`'s `build_conversation_history` and `format_reply_chain`, both consumed by `bridge/telegram_bridge.py`. No CLI entry point in `pyproject.toml [project.scripts]` is involved, no MCP server exposes this function, and the agent does not invoke `filter_tool_logs` directly. Existing integration coverage in `tests/integration/test_reply_delivery.py` verifies the bridge-level effect (Valor messages with tool-trace prefixes are filtered before reaching resumed-session prompts).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-response-improvements.md` line 31 — fix the stale `**Location**: bridge/telegram_bridge.py:filter_tool_logs()` reference (function actually lives in `bridge/response.py`) and add a one-line cross-reference noting that `bridge/context.py` reuses `filter_tool_logs` from `bridge/response.py` for reply-chain hydration.

### External Documentation Site
- No external documentation site involved — this repo uses `docs/` markdown directly, no Sphinx/MkDocs build step in scope.

### Inline Documentation
- [ ] Add a one-line comment above the new `from bridge.response import filter_tool_logs` line in `bridge/context.py`: `# Reuse the canonical tool-log filter from bridge.response (single source of truth).`

## Success Criteria

- [ ] `bridge/context.py` no longer defines `filter_tool_logs`; `grep -rn "def filter_tool_logs" bridge/` returns exactly one match (in `bridge/response.py`).
- [ ] `bridge/context.py` imports `filter_tool_logs` from `bridge.response` near the top of the file.
- [ ] `bridge.context.filter_tool_logs is bridge.response.filter_tool_logs` evaluates `True` at runtime (asserted by unit test).
- [ ] Both call sites at `bridge/context.py:375` (`build_conversation_history`) and `:485` (`format_reply_chain`) call the imported function unchanged in source — only the binding moves.
- [ ] `tests/unit/test_context_helpers.py::TestFilterToolLogsParity` contains three tests: identity assertion, variation-selector + backtick-echo through `format_reply_chain`, and length-floor through `format_reply_chain`.
- [ ] `pytest tests/unit/test_context_helpers.py tests/integration/test_reply_delivery.py tests/e2e/test_message_pipeline.py` passes.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Single builder + validator pair. The work is small enough that one builder can complete the source edit and the test additions in one pass. No parallel tracks needed.

### Team Members

- **Builder (consolidate-filter-tool-logs)**
  - Name: `filter-tool-logs-consolidator`
  - Role: Delete duplicate `filter_tool_logs` from `bridge/context.py`; add re-export import; add three tests (parity, integration, length-floor); update docs cross-reference.
  - Agent Type: builder
  - Resume: true

- **Validator (consolidate-filter-tool-logs)**
  - Name: `filter-tool-logs-validator`
  - Role: Verify `grep -rn "def filter_tool_logs" bridge/` returns exactly one match. Run targeted test files and confirm pass. Confirm `bridge.context.filter_tool_logs is bridge.response.filter_tool_logs` at the Python REPL.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Consolidate filter_tool_logs and add tests
- **Task ID**: build-consolidate-filter-tool-logs
- **Depends On**: none
- **Validates**: `tests/unit/test_context_helpers.py` (extend with `TestFilterToolLogsParity`), `tests/integration/test_reply_delivery.py` (re-run unchanged), `tests/e2e/test_message_pipeline.py` (re-run unchanged)
- **Informed By**: Issue #1359 file:line targets, Freshness Check (all references re-verified at commit 53a64e7c), Resolved Decisions section
- **Assigned To**: filter-tool-logs-consolidator
- **Agent Type**: builder
- **Parallel**: false
- Delete the duplicate `def filter_tool_logs(response: str) -> str:` block at `bridge/context.py:104-148` and the surrounding section-header comment at lines 99-102.
- Add `from bridge.response import filter_tool_logs` near the top of `bridge/context.py`. Place it adjacent to existing `from bridge.` imports if any exist; otherwise add a new top-level import line. Add a one-line comment above it explaining the single-source-of-truth intent.
- Verify via `grep -rn "def filter_tool_logs" bridge/` that exactly one match remains (the canonical one in `response.py`).
- Extend `tests/unit/test_context_helpers.py` with a new `TestFilterToolLogsParity` class containing three tests:
  1. `test_filter_tool_logs_is_response_canonical`: imports `bridge.context` and `bridge.response`, asserts `bridge.context.filter_tool_logs is bridge.response.filter_tool_logs`.
  2. `test_format_reply_chain_drops_variation_selector_and_backtick_echo`: builds a chain whose Valor message contains `🛠️ exec: ls` (with U+FE0F variation selector), `📖 read: file.py`, and a backtick-shell-echo line; calls `format_reply_chain`; asserts none of those filter targets remain in the output.
  3. `test_format_reply_chain_omits_messages_below_length_floor`: builds a chain whose Valor message would filter to `< 5` chars; calls `format_reply_chain`; asserts the message is omitted from the output (the existing `if not content: continue` at lines 486-487 handles this once `filter_tool_logs` returns `""` per the canonical floor).
- Update `docs/features/bridge-response-improvements.md` line 31 — the existing `**Location**: bridge/telegram_bridge.py:filter_tool_logs()` reference is stale (the function lives in `bridge/response.py`, not `telegram_bridge.py`). Replace it with two lines: (1) corrected canonical location `**Location**: bridge/response.py:filter_tool_logs()`, and (2) cross-reference: `bridge/context.py` re-exports `filter_tool_logs` from `bridge/response.py` for reply-chain hydration; this is the single source of truth.

### 2. Validate consolidation
- **Task ID**: validate-consolidate-filter-tool-logs
- **Depends On**: build-consolidate-filter-tool-logs
- **Assigned To**: filter-tool-logs-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "def filter_tool_logs" bridge/` — assert output is exactly one line, in `bridge/response.py`.
- Run `python -c "import bridge.context, bridge.response; assert bridge.context.filter_tool_logs is bridge.response.filter_tool_logs; print('PARITY OK')"` and confirm `PARITY OK` is printed.
- Run `pytest tests/unit/test_context_helpers.py tests/integration/test_reply_delivery.py tests/e2e/test_message_pipeline.py -x -q` — assert exit code 0.
- Run `python -m ruff check bridge/context.py tests/unit/test_context_helpers.py` and `python -m ruff format --check bridge/context.py tests/unit/test_context_helpers.py` — assert exit code 0.
- Confirm `docs/features/bridge-response-improvements.md` contains the new cross-reference line.
- Report pass/fail with the exact commands run.

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: build-consolidate-filter-tool-logs, validate-consolidate-filter-tool-logs
- **Assigned To**: filter-tool-logs-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full Verification table below.
- Verify all Success Criteria checkboxes are achievable from the changed code state (validator marks them; builder does not self-mark).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Single definition | `grep -rn "def filter_tool_logs" bridge/` | exit code 0, exactly one match line in output |
| Symbol identity | `python -c "import bridge.context, bridge.response; assert bridge.context.filter_tool_logs is bridge.response.filter_tool_logs"` | exit code 0 |
| Targeted tests pass | `pytest tests/unit/test_context_helpers.py tests/integration/test_reply_delivery.py tests/e2e/test_message_pipeline.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/context.py tests/unit/test_context_helpers.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/context.py tests/unit/test_context_helpers.py` | exit code 0 |
| Docs cross-ref present | `grep -n "context.py" docs/features/bridge-response-improvements.md` | exit code 0, output contains a line referencing the re-export |

## Critique Results

Verdict: READY TO BUILD (with concerns) — recorded `sha256:86f16bf9...`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Concern | Skeptic | Corrupted sentence in Architectural Impact line 97 (two clauses spliced, self-contradictory). | Plan revision | Replaced with a single declarative sentence about the new `bridge.context → bridge.response` dependency. |
| Concern | Archaeologist | `docs/features/bridge-response-improvements.md` line 31 says the function lives in `bridge/telegram_bridge.py`; it actually lives in `bridge/response.py`. | Plan revision + builder | Step 1 now instructs the builder to fix the stale Location reference and append the cross-reference in the same edit. |
| Concern | Operator | Original Open Question 1 left the test file undecided while validator/Verification commands hard-coded `tests/unit/test_context_module.py`. | Resolved Decisions section | Q1 resolved to extend `tests/unit/test_context_helpers.py`; all validator commands and Verification table updated to match. |
| Concern | Adversary | Risk 3 framed `is`-check as "permanent regression protection" — overclaim. | Plan revision | Risk 3 now scopes the guard to "the exact pattern that produced this bug" and notes it doesn't catch all re-divergences. |

---

## Resolved Decisions

1. **Test file placement — extend `tests/unit/test_context_helpers.py`.** Add a `TestFilterToolLogsParity` class to the existing file. Reasoning: `tests/unit/test_context_helpers.py:1-7` is explicitly scoped to `bridge/context.py` — its docstring says "Unit tests for bridge/context.py helper functions". It already groups multiple `bridge.context` concerns (`TestReplyThreadContextHeader`, `TestReferencesPriorContextDeictic`, `TestBuildCompletedResumeText`). Adding a new file for two tests would be an outlier in this codebase. All references to `tests/unit/test_context_module.py` in this plan are superseded by `tests/unit/test_context_helpers.py::TestFilterToolLogsParity`.

2. **Length-floor test scope — through `format_reply_chain` only.** One assertion: build a chain where a Valor message's filtered remainder is `<5` chars; assert it's omitted from the formatted output. Do not add a duplicate direct `filter_tool_logs(...)` length-floor unit test — the existing tests at `tests/integration/test_reply_delivery.py:191-229` and `tests/e2e/test_message_pipeline.py:239-246` cover the function directly; a through-pipeline test adds new coverage. The `< 5` floor is currently UNCOVERED through the pipeline (verified via grep — no length-floor assertions in `tests/`), so this is a genuine gap-closer.

### Net new coverage in this plan

1. **Identity assertion** — `bridge.context.filter_tool_logs is bridge.response.filter_tool_logs` (permanent regression guard).
2. **Length-floor through `format_reply_chain`** — chain with Valor message that filters to `<5` chars; assert message vanishes from output.
3. **Variation-selector + backtick-echo through `format_reply_chain`** — chain with Valor message containing `🛠️ exec: ls` (with U+FE0F variation selector), `📖 read: file.py`, and a backtick-shell-echo line; assert all three filter targets are dropped from the formatted output.

All three tests live in `tests/unit/test_context_helpers.py::TestFilterToolLogsParity`.
