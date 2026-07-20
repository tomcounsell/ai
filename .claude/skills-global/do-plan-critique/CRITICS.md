# War Room Critics

Critic depth is determined by the Step 2.6 triage step in SKILL.md. LITE runs 1 Consolidated Critic; FULL runs 3 merged critics.

## How to Spawn

Each critic is spawned as an Agent tool call. **Each critic ENDS by atomically writing its findings to a result file**: write the full content to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp`, then **rename** it to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`. The file must end with the **two-line terminal fence** `<<<CRITIQUE-RESULT-COMPLETE>>>` then `STATUS: COMPLETED` as the **last two lines** — appended after the findings body, as the critic's deliberate final action. (Foreground vs. background dispatch is a latency preference only; the barrier is the result-file membership check in SKILL.md Step 3.5, not driver-await.)

**IMPORTANT**: Emit the fence token `<<<CRITIQUE-RESULT-COMPLETE>>>` **ONLY** as the terminal completion marker — **never quote** it (or the bare `STATUS: COMPLETED` line) inside your findings text. The two-line fence must appear exactly once, as the last two lines of the file. If you quote either line in your findings prose, the gate can be defeated, so never quote it.

The prompt for each follows the template:

```
You are the {ROLE} critic in a plan war room. Your job is to find flaws in this plan from your specific perspective.

PLAN:
{full plan text}

SOURCE_FILES:
{verified file contents with paths, from Step 1.5}

CONTEXT:
{issue body, prior art summaries}

YOUR LENS: {lens description}

LOOK FOR: {specific checklist}

IMPORTANT: Use ONLY the provided SOURCE_FILES for code references. Do NOT read files yourself.
If a file is not in SOURCE_FILES, state "file not provided" rather than guessing its contents.
Any BLOCKER or CONCERN referencing a specific file must include a file:line citation from SOURCE_FILES.

GROUNDING CONTRACT (issue #2124 — hard requirement): your result file MUST include at
least one `GROUNDING:` line that quotes THIS plan verbatim — either a verbatim phrase of
at least ~24 characters copied exactly from the plan text above, OR an exact plan section
header (e.g. `## Solution`). This proves you actually read this plan and not a hallucinated
one. A result with zero verifiable plan citations is treated as an incomplete critic (like a
missing one), re-dispatched, then STOPs the stage — even if it carries the terminal fence.
`No findings.` is still valid but MUST still carry a `GROUNDING:` citation line.

Return 0-3 findings. If you find nothing, return "No findings." Do not invent problems.

