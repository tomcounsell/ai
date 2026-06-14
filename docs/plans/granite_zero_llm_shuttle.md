---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-06-14
tracking: https://github.com/tomcounsell/ai/issues/1681
last_comment_id:
---

# Make granite PTY operator a zero-LLM shuttle (remove PM↔Dev rewrites)

## Problem

The granite PTY operator (`granite4.1:3b`) sits between two Claude Code sessions
(PM and Dev) inside the interactive-TUI container. Its legitimate job is purely
mechanical: detect turn boundaries, parse TUI frames, classify the routing token
(`[/dev]` / `[/user]` / `[/complete]`), strip TUI chrome, and move bytes between
the two sessions. It must do **zero writing** — never rephrase, summarize, or
regenerate message content.

Today granite makes **two LLM rewrites**, both on the internal PM↔Dev channel:

| Call | File:line | Path | Behavior to remove |
|------|-----------|------|--------------------|
| `extract_dev_prompt()` | `agent/granite_container/granite_classifier.py:373` (ollama at :391) | PM → Dev | Re-writes the PM's `[/dev]` instruction via `granite4.1:3b`, even though `classify_pm_prefix` already extracted the verbatim payload. |
| `summarize_for_pm()` | `agent/granite_container/granite_classifier.py:408` (ollama at :426) | Dev → PM | Summarizes Dev's raw output via `granite4.1:3b` before the PM reads it. |

Both substitute a 3B model's prose for what the Opus-grade sessions actually
authored — the same laundering #1680 removed from the message drafter, one layer
inward.

**Current behavior:**
- PM emits `[/dev] <verbatim instruction>`. `classify_pm_prefix` extracts the
  verbatim, chrome-stripped payload — but the container **discards it** and calls
  `extract_dev_prompt(pm_buf)`, which round-trips the whole PM tail through
  `granite4.1:3b` and writes the 3B model's re-extraction to the Dev PTY.
- Dev produces output. The container reads `dev_buf` and calls
  `summarize_for_pm(dev_buf)`, writing the 3B model's *summary* — not Dev's
  actual words — to the PM PTY.

**Desired outcome:**
Granite becomes a **zero-LLM shuttle** on the PM↔Dev channel:

1. **`extract_dev_prompt` deleted.** The `[/dev]` write uses
   `classification.payload` (the verbatim text after `[/dev]`, already
   chrome-stripped by `classify_pm_prefix`). The PM's exact words reach Dev.
2. **`summarize_for_pm` deleted.** The full visible Dev output is forwarded to
   the PM **verbatim** — TUI-chrome stripped, but otherwise unsummarized. No 3B
   summary, no Dev self-summarization contract. The PM reads what Dev actually
   produced.
3. **The ollama *translation* path is removed** from the classifier:
   `ollama_chat` import, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, the two translation
   functions, `GraniteTranslationError`, `_events_from_text`,
   `_extract_tool_calls`, `_normalize_arguments`. Net-negative diff in
   `granite_classifier.py`.

## Freshness Check

