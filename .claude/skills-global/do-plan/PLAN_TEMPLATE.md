---
status: Planning
type: [bug | feature | chore]  # May be pre-populated from auto-classification
appetite: [Small | Medium | Large]
owner: [Name]
created: [YYYY-MM-DD]
tracking: [GitHub Issue URL - added automatically]
last_comment_id: [Latest issue comment ID incorporated into this plan - updated automatically]
<!-- Universal sections apply to any repo. The mandated Documentation / Update System /
     Agent Integration / Test Impact set is a repo convention declared in docs/sdlc/do-plan.md;
     a repo without it adapts or omits those sections. Delete CONDITIONAL sections that do not apply. -->

---

# [Feature Name]

## Problem

[Real scenario showing the pain. User perspective. Specific, not vague.]

**Current behavior:**
[What happens now that's broken/painful]

**Desired outcome:**
[What success looks like]

## Freshness Check

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

[Related closed issues and merged PRs: what each attempted and whether it worked.
If none: "No prior issues found related to this work."]

- **[Issue/PR #N]**: [Title] -- [What it did, outcome, relevance to current work]
- **[Issue/PR #N]**: [Title] -- [What it did, outcome, relevance to current work]

## Research

**Queries used:**
- [Search query 1]
- [Search query 2]

**Key findings:**
- [Finding 1 — source URL, how it informs the plan]
- [Finding 2 — source URL, how it informs the plan]

[If no useful results: "No relevant external findings — proceeding with codebase context and training data."]

## Spike Results

<!-- CONDITIONAL: only if Phase 1.5 ran spikes. -->

### spike-1: [Description]
- **Assumption**: "[What was being tested]"
- **Method**: [web-research | prototype | code-read]
- **Finding**: [What was discovered]
- **Confidence**: [high | medium | low]
- **Impact on plan**: [How this finding shaped the solution]

## Data Flow

<!-- CONDITIONAL: multi-component changes only. -->

1. **Entry point**: [Where the data/action originates]
2. **[Component]**: [What happens to the data here]
3. **[Component]**: [What happens to the data here]
4. **Output**: [Where and how the result is delivered]

## Why Previous Fixes Failed

<!-- CONDITIONAL: only if Prior Art found earlier failed or incomplete fixes. -->

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #N | [Description] | [Root cause analysis] |
| PR #N | [Description] | [Root cause analysis] |

**Root cause pattern:** [What underlying issue connects the repeated failures]

## Architectural Impact

<!-- CONDITIONAL: cross-component changes only. -->

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

## Prerequisites

[Environment requirements that must be satisfied before building. Each requirement has a programmatic check command. If no prerequisites are needed, write "No prerequisites — this work has no external dependencies."]

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Example: `EXAMPLE_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('EXAMPLE_API_KEY')"` | Example service access |

Run all checks via the repo's prerequisite checker if it has one (this repo: `python scripts/check_prerequisites.py docs/plans/{slug}.md`); otherwise run each Check Command above directly.

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

[How failure paths will be tested: swallowed exceptions, empty outputs that loop, error states that render wrong.]

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

[Existing tests this work breaks or changes, each with a disposition: UPDATE, DELETE, or REPLACE.]

- [ ] `tests/unit/test_example.py::test_old_behavior` — UPDATE: assert new return value instead of old
- [ ] `tests/integration/test_flow.py::test_end_to_end` — REPLACE: rewrite for new API contract
- [ ] `tests/unit/test_legacy.py::test_deprecated_path` — DELETE: tests removed feature

[If no existing tests are affected, state that explicitly with justification:]

No existing tests affected — [justification explaining why, e.g., "this is a greenfield feature with no prior test coverage" or "changes are purely additive and don't modify any existing behavior or interfaces"].

## Rabbit Holes

[Tempting avenues that would swallow disproportionate time.]

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

[What we are NOT doing. **Every entry carries one of the four tags below**; untagged
"deferred" / "follow-up" entries fail the plan validator (in this repo,
`.claude/hooks/validators/validate_no_gos_justification.py`). If the agent could finish the
item in this plan, do it instead of deferring (#1325).

Tag legend:

- `[EXTERNAL]` — needs a real human/world action (rotate a secret, click a
  third-party UI, run on a machine the agent cannot reach)
- `[ORDERED]` — sequenced deploy/merge that must wait for a human-gated event
  in another system
- `[DESTRUCTIVE]` — irreversible one-shot where review-before-execute is the
  safety mechanism (data deletion, schema migration on hot tables)
- `[SEPARATE-SLUG #NNN]` — already filed as issue NNN. The validator runs
  `gh issue view NNN` to confirm; tracking-only promises with no issue fail.

If nothing is genuinely out of scope, write: "Nothing deferred — every
relevant item is in scope for this plan."

**Anti-criteria tip:** For any No-Go describing a forbidden *code-level outcome*
(especially `[DESTRUCTIVE]` and `[SEPARATE-SLUG]` entries), add a corresponding
`## Verification` row asserting its absence — these inverse rows are anti-criteria.
`[EXTERNAL]` and `[ORDERED]` No-Gos are genuinely advisory and are never required
to have an anti-criterion (they describe human/world actions, not checkable code
outcomes). See `docs/features/machine-readable-dod.md` for examples.]

- [EXTERNAL] [Item that needs a real human/world action — explain why]
- [ORDERED] [Item blocked by a human-gated event — name the event]
- [SEPARATE-SLUG #NNN] [Item filed as separate issue — link the issue]

<!-- Repo-convention sections: docs/sdlc/do-plan.md is the authority for their content. -->

## Update System

- Whether the deploy/update process needs changes
- New dependencies or config files that must be propagated
- Migration steps for existing installations
- If no update changes are needed, state that explicitly (e.g., "No update system changes required — this feature is purely internal")

## Agent Integration

- Which agent-reachable surface (CLI entry point, tool, or direct import) exposes the new functionality
- Whether the entry point needs to import/call the new code directly
- Integration tests that verify the agent can actually invoke the new capability
- If no agent integration is needed, state that explicitly (e.g., "No agent integration required — this is a bridge-internal change")

## Documentation

[Docs to create or update when this ships (`documentarian` agent type).]

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

The build lead orchestrates and never builds directly.

### Team Members

[Uniquely named, so tasks can reference them. Pattern: a builder + validator pair per major component.]

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

### Available Agent Types

**Tier 1 — Core (default choices):**
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)
- `code-reviewer` - Code review, security checks
- `test-engineer` - Test implementation and strategy
- `documentarian` - Documentation updates
- `plan-maker` - Planning subagent
- `frontend-tester` - Browser testing

**Domain expertise:** there are no specialist agents. Assign a `builder` (or
`code-reviewer` for review-only work), add a `Domain: <tag>` line to the task, and paste the
matching rules from [`DOMAIN_FRAMING.md`](DOMAIN_FRAMING.md) into its assignment. Use the
built-in `Explore` / `general-purpose` agents for broad recon.

**Service Agents (domain-specific task delegation):**
- `linear`, `notion`, `sentry`, `stripe`, `render` — portable agents that wrap a
  SaaS/MCP integration; available in any repo via the synced `~/.claude/agents/`.

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

**Positive expectations** (the command must succeed or produce the expected output):
- `exit code N` — passes when exit_code == N (positive exact-match; e.g. `exit code 0` for success, `exit code 1` for "grep found no matches")
- `output > N` — passes when stdout (as integer) is greater than N
- `output contains X` — passes when substring X appears in stdout

**Inverse expectations / anti-criteria** (the command must NOT produce a forbidden result):
- `exit code != N` — passes when exit_code != N (command must fail or exit differently than N)
- `output does not contain X` — passes when X is absent from stdout AND stdout is non-empty (empty-stdout gate prevents false-passes from errored commands)
- `match count == 0` — passes when every non-blank stdout line is "0" or ends with ":0" (supports `grep -c`, `grep -rc`, and `grep -r ... | wc -l` shapes) AND stdout is non-empty

**Anti-criteria** are inverse rows asserting a No-Go's forbidden code-level outcome is absent.
Add one only when a command can detect the violation mechanically, and show it FAILS against a
deliberately violating input first (red-state proof, pasted into the PR description).

**One table definition per pipe-block.** A check table is identified by a `Command`
column among its first three columns. If this section needs a second table -- a
red/green summary, a findings recap -- keep it as its own contiguous markdown table;
it is a **non-check table** and is skipped with a named diagnostic rather than
executed as checks (the parser scopes to each pipe-block independently, per GFM's
definition of a table). A `## Verification` section that has table rows but whose
tables carry no `Command` column fails the gate loudly instead of silently passing on
zero checks -- give at least one table a `Command` column, or drop the rows.]

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
| [Anti-criterion example] | `grep -c "forbidden_pattern" changed/file.py` | match count == 0 |
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
