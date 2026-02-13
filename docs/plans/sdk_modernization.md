---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-02-13
tracking: https://github.com/tomcounsell/ai/issues/94
---

# SDK Modernization: Upgrade to Best Practices

## Problem

The Valor bridge's Claude Agent SDK integration was written for v0.1.27 and uses patterns from 6+ months ago. The SDK has since added programmatic agent definitions, expanded hook types, cost budgeting, proper type annotations, and streamlined APIs. Our code works but uses outdated patterns:

**Current behavior:**
- File-based agent definitions (`.claude/agents/*.md`) — invisible to the SDK client, only loaded by Claude Code CLI
- Single `PostToolUse` hook when 6 hook events are available in Python
- Hook function uses `Any` types instead of proper `PostToolUseHookInput`
- Manual cost tracking via `_COST_WARN_THRESHOLD` when SDK has `max_budget_usd`
- No programmatic agent definitions — can't pass agent teams to SDK sessions
- No `SubagentStop` hook to track subagent lifecycle
- No `Stop` hook for cleanup when sessions end
- No `PreCompact` hook to preserve context before compaction

**Desired outcome:**
- SDK pinned to v0.1.35 with all bugfixes (ARG_MAX fix, AssistantMessage.error fix)
- Programmatic `AgentDefinition` instances passed via `agents={}` for core agent types (builder, validator, code-reviewer)
- Expanded hook registrations covering all 6 Python-supported hook events
- Properly typed hook callbacks
- `max_budget_usd` for hard cost limits alongside existing soft warnings
- Codebase prepared for multi-agent teams with inter-agent message passing via steering queue

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on which agents to define programmatically)
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses only existing dependencies and the publicly available SDK.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| SDK installable | `pip install claude-agent-sdk==0.1.35` | Verify target version is available |
| Current tests pass | `pytest tests/ -x -q` | Baseline before refactoring |

## Solution

### Key Elements

- **SDK version bump**: Pin `claude-agent-sdk==0.1.35` in `pyproject.toml`
- **Programmatic agent registry**: New module `agent/agent_definitions.py` that exports `AgentDefinition` instances for core agent types, loaded from the existing `.claude/agents/*.md` files and converted to SDK objects
- **Hook expansion**: Register `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`, `PreCompact` hooks in `_create_options()`
- **Type safety**: Replace `Any` annotations in hook signatures with proper SDK types
- **Cost budgeting**: Add `max_budget_usd` to `ClaudeAgentOptions` alongside existing `_COST_WARN_THRESHOLD` logging
- **Import cleanup**: Import new types (`AgentDefinition`, `HookContext`, hook input types)

### Flow

**SDK Client initialization** → Load agent definitions from registry → Build expanded hooks dict → Create `ClaudeAgentOptions` with `agents={}` and full hooks → **Session runs** with programmatic agents and comprehensive hooks → `SubagentStop` hook tracks agent completion → `Stop` hook ensures cleanup → **Session ends**

### Technical Approach

- **Agent definitions**: Create `agent/agent_definitions.py` with a `get_agent_definitions()` function that returns a `dict[str, AgentDefinition]`. Start with the 3 most-used agents: `builder`, `validator`, `code-reviewer`. These mirror the `.claude/agents/*.md` files but are passed programmatically via the SDK's `agents` parameter. The markdown files remain as fallback for direct Claude Code CLI usage.

- **Hook expansion**: Refactor `agent/sdk_client.py` to register hooks for all 6 Python-supported events. Extract each hook into its own function in `agent/hooks/` (new package):
  - `agent/hooks/pre_tool_use.py` — security validation (block writes to protected paths)
  - `agent/hooks/post_tool_use.py` — existing watchdog (moved from `health_check.py`)
  - `agent/hooks/stop.py` — session cleanup, unregister client, log final metrics
  - `agent/hooks/subagent_stop.py` — log subagent completion, track agent team progress
  - `agent/hooks/pre_compact.py` — log compaction events, preserve key context

- **Type annotations**: Import and use `PostToolUseHookInput`, `PreToolUseHookInput`, `StopHookInput`, `SubagentStopHookInput`, `PreCompactHookInput`, `HookContext` from the SDK.

- **Cost control**: Add `max_budget_usd` parameter to `ValorAgent.__init__()`, defaulting to `float(os.getenv("SDK_MAX_BUDGET_USD", "5.00"))`. Pass through to `ClaudeAgentOptions`.

- **Steering for agent teams**: Extend the existing `agent/steering.py` Redis queue to support addressing messages to specific subagents by adding an optional `target_agent` field. This prepares the infrastructure for inter-agent communication without adding the feature itself.

## Rabbit Holes

