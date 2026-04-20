---
status: Planning
type: chore
appetite: Medium
owner: valor
created: 2026-04-20
revised: 2026-04-20
revision_reason: "Cycle 2: Address /do-plan-critique re-verdict — B1-R (caller inventory expanded to cover 4 additional test files: test_message_drafter_integration, test_reply_delivery, test_worker_entry, test_message_pipeline) and merged duplicate ## Test Impact H2 sections into one."
tracking: https://github.com/tomcounsell/ai/issues/1074
last_comment_id:
---

# Message Drafter Follow-Up — Close Out #1035 Deferred Scope

## Problem

PR #1072 shipped a deliberately-scoped partial build of the message-drafter refactor (parent issue #1035). It landed the critical worker-bypass dead-letter fix (drafter-at-the-handler, Task 7), the rename (Task 1), the relay length guard (Task 5), and feature docs (Task 13). It was merged with `allow_unchecked: true` on an explicit understanding that the remaining plan tasks would be closed out in a follow-up.

Issue #1074 enumerates those deferred tasks. But during reconnaissance (see the Recon Summary in the issue body), it turns out that **Tasks 3, 8, 11, and most of 12 already landed** on main after #1072 merged or as part of it. Only four items genuinely remain:

1. **Table producers in `/do-pr-review` (Task 2.5 scope-narrowed):** the skill's `code-review.md:121-134` has a "Pre-Verdict Checklist" producer table that instructs reviewers to emit `| # | Item | Verdict | Notes |` markdown. When `validate_telegram` or `validate_email` runs on PR-review output, that table trips a violation. This is the last producer table blocking a clean validator run.
2. **Task 9 as stated is out of date:** the issue wants `mcp_servers/message_delivery_server.py` registered in `.mcp.json`, but `mcp_servers/` is empty and no root `.mcp.json` exists. Meanwhile `tools/send_message.py` and `tools/react_with_emoji.py` already exist as CLI tools and the stop hook already classifies their tool_use blocks. The decision was already made implicitly by #1072's design — the CLI-tool surface is the shipped delivery mechanism. This plan merely **documents** that outcome (see RD-1). No MCP server is introduced.
3. **Validator unit tests (Task 12 residual):** `validate_telegram` / `validate_email` are pure functions at `bridge/message_drafter.py:266-331`. `test_tool_call_delivery.py` covers stop-hook classification but does NOT cover the validators as standalone units. Gap.
4. **Net-negative line count outside tests (Task 15):** Task 15's net-negative constraint is **scoped to this follow-up PR against the post-#1072 baseline** (`26c0ed5e`), not against the pre-#1035 history. The #1072 delta (net positive outside tests due to `bridge/message_drafter.py` absorbing the worker-bypass fix at 1,724 LOC) has already landed on main and is locked in. The correct question for this plan is: **does this plan's diff against `26c0ed5e` show negative LOC outside tests?** Baseline stats below.
5. **Full-diff code review (Task 14):** end-of-build, covers everything that lands here plus the #1072 delta. Not a build task — it runs during REVIEW stage.

