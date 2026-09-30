---
status: Planning
type: feature
appetite: Medium
owner: valorengels
created: 2026-04-11
tracking: https://github.com/tomcounsell/ai/issues/786
plan_url: https://github.com/tomcounsell/ai/blob/main/docs/plans/pm-session-child-fanout.md
last_comment_id: IC_kwDOEYGa0876XmEv
---

# PM Session Child Fan-out for Multi-Issue SDLC Prompts

## Problem

When Valor sends a message like "Run SDLC on issues 777, 775, 776", the bridge creates one PM `AgentSession` with the full message as `message_text`. That session attempts to handle all three issues inside a single agent run — growing context unboundedly, with no isolation between issues.

**Current behavior:** One PM session handles N issues serially in one turn. A failure on issue 775 pollutes the context and may corrupt state for 776. The dashboard shows one session instead of three trackable units.

**Desired outcome:** The parent PM session detects the multi-issue prompt, spawns one child PM session per issue via `valor_session create --role pm`, then pauses itself (`waiting_for_children`). Each child runs its own isolated SDLC pipeline. When all children complete, the parent auto-transitions to `completed` via the existing `_finalize_parent_sync()` hook.

## Freshness Check

**Baseline commit:** `f35ee9a2`
**Issue filed at:** 2026-04-07T06:07:14Z
**Disposition:** Minor drift — several PRs landed after the issue was filed that affect the implementation approach.

**File:line references re-verified:**
- `models/agent_session.py:219` — `parent_agent_session_id` KeyField (indexed) — still holds at line 219
- `models/agent_session.py:994` — `create_child()` factory — shifted to `create_local()` at 967; `create_child()` now at line 993. Still present and functional.
- `models/agent_session.py:1104` — `get_child_sessions()` — confirmed at line 1104
- `models/session_lifecycle.py:518` — `_finalize_parent_sync()` — confirmed at line 518
- `models/session_lifecycle.py:70` — `waiting_for_children` in non-terminal list — confirmed at line 70
- `tools/valor_session.py:129` — `create` subcommand with `--role` and `--parent` — confirmed at line 129

**Cited sibling issues/PRs re-checked:**
- PR #821 — merged 2026-04-07: child session parent linkage via `VALOR_PARENT_SESSION_ID` — merged, working
- PR #902 — OPEN (not yet merged): PM persona dispatch update to `valor_session create --role dev`

**Commits on main since issue was filed (touching referenced files):**
- `36a18ecf` Phase 3+4: PM persona updated in `sdk_client.py` to dispatch dev sessions via `python -m tools.valor_session create --role dev --parent "$AGENT_SESSION_ID"` — **directly shapes this plan's approach**; fan-out for PM children follows the same pattern with `--role pm`
- `1458d493` Phase 5: Deleted `session_registry.py`, simplified hooks — irrelevant to fan-out
- `f35ee9a2` Fix tests for Phase 4+5 — irrelevant

**Active plans in `docs/plans/` overlapping this area:**
- `pm-autonomous-skills.md` — PR #902 in review; this plan extends the dispatch pattern it establishes (dev → pm child). No conflict; sequentially dependent.

**Notes:** PR #902 is a hard prerequisite — the PM persona dispatch pattern it establishes is what this plan extends. The plan assumes #902 merges before build starts.

## Prior Art

- **PR #390** — Add parent-child job hierarchy for job decomposition — established the `parent_agent_session_id` field and `create_child()` factory. Foundational; this plan builds on it.
- **PR #821** — fix: child session parent linkage via `VALOR_PARENT_SESSION_ID` env var — closed the parent-link gap where children were created unlinked. Required prereq for fan-out.
- **Issue #491** / **PR #496** — ChatSession steering: parent sends steering messages to child DevSessions — established the steering pattern PM sessions use to receive child completion notifications.
- **Issue #808** — bug: child sessions spawned by PM have null `parent_agent_session_id` — fixed by PR #821.