**Baseline commit:** `ef4527044ce48a811dcbacdf48a972263bab6497`
**Issue filed at:** 2026-06-13T16:28:04Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/granite_container/granite_classifier.py:373` — `extract_dev_prompt` definition — still holds (line 373, ollama call at 391).
- `agent/granite_container/granite_classifier.py:408` — `summarize_for_pm` definition — still holds (line 408, ollama call at 426).
- `agent/granite_container/granite_classifier.py:184` — `TRANSLATION_TOOLS` — still holds.
- `agent/granite_container/granite_classifier.py:51` — `DEFAULT_MODEL` import from `config.models.OLLAMA_CLASSIFIER_MODEL` — still holds.
- `agent/granite_container/container.py:1033` — `extract_dev_prompt(pm_buf)` call — still holds (line 1033).
- `agent/granite_container/container.py:1077` — `summarize_for_pm(dev_buf)` call — still holds (line 1077). **Drift note:** the issue cites the Dev→PM forward at "~:1077"; confirmed exact. A **third** call site exists at `container.py:1136` (wrap-up guard seed) that the issue did not enumerate — it must also be de-LLM'd (see Solution).

**Cited sibling issues/PRs re-checked:**
- #1680 — Message drafter repositioned to verbatim pass-through — merged as #1685 (commit `ef452704`). Confirms the principle this issue extends; no overlap in touched files (drafter lives in a different module).
- #1636 / #1679 — gemma4/ollama consolidation onto granite — merged. Established `OLLAMA_CLASSIFIER_MODEL = granite4.1:3b` in `config/models.py`. **Material to this plan:** granite is still a hard worker precondition for the **classification** role (bridge `classify_needs_response` etc.), independent of the PTY routing role being de-LLM'd here.
- #1647 — wrap-up guard — merged. Introduced the third `summarize_for_pm` call site at `container.py:1136` and `self._last_dev_report`.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since=<createdAt> -- granite_classifier.py container.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** None. Granite plans (`granite_pty_production_cutover.md`, `granite_root_session_runner.md`, `gemma4_ollama_consolidation.md`) are completed/older and do not touch the translation functions being removed.

**Notes:** The issue's success criterion "No `ollama`/`granite4` call remains anywhere in `agent/granite_container/`" requires refinement — see Risk 1. `ensure_granite_model` (in `granite_classifier.py`) and its worker-startup caller must **stay**, because granite remains a hard precondition for the *classification* role even after the *PTY routing* role drops its ollama calls.

## Prior Art

- **#1680 / PR #1685**: "Reposition message drafter from LLM rewriter to verbatim pass-through" — merged. Same principle (stop laundering Opus-authored text through a small model), applied one layer outward (drafter, not PTY operator). Directly motivates this issue. No shared code.
- **#1647 / `docs/plans/sdlc-1647-1644.md`**: wrap-up guard — merged. Added `self._last_dev_report` + the third `summarize_for_pm` call site. Relevant because the wrap-up seed path must be migrated too.
- No prior attempt to de-LLM the PTY operator was found (`gh issue list --state closed --search "granite shuttle verbatim summarize"` and `gh pr list --state merged --search "granite verbatim summarize PTY"` both empty). This is a first attempt; no "Why Previous Fixes Failed" section needed.

## Data Flow

Trace of the PM↔Dev channel inside `Container._route_pm_classification` and `_run_wrapup_guard`:

1. **Entry point**: PM PTY reaches idle; `_cycle_idle(self._pm_pty)` returns `pm_buf` (ANSI-stripped by `read_until_idle`).
2. **Classify**: `classify_pm_prefix(pm_buf)` → `ClassificationResult` with `destination` and `payload`. `payload` is already chrome-stripped (ANSI via `_strip_ansi`, frame furniture via `_FRAME_ARTIFACT_RE`, anchored to the `[/...]` transcript bullet).
3. **PM → Dev (`destination == "dev"`)**:
   - *Today:* discard `classification.payload`, call `extract_dev_prompt(pm_buf)` → ollama → write 3B re-extraction to Dev PTY (`container.py:1033`, `:1064`).
   - *After:* write `classification.payload` directly to Dev PTY. No ollama.
4. **Dev cycle**: `_cycle_idle(self._dev_pty)` → `dev_buf` (ANSI-stripped but full TUI frame chrome present — input prompt, status bars, transcript bullets, box borders).
5. **Dev → PM**:
   - *Today:* `summarize_for_pm(dev_buf)` → ollama → write 3B summary to PM PTY (`container.py:1077`, `:1092`).
   - *After:* `strip_dev_chrome(dev_buf)` (new deterministic helper) → write the chrome-stripped verbatim Dev output to PM PTY. No ollama.
6. **Wrap-up seed (`container.py:1136`)**:
   - *Today:* `seed = summarize_for_pm(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE`.
   - *After:* `seed = strip_dev_chrome(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE`.
7. **Output**: `self._last_dev_report` holds the verbatim (chrome-stripped) Dev report; PM reads it; the `[/user]`/`[/complete]` path (unchanged) delivers PM's verbatim words to the human.

The `[/user]` and `[/complete]` paths (`container.py:981`, `:944`) already use `classification.payload` directly and make **no** ollama call. They are the template the `[/dev]` path should mirror, and they are **out of scope** (do not touch).

## Architectural Impact

- **New dependencies**: None added. Net removal of the `ollama` import from `granite_classifier.py`'s translation path (the import stays only insofar as `ensure_granite_model` needs the daemon for the classification role — see Risk 1).
- **Interface changes**: `extract_dev_prompt`, `summarize_for_pm`, `GraniteTranslationError`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments` removed from the classifier's public/module surface. One new deterministic helper added: `strip_dev_chrome(dev_buf: str) -> str`.
- **Coupling**: Decreases. The PTY routing path no longer depends on the ollama runtime at all; only the classification role does.
- **Data ownership**: Unchanged. The container still owns the PTY buffers and routing decisions.
- **Reversibility**: High. The change is a deletion plus one helper; reverting restores the prior commit. No data migration, no schema change.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (issue is fully scoped, product decision resolved in the issue body)
- Review rounds: 1 (code review for the chrome-strip helper correctness and the verbatim-forward regression coverage)

This is a deletion-heavy refactor with one genuinely new piece (the deterministic Dev-chrome stripper). The risk surface is small and well-bounded.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Local ollama + granite (for the surviving classification role; unit tests mock it) | `python -c "import ollama"` | Confirms the runtime still present; the change does not remove it |

The build itself needs no live ollama — every translation unit test mocks `ollama_chat`, and those tests are being deleted. Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_zero_llm_shuttle.md`

## Solution

### Key Elements

- **`classify_pm_prefix` (unchanged)**: already produces the verbatim, chrome-stripped `[/dev]` payload. The fix is to *use* it instead of discarding it.
- **`strip_dev_chrome(dev_buf)` (new, deterministic)**: a small regex helper that strips the Dev TUI frame furniture from `dev_buf` and returns the verbatim Dev-authored content. Reuses the existing `_strip_ansi` (already applied upstream) and the same class of `_FRAME_ARTIFACT_RE` patterns used by the `[/user]` path — extract transcript-bullet content, cut at box borders / input-prompt glyph / spinner verb / bypass-permissions footer.
- **Container call-site edits (3)**: `container.py:1033` (PM→Dev), `:1077` (Dev→PM), `:1136` (wrap-up seed).
- **Deletions in `granite_classifier.py`**: the entire "Translation (the 2 ollama calls)" block plus `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments`, and the module-level `ollama_chat` import **if** `ensure_granite_model` can be reworked to not need it (see Risk 1 — `ensure_granite_model` uses the `ollama` *CLI*, not the python client, except the importability guard; that guard can become a soft note or move).

### Flow

PM idle → `classify_pm_prefix` → `[/dev]` payload (verbatim) → **write payload directly to Dev PTY** → Dev idle → `strip_dev_chrome(dev_buf)` (verbatim, chrome-stripped) → **write directly to PM PTY** → PM reads Dev's actual words → `[/user]`/`[/complete]` (unchanged) → human.

### Technical Approach

- **PM → Dev**: replace `dev_prompt = extract_dev_prompt(pm_buf)` with `dev_prompt = classification.payload`. The empty-payload compliance-miss branch (`container.py:1010`) already exists and covers empty payloads — keep it. Remove the `extract_start`/`extract_ms` timing and the `granite_extract_ms` field's *population* (keep the `TurnRecord` field as `0` or drop it consistently — see Test Impact).
- **Dev → PM**: replace `summary = summarize_for_pm(dev_buf)` with `summary = strip_dev_chrome(dev_buf)`. Keep `self._last_dev_report = summary` (now the verbatim report). Remove the `summarize_start`/`summarize_ms` timing.
- **Wrap-up seed**: replace `summarize_for_pm(dev_buf)` at `:1136` with `strip_dev_chrome(dev_buf)`.
- **`strip_dev_chrome`**: implement in `granite_classifier.py` (or `pty_driver.py` next to `_strip_ansi`, whichever keeps the chrome-strip machinery cohesive — the builder picks, documenting the choice). It must be a pure function with no I/O and no LLM call. Cover it with direct unit tests using realistic painted Dev captures.
- **`TurnRecord.granite_extract_ms` / `granite_summarize_ms`**: these telemetry fields now describe a removed step. Set both to `0` permanently and note in their docstring that they are retained-as-zero for results-doc schema stability, OR remove them and update `to_json` + the results doc. The builder decides; the simpler path (zero-fill, schema stable) is preferred to avoid a results-schema migration.
- **`SYSTEM_PROMPT`**: deleted (only the translation calls used it).
- **Module docstring + `container.py` loop docstring (`container.py:12-15`)**: rewrite to describe the zero-LLM shuttle ("classify by regex → forward verbatim, chrome-stripped"). Invariant #5 ("stateless, each ollama.chat sees only the current turn") is moot for the routing path — update it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The two `try/except Exception` blocks wrapping `extract_dev_prompt`/`summarize_for_pm` (`container.py:1034`, `:1078`) are removed with the calls. `strip_dev_chrome` is a pure regex function that cannot raise on string input — no try/except needed, but add a test that it returns `""` (not a crash) on empty/whitespace/None-coerced input.
- [ ] The wrap-up-guard `except Exception` at `container.py:1137` stays (it guards `_cycle_idle`, not the translation) — assert it still falls back to `DEV_REPORT_UNAVAILABLE`.

### Empty/Invalid Input Handling
- [ ] `strip_dev_chrome("")` → `""`; `strip_dev_chrome("   \n  ")` → `""`; pure-chrome input (only borders/prompt, no content) → `""`.
- [ ] Empty `[/dev]` payload still routes through the existing compliance-miss branch (`container.py:1010`) and writes `PM_COMPLIANCE_NUDGE` — verify unchanged behavior.
- [ ] Empty `strip_dev_chrome(dev_buf)` result at the wrap-up seed falls back to `DEV_REPORT_UNAVAILABLE`.

### Error State Rendering
- [ ] The `[/user]`/`[/complete]` delivery path is unchanged — assert the human still receives PM's verbatim words (regression).
- [ ] When Dev produces no usable content, the wrap-up guard still delivers `OPERATOR_TERMINAL_MESSAGE` to the human (no silent loop).

## Test Impact

- [ ] `tests/unit/granite_container/test_granite_classifier.py` — DELETE the translation test classes: `_make_ollama_response` helper, `TestExtractDevPrompt` (`test_returns_dev_prompt`, `test_raises_on_wrong_tool`, `test_raises_on_ollama_failure`), `TestSummarizeForPm` (`test_returns_summary`, `test_raises_on_wrong_tool`, ...), and the imports of `extract_dev_prompt`, `summarize_for_pm`, `TRANSLATION_TOOLS`, `GraniteTranslationError`. The `classify_pm_prefix` and `_strip_ansi` test classes stay.
- [ ] `tests/unit/granite_container/test_granite_classifier.py` — ADD a `TestStripDevChrome` class covering: verbatim content preserved, box borders stripped, input-prompt glyph stripped, spinner verb stripped, empty/whitespace/pure-chrome → `""`, idempotency.
- [ ] `tests/unit/granite_container/test_container.py::TestClassifyDevRoutesToDev` (line ~143) — UPDATE: remove `patch("...container.extract_dev_prompt")` / `patch("...container.summarize_for_pm")`; assert the Dev PTY receives `classification.payload` verbatim and the PM PTY receives `strip_dev_chrome(dev_buf)`.
- [ ] `tests/unit/granite_container/test_container.py` (lines ~381, ~848, ~960, ~1028) — UPDATE: every test patching `extract_dev_prompt`/`summarize_for_pm` must drop the patches and assert verbatim forwarding (or patch `strip_dev_chrome` if isolating the container loop).
- [ ] `tests/unit/granite_container/test_container.py::test_to_json` (line ~732) — UPDATE: if `granite_extract_ms`/`granite_summarize_ms` are zero-filled, assert `0`; if removed, drop the fields from the expected JSON.
- [ ] `scripts/granite_smoke_test.py` — UPDATE or DELETE: this PoC gate scripts `extract_dev_prompt`/`summarize_for_pm` operator scenarios that no longer exist. It is not CI-wired (no test/CI reference). Disposition: DELETE the `extract_dev_prompt`/`summarize_for_pm` scenarios (they validate a removed capability); keep only any classification-token scenarios if present, else delete the file. Builder confirms it is not referenced by any launchd/cron/CI before deleting.
- [ ] `tests/unit/granite_container/test_persona_priming.py` — NO CHANGE expected (it asserts the PM body quotes `PREFIX_TOKEN_RE`; the Dev prime is explicitly unchanged per the issue). Verify it still passes.

## Rabbit Holes

- **Re-implementing a full ANSI/TUI terminal emulator** to "perfectly" reconstruct Dev output. The `[/user]` path already proves a regex chrome-strip is good enough; mirror it. Do not pull in `pyte` or a screen-buffer model.
- **Adding Dev-persona self-summarization** to compensate for losing the 3B summary. Explicitly rejected by the product owner in the issue — the Dev prime (`/granite:prime-dev-role`) does NOT change.
- **Reintroducing summarization "just for very large turns."** The product decision accepts raw forwarding. Large-turn PM-context pressure is a documented Risk, not a reason to keep an LLM rewrite.
- **Ripping out `ensure_granite_model` / the worker startup gate.** Granite is still required for the classification role. Removing the gate would silently break bridge classification.
- **Migrating the `TurnRecord` results-doc schema.** Prefer zero-filling the two now-dead timing fields over a schema change that ripples into the results doc and any analytics consumers.

## Risks

### Risk 1: Success criterion "no ollama/granite4 in `agent/granite_container/`" is literally unachievable
**Impact:** `ensure_granite_model` lives in `granite_classifier.py`, references `ollama_chat` (importability guard), the `ollama` CLI, and `granite4.1:3b` — and it MUST stay because granite is a hard worker precondition for the **classification** role (`OLLAMA_CLASSIFIER_MODEL`, used by bridge `classify_needs_response` etc.). Taking the success criterion literally would break classification.
**Mitigation:** Refine the criterion to its true intent: **no ollama call remains in the PM↔Dev *routing/translation* path.** `ensure_granite_model` and its worker gate are retained for the classification role. Update `ensure_granite_model`'s docstring (which currently claims "every PM/Dev turn is routed by an ollama call") to reflect that the PTY routing role is now zero-LLM and the gate exists for classification. The `grep` in Success Criteria is scoped to exclude `ensure_granite_model`.

### Risk 2: `strip_dev_chrome` over-strips or under-strips, corrupting the PM's view of Dev output
**Impact:** PM reads garbled or truncated Dev output; routing decisions degrade.
**Mitigation:** Build `strip_dev_chrome` against realistic painted Dev captures (reuse fixtures from `test_pty_driver.py` / `test_startup_parser.py`). Unit-test verbatim preservation explicitly. Mirror the proven `[/user]`-path machinery (`_FRAME_ARTIFACT_RE` class) rather than inventing new patterns.

### Risk 3: Large/tool-spammy Dev turns now reach the read-only, context-limited PM verbatim
**Impact:** A very large Dev turn could pressure the PM PTY's context window — the original reason `summarize_for_pm` existed.
**Mitigation:** Per the product decision, accept raw forwarding; do NOT reintroduce summarization. Confirm during build that the PM PTY tolerates the larger inbound payload (it is the same TUI input channel that already accepts the prime persona body, a large payload). Document this as a known operational characteristic in `granite-pty-production.md`. If it ever becomes a real problem, that is a separate issue — not a reason to relaunder Dev's words through a 3B model.

## Race Conditions

No new race conditions identified. The change is a 1:1 substitution within the existing turn-boundary state machine (`_route_pm_classification`): the same `_cycle_idle` "write only to idle PTYs" invariant governs the PM→Dev write and Dev→PM write before and after. No new shared mutable state, no new async fan-out — `strip_dev_chrome` is a synchronous pure function replacing a synchronous (`asyncio.to_thread`-free, blocking) ollama call. The existing two-PTY coordination is unchanged.

## No-Gos (Out of Scope)

- The `[/user]` and `[/complete]` paths (`container.py:981`, `:944`) — already verbatim and zero-LLM; touching them risks regressing the working path. (Not deferred — genuinely correct as-is; modifying them is out of scope by design.)
- Dev-persona self-summarization / changes to `/granite:prime-dev-role` — explicitly rejected by the product owner in the issue. (Not deferred — rejected.)
- The classification role's ollama dependency (`OLLAMA_CLASSIFIER_MODEL`, `ensure_granite_model`, the worker startup gate) — out of scope; required by bridge classification. (Not deferred — must remain.)
- The message drafter — shipped in #1680/#1685. (Not deferred — already done.)

Nothing is being deferred to a future issue — every in-scope item is completed within this plan.

## Update System

No update system changes required. This is a purely internal refactor of the granite PTY routing path:
- No new dependencies (net removal of an ollama call path; `ollama` remains installed for the classification role, which `/update` already provisions via the gemma4/ollama consolidation, #1636).
- No new config files or env vars.
- No migration steps for existing installations — the change takes effect on the next worker restart, which `/update` already performs (`scripts/valor-service.sh restart`).
- `ensure_granite_model` and the worker startup gate are unchanged, so `/update`'s granite-readiness assumptions still hold.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change.
- No new CLI entry point in `pyproject.toml [project.scripts]`.
- The bridge does not call the granite classifier directly; the worker drives the PTY container via `BridgeAdapter`/`Container.run`. That wiring is unchanged — only the internal routing behavior changes.
- The agent surface (Telegram → bridge → worker → PTY sessions) is unchanged; the human-facing `[/user]`/`[/complete]` delivery path is explicitly untouched.
- Integration coverage: existing `tests/unit/granite_container/test_bridge_adapter*.py` exercise the adapter→container delivery path; verify they still pass (no expected change). The verbatim-forwarding assertions live in `test_container.py` (see Test Impact).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md`: rewrite the routing description so the PTY operator is described as a **zero-LLM shuttle** — classify by regex, forward verbatim (chrome-stripped). Specifically:
  - Lines ~65-66 ("Granite is the routing brain — every PM/Dev turn is classified **and translated** by an ollama call") → "classified by a regex parse; payloads are forwarded verbatim, chrome-stripped — no LLM rewrite on the PM↔Dev channel."
  - Lines ~395-396 (`[/dev]` turns hard-depend on local ollama via `extract_dev_prompt`/`summarize_for_pm`) → remove; the `[/dev]` path no longer depends on ollama.
  - Lines ~525-540 (model roles table): keep granite as the classification model; remove the "PTY operator (PM↔Dev routing)" row that lists `Container.extract_dev_prompt`/`Container.summarize_for_pm`, or rewrite it to "PTY operator (PM↔Dev routing) — regex classify + verbatim forward (no model)."
  - Lines ~75-78 (`ensure_granite_model` rationale): reframe the gate as a precondition for the **classification** role, not "every PM/Dev turn is routed by an ollama call."
- [ ] No `docs/features/README.md` index change needed (the feature already has an entry).

### External Documentation Site
- [ ] N/A — this repo has no Sphinx/MkDocs site for this area.

### Inline Documentation
- [ ] Rewrite the `granite_classifier.py` module docstring (lines 1-31): drop the "classification vs translation" framing; it is now classification-only.
- [ ] Rewrite the `container.py` loop docstring (lines 12-15): replace "call granite to extract_dev_prompt (ollama) ... summarize_for_pm (ollama)" with "forward the verbatim `[/dev]` payload to Dev; forward chrome-stripped Dev output to PM."
- [ ] Update `ensure_granite_model`'s docstring per Risk 1.
- [ ] Docstring for the new `strip_dev_chrome` helper.

## Success Criteria

- [ ] No ollama call remains in the PM↔Dev **routing/translation** path. `grep -rn "ollama\|granite4" agent/granite_container/granite_classifier.py | grep -v ensure_granite_model | grep -v "^.*#"` returns no live translation call (only the surviving `ensure_granite_model` classification gate, if any references remain there).
- [ ] `extract_dev_prompt`, `summarize_for_pm`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments` are deleted from `granite_classifier.py`.
- [ ] The `[/dev]` instruction written to Dev is `classification.payload` verbatim, not an LLM re-extraction (asserted in `test_container.py`).
- [ ] The full visible Dev output is forwarded to PM verbatim, chrome-stripped via `strip_dev_chrome`, with no 3B-rewritten prose and no summarization step (asserted in `test_container.py`).
- [ ] `[/user]` path unchanged and still verbatim (regression assertion in `test_container.py`).
- [ ] Net-negative diff in `granite_classifier.py` (`git diff --stat` shows more deletions than insertions for that file).
- [ ] `scripts/granite_smoke_test.py` no longer references the removed translation tools (updated or deleted; confirmed not CI-wired).
- [ ] `ensure_granite_model` and the worker startup gate remain functional for the classification role.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — `docs/features/granite-pty-production.md` and the inline docstrings reflect the zero-LLM shuttle.

## Team Orchestration

### Team Members

- **Builder (classifier-de-llm)**
  - Name: shuttle-builder
  - Role: Delete the translation path in `granite_classifier.py`, add `strip_dev_chrome`, edit the three `container.py` call sites, update docstrings.
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: shuttle-test-builder
  - Role: Delete translation tests, add `TestStripDevChrome`, update `test_container.py` verbatim-forwarding assertions, handle `granite_smoke_test.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (shuttle)**
  - Name: shuttle-validator
  - Role: Verify all success criteria — net-negative diff, no live translation ollama call, verbatim forwarding asserted, `[/user]` regression, classification gate intact.
  - Agent Type: validator
  - Resume: true

- **Documentarian (granite-docs)**
  - Name: granite-doc
  - Role: Update `docs/features/granite-pty-production.md` per the Documentation section.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

(Standard roster — builder, test-engineer, validator, documentarian used here.)

## Step by Step Tasks

### 1. Delete translation path + add chrome stripper + edit call sites
- **Task ID**: build-shuttle
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_granite_classifier.py, tests/unit/granite_container/test_container.py
- **Assigned To**: shuttle-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `extract_dev_prompt`, `summarize_for_pm`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments`, and the translation-only `ollama_chat` usage from `granite_classifier.py`.
- Add `strip_dev_chrome(text: str) -> str` (pure, deterministic; mirror `_FRAME_ARTIFACT_RE` machinery; reuse `_strip_ansi`).
- Edit `container.py`: `:1033` → `dev_prompt = classification.payload`; `:1077` → `summary = strip_dev_chrome(dev_buf)`; `:1136` → `seed = strip_dev_chrome(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE`. Remove the now-dead extract/summarize try/except and timing.
- Zero-fill `granite_extract_ms`/`granite_summarize_ms` (schema-stable) or remove consistently with `to_json`.
- Update module docstring, container loop docstring, and `ensure_granite_model` docstring (Risk 1).

### 2. Test changes
- **Task ID**: build-tests
- **Depends On**: build-shuttle
- **Validates**: tests/unit/granite_container/test_granite_classifier.py, tests/unit/granite_container/test_container.py
- **Assigned To**: shuttle-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete translation test classes + imports in `test_granite_classifier.py`; add `TestStripDevChrome`.
- Update `test_container.py` dev-routing tests to assert verbatim PM→Dev and chrome-stripped Dev→PM forwarding; fix `test_to_json`.
- Update or delete `scripts/granite_smoke_test.py` (confirm not CI-wired first).

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-shuttle
- **Assigned To**: granite-doc
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/granite-pty-production.md` per the Documentation section.

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: shuttle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification checks; confirm every Success Criterion; confirm `[/user]` regression and classification-gate intactness.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Granite container tests pass | `pytest tests/unit/granite_container/ -q` | exit code 0 |
| No live translation ollama call in classifier | `grep -n "ollama_chat(" agent/granite_container/granite_classifier.py` | exit code 1 |
| Translation functions deleted | `grep -nE "def (extract_dev_prompt\|summarize_for_pm)\b" agent/granite_container/granite_classifier.py` | exit code 1 |
| Chrome stripper exists | `grep -n "def strip_dev_chrome" agent/granite_container/` | output contains strip_dev_chrome |
| Net-negative classifier diff | `git diff --stat main -- agent/granite_container/granite_classifier.py` | more deletions than insertions |
| Classification gate retained | `grep -n "def ensure_granite_model" agent/granite_container/granite_classifier.py` | output contains ensure_granite_model |
| Lint clean | `python -m ruff check agent/granite_container/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/granite_container/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The issue is fully scoped: the product owner resolved the Dev→PM forwarding decision (verbatim, chrome-stripped, no self-summarization) in the issue body, and the only ambiguity discovered during planning — the literal "no ollama in `agent/granite_container/`" success criterion vs. the surviving classification-role gate — is resolved in Risk 1 by scoping the criterion to the PM↔Dev routing path. If the supervisor disagrees with retaining `ensure_granite_model`, that is the one decision worth confirming, but the issue's intent (granite does zero *writing*) is unambiguously preserved either way.
