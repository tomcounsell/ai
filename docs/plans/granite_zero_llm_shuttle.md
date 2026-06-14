---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-06-14
tracking: https://github.com/tomcounsell/ai/issues/1681
last_comment_id:
---

# Make granite PTY operator a zero-LLM transcript-content shuttle (remove PM↔Dev rewrites)

## Problem

The granite PTY operator (`granite4.1:3b`) sits between two Claude Code sessions
(PM and Dev) inside the interactive-TUI container. Its legitimate job is purely
mechanical: detect turn boundaries, classify the routing token
(`[/dev]` / `[/user]` / `[/complete]`), and move message content between
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

**The right surface: the JSONL transcript, not the painted frame.**
A first draft of this plan proposed a deterministic regex helper
(`strip_dev_chrome`) that scrapes the *painted TUI scrollback* (`dev_buf`) to
recover Dev's words. A war-room critique returned ~8 BLOCKERs, all rooted in the
same mistake: a painted terminal buffer is the wrong surface. Cut-at-first-artifact
truncates real content; `─────`/`❯`/spinner glyphs collide with legitimate code,
tables, and prose; there is no reliable anchor in Dev output (unlike the PM's
`[/...]` transcript bullet); and an empty strip crashes the PTY write. Scraping
paint to reconstruct text that Claude Code *already serialized cleanly* is
backwards.

Claude Code writes every assistant turn to a structured JSONL transcript. We
spawn each session with a deterministic UUID (`claude --session-id <uuid>`,
`pty_driver.py:379`), and the container already computes both transcript paths
(`container.py:_transcript_path`, set at `container.py:292/295`) and already tails
them incrementally for telemetry (`transcript_tailer.py`). Reading the last
assistant turn's `text` blocks out of that JSONL is clean, structured, and
artifact-free — strictly better than scraping the frame.

**Desired outcome — a zero-LLM *transcript-content* shuttle on the PM↔Dev channel:**

1. **`extract_dev_prompt` deleted.** The `[/dev]` write uses `classify_pm_prefix`
   run on the PM's **last assistant text** read from the PM transcript (not the
   painted `pm_buf`), then writes `classification.payload`. The PM's exact words
   reach Dev — from clean structured text.
2. **`summarize_for_pm` deleted.** The Dev session's **last assistant text** (its
   final authored turn) is read from the Dev transcript and forwarded to the PM
   **verbatim**. No 3B summary, no Dev self-summarization contract, no frame
   scraping. This changes the Dev→PM payload from "whole scrollback" to "Dev's
   final authored turn" — which is exactly what the PM needs to route on.
3. **The ollama *translation* path is removed** from the classifier:
   `ollama_chat` translation usage, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, the two
   translation functions, `GraniteTranslationError`, `_events_from_text`,
   `_extract_tool_calls`, `_normalize_arguments`. Net-negative diff in
   `granite_classifier.py`.
4. **One new pure function** — `last_assistant_text(transcript_path) -> str` —
   reads the JSONL and returns the most recent assistant turn's concatenated
   `text` blocks. No new dependency; reuses the JSONL surface the tailer already
   parses.

`ensure_granite_model` and its worker-startup gate **stay** — granite remains a
hard precondition for the **classification** role (bridge `classify_needs_response`,
`OLLAMA_CLASSIFIER_MODEL`). Only the *PTY routing* role drops its LLM calls.

