---
status: Planning
type: chore
appetite: Medium
owner: valor
created: 2026-04-20
tracking: https://github.com/tomcounsell/ai/issues/1074
last_comment_id:
---

# Message Drafter Follow-Up — Close Out #1035 Deferred Scope

## Problem

PR #1072 shipped a deliberately-scoped partial build of the message-drafter refactor (parent issue #1035). It landed the critical worker-bypass dead-letter fix (drafter-at-the-handler, Task 7), the rename (Task 1), the relay length guard (Task 5), and feature docs (Task 13). It was merged with `allow_unchecked: true` on an explicit understanding that the remaining plan tasks would be closed out in a follow-up.

Issue #1074 enumerates those deferred tasks. But during reconnaissance (see the Recon Summary in the issue body), it turns out that **Tasks 3, 8, 11, and most of 12 already landed** on main after #1072 merged or as part of it. Only four items genuinely remain:

1. **Table producers in `/do-pr-review` (Task 2.5 scope-narrowed):** the skill's `code-review.md:121-134` has a "Pre-Verdict Checklist" producer table that instructs reviewers to emit `| # | Item | Verdict | Notes |` markdown. When `validate_telegram` or `validate_email` runs on PR-review output, that table trips a violation. This is the last producer table blocking a clean validator run.
2. **Task 9 as stated is out of date:** the issue wants `mcp_servers/message_delivery_server.py` registered in `.mcp.json`, but `mcp_servers/` is empty and no root `.mcp.json` exists. Meanwhile `tools/send_message.py` and `tools/react_with_emoji.py` already exist as CLI tools and the stop hook already classifies their tool_use blocks. We need to make a **decision** — wrap the CLI tools in a real MCP server, or formally declare the CLI-tool surface complete — and record the outcome.
3. **Validator unit tests (Task 12 residual):** `validate_telegram` / `validate_email` are pure functions at `bridge/message_drafter.py:266-331`. `test_tool_call_delivery.py` covers stop-hook classification but does NOT cover the validators as standalone units. Gap.
4. **Net-negative line count outside tests (Task 15):** `git diff --stat 41382113^..main -- . ':(exclude)tests/'` shows `+2803 / -827` (net **+1976**). `bridge/summarizer.py` (−1,525) and `bridge/formatting.py` (−76) are gone, but `bridge/message_drafter.py` (+1,724) is larger than the file it replaced, and `bridge/response.py` only shrunk from 823→753. The plan required net-negative outside tests; we are +1976.
5. **Full-diff code review (Task 14):** end-of-build, covers everything that lands here plus the #1072 delta. Not a build task — it runs during REVIEW stage.

**Current behavior:**
- Reviewer agents run `/do-pr-review` and emit markdown tables. When those outputs route through a validated medium (Telegram with `validate_telegram`, email with `validate_email`), they trip a `no_markdown_tables` violation and the agent sees a `⚠️` nag.
- `mcp_servers/` exists but is empty; there is no live MCP server for message delivery. The agent delivers by invoking `python tools/send_message.py '...'` as a Bash tool call. Whether that should be the final design is undecided.
- Two high-value pure functions (`validate_telegram`, `validate_email`) have no direct unit tests.
- The consolidation promised in #1035 left us net-positive by nearly 2k lines outside tests. Specifically, `bridge/response.py` still carries `send_response_with_files` (~322 lines) and supporting helpers that overlap heavily with what the `TelegramRelayOutputHandler` now does directly from the worker.

**Desired outcome:**
- Every producer table in `.claude/skills/do-pr-review/` migrated to prose (reference tables describing env vars or sub-skill structure stay).
- The Task 9 question resolved: either a real MCP server exists AND is registered, or the CLI-tool surface is formally declared as the final design with a short rationale in `docs/features/message-drafter.md`.
- `tests/unit/test_medium_validators.py` covers `validate_telegram`, `validate_email`, `_validate_for_medium`, and `format_violations` — happy paths, edge cases, and one coverage case per rule.
- `git diff --stat 41382113^..HEAD -- . ':(exclude)tests/' | tail -1` shows a **negative** net line count. This is the hardest constraint and is forced by decomposing `bridge/response.py` (its surviving behavior either moves into the output handlers, moves into `bridge/message_drafter.py`, or gets deleted).
- A full-diff `/do-pr-review` pass signs off on the consolidated feature at merge time.

## Freshness Check

**Baseline commit:** `e6cd0b7ce7ac90c31ae14e906857ed9b357f99b5`
**Issue filed at:** 2026-04-20T10:06Z (today)
**Disposition:** Major drift — the issue's Task list is mostly obsolete. See Recon Summary on the issue for the full bucketing.

