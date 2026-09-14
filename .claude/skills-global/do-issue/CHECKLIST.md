# Pre-Publish Checklist

Run every check before creating the issue. Fix failures — do not skip items.

## Falsification Checks

Run these first. They ask whether the issue should exist at all; every other section asks only whether it is well written. A wrong issue that passes every readability check still costs the next reader a full investigation to disprove — and it reads as authoritative while doing it.

- [ ] **Observed, not inferred** — The issue names what was actually seen: a command and its output, a log line, a failing test, a timeline entry. If the central claim is phrased "could", "would", or "may", it is a hypothesis. Reproduce it, or raise it in the originating discussion and file nothing.

- [ ] **Counter-case checked** — If the issue claims a pattern ("systemic", "every", "always", "will recur"), name the instances you checked that could have falsified it, and what they showed. One observation is an anecdote. The cases that did *not* exhibit the behavior are the evidence that decides it.

- [ ] **Reachable in a supported configuration** — The bad state is reachable without a setup the repo actively prevents or heals. If code deliberately avoids that state, the finding is usually a stale comment, not a defect.

- [ ] **Not already decided** — Search for a test, code comment, or doc encoding the opposite intent (`git grep` the path, constant, or behavior in question). If one exists, this is a proposal to change a decision, not a bug report — name that reasoning and argue against it, or drop the issue.

**Kill criterion:** if one of these fails, the right output is usually *no issue*. Say so where the finding came up and move on. Filing anyway transfers the cost of disproving your claim onto whoever picks it up next.

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
