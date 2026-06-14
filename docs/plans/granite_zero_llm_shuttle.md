---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-06-14
tracking: https://github.com/tomcounsell/ai/issues/1681
last_comment_id:
revision_applied: true
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

1. **`extract_dev_prompt` deleted.** The `[/dev]` write uses
   `classification.payload` — the verbatim text after the `[/dev]` token — not an
   LLM re-extraction. The one-line swap at the `[/dev]` write site
   (`container.py:1033`) is `dev_prompt = extract_dev_prompt(pm_buf)` →
   `dev_prompt = classification.payload`. The `[/user]`/`[/complete]` branches
   already write `classification.payload` directly; this aligns `[/dev]` with
   them.

   The classify **input** moves from the painted `pm_buf` to the PM's **last
   assistant text** read from the PM transcript (the three `classify_pm_prefix`
   call sites: `container.py:788` prime-turn, `:844` steady-state, `:1156`
   wrap-up). This is required so the dead painted-frame anchored path can be
   deleted (desired-outcome #3 / BLOCKER below): on clean transcript text there
   is no container-echo prefix, so the strict `PREFIX_TOKEN_RE` first-line path
   is sufficient and the `⏺`-anchoring root-cause class disappears.

   **Empty/None-read fallback is CONSERVATIVE, NOT a re-parse of `pm_buf`**
   (resolves the re-critique BLOCKER): when the transcript read is empty (flush
   race, mtime-unchanged, read miss, or `None` transcript path), the site does
   **NOT** call `classify_pm_prefix(pm_buf)` — doing so would re-parse the
   *painted* buffer through the strict/200-char path, which on a painted frame
   reads the **container echo**, not the model reply. That is exactly the
   misparse the anchored path was built to prevent, and the fallback fires
   precisely during flush-lag/startup, when painted-frame parsing is least
   reliable. Instead, the empty-read path returns a synthetic
   `ClassificationResult(destination="unknown", compliance_miss=True, payload="")`,
   which drives the existing compliance-miss branch (`container.py:929/945`):
   write `PM_COMPLIANCE_NUDGE` and re-poll on the next idle cycle. No PM turn is
   silently dropped (the turn is re-read next cycle once the transcript flushes),
   and the painted `pm_buf` is **never** parsed for routing — fully consistent
   with deleting the anchored path. A `logger.warning` marks each occurrence so
   a systemic flush-race is grep-able in `worker.log`.
2. **`summarize_for_pm` deleted.** The Dev session's **last assistant text** (its
   final authored turn) is read from the Dev transcript and forwarded to the PM
   **verbatim**. No 3B summary, no Dev self-summarization contract, no frame
   scraping. This changes the Dev→PM payload from "whole scrollback" to "Dev's
   final authored turn" — which is exactly what the PM needs to route on.
3. **The ollama *translation* path is removed** from the classifier:
   `ollama_chat` translation usage, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, the two
   translation functions, `GraniteTranslationError`, `_events_from_text`,
   `_extract_tool_calls`, `_normalize_arguments`. **Plus the dead painted-frame
   anchored path** (`PREFIX_TOKEN_ANCHORED_RE` at line 142, `_FRAME_ARTIFACT_RE`
   at line 148, and the anchored-match block at lines 284-297) — it is *live*,
   not a no-op: it fires on any `⏺`/`●` char and takes the LAST match *before*
   the strict first-line path, so a PM message that quotes Dev output or code
   containing `⏺` would misroute. It only existed to recover the model's reply
   from a painted frame where the container's echo precedes it; once the PM
   classify input is the PM's clean transcript text (outcome #1), there is no
   echo prefix and the strict `PREFIX_TOKEN_RE` first-line path is sufficient.
   Deleting it dissolves this plan's own routing-token Risk entirely. Net-negative
   diff in `granite_classifier.py`.
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
- **`classify_pm_prefix` call sites (corrected from an earlier draft):** the three live calls are `container.py:788` (prime-turn relay), `:844` (steady-state loop), and `:1156` (wrap-up guard). An earlier draft of this plan mis-cited these as 1033/1077/1136 — those are the `extract_dev_prompt`/`summarize_for_pm` sites, NOT the classify sites. Verified via `grep -n "classify_pm_prefix\|extract_dev_prompt\|summarize_for_pm" container.py`. The classify *input* must change at all three (read PM transcript), so the anchored-frame path can be deleted (see Risk 4 / Solution).
- **Telemetry-field consumer grep (archaeology artifact):** `grep -rn "granite_extract_ms\|granite_summarize_ms" . --include="*.py" --include="*.json" --include="*.yaml" | grep -v "def granite_"` returns matches ONLY in `container.py` (the `TurnRecord` definition at :212-213 and the assignment sites) and the `test_to_json` fixture at `tests/unit/granite_container/test_container.py:745-746`. **No dashboard, reflection, or analytics consumer reads either field** — confirming the fields are safe to delete (no `record["granite_extract_ms"]` access anywhere). This grep is the cited artifact backing the "no consumer" claim.

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

1. **Entry point**: PM PTY reaches idle; `_cycle_idle(self._pm_pty)` returns `pm_buf` (used **only** to detect idle — `pm_buf` is no longer a classify source at all, neither primary nor fallback; the empty-read fallback is a synthetic `unknown`, not a `pm_buf` parse).
2. **Classify** (three sites: `container.py:788` prime-turn, `:844` steady-state, `:1156` wrap-up): compute the PM transcript path (`_transcript_path(self.cwd, self._pm_pty._session_id)` or the populated `result.pm_transcript_path`), snapshot its mtime before `_cycle_idle`, read `pm_text = last_assistant_text(pm_transcript, mtime_before=...)`, then `classify_pm_prefix(pm_text)` → `ClassificationResult`. The classifier now runs on **clean structured text** with no container-echo prefix, so the strict `PREFIX_TOKEN_RE` first-line path carries the classification and the anchored-frame path is deleted. `payload` is the verbatim text after `[/dev]`. **Conservative fallback (NOT a `pm_buf` re-parse):** if `pm_text` is empty (flush race, mtime-unchanged, read miss, or `None` transcript path), synthesize `ClassificationResult(destination="unknown", compliance_miss=True, payload="")` and `logger.warning` the occurrence — this drives the existing compliance-miss branch (`PM_COMPLIANCE_NUDGE` + re-poll next cycle, `container.py:929/945`). It does **NOT** call `classify_pm_prefix(pm_buf)`: the painted buffer's strict/200-char parse reads the container echo, not the model reply, reintroducing the very misparse the anchored-path deletion removes — and the fallback fires exactly when painted parsing is least reliable. The turn is re-read next idle cycle once the transcript flushes, so nothing is dropped.
3. **PM → Dev (`destination == "dev"`)**:
   - *Today:* discard `classification.payload`, call `extract_dev_prompt(pm_buf)` → ollama → write 3B re-extraction to Dev PTY (`container.py:1033`, `:1064`).
   - *After:* `dev_prompt = classification.payload`; write it directly to Dev PTY. Empty payload already routes through the compliance-miss branch at `container.py:1010` (writes `PM_COMPLIANCE_NUDGE`) BEFORE the write site, so the write at `:1064` only ever receives non-empty text. No ollama.
4. **Dev cycle**: `_cycle_idle(self._dev_pty)` detects Dev idle (boundary only). The painted `dev_buf` is no longer parsed for content.
5. **Dev → PM**:
   - *Today:* `summarize_for_pm(dev_buf)` → ollama → write 3B summary to PM PTY (`container.py:1077`, `:1092`).
   - *After:* `dev_text = last_assistant_text(dev_transcript, mtime_before=...)` — Dev's final authored turn, structurally clean. Dev emits no routing token (it's a report), so this is forwarded verbatim. Empty-guard the PTY write: `self._pm_pty.write(dev_text or DEV_REPORT_UNAVAILABLE)` — `PTYDriver.write()` raises on empty input (`pty_driver.py:439`), so the guard is mandatory. When the guard fires (empty/stale read), `logger.warning(..., extra={"session_id": ..., "transcript_path": ..., "fallback": "DEV_REPORT_UNAVAILABLE"})` so the substitution is grep-able in `worker.log`. **Tool-only-final-turn invariant:** `last_assistant_text` walks assistant entries newest-first and returns the most recent one that has at least one `text` block (skipping a final entry that is pure `tool_use`/`thinking`). A Dev turn whose true final assistant entry has text is therefore forwarded; `DEV_REPORT_UNAVAILABLE` is reserved for the genuine no-text-anywhere case (which the prime-dev-role wording correction frames as the protocol the Dev persona must follow: end each turn with a natural-language report). No ollama.