No prior attempt at multi-issue fan-out logic exists. This is the first implementation.

## Data Flow

**When "Run SDLC on issues 777, 775, 776" arrives:**

1. **Bridge** — receives Telegram message, creates one PM `AgentSession` with `message_text = "Run SDLC on issues 777, 775, 776"`, enqueues to Redis.
2. **Worker** — dequeues PM session, injects `AGENT_SESSION_ID` env var, starts Claude API call via `sdk_client.py`.
3. **PM agent** — receives the enriched prompt (injected by `sdk_client.py`). Detects multiple issue numbers in the message text.
4. **Fan-out** (new behavior) — PM calls:
   ```bash
   python -m tools.valor_session create \
     --role pm \
     --parent "$AGENT_SESSION_ID" \
     --message "Run SDLC on issue 777"

   python -m tools.valor_session create \
     --role pm \
     --parent "$AGENT_SESSION_ID" \
     --message "Run SDLC on issue 775"

   python -m tools.valor_session create \
     --role pm \
     --parent "$AGENT_SESSION_ID" \
     --message "Run SDLC on issue 776"
   ```
   Each call enqueues a child PM session with `parent_agent_session_id` pointing to the parent.
5. **Parent status transition** — PM calls `python -m tools.valor_session wait-for-children --session-id "$AGENT_SESSION_ID"` (new subcommand) to transition itself to `waiting_for_children`.
6. **Child PM sessions** — Worker picks up each child PM session one at a time (project-keyed serialization). Each child runs its own single-issue SDLC pipeline.
7. **Auto-completion** — When each child finalizes, `_finalize_parent_sync()` is called automatically. When all children are terminal, the parent transitions to `completed`.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (after plan finalize)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #902 merged | `gh pr view 902 --json mergedAt -q .mergedAt` | PM persona dispatch pattern this plan extends |
| `valor_session create --role pm` works | `python -m tools.valor_session create --help \| grep 'pm'` | Child PM session spawning |

Run all checks: `python scripts/check_prerequisites.py docs/plans/pm-session-child-fanout.md`

## Solution

### Key Elements

