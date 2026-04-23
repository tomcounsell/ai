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
When an `AgentSession` has `model` set, the `claude -p` subprocess spawned for that session receives `--model <value>` on its argv. When the field is `None`, behavior is unchanged (CLI default applies). The field is respected for all three session types (PM, Teammate, Dev).

Additionally — and scoped by the 2026-04-23 issue update — the **default model for all three session types is Opus**, configurable via `config/settings.py`. PM and Teammate always run on Opus (there is no stage gradient for them). Dev defaults to Opus, but the PM can override per-spawn via `--model` on `valor_session create`, which persists to `AgentSession.model` and therefore flows through the new wiring.

## Freshness Check

**Baseline commit:** `ceedbe68b76337baa317a719ef217e13f3b82852`
**Issue filed at:** 2026-04-22T17:00:24Z (scope updated 2026-04-23T02:45:25Z)
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_executor.py:1110-1180` (harness invocation path) — still holds. The `from agent.sdk_client import ... get_response_via_harness` import is at line 1110-1114; the `get_response_via_harness(...)` call is at line 1177-1189. No `--model` anywhere in this region.
- `agent/sdk_client.py:1549` (`_HARNESS_COMMANDS`) — still holds at line 1549-1561. Two entries (`claude-cli`, `opencode`), neither contains `--model`.
- `agent/sdk_client.py:1405` (`_is_auth_error`) — still holds, but **OUT OF SCOPE** for Layer 1 per 2026-04-23 scope update. Noted only.
- `models/agent_session.py:217` (`model = Field(null=True)`) — still holds.
- `tools/valor_session.py:1040-1046` (`--model` flag on `create`) — still holds (note: line shifted slightly; the `--model` arg is at line 1040-1046, end of create subparser).

**Cited sibling issues/PRs re-checked:**
- **#1106** — closed 2026-04-22T17:01:24Z as superseded by #1129. Expected.
- **#1137** — OPEN: "Backlog: Ollama credit-exhaust fallback for harness" — created by the 2026-04-23 scope split. This is where Layer 2 lives now.
- **#900** — closed 2026-04-13, implemented by **PR #909**. PR #909's description asserts "Thread session model through `ValorAgent` → `ClaudeAgentOptions`" — that wiring is correct but lives on a code path the worker no longer uses. **This is the crux of the bug.**
- **#928** — closed, "PM dev-session briefing quality: include recon summary, key files, constraints, and --model in every dispatch". Made the PM always pass `--model`. Complements this work — PM dispatches already carry the flag, so once the wiring lands, the whole chain is honest.

**Commits on main since issue was filed (touching referenced files):**
- `a13b7470` (PR #1135) "Compaction hardening: JSONL backup, cooldown, post-compact nudge guard" — touched `agent/session_executor.py` and `agent/sdk_client.py` but unrelated to model wiring (PreCompact hook + JSONL backup). Does not change the harness invocation path. **Irrelevant.**

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/agent-session-model-audit.md` is about `status` KeyField duplication and dead field pruning — no overlap.
- No other plan touches `session_executor.py:1109-1189` or `_HARNESS_COMMANDS`.

**Notes:**
- **One factual correction vs. issue body:** the issue claims "All sessions currently run on whichever model the global `CLAUDE_MODEL` env var specifies — usually Sonnet." `grep -rn CLAUDE_MODEL` across the repo finds **zero** references. No such env var exists; the Claude CLI applies its own internal default. This does not change the plan — the gap and the fix are the same — but the new default-Opus choice is then effectively a behavior change, not a migration from Sonnet.
- **One doc correction needed:** `docs/features/agent-session-model.md:192-193` states the flow is `sdk_client.get_agent_response_sdk() → ValorAgent(model=...) → ClaudeAgentOptions`. That path is not the live path anymore. Docs must be corrected when this ships.

## Prior Art

