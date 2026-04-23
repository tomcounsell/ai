---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1129
last_comment_id:
---

# Session Model Routing — Wire `AgentSession.model` Through the Harness CLI

## Problem

**Current behavior:**
`AgentSession.model` is a first-class field (`models/agent_session.py:217`) and `valor_session create --model` persists it (`tools/valor_session.py:1040-1046`). PR #909 ("feat: SDLC stage model selection and hard-PATCH builder session resume", merged 2026-04-13) also wired `model` through `ValorAgent → ClaudeAgentOptions` so it reaches `_create_options()` (`agent/sdk_client.py:1020-1137`).

However, the **live execution path for every session type no longer goes through `ValorAgent`**. The worker calls `agent/session_executor.py:1109-1189`, which imports `get_response_via_harness` directly and runs a `claude -p` subprocess using the default `_HARNESS_COMMANDS["claude-cli"]` list (`agent/sdk_client.py:1549-1561`). That list contains no `--model` flag, and the call site at `session_executor.py:1177-1189` never reads `session.model`. The only consumer of `_session_model` is the dormant `get_response_for_request` path at `sdk_client.py:2584-2598`, which `session_executor.py` bypasses entirely.

**Net effect:** `valor-session create --model opus` stores `opus` on the AgentSession record, but the subprocess invocation is still `claude -p --verbose --output-format stream-json --include-partial-messages --permission-mode bypassPermissions <message>` — no `--model` flag — so the Claude CLI uses its own internal default. Per-session model selection is inert.

PM persona already documents a Stage→Model Dispatch Table (`config/personas/project-manager.md:134-148`) instructing dispatches to pass `--model opus|sonnet`, and PM briefings set the flag correctly. All of that work is wasted because the worker drops the value on the floor.

**Desired outcome:**
When an `AgentSession` has `model` set, the `claude -p` subprocess spawned for that session receives `--model <value>` on its argv. When the field is `None`, the model resolves via a documented precedence cascade (session.model > settings > codebase default), ending at `"opus"` as the codebase default. The field is respected for all three session types (PM, Teammate, Dev).

Stage-level PM overrides (Sonnet for BUILD/TEST/PATCH/DOCS, Opus for PLAN/CRITIQUE/REVIEW) **start taking effect** once this wiring lands — that's the desired behavior. The Opus codebase default applies only when nothing explicit is set anywhere in the cascade.

Additionally — per Tom's 2026-04-23 scope expansion on Q6 — the **PM-to-CEO final-delivery drafter** (`session_completion.py:450`) is hardened: always Opus, 2-pass (draft → self-review/refine), no-silent-fail contract (raises loudly on empty output). Ollama fallback remains deferred to #1137; until that lands, Anthropic-down means loud failure (satisfies no-silent-fail).

## Freshness Check

