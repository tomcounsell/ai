# Pre-Publish Checklist

Run every check before creating the issue. Fix failures — do not skip items.

## Reconnaissance Checks

- [ ] **Recon performed** — The reconnaissance routine (Step 3) was executed: broad scan completed, concerns identified, parallel fan-out agents dispatched, and findings synthesized. Skip only for trivially simple issues (typo fixes, config changes).

- [ ] **Recon summary present** — The issue body contains a `## Recon Summary` section with the four buckets (Confirmed, Revised, Pre-requisites, Dropped) and at least one concrete item. OR it contains a `## Recon: Skipped` section with justification for why recon was unnecessary.

- [ ] **Scope reflects recon** — The Solution Sketch and Acceptance Criteria reflect recon findings. Items flagged as "already done" or "dropped" are not in scope. Items flagged as "pre-requisites" are called out as blockers.

## Readability Checks

- [ ] **Stranger test** — Could someone with general software experience but zero knowledge of this codebase understand the issue? Read the title and first paragraph as if you've never seen this repo.

- [ ] **No undefined jargon** — Every project-specific term, system name, or acronym that isn't common software engineering knowledge is defined in the Context blockquote or Definitions table. Common knowledge does NOT need defining (e.g., "REST API", "Redis", "git branch", "CI/CD"). Project-specific concepts DO need defining (e.g., "Observer Agent", "SDLC pipeline", "attractor spec", "steering queue").

- [ ] **Links present** — Every reference to a file, repo, PR, issue, doc page, or external resource includes a clickable link. No "see the docs" without a URL.

## Structural Checks

- [ ] **Context blockquote** — Issue opens with a `> **Context:**` blockquote that orients the reader. If the issue is simple and self-evident (e.g., "Fix typo in README"), the blockquote can be omitted.

- [ ] **Problem before solution** — The Problem section comes before the Solution Sketch. The reader understands *what's wrong* before learning *what to do about it*.

- [ ] **Measurable acceptance criteria** — Each criterion is verifiable (can be checked off with a yes/no). No vague criteria like "improve quality" — instead, "issues created by the skill pass the pre-publish checklist."

- [ ] **Type label** — Issue has a label: `bug`, `feature`, or `chore`.

## Downstream Checks

- [ ] **Planner-ready** — The Problem and Solution Sketch sections contain enough detail for `/do-plan` to produce a meaningful plan without asking clarifying questions. If you read only those two sections, could you start planning?

- [ ] **No implementation details** — The issue describes *what* and *why*, not *how*. Implementation details belong in the plan document, not the issue. Exception: constraints ("must not add new dependencies") are appropriate.

## Quality Bar

If any check fails, fix the issue body before publishing. The purpose of this checklist is to catch issues that would produce low-quality plans downstream.

The heuristic: **if `/do-plan` would need to ask you "what does X mean?" after reading the issue, X needs to be defined in the issue.**