This plan (#1681) is the **content swap**: read the JSONL transcript instead of
scraping paint, on an idle-polling turn boundary. Its deterministic complement is
followup issue **#1688** ("Hook-driven turn returns for granite PTY shuttle"),
which replaces the idle-poll + flush-timing heuristics this plan still relies on
with hook-driven turn boundaries and a crash-path supervisor. #1681 = read
structured content; #1688 = deterministic boundaries.

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

**Transcript-shuttle facts re-verified (the new mechanism):**
- `claude --session-id <uuid>` (deterministic UUID) — `agent/granite_container/pty_driver.py:379`. Holds.
- The container already computes per-role transcript paths: `_transcript_path(cwd, session_id)` at `container.py:261`, populated into `result.pm_transcript_path` / `result.dev_transcript_path` from `pty._session_id` at `container.py:292/295`. `self.cwd` and `self._pm_pty._session_id` / `self._dev_pty._session_id` are in scope inside `_route_pm_classification`. Holds — no new path-derivation code needed.
- `bridge_adapter.py:92` has an equivalent `_transcript_path_from_spec(cwd, session_id)`; the container's `_transcript_path` is the in-scope one for this work.
- The incremental tailer (`transcript_tailer.py`) already parses `type:"assistant"` entries, walks `message.content[]` blocks (`tool_use`/`thinking`), is fail-silent, and tolerates a partial trailing line (only advances the byte offset to the last complete `\n`). The new `last_assistant_text` reuses that exact JSONL contract.
- `result_to_json` (`container.py:1221`, **not** `to_json`) serializes via `asdict(result)` — so dropping the two dead `TurnRecord` fields removes them from the JSON automatically.

**Painted-frame helper note:** the first draft's `strip_dev_chrome(dev_buf)` regex scraper is **abandoned**. It is not introduced; no references to it remain in this plan. The replacement reads the JSONL transcript instead (see Problem and Data Flow).

**Notes:** The issue's success criterion "No `ollama`/`granite4` call remains anywhere in `agent/granite_container/`" requires refinement — see Risk 1. `ensure_granite_model` (in `granite_classifier.py`) and its worker-startup caller must **stay**, because granite remains a hard precondition for the *classification* role even after the *PTY routing* role drops its ollama calls.

## Prior Art

- **#1680 / PR #1685**: "Reposition message drafter from LLM rewriter to verbatim pass-through" — merged. Same principle (stop laundering Opus-authored text through a small model), applied one layer outward (drafter, not PTY operator). Directly motivates this issue. No shared code.
- **#1647 / `docs/plans/sdlc-1647-1644.md`**: wrap-up guard — merged. Added `self._last_dev_report` + the third `summarize_for_pm` call site. Relevant because the wrap-up seed path must be migrated too.
- **#1688 (followup, open): "Hook-driven turn returns for granite PTY shuttle."** The deterministic complement to this plan. This plan (#1681) reads the JSONL transcript on an idle-polling turn boundary — a content swap that still inherits the idle-poll timing and a flush-timing heuristic (the "last assistant entry" must be the *completed* turn). #1688 replaces those heuristics with hook-driven turn boundaries (a deterministic Stop signal) and adds a crash-path supervisor. The two are sequenced: land the clean-content read first (#1681), then make the boundaries deterministic (#1688). The flush-race Risk below points to #1688 as its proper fix.
- No prior attempt to de-LLM the PTY operator was found (`gh issue list --state closed --search "granite shuttle verbatim summarize"` and `gh pr list --state merged --search "granite verbatim summarize PTY"` both empty). This is a first attempt; no "Why Previous Fixes Failed" section needed.

## Data Flow

Trace of the PM↔Dev channel inside `Container._route_pm_classification` and `_run_wrapup_guard`. The PTY idle cycle (`_cycle_idle`) still detects turn boundaries; the change is **what content is read** at each boundary — the JSONL transcript, not the painted buffer.

1. **Entry point**: PM PTY reaches idle; `_cycle_idle(self._pm_pty)` returns `pm_buf` (still used to detect idle; no longer the source of routed content).
2. **Classify**: compute the PM transcript path (`_transcript_path(self.cwd, self._pm_pty._session_id)`), read `pm_text = last_assistant_text(pm_transcript)`, then `classify_pm_prefix(pm_text)` → `ClassificationResult`. The classifier now runs on **clean structured text**, so its painted-frame internals (`PREFIX_TOKEN_ANCHORED_RE` `⏺`-anchoring, `_FRAME_ARTIFACT_RE` cut) are largely no-ops — the token still anchors via the strict `PREFIX_TOKEN_RE` first-line path; `payload` is the verbatim text after `[/dev]`. (Fallback: if `pm_text` is empty — flush race or read miss — fall back to classifying `pm_buf` so the turn is never silently dropped; see Flush-timing Risk.)
3. **PM → Dev (`destination == "dev"`)**:
   - *Today:* discard `classification.payload`, call `extract_dev_prompt(pm_buf)` → ollama → write 3B re-extraction to Dev PTY (`container.py:1033`, `:1064`).
   - *After:* write `classification.payload` directly to Dev PTY, guarded for emptiness (`self._dev_pty.write(payload or PM_COMPLIANCE_NUDGE)` — empty payload already routes through the compliance-miss branch at `container.py:1010`). No ollama.
4. **Dev cycle**: `_cycle_idle(self._dev_pty)` detects Dev idle (boundary only). The painted `dev_buf` is no longer parsed for content.
5. **Dev → PM**:
   - *Today:* `summarize_for_pm(dev_buf)` → ollama → write 3B summary to PM PTY (`container.py:1077`, `:1092`).
   - *After:* `dev_text = last_assistant_text(dev_transcript)` — Dev's final authored turn, structurally clean. Dev emits no routing token (it's a report), so this is forwarded verbatim. Empty-guard the PTY write: `self._pm_pty.write(dev_text or DEV_REPORT_UNAVAILABLE)` — `PTYDriver.write()` raises on empty input (`pty_driver.py:439-440`), so the guard is mandatory. No ollama.
6. **Wrap-up seed (`container.py:1136`)**:
   - *Today:* `seed = summarize_for_pm(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE`.
   - *After:* `seed = last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE`.
7. **Output**: `self._last_dev_report` holds the verbatim Dev final turn; PM reads it; the `[/user]`/`[/complete]` path (unchanged) delivers PM's verbatim words to the human.