- **Migrating ALL 30+ agent definitions to programmatic**: Only migrate the 3 most-used agents. The rest stay as `.claude/agents/*.md` files and still work via Claude Code's filesystem loading. Attempting to convert all 30+ would be a massive scope expansion with no immediate benefit.
- **Implementing `can_use_tool` callback**: The hook-based approach is sufficient. A full permission callback system is a separate feature.
- **Adding `PostToolUseFailure` hook**: This is TypeScript-only in the Python SDK per the docs. The changelog mentioned it for v0.1.26 but the official docs table marks it as TypeScript-only. Skip.
- **Structured output (`output_format`)**: Interesting but changes the response contract. Separate feature.
- **`fork_session` for parallel exploration**: Requires architectural changes to session management. Separate feature.
- **Custom MCP tools via `@tool` decorator**: Our tools work fine via external MCP servers. Converting them is a separate modernization effort.

## Risks

### Risk 1: Hook expansion breaks existing watchdog behavior
**Impact:** Steering injection, health checks, or session tracking could silently fail if hook registration format changes between SDK versions.
**Mitigation:** The watchdog hook has integration tests. Run them before and after. Keep the existing `health_check.py` module intact and import from it — don't rewrite the core logic, just move the entry point.

### Risk 2: Programmatic agent definitions conflict with filesystem definitions
**Impact:** If both programmatic and filesystem agents exist with the same name, the SDK docs state "programmatically defined agents take precedence." This is the desired behavior, but could confuse developers who edit the `.md` files expecting changes to take effect.
**Mitigation:** Add a comment in each `.md` file noting that the programmatic definition takes precedence for SDK sessions. Document this in the feature doc.

### Risk 3: `max_budget_usd` cuts off sessions prematurely
**Impact:** Hard budget limit could kill a session mid-task, leaving work incomplete.
**Mitigation:** Set a generous default ($5.00) well above typical query costs ($0.10-$0.50). Make it configurable via env var. The existing soft warning threshold continues to operate independently.

## No-Gos (Out of Scope)

- No new agent types or capabilities
- No changes to Telegram bridge message handling
- No changes to job queue or worker architecture
- No new MCP servers or tools
- No changes to session ID format or session model
- No changes to worktree management
- No migration of existing `.claude/agents/*.md` files beyond the 3 core types
- No `query()` function adoption (we need `ClaudeSDKClient` for multi-turn)

## Update System

The update script (`scripts/remote-update.sh`) runs `pip install -e .` which will pick up the new SDK version from `pyproject.toml`. No update system changes required — the version bump propagates automatically.

## Agent Integration

No agent integration changes required. This refactoring affects how the bridge spawns and configures SDK sessions internally. The agent's available tools, MCP servers, and Telegram interface are unchanged. The programmatic agent definitions replicate what's already in the filesystem — they don't add new capabilities.

## Documentation

- [ ] Create `docs/features/sdk-modernization.md` describing the new patterns (programmatic agents, expanded hooks, cost budgeting)
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add inline comments in `agent/agent_definitions.py` explaining the registry pattern
- [ ] Add note to each migrated `.claude/agents/*.md` file about programmatic precedence

## Success Criteria

- [ ] `claude-agent-sdk==0.1.35` installed and pinned in `pyproject.toml`
- [ ] `agent/agent_definitions.py` exports `get_agent_definitions()` returning builder, validator, code-reviewer as `AgentDefinition` instances
- [ ] `ValorAgent._create_options()` passes `agents=get_agent_definitions()` to `ClaudeAgentOptions`
- [ ] Hooks registered for all 6 Python-supported events: `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`, `SubagentStop`, `PreCompact`
- [ ] All hook callbacks use proper SDK type annotations (not `Any`)
- [ ] `max_budget_usd` configurable via `SDK_MAX_BUDGET_USD` env var
- [ ] Existing tests pass (`pytest tests/ -x`)
- [ ] Bridge starts and processes a test message successfully
- [ ] `steering.py` supports optional `target_agent` field in message dict
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (sdk-upgrade)**
  - Name: sdk-upgrader
  - Role: Implement all SDK modernization changes
  - Agent Type: builder
  - Resume: true

- **Validator (sdk-upgrade)**
  - Name: sdk-validator
  - Role: Verify all changes work correctly and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Bump SDK version and verify compatibility
- **Task ID**: build-version-bump
- **Depends On**: none
- **Assigned To**: sdk-upgrader
- **Agent Type**: builder
- **Parallel**: false
- Update `pyproject.toml`: change `claude-agent-sdk==0.1.27` to `claude-agent-sdk==0.1.35`
- Run `pip install -e .` to install new version
- Run `pytest tests/ -x -q` to verify existing tests pass
- Verify `python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"` shows 0.1.35