- **PR #909** (merged 2026-04-13, by this author): added `model` field on `AgentSession`, `--model` flag on `valor-session create`, and threaded `model` through `ValorAgent → _create_options() → ClaudeAgentOptions`. Wiring is sound but routed via a code path the current worker bypasses. **This plan finishes PR #909's job on the live path.**
- **PR #928 / Issue #928** (closed): made the PM persona require `--model` on every dev-session dispatch. Complements this plan — PM already passes the value end-to-end; we just need the worker to honour it.
- **PR #1054** (merged 2026-04-20): collapsed `session_mode` into `session_type`, removed `role` field. Means the only per-session knob for routing is now `session_type` + `model` — we lean into the `model` field, no new fields needed.
- **Issue #1106** (closed, superseded by this issue): the original scope included both per-session model routing AND Ollama fallback. The 2026-04-23 split peeled off Ollama into **#1137**. This plan follows the Layer-1-only scope.

## Research

No external research performed — the work is purely internal wiring. Two externally-verifiable claims were checked against local tooling:

- **Claude CLI `--model` flag format** — confirmed via `claude --help`: accepts both short aliases (`opus`, `sonnet`, `haiku`) and full names (`claude-sonnet-4-6`, `claude-opus-4-7`). The short alias is the precedent used throughout `.claude/agents/*.md` subagent frontmatter, so we follow that convention.
- **No `CLAUDE_MODEL` env var in the codebase** — confirmed via repo-wide grep. The issue's assertion that such a var is the current default is stale.

## Data Flow

The change is strictly additive on one axis (insert `--model` into `harness_cmd`). The end-to-end flow after this plan:

1. **Entry point**: PM or human invokes `python -m tools.valor_session create --role dev --model opus --message "..."` (or omits `--model`, in which case the PM default kicks in per Opus-for-all policy).
2. **`tools/valor_session.py:cmd_create`** → constructs `AgentSession` via `enqueue_agent_session(model=args.model)`.
3. **`agent/agent_session_queue.py:_push_agent_session`** → persists `session.model` to Redis.
4. **Worker picks up the session** → `agent/session_executor.py:_execute_agent_session` → reaches the harness branch at line 1109.
5. **NEW: read `session.model` (with default lookup) and pass to `get_response_via_harness(model=...)`**. The executor is already reading the session record via `agent_session` in scope (see line 1102-1107); no extra Redis fetch.
6. **`agent/sdk_client.py:get_response_via_harness`** — existing `harness_cmd: list[str] | None = None` parameter. NEW: accept an optional `model: str | None = None` kwarg. When set, inject `["--model", model]` into the final `cmd` between `harness_cmd` and the positional `message`.
7. **`claude -p --verbose --output-format stream-json --include-partial-messages --permission-mode bypassPermissions --model opus [--resume UUID] <message>`** is the subprocess argv.
8. **Output**: the Claude CLI honours `--model` and the session runs on the requested model.

The `session_completion.py:448-456` harness call (PM final-delivery runner) should use the PM session's model as well — that runner is a Haiku-sized job in principle but is tied to the PM session's own cost tier. We pass the same resolved value; no special casing.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**:
  - `get_response_via_harness()` adds a `model: str | None = None` kwarg. Default `None` preserves current behavior.
  - `config/settings.py` adds one new nested field on `ModelSettings`: `session_default_model: str = "opus"` (or a richer per-role dict — see Open Questions).
- **Coupling**: slightly reduces coupling. Today, per-session model selection is dead weight on `AgentSession`. After this, the field has meaning at the subprocess boundary rather than at the dormant `ValorAgent` boundary.
- **Data ownership**: unchanged. `AgentSession.model` remains the single source of truth.
- **Reversibility**: trivial. Revert the two edits to `session_executor.py` and `sdk_client.py`, restore the settings default. No migrations, no Redis schema changes, no stored-state format changes.

## Appetite

**Size:** Small

**Team:** Solo dev (builder + validator pair is sufficient)

**Interactions:**
- PM check-ins: 1 (plan review before build, to resolve the Open Questions below)
- Review rounds: 1 (standard `/do-pr-review` pass)