Format each finding as:
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: {section name}
FINDING: {what's wrong, 1-2 sentences}
SUGGESTION: {how to fix, 1-2 sentences}

Begin the result file with your grounding evidence, e.g.:
GROUNDING: "<verbatim phrase copied from the plan>"

As your FINAL action, write your findings to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md.tmp`,
then rename it to `${CRITIQUE_RUN_DIR}/{critic_name}.result.md`. The file must end with these
two lines exactly:
<<<CRITIQUE-RESULT-COMPLETE>>>
STATUS: COMPLETED
NEVER quote the fence token `<<<CRITIQUE-RESULT-COMPLETE>>>` (or the bare `STATUS: COMPLETED`
line) anywhere inside your findings text — emit the two-line fence ONLY as the terminal marker.
```

---

## LITE Path Critics

### Consolidated Critic

**Lens**: "Is this plan ready to build?" (combines failure modes, scope/value, internal consistency)

**Prompt addition**:
```
You are the consolidated plan critic. Assess this plan from three angles:

**Sub-section A — Failure Modes (Skeptic + Adversary lens)**
LOOK FOR:
- Assumptions stated as facts without evidence or spike results
- Missing failure modes — what happens when the happy path breaks?
- Race conditions NOT identified in the plan's Race Conditions section
- Edge cases in the data model: null fields, empty strings, missing relationships

**Sub-section B — Scope & Value (Simplifier + User lens)**
LOOK FOR:
- Features smuggled in as "refactoring" — scope creep disguised as current requirements
- Problem statement disconnected from user pain
- Tasks that exist for theoretical completeness but don't address the stated problem

**Sub-section C — Internal Consistency (Consistency Auditor lens)**
LOOK FOR:
- A success criterion that assumes behavior the Technical Approach explicitly excludes
- Two sections that name different components as responsible for the same thing
- No-Gos that contradict items in the Solution

Emit your findings organized under the three sub-section headers above.
Return 0-3 findings total (combined across all sub-sections).

IMPORTANT: For every BLOCKER or CONCERN finding, you MUST include a concrete IMPLEMENTATION NOTE.
If you cannot write a specific guard condition, call signature, or gotcha that makes the finding
implementable without re-investigation, downgrade the finding to NIT or drop it. A BLOCKER or
CONCERN without an Implementation Note will be excluded from the report.
```

---

## FULL Path Critics

### Risk & Robustness

**Lens**: "Why will this fail, and what breaks it at runtime?" (Skeptic + Adversary + Operator merged)

**Prompt addition**:
```
You are the Risk & Robustness critic. Assess this plan from three angles, emitting a labeled sub-section for each:

**Sub-section: Skeptic (Failure Assumptions)**
LOOK FOR:
- Assumptions stated as facts without evidence or spike results
- Missing failure modes — what happens when the happy path breaks?
- Components taking on too many responsibilities (3+ roles merged into one)
- Dependencies on external behavior that isn't contractually guaranteed

**Sub-section: Adversary (Edge Cases & Race Conditions)**
LOOK FOR:
- Race conditions NOT identified in the plan's Race Conditions section
- Edge cases in the data model: null fields, empty strings, missing relationships, orphaned records
- State corruption paths: what if a session crashes between two writes?
- Resource exhaustion: unbounded queues, uncapped retries

**Sub-section: Operator (Operational Risk)**
LOOK FOR:
- Monitoring gaps — how do you know the new system is working?
- Rollback realism — can each phase actually be reverted, or does it leave orphaned state?
- Partial failure states — what does the system look like if deploy happens mid-migration?
- Emergency recovery — is there a "break glass" procedure if the new architecture fails?

Return 0-3 findings total (combined across sub-sections).

IMPORTANT: For every BLOCKER or CONCERN finding, you MUST include a concrete IMPLEMENTATION NOTE
(specific guard condition, call signature, or gotcha). If you cannot write one, downgrade to NIT or drop.
```

### Scope & Value

**Lens**: "Is this the right solution at the right scope?" (Simplifier + User merged)

**Prompt addition**:
```
You are the Scope & Value critic. Assess from two angles:

**Sub-section: Simplifier (Unnecessary Complexity)**
LOOK FOR:
- Components absorbing multiple responsibilities that should be split or dropped
- Features smuggled in as "refactoring" — scope creep disguised as current requirements
- Over-specified solutions dictating implementation details the builder should own
- Abstractions introduced for a single use case
- "Future-proofing" disguised as current requirements

**Sub-section: User (Problem-Solution Fit)**
LOOK FOR:
- Problem statement disconnected from user pain — engineering itch vs. real problem
- User-facing behavior changes not called out
- Success criteria that are all technical with no user-facing validation
- The plan solving a bigger problem than what was asked for

Return 0-3 findings total.

IMPORTANT: For every BLOCKER or CONCERN, include a concrete IMPLEMENTATION NOTE. If you cannot,
downgrade to NIT or drop.
```

### History & Consistency

**Lens**: "Does this repeat old mistakes, and does it contradict itself?" (Archaeologist + Consistency Auditor merged)

**Prompt addition**:
```
You are the History & Consistency critic. Assess from two angles:

**Sub-section: Archaeologist (Learning from History)**
LOOK FOR:
- Patterns from failed prior attempts being repeated in the new plan
- Root causes identified in "Why Previous Fixes Failed" that the new plan doesn't address
- Missing prior art — are there related PRs/issues NOT listed that should be?
- Over-engineering patterns that this codebase has repeatedly fallen into

IMPORTANT: If the plan has no "Prior Art" or "Why Previous Fixes Failed" section, flag it as a CONCERN
(the plan hasn't learned from history).

**Sub-section: Consistency Auditor (Internal Contradictions)**
LOOK FOR:
- A spike finding directly contradicted by a task step
- A success criterion that assumes behavior the Technical Approach explicitly excludes
- Two sections naming different components as responsible for the same thing
- No-Gos that contradict items in the Solution

Return 0-3 findings total.

IMPORTANT: For every BLOCKER or CONCERN, include a concrete IMPLEMENTATION NOTE. If you cannot,
downgrade to NIT or drop.
```

---

## Critic Selection

Critic depth is determined by the Step 2.6 triage step in SKILL.md, not by the plan author:

- **LITE** (1 critic): plan passes the triage step without triggering force-FULL conditions. Use the **Consolidated Critic**.
- **FULL** (3 critics): plan touches doctrine paths, has `appetite: Large`, or triage classifies as FULL. Use **Risk & Robustness**, **Scope & Value**, and **History & Consistency** — dispatched in a single parallel message.

Force-FULL doctrine paths (override triage): `config/personas/`, `.claude/skills/`, `.claude/skills-global/`, `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/hooks/`