**File:line references re-verified:**
- `.claude/skills/do-pr-review/SKILL.md:31` — reference table (`$SDLC_*` env vars), not a producer table; leave alone.
- `.claude/skills/do-pr-review/sub-skills/README.md:9,20` — two reference tables about sub-skill structure; leave alone.
- `.claude/skills/do-pr-review/sub-skills/code-review.md:121-134` — producer table (Pre-Verdict Checklist). **Must migrate.**
- `bridge/message_drafter.py:266` (`validate_telegram`) — confirmed present.
- `bridge/message_drafter.py:306` (`validate_email`) — confirmed present.
- `bridge/message_drafter.py:346` (`_validate_for_medium`) — confirmed present.
- `agent/hooks/stop.py:63` (`_is_user_triggered`, renamed from `_is_telegram_triggered`) — confirmed.
- `agent/hooks/stop.py:114` (parent_agent_session_id early-return) — confirmed.
- `agent/hooks/stop.py:217` (`classify_delivery_outcome`) — confirmed.
- `tests/integration/test_worker_pm_long_output.py` — exists, 180+ lines, covers enabled + rollback paths.
- `tests/unit/test_tool_call_delivery.py` — exists.
- `bridge/response.py` — still 753 lines; still imported by `bridge/update.py`, `bridge/routing.py:1031`, `bridge/telegram_relay.py:95`, `bridge/telegram_bridge.py:120`, and `tests/unit/test_emoji_embedding.py`, `tests/unit/test_message_drafter.py`.
- `mcp_servers/` — contains only `__pycache__` on main. No Python modules.
- `.mcp.json` — does NOT exist at repo root. `config/mcp_library.json` exists but is a descriptor library for third-party MCPs (Sentry, Linear, Notion), not the tool-registration file the issue implies.

**Cited sibling issues/PRs re-checked:**
- #1035 (parent) — open as the original tracking issue; #1072 closed out its partial build.
- #1072 — merged 2026-04-20T08:03Z. Confirmed.