**Flush-timing note:** the "last assistant entry" must be the *completed* turn. We read after `_cycle_idle` reports idle, by which point Claude Code has normally flushed the assistant message to the JSONL — but this is a heuristic, not a guarantee. `last_assistant_text` reads only complete JSONL lines (ignoring any partial trailing line, exactly as the tailer does), so a half-written final line is skipped rather than mis-parsed. The deterministic fix (a hook-driven Stop signal so we read only after the turn is provably complete) is followup issue **#1688**.

The `[/user]` and `[/complete]` paths (`container.py:981`, `:944`) already use `classification.payload` directly and make **no** ollama call. With this change they classify `pm_text` (the same clean read) rather than `pm_buf`, but their *delivery* logic is otherwise unchanged and **out of scope** (do not alter their human-delivery behavior; they only benefit from the cleaner classify input).

## Architectural Impact

- **New dependencies**: None added. The new `last_assistant_text` reads the same JSONL surface the tailer already consumes (stdlib `json` + file I/O). Net removal of the `ollama` translation usage from `granite_classifier.py` (the runtime stays only insofar as `ensure_granite_model` needs it for the classification role — see Risk 1).
- **Interface changes**: `extract_dev_prompt`, `summarize_for_pm`, `GraniteTranslationError`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments` removed from the classifier's public/module surface. One new pure function added: `last_assistant_text(transcript_path: str) -> str`. The two dead `TurnRecord` telemetry fields (`granite_extract_ms`, `granite_summarize_ms`) are **deleted** (see Test Impact).
- **New content surface**: the routing path now reads message content from the Claude Code JSONL transcript (already computed by the container) instead of the painted PTY scrollback. The PTY idle cycle remains the turn-boundary detector.
- **Coupling**: Net change is mixed but favorable. The PTY routing path no longer depends on the ollama runtime at all (only the classification role does), and stops depending on painted-frame layout. It gains a dependency on the JSONL transcript being present and flushed at the idle boundary — a heuristic this plan documents and #1688 makes deterministic.
- **Data ownership**: Unchanged. The container still owns the PTY drivers, the transcript paths, and the routing decisions.
- **Reversibility**: High. The change is a deletion plus one pure function; reverting restores the prior commit. No data migration, no schema change (the results-doc schema *shrinks* by two fields).

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (issue is fully scoped; the product decision resolved in the issue body; the painted-frame-vs-transcript pivot is resolved by the owner)
- Review rounds: 1 (code review for `last_assistant_text` correctness, the empty-guards on every PTY write, and the verbatim-forward regression coverage)

This is a deletion-heavy refactor with one genuinely new piece (the pure `last_assistant_text` JSONL reader). The risk surface is small and well-bounded; the largest residual is the flush-timing heuristic, deferred to #1688.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Local ollama + granite (for the surviving classification role; unit tests mock it) | `python -c "import ollama"` | Confirms the runtime still present; the change does not remove it |

The build itself needs no live ollama — every translation unit test mocks `ollama_chat`, and those tests are being deleted. Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_zero_llm_shuttle.md`

## Solution

### Key Elements