**Baseline LOC math (post-#1072 reference = commit `26c0ed5e`):**
- `git diff --stat 26c0ed5e..main -- . ':(exclude)tests/' ':(exclude)docs/plans/'` on the current main → only `docs/features/` changes from plan-doc commits. No code changes on main since #1072.
- `bridge/response.py` at main HEAD: **753 LOC** (starting point for this PR).
- `bridge/message_drafter.py` at main HEAD: **1,724 LOC** (unchanged by this PR except inline absorption of `_truncate_at_sentence_boundary` if moved).
- `agent/output_handler.py` at main HEAD: **348 LOC** (grows by ~100-180 LOC as `send_response_with_files` behavior is absorbed).
- **Target outside tests (this PR only):** ≥ 150 LOC net negative. Concrete: ~300 LOC deleted from `bridge/response.py` − ~150 LOC added to `agent/output_handler.py` = −150 LOC minimum.
- **Acknowledgment:** the original parent plan's "net-negative" aspiration was scoped to the full refactor trajectory. Because #1072 landed net-positive (intentionally, to put the worker-bypass fix in one place), the cumulative trajectory from pre-#1035 to post-this-PR may remain net-positive. That is an accepted consequence of the scope split, not a failure of this plan. **The commitment made by this PR is: this PR's own diff is net-negative outside tests.**

**Current behavior:**
- Reviewer agents run `/do-pr-review` and emit markdown tables. When those outputs route through a validated medium (Telegram with `validate_telegram`, email with `validate_email`) — e.g., when a PM session echoes the verdict back to the user — they trip a `no_markdown_tables` violation and the agent sees a `⚠️` nag.
- `mcp_servers/` exists but is empty; there is no live MCP server for message delivery. The agent delivers by invoking `python tools/send_message.py '...'` as a Bash tool call. This is the shipped behavior since #1072 merged on 2026-04-20; it is not a gap to fill, but an un-documented design choice.
- Two high-value pure functions (`validate_telegram`, `validate_email`) have no direct unit tests.
- `bridge/response.py` still carries `send_response_with_files` (~322 lines) and supporting helpers that overlap heavily with what the `TelegramRelayOutputHandler` now does directly from the worker. Removing this duplication is the forcing function for the net-negative delta against the post-#1072 baseline.

**Desired outcome:**
- Every producer table in `.claude/skills/do-pr-review/` migrated to prose (reference tables describing env vars or sub-skill structure stay).
- The Task 9 question formally closed: the CLI-tool surface (`tools/send_message.py` + `tools/react_with_emoji.py`, already in production since #1072) is documented as the final design in `docs/features/message-drafter.md` with a short rationale. See **RD-1** below. No new MCP server is built in this PR.
- `tests/unit/test_medium_validators.py` covers `validate_telegram`, `validate_email`, `_validate_for_medium`, and `format_violations` — happy paths, edge cases, and one coverage case per rule.
- `git diff --stat 26c0ed5e..HEAD -- . ':(exclude)tests/' ':(exclude)docs/plans/' | tail -1` shows a **negative** net line count for this PR's diff specifically. This is the hardest constraint and is forced by decomposing `bridge/response.py` (its surviving behavior either moves into the output handlers, moves into `bridge/message_drafter.py`, or gets deleted). The baseline is the post-#1072 commit, not pre-#1035 — we are not re-litigating the #1072 size delta.
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
- `bridge/response.py` — still 753 lines; still imported by production callers `bridge/update.py`, `bridge/routing.py:1031`, `bridge/telegram_relay.py:95`, `bridge/telegram_bridge.py:120`. Full test-side caller inventory (re-verified via `grep -rn "from bridge.response\|import bridge.response" tests/`):
  - `tests/unit/test_message_drafter.py` — 5 `send_response_with_files` sites (lines 1924, 1947, 1985, 2012, 2038); 7 `_truncate_at_sentence_boundary` sites (lines 2123, 2129, 2138, 2146, 2152, 2159, 2166).
  - `tests/unit/test_emoji_embedding.py` — 3 `VALIDATED_REACTIONS` sites (lines 19, 27, 34).
  - `tests/unit/test_worker_entry.py` — `VALIDATED_REACTIONS` (line 236), `REACTION_*` backward-compat imports (line 248).
  - `tests/integration/test_message_drafter_integration.py` — 1 `send_response_with_files` site (line 43).
  - `tests/integration/test_reply_delivery.py` — multiple sites: `REACTION_COMPLETE/ERROR/SUCCESS/RECEIVED` + `VALIDATED_REACTIONS` (lines 131, 137, 143, 149), `INVALID_REACTIONS` (lines 155, 185), multi-symbol import (line 165), 5 `filter_tool_logs` sites (lines 196, 203, 210, 217, 223).
  - `tests/e2e/test_message_pipeline.py` — top-level import of `clean_message`, `extract_files_from_response`, `filter_tool_logs` (line 13).
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
- **Confidence:** high — the CLI tool surface is already the shipped behavior in production as of #1072 merge on 2026-04-20. The stop hook's `classify_delivery_outcome` already recognizes these tool_use patterns. There is nothing to decide; there is only something to document.
- **Impact on plan:** Task 9 becomes a documentation-only task: add a "Delivery Tool Surface" section to `docs/features/message-drafter.md` explaining the CLI-tool choice, noting the reversibility path. Captured as **RD-1 (Resolved Decision)** below, not as an Open Question — the decision is *already* in production.

### spike-3: Are there callers of `send_response_with_files` (and other `bridge/response.py` symbols) outside `bridge/`?

- **Assumption:** "Deleting `send_response_with_files` only requires changes inside `bridge/`."
- **Method:** code-read (`grep -rn "from bridge.response\|import bridge.response" tests/` + same pattern for production dirs)
- **Finding:** Confirmed, with expanded test-side scope (this expansion addresses B1-R from the re-critique).
  - **Production callers of `send_response_with_files`:** `bridge/telegram_bridge.py:120` (top-level import), `bridge/routing.py:1031` (deferred import inside a function).
  - **Production callers of other `bridge/response.py` symbols (`set_reaction`, `VALIDATED_REACTIONS`, `REACTION_*`, `filter_tool_logs`):** `bridge/update.py`, `bridge/telegram_relay.py:95`, `bridge/routing.py`.
  - **Test callers — complete list:**
    1. `tests/unit/test_message_drafter.py` — 5 `send_response_with_files` call sites (lines 1924, 1947, 1985, 2012, 2038) + 7 `_truncate_at_sentence_boundary` sites (lines 2123-2166). These are the canonical drafter tests; migrate each to either `TelegramRelayOutputHandler.send` or `bridge.message_drafter.draft_message`, depending on what behavior each test actually asserts.
    2. `tests/unit/test_emoji_embedding.py` — 3 `VALIDATED_REACTIONS` sites (lines 19, 27, 34). Pure constant import; redirect to wherever `VALIDATED_REACTIONS` ends up (remains in `bridge/response.py` if the module survives, or moves to `bridge/reactions.py`).
    3. `tests/unit/test_worker_entry.py` — `VALIDATED_REACTIONS` (line 236) + `REACTION_*` backward-compat symbols (line 248). Same redirect treatment as (2). The REACTION_* aliases already exist as backward-compat shims; this test exists precisely to guard that compat surface, so keep the symbols live wherever they move.
    4. `tests/integration/test_message_drafter_integration.py` — 1 `send_response_with_files` site (line 43). Integration path — migrate to `TelegramRelayOutputHandler.send` and verify the integration behavior still holds end-to-end.
    5. `tests/integration/test_reply_delivery.py` — heaviest consumer: `REACTION_COMPLETE/ERROR/SUCCESS/RECEIVED` + `VALIDATED_REACTIONS` (lines 131-149), `INVALID_REACTIONS` (lines 155, 185), consolidated multi-symbol import (line 165), 5 `filter_tool_logs` sites (lines 196-223). If `filter_tool_logs` is deleted as part of the `send_response_with_files` cleanup (spike-1 listed it for removal), these 5 tests need to either (a) be deleted if they were only guarding `send_response_with_files`-specific log filtering, or (b) assert the equivalent behavior on whatever path replaces it. Case-by-case.
    6. `tests/e2e/test_message_pipeline.py` — top-level import of `clean_message`, `extract_files_from_response`, `filter_tool_logs` (line 13). All three are in spike-1's deletion bucket. E2E path — tests probably need to be rewritten to run against the consolidated `TelegramRelayOutputHandler.send` path, or deleted if the specific helpers are no longer exposed.
- **Confidence:** high
- **Impact on plan:** Consolidation touches production files `bridge/telegram_bridge.py`, `bridge/routing.py` (callers), `bridge/update.py` and `bridge/telegram_relay.py` (if `set_reaction`/`VALIDATED_REACTIONS` move modules), plus six test files (all listed above). All test changes fall into the `tests/` exclusion for Task 15's line count so they don't threaten net-negative. However, the test-migration surface is larger than spike-3 originally claimed — builder should budget additional time for `test_reply_delivery.py` and `test_message_pipeline.py` since those two files have non-trivial symbol surface to migrate or delete.

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

### spike-5: Complete producer inventory — which agent outputs reach `validate_telegram` / `validate_email`?

- **Assumption:** "Only the `/do-pr-review` Pre-Verdict Checklist is at risk of tripping the validators. All other markdown tables in `.claude/` are reference documentation read by the agent, not produced as output."
- **Method:** code-read of every callsite of `draft_message()` + `_validate_for_medium()`, cross-referenced with every markdown-table producer in the skill/agent surfaces.
- **Finding:** Confirmed with one caveat — **conversational echo** is the subtle risk.

  **Validator reach (who runs text through the validators):** The only codepaths that invoke `_validate_for_medium(text, medium)` on agent output are:
  1. `agent/output_handler.py:258` — `TelegramRelayOutputHandler.send` → `draft_message(medium="telegram")` → validators run on the PM/dev session's final response text.
  2. `bridge/email_bridge.py:310` — `EmailOutputHandler.send` → `draft_message(medium="email")` → validators run on email reply text.
  3. `agent/hooks/stop.py:150` — stop-hook review-gate presentation → `draft_message(medium=<resolved>)` → validators run on the transcript tail before the agent decides to send/react/silent.
  4. `bridge/response.py:471` — `send_response_with_files` legacy path → `draft_message(text, session=session)` (Telegram only, default medium) → validators run. Removed by this plan's Part C.

  **Producer inventory (where markdown tables can originate in text that reaches the above codepaths):**

  | Source | What it does | Medium(s) it reaches | Currently emits table? | Disposition |
  |--------|-------------|---------------------|----------------------|-------------|
  | `.claude/skills/do-pr-review/sub-skills/code-review.md:121-134` | Pre-Verdict Checklist; agent emits this verbatim. When posted via `gh pr comment`, bypasses the drafter. When a PM session **echoes the verdict back to the user via Telegram** (common pattern: "Review posted — here's the verdict"), the table text passes through `TelegramRelayOutputHandler.send`. | Telegram (via conversational echo); GitHub PR body bypasses drafter | YES | **MIGRATE** (Task 2.5) |
  | `.claude/skills/do-pr-review/SKILL.md:31` | Reference table of `$SDLC_*` env vars. Agent reads it; does not emit it. | None — read-only | N/A | Leave alone |
  | `.claude/skills/do-pr-review/sub-skills/README.md:9, 20` | Reference tables (sub-skill structure, env var sourcing). Read-only. | None — read-only | N/A | Leave alone |
  | Other `.claude/skills/*` tables (46 files per `grep`) | All are reference tables in skill documentation, consumed by the agent as instructions. Not emitted as output. | None — read-only | N/A | Leave alone |
  | `.claude/agents/*.md` (validator, notion, mcp-specialist, frontend-tester, documentation-specialist, dev-session, builder, baseline-verifier) | Agent-persona system prompts with reference tables (tool lists, capability matrices). Read-only. | None — read-only | N/A | Leave alone |
  | `reflections/*` | No markdown tables in the Python package (grep returned zero hits). Reflection outputs are structured JSON, not markdown. | N/A | N/A | Leave alone |
  | `mcp_servers/*` | Empty at baseline (only `__pycache__`). No producers. | N/A | N/A | Leave alone |
  | `bridge/*`, `agent/*`, `worker/*`, `tools/*` Python code | Code does not emit literal markdown tables in response text. The PM/dev sessions construct response text at runtime; the only table-producing instruction in their prompt surface is `code-review.md:121-134` (already counted). | N/A | N/A | Leave alone |
  | PM/dev session free-form responses | The agent can in principle emit a markdown table in any response (e.g., summarizing tabular data in chat). The validator catches these at wire time; the drafter either rewrites or surfaces `⚠️` to the agent. **This is by design** — the validator exists precisely because free-form output may violate, and the agent learns from the violation nag. | Telegram, Email | Occasionally | **NOT MIGRATE** — validator handles these at runtime, not a static producer |

- **Confidence:** high — validator call graph is complete; producer list exhausted by `grep` across all skill/agent/reflection surfaces.
- **Impact on plan:** Task 2.5 scope confirmed — ONE file, ONE table (`code-review.md:121-134`). The universe of *static* markdown-table producers that reach the validators is exhausted by this single migration. Dynamic producers (agent free-form output) are handled correctly by the validator's runtime behavior. No additional migration subtasks needed.

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
- PM check-ins: 0 required before build (Task 9 is resolved as RD-1; Open Questions #1 and #2 in the review section concern margin target and module rename — non-blocking nice-to-haves).
- Review rounds: 1 (full-diff `/do-pr-review` covers both #1072's delta and this plan's delta)

**Justification for Medium (not Large):**
- Only four items genuinely remain; three of them are small (table migration, validator unit tests, documentation note). The fourth (`bridge/response.py` consolidation + net-negative enforcement) is moderately-sized but mechanical: move ~322 lines into a handler that already does 90% of the job and delete the rest.
- Net-line-count target (against post-#1072 baseline `26c0ed5e`): this PR's diff must be at least **−150 LOC** outside tests (conservatively, ≥ −200 to have margin). The deletion comes from `bridge/response.py`'s 753 LOC; the addition comes from ~150 LOC of absorbed behavior landing in `agent/output_handler.py`. Achievable within a single PR.

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
- **`docs/features/message-drafter.md`** gets a "Delivery Tool Surface" section documenting the CLI-tool decision per RD-1.
- **No new MCP server**, no `.mcp.json` changes. RD-1 records the rationale; the CLI-tool surface is already shipped.

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
- **Part D — Task 9 decision documentation:** Add a `### Delivery Tool Surface` subsection to `docs/features/message-drafter.md` explaining the CLI-tool choice (reversibility path, reasoning, current invocation pattern). This is executed against RD-1 (Resolved Decision): the CLI surface is already shipped and working since #1072. See Risk 4 for the contingency path if the user reverses RD-1 during review.
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

Complete test-side caller inventory for `bridge/response.py` (re-verified via `grep -rn "from bridge.response\|import bridge.response" tests/`). Six existing test files reference `bridge/response.py` symbols; all are listed below.

- [ ] `tests/unit/test_message_drafter.py` — UPDATE: 5 `send_response_with_files` sites (lines 1924, 1947, 1985, 2012, 2038) migrate to the consolidated delivery path. Each test either exercises draft construction (call `draft_message` directly) or delivery (call `TelegramRelayOutputHandler.send` with a mock session). Case-by-case judgment by builder. Additionally, 7 `_truncate_at_sentence_boundary` sites (lines 2123, 2129, 2138, 2146, 2152, 2159, 2166) — UPDATE import path if the helper moves to `bridge/message_drafter.py`, or DELETE tests that only exercised that helper if it is absorbed inline.
- [ ] `tests/unit/test_emoji_embedding.py` — UPDATE: 3 `VALIDATED_REACTIONS` import sites (lines 19, 27, 34) redirect to whichever module survives (`bridge/response.py` thin-module OR `bridge/reactions.py` if rename is taken).
- [ ] `tests/unit/test_worker_entry.py` — UPDATE: `VALIDATED_REACTIONS` (line 236) and `REACTION_*` backward-compat imports (line 248) redirect to the surviving module. This test exists specifically to guard the backward-compat shim surface — the shim symbols must remain importable wherever they move.
- [ ] `tests/integration/test_message_drafter_integration.py` — UPDATE: 1 `send_response_with_files` site (line 43) migrates to `TelegramRelayOutputHandler.send`. Verify integration behavior (end-to-end delivery + drafter validation) still holds.
- [ ] `tests/integration/test_reply_delivery.py` — UPDATE or REPLACE (case-by-case): imports `REACTION_COMPLETE/ERROR/SUCCESS/RECEIVED` + `VALIDATED_REACTIONS` (lines 131-149), `INVALID_REACTIONS` (155, 185), multi-symbol bundle (165), and 5 `filter_tool_logs` sites (196, 203, 210, 217, 223). Reaction-constant imports redirect like `test_emoji_embedding.py`. `filter_tool_logs` is in spike-1's deletion bucket — the 5 tests guarding its behavior need to either (a) DELETE if they were only asserting `send_response_with_files`-specific log filtering, or (b) REPLACE with equivalent assertions against `TelegramRelayOutputHandler.send`.
- [ ] `tests/e2e/test_message_pipeline.py` — REPLACE or DELETE (builder decides per case): top-level import of `clean_message`, `extract_files_from_response`, `filter_tool_logs` (line 13). All three symbols are in spike-1's deletion bucket. E2E tests should be rewritten to drive `TelegramRelayOutputHandler.send` directly, or deleted if the specific helper behavior is no longer surfaced.
- [ ] `tests/unit/test_medium_validators.py` — CREATE (new file): unit tests for the validators, ≈20 tests total. Covers the Task 12 residual gap.
- [ ] `tests/integration/test_worker_pm_long_output.py` — NO CHANGE expected; run to validate consolidation did not regress the worker-bypass fix.
- [ ] `tests/unit/test_output_handler.py` — UPDATE: add one new test asserting the consolidated handler handles the handler-event entry path (reply_to, file_paths). Suggested name: `test_handler_event_entry_parity`.
- [ ] `tests/unit/test_tool_call_delivery.py` — NO CHANGE expected; stop-hook classification already covered.

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
**Mitigation:** Track the line count as a first-class build signal against the post-#1072 baseline (`26c0ed5e`). After Part C step 4 (deletion), run the diff stat against that baseline excluding tests/ and docs/plans/. If margin is < 150 lines negative, pause and re-audit for dead code before adding new tests (which land in tests/ and don't count against the budget). Escalate to user if margin cannot be reached without breaking the stop-hook contract.

### Risk 3: `tests/unit/test_message_drafter.py` migration introduces test failures
**Impact:** Build goes red; unclear whether the failure is test migration bug or real regression.
**Mitigation:** Migrate tests one at a time, running the single test after each change. Keep the old import available via a temporary alias during migration if helpful, then remove the alias at the end.

### Risk 4: User overrides RD-1 and demands a real MCP server
**Impact:** If RD-1 is reversed during review (user insists on `mcp_servers/message_delivery_server.py` + `.mcp.json`), the plan blows up. Task 9 becomes +300 to +500 lines (server, config registration, tests), which makes Task 15 nearly impossible without an extra round of consolidation that is not currently in scope.
**Mitigation:** RD-1 is presented as a Resolved Decision (not an Open Question), reflecting that the CLI tool surface is already the shipped behavior since #1072. If the user reverses RD-1 during plan review or critique, this plan is paused, the appetite is revised to Large, and a Part F is added for the MCP server implementation. Part C (consolidation) would proceed in parallel since it is orthogonal to the delivery tool surface.

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

See **RD-1 (Resolved Decisions)** below for the recorded rationale. This is not a gating question — it reflects the current production behavior shipped in PR #1072.

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

## Success Criteria

- [ ] `.claude/skills/do-pr-review/sub-skills/code-review.md:121-134` Pre-Verdict Checklist migrated to bulleted prose. `grep -cE "^\|.*\|.*\|" .claude/skills/do-pr-review/sub-skills/code-review.md` returns 0.
- [ ] `tests/unit/test_medium_validators.py` exists and passes. Coverage includes `validate_telegram`, `validate_email`, `_validate_for_medium`, `format_violations`.
- [ ] `bridge/response.py::send_response_with_files` is deleted from main. `grep -rn "send_response_with_files" --include="*.py"` returns zero production matches (tests migrated).
- [ ] `git diff --stat $(git merge-base main HEAD)..HEAD -- . ':(exclude)tests/' ':(exclude)docs/plans/' | tail -1` shows this PR's diff is negative against the post-#1072 baseline (`26c0ed5e`). Concrete target: **≥ 150 lines negative outside tests and plan docs** to have margin.
- [ ] `docs/features/message-drafter.md` has a `### Delivery Tool Surface` subsection documenting RD-1.
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

### 1. Migrate producer table in code-review.md (Part A)

- **Task ID**: build-table-migration
- **Depends On**: none
- **Validates**: `grep -cE "^\|.*\|.*\|" .claude/skills/do-pr-review/sub-skills/code-review.md` returns 0
- **Informed By**: spike-4 (only one producer table exists); spike-5 (complete producer inventory confirms no other migrations needed)
- **Assigned To**: consolidation-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills/do-pr-review/sub-skills/code-review.md:121-134`. Replace the markdown table with a bulleted list of the same 12 items, format `- **N. Item name** — PASS/FAIL/N/A — *notes*`.
- Keep surrounding prose and heading.
- Do NOT touch any other markdown tables in the skill.

### 2. Write medium validator unit tests (Part B)

- **Task ID**: build-validator-tests
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_medium_validators.py -q` exits 0 with ≥ 18 tests run
- **Informed By**: direct code-read of `bridge/message_drafter.py:260-342`
- **Assigned To**: validator-test-writer
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_medium_validators.py`.
- Test classes: `TestValidateTelegram`, `TestValidateEmail`, `TestValidateForMedium`, `TestFormatViolations`.
- Happy paths, each rule, empty inputs, unknown-medium dispatch.

### 3. Consolidate bridge/response.py into TelegramRelayOutputHandler (Part C)

- **Task ID**: build-consolidation
- **Depends On**: build-table-migration, build-validator-tests
- **Validates**: `pytest tests/ -x -q` passes; `grep -rn "send_response_with_files" --include="*.py" .` returns zero production hits; `git diff --stat $(git merge-base main HEAD)..HEAD -- . ':(exclude)tests/' ':(exclude)docs/plans/' | tail -1` shows ≥ 150 LOC net negative against post-#1072 baseline.
- **Informed By**: spike-1 (≥250 lines of movable surface), spike-3 (caller list)
- **Assigned To**: consolidation-builder
- **Agent Type**: builder
- **Parallel**: false
- Step-by-step per Technical Approach Part C (items 1-7).
- Migrate `tests/unit/test_message_drafter.py` import sites as part of this task.
- Run the line-count check mid-task; adjust if margin insufficient.

### 4. Update feature documentation (Part D)

- **Task ID**: document-delivery-surface
- **Depends On**: build-consolidation
- **Validates**: `grep -c "### Delivery Tool Surface" docs/features/message-drafter.md` returns ≥ 1
- **Informed By**: spike-2 (CLI-tool decision); RD-1 (resolved decision)
- **Assigned To**: consolidation-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add `### Delivery Tool Surface` subsection to `docs/features/message-drafter.md`.
- Update any file-path references affected by the consolidation.

### 5. Final validation

- **Task ID**: validate-all
- **Depends On**: build-table-migration, build-validator-tests, build-consolidation, document-delivery-surface
- **Informed By**: RD-1, spike-5
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
| Net-negative line count outside tests and plan docs | `git diff --stat $(git merge-base main HEAD)..HEAD -- . ':(exclude)tests/' ':(exclude)docs/plans/' \| tail -1 \| grep -oE '[0-9]+ insertions.*[0-9]+ deletions' \| python -c "import sys; ins, dels = [int(x.split()[0]) for x in sys.stdin.read().split(',')]; sys.exit(0 if dels - ins >= 150 else 1)"` | exit code 0 |
| Medium validator tests | `pytest tests/unit/test_medium_validators.py -q` | exit code 0 |
| Worker-bypass integration test | `pytest tests/integration/test_worker_pm_long_output.py -q` | exit code 0 |
| Feature doc has Delivery Tool Surface section | `grep -c "### Delivery Tool Surface" docs/features/message-drafter.md` | output ≥ 1 |

## Critique Results

**Verdict**: NEEDS REVISION — 5 blockers must be resolved before build.
**Run**: 2026-04-20 | Critics: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor
**Findings**: 11 total (5 blockers, 5 concerns, 1 nit)

### Blockers (must resolve before build)

**B1 — Missing behavior migration map for `send_response_with_files` consolidation** *(Skeptic, Adversary, User, Archaeologist)*
`TelegramRelayOutputHandler.send()` currently lacks at least 7 behaviors from `send_response_with_files`: (1) PM bypass, (2) needs_self_draft steering, (3) routing fields persistence, (4) file type detection (image/video/audio/document), (5) dead-letter queue, (6) `_truncate_at_sentence_boundary`, (7) `filter_tool_logs` + `extract_files_from_response`. Technical Approach Part C says "absorb the unique behavior" but doesn't specify which behaviors are ported vs. deleted.
→ **Fix**: Add a Decomposition Table in Part C listing each of the 7 behaviors with its destination (port to handler, port to drafter, port to caller, or delete with justification).

**B2 — PM bypass logic not ported — duplicate delivery risk** *(Adversary, User)*
`send_response_with_files` lines 445-463 guard against duplicate delivery when a PM session self-messages (`session.has_pm_messages()` + parent session check). `TelegramRelayOutputHandler.send()` has no such guard. If not ported, PM orchestration sessions will double-deliver.
→ **Fix**: Trace the PM bypass path explicitly: confirm whether stop-hook fires before the handler writes to outbox (making porting unnecessary), or add `if session and getattr(session, 'has_pm_messages', lambda: False)(): return` to the handler.

**B3 — Dead-letter queue regression** *(Operator)*
`send_response_with_files` enqueues failed sends to `bridge/dead_letters.py`. `TelegramRelayOutputHandler.send()` only logs Redis errors to stderr — messages are silently lost on Redis failure. Operational regression.
→ **Fix**: Add dead-letter enqueue to the handler's exception handler using `bridge.dead_letters.persist_failed_delivery(chat_id, reply_to, delivery_text)` (signature at `bridge/dead_letters.py:84`).

**B4 — Feature flag rollback breaks after `send_response_with_files` deletion** *(Consistency Auditor)*
Success Criteria: "Feature flag `MESSAGE_DRAFTER_IN_HANDLER` still toggleable; rollback path verified by `test_worker_pm_long_output.py`." But Part C deletes `send_response_with_files`, which is the flag=0 path. Toggling the flag off after this PR would invoke code that no longer exists.
→ **Fix**: Either (a) keep `send_response_with_files` as a thin stub delegating to the handler, or (b) update the Success Criterion to state the flag is deprecated and update the disabled-drafter test assertion. Choose one.

**B5 — Pre-Verdict Checklist new prose format unspecified** *(User)*
Task 1 step-by-step says "replace the markdown table with a bulleted list" but doesn't include a concrete example of the migrated 12-item checklist. Builder ambiguity, especially regarding bold/italic usage and whether the format itself passes `validate_telegram`.
→ **Fix**: Add a 3-item example of the migrated format in Task 1. Confirm `validate_telegram(new_checklist_text)` returns `[]`.

### Concerns (address before review)

- **C1**: Verification table uses `$(git merge-base main HEAD)` but plan body specifies baseline `26c0ed5e` — hardcode the literal commit.
- **C2**: Race Conditions section is one line for a hot-path consolidation — document deployment atomicity and session object lifetime guarantees.
- **C3**: `validate_telegram(None)` contract unresolved — choose: raise `TypeError` or document caller must pre-check. *Note: current impl's `if not text: return []` may already handle None; verify before writing the test.*
- **C4**: Handler's INFO-level log on Redis success is present (output_handler.py:295-299) — verify log level is not suppressed in prod.
- **C5**: Rollback runbook missing: add `git revert <merge-commit> && ./scripts/valor-service.sh restart` to Risk 1 mitigation.

### Nits
- **N1**: Verification table net-negative check shell command is overly complex — simplify to `git diff --stat 26c0ed5e..HEAD -- . ':(exclude)tests/' ':(exclude)docs/plans/' | tail -1`.

---

## Resolved Decisions

**RD-1 — Delivery tool surface is the existing CLI-tool pattern (settles original Task 9).**
Source: PR #1072 shipped `tools/send_message.py`, `tools/react_with_emoji.py`, and the stop-hook classification (`agent/hooks/stop.py:217`'s `classify_delivery_outcome` already pattern-matches tool_use blocks for these scripts). This is the *de facto* current design and has been in production since #1072 merged on 2026-04-20. No new MCP server is planned. Task 9's original ask ("create `mcp_servers/message_delivery_server.py`, register in `.mcp.json`") is **withdrawn** — the surface already exists and functions. This plan's Part D merely documents the decision in `docs/features/message-drafter.md` so future maintainers don't re-open the question. The MCP-server alternative remains reversible — a future chore can wrap the CLI tools if transcript readability becomes a pain point — but that is out of scope for this PR. No build task is gated on reaffirming this.

## Open Questions

1. **Task 15 margin target.** Plan sets "≥150 lines negative against post-#1072 baseline, excluding tests/ and docs/plans/" as the Success Criterion for net-line-count. Do you want a larger explicit margin (say, −250 lines) to ensure headroom, or is ≥150 sufficient?
2. **`bridge/response.py` disposition.** After consolidation, should the surviving ≈100 lines (`set_reaction`, `VALIDATED_REACTIONS`, possibly `_truncate_at_sentence_boundary`) stay in `bridge/response.py` under a clearer-named module docstring, or be renamed to `bridge/reactions.py`? Either works; rename adds 3 call-site edits (`bridge/update.py`, `bridge/telegram_relay.py`, `bridge/routing.py`) to the diff but improves clarity.
