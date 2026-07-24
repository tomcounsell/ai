# Pre-Publish Checklist

Run every check before creating the issue. Fix failures — do not skip items.

**Mode-aware:** a few checks branch on the Step 0 mode (well-scoped vs blue-sky /
exploratory) — each is tagged **[mode-aware]**. Untagged checks apply to both
modes. In blue-sky mode, do NOT fail an issue for lacking crispness the mode
deliberately relaxes; apply the blue-sky variant of the tagged check instead.

## Reconnaissance Checks

- [ ] **Recon performed** — The reconnaissance routine (Step 3) was executed: broad scan completed, concerns identified, parallel fan-out agents dispatched, and findings synthesized. Skip only for trivially simple issues (typo fixes, config changes).

- [ ] **Recon summary present** — The issue body contains a `## Recon Summary` section with the four buckets (Confirmed, Revised, Pre-requisites, Dropped) and at least one concrete item. OR it contains a `## Recon: Skipped` section with justification for why recon was unnecessary. **This check is NOT relaxed in blue-sky mode** — a fog-forward issue still needs the four-bucket `## Recon Summary` (lighter content, same shape); `## Recon: Skipped` is only for trivial issues, never for exploratory ones.

- [ ] **Scope reflects recon** — The Solution Sketch and Acceptance Criteria reflect recon findings. Items flagged as "already done" or "dropped" are not in scope. Items flagged as "pre-requisites" are called out as blockers.

## Readability Checks

- [ ] **Stranger test** — Could someone with general software experience but zero knowledge of this codebase understand the issue? Read the title and first paragraph as if you've never seen this repo.

- [ ] **No undefined jargon** **[mode-aware]** — Every project-specific term, system name, or acronym that isn't common software engineering knowledge is defined in the Context blockquote or Definitions table. Common knowledge does NOT need defining (e.g., "REST API", "Redis", "git branch", "CI/CD"). Project-specific concepts DO need defining (e.g., "Observer Agent", "SDLC pipeline", "attractor spec", "steering queue"). *Blue-sky variant:* definitions are encouraged but not blocking — define what you can; an undefined term the exploration itself is meant to pin down is acceptable if named in `## Fog`.

- [ ] **Links present** — Every reference to a file, repo, PR, issue, doc page, or external resource includes a clickable link. No "see the docs" without a URL.

## Structural Checks

- [ ] **Context blockquote** — Issue opens with a `> **Context:**` blockquote that orients the reader. If the issue is simple and self-evident (e.g., "Fix typo in README"), the blockquote can be omitted.

- [ ] **Problem before solution** — The Problem section comes before the Solution Sketch. The reader understands *what's wrong* before learning *what to do about it*.

- [ ] **Measurable acceptance criteria** **[mode-aware]** — Each criterion is verifiable (can be checked off with a yes/no). No vague criteria like "improve quality" — instead, "issues created by the skill pass the pre-publish checklist." *Blue-sky variant:* criteria are **fog-clearing signals** — what we expect to observe once the direction resolves (e.g. "we can articulate which of {A, B, C} fits, with rationale"). Still concrete and checkable ("did we reach that signal?"), just not a pre-committed deliverable.

- [ ] **Type label** — Issue has a label: `bug`, `feature`, or `chore`.

## Downstream Checks

- [ ] **Planner-ready** — The Problem and Solution Sketch sections contain enough detail for `/do-plan` to produce a meaningful plan without asking clarifying questions. If you read only those two sections, could you start planning?

- [ ] **No implementation details** — The issue describes *what* and *why*, not *how*. Implementation details belong in the plan document, not the issue. Exception: constraints ("must not add new dependencies") are appropriate.

## Quality Bar

If any check fails, fix the issue body before publishing. The purpose of this checklist is to catch issues that would produce low-quality plans downstream.

The heuristic: **if `/do-plan` would need to ask you "what does X mean?" after reading the issue, X needs to be defined in the issue.**