- **`last_assistant_text(transcript_path: str) -> str` (new, pure)**: reads the Claude Code JSONL transcript, finds the **most recent `type:"assistant"` entry**, and returns the concatenation of its `message.content[]` blocks where `type == "text"` (EXCLUDING `tool_use`, `tool_result`, `thinking`). Returns `""` if no assistant entry / file missing / all lines fail to parse. Fail-silent like the existing tailer; tolerate a partial trailing line (parse only complete `\n`-terminated lines, mirroring `read_transcript_telemetry`'s safe-offset logic). **Placement:** put it in `transcript_tailer.py` next to the existing JSONL parsing (preferred — keeps the JSONL contract in one module and lets it share the partial-line handling); the builder may instead place it in `granite_classifier.py` if that reads more cohesively, and must document the choice in the PR.
- **`classify_pm_prefix` (light adaptation)**: now receives clean transcript text rather than a painted frame. Its chrome-stripping internals (`PREFIX_TOKEN_ANCHORED_RE` `⏺`-anchoring, `_FRAME_ARTIFACT_RE` cut) become largely no-ops on clean input — that is fine, leave them in place (they harmlessly no-op and keep the function robust if ever fed a frame). The token-anchoring via the strict `PREFIX_TOKEN_RE` first-line path is what now carries the classification. No signature change.
- **Container call-site edits (3)**: `container.py:1033` (PM→Dev: read PM transcript → classify → write `classification.payload`), `:1077` (Dev→PM: read Dev transcript → write verbatim), `:1136` (wrap-up seed: read Dev transcript). All three PTY writes empty-guarded.
- **Deletions in `granite_classifier.py`**: the entire "Translation (the 2 ollama calls)" block plus `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments`, and the translation-only `ollama_chat` usage. `ensure_granite_model` stays (it uses the `ollama` CLI for the classification role; its docstring is corrected per Risk 1).

### Flow

PM idle → read PM transcript → `last_assistant_text` → `classify_pm_prefix` → `[/dev]` payload (verbatim) → **write payload to Dev PTY (empty-guarded)** → Dev idle → read Dev transcript → `last_assistant_text` (Dev's final authored turn) → **write to PM PTY (empty-guarded)** → PM reads Dev's actual words → `[/user]`/`[/complete]` (unchanged delivery) → human.

### Technical Approach

- **PM → Dev** (`container.py:~1031-1064`): replace `dev_prompt = extract_dev_prompt(pm_buf)` with `dev_prompt = classification.payload` (where `classification` was produced by classifying `last_assistant_text(pm_transcript)`). Remove the `extract_start`/`extract_ms` timing and the surrounding `try/except` (deleted with the call). The empty-payload compliance-miss branch (`container.py:1010`) is kept. Guard the Dev PTY write against emptiness.
- **Dev → PM** (`container.py:~1074-1092`): replace `summary = summarize_for_pm(dev_buf)` with `dev_text = last_assistant_text(dev_transcript)`. Keep `self._last_dev_report = dev_text` (now the verbatim final turn). Remove the `summarize_start`/`summarize_ms` timing and `try/except`. **Empty-guard the PTY write**: `self._pm_pty.write(dev_text or DEV_REPORT_UNAVAILABLE)` — `PTYDriver.write()` raises `PTYDriverError` on empty input (`pty_driver.py:439-440`).
- **Wrap-up seed** (`container.py:1136`): replace `summarize_for_pm(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE` with `last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE`. (The surviving outer `try/except` at `:1137` stays — it guards `_cycle_idle`.)
- **Transcript paths in scope**: inside `_route_pm_classification`, compute via the existing `_transcript_path(self.cwd, self._pm_pty._session_id)` and `_transcript_path(self.cwd, self._dev_pty._session_id)` (or read the already-populated `result.pm_transcript_path` / `result.dev_transcript_path`). If a path is `None` (unknown session_id), fall back to classifying/forwarding the painted buffer so the turn is never dropped, and log a warning.
- **`TurnRecord.granite_extract_ms` / `granite_summarize_ms`**: **DELETE both fields** and all assignment sites (`container.py:212-213` definition; the `0`/`extract_ms`/`summarize_ms` assignments at `:939, :958, :991, :1022, :1049, :1102` and `:940, :959, :992, :1023, :1050, :1103`). Archaeology confirmed NO dashboard/reflection/analytics consumer reads them. Do NOT zero-fill (violates NO LEGACY CODE TOLERANCE). `result_to_json` uses `asdict` so the JSON shrinks automatically; update `test_to_json` to drop the fields.
- **`SYSTEM_PROMPT`**: deleted (only the translation calls used it).
- **Module docstring + `container.py` loop docstring (`container.py:12-15`)**: rewrite to describe the zero-LLM transcript-content shuttle ("classify the PM's last assistant text by regex → forward Dev's last assistant text verbatim"). Any invariant that says "each ollama.chat sees only the current turn" is moot for the routing path — update it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The two `try/except Exception` blocks wrapping `extract_dev_prompt`/`summarize_for_pm` (`container.py:1034`, `:1078`) are removed with the calls. `last_assistant_text` is fail-silent (returns `""` on any I/O / parse / missing-file error) and cannot raise — add a test that a corrupt/garbage JSONL file yields `""`, not an exception.
- [ ] The wrap-up-guard `except Exception` at `container.py:1137` stays (it guards `_cycle_idle`, not the translation) — assert it still falls back to `DEV_REPORT_UNAVAILABLE`.
- [ ] **Empty-guard on the main Dev→PM PTY write** (BLOCKER): `last_assistant_text` can return `""`, and `PTYDriver.write()` raises `PTYDriverError` on empty input (`pty_driver.py:439-440`). Add a test that a no-content Dev turn forwards `DEV_REPORT_UNAVAILABLE` and **never raises** at the PTY write.

### Empty/Invalid Input Handling
- [ ] `last_assistant_text("")`-equivalent (missing file) → `""`; empty file → `""`; file with only `user`/`tool_result` entries (no assistant) → `""`; corrupt/partial-only JSONL → `""`.
- [ ] Empty `[/dev]` payload still routes through the existing compliance-miss branch (`container.py:1010`) and writes `PM_COMPLIANCE_NUDGE` — verify unchanged behavior.
- [ ] Empty `last_assistant_text(dev_transcript)` at the wrap-up seed falls back to `DEV_REPORT_UNAVAILABLE`.
- [ ] `None` transcript path (unknown session_id) → graceful fallback (classify/forward the painted buffer), no crash, warning logged.

### Error State Rendering
- [ ] The `[/user]`/`[/complete]` delivery path is unchanged — assert the human still receives PM's verbatim words (regression).
- [ ] When Dev produces no usable content, the wrap-up guard still delivers `OPERATOR_TERMINAL_MESSAGE` to the human (no silent loop).

### Routing-token re-injection (CONCERN, not blocker)
- [ ] With the classifier now running only on the PM's own structured assistant text, the surface is smaller than scraping a shared frame. But if a Dev turn's final text literally contains `⏺ [/complete]` (or another routing token) and the PM later echoes it verbatim, `classify_pm_prefix` takes the LAST anchored match and could misroute. Add a defang note in code and a test feeding a Dev report whose text contains a literal routing token, asserting the PM's *own* token (not Dev's echoed one) drives routing. Downgraded to CONCERN under the transcript mechanism.

## Test Impact

- [ ] `tests/unit/granite_container/test_granite_classifier.py` — DELETE the translation test artifacts: the `_make_ollama_response` helper (line 301), `TestExtractDevPromptMocked` (line 316), `TestSummarizeForPmMocked` (line 343), `TestTranslationTools` (line 369 — references `TRANSLATION_TOOLS`/`SYSTEM_PROMPT` and MUST be deleted), and the imports of `SYSTEM_PROMPT`, `TRANSLATION_TOOLS`, `extract_dev_prompt`, `summarize_for_pm`, `GraniteTranslationError` (lines ~24-25 and surrounding). The `classify_pm_prefix`, `_strip_ansi`, anchored-frame, and `TestEnsureGraniteModel` (line 414) classes STAY.
- [ ] `tests/unit/granite_container/` — ADD a `TestLastAssistantText` class (in the test file matching `last_assistant_text`'s placement — `test_transcript_tailer.py` if placed there, else `test_granite_classifier.py`) covering: picks the LAST assistant entry when several exist; concatenates only `text` blocks; EXCLUDES `tool_use`/`tool_result`/`thinking` blocks; returns `""` on empty/missing/corrupt file; returns `""` when only `user`/`tool_result` entries exist; tolerates a partial (non-newline-terminated) trailing line; a multi-assistant-entry fixture returns the LAST one's text.
- [ ] `tests/unit/granite_container/test_container.py` — UPDATE the dev-routing tests at patch sites lines ~185-186, ~381-382, ~848-849, ~960-961, and ~1028 (each currently `patch("agent.granite_container.container.extract_dev_prompt")` and/or `...summarize_for_pm`). Drop those patches; instead stub `last_assistant_text` (patch where it is imported in `container`) to return fixture text, and assert: the Dev PTY receives `classification.payload` verbatim (PM→Dev) and the PM PTY receives the Dev transcript's last assistant text verbatim (Dev→PM). Note: `TestClassifyDevRoutesToDev` does NOT exist — patch sites are identified by line, not class name.
- [ ] `tests/unit/granite_container/test_container.py::test_to_json` (line ~732, asserts `granite_extract_ms=50`/`granite_summarize_ms=30`) — UPDATE: the fields are DELETED from `TurnRecord`, so remove them from the constructed `TurnRecord` and from the expected JSON.
- [ ] `tests/unit/granite_container/test_container.py` — ADD: a no-content Dev turn (`last_assistant_text` → `""`) forwards `DEV_REPORT_UNAVAILABLE` to PM and never raises `PTYDriverError`; a Dev report whose text contains a literal routing token does not hijack PM routing.
- [ ] `scripts/granite_smoke_test.py` — DELETE the `extract_dev_prompt`/`summarize_for_pm` operator scenarios (4 each, lines ~166-200; they validate a removed capability). Confirmed NOT CI/launchd/cron-wired (`grep -rn granite_smoke_test` outside the file itself returns nothing). Keep any classification-token scenarios if present; if the file is left with no live scenarios, DELETE the whole file. Builder re-confirms no wiring before deleting.
- [ ] `tests/unit/granite_container/test_persona_priming.py` — NO CHANGE expected (it asserts the PM body quotes `PREFIX_TOKEN_RE`; the Dev prime *body wording* changes but its persona contract is unchanged). Verify it still passes; if it asserts the now-corrected "summarized by the operator" Dev-prime wording, UPDATE that assertion.

## Rabbit Holes

- **Scraping the painted frame instead of reading the JSONL transcript.** This is the rejected first-draft mechanism (`strip_dev_chrome`). A painted terminal buffer is the wrong surface — cut-at-first-artifact truncates real content, `─────`/`❯`/spinner glyphs collide with legitimate code/tables, and there is no reliable Dev-side anchor. Claude Code already serializes every assistant turn to JSONL; read `last_assistant_text` from there. Do NOT reintroduce any frame-scraping regex, do not pull in `pyte` or a screen-buffer model.
- **Adding Dev-persona self-summarization** to compensate for losing the 3B summary. Explicitly rejected by the product owner — the Dev prime persona contract does NOT change. (Correcting the *false wording* in `/granite:prime-dev-role` that claims Dev output is "summarized by the operator" is a separate, required doc edit — it is not self-summarization.)
- **Reintroducing summarization "just for very large turns."** The product decision accepts raw forwarding. Large-turn PM-context pressure is a documented Risk, not a reason to keep an LLM rewrite.
- **Ripping out `ensure_granite_model` / the worker startup gate.** Granite is still required for the classification role. Removing the gate would silently break bridge classification.
- **Building hook-driven turn boundaries here.** That is followup #1688. This plan stays on the existing idle-poll boundary and accepts the documented flush-timing heuristic. Do not add Stop-hook plumbing or a crash-path supervisor in this PR.
- **Re-deriving transcript paths.** The container already computes `result.pm_transcript_path` / `result.dev_transcript_path` and has `_transcript_path(cwd, session_id)` in scope. Reuse them; do not duplicate the slug logic.

## Risks

### Risk 1: Success criterion "no ollama/granite4 in `agent/granite_container/`" is literally unachievable
**Impact:** `ensure_granite_model` lives in `granite_classifier.py`, references `ollama_chat` (importability guard), the `ollama` CLI, and `granite4.1:3b` — and it MUST stay because granite is a hard worker precondition for the **classification** role (`OLLAMA_CLASSIFIER_MODEL`, used by bridge `classify_needs_response` etc.). Taking the success criterion literally would break classification.
**Mitigation:** Refine the criterion to its true intent: **no ollama call remains in the PM↔Dev *routing/translation* path.** `ensure_granite_model` and its worker gate are retained for the classification role. Update `ensure_granite_model`'s docstring (which currently claims "every PM/Dev turn is routed by an ollama call") to reflect that the PTY routing role is now zero-LLM and the gate exists for classification. The `grep` in Success Criteria is scoped to exclude `ensure_granite_model`.

### Risk 2: Flush-timing race — the "last assistant entry" may not be the completed turn
**Impact:** We read the JSONL transcript right after `_cycle_idle` reports the PTY idle. The assistant message is *normally* flushed to JSONL by then, but this is a heuristic, not a guarantee. If we read a beat early we could miss the final turn (`last_assistant_text` returns the prior turn, or `""`) and forward stale/empty content.
**Mitigation (this plan):** `last_assistant_text` reads only complete `\n`-terminated JSONL lines (mirroring `read_transcript_telemetry`'s safe-offset logic), so a half-written final line is skipped rather than mis-parsed. Empty-guard every PTY write so an early read degrades to `DEV_REPORT_UNAVAILABLE` / a compliance nudge rather than a crash. Add a test that reads a fixture with a partial trailing line and returns the last *complete* assistant entry.
**Deterministic fix (followup #1688):** a hook-driven Stop signal so we read only after the turn is provably complete — removing the idle-poll + flush heuristic entirely. Tracked separately; out of scope here.

### Risk 3: Large/tool-spammy Dev turns now reach the read-only, context-limited PM verbatim
**Impact:** A large Dev final turn could pressure the PM PTY's context window — the original reason `summarize_for_pm` existed. Note: reading only Dev's *last assistant turn* (not the whole scrollback the old `summarize_for_pm(dev_buf)` consumed) already bounds this far tighter than the rejected whole-frame approach.
**Mitigation:** Per the product decision, accept raw forwarding; do NOT reintroduce summarization. Confirm during build that the PM PTY tolerates the larger inbound payload (it is the same TUI input channel that already accepts the prime persona body, a large payload). Add a behavioral acceptance check (see Success Criteria) that the PM still routes correctly (`[/dev]`/`[/user]`/`[/complete]`) on a real, tool-spammy Dev turn fed through the new path. Document the operational characteristic in `granite-pty-production.md`. If it ever becomes a real problem, that is a separate issue — not a reason to relaunder Dev's words through a 3B model.

### Risk 4: Routing-token re-injection (CONCERN)
**Impact:** If a Dev report's final text literally contains a routing token (`⏺ [/complete]`, etc.) and the PM echoes it, `classify_pm_prefix` (which takes the LAST anchored match) could misroute.
**Mitigation:** Smaller surface than the rejected shared-frame scrape — the classifier now runs only on the PM's *own* structured assistant text. Add a defang note in code and a test (Dev report text contains a literal token; PM's own token still drives routing). Downgraded to CONCERN, not a blocker.

## Race Conditions

No new *concurrency* race conditions identified. The change is a substitution within the existing turn-boundary state machine (`_route_pm_classification`): the same `_cycle_idle` "write only to idle PTYs" invariant governs the PM→Dev write and Dev→PM write before and after. No new shared mutable state, no new async fan-out — `last_assistant_text` is a synchronous pure file read replacing a synchronous blocking ollama call. The existing two-PTY coordination is unchanged.

The one **temporal** hazard introduced is the JSONL flush-timing heuristic (read-at-idle vs. assistant-message-flushed), covered as Risk 2 and made deterministic by followup #1688. It is a single-reader timing dependency on Claude Code's own append, not a multi-writer data race.

## No-Gos (Out of Scope)

- The `[/user]` and `[/complete]` paths (`container.py:981`, `:944`) — already verbatim and zero-LLM; touching them risks regressing the working path. (Not deferred — genuinely correct as-is; modifying them is out of scope by design.)
- Dev-persona **self-summarization** — explicitly rejected by the product owner. (Not deferred — rejected.) NOTE: this is distinct from the required *wording correction* in `/granite:prime-dev-role` (removing the false "your output is summarized by the operator" claim), which IS in scope — see Documentation.
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
- [ ] Update `docs/features/granite-pty-production.md`: rewrite the routing description so the PTY operator is described as a **zero-LLM transcript-content shuttle** — classify the PM's last assistant text (read from the JSONL transcript) by regex, forward Dev's last assistant text verbatim. Specifically:
  - Lines ~65-66 ("Granite is the routing brain — every PM/Dev turn is classified **and translated** by an ollama call") → "classified by a regex parse over the session's JSONL transcript content; payloads are forwarded verbatim — no LLM rewrite on the PM↔Dev channel."
  - Lines ~395-396 (`[/dev]` turns hard-depend on local ollama via `extract_dev_prompt`/`summarize_for_pm`) → remove; the `[/dev]` path no longer depends on ollama and reads message content from the transcript.
  - Lines ~525-540 (model roles table): keep granite as the classification model; remove/rewrite the "PTY operator (PM↔Dev routing)" row that lists `Container.extract_dev_prompt`/`Container.summarize_for_pm` to "PTY operator (PM↔Dev routing) — regex classify + verbatim transcript-content forward (no model)."
  - Lines ~75-78 (`ensure_granite_model` rationale): reframe the gate as a precondition for the **classification** role, not "every PM/Dev turn is routed by an ollama call."
  - Add a short subsection on the JSONL-transcript content surface (it's the same surface the telemetry tailer reads) and the flush-timing heuristic, cross-referencing followup **#1688** as the deterministic (hook-driven) complement.
- [ ] Correct `.claude/commands/granite/prime-dev-role.md`: the persona body falsely claims Dev output is summarized by the operator — line 11 ("Your output is summarized by the granite operator and forwarded to the PM"), line 18 ("The operator will summarize your output and forward it back to the PM"), and the "the PM's summary reaches the human" wording (~line 25). Rewrite all of these to reflect **verbatim forwarding of Dev's final authored message** (e.g. "Your final message each turn is forwarded verbatim to the PM — write it as the report you want the PM to read"). This is a doc/wording edit, NOT Dev self-summarization.
- [ ] No `docs/features/README.md` index change needed (the feature already has an entry).

### External Documentation Site
- [ ] N/A — this repo has no Sphinx/MkDocs site for this area.

### Inline Documentation
- [ ] Rewrite the `granite_classifier.py` module docstring (lines 1-31): drop the "classification vs translation" framing; it is now classification-only.
- [ ] Rewrite the `container.py` loop docstring (lines 12-15): replace "call granite to extract_dev_prompt (ollama) ... summarize_for_pm (ollama)" with "read the PM's last assistant text from the JSONL transcript, classify it, forward the verbatim `[/dev]` payload to Dev; forward Dev's last assistant text verbatim to PM."
- [ ] Update `ensure_granite_model`'s docstring per Risk 1.
- [ ] Docstring for the new `last_assistant_text` helper: states the JSONL contract (last `assistant` entry, `text` blocks only, fail-silent, partial-trailing-line tolerant) and notes the flush-timing heuristic / #1688.

## Success Criteria

- [ ] No ollama call remains in the PM↔Dev **routing/translation** path. `grep -rn "ollama\|granite4" agent/granite_container/granite_classifier.py | grep -v ensure_granite_model | grep -v "^.*#"` returns no live translation call (only the surviving `ensure_granite_model` classification gate, if any references remain there).
- [ ] `extract_dev_prompt`, `summarize_for_pm`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments` are deleted from `granite_classifier.py`.
- [ ] `last_assistant_text` exists, is pure/fail-silent, returns the last assistant entry's `text` blocks only, and tolerates a partial trailing line (asserted in `TestLastAssistantText`).
- [ ] The `[/dev]` instruction written to Dev is `classification.payload` verbatim (classified from the PM transcript's last assistant text), not an LLM re-extraction (asserted in `test_container.py`).
- [ ] Dev's last assistant text is forwarded to PM verbatim, with no 3B-rewritten prose and no summarization step, and a no-content Dev turn forwards `DEV_REPORT_UNAVAILABLE` without raising `PTYDriverError` (asserted in `test_container.py`).
- [ ] `granite_extract_ms`/`granite_summarize_ms` are removed from `TurnRecord` and from `result_to_json` output (asserted in updated `test_to_json`); no zero-fill remains.
- [ ] `[/user]`/`[/complete]` human-delivery path unchanged and still verbatim (regression assertion in `test_container.py`).
- [ ] **Behavioral acceptance:** the PM routes correctly (`[/dev]`/`[/user]`/`[/complete]`) on a real, tool-spammy Dev turn fed through the new transcript path — a lightweight integration test or a manual e2e smoke with a captured JSONL trace artifact attached to the PR.
- [ ] Net-negative diff in `granite_classifier.py` (`git diff --stat` shows more deletions than insertions for that file).
- [ ] `scripts/granite_smoke_test.py` no longer references the removed translation tools (updated or deleted; confirmed not CI-wired).
- [ ] `ensure_granite_model` and the worker startup gate remain functional for the classification role.
- [ ] `.claude/commands/granite/prime-dev-role.md` no longer claims Dev output is "summarized by the operator" (verbatim-forwarding wording).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — `docs/features/granite-pty-production.md`, the prime-dev-role doc, and the inline docstrings reflect the zero-LLM transcript-content shuttle.

## Team Orchestration

### Team Members

- **Builder (classifier-de-llm)**
  - Name: shuttle-builder
  - Role: Delete the translation path in `granite_classifier.py`, add `last_assistant_text` (transcript JSONL reader), edit the three `container.py` call sites to read transcript content with empty-guarded PTY writes, delete the two dead `TurnRecord` telemetry fields, update docstrings.
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: shuttle-test-builder
  - Role: Delete translation tests (incl. `TestTranslationTools`), add `TestLastAssistantText`, update `test_container.py` to stub `last_assistant_text` and assert verbatim forwarding + empty-guard + token-defang, fix `test_to_json`, handle `granite_smoke_test.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator (shuttle)**
  - Name: shuttle-validator
  - Role: Verify all success criteria — net-negative diff, no live translation ollama call, verbatim transcript-content forwarding asserted, empty-guard, `[/user]` regression, classification gate intact, behavioral acceptance.
  - Agent Type: validator
  - Resume: true

- **Documentarian (granite-docs)**
  - Name: granite-doc
  - Role: Update `docs/features/granite-pty-production.md` and `.claude/commands/granite/prime-dev-role.md` per the Documentation section.
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
- Add `last_assistant_text(transcript_path: str) -> str` (pure, fail-silent; last `assistant` entry; `text` blocks only; partial-trailing-line tolerant). Place in `transcript_tailer.py` (preferred) or `granite_classifier.py`; document the choice.
- Edit `container.py`: PM→Dev (`~:1031-1064`) → classify `last_assistant_text(pm_transcript)`, write `classification.payload` (empty-guarded); Dev→PM (`~:1074-1092`) → `self._pm_pty.write(last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE)`; wrap-up seed (`:1136`) → `last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE`. Reuse `_transcript_path(self.cwd, pty._session_id)`; fall back to the painted buffer when the path is `None`. Remove the now-dead extract/summarize try/except and timing.
- DELETE `granite_extract_ms`/`granite_summarize_ms` from `TurnRecord` and every assignment site; `result_to_json` (asdict) drops them automatically.
- Update module docstring, container loop docstring, and `ensure_granite_model` docstring (Risk 1).

### 2. Test changes
- **Task ID**: build-tests
- **Depends On**: build-shuttle
- **Validates**: tests/unit/granite_container/test_granite_classifier.py, tests/unit/granite_container/test_container.py
- **Assigned To**: shuttle-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete translation test classes + imports in `test_granite_classifier.py` (incl. `TestExtractDevPromptMocked`, `TestSummarizeForPmMocked`, `TestTranslationTools`, `_make_ollama_response`); add `TestLastAssistantText`.
- Update `test_container.py` dev-routing tests (stub `last_assistant_text`) to assert verbatim PM→Dev and verbatim Dev→PM forwarding; add empty-guard test (`DEV_REPORT_UNAVAILABLE`, no `PTYDriverError`) and routing-token defang test; fix `test_to_json` (drop the two deleted fields).
- Update or delete `scripts/granite_smoke_test.py` (confirm not CI-wired first).

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-shuttle
- **Assigned To**: granite-doc
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/granite-pty-production.md` and `.claude/commands/granite/prime-dev-role.md` per the Documentation section.

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
| Transcript reader exists | `grep -rn "def last_assistant_text" agent/granite_container/` | output contains last_assistant_text |
| Dead telemetry fields deleted | `grep -rn "granite_extract_ms\|granite_summarize_ms" agent/granite_container/` | exit code 1 |
| Prime-dev wording corrected | `grep -in "summarized by the" .claude/commands/granite/prime-dev-role.md` | exit code 1 |
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

The mechanism pivot (read the JSONL transcript instead of scraping the painted frame) is resolved by the owner. The Dev→PM forwarding decision (verbatim, no self-summarization) and the classification-gate retention (Risk 1) are settled.

**One residual, honestly stated, deferred not blocking:** the flush-timing heuristic (read-at-idle vs. assistant-message-flushed-to-JSONL). This plan mitigates it (complete-lines-only read + empty-guards + a partial-line test) but cannot *eliminate* it on the idle-poll boundary. The deterministic elimination is followup **#1688** (hook-driven Stop signal). If the supervisor judges the heuristic unacceptable to ship even with the mitigations, the correct move is to sequence #1688 first — but the owner's stated sequencing is content-swap now (#1681), deterministic boundaries next (#1688).

**Builder decision left open (intentionally):** the placement of `last_assistant_text` (`transcript_tailer.py` preferred vs. `granite_classifier.py`) — both are acceptable; the builder documents the choice in the PR.