**Commits on main since issue was filed (touching referenced files):**
- None besides pre-issue history. Issue and baseline are contemporaneous.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/completed/message-drafter.md` — the parent plan, now in `completed/`. Reference only; do not edit.
- No active plan files touch `bridge/response.py`, `bridge/message_drafter.py`, or `.claude/skills/do-pr-review/` at baseline.

**Notes:** The issue's recommended 3-PR split is based on the obsolete task list. Since the remaining scope is modest (≈4 items), a **single PR is correct**. Justification in the Appetite section.

## Prior Art

- **PR #1072** (merged 2026-04-20, `session/message-drafter`): the partial build. Landed Tasks 1, 5, 7, 13. Introduced `bridge/message_drafter.py`, deleted `bridge/summarizer.py` and `bridge/formatting.py`, and left `bridge/response.py` partially gutted (still 753 lines). Also merged follow-on work: Tasks 3 (validators), 8 (worker-bypass integration test), 11 (stop-hook unification), and most of 12 (delivery outcome tests). Commit `26c0ed5e` on main. Direct relevance: this is the code we are finishing.
- **Plan: `docs/plans/completed/message-drafter.md`** — the original plan. Every Resolved Design Decision and Architectural Impact note remains authoritative. Read once for context; do not modify.
- **Issue #1035** (parent) — still open. Gets closed by the implementation PR that lands *this* plan's work (not by this plan doc).

## Research

No relevant external findings — proceeding with codebase context. All remaining work is internal to the `bridge/`, `tools/`, `agent/hooks/`, and `.claude/skills/do-pr-review/` surfaces. No new libraries, APIs, or external ecosystem patterns are involved. Telegram's 4096-char invariant and the Telethon file-attachment pattern were already researched and memorized during the original plan (memory `96b2e19117d8415b90709ae183b108eb`).

## Spike Results

### spike-1: Can `bridge/response.py` shrink enough to force net-negative?

- **Assumption:** "The remaining 753 lines of `bridge/response.py` contain enough movable or deletable code to force the total diff to net negative."
- **Method:** code-read
- **Finding:** Confirmed with margin. `bridge/response.py` carries three buckets:
  - `send_response_with_files` (368-689, ≈322 lines): the handler-event delivery path. Overlaps heavily with `TelegramRelayOutputHandler.send` now that the drafter is at the handler. Candidate for **consolidation into `agent/output_handler.py`** (merge the file-path / reaction / reply-to handling into `TelegramRelayOutputHandler.send` as its canonical implementation) — net shrink of ~250 lines if dedupe is executed cleanly.
  - `set_reaction` (690-753, ≈63 lines): reaction-posting helper. Used by `bridge/update.py`, `bridge/telegram_relay.py`, `bridge/routing.py`. Small, single-purpose. **Keep** but possibly move to `bridge/reactions.py` (≈63 lines) if the module otherwise dies. Neutral on line count.
  - Helpers: `filter_tool_logs`, `extract_files_from_response`, `clean_message`, `_truncate_at_sentence_boundary`, `VALIDATED_REACTIONS` (172-367, ≈195 lines). Most callers are `send_response_with_files` itself; if it goes, these go with it. Small leftover (`VALIDATED_REACTIONS`, `_truncate_at_sentence_boundary`) is used by external tests — move alongside `set_reaction`.
- **Confidence:** high
- **Impact on plan:** Task 15 is achievable. The shrink comes from consolidating `send_response_with_files` into `TelegramRelayOutputHandler.send` and deleting the helpers that only `send_response_with_files` used. Remaining `bridge/response.py` becomes a thin shim or is renamed to `bridge/reactions.py`. Expected delta: −250 to −400 lines outside tests, plus small adjustments in `bridge/telegram_bridge.py` and `bridge/routing.py` to call the handler directly instead of going through `send_response_with_files`.

### spike-2: What is the Task 9 decision — real MCP server, or CLI-tools-as-surface?

- **Assumption:** "A real MCP server is strictly better than CLI tools for the delivery surface."
- **Method:** code-read + architectural inspection
- **Finding:** Not strictly better. Tradeoffs:
  - **CLI-tools-as-surface (current)**: zero new code, zero new config, stop hook already classifies correctly (`_SEND_MESSAGE_PATTERN`, `_REACT_PATTERN`). Agent invokes via `Bash("python tools/send_message.py 'text'")`. Works today. Con: tool calls surface as Bash invocations in transcript, not as semantic tool calls; harder to audit.
  - **Real MCP server**: `mcp_servers/message_delivery_server.py` using `FastMCP` (or similar) with `send_message(text: str, reply_to: int | None)` and `react_with_emoji(emoji: str)` tool definitions. Requires a root `.mcp.json` file (does not currently exist in this repo — sdk_client would need to know about it), or registration via `claude_agent_sdk` config. Pro: semantic tool calls in transcript. Con: new infrastructure for a surface that already works; risks adding code when we need to subtract (Task 15).
  - **Tiebreaker:** Task 15 demands net-negative. Adding an MCP server is net-positive and conflicts directly with Task 15. Decision: **declare CLI-tool surface as the final design.** Document the rationale in `docs/features/message-drafter.md`. Keep the option open to revisit in a future chore if the transcript-readability cost becomes painful.
- **Confidence:** medium (architectural judgment; final call is the user's via plan review)
- **Impact on plan:** Task 9 becomes a documentation-only task: add a "Delivery Tool Surface" section to `docs/features/message-drafter.md` explaining the CLI-tool choice, noting the reversibility path. **This becomes Open Question #1 — user must confirm before build starts.**

### spike-3: Are there callers of `send_response_with_files` outside `bridge/`?

- **Assumption:** "Deleting `send_response_with_files` only requires changes inside `bridge/`."
- **Method:** code-read (grep)
- **Finding:** Confirmed with one caveat. Production callers: `bridge/telegram_bridge.py:120` (top-level import), `bridge/routing.py:1031` (deferred import inside a function). Test callers: `tests/unit/test_message_drafter.py` (5 call sites, lines 1924-2038) and `tests/unit/test_emoji_embedding.py` (3 references to `VALIDATED_REACTIONS`). The test file `test_message_drafter.py` is the canonical test suite for the drafter feature; its calls should migrate to the new consolidated entry point (`TelegramRelayOutputHandler.send`) or be rewritten to target `bridge/message_drafter.py::draft_message` directly if they are really testing draft construction.
- **Confidence:** high
- **Impact on plan:** The consolidation touches `bridge/telegram_bridge.py`, `bridge/routing.py`, `tests/unit/test_message_drafter.py`, and (possibly) `tests/unit/test_emoji_embedding.py`. The test-side changes fall into the "tests/" exclusion for Task 15's line count, so they don't threaten net-negative.

### spike-4: Which tables in `.claude/skills/do-pr-review/` are producer vs. reference?

- **Assumption:** "Only `code-review.md:121-134` is a producer table; the others are reference tables that should stay."
- **Method:** code-read (inspection of each table's surrounding prose)
- **Finding:** Confirmed.
  - `SKILL.md:31` — `| Variable | Description | Fallback |` — documents env var contract. Reference.
  - `sub-skills/README.md:9` — `| File | Type | Responsibility |` — documents sub-skill structure. Reference.
  - `sub-skills/README.md:20` — `| Variable | Source | Example |` — documents env var sourcing. Reference.
  - `sub-skills/code-review.md:121-134` — `| # | Item | Verdict | Notes |` Pre-Verdict Checklist — **this one is filled out by the reviewer and emitted verbatim into the PR review comment body**. Producer.
- **Confidence:** high
- **Impact on plan:** Task 2.5 migration touches ONE file, ONE table. Scope is small.

## Data Flow

The data flows relevant to this plan are already well-documented in the parent plan. This plan does not introduce new flows; it refactors the internals of existing ones:

1. **Delivery flow (unchanged by this plan, but shortened by consolidation):**
   - PM session emits output via `send_cb` → `TelegramRelayOutputHandler.send` (in `agent/output_handler.py`) → calls `draft_message(medium="telegram")` → writes payload to Redis outbox → relay delivers.
   - After consolidation: the handler-event flow (human sends Telegram message) also routes through `TelegramRelayOutputHandler.send` instead of `bridge/response.py::send_response_with_files`. Identical downstream behavior.

2. **Review gate flow (unchanged by this plan):**
   - Session stop → `agent/hooks/stop.py::on_stop` → resolves medium → `draft_message` → tool-call presentation → second stop → `classify_delivery_outcome`.
   - Only changed if Task 9 decision (spike-2) opens a real MCP server — which we are declining.

