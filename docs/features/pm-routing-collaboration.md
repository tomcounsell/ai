# PM Routing: Collaboration and Other Classifier Buckets

Extends the PM routing system from a binary classification to a four-way classification at both the bridge and intent classifier levels, enabling the PM to handle direct tasks without spawning a full SDLC dev-session.

## Problem

Previously, the bridge classifier only distinguished "sdlc" and "question", and the intent classifier only distinguished "teammate" and "work". Tasks the PM could handle directly (saving to knowledge base, drafting issues, writing docs) were funneled into the full SDLC pipeline, wasting time on work that should take under a minute.

## Design

### Two-Layer Classification

**Bridge classifier** (`bridge/routing.py::classify_work_request()`):
- Four outcomes: `sdlc`, `collaboration`, `other`, `question`
- Uses first-token extraction with exact match (no substring collisions)
- Default: "If in doubt, classify as collaboration"
- Result stored as `classification_type` on the AgentSession

**Intent classifier** (`agent/intent_classifier.py::classify_intent()`):
- Four outcomes: `teammate`, `collaboration`, `other`, `work`
- `is_collaboration` property (no confidence threshold -- any collaboration intent routes)
- `is_other` property (no confidence threshold)
- `is_direct_action` convenience property (True for collaboration or other)
- `is_teammate` retains the 0.90 confidence threshold
- `is_work` returns True only when intent is literally "work"
- Default for unparseable/low-confidence: "work" (fail-safe to dev-session)

### Three-Way PM Dispatch

`agent/sdk_client.py` dispatch logic:

```
if _teammate_mode:
    # Teammate instructions (informational response)
elif _collaboration_mode:
    # Direct-action instructions (handle with available tools)
else:
    # SDLC orchestration (spawn dev-session)
```

**Config-driven PM groups** check the bridge-level classification for `COLLABORATION` or `OTHER`, since they bypass the intent classifier.

**Unconfigured groups** use the intent classifier result (`is_direct_action`, which covers both collaboration and other).

### ClassificationType Enum

`config/enums.py::ClassificationType` has four members:
- `SDLC = "sdlc"`
- `COLLABORATION = "collaboration"`
- `OTHER = "other"`
- `QUESTION = "question"`

## Collaboration Mode Instructions

When a PM session enters collaboration mode, it receives direct-action instructions listing available tools (Bash, GitHub CLI, Google Workspace, memory search, Office CLI, session management) with an explicit fallback: "If you determine this task requires code changes, spawn a dev-session via the Agent tool instead."

## Safety

- Bridge classifier default: "If in doubt, classify as collaboration" (cheaper when wrong -- PM tries directly in seconds vs 60-min dev-session timeout)
- Intent classifier fail-safe: "work" for unparseable/error responses (conservative)
- Double classification (bridge + intent) provides two chances to catch misroutes
- PM guard still prevents PM sessions from entering Teammate mode
- `is_sdlc` property on AgentSession unchanged -- explicit SDLC references (issue/PR numbers) always fast-path to SDLC

## Files Changed

- `config/enums.py` -- COLLABORATION and OTHER enum members
- `bridge/routing.py` -- Four-outcome prompt, collaboration default, first-token exact match parsing
- `agent/intent_classifier.py` -- Four-outcome prompt (teammate/collaboration/other/work), `is_collaboration`, `is_other`, `is_direct_action` properties, narrowed `is_work`
- `agent/sdk_client.py` -- `_collaboration_mode` flag, three-way dispatch, `is_direct_action` check
- `config/personas/project-manager.md` -- Available Tools section

## Related

- [SDLC-First Routing](sdlc-first-routing.md) -- Original two-way bridge classifier
- [PM session Teammate Mode](pm-teammate-mode.md) -- Intent classifier for Teammate routing
- [Config-Driven Chat Mode](config-driven-chat-mode.md) -- Persona-based classifier bypass
- [PM Channels](pm-channels.md) -- PM channel routing