**Baseline commit:** `ceedbe68b76337baa317a719ef217e13f3b82852`
**Issue filed at:** 2026-04-22T17:00:24Z (scope updated 2026-04-23T02:45:25Z, Q&A answers encoded 2026-04-23T~18:00Z)
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_executor.py:1110-1180` (harness invocation path) — still holds. The `from agent.sdk_client import ... get_response_via_harness` import is at line 1110-1114; the `get_response_via_harness(...)` call is at line 1177-1189. No `--model` anywhere in this region.
- `agent/sdk_client.py:1549` (`_HARNESS_COMMANDS`) — still holds at line 1549-1561. Two entries (`claude-cli`, `opencode`), neither contains `--model`.
- `agent/sdk_client.py:1405` (`_is_auth_error`) — still holds, but **OUT OF SCOPE** for this plan (deferred to #1137). Noted only.
- `models/agent_session.py:217` (`model = Field(null=True)`) — still holds.
- `tools/valor_session.py:1040-1046` (`--model` flag on `create`) — still holds.
- `agent/session_completion.py:448-456` (PM final-delivery `get_response_via_harness` call) — still holds; currently passes no `model` kwarg and has a silent-fallback path (`final_text = ""` → summary_context fallback) that this plan tightens.
- `config/settings.py:169-175` (`ModelSettings`) — still holds, only `ollama_vision_model` today.

**Cited sibling issues/PRs re-checked:**
- **#1106** — closed 2026-04-22T17:01:24Z as superseded by #1129. Expected.
- **#1137** — OPEN: "Backlog: Ollama credit-exhaust fallback for harness". Tom updated its body 2026-04-23 to flag the completion-runner as the priority consumer. Confirmed.
- **#900** — closed 2026-04-13, implemented by **PR #909**. PR #909's wiring is correct but lives on a code path the worker no longer uses. **This is the crux of the bug.**
- **#928** — closed; made the PM always pass `--model`. Complements this work — PM dispatches already carry the flag, so once the wiring lands, the whole chain is honest.

**Commits on main since issue was filed (touching referenced files):**
- `a13b7470` (PR #1135) "Compaction hardening: JSONL backup, cooldown, post-compact nudge guard" — touched `agent/session_executor.py` and `agent/sdk_client.py` but unrelated to model wiring. **Irrelevant.**
- `9935778d` (post-pull today) — queue/output handler cleanup; unrelated.

**Active plans in `docs/plans/` overlapping this area:** None. The `agent-session-model-audit.md` plan covers status-field cleanup, no overlap.

**Notes:**
- **One factual correction vs. issue body:** the issue claims "All sessions currently run on whichever model the global `CLAUDE_MODEL` env var specifies — usually Sonnet." Repo-wide grep finds **zero** references to `CLAUDE_MODEL`. No such env var exists; the Claude CLI applies its own internal default. The gap and the fix are the same; the new default-Opus choice is effectively a behavior change, not a migration from Sonnet.
- **One doc correction needed:** `docs/features/agent-session-model.md:192-193` states the flow is `sdk_client.get_agent_response_sdk() → ValorAgent(model=...) → ClaudeAgentOptions`. That path is not the live path anymore. Docs must be corrected when this ships.

## Prior Art

- **PR #909** (merged 2026-04-13): added `model` field on `AgentSession`, `--model` flag on `valor-session create`, and threaded `model` through `ValorAgent → _create_options() → ClaudeAgentOptions`. Wiring is sound but routed via a dormant code path. **This plan finishes PR #909's job on the live path.**
- **PR #928 / Issue #928** (closed): made the PM persona require `--model` on every dev-session dispatch. Complements this plan — PM already passes the value end-to-end; we just need the worker to honour it.
- **PR #1054** (merged 2026-04-20): collapsed `session_mode` into `session_type`, removed `role` field. Only per-session knob for routing is now `session_type` + `model`.
- **Issue #1106** (closed, superseded by this issue): original scope included both per-session model routing AND Ollama fallback. The 2026-04-23 split peeled off Ollama into **#1137**.

## Research

No external research performed — the work is purely internal wiring. Two externally-verifiable claims were checked against local tooling:

- **Claude CLI `--model` flag format** — confirmed via `claude --help`: accepts both short aliases (`opus`, `sonnet`, `haiku`) and full names (`claude-sonnet-4-6`, `claude-opus-4-7`). Short aliases are the precedent used throughout `.claude/agents/*.md` subagent frontmatter, and Tom has confirmed (Q4) we use short aliases for this plan too.
- **No `CLAUDE_MODEL` env var in the codebase** — confirmed via repo-wide grep. The issue's assertion that such a var is the current default is stale, and Tom has confirmed (Q5) we do **not** introduce a compatibility shim.

## Decisions (Resolved Open Questions)

Tom's answers from 2026-04-23 Q&A, encoded here so the plan is self-contained.

### D1. Precedence Cascade (generalizing the Q1 answer)

**General rule (applies to future attributes too, not just `model`):**
> The attribute value that applies is the one explicitly set closest to the LLM call. When not set, defaults cascade with codebase defaults at the bottom.

**Concrete precedence for session model resolution** (highest to lowest):

1. **`session.model`** (explicit, closest to call) — set via `valor_session create --model <name>`, persisted on the `AgentSession` record.
2. **`settings.models.session_default_model`** (machine-local override) — `pydantic-settings` field, env var `MODELS__SESSION_DEFAULT_MODEL`, sourced from `~/Desktop/Valor/.env` (iCloud-synced).
3. **Codebase default** — `"opus"`, hard-coded as the pydantic Field default, lowest priority.

This cascade is implemented explicitly in `agent/session_executor.py` via a `_resolve_session_model()` helper (or equivalent inline), and documented in code comments + `docs/features/agent-session-model.md`. Tests assert all three branches fire correctly.

### D2. Stage Table Stays (Q2)

The PM persona's Stage→Model Dispatch Table (`config/personas/project-manager.md:134-148`) remains intact:

- **PM persona flow** assigns Sonnet to BUILD/TEST/PATCH/DOCS, Opus to PLAN/CRITIQUE/REVIEW.
- **After this wiring lands**, those Sonnet overrides start actually taking effect (today they're ignored because the worker drops the flag).
- **"Opus default"** means: when no explicit choice is made anywhere in the cascade, fall back to Opus. It does **not** mean force everyone onto Opus.
- If the PM skill says "use Sonnet for BUILD", the PM explicitly passes `--model sonnet` when spawning the Dev session. That value sets `session.model="sonnet"` on the Dev's AgentSession record, which wins in the cascade. **That is the desired behavior.**

We are not changing skills or workflows — we are setting fallback defaults that the existing skill-level decisions (which were previously inert) can override.

### D3. KISS on Dev Inheritance (Q3)

If the PM omits `--model` on `valor_session create --role dev`, the Dev session runs on **settings default (Opus)**. No stage inference, no PM-model inheritance, no special cases. One code path:

```
session.model if set else settings.models.session_default_model if set else "opus"
```

### D4. Short Aliases (Q4)

Use short aliases throughout: `opus`, `sonnet`, `haiku`. Not pinning specific versions in this plan. The Claude CLI accepts both short and long forms, so operators who want to pin a specific version can override `MODELS__SESSION_DEFAULT_MODEL=claude-opus-4-7` in `.env` — the cascade passes the value through verbatim.

### D5. No `CLAUDE_MODEL` Compat Shim, BUT `.env.example` Gets the New Var (Q5)

- **No** `CLAUDE_MODEL` env var is introduced. The codebase has never used one; there is nothing to preserve compatibility with.
- **YES** `MODELS__SESSION_DEFAULT_MODEL` is added to `.env.example` with a comment explaining usage and the full precedence cascade from D1 (so operators can find it when looking at `.env.example`).

### D6. Completion-Runner Hardening (Q6 — SCOPE EXPANSION)

The PM-to-CEO final-delivery drafter at `agent/session_completion.py:450` is hardened into a quality + reliability gate, not a pass-through. Four requirements:

**(a) Always Opus.** The PM-to-CEO message is high-value. Quality trumps cost for this one call. The completion-runner pins `model="opus"` regardless of the PM session's model.

**(b) 2-Pass Drafter (Self-Review/Refine).** A PM would typically read and edit their message a couple of times before sending. Implement as two sequential harness calls:

- **Pass 1 — Draft.** Existing prompt generates the first-draft message. Output captured as `draft_text`.
- **Pass 2 — Self-Review/Refine.** A second harness call with a review prompt. The prompt hands the draft back and asks for refinement against Tom's quality vision:
  - short and sweet
  - high density of information
  - thoughtfully worded
  Output is the refined, final message. This is a **code review of the message content**, not prompt engineering. The review prompt stays tight (< 200 tokens of instruction).
- Both passes use `--model opus`, same session/UUID context (continues the PM session so Pass 2 sees Pass 1's draft as prior context).

**(c) No-Silent-Fail Contract.** Tom: "it is critical that messages are sent (no silent fail)."

- If Pass 1 returns empty/None → **raise loudly** (`RuntimeError` with context). No fallback to `summary_context`. Log at ERROR level.
- If Pass 2 returns empty/None → **fall back to Pass 1's draft** (not empty-string). That still satisfies "a message is sent" and preserves the Pass 1 content.
- Any harness exception → log at ERROR level and re-raise. The existing silent `final_text = ""` path at `session_completion.py:463` is **removed**.
- Delivery must happen: if `send_cb` raises, the error surfaces to the worker (not swallowed into a warning log). Current behavior already logs and continues — we keep log-and-continue for `send_cb` because the raised error upstream is covered by (b/c), but the log level rises from `error` to `error` with a more specific message.

**(d) Ollama Fallback — Deferred to #1137.**

- The broader Ollama harness fallback stays in #1137's scope.
- #1137's body has been updated (2026-04-23) to flag the completion-runner as the priority consumer once that issue is picked up.
- Until #1137 ships: if Anthropic is down, the completion-runner **raises loudly** (satisfies (c) — no silent fail, just a loud failure). Tom can manually retry or draft.
- When #1137 ships, the completion-runner path is the first consumer wired to the Ollama fallback.

Cost note: PM running on Opus + completion-runner on Opus is not ~10× vs Haiku — Tom prioritizes quality here.

## Data Flow

The change is strictly additive on one axis (insert `--model` into `harness_cmd`). The end-to-end flow after this plan:

### Normal session flow
1. **Entry point**: PM or human invokes `python -m tools.valor_session create --role dev --model opus --message "..."` (or omits `--model`).
2. **`tools/valor_session.py:cmd_create`** → constructs `AgentSession` via `enqueue_agent_session(model=args.model)`.
3. **`agent/agent_session_queue.py:_push_agent_session`** → persists `session.model` to Redis.
4. **Worker picks up the session** → `agent/session_executor.py:_execute_agent_session` → reaches the harness branch at line 1109.
5. **NEW: resolve model via the cascade** (`session.model` → `settings.models.session_default_model` → `"opus"`), pass to `get_response_via_harness(model=...)`.
6. **`agent/sdk_client.py:get_response_via_harness`** — new `model: str | None = None` kwarg. When set, inject `["--model", model]` into `harness_cmd` before the positional `message`.
7. **Subprocess argv**: `claude -p --verbose --output-format stream-json --include-partial-messages --permission-mode bypassPermissions --model opus [--resume UUID] <message>`.
8. **Output**: the Claude CLI honours `--model` and the session runs on the requested model.

### Completion-runner flow (PM final delivery)

1. **Trigger**: pipeline reaches terminal state → `_deliver_pipeline_completion` invoked.
2. **Resolve PM UUID** (existing `_get_prior_session_uuid`).
3. **Pass 1 — Draft**: `raw_draft = await get_response_via_harness(message=prompt, model="opus", prior_uuid=pm_uuid, ...)`.
   - If `raw_draft` is None/empty → **raise RuntimeError** (no silent fallback).
4. **Pass 2 — Self-Review/Refine**: `refined = await get_response_via_harness(message=REVIEW_PROMPT.format(draft=raw_draft), model="opus", prior_uuid=pm_uuid, ...)`.
   - If `refined` is None/empty → fall back to `raw_draft` (Pass 1's content), log WARNING.
5. **Deliver** via `send_cb`. If `send_cb` raises, log ERROR and re-raise to the caller (background task).

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**:
  - `get_response_via_harness()` adds a `model: str | None = None` kwarg. Default `None` preserves current behavior.
  - `config/settings.py` adds one new field on `ModelSettings`: `session_default_model: str = "opus"`.
  - `.env.example` documents `MODELS__SESSION_DEFAULT_MODEL` with precedence note.
  - `session_completion.py` gains a `_REVIEW_PROMPT` constant and a 2-pass control flow inside `_deliver_pipeline_completion`.
- **Coupling**: slightly reduces coupling. Today, per-session model selection is dead weight on `AgentSession`. After this, the field has meaning at the subprocess boundary.
- **Data ownership**: unchanged. `AgentSession.model` remains the single source of truth.
- **Reversibility**: trivial. Revert the edits to `session_executor.py`, `sdk_client.py`, `session_completion.py`, `settings.py`, `.env.example`. No migrations, no Redis schema changes, no stored-state format changes.

## Appetite

**Size:** Small (bumped up slightly by the completion-runner 2-pass + no-silent-fail work in D6, but still single-session builder scope)

**Team:** Solo dev (builder + validator pair)

**Interactions:**
- PM check-ins: 0 (Tom has answered all Open Questions; pipeline auto-progresses unless a new question surfaces)
- Review rounds: 1 (standard `/do-pr-review` pass)

Rationale: the surface area is five files (~40 lines of live code), one settings field, one env.example entry, and targeted tests. The scope expansion on the completion-runner is small (one new constant + two `get_response_via_harness` calls replacing one, plus the raise-loudly path replacing a silent fallback). Still a Small appetite.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` CLI on `$PATH` with `--model` support | `claude --help \| grep -q "^  --model"` | Confirms the CLI binary accepts `--model` |

No API keys, no services, no env changes required to ship.

## Solution

### Key Elements

- **Per-session `--model` injection in the harness path**: `session_executor.py` resolves the effective model via the D1 cascade and passes it to `get_response_via_harness`. The subprocess argv includes `--model <value>` when the cascade resolves to a truthy string.
- **Precedence cascade (`_resolve_session_model` helper)**: explicit three-level fallback (`session.model` → settings → codebase default `"opus"`). Tested at each level.
- **Configurable default in `config/settings.py`**: new `ModelSettings.session_default_model: str = "opus"`. Env override via `MODELS__SESSION_DEFAULT_MODEL` (pydantic-settings nested delimiter).
- **`.env.example` documentation**: adds the new var with a comment explaining the precedence cascade.
- **Stage table stays (D2)**: PM persona unchanged. Stage-based Sonnet overrides start taking effect (previously inert).
- **Completion-runner hardening (D6)**: always Opus, 2-pass draft+refine, no-silent-fail raise-loudly contract. Ollama fallback deferred to #1137.
- **Subagents untouched**: `.claude/agents/*.md` keep their `model:` frontmatter mechanism, consumed by the Claude CLI's own subagent machinery, independent of our `--model` flag.

### Flow

`valor_session create --role dev --model opus` → AgentSession stored with `model=opus` → worker pops session → `_resolve_session_model(session)` returns `"opus"` (level 1 hit) → `get_response_via_harness(..., model="opus")` → argv includes `--model opus` → Claude CLI runs on Opus.

Parallel path when `--model` is omitted:

`valor_session create --role dev` → AgentSession stored with `model=None` → worker pops session → `_resolve_session_model(session)` checks session.model (None), then `settings.models.session_default_model` (default `"opus"`) → returns `"opus"` → argv includes `--model opus`.

Operator-override path:

`MODELS__SESSION_DEFAULT_MODEL=sonnet` in `~/Desktop/Valor/.env` → `_resolve_session_model(session)` for a session without explicit `model` returns `"sonnet"` → argv includes `--model sonnet`.

Completion-runner path (D6):

`_deliver_pipeline_completion` → Pass 1 `get_response_via_harness(..., model="opus")` → empty? raise. Non-empty → Pass 2 `get_response_via_harness(REVIEW_PROMPT.format(draft=...), ..., model="opus")` → empty? fall back to draft with WARNING. Non-empty → `send_cb(refined)`.

### Technical Approach

Five edits in live code, one in settings, one in `.env.example`.

**Edit 1 — `agent/sdk_client.py:get_response_via_harness`** (~20 lines)
- Add `model: str | None = None` as a keyword-only parameter.
- After `harness_cmd = list(_HARNESS_COMMANDS["claude-cli"])` (~line 1637-1638), if `model` is truthy, append `["--model", model]` to `harness_cmd` *before* the positional `message` assembly. Argv order: `harness_cmd + [--resume, uuid, message]` or `harness_cmd + [message]`; `--model` must live in `harness_cmd` so it precedes positional `message`.
- Log at INFO on first-turn path: `logger.info(f"[harness] Using --model {model} for session_id={session_id}")`.
- Docstring `Args:` entry updated.

**Edit 2 — `agent/session_executor.py`** (~10 lines)
- Add `_resolve_session_model(session) -> str | None` helper (module-level). Returns `session.model or settings.models.session_default_model or None`. Documents the D1 cascade in the docstring.
- At the harness branch around line 1109: `effective_model = _resolve_session_model(agent_session)`. Pass `model=effective_model` into the `get_response_via_harness(...)` call at line 1177-1189.
- Inline comment referencing D1.

**Edit 3 — `config/settings.py`** (~10 lines)
- Extend `ModelSettings` (line 169-175) with:
  ```python
  session_default_model: str = Field(
      default="opus",
      description=(
          "Fallback Claude model for sessions where AgentSession.model is None/empty. "
          "Part of the precedence cascade: session.model > settings > codebase default 'opus'. "
          "Short aliases (opus, sonnet, haiku) preferred; full names (claude-opus-4-7) also accepted. "
          "Env: MODELS__SESSION_DEFAULT_MODEL."
      ),
  )
  ```
- No change to `ollama_vision_model`.

**Edit 4 — `.env.example`** (~8 lines added)
- Add a new subsection (after `OLLAMA_VISION_MODEL`, before `Email Bridge`):
  ```
  # =============================================================================
  # Session Model Routing
  # =============================================================================

  # Codebase default for Claude sessions (PM, Teammate, Dev) when AgentSession.model
  # is not explicitly set. Precedence cascade (closest to LLM call wins):
  #   1. AgentSession.model (per-session, via `valor-session create --model <name>`)
  #   2. MODELS__SESSION_DEFAULT_MODEL (this var — machine-local override)
  #   3. codebase default ("opus")
  # Short aliases: opus, sonnet, haiku. Full names also accepted.
  # MODELS__SESSION_DEFAULT_MODEL=opus
  ```

**Edit 5 — `agent/session_completion.py`** (~60 lines — the bulk of the D6 scope expansion)

Introduce the 2-pass drafter + no-silent-fail contract.

- Add a module-level constant near `_COMPLETION_PROMPT`:
  ```python
  _COMPLETION_REVIEW_PROMPT = (
      "Below is a draft final-delivery message for the user. Review it against "
      "these criteria and return a refined version:\n\n"
      "1. SHORT — no wasted words. Cut anything that isn't load-bearing.\n"
      "2. DENSE — maximum information per word. Preserve concrete outcomes.\n"
      "3. THOUGHTFUL — phrase like a colleague writing with care, not a template.\n\n"
      "Return ONLY the refined message. No preamble, no meta-commentary, no "
      "markdown headers. Just the message as it should be sent.\n\n"
      "DRAFT:\n{draft}"
  )
  ```

- Replace the current single-call block at line 446-468 with a two-pass sequence:
  1. **Pass 1**: call `get_response_via_harness(message=prompt, model="opus", ...)`. Strip result.
     - If empty/None → `raise RuntimeError(f"[completion-runner] Pass 1 (draft) returned empty for parent_id={parent_id}")`. Log ERROR before raise.
  2. **Pass 2**: call `get_response_via_harness(message=_COMPLETION_REVIEW_PROMPT.format(draft=draft_text), model="opus", prior_uuid=pm_uuid, session_id=session_id, ...)`. Strip result.
     - If empty/None → log WARNING, use `draft_text` as `final_text` (fall back to Pass 1 rather than raise — (c)'s fallback branch).
     - Else → `final_text = refined_text`.
  3. **Existing delivery** (`send_cb` call) proceeds with `final_text`, which is guaranteed non-empty at this point.

- **Remove the silent-fail path** at line 458-468 (the current `except Exception: final_text = ""` → `if not final_text: final_text = summary_context.strip() or "..."` fallback). Replace with:
  - On any harness exception → log at ERROR and re-raise (do not swallow). The background task's outer exception handler will record it; the user sees a loud failure instead of a bland summary. This aligns with D6(c): "no silent fail."

- The existing `response_delivered_at` stamping at line 494-499 moves after the successful `send_cb` — it must NOT fire if delivery raises. Current code already puts this after the send block; confirm placement during build.

**Edit 6 — `docs/features/agent-session-model.md`** (docs correction; done in DOCS stage)
- Rewrite "Per-Session Model Selection" section (lines 184-195) to describe the harness-CLI live path + the D1 precedence cascade.
- Add a short section on the completion-runner's always-Opus + 2-pass drafter policy, with pointer to #1137 for the Ollama fallback plan.

**What we do NOT touch:**
- `_HARNESS_COMMANDS` — no new entries (Ollama deferred to #1137).
- `_is_auth_error` call sites — no retry logic (deferred to #1137).
- `.claude/agents/*.md` — subagent model routing is independent.
- `ValorAgent` / `_create_options` — still wires `model` correctly for test fixtures. Cleanup is a separate issue.
- `config/personas/project-manager.md` Stage→Model Dispatch Table — **stays as-is per D2**. Stage overrides start taking effect once wiring lands; that IS the desired behavior.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks. Existing handlers in `get_response_via_harness` (subprocess error paths, image-dimension sentinel, context-budget) are not touched.
- [ ] The old silent-fail branch in `session_completion.py:458-468` (`except Exception: final_text = ""`) is **removed**. Its replacement logs at ERROR and re-raises. Tested by a mock that forces Pass 1 to raise.
- [ ] If `_resolve_session_model` raises (unlikely), the executor surfaces the error — no silent `None` return. Test covers.

### Empty/Invalid Input Handling
- [ ] `session.model = ""`: cascade falls through to settings default. Test asserts `--model opus` (or configured default) on argv.
- [ ] `session.model = None`: same behavior — settings default kicks in.
- [ ] `session.model = "opus"`: passed verbatim. Argv includes `--model opus`.
- [ ] `settings.models.session_default_model = ""` (operator misconfigures): cascade falls through to `None` in `_resolve_session_model` → `get_response_via_harness` receives `model=None`, truthiness check skips `--model` injection → argv goes out without `--model` and CLI uses its own default. Test asserts this graceful degradation.
- [ ] Invalid model name (e.g., `"sonic"`): passed verbatim; Claude CLI returns non-zero exit with error. No pre-validation. Test asserts existing error path propagates.
- [ ] **Completion-runner Pass 1 empty** → RuntimeError raised, no delivery. Test mocks Pass 1 to return `""`.
- [ ] **Completion-runner Pass 2 empty** → falls back to Pass 1's draft, WARNING logged, delivery proceeds with draft. Test mocks Pass 2 to return `""`.
- [ ] **Completion-runner Pass 1 exception** → re-raised with ERROR log, no delivery. Test mocks harness to raise.

### Error State Rendering
- [ ] If the Claude CLI rejects `--model <bad>`, the error reaches the user via existing `_run_harness_subprocess` → `raw.returncode != 0` path. No new silent surfaces.
- [ ] Operator-visible log line at INFO confirms the resolved model each turn.
- [ ] Completion-runner raises produce a loud ERROR log + traceback path so operators see failures immediately (no more bland "pipeline completed, see history" filler).

## Test Impact

- [ ] `tests/unit/test_harness_streaming.py` — UPDATE: existing tests construct `get_response_via_harness` call args; add the new `model` kwarg where relevant (or leave as `None` and add a new test case for model-injected argv).
- [ ] `tests/unit/test_harness_retry.py` — UPDATE: add `model=None` as default kwarg in existing assertions; add one new case asserting `--model` appears in argv when `model="opus"`.
- [ ] `tests/unit/test_sdk_client_image_sentinel.py` — NO CHANGE: image-sentinel logic runs on stdout, unrelated to argv assembly.
- [ ] `tests/integration/test_session_spawning.py` — UPDATE: `valor_session create --model opus` end-to-end case should assert the harness argv actually includes `--model opus` via subprocess mock or log capture.
- [ ] `tests/integration/test_harness_resume.py` — UPDATE: existing resume tests pass `prior_uuid`; extend one case to also pass `model=` and assert `--model opus` precedes `--resume <uuid>` in argv.
- [ ] `tests/integration/test_harness_no_op_contract.py` — UPDATE only if it asserts exact argv. Check during build.
- [ ] `tests/unit/test_deliver_pipeline_completion.py` — UPDATE: the existing silent-fail path expectations must flip. Add cases:
  - Pass 1 empty → RuntimeError raised.
  - Pass 2 empty → falls back to Pass 1 draft, delivery proceeds.
  - Pass 1 exception → re-raised (no silent swallow).
  - Happy path: Pass 1 + Pass 2 both succeed → refined message delivered, both calls used `model="opus"`.
- [ ] `tests/integration/test_pm_final_delivery.py` — UPDATE: existing integration test for PM final delivery needs the 2-pass flow assertions (both harness calls happen with `model="opus"`, argv order correct). **REPLACE** any expectation of silent summary_context fallback.
- [ ] **NEW**: `tests/unit/test_session_model_routing.py` — CREATE: focused module asserting:
  - (a) `session.model` string flows to argv.
  - (b) `session.model=None` + non-empty settings default → argv uses settings default.
  - (c) `session.model=None` + empty settings default → argv has no `--model`, graceful.
  - (d) `session.model=""` treated same as None.
  - (e) `--model` precedes `message` in argv.
  - (f) `--model` precedes `--resume <uuid>` in argv when resuming.
  - (g) INFO log line fires with the resolved value.
  - (h) `_resolve_session_model` cascade — each level covered independently.
- [ ] **NEW**: `tests/unit/test_completion_runner_two_pass.py` — CREATE: focused module for D6(b)+(c):
  - Happy path: both passes succeed, Pass 2 output delivered.
  - Pass 1 empty → RuntimeError, no delivery.
  - Pass 2 empty → WARNING log + Pass 1 delivered as fallback.
  - Pass 1 exception → re-raised.
  - Both passes use `model="opus"`.

**NOT affected:** memory system, PostToolUse hooks, subagent tests (`.claude/agents/*.md` mechanism), Popoto model tests, task list isolation tests. The change is surgical.

## Rabbit Holes

- **Rewriting `ValorAgent` or deleting it**: tempting because the `model=...` path there is now unreachable in production. Don't. Tests still use `ValorAgent`. Cleanup is a separate issue.
- **Validating model names against a whitelist**: don't. The Claude CLI already rejects unknown models cleanly.
- **Adding a `--model` override to `_HARNESS_COMMANDS["opencode"]`**: don't. Opencode is a non-Claude harness with different flag syntax.
- **Building a per-stage model dispatch dict in settings** (PLAN→opus, BUILD→sonnet, etc.): the stage table stays in PM persona prose (D2). Don't duplicate it in settings.
- **Chasing `CLAUDE_MODEL` env var**: it doesn't exist in the repo. Don't "preserve backward compat" for a var that isn't there (D5).
- **Auto-detecting Ollama as a `--model` value**: that's #1137, not this issue (D6d).
- **Inventing a new frontmatter field for PM/Teammate model**: no. PM and Teammate run on the cascade like everyone else (D2/D3).
- **Engineering Pass 2's review prompt to death**: keep the review prompt tight. Tom flagged it as a code review of message content, not prompt engineering. Don't add chain-of-thought or multi-criterion scoring rubrics.
- **Adding Pass 3+ for further refinement**: two passes is the contract. Three passes is a different plan.

## Risks

### Risk 1: Opus-by-default increases baseline cost
**Impact:** PM sessions, Teammate sessions, and Dev sessions without `--model` all resolve to Opus via the cascade. Roughly 5× Sonnet per input token.
**Mitigation:** Per D2, PM persona's stage table still dispatches Sonnet for BUILD/TEST/PATCH/DOCS — those overrides now take effect (previously inert), so tool-heavy stages still run on Sonnet. The Opus default only bites for (a) PM sessions themselves, (b) Teammate sessions, (c) Dev sessions spawned without `--model`. If cost becomes a problem, `MODELS__SESSION_DEFAULT_MODEL=sonnet` in `.env` is a one-line override per D5.

### Risk 2: Regression — executor ignores `session.model`
**Impact:** If a build regression skips the cascade, PM dispatches would silently run on Opus despite passing `--model sonnet`. Invisible without log.
**Mitigation:** INFO log line is a cheap sanity check. Unit test `test_session_model_routing.py` (a) explicitly asserts `--model <session.model>` on argv, so a regression fails tests.

### Risk 3: Short alias vs. full name mismatch (CLI version drift)
**Impact:** If Claude CLI updates drop short-alias support, passing `opus` breaks every session.
**Mitigation:** The CLI returns a clear error on unknown models with non-zero exit; existing `session_executor.py` error path surfaces it. `.claude/agents/*.md` subagents have used short aliases in production for weeks — if support dropped, subagents would already be broken. Low probability.

### Risk 4: Dormant `ValorAgent → ClaudeAgentOptions` wiring becomes confusing
**Impact:** Two paths for "how does model get to the CLI" invite drift.
**Mitigation:** Docs correction (Edit 6) is non-negotiable. Add a TODO in `_create_options()` noting that the live path is the harness CLI and this wiring is retained for test fixtures. Follow-up cleanup issue optional.

### Risk 5: Completion-runner 2-pass doubles PM-final-delivery latency
**Impact:** Two sequential harness calls roughly double the wall-clock time for final delivery (from ~X seconds to ~2X seconds).
**Mitigation:** Acceptable per Tom's quality-over-cost stance for this specific call. The final-delivery is async background work; user-visible latency is bounded by `send_cb` → Telegram, not the harness turns. If latency becomes a problem, Pass 2 could degrade to a client-side length-trim heuristic, but that's a follow-up.

### Risk 6: No-silent-fail contract surfaces failures that were previously hidden
**Impact:** Until #1137 ships, Anthropic outages that would have silently returned `summary_context` now raise and may leave users without *any* final-delivery message.
**Mitigation:** Per D6(c) + D6(d) — this is the desired behavior. "Loud failure" is better than "bland filler message that obscures a real outage." Ops will see the error in logs and can manually retry. Once #1137 ships, Ollama absorbs the outage.

### Risk 7: Pass 2 review LLM introduces hallucination in the final message
**Impact:** Pass 2 is a Claude-Opus refinement of Pass 1's draft. The review prompt says "return a refined version," but an LLM could invent facts or change meaning.
**Mitigation:** The review prompt explicitly says "Preserve concrete outcomes" and "Return ONLY the refined message." Pass 2 operates over a short input (Pass 1's draft is 2-3 sentences per `_COMPLETION_PROMPT`), bounding drift. Unit tests compare Pass 1 vs Pass 2 output length/similarity (smoke test, not strict). If drift becomes a problem in production, the review prompt tightens; this is in scope for a future tuning pass.

## Race Conditions

No new race conditions introduced.

- The read of `session.model` happens inside a single synchronous executor frame before the subprocess launch. The Redis record is populated by `_push_agent_session` at enqueue time and is immutable for the session lifetime.
- The completion-runner already uses a CAS lock (`_pipeline_complete_lock_key(parent_id)`) to dedupe concurrent invocations (race 1, race 2 from the existing design). The 2-pass sequence runs inside that locked region, so no new concurrency surface.
- Pass 1 → Pass 2 is sequential (`await` chain); no concurrent invocation of Pass 2 against the same `prior_uuid`.

## No-Gos (Out of Scope)

- **Layer 2 — Ollama credit-exhaust fallback.** Explicitly deferred to **#1137**. No new entry in `_HARNESS_COMMANDS`, no `_is_auth_error` retry, no `harness_override` field, no Ollama bootstrap. Completion-runner wiring to Ollama fallback happens when #1137 ships.
- **Per-stage model routing in settings.** Stage variation stays with the PM persona dispatch table (D2).
- **Subagent `.claude/agents/*.md` model routing.** Already works via CLI-native frontmatter. Untouched.
- **Validating/whitelisting model names.** CLI handles it.
- **Deleting the dormant `ValorAgent → _create_options()` model wiring.** Separate cleanup.
- **LiteLLM proxy alternative.** Prior #1106 floated it; dropped.
- **A per-session `harness_override` field.** That was Layer 2's mechanism. Not needed.
- **Environment variable `CLAUDE_MODEL` as an override.** Does not exist and we don't introduce one (D5).
- **Pass 3+ refinement passes** in the completion-runner. Two passes is the contract (D6b).
- **Generative self-critique scoring** (LLM-as-judge patterns) in the completion-runner. Pass 2 is a prompt-guided refinement, not a multi-criterion scoring pass.

## Update System

No update system changes required.

- The new settings field has a default value (`opus`), so existing machines behave correctly on first boot post-deploy without any env or config edits.
- `.env.example` update is documentation-only; operators can opt into `MODELS__SESSION_DEFAULT_MODEL=<name>` by editing `~/Desktop/Valor/.env` (iCloud-synced, handled by `scripts/update/env_sync.py`).
- No `scripts/remote-update.sh` changes, no `.claude/skills/update/` changes.

## Agent Integration

No agent integration required.

- The PM spawns Dev sessions via `python -m tools.valor_session create --model <name>`, which is already wrapped and exposed. `.mcp.json` is untouched.
- The bridge (`bridge/telegram_bridge.py`) does not need to import anything new.
- The completion-runner is internal worker code; not invoked via MCP.

Integration tests verifying end-to-end agent behavior: `tests/integration/test_session_spawning.py` covers the `--model` round-trip; `tests/integration/test_pm_final_delivery.py` covers the completion-runner 2-pass flow.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-model.md` — rewrite the "Per-Session Model Selection" section (lines 184-195) to describe the **harness-CLI live path** (executor's `_resolve_session_model` cascade, argv injection in `get_response_via_harness`). Remove the claim about `ValorAgent → ClaudeAgentOptions` being the live flow.
- [ ] Add a "Precedence Cascade" subsection documenting D1's general rule (closest-to-LLM-call wins, codebase defaults at bottom) with the model resolution as the worked example.
- [ ] Add a short "PM Final-Delivery Drafter" subsection covering D6: always-Opus, 2-pass (draft + self-review/refine), no-silent-fail contract, pointer to #1137 for the planned Ollama fallback.
- [ ] Add an "Override via `.env`" note pointing to `MODELS__SESSION_DEFAULT_MODEL`.
- [ ] No change needed in `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on `get_response_via_harness` gains a `model` arg entry.
- [ ] Docstring on `AgentSession.model` field updated to reflect subprocess flow.
- [ ] Docstring on new `_resolve_session_model()` helper in `session_executor.py` documents the D1 cascade.
- [ ] Module-level comment near `_COMPLETION_PROMPT` / `_COMPLETION_REVIEW_PROMPT` in `session_completion.py` explains the 2-pass + no-silent-fail contract.

### External Documentation Site
- Not applicable — this repo does not ship a public docs site.

## Success Criteria

- [ ] `session.model` is passed as `--model <value>` to the `claude -p` subprocess for every session that sets it (verified by subprocess-argv mock in unit tests and by log capture in an integration test).
- [ ] When `session.model` is None/empty, `settings.models.session_default_model` governs — unit tests cover.
- [ ] When both are empty, codebase default `"opus"` governs — unit test covers.
- [ ] **Precedence cascade test** asserts all three levels independently: explicit `session.model` beats settings; settings beats codebase default; codebase default fires when nothing else set.
- [ ] `valor-session create --model sonnet --role dev` results in argv containing `--model sonnet` at runtime (integration test).
- [ ] `MODELS__SESSION_DEFAULT_MODEL=haiku` env override flips the default for sessions without an explicit model (settings test).
- [ ] **`.env.example` contains `MODELS__SESSION_DEFAULT_MODEL`** with a usage-note comment documenting the D1 precedence cascade (grep test).
- [ ] No regression in the `--resume UUID` path: a resumed session still passes `--model` correctly, and `--model <value>` appears before `--resume <uuid>` in argv.
- [ ] `docs/features/agent-session-model.md` updated to describe the live path, the precedence cascade, and the completion-runner 2-pass policy.
- [ ] INFO-level log line on each harness turn shows the resolved model.
- [ ] PM persona Stage→Model Dispatch Table remains functional — Sonnet overrides written by the PM still flow to argv (no regression in existing stage routing); if anything, they now take effect where they previously didn't.
- [ ] **Completion-runner: always Opus** — both harness calls in `_deliver_pipeline_completion` use `model="opus"` regardless of PM session model (unit test).
- [ ] **Completion-runner: 2-pass drafter** — Pass 1 produces a draft, Pass 2 refines; both calls observable in test (unit test + integration test).
- [ ] **Completion-runner: no-silent-fail** — Pass 1 empty raises `RuntimeError`; Pass 1 exception re-raises; Pass 2 empty logs WARNING and falls back to Pass 1 (three unit tests).
- [ ] **Completion-runner: Ollama-fallback-pending-#1137 note** — code comment near the Pass 1 call references #1137 as the issue that will add Ollama failover on Anthropic outage.
- [ ] Tests pass (`pytest tests/ -x -q`).
- [ ] Format clean (`python -m ruff format .`).

## Team Orchestration

Small appetite, one builder + one validator.

### Team Members

- **Builder (session-model-routing)**
  - Name: `model-router-builder`
  - Role: Implement Edits 1–5 (sdk_client.py, session_executor.py, settings.py, .env.example, session_completion.py) and the two new test modules.
  - Agent Type: builder
  - Resume: true

- **Validator (session-model-routing)**
  - Name: `model-router-validator`
  - Role: Run the full test suite, assert argv / log-line assertions, confirm 2-pass flow works, verify no #1137-scope creep. Confirm docs-correction task is queued for DOCS stage but NOT landed in BUILD.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the `session_default_model` setting
- **Task ID**: build-settings
- **Depends On**: none
- **Validates**: unit tests for `Settings.models.session_default_model` default and env-override
- **Informed By**: D5 (no `CLAUDE_MODEL` var to preserve)
- **Assigned To**: `model-router-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `session_default_model: str = Field(default="opus", ...)` to `ModelSettings` in `config/settings.py` with the description from Edit 3.
- Verify `MODELS__SESSION_DEFAULT_MODEL=sonnet` env override works via pydantic-settings `env_nested_delimiter="__"`.

### 2. Document the new setting in `.env.example`
- **Task ID**: build-env-example
- **Depends On**: build-settings
- **Validates**: grep test for `MODELS__SESSION_DEFAULT_MODEL` in `.env.example`
- **Informed By**: D5
- **Assigned To**: `model-router-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add the subsection from Edit 4 in `.env.example` (after `OLLAMA_VISION_MODEL`, before `Email Bridge`).
- The entry must include the full precedence cascade comment from D1.

### 3. Thread `model` through `get_response_via_harness`
- **Task ID**: build-harness-model
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_streaming.py`, `tests/unit/test_harness_retry.py`, new `tests/unit/test_session_model_routing.py`
- **Informed By**: Data Flow section; argv-order finding (`--model` lives in `harness_cmd`, must precede `--resume` and positional `message`)
- **Assigned To**: `model-router-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `model: str | None = None` keyword-only param to `get_response_via_harness` in `agent/sdk_client.py`.
- After the `harness_cmd = list(_HARNESS_COMMANDS["claude-cli"])` line (~1637-1638), if `model`: `harness_cmd.extend(["--model", model])`.
- Add INFO log line: `logger.info(f"[harness] Using --model {model} for session_id={session_id}")` when `model` is set.
- Update the docstring `Args:` block.

### 4. Wire executor to pass resolved `session.model` to the harness
- **Task ID**: build-executor-wiring
- **Depends On**: build-settings, build-harness-model
- **Validates**: integration test extension in `tests/integration/test_session_spawning.py`
- **Informed By**: D1 (precedence cascade), D3 (KISS on dev inheritance)
- **Assigned To**: `model-router-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_executor.py` (around line 1109-1189): import `settings` if not already imported.
- Add module-level `_resolve_session_model(session) -> str | None` helper per Edit 2. Docstring documents the D1 cascade.
- Before the `async def do_work()` closure: `_effective_model = _resolve_session_model(agent_session)`.
- Pass `model=_effective_model` into the `get_response_via_harness(...)` call at line 1177-1189.

### 5. Harden the completion-runner (D6 scope expansion)
- **Task ID**: build-completion-runner
- **Depends On**: build-harness-model
- **Validates**: new `tests/unit/test_completion_runner_two_pass.py`, updates to `tests/unit/test_deliver_pipeline_completion.py`, updates to `tests/integration/test_pm_final_delivery.py`
- **Informed By**: D6 — always-Opus, 2-pass drafter, no-silent-fail, Ollama-pending-#1137
- **Assigned To**: `model-router-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `_COMPLETION_REVIEW_PROMPT` module-level constant per Edit 5.
- Rewrite the harness-call block in `_deliver_pipeline_completion` (lines 446-468) as a 2-pass sequence:
  - Pass 1 (`get_response_via_harness(..., model="opus")`) → captures `draft_text`. Empty → `RuntimeError` with ERROR log.
  - Pass 2 (`get_response_via_harness(message=_COMPLETION_REVIEW_PROMPT.format(draft=draft_text), model="opus", prior_uuid=pm_uuid, ...)`) → captures `refined_text`. Empty → WARNING log, fall back to `draft_text`.
- **Remove** the silent `except Exception: final_text = ""` → `summary_context` fallback at lines 458-468. Exceptions re-raise with ERROR log.
- Add a code comment near Pass 1 referencing #1137 as the pending Ollama-fallback issue.

### 6. Write unit tests for model-routing logic
- **Task ID**: build-unit-tests-routing
- **Depends On**: build-harness-model, build-executor-wiring
- **Assigned To**: `model-router-builder`
- **Agent Type**: test-writer
- **Parallel**: true
- Create `tests/unit/test_session_model_routing.py` covering all eight cases from Test Impact (items a-h under "NEW").

### 7. Write unit tests for the completion-runner 2-pass flow
- **Task ID**: build-unit-tests-completion
- **Depends On**: build-completion-runner
- **Assigned To**: `model-router-builder`
- **Agent Type**: test-writer
- **Parallel**: true
- Create `tests/unit/test_completion_runner_two_pass.py` covering all five cases from Test Impact (happy path, Pass 1 empty → RuntimeError, Pass 2 empty → WARNING + Pass 1 fallback, Pass 1 exception → re-raised, both passes use `model="opus"`).

### 8. Extend integration tests
- **Task ID**: build-integration-tests
- **Depends On**: build-executor-wiring, build-completion-runner
- **Assigned To**: `model-router-builder`
- **Agent Type**: test-writer
- **Parallel**: false
- In `tests/integration/test_session_spawning.py`, extend the `--model` round-trip test to assert `--model opus` appears on argv.
- In `tests/integration/test_harness_resume.py`, add one case with `model="opus"` + `prior_uuid` asserting argv order (`--model` before `--resume`).
- In `tests/integration/test_pm_final_delivery.py`, **REPLACE** any expectation of silent summary_context fallback; add 2-pass flow assertions.

### 9. Validate everything
- **Task ID**: validate-all
- **Depends On**: all prior
- **Assigned To**: `model-router-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite (`pytest tests/ -x -q`).
- Run `python -m ruff format .` (formatting only per user global preference — no `ruff check`).
- Manually smoke-test: `python -m tools.valor_session create --role dev --slug test-model-wiring --message "say hello"` with worker running; confirm log line `[harness] Using --model opus`.
- Verify no changes to `_HARNESS_COMMANDS` (Ollama entry must NOT exist).
- Verify no changes to `_is_auth_error` call sites (no retry logic).
- Verify `ValorAgent._create_options()` model wiring is still intact (not ripped out).
- Verify `.env.example` contains `MODELS__SESSION_DEFAULT_MODEL`.
- Verify completion-runner: no silent-fail path remains; both calls pin `model="opus"`.
- Report pass/fail.

### 10. Docs correction (deferred to DOCS stage)
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: (documentarian in DOCS stage — not part of BUILD)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md` per the Documentation section above (live path, precedence cascade, completion-runner 2-pass).
- Update docstring on `AgentSession.model`.
- Update docstring on `get_response_via_harness`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_session_model_routing.py tests/unit/test_completion_runner_two_pass.py tests/unit/test_harness_streaming.py tests/unit/test_harness_retry.py -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_session_spawning.py tests/integration/test_harness_resume.py tests/integration/test_pm_final_delivery.py -x -q` | exit code 0 |
| All tests pass | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `--model` appears in harness argv | `grep -n '"--model"' agent/sdk_client.py` | output > 0 |
| Executor cascade helper exists | `grep -n '_resolve_session_model' agent/session_executor.py` | output > 0 |
| No Ollama harness added (Layer 2 guard) | `grep -c '"ollama"' agent/sdk_client.py` | exit code 1 |
| No credit-retry added (Layer 2 guard) | `grep -nE '_is_auth_error.*retry\|harness_override' agent/` | exit code 1 |
| Settings default is "opus" | `python -c "from config.settings import settings; assert settings.models.session_default_model == 'opus'"` | exit code 0 |
| `.env.example` has the new var | `grep -n 'MODELS__SESSION_DEFAULT_MODEL' .env.example` | output > 0 |
| Completion-runner uses Opus (Pass 1) | `grep -nE 'get_response_via_harness.*model="opus"' agent/session_completion.py` | output ≥ 2 |
| Completion-runner has review prompt | `grep -n '_COMPLETION_REVIEW_PROMPT' agent/session_completion.py` | output > 0 |
| No silent-fail fallback remains | `grep -n "final_text = \"\"" agent/session_completion.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

All resolved — see **Decisions (D1–D6)** section above. No open questions remain. Plan is ready for `/do-plan-critique`.