6. **Wrap-up seed (`container.py:1136`)**:
   - *Today:* `seed = summarize_for_pm(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE`.
   - *After:* `seed = last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE`; `logger.warning` the fallback when it resolves to `DEV_REPORT_UNAVAILABLE`.
7. **Output**: `self._last_dev_report` holds the verbatim Dev final turn; PM reads it; the `[/user]`/`[/complete]` path (unchanged) delivers PM's verbatim words to the human.

**Flush-timing note:** the "last assistant entry" must be the *completed* turn. We read after `_cycle_idle` reports idle, by which point Claude Code has normally flushed the assistant message to the JSONL — but this is a heuristic, not a guarantee. Two distinct failure modes:
1. **Half-written final line** — `last_assistant_text` reads only complete `\n`-terminated JSONL lines (ignoring any partial trailing line, exactly as the tailer does), so a half-written line is skipped rather than mis-parsed.
2. **Stale-but-complete read** (the subtle one) — if the *current* turn hasn't flushed at all but *prior* turns are fully flushed, a naive "last assistant entry" read returns the PRIOR turn's text: non-empty, passing the empty-guard, and forwarded verbatim as if it were the current turn. The mitigation is an **mtime snapshot**: the caller records `os.path.getmtime(transcript)` *before* `_cycle_idle` polls, and passes it as `mtime_before` to `last_assistant_text`. If the file's mtime has not advanced since the snapshot, the current turn was not flushed during this idle cycle → return `""` (treated as a flush-miss → fallback + `logger.warning`). This is a one-line check that does NOT require #1688. (#1688's hook-driven Stop signal still supersedes the whole heuristic later.)

The deterministic fix (a hook-driven Stop signal so we read only after the turn is provably complete) is followup issue **#1688**.

The `[/user]` and `[/complete]` paths (`container.py:981`, `:948`) already use `classification.payload` directly and make **no** ollama call. With this change they classify `pm_text` (the same clean PM-transcript read, since all three classify sites switch input) rather than `pm_buf`, but their *delivery* logic is otherwise unchanged and **out of scope** (do not alter their human-delivery behavior; they only benefit from the cleaner classify input).

## Architectural Impact