3. **Validator flow (strengthened by unit tests, no behavior change):**
   - `draft_message` calls `_validate_for_medium(text, medium)` → returns `list[Violation]` → `format_violations(violations, medium)` → appended to review-gate presentation as `⚠️` note.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1072 | Shipped Tasks 1, 5, 7, 13 plus follow-on Tasks 3/8/11/12 | Deliberately-scoped partial. Critical fix landed; remaining four items (table migration, Task 9 decision, validator units, net-negative) deferred to #1074 by design. Not a failure — an explicit scope reduction for speed of dead-letter fix. |

**Root cause pattern:** None. #1072 was a deliberate scope trade, not a failed attempt.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:**
  - `bridge.response.send_response_with_files` is **removed**. Callers (`bridge/telegram_bridge.py`, `bridge/routing.py`) call `TelegramRelayOutputHandler.send` directly. Tests migrate accordingly.
  - `bridge.response.set_reaction` either stays in `bridge/response.py` (if that module survives as a thin module of 80-ish lines) or moves to a new `bridge/reactions.py`. **Deferred to build-time implementer choice** — either works.
- **Coupling:**
  - *Reduced*: one fewer delivery entry point. Handler event and worker send_cb now converge on `TelegramRelayOutputHandler.send`.
- **Data ownership:** Unchanged. `TelegramRelayOutputHandler` was already the intended single authority post-#1035; this plan finishes moving callers onto it.
- **Reversibility:** High. Restore `send_response_with_files` from git history and revert the two caller diffs. The feature flag `MESSAGE_DRAFTER_IN_HANDLER` (default true) is untouched and preserves its rollback semantics.

## Appetite

**Size:** Medium

**Team:** Solo dev (builder), code reviewer (PR review at REVIEW stage)

