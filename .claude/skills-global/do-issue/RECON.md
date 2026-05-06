# Reconnaissance Routine (Explore → Concerns → Fan-out → Synthesize)

A pre-planning investigation pattern that surfaces unknowns, conflicts, and stale assumptions before they get baked into the issue. This prevents downstream waste: issues that propose already-done work, reference dead code, or conflate separate systems.

## When to Run

- **Always** for feature and chore issues touching multiple files or systems
- **Always** when the request references recent PRs or refactors (high staleness risk)
- **Skip** for trivial issues: typo fixes, config changes, single-file bugs with obvious fixes

## Phase 1: Broad Scan

Spawn a single Explore agent (thoroughness: "very thorough") with a prompt like:

> Map the affected area for [TOPIC]. Find: relevant source files, existing tests, recent PRs, related docs, and any infrastructure that would be touched. Return file paths and key details.

**What you're looking for:**
- Which files exist and what they contain
- What tests already cover this area
- What changed recently (last 2-3 PRs)
- What docs describe the intended architecture

## Phase 2: Surface Concerns

From the scan results, identify **discrete concerns** — things that are:

| Category | Signal |
|----------|--------|
| **Already done** | Tests/code already exist for a proposed deliverable |
| **Conflicting** | Code contradicts docs, or two systems do the same thing differently |
| **Stale** | Code comments say "legacy", dead imports, functions defined but never called |
| **Conflated** | Issue treats two independent systems as one (e.g., "nudge loop + steering queue") |
| **Missing infrastructure** | Issue assumes code exists that doesn't (e.g., retry logic that's stubbed out) |
| **Architectural mismatch** | Proposed approach contradicts the current design direction |

List each concern as a **specific, answerable question**. Aim for 3-8 concerns. Fewer means you haven't looked hard enough; more means you're splitting hairs.

## Phase 3: Parallel Fan-out

Spawn **one Explore agent per concern**, all in parallel. Each agent gets:

1. **The specific question** it's investigating
2. **Which files to read** (from Phase 1 scan)
3. **What to return**: findings + a concrete recommendation (CREATE / EXTEND / SKIP / FIX FIRST / SPLIT)

Example prompt template:

> Research task — no code changes.
>
> **Question:** [The specific concern from Phase 2]
>
> **Investigate:**
> 1. Read [specific files] — find [specific functions/patterns]
> 2. Search for existing tests covering this
> 3. Check if [assumption] is true in the current code
>
> **Return:** Findings and recommendation: should this be included in the issue as-is, modified, split out, or dropped?

**Key rules:**
- Each agent investigates ONE concern (keeps responses focused)
- All agents run in parallel (wall-clock time = slowest agent, not sum)
- Agents are read-only explorers, never write code
- Give each agent enough file paths to be self-sufficient (don't make them search from scratch)

## Phase 4: Synthesize

Reconcile all agent findings into four buckets:

### 1. Confirmed (include in issue as-is)
Items where the investigation validated the original assumption. No changes needed.

### 2. Revised (include but modify scope)
Items where the investigation found partial overlap, architectural nuance, or better boundaries. Update the issue scope to match reality.

### 3. Pre-requisites (fix before this issue)
Items where stale code, architectural conflicts, or missing infrastructure must be addressed first. Either: call these out as blockers in the issue, or split them into a separate issue.

### 4. Dropped (remove from issue)
Items where the work is already done, the assumption was wrong, or the proposed approach doesn't match the architecture. Don't include these — they'll waste planning and build time.

## Output Format

After synthesis, present findings to the user before writing the issue body:

```
## Recon Summary

**Confirmed:** [N items] — ready to include
- [Item]: [one-line summary]

**Revised:** [N items] — scope adjusted
- [Item]: [what changed and why]

**Pre-requisites:** [N items] — must fix first
- [Item]: [what's blocking and suggested action]

**Dropped:** [N items] — removed from scope
- [Item]: [why it was dropped]

Proceed with writing the issue?
```

Wait for user confirmation before moving to Step 4 (Write the Issue Body). The user may want to discuss scope changes or disagree with a recommendation.

## Anti-Patterns

- **Skipping recon for "simple" issues that aren't simple** — If the issue touches code that changed in the last week, run recon. Recent changes are the #1 source of stale assumptions.
- **Running recon serially** — The whole point is parallel fan-out. If you're investigating concerns one at a time, you're wasting wall-clock time.
- **Agents that are too broad** — "Investigate everything about the job queue" produces noise. "Does the retry logic in bridge/agents.py get called in production?" produces signal.
- **Skipping synthesis** — Raw agent outputs aren't useful to the user. Reconcile into the four buckets. Conflicts between agents are especially valuable — they reveal genuine architectural ambiguity.
- **Proceeding without user confirmation** — Recon often changes the scope significantly. The user needs to approve the revised scope before you write the issue.
