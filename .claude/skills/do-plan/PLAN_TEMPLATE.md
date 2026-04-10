---
status: Planning
type: [bug | feature | chore]  # May be pre-populated from auto-classification
appetite: [Small | Medium | Large]
owner: [Name]
created: [YYYY-MM-DD]
tracking: [GitHub Issue URL - added automatically]
last_comment_id: [Latest issue comment ID incorporated into this plan - updated automatically]
---

# [Feature Name]

## Problem

[Real scenario showing the pain. User perspective. Specific, not vague.]

**Current behavior:**
[What happens now that's broken/painful]

**Desired outcome:**
[What success looks like]

## Freshness Check

<!-- Phase 0.5 of /do-plan re-verifies the issue's claims against the current state of main
     before planning. This section records what was re-verified and what (if anything) drifted.
     Skip only if the issue was filed within the last hour AND no commits have landed on main since. -->

**Baseline commit:** [SHA of `git rev-parse HEAD` at plan time]
**Issue filed at:** [createdAt from `gh issue view N --json createdAt`]
**Disposition:** [Unchanged | Minor drift | Major drift | Overlap]

**File:line references re-verified:**
- `path/to/file.py:NNN` — [what the issue claimed] — [still holds / drifted to line MMM / gone]

**Cited sibling issues/PRs re-checked:**
- #NNN — [still open / closed at DATE with resolution: ...]

**Commits on main since issue was filed (touching referenced files):**
- `abc1234` [commit title] — [irrelevant / partially addresses / changed root cause / already fixes]

**Active plans in `docs/plans/` overlapping this area:** [none | list plan slugs and overlap description]

**Notes:** [Any drift that didn't change the plan's premise; any corrected line numbers worth noting inline in Technical Approach.]

## Prior Art

<!-- Search closed issues and merged PRs for related work before proposing solutions.
     Skip for trivial changes (Small appetite, no-code, or clearly greenfield work). -->

[Search results from `gh issue list --state closed` and `gh pr list --state merged` for related work.
Include issue/PR numbers, what they attempted, and whether they succeeded or failed.
If nothing found, state "No prior issues found related to this work."]

- **[Issue/PR #N]**: [Title] -- [What it did, outcome, relevance to current work]
- **[Issue/PR #N]**: [Title] -- [What it did, outcome, relevance to current work]

## Spike Results

<!-- CONDITIONAL: Only include if Phase 1.5 ran spike tasks to validate assumptions.
     Delete this section if no spikes were needed (e.g., Small appetite greenfield work).
     Spike results provide validated findings so builders don't re-investigate assumptions. -->

[Results from time-boxed spike investigations that validated or invalidated plan assumptions.]

### spike-1: [Description]
- **Assumption**: "[What was being tested]"
- **Method**: [web-research | prototype | code-read]
- **Finding**: [What was discovered]
- **Confidence**: [high | medium | low]
- **Impact on plan**: [How this finding shaped the solution]

## Data Flow

<!-- Trace the end-to-end data flow through the components this change touches.
     Skip for trivial changes or purely documentation/process work.
     For multi-component features, trace from input to output across all boundaries. -->

[Trace how data moves through the system for the feature/fix being planned.
Start from the entry point (user action, API call, event) and follow through
each component, transformation, and storage layer to the final output.]

1. **Entry point**: [Where the data/action originates]
2. **[Component]**: [What happens to the data here]
3. **[Component]**: [What happens to the data here]
4. **Output**: [Where and how the result is delivered]

## Why Previous Fixes Failed

<!-- CONDITIONAL: Only include this section if Prior Art search found previous attempts
     to fix the same or similar problem. If this is greenfield work or no prior fixes
     exist, delete this entire section.
     This section prevents the pattern of repeated fixes that each address a symptom
     without resolving the root cause. -->

[For each prior attempt that failed or was incomplete, analyze WHY it failed.
Look for patterns: Was the root cause misidentified? Was the fix applied at the
wrong layer? Did it address a symptom instead of the cause?]

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #N | [Description] | [Root cause analysis] |
| PR #N | [Description] | [Root cause analysis] |

**Root cause pattern:** [What underlying issue connects the repeated failures]

## Architectural Impact

<!-- Assess how this change affects the broader system architecture.
     Skip for isolated changes with no cross-component effects.
     Focus on: coupling changes, new dependencies, interface modifications,
     and changes to data ownership or flow direction. -->

[How does this change affect system architecture? Consider:]

- **New dependencies**: [Any new imports, services, or libraries required]
- **Interface changes**: [APIs, function signatures, or contracts that change]
- **Coupling**: [Does this increase or decrease coupling between components?]
- **Data ownership**: [Does this change which component owns or manages data?]
- **Reversibility**: [How hard would it be to undo this change?]

## Appetite

**Size:** [Small | Medium | Large]

**Team:** [list roles involved, e.g., "Solo dev" or "Solo dev, PM" or "Solo dev, PM, code reviewer"]

**Interactions:**
- PM check-ins: [0 | 1-2 | 2-3] (scope alignment, requirement clarification)
- Review rounds: [0 | 1 | 2+] (code review, design review, QA)

Solo dev work is fast — the bottleneck is alignment and review. Appetite measures communication overhead, not coding time.

## Prerequisites

[Environment requirements that must be satisfied before building. Each requirement has a programmatic check command. If no prerequisites are needed, write "No prerequisites — this work has no external dependencies."]

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Example: `EXAMPLE_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('EXAMPLE_API_KEY')"` | Example service access |

Run all checks: `python scripts/check_prerequisites.py docs/plans/{slug}.md`

## Solution

### Key Elements

- **[Component 1]**: [What it does, not how]
- **[Component 2]**: [What it does, not how]
- **[Component 3]**: [What it does, not how]

### Flow

[Breadboard-style flow showing user journey]

**Starting point** → [Action/affordance] → **Next place** → [Action/affordance] → **End state**

Example:
Settings page → Click "Enable 2FA" → Setup screen → Enter code → Confirmation → Back to settings (with 2FA enabled)

### Technical Approach

[High-level technical direction - stay abstract enough for implementation flexibility]

- [Key decision 1]
- [Key decision 2]
- [Integration points]

## Failure Path Test Strategy

[Every plan must address how failure paths will be tested. Silent failures are a class of bug where exceptions are caught and swallowed without logging, empty outputs loop indefinitely, or error states render incorrectly. Address each category below.]

### Exception Handling Coverage
- [ ] Identify `except Exception: pass` blocks in touched files — each must have a corresponding test asserting observable behavior (logger.warning, metric, or state change)
- [ ] If no exception handlers exist in the scope of this work, state "No exception handlers in scope"

### Empty/Invalid Input Handling
- [ ] Document what happens when functions receive empty strings, None, or whitespace-only inputs
- [ ] Add tests for empty input edge cases in any new or modified functions
- [ ] If the feature involves agent output processing, verify empty output does not trigger silent loops

### Error State Rendering
- [ ] If the feature has user-visible output, test the error/failure rendering path (not just success)
- [ ] Verify error messages propagate to the user rather than being swallowed silently

## Test Impact

[Audit existing tests that will break or need changes due to this work. For each affected test file or test case, specify a disposition: UPDATE, DELETE, or REPLACE. This gives builders clear guidance on test modifications before they start implementation.]

- [ ] `tests/unit/test_example.py::test_old_behavior` — UPDATE: assert new return value instead of old
- [ ] `tests/integration/test_flow.py::test_end_to_end` — REPLACE: rewrite for new API contract
- [ ] `tests/unit/test_legacy.py::test_deprecated_path` — DELETE: tests removed feature

[If no existing tests are affected, state that explicitly with justification:]

No existing tests affected — [justification explaining why, e.g., "this is a greenfield feature with no prior test coverage" or "changes are purely additive and don't modify any existing behavior or interfaces"].

## Rabbit Holes

[Areas that look tempting but will swallow disproportionate time. Call these out so the team deliberately avoids them.]

- [Tempting but wasteful avenue to avoid]
- [Complexity trap that seems important but isn't worth it]
- [Tangent that should be a separate project]

## Risks

### Risk 1: [Description]
**Impact:** [What breaks if this goes wrong]
**Mitigation:** [How we'll handle it]

### Risk 2: [Description]
**Impact:** [What breaks if this goes wrong]
**Mitigation:** [How we'll handle it]

## Race Conditions

[Enumerate timing-dependent bugs, concurrent access patterns, and data/state prerequisites.
For each hazard identified, fill out the template below. If no concurrency concerns exist,
state "No race conditions identified" with justification (e.g., "all operations are synchronous
and single-threaded").]

### Race N: [Description]
**Location:** [File and line range]
**Trigger:** [What sequence of events causes the race]
**Data prerequisite:** [What data must exist/be populated before the dependent operation]
**State prerequisite:** [What system state must hold for correctness]
**Mitigation:** [How the implementation prevents this -- await, lock, re-read, idempotency, etc.]

## No-Gos (Out of Scope)

[Explicitly state what we're NOT doing. This is critical for scope control.]

- [Feature deferred to later]
- [Edge case we'll handle in v2]
- [Related but separate concern]

## Update System

[This system is deployed across multiple machines via the `/update` skill. Consider whether the update process needs changes.]

- Whether the update script or update skill needs changes
- New dependencies or config files that must be propagated
- Migration steps for existing installations
- If no update changes are needed, state that explicitly (e.g., "No update system changes required — this feature is purely internal")

## Agent Integration

[The agent receives Telegram messages via the bridge and can only use tools exposed through MCP servers registered in `.mcp.json`. New Python functions in `tools/` are invisible to the agent unless wrapped.]

- Whether a new or existing MCP server in `mcp_servers/` needs to expose this functionality
- Changes to `.mcp.json` registration
- Whether the bridge itself (`bridge/telegram_bridge.py`) needs to import/call the new code directly
- Integration tests that verify the agent can actually invoke the new capability
- If no agent integration is needed, state that explicitly (e.g., "No agent integration required — this is a bridge-internal change")

## Documentation

[What documentation needs to be created or updated when this work ships. Use the `documentarian` agent type for these tasks.]

### Feature Documentation
- [ ] Create/update `docs/features/[feature-name].md` describing the feature
- [ ] Add entry to `docs/features/README.md` index table

### External Documentation Site
[If the repo uses Sphinx, Read the Docs, MkDocs, or similar:]
- [ ] Update relevant pages in the documentation site
- [ ] Verify docs build passes

### Inline Documentation
- [ ] Code comments on non-obvious logic
- [ ] Updated docstrings for public APIs

[If no documentation changes are needed, state that explicitly and explain why.]

## Success Criteria

[Measurable outcomes tied to the appetite. What does "done" look like?]

- [ ] [Criterion 1]
- [ ] [Criterion 2]
- [ ] [Criterion 3]
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] [If Agent Integration section specifies "X calls Y": grep confirms X references Y]
- [ ] [If bug fix: All related xfail/xpass tests converted to hard assertions]

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly - they deploy team members and coordinate.

### Team Members

[List each team member needed. Name them uniquely so they can be referenced in tasks.]

- **Builder ([component-name])**
  - Name: [unique-name, e.g., "api-builder"]
  - Role: [Single focused responsibility]
  - Agent Type: [builder | code-reviewer | test-engineer | etc.]
  - Resume: true

- **Validator ([component-name])**
  - Name: [unique-name, e.g., "api-validator"]
  - Role: [What they verify]
  - Agent Type: validator
  - Resume: true

[Add more team members as needed. Pattern: builder + validator pairs for each major component.]

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)
- `code-reviewer` - Code review, security checks
- `test-engineer` - Test implementation and strategy
- `documentarian` - Documentation updates
- `plan-maker` - Planning subagent
- `frontend-tester` - Browser testing

**Tier 2 — Specialists (recruit for specific needs):**
- `debugging-specialist` - Complex bug investigation, memory leaks, async debugging
- `async-specialist` - Concurrency, rate limiting, circuit breakers, event loop optimization
- `security-reviewer` - OWASP vulnerability scanning, auth review, secrets detection
- `performance-optimizer` - Query optimization, multi-tier caching, profiling
- `mcp-specialist` - MCP server development and tool integration
- `agent-architect` - Agent systems, context management, living codebase patterns
- `api-integration-specialist` - External API auth, rate limiting, error strategies
- `data-architect` - Schema design, migrations, audit triggers, archival
- `migration-specialist` - Data migration, traffic routing, rollback procedures
- `documentation-specialist` - Doc format standards, templates, Mermaid diagrams
- `test-writer` - Edge case generation, assertion patterns, async testing
- `ui-ux-specialist` - Conversational UX, error humanization, accessibility
- `designer` - UI implementation, atomic design, design system adherence

**Service Agents (domain-specific task delegation):**
- `linear`, `notion`, `sentry`, `stripe`, `render`

## Step by Step Tasks

[Each task maps to a `TaskCreate` call. Execute top to bottom. Build tasks can run in parallel; validators wait for their builder.]

### 1. [First Build Task]
- **Task ID**: build-[component]
- **Depends On**: none
- **Validates**: [test files/patterns that must pass, e.g., tests/unit/test_component.py, tests/integration/test_bar.py (create)]
- **Informed By**: [spike task IDs with key findings, e.g., spike-1 (confirmed: API supports batch calls)]
- **Assigned To**: [builder name from Team Members]
- **Agent Type**: [agent type]
- **Parallel**: true
- [Specific action to complete]
- [Specific action to complete]

### 2. [Validation Task]
- **Task ID**: validate-[component]
- **Depends On**: build-[component]
- **Assigned To**: [validator name from Team Members]
- **Agent Type**: validator
- **Parallel**: false
- Verify implementation meets criteria
- Run validation commands
- Report pass/fail status

[Continue pattern for each component...]

### N-1. Documentation
- **Task ID**: document-feature
- **Depends On**: [final build/validate task IDs]
- **Assigned To**: [documentarian name from Team Members]
- **Agent Type**: documentarian
- **Parallel**: false
- Create/update feature docs in `docs/features/`
- Add entry to documentation index
- Update external docs site if applicable

### N. Final Validation
- **Task ID**: validate-all
- **Depends On**: [all previous task IDs including document-feature]
- **Assigned To**: [lead validator]
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

[Machine-readable checks that `/do-build` executes automatically after the build.
Each row is a named check with an executable command and expected result.
Supported expectations: "exit code N", "output > N", "output contains X".]

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
| [Feature-specific check] | `[command]` | [expected] |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | [agent-type] | [The concern raised] | [How/whether addressed] | [Guard condition or gotcha] |

---

## Open Questions

[Critical unknowns that need supervisor input before finalizing]

1. [Question about scope/approach]
2. [Question about priority/tradeoff]
3. [Question about technical constraint]