Rationale: the surface area is two files (~20 lines of live code), one settings field, and targeted tests. The cost is in getting the defaults and inheritance rule right, not in code volume.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` CLI on `$PATH` with `--model` support | `claude --help \| grep -q "^  --model"` | Confirms the CLI binary accepts `--model` |

No API keys, no services, no env changes.

## Solution

### Key Elements

- **Per-session `--model` injection in the harness path**: `session_executor.py` reads `session.model` (with a default) and passes it to `get_response_via_harness`. The subprocess argv includes `--model <value>` when resolved to a non-empty string.
- **Configurable default in `config/settings.py`**: one new field on `ModelSettings` giving the default model for sessions that don't specify one. Defaults to `"opus"`. Null in the session overrides via the default; an explicit value in the session takes precedence.
- **PM / Teammate always run on the default (Opus)**: their `AgentSession.model` is not set via `valor_session create` anyway (no public CLI path writes PM/Teammate model at runtime), so the default governs.
- **Dev sessions inherit Opus as the default, PM overrides per-spawn**: the PM already passes `--model` via `valor_session create` per `config/personas/project-manager.md`. The only change is that the stage→model table becomes advisory rather than wasted — but a separate decision (Open Question 1) is whether the scope update's "Opus for all roles" means the PM's stage table should be simplified to always pass Opus, or whether PM remains free to pass Sonnet/Haiku for BUILD/TEST/DOCS.
- **Subagents untouched**: `.claude/agents/*.md` keep their `model:` frontmatter mechanism, which is consumed by the Claude CLI's own subagent machinery, independent of our `--model` flag on the parent subprocess.

### Flow

`valor_session create --role dev --model opus` → AgentSession stored with `model=opus` → worker pops session → executor reads `session.model` → executor calls `get_response_via_harness(..., model="opus")` → argv includes `--model opus` → Claude CLI runs on Opus → session responds.

Parallel path when `--model` is omitted:

`valor_session create --role dev` (no `--model`) → AgentSession stored with `model=None` → worker pops session → executor reads `session.model=None`, falls back to `settings.models.session_default_model` (= `"opus"`) → `get_response_via_harness(..., model="opus")` → argv includes `--model opus`.

### Technical Approach

Two edits in live code, one in settings, one in docs.

**Edit 1 — `agent/sdk_client.py:get_response_via_harness`** (single function, ~20 lines touched)
- Add `model: str | None = None` as a keyword-only parameter.
- After the existing `harness_cmd = list(_HARNESS_COMMANDS["claude-cli"])` block (line ~1637-1638), if `model` is truthy, append `["--model", model]` to `harness_cmd` *before* the `--resume UUID / message` assembly at lines 1658-1662. The argv order is `harness_cmd + [--resume, uuid, message]` or `harness_cmd + [message]`; `--model` must live in `harness_cmd` so it precedes the positional `message`.
- Log the resolved model at INFO level on first-turn path (cache-friendly) so operators can confirm the flag took effect: `logger.info(f"[harness] Using --model {model} for session_id={session_id}")`.

**Edit 2 — `agent/session_executor.py`** (single call site, ~5 lines touched)
- At the harness branch around line 1109, resolve the effective model:
  ```python
  from config.settings import settings  # if not already imported
  _effective_model = getattr(agent_session, "model", None) or settings.models.session_default_model
  ```
- Pass `model=_effective_model` to the `get_response_via_harness(...)` call at line 1177-1189.

**Edit 3 — `config/settings.py`**
- Extend `ModelSettings` (line 169-175) with:
  ```python
  session_default_model: str = Field(
      default="opus",
      description=(
          "Default Claude model for sessions that don't set AgentSession.model. "
          "Covers PM, Teammate, and Dev sessions. Override per-session via "
          "`valor-session create --model <name>`. Env: MODELS__SESSION_DEFAULT_MODEL."
      ),
  )
  ```
- No change to existing `ollama_vision_model` (image analysis, unrelated).

**Edit 4 — `session_completion.py:450`** (single call, ~1 line touched)
- The PM final-delivery runner also calls `get_response_via_harness`. Since it runs in the PM session's context, it should pass the same resolved model. Look up the PM session record once and pass its `model` (or the default).

**Edit 5 — `docs/features/agent-session-model.md`** (docs correction; done in DOCS stage)
- Lines 192-193 describe the wiring as going through `sdk_client.get_agent_response_sdk() → ValorAgent(model=...) → ClaudeAgentOptions`. That path is not the live path anymore. Replace with a description of the harness-CLI path: `session_executor.py` reads `session.model`, falls back to `settings.models.session_default_model` (default `"opus"`), and passes it to `get_response_via_harness` which appends `--model <value>` to the `claude -p` argv.

**What we do NOT touch:**
- `_HARNESS_COMMANDS` — no new entries. The opencode entry stays as-is. No Ollama entry (Layer 2, deferred to #1137).
- `_is_auth_error` call sites — no retry logic. Deferred to #1137.
- `.claude/agents/*.md` — subagent model routing is independent.
- `ValorAgent` / `_create_options` — still wires `model` correctly for its own (now-dormant) code path. We leave that intact rather than delete it, because test paths still construct `ValorAgent` directly (see `tests/unit/test_sdk_client_*`) and ripping it out is a separate cleanup.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced. Existing handlers in `get_response_via_harness` (subprocess error paths, image-dimension sentinel, context-budget) are not touched.
- [ ] If `session.model` lookup raises (unlikely — it's a direct attribute read), the executor falls through to `settings.models.session_default_model`. Covered by a test that forces `getattr` to raise.

### Empty/Invalid Input Handling
- [ ] `session.model = ""` (empty string): `or settings.models.session_default_model` falls through to the default. Test asserts `--model opus` on argv.
- [ ] `session.model = None`: same behavior as empty string — default kicks in.
- [ ] `session.model = "opus"`: passed verbatim. Test asserts `--model opus` on argv.
- [ ] `settings.models.session_default_model = ""` (operator misconfigures to empty): `get_response_via_harness` receives `model=""`, and its truthiness check skips the `--model` injection entirely — argv goes out without `--model` and the CLI uses its own default. Test asserts this behavior (graceful degradation, no crash).
- [ ] Invalid model name (e.g., `"sonic"`): we pass it verbatim; the Claude CLI returns a non-zero exit with an error. No pre-validation. Test asserts the existing error surfacing path propagates the CLI error, no silent swallow.

### Error State Rendering
- [ ] If the Claude CLI rejects `--model <bad>`, the error text reaches the user through the existing `_run_harness_subprocess` → `raw.returncode != 0` path in `session_executor.py`. No new silent-failure surface.
- [ ] Operator-visible log line at INFO confirms the model each turn (aids debugging "why did my --model opus not take effect").

## Test Impact

- [ ] `tests/unit/test_harness_streaming.py` — UPDATE: existing tests construct `get_response_via_harness` call args; add the new `model` kwarg where relevant (or leave as `None` and add a new test case for model-injected argv).
- [ ] `tests/unit/test_harness_retry.py` — UPDATE: same — add `model=None` as default kwarg in existing assertions, add one new case asserting `--model` appears in argv when `model="opus"`.
- [ ] `tests/unit/test_sdk_client_image_sentinel.py` — NO CHANGE: the image-sentinel logic runs on stdout, unrelated to argv assembly.
- [ ] `tests/integration/test_session_spawning.py` — UPDATE: `valor_session create --model opus` end-to-end case should add an assertion that spawns then reads the session back with `model=="opus"` (already exists per PR #909); extend to assert the harness argv actually includes `--model opus` via subprocess mock or log capture.
- [ ] `tests/integration/test_harness_resume.py` — UPDATE: existing resume path tests pass `prior_uuid`; extend one case to also pass `model=` and assert `--model opus` precedes `--resume <uuid>` in argv.
- [ ] `tests/integration/test_harness_no_op_contract.py` — UPDATE only if it asserts the exact argv. Check during build; if it only asserts behavior (output shape), no change.
- [ ] **NEW**: `tests/unit/test_session_model_routing.py` — CREATE: a small, focused test module asserting (a) `session.model` string flows to argv, (b) `None` falls back to `settings.models.session_default_model`, (c) empty-string default is graceful (no `--model` in argv, no crash), (d) `--model` appears before `message` in argv, (e) `--model` appears before `--resume <uuid>` in argv when resuming.

**NOT affected:** any memory, PostToolUse hooks, subagent tests (`.claude/agents/*.md` mechanism), Popoto model tests, task list isolation tests. The change is surgical.

## Rabbit Holes

- **Rewriting `ValorAgent` or deleting it**: tempting because the `model=...` path there is now unreachable in production. Don't. Tests still use `ValorAgent`, and the plumbing is correct — it just lives on a dormant path. Cleanup is a separate issue if we want to pursue it; it is not required to make Layer 1 work.
- **Validating model names against a whitelist**: don't. The Claude CLI already rejects unknown models cleanly. A whitelist in our code would need maintenance every time Anthropic ships a new model.
- **Adding a `--model` override to `_HARNESS_COMMANDS["opencode"]` to match**: don't. Opencode is a non-Claude harness with its own flag syntax. Out of scope.
- **Building a per-stage model dispatch dict in settings** (PLAN→opus, BUILD→sonnet, etc.): the issue's scope update says "Opus for all 3 roles". A per-stage dict contradicts that. If we later want per-stage variation, that's a follow-up. Keep settings to one `session_default_model` for now.
- **Chasing `CLAUDE_MODEL` env var**: it doesn't exist in the repo. The issue is stale on this point. Don't "preserve backward compat" for a var that isn't there.
- **Auto-detecting Ollama as a `--model` value**: that's Layer 2, deferred to #1137.
- **Inventing a new frontmatter field for PM/Teammate model**: no. PM and Teammate always run on the default. If we ever need to vary them, add a setting then — not now.

## Risks

### Risk 1: Opus-by-default significantly increases API cost
**Impact:** Every PM session, Teammate session, and every Dev session that doesn't override will run on Opus. Opus is roughly 5× Sonnet per input token. The existing Stage→Model Dispatch Table (PM persona, line 140-148) assigns Sonnet to BUILD/TEST/PATCH/DOCS specifically because those are tool-heavy — running them on Opus burns budget for no benefit.
**Mitigation:** This is a deliberate product choice from the 2026-04-23 scope update ("Default: Opus for all 3 roles"). The PM persona's existing stage-table dispatches still override per-stage with `--model sonnet`, and those overrides continue to work (PM writes `--model sonnet` on `valor_session create`, it flows to argv). So the cost exposure is bounded to: (a) PM sessions themselves, (b) Teammate sessions, (c) Dev sessions that are spawned without `--model`. If cost becomes a problem, the settings default is a one-line change. Flagged in Open Question #2.

### Risk 2: Existing PM dispatches that rely on `--model sonnet` continue to work correctly
**Impact:** If the settings default is Opus AND the executor ignores `session.model` even when set (regression), PM dispatches would silently run on Opus despite passing `--model sonnet`. This would be invisible without the new INFO log line.
**Mitigation:** Order of precedence is `session.model if set else settings.models.session_default_model`. Test case covers both branches. The INFO log line is a cheap sanity check at runtime.

### Risk 3: Short alias vs. full name mismatch
**Impact:** If Claude Code or the installed CLI version only accepts full names (e.g., `claude-opus-4-7`), passing `opus` breaks every session. Our recon showed the CLI accepts both, but CLI updates could narrow that.
**Mitigation:** The CLI returns a clear error on unknown models with exit code ≠ 0, and the existing `session_executor.py` error path surfaces that to the user. Plus, `.claude/agents/*.md` subagents have been using `opus`/`sonnet`/`haiku` short aliases in production for weeks — if the CLI dropped short-alias support, subagents would already be broken. Low probability.

### Risk 4: The dormant `ValorAgent → ClaudeAgentOptions` wiring becomes confusing over time
**Impact:** Two paths for "how does model get to the CLI" invite drift. A future developer patches one and not the other.
**Mitigation:** Docs correction (Edit 5) is non-negotiable. Add a TODO in `_create_options()` noting that the live path is the harness CLI and this wiring is retained for test fixtures. Consider a follow-up cleanup issue to unify, but not in this plan.

## Race Conditions

No race conditions identified. The read of `session.model` happens inside a single synchronous executor frame before the subprocess launch. The Redis record is populated by `_push_agent_session` at enqueue time and is immutable for the lifetime of a session (no code path writes `.model` after enqueue). The subprocess is fire-once per turn; concurrent turns on the same session are serialized by the per-project worker already.

## No-Gos (Out of Scope)

- **Layer 2 — Ollama credit-exhaust fallback.** Explicitly deferred to **#1137**. No new entry in `_HARNESS_COMMANDS`, no `_is_auth_error` retry, no `harness_override` field, no Ollama bootstrap.
- **Per-stage model routing in settings.** The scope update says Opus for all roles; stage variation stays with the PM persona dispatch table, which is prose, not code.
- **Subagent `.claude/agents/*.md` model routing.** Already works via CLI-native frontmatter. Untouched.
- **Validating/whitelisting model names.** CLI handles it.
- **Deleting the dormant `ValorAgent → _create_options()` model wiring.** Separate cleanup.
- **LiteLLM proxy alternative.** Prior #1106 floated it; the scope update drops it for this issue.
- **A per-session `harness_override` field.** That was Layer 2's mechanism. Not needed for Layer 1.
- **Environment variable `CLAUDE_MODEL` as an override.** Does not exist today and we don't introduce one. If a future operator wants a global override, `MODELS__SESSION_DEFAULT_MODEL` via pydantic-settings is the knob.

## Update System

No update system changes required. The new settings field has a default value (`opus`), so existing machines behave correctly on first boot post-deploy without any env or config edits. If an operator wants to change the default, they add `MODELS__SESSION_DEFAULT_MODEL=sonnet` to `~/Desktop/Valor/.env` — that's a run-of-the-mill secret/env change already handled by `scripts/update/env_sync.py`. No `scripts/remote-update.sh` changes, no `.claude/skills/update/` changes.

## Agent Integration

No agent integration required. This is purely internal — the agent (dev sessions, PM sessions) does not directly invoke session-creation code; the PM does via `python -m tools.valor_session create --model <name>`, which is already wrapped and exposed. `.mcp.json` is untouched. No MCP server gains or loses capabilities. The bridge (`bridge/telegram_bridge.py`) does not need to import anything new.

Integration tests that verify end-to-end agent behavior: `tests/integration/test_session_spawning.py` already covers `valor_session create --model` round-trip to the AgentSession record; we extend it to assert the harness argv actually honors the value.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/agent-session-model.md` — rewrite the "Per-Session Model Selection" section (lines 184-195) to describe the **harness-CLI live path** (executor reads `session.model`, falls back to `settings.models.session_default_model`, passes to `get_response_via_harness`, argv gains `--model <value>`). Remove the claim about `ValorAgent → ClaudeAgentOptions` being the live flow.
- [ ] Add a short note at the top of the same section explaining the default-Opus policy and how to override via `MODELS__SESSION_DEFAULT_MODEL` or per-session `--model`.
- [ ] No change needed in `docs/features/README.md` index table (feature already listed).

### Inline Documentation
- [ ] Docstring on `get_response_via_harness` gains a `model` arg entry.
- [ ] Docstring on `AgentSession.model` field updated to reflect that the value now actually flows to the subprocess.
- [ ] Comment in `session_executor.py` at the call site explaining the precedence (`session.model` > settings default > CLI default).

### External Documentation Site
- Not applicable — this repo does not ship a public docs site.

## Success Criteria

- [ ] `session.model` is passed as `--model <value>` to the `claude -p` subprocess for every session that sets it (verified by subprocess-argv mock in unit tests and by log capture in an integration test).
- [ ] When `session.model` is None/empty, the `settings.models.session_default_model` (default `"opus"`) governs — unit tests cover both branches.
- [ ] `valor-session create --model sonnet --role dev` results in an argv containing `--model sonnet` when that session runs (integration test).
- [ ] `MODELS__SESSION_DEFAULT_MODEL=haiku` env override flips the default for sessions without an explicit model (settings test).
- [ ] No regression in the `--resume UUID` path: a resumed session still passes `--model` correctly, and `--model <value>` appears before `--resume <uuid>` in argv.
- [ ] `docs/features/agent-session-model.md` updated to describe the live path, not the dormant one.
- [ ] INFO-level log line on each harness turn shows the resolved model.
- [ ] PM persona Stage→Model Dispatch Table remains functional (sonnet overrides still take effect when PM writes them) — no regression in existing stage-specific routing.
- [ ] Tests pass (`pytest tests/ -x -q`).
- [ ] Lint / format clean (`python -m ruff format .`).

## Team Orchestration

Small appetite, one builder + one validator.

### Team Members

- **Builder (session-model-routing)**
  - Name: `model-router-builder`
  - Role: Implement Edits 1–4 (sdk_client.py, session_executor.py, settings.py, session_completion.py) and the new test module.
  - Agent Type: builder
  - Resume: true

- **Validator (session-model-routing)**
  - Name: `model-router-validator`
  - Role: Run the full test suite, assert the argv assertions fire, confirm the INFO log line, sanity-check the docs-correction task lands in the DOCS stage plan item (but not in the build itself).
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the `session_default_model` setting
- **Task ID**: build-settings
- **Depends On**: none
- **Validates**: unit tests for `Settings.models.session_default_model` default and env-override behavior
- **Informed By**: Freshness Check (no existing `CLAUDE_MODEL` var to preserve)
- **Assigned To**: `model-router-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `session_default_model: str = Field(default="opus", ...)` to `ModelSettings` in `config/settings.py`.
- Verify `MODELS__SESSION_DEFAULT_MODEL=sonnet` env override works via pydantic-settings nested delimiter (existing pattern on `FEATURES__` — same mechanism).

### 2. Thread `model` through `get_response_via_harness`
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

### 3. Wire executor to pass `session.model` to the harness
- **Task ID**: build-executor-wiring
- **Depends On**: build-settings, build-harness-model
- **Validates**: integration test extension in `tests/integration/test_session_spawning.py` asserting argv contains `--model`
- **Assigned To**: `model-router-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_executor.py` around line 1109-1189: import `settings` if not already imported.
- Resolve `_effective_model = getattr(agent_session, "model", None) or settings.models.session_default_model` before the `async def do_work()` closure.
- Pass `model=_effective_model` into `get_response_via_harness(...)` at line 1177-1189.
- Also thread it into `session_completion.py:450` for the PM final-delivery runner (look up PM session's model via `agent_session` or session lookup).

### 4. Write unit tests for model-routing logic
- **Task ID**: build-unit-tests
- **Depends On**: build-harness-model
- **Assigned To**: `model-router-builder`
- **Agent Type**: test-writer
- **Parallel**: true
- Create `tests/unit/test_session_model_routing.py` covering:
  - `session.model="opus"` → argv includes `--model opus`.
  - `session.model=None` + settings default `"opus"` → argv includes `--model opus`.
  - `session.model=""` + settings default `"opus"` → argv includes `--model opus`.
  - `session.model=None` + settings default `""` → argv has no `--model` (graceful).
  - `--model <value>` precedes `--resume <uuid>` in argv.
  - `--model <value>` precedes positional `message` in argv.
  - INFO log line fires with the resolved value.

### 5. Extend integration tests
- **Task ID**: build-integration-tests
- **Depends On**: build-executor-wiring
- **Assigned To**: `model-router-builder`
- **Agent Type**: test-writer
- **Parallel**: false
- In `tests/integration/test_session_spawning.py`, extend the existing `--model` round-trip test to assert the harness subprocess was invoked with `--model opus` on argv (mock or capture subprocess).
- In `tests/integration/test_harness_resume.py`, add one case where `model="opus"` is passed alongside `prior_uuid` and assert argv order.

### 6. Validate everything
- **Task ID**: validate-all
- **Depends On**: build-settings, build-harness-model, build-executor-wiring, build-unit-tests, build-integration-tests
- **Assigned To**: `model-router-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite (`pytest tests/ -x -q`).
- Run `python -m ruff format . && python -m ruff check .`.
- Manually smoke-test: `python -m tools.valor_session create --role dev --slug test-model-wiring --message "say hello"` with worker running, then check the log line for `[harness] Using --model opus`.
- Verify no changes to `_HARNESS_COMMANDS` (Ollama entry must NOT exist).
- Verify no changes to `_is_auth_error` call sites (no retry logic).
- Verify `ValorAgent._create_options()` model wiring is still intact (we didn't rip it out).
- Report pass/fail.

### 7. Docs correction (deferred to DOCS stage)
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: (documentarian in DOCS stage — not part of BUILD)
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-model.md` lines 184-195 per the Documentation section above.
- Update docstring on `AgentSession.model`.
- Update docstring on `get_response_via_harness`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_session_model_routing.py tests/unit/test_harness_streaming.py tests/unit/test_harness_retry.py -x -q` | exit code 0 |
| Integration tests pass | `pytest tests/integration/test_session_spawning.py tests/integration/test_harness_resume.py -x -q` | exit code 0 |
| All tests pass | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| `--model` appears in harness argv | `grep -n '"--model"' agent/sdk_client.py` | output > 0 |
| Executor reads session.model | `grep -n 'session.model\|agent_session.*model' agent/session_executor.py` | output > 0 |
| No Ollama harness added (Layer 2 guard) | `grep -c '"ollama"' agent/sdk_client.py` | exit code 1 |
| No credit-retry added (Layer 2 guard) | `grep -n '_is_auth_error.*retry\|harness_override' agent/` | exit code 1 |
| Settings default is "opus" | `python -c "from config.settings import settings; assert settings.models.session_default_model == 'opus'"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

These questions need human input before the plan can advance to `/do-plan-critique`. The plan is ready-to-review, but Build should not start until these are resolved.

1. **Settings vs. Python constant for the default model** — the plan places `session_default_model` on `config/settings.py/ModelSettings` so operators can override via `MODELS__SESSION_DEFAULT_MODEL` env var. Alternative: a plain Python constant (e.g., `DEFAULT_SESSION_MODEL = "opus"` in `agent/__init__.py` or a new `agent/model_policy.py`). **Settings is more flexible** (env override without code change, iCloud-synced via `~/Desktop/Valor/.env`), but **a constant is simpler** (one fewer layer, no pydantic-settings plumbing). Prefer settings? Prefer constant? Override precedence we're proposing: `session.model` > `settings.models.session_default_model` > CLI default. Is that right?

2. **Stage→Model table vs. "Opus for all"** — the 2026-04-23 scope update says "Default: Opus for all 3 roles (PM/Teammate always Opus, Dev defaults to Opus but PM can override at spawn via --model)". The existing PM persona at `config/personas/project-manager.md:134-148` still instructs PM to dispatch Sonnet for BUILD/TEST/PATCH/DOCS and Opus only for PLAN/CRITIQUE/REVIEW. After this wiring lands, those Sonnet overrides will actually take effect (today they're ignored). **Do we keep the stage table as-is** (so BUILD runs on Sonnet, saving cost)? **Or simplify it to always Opus** (consistent with the scope update, but ~5× more expensive per tool-heavy stage)? The plan currently assumes the former — PM keeps its stage table, default covers the gap. Confirm.

3. **Dev inheritance when PM omits `--model`** — if the PM forgets to pass `--model` on `valor_session create --role dev`, what should the Dev session run on? The plan assumes **the settings default (Opus)** — simple, explicit, no surprise inheritance. Alternative: **inherit the PM's own model** (which is always Opus anyway, so same result today — but breaks if we ever allow PM to run on something else). Alternative 2: **use the stage→model table as a source of truth inside the worker** (e.g., infer stage from session metadata and pick model accordingly — significantly more complexity). Prefer option 1 (settings default)? Confirm.

4. **Model string format** — short aliases (`opus`, `sonnet`, `haiku`) vs. full names (`claude-opus-4-7`, `claude-sonnet-4-6`). The plan uses short aliases (matching subagent frontmatter in `.claude/agents/*.md` and the Claude CLI's own accepted syntax). **If you want to pin a specific version** (e.g., `claude-opus-4-7` explicitly), say so — we'd then pass that exact string. Mixing is fine; no code change needed either way.

5. **`CLAUDE_MODEL` env var treatment** — it does not exist in the codebase today, despite the issue's implicit claim. We do not introduce one. Operators who want a global override use `MODELS__SESSION_DEFAULT_MODEL` via `.env` (standard pydantic-settings pattern). Confirm we're not on the hook for any `CLAUDE_MODEL` compatibility shim.

6. **Completion-runner (PM final-delivery) model** — `session_completion.py:450` calls `get_response_via_harness` to draft the final delivery message. The plan threads the PM session's `model` through (so if PM runs on Opus, the drafter runs on Opus). **Is that right, or should the drafter always run on a cheaper model** (e.g., force Haiku for this one call since it's a summarization job)? We default to "consistent with the session" — but a forced-Haiku here is a ~10× cost win if the PM is on Opus. Out of scope to decide here? Or worth a one-line flag?

---