- **Multi-issue parser** — Extracts `[777, 775, 776]` from the message text. Pure regex, no LLM needed. Handles: "issues 777, 775, 776", "issues 777 775 776", "#777 #775 #776", "777,775,776".
- **`wait-for-children` subcommand** in `tools/valor_session.py` — transitions the calling session to `waiting_for_children` status. Takes `--session-id` (defaults to `$AGENT_SESSION_ID`). The PM calls this after spawning all children.
- **PM persona fan-out instruction** — Added to the SDLC orchestration block in `sdk_client.py` (and `config/personas/project-manager.md`): when message contains multiple issue numbers, spawn one child PM session per issue, then call `valor_session wait-for-children`.
- **Sequential scheduling** — children run one at a time via existing project-keyed serialization (PR #831). No additional scheduling logic needed.

### Flow

**Multi-issue message arrives** → PM session detects N>1 issues → spawns N child PM sessions via bash loop → calls `valor_session wait-for-children` → pauses self → children run sequentially → `_finalize_parent_sync()` fires on each completion → when all children terminal, parent transitions to `completed`.

### Technical Approach

1. **Add `wait-for-children` subcommand to `tools/valor_session.py`:**
   - Accepts `--session-id <ID>` (defaults to `$AGENT_SESSION_ID` env var)
   - Calls `transition_status(session, "waiting_for_children")` from `models.session_lifecycle`
   - Exits 0 on success; exits 1 if session not found or already terminal

2. **Extend PM persona instructions in `agent/sdk_client.py`:**
   - In the SDLC orchestration block (currently around line 1955), add a detection block:
     > "If the message contains multiple issue numbers (e.g., 'Run SDLC on issues 777, 775, 776'), fan out: spawn one child PM session per issue via `valor_session create --role pm --parent "$AGENT_SESSION_ID" --message "Run SDLC on issue N"`, then call `python -m tools.valor_session wait-for-children --session-id "$AGENT_SESSION_ID"` to pause yourself. Do NOT handle multiple issues in a single session."
   - Mirror this instruction in `config/personas/project-manager.md`

3. **No parser utility needed** — the PM agent is LLM-powered and can extract issue numbers from any natural language phrasing. The instruction tells it what to do when it detects multiple issues; the LLM handles the extraction.

4. **Sequential execution guaranteed** — project-keyed serialization (PR #831/`agent/session_queue.py`) already ensures child sessions in the same project queue one at a time. No explicit `scheduled_at` staggering needed.

5. **Parent completion** — `_finalize_parent_sync()` in `models/session_lifecycle.py:518` already handles this. No changes to lifecycle needed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `transition_status()` call in `wait-for-children` subcommand: if session not found, print error to stderr and exit 1. No silent swallow.
- [ ] If `valor_session create` fails mid-fan-out (e.g., Redis down), the PM session will not have transitioned to `waiting_for_children` and will remain active — worker will eventually timeout. This is acceptable; the existing health-check loop handles stale sessions.
- [ ] `_finalize_parent_sync()` already has try/except logging at lines 540-548; no new exception paths introduced.

### Empty/Invalid Input Handling
- [ ] `wait-for-children` with no `--session-id` and no `$AGENT_SESSION_ID` env var: exit 1 with "No session ID provided" message.
- [ ] `wait-for-children` when session is already terminal: exit 1 with "Session already in terminal status" — do not attempt to re-transition.
- [ ] `wait-for-children` when session has no children yet (PM calls it before spawning): allowed — the PM is expected to spawn first, then call this. The subcommand does not validate child count.

### Error State Rendering
- [ ] If fan-out creates 2 of 3 children and then fails: parent is not in `waiting_for_children` yet (fan-out happened but `wait-for-children` was not called). The PM session will be marked failed by the worker. Acceptable degradation.
- [ ] PM sends a Telegram update before and after fan-out so Valor has visibility.

## Test Impact

- [ ] `tests/unit/test_agent_session_hierarchy.py` — UPDATE: add test cases for `wait-for-children` subcommand via CLI invocation (mock `transition_status`); no existing tests break.
- [ ] `tests/unit/test_pm_session_factory.py` — UPDATE: add test for PM persona instruction block containing fan-out guidance; check string presence in enriched prompt.
- [ ] No existing hierarchy tests break — all new behavior is additive. The `_finalize_parent_sync()` tests remain unchanged.

## Rabbit Holes

- **Parsing multi-issue syntax in Python** — The PM is an LLM; it can extract issue numbers from any phrasing without a regex parser. Do not build a structured parser.
- **`scheduled_at` staggering** — Project-keyed serialization already queues children one at a time. Do not add explicit `scheduled_at` delays.
- **Dashboard hierarchy view** — Dashboard already shows parent-child relationships (PR #664). Do not extend dashboard as part of this work.
- **Broadcasting child progress to parent** — The PM session is paused in `waiting_for_children`. It does not need incremental progress updates from children. Only the final completion matters.
- **Child-to-child communication** — Children are fully independent. No inter-child steering needed.

## Risks

### Risk 1: PR #902 not merged before build starts
**Impact:** The `valor_session create --role dev` pattern this plan extends may not be in the PM persona yet, causing inconsistency in the instruction block.
**Mitigation:** Plan lists PR #902 as a hard prerequisite. Build session should check `gh pr view 902 --json mergedAt` before proceeding.

### Risk 2: PM agent ignores fan-out instruction for ambiguous messages
**Impact:** A message like "work on issue 777 and also 775" may not trigger fan-out if the LLM doesn't recognize it as multi-issue.
**Mitigation:** The instruction block should include varied phrasing examples. The LLM is resilient to paraphrase — if it detects 2+ issue numbers, it should fan out. Worst case: PM handles them serially in one session (current behavior, not worse than today).

### Risk 3: `wait-for-children` called before children are registered
**Impact:** `_finalize_parent_sync()` may fire immediately if there are no children yet (edge case: children enqueued but not yet saved to Redis when finalize runs).
**Mitigation:** `enqueue_agent_session()` is synchronous (Redis write completes before returning). The PM calls `valor_session create` (which calls `enqueue_agent_session()`) before calling `wait-for-children`. No race here.

## Race Conditions

### Race 1: Child completes before parent calls `wait-for-children`
**Location:** `models/session_lifecycle.py:571-573`
**Trigger:** A fast-completing child (e.g., issue already done) finalizes between the last `valor_session create` call and the `valor_session wait-for-children` call.
**Data prerequisite:** All children must be registered in Redis before `_finalize_parent_sync()` fires.
**State prerequisite:** Parent must be in `waiting_for_children` for finalization to trigger.
**Mitigation:** `_finalize_parent_sync()` at line 571-573 handles this: if parent isn't in `waiting_for_children` yet when a child finalizes, it sets the parent to `waiting_for_children` itself. Then it checks all children's statuses. So even if the child finishes before the parent self-transitions, the lifecycle code handles it correctly.

## No-Gos (Out of Scope)

- Handling "Run SDLC on all open issues" — unbounded fan-out is dangerous; require explicit issue numbers.
- Cancelling in-flight children when parent is killed — existing kill logic handles this separately.
- Dashboard changes — parent-child hierarchy already visible.
- Fan-out for non-SDLC multi-issue requests (e.g., "check status of 777, 775") — those don't need child PM sessions.
- Concurrency between children — sequential via project-keyed queue; no parallel child execution.

## Update System

No update system changes required. This feature adds a new CLI subcommand to an existing tool (`tools/valor_session.py`) and extends persona instructions in `agent/sdk_client.py`. Both files are already deployed via `git pull` in the standard update flow. No new dependencies, config files, or migration steps.

## Agent Integration

No MCP server changes needed. The PM agent accesses `valor_session` via Bash (the existing pattern for dev session dispatch per PR #902). The new `wait-for-children` subcommand is a plain CLI command callable from Bash.

The `sdk_client.py` instruction update IS the agent integration — it tells the PM agent when and how to use the new subcommand.

Integration test: after build, a PM session receiving "Run SDLC on issues 777, 775" should produce two child PM sessions in Redis with `parent_agent_session_id` set, and the parent should be in `waiting_for_children`.

## Documentation

- [ ] Create `docs/features/pm-session-child-fanout.md` describing the fan-out pattern, trigger conditions, and parent-child lifecycle
- [ ] Add entry to `docs/features/README.md` index table under "Session Management"
- [ ] Update `docs/features/pm-dev-session-architecture.md` to note that PM sessions can spawn child PM sessions (not just dev sessions)

## Success Criteria

- [ ] "Run SDLC on issues 777, 775, 776" results in 3 child PM sessions, each with `parent_agent_session_id` set to the parent's ID
- [ ] Parent session transitions to `waiting_for_children` after spawning children
- [ ] Children run sequentially (not concurrently) via project-keyed serialization
- [ ] When all children complete, parent auto-transitions to `completed` via `_finalize_parent_sync()`
- [ ] `python -m tools.valor_session wait-for-children --session-id <ID>` transitions session to `waiting_for_children`
- [ ] `python -m tools.valor_session wait-for-children` (no args) reads `$AGENT_SESSION_ID` from env
- [ ] Unit test: `wait-for-children` subcommand calls `transition_status` with `"waiting_for_children"`
- [ ] Unit test: `wait-for-children` with missing session exits 1
- [ ] Unit test: PM persona enriched prompt contains fan-out instruction text
- [ ] Tests pass (`pytest tests/unit/ -q`)
- [ ] Ruff clean (`python -m ruff check . && python -m ruff format --check .`)
- [ ] Documentation created at `docs/features/pm-session-child-fanout.md`

## Team Orchestration

### Team Members

- **Builder (valor-session-cli)**
  - Name: cli-builder
  - Role: Add `wait-for-children` subcommand to `tools/valor_session.py`
  - Agent Type: builder
  - Resume: true

- **Builder (pm-persona)**
  - Name: persona-builder
  - Role: Add fan-out instruction block to `agent/sdk_client.py` and `config/personas/project-manager.md`
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: test-writer
  - Role: Write unit tests for `wait-for-children` subcommand and PM persona instruction presence
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Create feature docs and update index
  - Agent Type: documentarian
  - Resume: true

- **Final Validator**
  - Name: final-validator
  - Role: Run full test suite, lint, verify criteria
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

#### 1. Add `wait-for-children` subcommand
- **Task ID**: build-wait-subcommand
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_hierarchy.py`
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `cmd_wait_for_children(args)` function to `tools/valor_session.py`
- Accept `--session-id` flag, default to `os.environ.get("AGENT_SESSION_ID")`
- Import `transition_status` from `models.session_lifecycle`
- Exit 1 if no session ID or session not found; exit 0 on success
- Register subcommand as `wait-for-children` in the argparse subparsers block
- Add test: `test_wait_for_children_transitions_status` in `tests/unit/test_agent_session_hierarchy.py`
- Add test: `test_wait_for_children_missing_session_exits_1`
- Add test: `test_wait_for_children_uses_env_var`

#### 2. Add fan-out instruction to PM persona
- **Task ID**: build-persona-fanout
- **Depends On**: none
- **Validates**: `tests/unit/test_pm_session_factory.py`
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py` SDLC orchestration block (around line 1955), prepend a fan-out detection paragraph:
  > "MULTI-ISSUE FAN-OUT: If the message contains more than one GitHub issue number (e.g., 'Run SDLC on issues 777, 775, 776'), you MUST fan out. For each issue number N: run `python -m tools.valor_session create --role pm --parent "$AGENT_SESSION_ID" --message "Run SDLC on issue N"`. After spawning all children, run `python -m tools.valor_session wait-for-children --session-id "$AGENT_SESSION_ID"` to pause this session. Do NOT process multiple issues in a single session."
- Mirror the instruction in `config/personas/project-manager.md` under a new `## Multi-Issue Fan-out` section
- Add test: `test_pm_persona_contains_fanout_instruction` in `tests/unit/test_pm_session_factory.py` — asserts the string "MULTI-ISSUE FAN-OUT" appears in the enriched PM prompt

#### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-wait-subcommand, build-persona-fanout
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-session-child-fanout.md`
- Add entry to `docs/features/README.md` index
- Update `docs/features/pm-dev-session-architecture.md` to note PM→PM child spawning

#### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-wait-subcommand, build-persona-fanout, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -q` — must pass
- Run `python -m ruff check . && python -m ruff format --check .` — must be clean
- Verify `docs/features/pm-session-child-fanout.md` exists
- Verify `python -m tools.valor_session wait-for-children --help` works
- Report pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| wait-for-children subcommand exists | `python -m tools.valor_session wait-for-children --help` | exit code 0 |
| PM persona has fanout instruction | `grep -r "MULTI-ISSUE FAN-OUT" agent/sdk_client.py config/personas/project-manager.md` | output > 0 |
| Feature doc exists | `ls docs/features/pm-session-child-fanout.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **PR #902 dependency**: Should build be blocked until PR #902 merges, or can we build in parallel and merge after? The fan-out instruction in `sdk_client.py` can be written independently of #902's changes, but the two touch the same block of text. Recommend: build after #902 merges to avoid merge conflicts.

2. **Notification on fan-out**: Should the parent PM session send a Telegram message to Valor before pausing (e.g., "Spawning 3 child sessions for issues 777, 775, 776...")? This would improve visibility. Or is the dashboard sufficient?
