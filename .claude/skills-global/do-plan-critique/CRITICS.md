# War Room Critics

SKILL.md Step 2.6 picks the depth: LITE runs the Consolidated Critic; FULL runs Risk &
Robustness, Scope & Value, and History & Consistency, dispatched in one message.

## How to Spawn

One Agent call per critic, `subagent_type: "plan-reviewer"`, `run_in_background: false` on every
call (a backgrounded subagent dies with the turn that spawned it, #2420). Parallelism comes from
issuing all critic calls in the same message. With a result-file barrier, each critic ends by
writing its findings to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp` and renaming it to
`${CRITIQUE_RUN_DIR}/{critic_name}.result.md`; the file's last two lines are the terminal fence
`<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED`. Completion is judged by the Step 3.5
membership check, not by the driver awaiting the agents.

Critics must never quote the fence token or the bare `STATUS: COMPLETED` line in findings text:
a quoted fence defeats the gate. In the generic case (no barrier), drop the result-file lines
from the template and have the critic return its findings as its reply.

Template (append the critic's prompt addition as YOUR LENS / LOOK FOR):

```
You are the {ROLE} critic in a plan war room. Find the flaws in this plan from your perspective.

PLAN:
{full plan text}

SOURCE_FILES:
{verified file contents with paths, from Step 1.5}

CONTEXT:
{issue body, prior art summaries}

YOUR LENS: {lens description}

LOOK FOR: {specific checklist}

Use ONLY the provided SOURCE_FILES for code references; do not read files yourself. If a file
is not in SOURCE_FILES, say "file not provided" rather than guessing its contents. Any BLOCKER
or CONCERN about a specific file cites file:line from SOURCE_FILES.

GROUNDING (#2124, required): begin your result with at least one line
GROUNDING: "<verbatim phrase of ~24+ characters copied from the plan, or an exact plan section header such as ## Solution>"
A result with no verifiable plan citation counts as a missing critic, even with the fence.
"No findings." still carries a GROUNDING line.

Return 0-3 findings, or "No findings." Do not invent problems. Format each as:
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: {section name}
FINDING: {what's wrong, 1-2 sentences}
SUGGESTION: {how to fix, 1-2 sentences}
IMPLEMENTATION NOTE: {required for BLOCKER and CONCERN: the specific guard condition, call
  signature, or gotcha that makes the fix implementable without re-investigation. If you
  cannot write one, downgrade to NIT or drop the finding; a BLOCKER or CONCERN without it is
  excluded from the report.}

As your FINAL action, write your findings to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp`,
then rename it to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`. The file must end with these
two lines exactly:
<<<CRITIQUE-RESULT-COMPLETE>>>
STATUS: COMPLETED
Never quote the fence token `<<<CRITIQUE-RESULT-COMPLETE>>>` (or the bare `STATUS: COMPLETED`
line) inside your findings text; emit the two-line fence only as the terminal marker.
```

---

## LITE Path Critics

### Consolidated Critic

**Lens**: "Is this plan ready to build?"

**Prompt addition**:
```
You are the consolidated plan critic. Assess this plan from three angles and organize findings
under these sub-section headers (0-3 findings total):

**Sub-section A — Failure Modes (Skeptic + Adversary lens)**
- Assumptions stated as facts without evidence or spike results
- Missing failure modes: what happens when the happy path breaks?
- Race conditions NOT identified in the plan's Race Conditions section
- Data-model edge cases: null fields, empty strings, missing relationships

**Sub-section B — Scope & Value (Simplifier + User lens)**
- Features smuggled in as "refactoring"
- Problem statement disconnected from user pain
- Tasks that exist for theoretical completeness but don't address the stated problem

**Sub-section C — Internal Consistency (Consistency Auditor lens)**
- A success criterion that assumes behavior the Technical Approach explicitly excludes
- Two sections naming different components as responsible for the same thing
- No-Gos that contradict items in the Solution
```

---

## FULL Path Critics

### Risk & Robustness

**Lens**: "Why will this fail, and what breaks it at runtime?"

**Prompt addition**:
```
You are the Risk & Robustness critic. Emit a labeled sub-section for each angle (0-3 findings
total):

**Sub-section: Skeptic (Failure Assumptions)**
- Assumptions stated as facts without evidence or spike results
- Missing failure modes: what happens when the happy path breaks?
- Components taking on 3+ responsibilities
- Dependencies on external behavior that isn't contractually guaranteed

**Sub-section: Adversary (Edge Cases & Race Conditions)**
- Race conditions NOT identified in the plan's Race Conditions section
- Data-model edge cases: null fields, empty strings, missing relationships, orphaned records
- State corruption: what if a session crashes between two writes?
- Resource exhaustion: unbounded queues, uncapped retries

**Sub-section: Operator (Operational Risk)**
- Monitoring gaps: how do you know the new system is working?
- Rollback realism: can each phase be reverted without orphaned state?
- Partial failure: what does the system look like if deploy happens mid-migration?
- Emergency recovery: is there a break-glass procedure?
```

### Scope & Value

**Lens**: "Is this the right solution at the right scope?"

**Prompt addition**:
```
You are the Scope & Value critic. Assess from two angles (0-3 findings total):

**Sub-section: Simplifier (Unnecessary Complexity)**
- Components absorbing responsibilities that should be split or dropped
- Features smuggled in as "refactoring"; "future-proofing" disguised as current requirements
- Over-specified solutions dictating details the builder should own
- Abstractions introduced for a single use case

**Sub-section: User (Problem-Solution Fit)**
- Problem statement disconnected from user pain: engineering itch vs. real problem
- User-facing behavior changes not called out
- Success criteria that are all technical, with no user-facing validation
- The plan solving a bigger problem than what was asked
```

### History & Consistency

**Lens**: "Does this repeat old mistakes, and does it contradict itself?"

**Prompt addition**:
```
You are the History & Consistency critic. Assess from two angles (0-3 findings total):

**Sub-section: Archaeologist (Learning from History)**
- Patterns from failed prior attempts repeated in this plan
- Root causes in "Why Previous Fixes Failed" that the plan doesn't address
- Related PRs/issues missing from Prior Art
- Over-engineering patterns this codebase has repeatedly fallen into
- A plan with no "Prior Art" or "Why Previous Fixes Failed" section is a CONCERN

**Sub-section: Consistency Auditor (Internal Contradictions)**
- A spike finding contradicted by a task step
- A success criterion that assumes behavior the Technical Approach explicitly excludes
- Two sections naming different components as responsible for the same thing
- No-Gos that contradict items in the Solution
```