### 2. Create agent definitions registry
- **Task ID**: build-agent-registry
- **Depends On**: build-version-bump
- **Assigned To**: sdk-upgrader
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/agent_definitions.py` with `get_agent_definitions() -> dict[str, AgentDefinition]`
- Define `builder` agent: description from `.claude/agents/builder.md`, prompt from the markdown body, tools include all write/execute tools, model inherits
- Define `validator` agent: description from `.claude/agents/validator.md`, prompt from markdown body, tools are read-only (Read, Grep, Glob, Bash), model is "sonnet"
- Define `code-reviewer` agent: description from `.claude/agents/code-reviewer.md`, prompt from markdown body, tools are read-only, model is "sonnet"
- Import `AgentDefinition` from `claude_agent_sdk`
- Add a brief note to each `.claude/agents/{builder,validator,code-reviewer}.md` that the programmatic definition in `agent/agent_definitions.py` takes precedence for SDK sessions

### 3. Create hooks package and expand hook registrations
- **Task ID**: build-hooks
- **Depends On**: build-version-bump
- **Assigned To**: sdk-upgrader
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/hooks/__init__.py` that exports `build_hooks_config() -> dict`
- Create `agent/hooks/pre_tool_use.py` with a `pre_tool_use_hook` that validates tool calls (block writes to `.env`, `credentials.json`, etc.)
- Move watchdog hook registration from `sdk_client.py` into `agent/hooks/post_tool_use.py` (import from existing `health_check.py`, don't rewrite)
- Create `agent/hooks/stop.py` with a `stop_hook` that logs session completion metrics
- Create `agent/hooks/subagent_stop.py` with a `subagent_stop_hook` that logs subagent completion with agent_type info
- Create `agent/hooks/pre_compact.py` with a `pre_compact_hook` that logs compaction events
- All hooks use proper SDK type annotations: `PostToolUseHookInput`, `PreToolUseHookInput`, `StopHookInput`, `SubagentStopHookInput`, `PreCompactHookInput`, `HookContext`
- `build_hooks_config()` returns the full hooks dict for `ClaudeAgentOptions`

### 4. Refactor ValorAgent to use new patterns
- **Task ID**: build-sdk-client
- **Depends On**: build-agent-registry, build-hooks
- **Assigned To**: sdk-upgrader
- **Agent Type**: builder
- **Parallel**: false
- Update `agent/sdk_client.py` imports: add `AgentDefinition`, `HookContext`, remove redundant imports
- Update `_create_options()` to call `get_agent_definitions()` and pass as `agents=` parameter
- Update `_create_options()` to call `build_hooks_config()` instead of inline hook dict
- Add `max_budget_usd` parameter to `ValorAgent.__init__()` with env var default
- Pass `max_budget_usd` through to `ClaudeAgentOptions`
- Update `watchdog_hook` signature in `health_check.py` to use `HookContext` type instead of `Any`
- Verify the existing error retry loop, cost logging, and client registry still work

### 5. Extend steering for agent-addressable messages
- **Task ID**: build-steering
- **Depends On**: none
- **Assigned To**: sdk-upgrader
- **Agent Type**: builder
- **Parallel**: true
- Add optional `target_agent: str | None = None` parameter to `push_steering_message()`
- Include `target_agent` in the Redis message dict when provided
- Update `pop_all_steering_messages()` return type to include `target_agent` field
- Update `_handle_steering()` in `health_check.py` to pass through `target_agent` in combined message (no filtering yet — just preservation)
- This is preparation only — no consumer filters by `target_agent` yet

### 6. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-sdk-client, build-steering
- **Assigned To**: sdk-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -v` — all tests pass
- Run `ruff check .` — no lint errors
- Run `black --check .` — formatting correct
- Verify `python -c "from agent.sdk_client import ValorAgent; print('OK')"` succeeds
- Verify `python -c "from agent.agent_definitions import get_agent_definitions; d = get_agent_definitions(); print(list(d.keys()))"` prints `['builder', 'validator', 'code-reviewer']`
- Verify `python -c "from agent.hooks import build_hooks_config; h = build_hooks_config(); print(list(h.keys()))"` prints all 5 hook event names (note: UserPromptSubmit may not need a custom hook — verify at least PreToolUse, PostToolUse, Stop, SubagentStop, PreCompact)
- Check that `agent/sdk_client.py` has no remaining `Any` type annotations for hook-related code
- Check that `max_budget_usd` appears in `ClaudeAgentOptions` construction

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: sdk-upgrader
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/sdk-modernization.md` covering: version upgrade rationale, programmatic agent registry pattern, hook expansion, cost budgeting, steering target_agent field
- Add entry to `docs/features/README.md` index table
- Commit all changes with descriptive message
- Push to remote

## Validation Commands

- `pip show claude-agent-sdk | grep Version` — should show 0.1.35
- `pytest tests/ -x -q` — all tests pass
- `ruff check .` — no lint errors
- `black --check .` — formatting correct
- `python -c "from agent.agent_definitions import get_agent_definitions; print(len(get_agent_definitions()))"` — should print 3
- `python -c "from agent.hooks import build_hooks_config; print(sorted(build_hooks_config().keys()))"` — should list hook events