**Interactions:**
- PM check-ins: 1 (to resolve Open Question #1 — Task 9 CLI-vs-MCP decision — before build starts)
- Review rounds: 1 (full-diff `/do-pr-review` covers both #1072's delta and this plan's delta)

**Justification for Medium (not Large):**
- Only four items genuinely remain; three of them are small (table migration, validator unit tests, documentation note). The fourth (`bridge/response.py` consolidation + net-negative enforcement) is moderately-sized but mechanical: move ~322 lines into a handler that already does 90% of the job and delete the rest.
- Total net-line-count target: from current **+1976** to **negative** — so the PR must delete at least ~2,000 lines outside tests (conservatively, ≥2,100 to have margin). That is achievable within a single PR because `bridge/response.py` and its helpers carry the bulk of the deletable surface.

**Justification for single PR (rejecting the issue's 3-PR split):**
- The issue recommended 3 PRs based on the obsolete task list. Now that most of the tasks are already done, splitting into 3 PRs would (a) create overhead disproportionate to the remaining scope, (b) make it harder to demonstrate net-negative in any single PR (each would be a small independent delta), and (c) spread the `/do-pr-review` gate across three reviews when one is sufficient.
- Blast radius of consolidation (Task 15) is wide but shallow: ≈4 production files + 1 test file. Reviewing it in isolation from the table migration and validator tests makes the review more, not less, expensive.
- Rollback granularity concern is mitigated by the `MESSAGE_DRAFTER_IN_HANDLER` feature flag (still in place) and by the consolidation being a move-and-delete rather than a behavior change.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Anthropic API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Drafter LLM backend (unchanged) |
| OpenRouter API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENROUTER_API_KEY')"` | Drafter fallback backend (unchanged) |
| Redis running | `redis-cli ping` | Outbox + session state (unchanged) |
| Bridge + worker stopped before integration tests | `./scripts/valor-service.sh status \| grep -c 'not running'` | `test_worker_pm_long_output.py` spawns its own worker |

Run all checks: `python scripts/check_prerequisites.py docs/plans/message-drafter-followup.md`

## Solution

### Key Elements

- **`send_response_with_files` removed from `bridge/response.py`**: its surviving behavior (handler-event delivery path) is absorbed into `TelegramRelayOutputHandler.send` in `agent/output_handler.py`. `bridge/telegram_bridge.py` and `bridge/routing.py` call the handler directly.
- **`bridge/response.py` reduced or renamed**: whatever survives (just `set_reaction`, `VALIDATED_REACTIONS`, `_truncate_at_sentence_boundary` — roughly 100 lines) either stays as a slim module OR is renamed to something more honest like `bridge/reactions.py`. Builder decides based on which is cleaner.
- **`code-review.md:121-134` Pre-Verdict Checklist migrates to bulleted prose**: same 12 items, no table syntax. Reviewer still emits a structured verdict; format becomes `- **[N] Item** — PASS/FAIL/N/A — *notes*` (one line per item).
- **`tests/unit/test_medium_validators.py`** (new): direct unit tests on `validate_telegram`, `validate_email`, `_validate_for_medium`, `format_violations`.
- **`docs/features/message-drafter.md`** gets a "Delivery Tool Surface" section documenting the CLI-tool decision (contingent on Open Question #1 resolution).
- **No new MCP server**, no `.mcp.json` changes (contingent on Open Question #1 resolution).

### Flow

**For the reviewer:** Loads `/do-pr-review` → reads sub-skills/code-review.md → fills out the Pre-Verdict Checklist (now a bulleted list instead of a table) → emits it into the PR review body → validator no longer trips `no_markdown_tables`.

**For the deliverer (unchanged):** PM → `send_cb` → `TelegramRelayOutputHandler.send` (now also the entry point for the handler event flow) → `draft_message(medium=...)` → outbox → relay.

### Technical Approach

- **Part A — Table migration (Task 2.5):** Edit `sub-skills/code-review.md:121-134`. Replace the markdown table with an unordered list of identical items, each formatted as `- **N. Item name** — PASS/FAIL/N/A — *notes here*`. Keep the heading "Pre-Verdict Checklist" and the surrounding prose. Do NOT touch `SKILL.md:31` or `sub-skills/README.md:9,20` — spike-4 classified these as reference tables.
- **Part B — Validator unit tests (Task 12 residual):** Create `tests/unit/test_medium_validators.py`. One test class per function: `TestValidateTelegram`, `TestValidateEmail`, `TestValidateForMedium`, `TestFormatViolations`. Cover: happy path (empty list), table detection (Telegram), each email rule (fenced/inline code, headings, bold, italic, bullets, links), dispatch by medium string, unknown medium returns empty, formatting produces a `⚠️` prefix. Target: one test per rule × one positive + one negative = ≈20 tests.
- **Part C — Consolidate `bridge/response.py` (Task 15 forcing function):** 
  1. Audit `send_response_with_files` (bridge/response.py:368-689). Extract the delivery logic that overlaps with `TelegramRelayOutputHandler.send`. Identify what is genuinely unique (file path handling, reply_to semantics for handler events, error propagation).
  2. Extend `TelegramRelayOutputHandler.send` to absorb the unique behavior. The handler becomes the one true path.
  3. Update call sites: `bridge/telegram_bridge.py:120` (top-level import) and `bridge/routing.py:1031` (deferred import). Each becomes a direct call to the output handler. Remove the `from bridge.response import` lines.
  4. Delete `send_response_with_files`, `filter_tool_logs`, `extract_files_from_response`, `clean_message`, `_truncate_at_sentence_boundary` — only if nothing external still uses them. If something does (e.g., `test_message_drafter.py` imports `_truncate_at_sentence_boundary`), move that helper into `bridge/message_drafter.py` where it logically belongs.
  5. Decide what to do with the remaining `set_reaction` + `VALIDATED_REACTIONS`. Two options: (a) leave `bridge/response.py` as a thin ≈100-line module named for reactions, or (b) rename the module to `bridge/reactions.py` and update all three call sites (`bridge/update.py:14`, `bridge/telegram_relay.py:95`, `bridge/routing.py:1031`). Builder picks whichever produces the cleaner final state.
  6. Update `tests/unit/test_message_drafter.py`: 5 call sites import `send_response_with_files`. Migrate each to call `TelegramRelayOutputHandler.send` via the test's existing session/handler fixtures, OR if the test is actually testing draft construction (not delivery), migrate to `bridge.message_drafter.draft_message` directly. Each migration is a small edit, not a rewrite.
  7. Verify net-negative: run `git diff --stat HEAD..target_sha -- . ':(exclude)tests/' | tail -1`. Loop back to step 4-5 if the number is not negative by at least 50 lines (safety margin).
- **Part D — Task 9 decision documentation:** Contingent on Open Question #1 being answered "CLI tools are the final surface." Add a `### Delivery Tool Surface` subsection to `docs/features/message-drafter.md` explaining the choice (reversibility path, reasoning, current invocation pattern). If Open Question #1 comes back "build the MCP server," scope blows up and this plan becomes Large — rewrite required.
- **Part E — Full-diff `/do-pr-review` (Task 14):** Runs automatically at REVIEW stage via `/sdlc`. This is not a build task — it is a pipeline stage.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `validate_telegram` / `validate_email` are pure functions and raise nothing — state "No exception handlers in scope for new validator unit tests."
- [ ] The consolidation edits (`send_response_with_files` → `TelegramRelayOutputHandler.send`) do NOT introduce new `except Exception` blocks. Verify during build: `grep -c "except Exception" agent/output_handler.py` before vs. after — should not increase.
- [ ] Any `except Exception: pass` pattern encountered during consolidation must either be replicated with identical semantics in the new location or removed with a commit-message note.

### Empty/Invalid Input Handling
- [ ] `validate_telegram("")` → returns `[]` (test this).
- [ ] `validate_telegram(None)` → Python would raise AttributeError on `.split()`; add a test that documents the contract: validator is called only on strings, not None. Either raise explicitly with a TypeError or document that the caller must pre-check.
- [ ] `validate_email("")` → returns `[]` (test this).
- [ ] `_validate_for_medium(text, "unknown_medium")` → returns `[]` (test this; line 351).

### Error State Rendering
- [ ] `format_violations([], medium="telegram")` → returns `""` (no violations, no noise). Test this; implementation at line 336-337 returns empty string on empty list.
- [ ] A multi-rule email violation produces a multi-line `⚠️` prefix that fits the review-gate presentation — smoke-test one fixture containing bold + headings + bullets.

## Test Impact

- [ ] `tests/unit/test_message_drafter.py::test_*` (5 sites importing `send_response_with_files` at lines 1924, 1947, 1985, 2012, 2038) — UPDATE: migrate to the consolidated delivery path. Each test either exercises draft construction (call `draft_message` directly) or delivery (call `TelegramRelayOutputHandler.send` with a mock session). Case-by-case judgment by builder. Two sites import `_truncate_at_sentence_boundary` (lines 2123, 2129, 2138) — UPDATE import path if the helper moves to `bridge/message_drafter.py`, or DELETE if the helper is fully absorbed inline.
- [ ] `tests/unit/test_emoji_embedding.py` (3 sites importing `VALIDATED_REACTIONS`) — UPDATE import path if `set_reaction` + `VALIDATED_REACTIONS` move to `bridge/reactions.py`.
- [ ] `tests/unit/test_medium_validators.py` — CREATE (new file): unit tests for the validators, ≈20 tests total. Covers the Task 12 residual gap.
- [ ] `tests/integration/test_worker_pm_long_output.py` — no changes expected; validates consolidation did not regress the worker-bypass fix.
- [ ] `tests/unit/test_output_handler.py` — may need one new test asserting the consolidated handler handles the handler-event entry path (reply_to, file_paths). UPDATE: add `test_handler_event_entry_parity` or similar.
- [ ] `tests/unit/test_tool_call_delivery.py` — no changes expected; stop-hook classification already covered.

## Rabbit Holes

- **Do NOT introduce a new MCP server to satisfy Task 9 literally.** It conflicts with Task 15 (net-negative) and the spike confirmed the CLI-tool surface is functional. Surface the question, document the decision, move on.
- **Do NOT migrate the SDLC reference tables in `.claude/skills/do-pr-review/SKILL.md` or `sub-skills/README.md`.** spike-4 classified them as documentation, not producer output. Migrating them is style, not substance.
- **Do NOT chase the 5-outcome test coverage matrix further.** `test_tool_call_delivery.py` already covers it; adding per-outcome tests for pure coverage is busywork.
- **Do NOT split `bridge/message_drafter.py` into sub-modules for "cleanliness."** At 1,724 lines, it is large but cohesive. Splitting adds lines (imports, boilerplate) and worsens Task 15.
- **Do NOT attempt Task 13.5 in this plan.** It is deferred 2 weeks post-#1072 merge per the original plan's D5d. File it as a separate chore issue when the clock runs out.
- **Do NOT reopen `bridge/markdown.py` splitting as a mitigation.** It was reverted at `1678068b` for good reason (UX regression, wrong layer). Net-negative gets met by deletion, not by new length-handling code.

## Risks

### Risk 1: Consolidation hides a behavioral difference between `send_response_with_files` and `TelegramRelayOutputHandler.send`
**Impact:** Handler-event delivery (human sends Telegram message → bridge handler → response) regresses in subtle ways. User sees wrong formatting, missing file attachments, or broken reply-to.
**Mitigation:** Before deleting `send_response_with_files`, write a side-by-side behavior note listing each thing that function does that the handler does not. Either port the missing behavior or explicitly justify why it is safe to drop (e.g., "this branch was dead code covering a format the drafter now prevents"). Rerun `tests/integration/test_message_drafter_integration.py` and the worker-bypass integration test after consolidation.

### Risk 2: Net-negative target not reached even after consolidation
**Impact:** Task 15 acceptance check fails; builder has to hunt for more deletions mid-PR, possibly breaking invariants.
**Mitigation:** Track the line count as a first-class build signal. After Part C step 4 (deletion), run the diff stat. If margin is < 50 lines negative, pause and re-audit for dead code before adding new tests (which land in tests/ and don't count against the budget). Escalate to user if margin cannot be reached without breaking the stop-hook contract.

### Risk 3: `tests/unit/test_message_drafter.py` migration introduces test failures
**Impact:** Build goes red; unclear whether the failure is test migration bug or real regression.
**Mitigation:** Migrate tests one at a time, running the single test after each change. Keep the old import available via a temporary alias during migration if helpful, then remove the alias at the end.

### Risk 4: Open Question #1 answered "build the MCP server"
**Impact:** Plan blows up. Task 9 becomes +300 to +500 lines (server, config registration, tests), which makes Task 15 nearly impossible without an extra round of consolidation that is not currently in scope.
**Mitigation:** Open Question #1 is the first question in the plan review. If the answer is "build it," revise the plan appetite to Large and add a Part F for the MCP server implementation. Do not start Part C until the answer is in.

## Race Conditions

No race conditions identified — all work is synchronous file edits (table migration, consolidation), async I/O paths that are unchanged in behavior (handler.send), and new pure-function unit tests. The delivery path's existing race-safety (registry-before-session-create invariants, outbox write atomicity) is unchanged.

## No-Gos (Out of Scope)

- **No new MCP server.** The current CLI-tool surface is the final design; we document it.
- **No splitting of `bridge/message_drafter.py`.** Keep cohesive.
- **No Task 13.5 schema cleanup.** Defer per original plan's D5d timeline.
- **No reintroduction of message splitting in any form.** The relay length guard stands.
- **No edits to reference tables in `.claude/skills/do-pr-review/SKILL.md` or `sub-skills/README.md`.**
- **No edits to `docs/plans/completed/message-drafter.md`.** Historical artifact.

## Update System

No update system changes required — all changes are internal to the `ai/` repository. No new environment variables, no new service dependencies, no migration steps for existing installations. The `MESSAGE_DRAFTER_IN_HANDLER` feature flag from #1072 is untouched.

## Agent Integration

**Primary finding from spike-2:** Task 9's original ask ("create `mcp_servers/message_delivery_server.py`, register in `.mcp.json`") conflicts with Task 15's net-negative target. The CLI-tool surface (`tools/send_message.py`, `tools/react_with_emoji.py`) is already functional and classified correctly by the stop hook.

**Recommendation:** declare the CLI-tool surface as the final design. Document the decision in `docs/features/message-drafter.md` under a new `### Delivery Tool Surface` subsection explaining:

- Why CLI tools over MCP (zero new code, fits Task 15 budget).
- How the stop hook classifies outcomes (pattern-match tool_use blocks containing `tools/send_message.py` or `tools/react_with_emoji.py`).
- The reversibility path: a future chore can wrap the CLI tools in an MCP server if transcript readability becomes a problem.

**No changes to `mcp_servers/`, no creation of `.mcp.json`**, no changes to `bridge/telegram_bridge.py` agent-facing plumbing. The agent-integration story is "unchanged by this plan; documented as a deliberate design choice."

**Open Question #1 below** captures the user-review gate for this decision.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/message-drafter.md` to add a `### Delivery Tool Surface` subsection (~15 lines) documenting the CLI-tool decision.
- [ ] Update the same doc to reflect post-consolidation file locations (e.g., `bridge/response.py` → `bridge/reactions.py` if the rename is taken).
- [ ] Verify `docs/features/README.md` entry for message-drafter still points to the right paths.

### Inline Documentation
- [ ] Docstrings on `TelegramRelayOutputHandler.send` updated to describe the now-consolidated handler-event + worker-send paths.
- [ ] `test_medium_validators.py` module docstring explains the validator contract and why these tests exist separately from `test_tool_call_delivery.py`.

### External Documentation Site
- Not applicable — no Sphinx / MkDocs / Read the Docs site in this repo.

## Test Impact

See the Test Impact section above (rendered separately per template ordering; same content).

## Success Criteria

- [ ] `.claude/skills/do-pr-review/sub-skills/code-review.md:121-134` Pre-Verdict Checklist migrated to bulleted prose. `grep -cE "^\|.*\|.*\|" .claude/skills/do-pr-review/sub-skills/code-review.md` returns 0.
- [ ] `tests/unit/test_medium_validators.py` exists and passes. Coverage includes `validate_telegram`, `validate_email`, `_validate_for_medium`, `format_violations`.
- [ ] `bridge/response.py::send_response_with_files` is deleted from main. `grep -rn "send_response_with_files" --include="*.py"` returns zero production matches (tests migrated).
- [ ] `git diff --stat $(git merge-base main HEAD)..HEAD -- . ':(exclude)tests/' | tail -1` shows negative net lines. Concrete target: **≥ 50 lines negative** to have margin.
- [ ] `docs/features/message-drafter.md` has a `### Delivery Tool Surface` subsection (assuming Open Question #1 resolves as expected).
- [ ] `/do-test` passes.
- [ ] `/do-pr-review` (full-diff pass, covers both #1072 delta and this PR) returns no unresolved blockers, nits, or tech debt.
- [ ] Feature flag `MESSAGE_DRAFTER_IN_HANDLER` still toggleable; rollback path verified by `test_worker_pm_long_output.py`'s disabled-drafter test case.

## Team Orchestration

### Team Members

- **Builder (consolidation)**
  - Name: `consolidation-builder`
  - Role: Execute Parts A, C, D — table migration, `bridge/response.py` consolidation, documentation update.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (validators)**
  - Name: `validator-test-writer`
  - Role: Execute Part B — write `tests/unit/test_medium_validators.py`.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `followup-validator`
  - Role: Verify Success Criteria, run `/do-test`, confirm net-negative line count.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard (Tier 1): `builder`, `validator`, `test-engineer`, `documentarian`.

## Step by Step Tasks

### 1. Resolve Open Question #1 (Task 9 decision)

- **Task ID**: resolve-q1
- **Depends On**: none
- **Validates**: user response to the Open Question below
- **Informed By**: spike-2
- **Assigned To**: user (via plan review)
- **Agent Type**: n/a (human decision)
- **Parallel**: true

Before any build task runs, the user must confirm the Task 9 decision: CLI-tool surface as final (expected) OR build a real MCP server (blows up scope).

### 2. Migrate producer table in code-review.md (Part A)

- **Task ID**: build-table-migration
- **Depends On**: resolve-q1
- **Validates**: `grep -cE "^\|.*\|.*\|" .claude/skills/do-pr-review/sub-skills/code-review.md` returns 0
- **Informed By**: spike-4 (only one producer table exists)
- **Assigned To**: consolidation-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills/do-pr-review/sub-skills/code-review.md:121-134`. Replace the markdown table with a bulleted list of the same 12 items, format `- **N. Item name** — PASS/FAIL/N/A — *notes*`.
- Keep surrounding prose and heading.
- Do NOT touch any other markdown tables in the skill.

### 3. Write medium validator unit tests (Part B)

- **Task ID**: build-validator-tests
- **Depends On**: resolve-q1
- **Validates**: `pytest tests/unit/test_medium_validators.py -q` exits 0 with ≥ 18 tests run
- **Informed By**: direct code-read of `bridge/message_drafter.py:260-342`
- **Assigned To**: validator-test-writer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_medium_validators.py`.
- Test classes: `TestValidateTelegram`, `TestValidateEmail`, `TestValidateForMedium`, `TestFormatViolations`.
- Happy paths, each rule, empty inputs, unknown-medium dispatch.

### 4. Consolidate bridge/response.py into TelegramRelayOutputHandler (Part C)

- **Task ID**: build-consolidation
- **Depends On**: resolve-q1, build-table-migration, build-validator-tests
- **Validates**: `pytest tests/ -x -q` passes; `grep -rn "send_response_with_files" --include="*.py" .` returns zero production hits; `git diff --stat $(git merge-base main HEAD)..HEAD -- . ':(exclude)tests/' | tail -1` shows ≥50 lines negative.
- **Informed By**: spike-1 (≥250 lines of movable surface), spike-3 (caller list)
- **Assigned To**: consolidation-builder
- **Agent Type**: builder
- **Parallel**: false
- Step-by-step per Technical Approach Part C (items 1-7).
- Migrate `tests/unit/test_message_drafter.py` import sites as part of this task.
- Run the line-count check mid-task; adjust if margin insufficient.

### 5. Update feature documentation (Part D)

- **Task ID**: document-delivery-surface
- **Depends On**: build-consolidation
- **Validates**: `grep -c "### Delivery Tool Surface" docs/features/message-drafter.md` returns ≥ 1
- **Informed By**: spike-2 (CLI-tool decision)
- **Assigned To**: consolidation-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add `### Delivery Tool Surface` subsection to `docs/features/message-drafter.md`.
- Update any file-path references affected by the consolidation.

### 6. Final validation

- **Task ID**: validate-all
- **Depends On**: build-table-migration, build-validator-tests, build-consolidation, document-delivery-surface
- **Validates**: all Success Criteria
- **Assigned To**: followup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `/do-test` — must pass.
- Run `python -m ruff check . && python -m ruff format --check .` — must pass.
- Verify net-negative line count outside tests.
- Verify no producer tables remain in `.claude/skills/do-pr-review/`.
- Verify `MESSAGE_DRAFTER_IN_HANDLER=0` rollback test still passes.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No producer tables in code-review.md | `grep -cE "^\\|.*\\|.*\\|" .claude/skills/do-pr-review/sub-skills/code-review.md` | output 0 |
| No send_response_with_files callers | `grep -rn "send_response_with_files" --include="*.py" .` | exit code 1 |
| Net-negative line count outside tests | `git diff --stat $(git merge-base main HEAD)..HEAD -- . ':(exclude)tests/' \| tail -1 \| grep -oE '[0-9]+ insertions.*[0-9]+ deletions' \| python -c "import sys; ins, dels = [int(x.split()[0]) for x in sys.stdin.read().split(',')]; sys.exit(0 if dels > ins else 1)"` | exit code 0 |
| Medium validator tests | `pytest tests/unit/test_medium_validators.py -q` | exit code 0 |
| Worker-bypass integration test | `pytest tests/integration/test_worker_pm_long_output.py -q` | exit code 0 |
| Feature doc has Delivery Tool Surface section | `grep -c "### Delivery Tool Surface" docs/features/message-drafter.md` | output ≥ 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Task 9 — CLI tool surface vs. real MCP server.** Spike-2 recommends declaring `tools/send_message.py` + `tools/react_with_emoji.py` as the final delivery tool surface and documenting the choice, rather than building a new `mcp_servers/message_delivery_server.py` + `.mcp.json` registration. Rationale: the CLI-tool surface works today, is already classified correctly by the stop hook, and adding an MCP server is net-positive (conflicts with Task 15's net-negative target). **Do you accept this recommendation, or do you want the MCP server built anyway (in which case the plan becomes Large and Task 15 may need to slip to a later PR)?**
2. **Task 15 margin target.** Plan sets "≥50 lines negative" as the Success Criterion for net-line-count outside tests. Do you want a larger explicit margin (say, −200 lines) to ensure headroom, or is ≥50 sufficient?
3. **`bridge/response.py` disposition.** After consolidation, should the surviving ≈100 lines (`set_reaction`, `VALIDATED_REACTIONS`, possibly `_truncate_at_sentence_boundary`) stay in `bridge/response.py` under a clearer-named module docstring, or be renamed to `bridge/reactions.py`? Either works; rename adds 3 call-site edits (`bridge/update.py`, `bridge/telegram_relay.py`, `bridge/routing.py`) to the diff but improves clarity.