- **New dependencies**: None added. The new `last_assistant_text` reads the same JSONL surface the tailer already consumes (stdlib `json` + file I/O). Net removal of the `ollama` translation usage from `granite_classifier.py` (the runtime stays only insofar as `ensure_granite_model` needs it for the classification role — see Risk 1).
- **Interface changes**: `extract_dev_prompt`, `summarize_for_pm`, `GraniteTranslationError`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments`, `PREFIX_TOKEN_ANCHORED_RE`, `_FRAME_ARTIFACT_RE` removed from the classifier's module surface. `classify_pm_prefix` keeps its signature but loses the anchored-frame branch. One new pure function added: `last_assistant_text(transcript_path: str, *, mtime_before: float | None = None) -> str`. The two dead `TurnRecord` telemetry fields (`granite_extract_ms`, `granite_summarize_ms`) are **deleted** (see Test Impact); one aggregate field — `ContainerResult.transcript_fallback_count: int` — is **added** to preserve a run-level fallback signal (see Solution / Test Impact).
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

- **`last_assistant_text(transcript_path: str, *, mtime_before: float | None = None) -> str` (new, pure)**: reads the Claude Code JSONL transcript and returns the concatenation of `text` blocks from the **most recent `type:"assistant"` entry that has at least one `text` block** (walking newest-first; EXCLUDING `tool_use`, `tool_result`, `thinking` — and skipping a final entry that is pure tool/thinking with no text, so a tool-only final turn does not collapse to empty when an earlier textual turn exists). Returns `""` if: no assistant entry has text / file missing / all lines fail to parse / **the file's mtime has not advanced past `mtime_before`** (stale-but-complete guard: the current turn was not flushed during this idle cycle). Fail-silent like the existing tailer; tolerate a partial trailing line (parse only complete `\n`-terminated lines, mirroring `read_transcript_telemetry`'s safe-offset logic). **Placement:** put it in `transcript_tailer.py` next to the existing JSONL parsing (preferred — keeps the JSONL contract in one module and lets it share the partial-line handling); the builder may instead place it in `granite_classifier.py` if that reads more cohesively, and must document the choice in the PR.
- **`classify_pm_prefix` (real change — delete the anchored-frame path)**: now receives clean transcript text rather than a painted frame, so the container-echo prefix that the `⏺`-anchored path existed to skip is no longer present. **Delete** the anchored-match block (`granite_classifier.py:284-297`), `PREFIX_TOKEN_ANCHORED_RE` (line 142), and `_FRAME_ARTIFACT_RE` (line 148). The anchored path is NOT a harmless no-op — it fires on any `⏺`/`●` and takes the LAST match before the strict path, misrouting any PM message that quotes a `⏺`. After deletion, classification flows: strip ANSI → strict `PREFIX_TOKEN_RE` on the first non-empty line → `PREFIX_TOKEN_FALLBACK_RE` on the first 200 chars → `unknown`. No signature change. The `_strip_ansi` call stays (transcript text is clean but the call is cheap and defensive). Update the anchored-frame tests in `test_granite_classifier.py` (`TestAnchoredPaintedFrames` / the `⏺` tests) — DELETE them (they validate deleted behavior).
- **Container call-site edits**:
  - **PM classify input (3 sites)**: `container.py:788` (prime-turn), `:844` (steady-state), `:1156` (wrap-up) — read `last_assistant_text(pm_transcript, mtime_before=...)`. If `pm_text` is non-empty, `classify_pm_prefix(pm_text)`. If it is empty or the transcript path is `None`, **do NOT call `classify_pm_prefix(pm_buf)`** — synthesize `ClassificationResult(destination="unknown", compliance_miss=True, payload="", raw_first_line="")` and `logger.warning` (so the existing `destination == "unknown"` branch writes `PM_COMPLIANCE_NUDGE` and re-polls next cycle). Re-parsing the painted `pm_buf` would read the container echo, not the model reply — the exact misparse the anchored-path deletion removes, and it fires precisely during flush-lag/startup. A small helper (e.g. `_unknown_classification()`) keeps the three sites identical.
  - **PM→Dev payload (`:1033`)**: `dev_prompt = classification.payload` (delete `extract_dev_prompt(pm_buf)` and its `extract_start`/`extract_ms`/`try-except`). The write at `:1064` is unchanged (non-empty guaranteed by the `:1010` compliance branch).
  - **Dev→PM (`:1077`)**: `dev_text = last_assistant_text(dev_transcript, mtime_before=...)`; `self._pm_pty.write(dev_text or DEV_REPORT_UNAVAILABLE)` (empty-guarded + `logger.warning` on fallback); `self._last_dev_report = dev_text`.
  - **Wrap-up seed (`:1136`)**: `seed = last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE` (+ `logger.warning` on fallback).
- **Deletions in `granite_classifier.py`**: the entire "Translation (the 2 ollama calls)" block plus `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments`, the translation-only `ollama_chat` usage, AND the anchored-frame path + its two regexes (above). `ensure_granite_model` stays (it uses the `ollama` CLI for the classification role; its docstring is corrected per Risk 1).

### Flow

PM idle → read PM transcript → `last_assistant_text` → `classify_pm_prefix` → `[/dev]` payload (verbatim) → **write payload to Dev PTY (empty-guarded)** → Dev idle → read Dev transcript → `last_assistant_text` (Dev's final authored turn) → **write to PM PTY (empty-guarded)** → PM reads Dev's actual words → `[/user]`/`[/complete]` (unchanged delivery) → human.

### Technical Approach

- **PM classify input** (`container.py:788`, `:844`, `:1156`): before each `classify_pm_prefix(...)`, snapshot `mtime_before = os.path.getmtime(pm_transcript)` (guarded for missing file) prior to the preceding `_cycle_idle`, then `pm_text = last_assistant_text(pm_transcript, mtime_before=mtime_before)`. If `pm_text` is truthy → `classify_pm_prefix(pm_text)`. If `pm_text` is falsy or the transcript path is `None` → `logger.warning` and use a synthetic `ClassificationResult(destination="unknown", compliance_miss=True, payload="", raw_first_line="")` (via a tiny `_unknown_classification()` helper). **Do NOT call `classify_pm_prefix(pm_buf)`** — the painted buffer's strict/200-char parse reads the container echo, not the model reply, reintroducing the painted-frame misparse the anchored-path deletion removes; and this fallback fires precisely during flush-lag/startup where painted parsing is least reliable. The `unknown` result drives the existing compliance-miss branch (`container.py:929/945`): write `PM_COMPLIANCE_NUDGE`, re-poll next idle cycle (the turn is re-read once the transcript flushes — not dropped). (The mtime snapshot must be taken before the idle wait so a fresh flush is detectable.)
- **PM → Dev** (`container.py:~1031-1064`): replace `dev_prompt = extract_dev_prompt(pm_buf)` with `dev_prompt = classification.payload`. Remove the `extract_start`/`extract_ms` timing and the surrounding `try/except` (deleted with the call). The empty-payload compliance-miss branch (`container.py:1010`) is kept and runs before the write, so the `:1064` write is non-empty by construction; no extra guard needed at `:1064`.
- **Dev → PM** (`container.py:~1074-1092`): replace `summary = summarize_for_pm(dev_buf)` with `dev_text = last_assistant_text(dev_transcript)`. Keep `self._last_dev_report = dev_text` (now the verbatim final turn). Remove the `summarize_start`/`summarize_ms` timing and `try/except`. **Empty-guard the PTY write**: `self._pm_pty.write(dev_text or DEV_REPORT_UNAVAILABLE)` — `PTYDriver.write()` raises `PTYDriverError` on empty input (`pty_driver.py:439-440`).
- **Wrap-up seed** (`container.py:1136`): replace `summarize_for_pm(dev_buf) if dev_buf.strip() else DEV_REPORT_UNAVAILABLE` with `last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE`. (The surviving outer `try/except` at `:1137` stays — it guards `_cycle_idle`.)
- **Transcript paths in scope**: inside `_route_pm_classification`, compute via the existing `_transcript_path(self.cwd, self._pm_pty._session_id)` and `_transcript_path(self.cwd, self._dev_pty._session_id)` (or read the already-populated `result.pm_transcript_path` / `result.dev_transcript_path`). If a path is `None` (unknown session_id), the PM-side classify degrades to the synthetic `unknown` result above (NOT a `pm_buf` re-parse), and the Dev-side forward degrades to `DEV_REPORT_UNAVAILABLE` — both with a `logger.warning`, so the turn is never silently dropped.
- **`TurnRecord.granite_extract_ms` / `granite_summarize_ms`**: **DELETE both fields** and all assignment sites (`container.py:212-213` definition; the `0`/`extract_ms`/`summarize_ms` assignments at `:939, :958, :991, :1022, :1049, :1102` and `:940, :959, :992, :1023, :1050, :1103`). Archaeology confirmed NO dashboard/reflection/analytics consumer reads them. Do NOT zero-fill (violates NO LEGACY CODE TOLERANCE). `result_to_json` uses `asdict` so the JSON shrinks automatically; update `test_to_json` to drop the fields.
- **Aggregate fallback signal — `ContainerResult.transcript_fallback_count: int = 0` (new)** (resolves the re-critique CONCERN about losing all telemetry): deleting the two per-turn `granite_*_ms` timing fields removes the only per-turn signal on this path. Preserve a minimal **aggregate** signal by adding one counter to `ContainerResult` (next to the existing `parse_failures` / `classification_compliance_misses` counters at `container.py:240-241`). Increment it once per fallback substitution — every PM-classify empty/`None` → synthetic-`unknown`, every Dev→PM `DEV_REPORT_UNAVAILABLE`, and every wrap-up-seed `DEV_REPORT_UNAVAILABLE`. This is the run-level rollup that complements the per-occurrence `logger.warning` lines: a single `result_to_json` field (auto-serialized via `asdict`) tells an operator at a glance whether the transcript-read path is degrading systemically, without re-introducing per-turn LLM-timing fields. (This is NOT new machinery for the flush race — it is observability for an existing degradation path; the race itself stays mitigated, not closed — see Risk 2.)
- **`SYSTEM_PROMPT`**: deleted (only the translation calls used it).
- **`ClassificationResult` docstring** (`granite_classifier.py:160-184`): the `payload` field doc currently says "for `dev` and `user`, the translation call's output." After the refactor `payload` is always the verbatim text following the prefix token. Replace "the translation call's output" with "the verbatim text following the prefix token."
- **Module docstring + `container.py` loop docstring (`container.py:12-15`)**: rewrite to describe the zero-LLM transcript-content shuttle ("classify the PM's last assistant text by regex → forward Dev's last assistant text verbatim"). Any invariant that says "each ollama.chat sees only the current turn" is moot for the routing path — update it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The two `try/except Exception` blocks wrapping `extract_dev_prompt`/`summarize_for_pm` (`container.py:1034`, `:1078`) are removed with the calls. `last_assistant_text` is fail-silent (returns `""` on any I/O / parse / missing-file error) and cannot raise — add a test that a corrupt/garbage JSONL file yields `""`, not an exception.
- [ ] The wrap-up-guard `except Exception` at `container.py:1137` stays (it guards `_cycle_idle`, not the translation) — assert it still falls back to `DEV_REPORT_UNAVAILABLE`.
- [ ] **Empty-guard on the main Dev→PM PTY write** (BLOCKER): `last_assistant_text` can return `""`, and `PTYDriver.write()` raises `PTYDriverError` on empty input (`pty_driver.py:439`). Add a test that a no-content Dev turn forwards `DEV_REPORT_UNAVAILABLE` and **never raises** at the PTY write.
- [ ] **Fallback observability** (CONCERN): every fallback substitution (Dev→PM `DEV_REPORT_UNAVAILABLE`, wrap-up seed `DEV_REPORT_UNAVAILABLE`, PM-classify empty/`None`-read → synthetic `unknown`) emits a `logger.warning` with `extra={"session_id", "transcript_path", "fallback"}` AND increments `result.transcript_fallback_count`. Assert via `assertLogs` in the empty-content test that the warning fires, and assert the counter advances — a systemic flush-race after a Claude Code version bump must leave both a grep-able `worker.log` line and a run-level aggregate, not loop silently. The PM-classify fallback is the **conservative `unknown`** path (never a `pm_buf` re-parse).

### Empty/Invalid Input Handling
- [ ] `last_assistant_text("")`-equivalent (missing file) → `""`; empty file → `""`; file with only `user`/`tool_result` entries (no assistant) → `""`; corrupt/partial-only JSONL → `""`.
- [ ] **Stale-but-complete guard** (BLOCKER): two complete assistant entries exist but the file's mtime has NOT advanced past `mtime_before` → `last_assistant_text` returns `""` (so the caller falls back rather than forwarding the prior turn as current). Add a test that constructs this exact condition.
- [ ] **Tool-only final assistant turn** (CONCERN): the most recent assistant entry is pure `tool_use` (no `text` block) but an earlier assistant entry has text → `last_assistant_text` returns the earlier entry's text (does NOT collapse to `""`). And the genuine no-text-anywhere case → `""` → caller forwards `DEV_REPORT_UNAVAILABLE` (the intended degradation; the prime-dev-role wording makes "end each turn with a text report" the protocol).
- [ ] Empty `[/dev]` payload still routes through the existing compliance-miss branch (`container.py:1010`) and writes `PM_COMPLIANCE_NUDGE` — verify unchanged behavior.
- [ ] Empty `last_assistant_text(dev_transcript)` at the wrap-up seed falls back to `DEV_REPORT_UNAVAILABLE`.
- [ ] `None` transcript path (unknown session_id) → graceful fallback: PM-side → synthetic `unknown` (`PM_COMPLIANCE_NUDGE`, NOT a `pm_buf` re-parse); Dev-side → `DEV_REPORT_UNAVAILABLE`. No crash, `logger.warning` fires and `transcript_fallback_count` increments (assert via `assertLogs`).

### Error State Rendering
- [ ] The `[/user]`/`[/complete]` delivery path is unchanged — assert the human still receives PM's verbatim words (regression).
- [ ] When Dev produces no usable content, the wrap-up guard still delivers `OPERATOR_TERMINAL_MESSAGE` to the human (no silent loop).

### Routing-token re-injection (RESOLVED by anchored-path deletion)
- [ ] With the anchored-frame path **deleted** and the PM classify input being the PM's own clean transcript text, classification flows only through the strict `PREFIX_TOKEN_RE` first-line match (then the 200-char fallback). A mid-body echoed token (e.g. the PM quoting Dev's `[/complete]` *after* its own `[/dev]` on line 1) can no longer hijack routing, because the strict path keys on the first non-empty line only — not the LAST anchored match. Add a regression test: a PM transcript turn whose first line is `[/dev] ...` and whose body later contains a literal `⏺ [/complete]` still routes to `dev`. This is the test that proves the ordering-attack class is closed by the deletion (not merely downgraded).

## Test Impact

- [ ] `tests/unit/granite_container/test_granite_classifier.py` — DELETE the translation test artifacts: the `_make_ollama_response` helper (line 301), `TestExtractDevPromptMocked` (line 316), `TestSummarizeForPmMocked` (line 343), `TestTranslationTools` (line 369 — references `TRANSLATION_TOOLS`/`SYSTEM_PROMPT` and MUST be deleted), and the imports of `SYSTEM_PROMPT`, `TRANSLATION_TOOLS`, `extract_dev_prompt`, `summarize_for_pm`, `GraniteTranslationError` (lines ~24-25 and surrounding). **ALSO DELETE the anchored-frame tests** in `TestAnchoredPaintedFrames` (class at line 111: `test_anchored_token_wins_over_echo`, `test_anchored_token_collapsed_whitespace`, `test_anchored_beats_nudge_echo_poisoning`, `test_last_anchored_match_wins`, `test_payload_cut_at_frame_artifacts`) — these validate the deleted anchored path. KEEP `test_clean_synthetic_input_unaffected` (currently inside `TestAnchoredPaintedFrames` at line 170 — move it into `TestClassifyPmPrefix` since the anchored class is being emptied/deleted). The `TestClassifyPmPrefix` (line 37, strict/fallback first-line), `TestAnsiStripping` (191), `TestCursorPositionedSpacingSurvives` (226), and `TestEnsureGraniteModel` (line 414) classes STAY.
- [ ] `tests/unit/granite_container/` — ADD a `TestLastAssistantText` class (in the test file matching `last_assistant_text`'s placement — `test_transcript_tailer.py` if placed there, else `test_granite_classifier.py`) covering: picks the LAST text-bearing assistant entry when several exist; concatenates only `text` blocks; EXCLUDES `tool_use`/`tool_result`/`thinking` blocks; returns `""` on empty/missing/corrupt file; returns `""` when only `user`/`tool_result` entries exist; tolerates a partial (non-newline-terminated) trailing line; a multi-assistant-entry fixture returns the LAST text-bearing one's text; **stale-but-complete: two complete assistant entries but `mtime_before` >= file mtime → returns `""`**; **tool-only final turn: last assistant entry is pure `tool_use` but an earlier entry has text → returns the earlier text; no text anywhere → `""`**.
- [ ] `tests/unit/granite_container/test_container.py` — UPDATE the dev-routing tests at patch sites lines ~185-186, ~381-382, ~848-849, ~960-961, and ~1028 (each currently `patch("agent.granite_container.container.extract_dev_prompt")` and/or `...summarize_for_pm`). Drop those patches; instead stub `last_assistant_text` (patch where it is imported in `container`) to return fixture text, and assert: the Dev PTY receives `classification.payload` verbatim (PM→Dev — note `classification` is now produced from the stubbed PM-transcript read) and the PM PTY receives the Dev transcript's last assistant text verbatim (Dev→PM). Because the PM classify input now reads the transcript, tests must stub `last_assistant_text` to return the PM's `[/dev] ...` text for the PM-side read and the Dev report for the Dev-side read (keyed by transcript path, or via `side_effect`). Note: `TestClassifyDevRoutesToDev` does NOT exist — patch sites are identified by line, not class name.
- [ ] `tests/unit/granite_container/test_container.py` — ADD: a PM-classify conservative-fallback test where the PM transcript read returns `""` (or path `None`) → the turn classifies as synthetic `unknown` (NOT a `classify_pm_prefix(pm_buf)` re-parse), writes `PM_COMPLIANCE_NUDGE`, a `logger.warning` fires (assert via `assertLogs`), and `result.transcript_fallback_count` increments. **Negative assertion (proves the BLOCKER is closed):** `classify_pm_prefix` is NOT called with `pm_buf` on the empty-read path — assert via a `patch`/spy on `classify_pm_prefix` that it received either the transcript text or nothing on that branch, never the painted `pm_buf`. Construct a `pm_buf` that, if it WERE parsed, would misroute (e.g. a painted frame whose echo line contains `[/complete]`) and assert it does NOT route to `complete`.
- [ ] `tests/unit/granite_container/test_container.py::test_to_json` (line ~732, asserts `granite_extract_ms=50`/`granite_summarize_ms=30`) — UPDATE: the two `granite_*_ms` fields are DELETED from `TurnRecord`, so remove them from the constructed `TurnRecord` and from the expected JSON; ADD `transcript_fallback_count` to the expected `ContainerResult` JSON (it serializes via `asdict`, default `0`).
- [ ] `tests/unit/granite_container/test_container.py` — ADD: a no-content Dev turn (`last_assistant_text` → `""`) forwards `DEV_REPORT_UNAVAILABLE` to PM and never raises `PTYDriverError`, and emits the fallback `logger.warning` (assert via `assertLogs`); a PM turn whose first line is `[/dev]` and whose body contains a literal `⏺ [/complete]` still routes to `dev` (ordering-attack regression, proves the anchored-path deletion closed the class).
- [ ] `scripts/granite_smoke_test.py` — DELETE the `extract_dev_prompt`/`summarize_for_pm` operator scenarios (4 each, lines ~166-200; they validate a removed capability). Confirmed NOT CI/launchd/cron-wired (`grep -rn granite_smoke_test` outside the file itself returns nothing). Keep any classification-token scenarios if present; if the file is left with no live scenarios, DELETE the whole file. Builder re-confirms no wiring before deleting.
- [ ] `tests/unit/granite_container/test_persona_priming.py` — Verify it still passes after the Dev-prime wording correction (verbatim forwarding + "end each turn with a natural-language report"). If it asserts the now-removed "summarized by the operator" Dev-prime wording, UPDATE that assertion to the verbatim-forwarding wording. The PM-body `PREFIX_TOKEN_RE` assertion is unchanged.

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
**Mitigation:** Refine the criterion to its true intent: **no ollama call remains in the PM↔Dev *routing/translation* path.** `ensure_granite_model` and its worker gate are retained for the classification role. Update `ensure_granite_model`'s docstring (which currently claims "every PM/Dev turn is routed by an ollama call") to reflect that the PTY routing role is now zero-LLM and the gate exists for classification. The Success-Criteria check uses the **call-invocation grep** `grep -n "ollama_chat(" granite_classifier.py` (returns 0 after deletion) — NOT a substring `ollama`/`granite4` grep, which would false-fail by matching the `from ollama import` line and `ensure_granite_model`'s legitimate `subprocess.run(["ollama", ...])` body.

### Risk 2: Flush-timing race — the "last assistant entry" may not be the completed turn
**Impact:** We read the JSONL transcript right after `_cycle_idle` reports the PTY idle. The assistant message is *normally* flushed to JSONL by then, but this is a heuristic, not a guarantee. Two failure modes: (a) a half-written final line, and (b) **stale-but-complete** — the current turn hasn't flushed at all but prior turns have, so a naive read returns the PRIOR turn's text (non-empty, passes the empty-guard, forwarded as if current).
**Mitigation (this plan) — NARROWS the window; does NOT close it.** Be honest: on the idle-poll boundary the stale-but-complete window cannot be *eliminated* without a deterministic turn signal (#1688). The mtime snapshot makes the common case safe; a residual race remains and is documented here, not papered over.
- (a) `last_assistant_text` reads only complete `\n`-terminated JSONL lines (mirroring `read_transcript_telemetry`'s safe-offset logic), so a half-written final line is skipped rather than mis-parsed. (This sub-case IS closed.)
- (b) **mtime snapshot (narrows (b), does not close it)** — the caller records `os.path.getmtime(transcript)` before `_cycle_idle` polls and passes it as `mtime_before`; if the file's mtime has not advanced, the current turn was not flushed → `last_assistant_text` returns `""` → caller falls back (not the stale prior turn). **Residual window:** mtime has 1-second granularity on some filesystems and the check is "advanced past `mtime_before`," so a *prior* turn that flushes within the same coarse mtime tick as the snapshot — or a current-turn write that lands between the snapshot and the idle poll but whose assistant line is not yet the last complete line — can still let a stale-but-complete prior turn through. The mtime guard catches the dominant case (no write at all during the cycle); it is a *narrowing*, not a guarantee. One-line check; does NOT require #1688.
- Empty-guard every PTY write so an early/stale read degrades to `DEV_REPORT_UNAVAILABLE` / a compliance nudge (with a `logger.warning` and a `transcript_fallback_count` increment) rather than a crash. **A stale-but-complete read that slips past the mtime guard is the residual exposure: it is non-empty, passes the empty-guard, and is forwarded verbatim as if current.** Empty-guards do NOT catch this case — only the mtime narrowing reduces its frequency, and only #1688 removes it. Tests: a partial-trailing-line fixture returns the last *complete* entry; an mtime-unchanged fixture returns `""`.
**Residual risk acceptance:** the narrowed stale-but-complete window is accepted for this content-swap PR (the owner's stated sequencing is content-swap now, deterministic boundaries next). It is the load-bearing reason #1688 exists.
**Deterministic fix (followup #1688):** a hook-driven Stop signal so we read only after the turn is provably complete — removing the idle-poll + flush heuristic entirely, which is the only thing that *closes* the residual window. Tracked separately; out of scope here.

### Risk 3: Large/tool-spammy Dev turns now reach the read-only, context-limited PM verbatim
**Impact:** A large Dev final turn could pressure the PM PTY's context window — the original reason `summarize_for_pm` existed. Note: reading only Dev's *last assistant turn* (not the whole scrollback the old `summarize_for_pm(dev_buf)` consumed) already bounds this far tighter than the rejected whole-frame approach.
**Mitigation:** Per the product decision, accept raw forwarding; do NOT reintroduce summarization. Confirm during build that the PM PTY tolerates the larger inbound payload (it is the same TUI input channel that already accepts the prime persona body, a large payload). Add a behavioral acceptance check (see Success Criteria) that the PM still routes correctly (`[/dev]`/`[/user]`/`[/complete]`) on a real, tool-spammy Dev turn fed through the new path. Document the operational characteristic in `granite-pty-production.md`. If it ever becomes a real problem, that is a separate issue — not a reason to relaunder Dev's words through a 3B model.

### Risk 3.5: Tool-only final Dev turn → false `DEV_REPORT_UNAVAILABLE`
**Impact:** If Dev's *final* assistant entry is pure `tool_use`/`thinking` with no `text` block, a naive "last assistant entry, text blocks only" read returns `""` → forwards `DEV_REPORT_UNAVAILABLE` even though Dev did real work. This is strictly more likely than the old `summarize_for_pm(dev_buf)` path (which consumed the whole scrollback).
**Mitigation:** `last_assistant_text` walks assistant entries **newest-first and returns the most recent one that has a `text` block** — so a tool-only *final* entry is skipped in favor of the preceding textual turn. `DEV_REPORT_UNAVAILABLE` is reserved for the genuine no-text-anywhere case. The prime-dev-role wording correction makes "end each turn with a natural-language report" the explicit Dev protocol, so a fully text-less turn is a protocol violation whose intended degradation is `DEV_REPORT_UNAVAILABLE` (asserted in a test). Both branches are covered in `TestLastAssistantText`.

### Risk 4: Routing-token re-injection — DISSOLVED by deleting the anchored path
**Original impact:** `classify_pm_prefix` took the LAST anchored (`⏺`) match, so a PM turn that echoed Dev's `[/complete]` *after* its own `[/dev]` could misroute (the echoed token wins).
**Resolution:** The anchored-frame path is **deleted** (see Solution / desired-outcome #3). With the PM classify input now the PM's clean transcript text and only the strict first-line `PREFIX_TOKEN_RE` path live, the LAST-match hijack is structurally impossible — classification keys on the first non-empty line only. This is not a downgrade to CONCERN; the failure class is removed. A regression test (PM first line `[/dev]`, body contains a literal `⏺ [/complete]`, still routes `dev`) locks it in.

**Closed on the fallback path too (re-critique BLOCKER):** an earlier revision left the empty-read fallback calling `classify_pm_prefix(pm_buf)`. That reintroduced the painted-frame misparse — the strict/200-char parse of a *painted* `pm_buf` reads the container echo, not the model reply — and it fired precisely during flush-lag/startup (when the anchored path's protection was most needed). **Fix:** the empty/`None`-read fallback no longer parses `pm_buf` at all; it returns a synthetic `unknown` result (compliance nudge + re-poll next cycle). The painted buffer is **never** a routing-classification input anywhere in the system. A negative test asserts `classify_pm_prefix` is never called with `pm_buf`, and a painted-`pm_buf`-that-would-misroute fixture confirms it does not route. This makes the anchored-path deletion fully consistent: there is no surviving code path that parses paint for routing.

## Race Conditions

No new *concurrency* race conditions identified. The change is a substitution within the existing turn-boundary state machine (`_route_pm_classification`): the same `_cycle_idle` "write only to idle PTYs" invariant governs the PM→Dev write and Dev→PM write before and after. No new shared mutable state, no new async fan-out — `last_assistant_text` is a synchronous pure file read replacing a synchronous blocking ollama call. The existing two-PTY coordination is unchanged.

The one **temporal** hazard introduced is the JSONL flush-timing heuristic (read-at-idle vs. assistant-message-flushed), covered as Risk 2 and made deterministic by followup #1688. It is a single-reader timing dependency on Claude Code's own append, not a multi-writer data race.

## No-Gos (Out of Scope)

- The `[/user]` and `[/complete]` paths (`container.py:981`, `:948`) — already verbatim and zero-LLM; touching them risks regressing the working path. (Not deferred — genuinely correct as-is; modifying them is out of scope by design.)
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
- [ ] Correct `.claude/commands/granite/prime-dev-role.md`: the persona body falsely claims Dev output is summarized by the operator — line 11 ("Your output is summarized by the granite operator and forwarded to the PM"), line 18 ("The operator will summarize your output and forward it back to the PM"), and the "the PM's summary reaches the human" wording (line 25). Rewrite all of these to reflect **verbatim forwarding of Dev's final authored message** (e.g. "Your final message each turn is forwarded verbatim to the PM — write it as the report you want the PM to read"). **Also add the end-turn-with-text protocol** the tool-only invariant relies on: state explicitly that each turn must END with a natural-language text report (not a bare tool call), because only the final assistant turn's text is forwarded — a text-less final turn degrades to `DEV_REPORT_UNAVAILABLE`. This is a doc/wording edit, NOT Dev self-summarization.
- [ ] No `docs/features/README.md` index change needed (the feature already has an entry).

### External Documentation Site
- [ ] N/A — this repo has no Sphinx/MkDocs site for this area.

### Inline Documentation
- [ ] Rewrite the `granite_classifier.py` module docstring (lines 1-31): drop the "classification vs translation" framing and the "each ollama.chat sees only the current turn" invariant; it is now classification-only (a regex parse, no ollama on the routing path).
- [ ] Correct the `ClassificationResult` docstring (`granite_classifier.py:160-184`): replace "for `dev` and `user`, the translation call's output" with "the verbatim text following the prefix token" (NIT — `payload` is no longer a translation output).
- [ ] Remove the now-stale comments above `PREFIX_TOKEN_ANCHORED_RE` / `_FRAME_ARTIFACT_RE` (lines ~132-153) — they are deleted with the regexes.
- [ ] Rewrite the `container.py` loop docstring (lines 12-15): replace "call granite to extract_dev_prompt (ollama) ... summarize_for_pm (ollama)" with "read the PM's last assistant text from the JSONL transcript, classify it, forward the verbatim `[/dev]` payload to Dev; forward Dev's last assistant text verbatim to PM."
- [ ] Update `ensure_granite_model`'s docstring per Risk 1.
- [ ] Docstring for the new `last_assistant_text` helper: states the JSONL contract (last `assistant` entry, `text` blocks only, fail-silent, partial-trailing-line tolerant) and notes the flush-timing heuristic / #1688.

## Success Criteria

- [ ] No ollama **call** remains in the PM↔Dev routing/translation path. **Use the call-invocation grep, not a substring grep:** `grep -n "ollama_chat(" agent/granite_container/granite_classifier.py` returns **0 matches** (exit 1). The two translation calls are at lines 391/426 today; after deletion none remain — the surviving `if ollama_chat is None:` guard (no open-paren-as-call) and the `from ollama import ...` line do NOT match `ollama_chat(`. (The earlier substring grep `grep "ollama\|granite4" ... | grep -v ensure_granite_model | grep -v "^.*#"` was REJECTED — it false-fails a correct end state by matching the line-44 `from ollama import chat as ollama_chat` import that `ensure_granite_model` legitimately needs; that import is not on an `ensure_granite_model` line and contains no `#`, so neither filter excludes it.) **Proof (run at plan time against the to-be-deleted lines):** `grep -n "ollama_chat(" granite_classifier.py | grep -v "^391:" | grep -v "^426:"` → no output, exit 1, confirming the only `ollama_chat(` calls are the two translation calls being deleted, so the end-state grep returns clean.
- [ ] `extract_dev_prompt`, `summarize_for_pm`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments` are deleted from `granite_classifier.py`.
- [ ] `last_assistant_text` exists, is pure/fail-silent, returns the last assistant entry's `text` blocks only, and tolerates a partial trailing line (asserted in `TestLastAssistantText`).
- [ ] The `[/dev]` instruction written to Dev is `classification.payload` verbatim (classified from the PM transcript's last assistant text), not an LLM re-extraction (asserted in `test_container.py`).
- [ ] Dev's last assistant text is forwarded to PM verbatim, with no 3B-rewritten prose and no summarization step, and a no-content Dev turn forwards `DEV_REPORT_UNAVAILABLE` without raising `PTYDriverError` (asserted in `test_container.py`).
- [ ] `granite_extract_ms`/`granite_summarize_ms` are removed from `TurnRecord` and from `result_to_json` output (asserted in updated `test_to_json`); no zero-fill remains. The repo-wide grep `grep -rn "granite_extract_ms\|granite_summarize_ms" . --include="*.py" --include="*.json" --include="*.yaml" | grep -v "def granite_"` returns 0 matches (no consumer reads the deleted fields — verified at plan time: only `container.py` and the `test_to_json` fixture referenced them).
- [ ] The painted-frame anchored path (`PREFIX_TOKEN_ANCHORED_RE`, `_FRAME_ARTIFACT_RE`, the 284-297 block) is deleted; classification flows only through `PREFIX_TOKEN_RE` (strict first line) → `PREFIX_TOKEN_FALLBACK_RE` (first 200 chars) → `unknown`. The PM classify input is the PM's transcript text; the empty/`None`-read fallback is a synthetic `unknown` (compliance nudge + re-poll), **never** a `classify_pm_prefix(pm_buf)` re-parse of the painted buffer.
- [ ] `last_assistant_text` accepts `mtime_before` and returns `""` when the transcript mtime has not advanced (stale-but-complete guard), and skips a tool-only final entry in favor of the most recent text-bearing one (asserted in `TestLastAssistantText`).
- [ ] Every fallback substitution emits a grep-able `logger.warning` AND increments `ContainerResult.transcript_fallback_count` (Dev→PM `DEV_REPORT_UNAVAILABLE`, wrap-up seed, PM-classify empty/`None`→synthetic `unknown`) — asserted via `assertLogs` and a counter assertion. The PM-classify fallback is the conservative `unknown` path and **never** re-parses the painted `pm_buf` (negative assertion: `classify_pm_prefix` is not called with `pm_buf`).
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
  - Role: Delete the translation path AND the anchored-frame path (regexes + 284-297 block) in `granite_classifier.py`; add `last_assistant_text` (transcript JSONL reader with mtime/tool-only handling); switch the three PM classify sites to read the PM transcript (conservative synthetic-`unknown` fallback + warning + counter, NOT a `pm_buf` re-parse); edit the Dev→PM / wrap-up / PM→Dev sites with empty-guarded PTY writes and fallback warnings + counter; delete the two dead `TurnRecord` telemetry fields and add `ContainerResult.transcript_fallback_count`; update docstrings (module, `ClassificationResult`, loop, `ensure_granite_model`).
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: shuttle-test-builder
  - Role: Delete translation tests (incl. `TestTranslationTools`) AND the anchored-frame tests, add `TestLastAssistantText` (with stale-mtime + tool-only fixtures), update `test_container.py` to stub `last_assistant_text` (PM + Dev reads) and assert verbatim forwarding + empty-guard + fallback-warning + ordering-attack regression, fix `test_to_json`, handle `granite_smoke_test.py`.
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

### 1. Delete translation + anchored-frame paths; add transcript reader; edit call sites
- **Task ID**: build-shuttle
- **Depends On**: none
- **Validates**: tests/unit/granite_container/test_granite_classifier.py, tests/unit/granite_container/test_container.py
- **Assigned To**: shuttle-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `extract_dev_prompt`, `summarize_for_pm`, `TRANSLATION_TOOLS`, `SYSTEM_PROMPT`, `GraniteTranslationError`, `_events_from_text`, `_extract_tool_calls`, `_normalize_arguments`, and the translation-only `ollama_chat` usage from `granite_classifier.py`.
- **Delete the anchored-frame path**: `PREFIX_TOKEN_ANCHORED_RE` (line 142), `_FRAME_ARTIFACT_RE` (line 148), and the anchored-match block (lines 284-297) plus their stale comments. Classification now flows strict `PREFIX_TOKEN_RE` → `PREFIX_TOKEN_FALLBACK_RE` → `unknown`.
- Add `last_assistant_text(transcript_path: str, *, mtime_before: float | None = None) -> str` (pure, fail-silent; most recent text-bearing `assistant` entry; `text` blocks only; skips tool-only final entry; partial-trailing-line tolerant; returns `""` when mtime hasn't advanced past `mtime_before`). Place in `transcript_tailer.py` (preferred) or `granite_classifier.py`; document the choice.
- Edit `container.py`:
  - **PM classify input (`:788`, `:844`, `:1156`)**: snapshot transcript mtime before `_cycle_idle`; `pm_text = last_assistant_text(pm_transcript, mtime_before=...)`; if truthy → `classify_pm_prefix(pm_text)`; on empty/`None` → `logger.warning` + `transcript_fallback_count += 1` + synthetic `ClassificationResult(destination="unknown", compliance_miss=True, payload="", raw_first_line="")` (via `_unknown_classification()`). **Do NOT** fall back to `classify_pm_prefix(pm_buf)` — that re-parses the painted buffer's container echo (the misparse the anchored-path deletion removes).
  - **PM→Dev (`:1033`)**: `dev_prompt = classification.payload` (no extra guard at `:1064`; the `:1010` compliance branch already runs first).
  - **Dev→PM (`:1077`)**: `self._pm_pty.write(last_assistant_text(dev_transcript, mtime_before=...) or DEV_REPORT_UNAVAILABLE)` + `logger.warning` + `transcript_fallback_count += 1` on fallback; `self._last_dev_report = dev_text`.
  - **Wrap-up seed (`:1136`)**: `last_assistant_text(dev_transcript) or DEV_REPORT_UNAVAILABLE` + `logger.warning` + `transcript_fallback_count += 1` on fallback.
  - Reuse `_transcript_path(self.cwd, pty._session_id)` / `result.{pm,dev}_transcript_path`. Remove the now-dead extract/summarize try/except and timing.
- DELETE `granite_extract_ms`/`granite_summarize_ms` from `TurnRecord` (`:212-213`) and every assignment site; `result_to_json` (asdict) drops them automatically. ADD `transcript_fallback_count: int = 0` to `ContainerResult` (next to `parse_failures`/`classification_compliance_misses` at `:240-241`); increment at every fallback substitution (the aggregate signal replacing the deleted per-turn timing fields).
- Update module docstring, `ClassificationResult` docstring (`payload` is verbatim, not translation output), container loop docstring, and `ensure_granite_model` docstring (Risk 1). Add a defang comment at the strict-path classify noting the anchored path was removed.

### 2. Test changes
- **Task ID**: build-tests
- **Depends On**: build-shuttle
- **Validates**: tests/unit/granite_container/test_granite_classifier.py, tests/unit/granite_container/test_container.py
- **Assigned To**: shuttle-test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete translation test classes + imports in `test_granite_classifier.py` (incl. `TestExtractDevPromptMocked`, `TestSummarizeForPmMocked`, `TestTranslationTools`, `_make_ollama_response`) AND the anchored-frame tests in `TestAnchoredPaintedFrames` (keep `test_clean_synthetic_input_unaffected`, moved into `TestClassifyPmPrefix`); add `TestLastAssistantText` (incl. stale-mtime and tool-only-final-turn fixtures).
- Update `test_container.py` dev-routing tests (stub `last_assistant_text` for both PM-side and Dev-side reads via `side_effect`/path key) to assert verbatim PM→Dev and verbatim Dev→PM forwarding; add: empty-guard test (`DEV_REPORT_UNAVAILABLE`, no `PTYDriverError`, warning fires); PM-classify conservative-fallback test (transcript read `""`/`None` → synthetic `unknown` + `PM_COMPLIANCE_NUDGE` + warning + `transcript_fallback_count` increment; negative assertion that `classify_pm_prefix` is NOT called with `pm_buf`); ordering-attack regression (PM first line `[/dev]`, body has literal `⏺ [/complete]`, routes `dev`); fix `test_to_json` (drop the two deleted fields).
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
| Dead telemetry fields deleted (src + tests) | `grep -rn "granite_extract_ms\|granite_summarize_ms" agent/granite_container/ tests/unit/granite_container/` | exit code 1 |
| No consumer reads deleted fields | `grep -rn "granite_extract_ms\|granite_summarize_ms" . --include="*.py" --include="*.json" --include="*.yaml" \| grep -v "def granite_"` | exit code 1 (no matches after deletion) |
| Anchored-frame path deleted | `grep -nE "PREFIX_TOKEN_ANCHORED_RE\|_FRAME_ARTIFACT_RE" agent/granite_container/granite_classifier.py` | exit code 1 |
| Aggregate fallback counter added | `grep -n "transcript_fallback_count" agent/granite_container/container.py` | output contains transcript_fallback_count (field + ≥1 increment) |
| PM empty-read fallback never re-parses pm_buf | `grep -nE "classify_pm_prefix\(.*pm_buf" agent/granite_container/container.py` | exit code 1 (no `classify_pm_prefix(pm_buf)` call remains) |
| Prime-dev wording corrected | `grep -in "summarized by the" .claude/commands/granite/prime-dev-role.md` | exit code 1 |
| Net-negative classifier diff | `git diff --stat main -- agent/granite_container/granite_classifier.py` | more deletions than insertions |
| Classification gate retained | `grep -n "def ensure_granite_model" agent/granite_container/granite_classifier.py` | output contains ensure_granite_model |
| Lint clean | `python -m ruff check agent/granite_container/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/granite_container/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-06-14. Verdict: NEEDS REVISION. -->
<!-- Revision applied 2026-06-14 — all 3 BLOCKERs + 4 CONCERNs + 2 NITs addressed; citations re-verified against live code. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Simplifier, Skeptic, Consistency | PM-side classify-input switch to `last_assistant_text(pm_transcript)` is both unnecessary AND un-enumerated. `classify_pm_prefix(pm_buf)` already returns `payload`=verbatim text after `[/dev]` (granite_classifier.py:314-329). The actual classify call sites are container.py:788/844/1156 (outside `_route_pm_classification`), but Solution/Task 1 only lists 1033/1077/1136. Either resolution requires plan text changes. | ✅ RESOLVED (revision) | The `[/dev]` write now uses `classification.payload` (one-line swap at :1033; no LLM re-extraction) — the Simplifier's core point. The PM classify *input* IS moved to the PM transcript, but only because BLOCKER 2 (anchored-path deletion) requires clean echo-free input; this is the critique's own explicitly-offered alternative. Citations corrected throughout: classify sites are **788/844/1156** (Freshness Check, Solution, Data Flow, Task 1 all fixed); extract/summarize sites are 1033/1077/1136. ~~Each classify site gets an empty/`None` fallback to `classify_pm_prefix(pm_buf)`.~~ **SUPERSEDED by rev-2 BLOCKER:** the `pm_buf` fallback reintroduced the painted-frame misparse; rev 2 replaced it with a conservative synthetic `unknown` (see the 2nd-pass table below). |
| BLOCKER | Archaeologist | The anchored-frame path in `classify_pm_prefix` (granite_classifier.py:284-297) is NOT a no-op on clean text — it fires on ANY `⏺`/`●` char and takes the LAST match, before the strict first-line path. If a PM message contains `⏺` (quoting Dev output / code), it misroutes. Plan says "leave them in place (harmlessly no-op)" — false, and violates NO LEGACY CODE TOLERANCE. | ✅ RESOLVED (revision) | Anchored block (284-297) + `PREFIX_TOKEN_ANCHORED_RE` (142) + `_FRAME_ARTIFACT_RE` (148) are now **deleted** in Solution/Task 1 (no longer "leave them in place"). Justified by the PM classify input being clean transcript text (no echo prefix). `TestAnchoredPaintedFrames` tests deleted (keep `test_clean_synthetic_input_unaffected`). Risk 4 rewritten as DISSOLVED. |
| BLOCKER | Adversary, Skeptic | Stale-but-complete read: if Claude Code hasn't flushed the current turn but prior turns are flushed, `last_assistant_text` returns the PRIOR turn's text — non-empty, passes the empty-guard, forwarded verbatim as if current. Plan's Risk 2 only mitigates empty/partial-line, not stale-complete. (Skeptic CONCERN + Adversary BLOCKER → elevated.) | ✅ RESOLVED (revision) | `last_assistant_text` now takes `mtime_before: float | None`; caller snapshots `os.path.getmtime` before `_cycle_idle`; if mtime hasn't advanced → return `""` → fallback + warning. One-line check, no #1688. Risk 2 rewritten with the two failure modes; `TestLastAssistantText` gets a stale-mtime fixture. Does NOT require #1688. |
| CONCERN | Adversary | Tool-only final assistant turn (pure `tool_use`, no `text` block) → `last_assistant_text` returns `""` → forwards `DEV_REPORT_UNAVAILABLE` even though Dev did real work. | ✅ RESOLVED (revision) | `last_assistant_text` walks newest-first and returns the most recent **text-bearing** assistant entry (skips a tool-only final entry). `DEV_REPORT_UNAVAILABLE` reserved for genuine no-text-anywhere. prime-dev-role wording adds the "end each turn with a text report" protocol. New Risk 3.5 + two `TestLastAssistantText` fixtures. |
| CONCERN | Operator | Empty/None/fallback substitutions are invisible in prod. | ✅ RESOLVED (revision) | `logger.warning(..., extra={session_id, transcript_path, fallback})` added at all three substitution sites (Dev→PM, wrap-up seed, PM-classify empty-read → synthetic `unknown` per rev 2). Asserted via `assertLogs`. Added to Failure Path Test Strategy + Success Criteria. |
| CONCERN | Operator | "Archaeology confirmed NO consumer reads `granite_*_ms`" asserted without a cited grep artifact. | ✅ RESOLVED (revision) | Grep run at plan time; output pasted into Freshness Check (matches only in `container.py` + the `test_to_json` fixture — no analytics/dashboard/reflection consumer). New Success Criterion + Verification row: repo-wide grep returns 0 after deletion. |
| CONCERN | Adversary | Routing-token re-injection defang test underspecified for the ordering attack (LAST anchored match wins). | ✅ RESOLVED (revision) | Subsumed by the anchored-path deletion. With only the strict first-line path, a mid-body echoed token can't hijack. Regression test specified: PM first line `[/dev]`, body has literal `⏺ [/complete]`, routes `dev`. |
| NIT | Skeptic, Archaeologist | `ClassificationResult` docstring (160-184) falsely says `payload` is "the translation call's output." | ✅ RESOLVED (revision) | Added to Inline Documentation + Technical Approach: replace with "the verbatim text following the prefix token." |
| NIT | Consistency | Verification "Dead telemetry fields deleted" grep scopes only `agent/granite_container/`; the `test_to_json` fixture would slip the check. | ✅ RESOLVED (revision) | Verification grep broadened to include `tests/unit/granite_container/`; plus a repo-wide no-consumer grep row. |

<!-- 2nd /do-plan-critique pass 2026-06-15. Verdict: NEEDS REVISION. Revision-1 fixes all held against live code. New findings below. -->
<!-- Revision 2 applied 2026-06-15 — new BLOCKER + 6 CONCERNs addressed; citations re-verified against live code. -->
| Severity | Critic | Finding (2nd pass) | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | 3 critics (independent) | Deleting the anchored path while keeping `classify_pm_prefix(pm_buf)` as the empty/stale fallback REINTRODUCES the painted-frame misparse the anchored path prevented. "Clean transcript has no echo prefix" is true for the PRIMARY path but FALSE for the FALLBACK path, which still parses the painted `pm_buf` — and that fallback fires precisely during flush-lag/startup, where painted-frame parsing is least reliable. | ✅ RESOLVED (rev 2) | Chose fix **(b)** — the fallback no longer parses `pm_buf`. On empty/`None` read the PM-classify site returns a synthetic `ClassificationResult(destination="unknown", compliance_miss=True, payload="")` via `_unknown_classification()`, driving the existing compliance-nudge + re-poll branch (`container.py:929/945`). Rationale for (b) over (a): it keeps the anchored-path deletion *total* (no painted-frame parsing survives anywhere — simpler invariant than "anchored path only in the fallback branch"), and the existing `unknown`/re-poll machinery already handles a non-routed turn without dropping it. Updated: Problem outcome #1, Data Flow step 2, Solution PM-classify bullet, Technical Approach, Task 1, Risk 4 (now explicitly closes the fallback path too), plus a negative test (`classify_pm_prefix` never called with `pm_buf`) and a painted-`pm_buf`-would-misroute fixture. |
| CONCERN 1 | (re-critique) | Success-Criteria no-ollama grep FALSE-FAILS a correct implementation — line-44 `from ollama import` + `ensure_granite_model` body survive both `grep -v` filters; contradicts Risk 1's claim. | ✅ RESOLVED (rev 2) | Success Criterion replaced with the **call-invocation** grep `grep -n "ollama_chat(" granite_classifier.py` (returns exit 1 / 0 matches after the two translation calls at 391/426 are deleted). The `if ollama_chat is None:` guard and `from ollama import` line do NOT match `ollama_chat(`. **Proof run at plan time:** `grep -n "ollama_chat(" granite_classifier.py | grep -v "^391:" | grep -v "^426:"` → no output, exit 1 — the only `ollama_chat(` calls are the two being deleted. Risk 1 reworded to cite the call grep. Verification table already used this grep (row consistent). |
| CONCERN 2 | (re-critique) | mtime guard NARROWS but does not CLOSE the stale-but-complete window; plan oversold it as RESOLVED. | ✅ RESOLVED (rev 2) | Risk 2 reframed honestly: "NARROWS the window; does NOT close it." Documented the residual (coarse mtime granularity; same-tick prior-turn flush) and added explicit **residual-risk acceptance** for this content-swap PR, with #1688 as the only deterministic close. No new machinery added — only honest framing. Open Questions corrected ("narrows" not "closes"). |
| CONCERN 3 | (re-critique) | Deleting routing telemetry leaves no per-turn/aggregate fallback signal. | ✅ RESOLVED (rev 2) | Added one aggregate field `ContainerResult.transcript_fallback_count: int = 0` (next to existing `parse_failures`/`classification_compliance_misses`), incremented at every fallback substitution and serialized via `asdict`. Complements the per-occurrence `logger.warning`. New Verification row + Success Criterion + test assertions. Minimal — a counter + a log line, not new race machinery. |
| CONCERN 4 | (re-critique) | Fallback substitutions must be observable in prod (per-occurrence). | ✅ RESOLVED (rev 2) | `logger.warning(..., extra={session_id, transcript_path, fallback})` at every substitution site (PM-classify synthetic-`unknown`, Dev→PM `DEV_REPORT_UNAVAILABLE`, wrap-up seed) PLUS the aggregate counter. Asserted via `assertLogs` + counter assertion in Failure Path Test Strategy and Success Criteria. |
| CONCERN 5 | (re-critique) | Internal consistency — "delete anchored path" + Risk 4 must reflect the fallback fix. | ✅ RESOLVED (rev 2) | Risk 4 extended with "Closed on the fallback path too" subsection; all `pm_buf`-fallback references across Problem/Data Flow/Solution/Technical Approach/Tasks/Tests rewritten to the conservative `unknown` fallback; Data Flow entry point clarified that `pm_buf` is idle-detection only (never a classify source). |
| CONCERN 6 | (re-critique) | Corrected grep must be run and its passing output pasted. | ✅ RESOLVED (rev 2) | Done — see CONCERN 1 proof line (run at plan time against the live file, confirming the only `ollama_chat(` calls are the two translation calls slated for deletion, so the end-state grep is clean). |

---

## Open Questions

The mechanism pivot (read the JSONL transcript instead of scraping the painted frame) is resolved by the owner. The Dev→PM forwarding decision (verbatim, no self-summarization) and the classification-gate retention (Risk 1) are settled. The critique's three BLOCKERs are resolved in this revision: (1) the PM→Dev write uses `classification.payload` (no LLM re-extraction) while the PM classify *input* moves to the PM transcript so that (2) the live anchored-frame path can be deleted (dissolving the routing-token risk), and (3) a one-line mtime snapshot **narrows** the stale-but-complete read (it does not close it — see Risk 2; the residual window is documented and accepted, with the deterministic close deferred to #1688). The tool-only-final-turn concern is handled by walking to the most recent text-bearing entry; fallback substitutions now log warnings; the no-consumer grep artifact is in the Freshness Check.

**Revision 2 (2026-06-15):** the re-critique's new BLOCKER — that the empty-read fallback `classify_pm_prefix(pm_buf)` reintroduced the painted-frame misparse the anchored deletion removed — is resolved by making the fallback a conservative synthetic `unknown` (compliance nudge + re-poll), so the painted buffer is never a routing-classification input anywhere. The six re-critique concerns are folded in: the no-ollama Success-Criteria grep was corrected to the call-invocation form `grep "ollama_chat("` (with the passing proof pasted); Risk 2's mtime guard is honestly reframed as *narrowing*, not closing, the stale-but-complete window (residual accepted, deterministic close = #1688); and a single aggregate `ContainerResult.transcript_fallback_count` plus per-occurrence `logger.warning` restores the fallback observability lost when the per-turn `granite_*_ms` fields were deleted.

**One residual, honestly stated, deferred not blocking:** the flush-timing heuristic (read-at-idle vs. assistant-message-flushed-to-JSONL). This plan mitigates it (complete-lines-only read + empty-guards + a partial-line test) but cannot *eliminate* it on the idle-poll boundary. The deterministic elimination is followup **#1688** (hook-driven Stop signal). If the supervisor judges the heuristic unacceptable to ship even with the mitigations, the correct move is to sequence #1688 first — but the owner's stated sequencing is content-swap now (#1681), deterministic boundaries next (#1688).

**Builder decision left open (intentionally):** the placement of `last_assistant_text` (`transcript_tailer.py` preferred vs. `granite_classifier.py`) — both are acceptable; the builder documents the choice in the PR.
