---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-20
tracking: https://github.com/tomcounsell/ai/issues/1066
last_comment_id:
---

# Opus 4.7 Audit of SDLC Skill Prompts

## Problem

Four SDLC skills explicitly spawn Claude Opus for tasks requiring deep reasoning: [`/do-plan`](/.claude/skills/do-plan/SKILL.md), [`/do-plan-critique`](/.claude/skills/do-plan-critique/SKILL.md), [`/do-pr-review`](/.claude/skills/do-pr-review/SKILL.md), and [`daily-integration-audit`](/.claude/skills/daily-integration-audit/SKILL.md). These skill prompts were authored against Opus 4.5/4.6 behavior. Anthropic shipped Opus 4.7 on 2026-04-16 with three documented behavioral deltas.

**Current behavior:**
- The prompts include instructions that assume Opus will *ask clarifying questions when ambiguous* (4.6 behavior). 4.7 attempts the task instead.
- Several places instruct the model to *announce or verify tool availability before claiming incapability* — redundant under 4.7, which calls `tool_search` proactively.
- Output format requirements are soft ("use this format") rather than hard ("you MUST emit exactly these fields") — under 4.7's conciseness default, soft requirements are the first casualty when the model judges the task simple.
- Soft qualifiers like "try to," "you might want to," and "consider" appear in instruction text — 4.7 interprets these literally, reducing their effect as guardrails.

**Desired outcome:**
Each Opus-targeting skill is reviewed against the 4.7 behavioral model, redundant instructions removed, and missing scaffolding (explicit output format requirements, self-contained context, hard tool-use rules) added — so the skills produce output of the same or higher quality under 4.7 as they did under 4.5/4.6.

## Freshness Check

**Baseline commit:** `c5c24ee3` (main at plan time)
**Issue filed at:** 2026-04-20T04:01:30Z (~hours before plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills/do-plan/SKILL.md` — exists (24KB, last touched 2026-04-15 by PR #982 which added WebSearch Phase 0.7) — still the Opus-dispatch path
- `.claude/skills/do-plan-critique/SKILL.md` — exists (11KB, touched 2026-04-18 by PR #1050 — G6 guard) — still the Opus-dispatch path
- `.claude/skills/do-plan-critique/CRITICS.md` — exists — confirms critic subagents use `model: "sonnet"` (line 152 of SKILL.md), so only the orchestrating layer is Opus
- `.claude/skills/do-pr-review/SKILL.md` — exists (18KB, touched 2026-04-18 by PR #1050) — still the Opus-dispatch path
- `.claude/skills/daily-integration-audit/SKILL.md` — exists (10KB, added 2026-04-17 by PR in commit `0790618e`) — confirms Opus subagent dispatch via Agent tool
- `.claude/skills/sdlc/SKILL.md:234-239` — confirms the Stage→Model table routes PLAN/CRITIQUE/REVIEW to `opus`

**Cited sibling issues/PRs re-checked:**
- #900 — CLOSED 2026-04-13 — "SDLC stage model selection and hard-PATCH builder session resume" — established the per-stage model routing table (PR #909 shipped it). Still the foundation this plan builds on.
- #928 — CLOSED 2026-04-14 — "PM dev-session briefing quality" — established that front-loading context into dev session dispatches matters. Relevant pattern for the "reduced clarification" fix.

**Commits on main since issue was filed (touching referenced files):** none — issue was filed hours before this plan, no intervening commits to the four skill files.

**Active plans in `docs/plans/` overlapping this area:** none. No other plan touches these four skill prompts.

**Notes:** The `config/personas/project-manager.md:298-302` snippet already dispatches `/do-plan` via `--model opus`. The model dispatch is working correctly; this plan addresses *prompt wording*, not model selection.

## Prior Art

- **#900 / PR #909** (merged 2026-04-13): *SDLC stage model selection and hard-PATCH builder session resume* — Established the per-stage model table that routes PLAN/CRITIQUE/REVIEW to Opus and the rest to Sonnet. Outcome: shipped. Relevance: defines the routing this plan operates on. No revision to the routing itself is in scope.
- **#928** (closed 2026-04-14): *PM dev-session briefing quality* — Related principle: front-load recon summary, key files, constraints, and `--model` into every dispatch so the dev session starts from a well-informed position. Directly analogous to the "reduced clarification" fix — 4.7 needs more upfront context, less reliance on the model probing.
- **PR #982** (merged 2026-04-09): *do-plan: add WebSearch research phase (Phase 0.7)* — Added external research to the plan flow. Relevance: confirms `ToolSearch("select:WebSearch")` is a technical schema-loading step, not a behavioral tool-announcement pattern. Must be preserved.
- **PR #1050** (merged 2026-04-18): *G6 terminal-merge-ready guard* — Recent edit to do-plan-critique / do-pr-review. Relevance: confirms these skills are actively maintained and that editing them is a routine change.

No prior attempts have audited these skills for Opus version-specific behavioral changes.

## Research

External research via WebSearch on Opus 4.7 behavioral deltas.

**Queries used:**
- "Claude Opus 4.7 system prompt behavioral changes verbosity clarification 2026"
- (WebFetch) Simon Willison's Opus 4.7 system-prompt diff article
- (WebFetch) keepmyprompts.com Opus 4.7 prompt migration guide

**Key findings:**
- **Conciseness shift** (source: [Simon Willison's analysis](https://simonwillison.net/2026/Apr/18/opus-system-prompt/)). 4.7's system prompt adds *"Claude keeps its responses focused and concise so as to avoid potentially overwhelming the user with overly-long responses."* Even disclaimers are disclosed *briefly*. Implication: soft format requests ("use this format") are the first casualty when 4.7 judges a task simple. Fix: promote "use this format" to "you MUST emit exactly these fields."
- **Proactive tool-checking** (source: Simon Willison): *"Before concluding Claude lacks a capability ... Claude calls tool_search to check whether a relevant tool is available but deferred."* Implication: skill instructions that explicitly announce tool availability are now redundant behavioral scaffolding. Fix: remove such announcements; keep `ToolSearch("select:X")` schema-loading because that's a *technical* requirement (deferred tool schemas must be fetched before call — still enforced, independent of behavioral prompt text).
- **Reduced clarification** (source: Simon Willison): *"When a request leaves minor details unspecified, the person typically wants Claude to make a reasonable attempt now, not to be interviewed first."* Implication: skills that expected Opus to surface ambiguity must front-load context so Opus starts well-informed instead of attempting-in-the-dark. Fix: add explicit context scaffolding to `/do-plan` (expand "Understand the request" into an evidence-gathering block) and to `daily-integration-audit` (make the subagent brief fully self-contained).
- **Literal interpretation** (source: [keepmyprompts.com migration guide](https://www.keepmyprompts.com/en/blog/claude-opus-4-7-prompting-guide-whats-changed)): 4.7 *"will not silently generalize an instruction from one item to another"* and *"will not infer requests you didn't make."* Vague hedges like "try to," "if possible," "you might want to" now undercut precision. Fix: replace hedges with hard directives where the intent is actually mandatory.
- **Prompt-engineering pattern to ADD** (keepmyprompts): *"Hard tool-use rules. For critical tools being underutilized: 'For any calculation involving more than 2 variables, you MUST use the calculator tool.'"* Applies to the structured-output fields in `/do-pr-review` (File/Code/Severity/Fix) and the verdict format in `/do-plan-critique`.
- **Prompt-engineering pattern to REMOVE** (keepmyprompts): *"Length scaffolding becomes redundant. Instructions like 'be concise' on simple queries should be deleted."* Check for such phrases in the four skills.

These findings directly shape the Technical Approach section: each skill gets a targeted pass for (a) hard format requirements, (b) removal of redundant tool-announcement phrasing, (c) context front-loading where relied on probing, (d) replacing vague hedges with hard directives.

## Data Flow

Prompts are Markdown files loaded at skill invocation time and injected into the Opus system/user prompt. The flow:

1. **Entry point**: PM session invokes the skill (e.g., `/do-plan` via dev session dispatched with `--model opus` from `config/personas/project-manager.md:298-302`)
2. **Skill loading**: Claude Code harness reads `.claude/skills/{name}/SKILL.md` and includes it in the context for the model
3. **Sub-file loading** (conditional): Some skills pull in additional files when needed (`PLAN_TEMPLATE.md`, `CRITICS.md`, `sub-skills/code-review.md`) — these inherit the same Opus behavior
4. **Model execution**: Opus 4.7 interprets the prompt text and produces output (plan doc, critique findings, PR review, audit report)
5. **Output consumption**: Downstream SDLC stages read the output (plan doc → critique; critique verdict → SDLC router; PR review → patch cycle; audit → triage tracks A/B/C)

This is a pure-documentation edit. No runtime code path changes. The only moving part is the *text* the model reads.

## Architectural Impact

- **New dependencies**: none (WebSearch is already loaded in `/do-plan` Phase 0.7)
- **Interface changes**: none — output formats are preserved verbatim (enforced by acceptance criteria)
- **Coupling**: unchanged — skills remain independent. No cross-skill coupling added or removed.
- **Data ownership**: unchanged — each skill still owns its own output artifacts (plan doc, critique report, review comment, audit findings)
- **Reversibility**: trivial — `git revert` restores previous prompt text. No state migration, no schema change.

## Appetite

**Size:** Small

**Team:** Solo dev (builder)

**Interactions:**
- PM check-ins: 0 (scope is fully spec'd in the issue and this plan)
- Review rounds: 1 (code review of the prompt edits + manual smoke-test of one updated skill)

Rationale: prompt-only edits across four files. No code, no schema, no migration. The only non-trivial judgment is *what to delete vs. what to preserve* — captured in detail in the Technical Approach so the builder has unambiguous guidance.

## Prerequisites

No prerequisites — this work touches only Markdown files under `.claude/skills/`. No environment access beyond `git` and a text editor.

## Solution

### Key Elements

- **Per-skill audit pass**: For each of the four skills, scan for three behavioral patterns (conciseness-vulnerable soft format requests; redundant tool-announcement instructions; clarification-reliant phrasing) and apply the edits specified in the Technical Approach.
- **Hard-directive format block**: In `/do-pr-review`, promote the File/Code/Issue/Severity/Fix format from "use this format" to "you MUST emit every finding in exactly this format, with every field filled; findings missing any field are invalid and must be dropped."
- **Self-contained subagent brief**: In `daily-integration-audit`, rewrite the Opus subagent brief so it carries all the context (feature topic, doc path, verification-pass reminder, output format spec) without relying on Opus asking follow-ups.
- **Context scaffolding in /do-plan**: Expand Phase 1 Step 1 ("Understand the request") into an explicit evidence-gathering checklist (read the issue body, read the Recon Summary, read the linked sibling issues) — so 4.7 starts from a well-informed position rather than attempting blind.

### Flow

For the *builder* executing this plan:

Read the four skill files → For each: apply the targeted edits from Technical Approach → Verify no output-format field name changed (grep the test files) → Commit → Smoke-test one skill manually → PR

### Technical Approach

For each of the four skills, apply **only** the edits listed. Do not rewrite wholesale; do not "improve" unrelated sections; do not rename fields. The external interface (args, output format, field names) is frozen.

#### A. `/do-plan` (`.claude/skills/do-plan/SKILL.md`)

- **Phase 1 Step 1 "Understand the request"** (currently one line: "What's being asked?"). Replace with explicit 3-step evidence-gathering checklist:
  1. Read the full issue body (not just title); identify the Problem, Desired Outcome, and any Acceptance Criteria checklist.
  2. Read the `## Recon Summary` section if present; extract the "Confirmed," "Revised," "Pre-requisites," and "Dropped" buckets as input to Phase 1.
  3. Follow any cited sibling issues/PRs from the issue body and summarize their relevance in a sentence each.
  
  This replaces reliance on the model asking "what do you mean by X?" with an explicit front-loaded read pass.
- **Phase 0.7 External Research / Step 1 "Load the WebSearch tool"** — keep verbatim. The `ToolSearch("select:WebSearch")` line is a *technical schema-loading* requirement (deferred tool), not a behavioral instruction. It is NOT in scope for removal.
- **Scan for vague hedges** in *instruction* text (not descriptive prose): "try to," "if possible," "you might want to." Where the intent is mandatory, replace with direct verbs ("do X," "you must X"). Where the intent is genuinely optional (examples, illustrative asides), leave unchanged. Specifically check:
  - Phase 1 bullets "Set appetite," "Rough out solution"
  - Phase 3 "Add questions to plan"
- **No changes** to Phases 0, 0.5, 1.5, 2, 2.5, 2.6, 2.7, 4. These are mechanical / already hard-directive.

#### B. `/do-plan-critique` (`.claude/skills/do-plan-critique/SKILL.md`)

- **Critic subagents use `model: "sonnet"`** (confirmed via line 152 of the current SKILL.md). The six-critic parallel spawn is Sonnet, not Opus. The ONLY Opus-executed layer is the orchestrating synthesis (Steps 1, 1.5, 4, 5, 5.5).
- **Synthesis step (Step 5) output format** — currently uses a structured markdown template. Verify the template is explicit enough that 4.7 will not elide sections under its conciseness default. Specifically:
  - The `## Verdict` section already hard-requires one of four exact strings. No change needed — this is good 4.7 form.
  - The `## Blockers`, `## Concerns`, `## Nits` section headers are exemplar-driven (use the literal fields shown). Add one sentence above Step 5 asserting: "Emit every field literally. Empty sections must still be emitted as '## Blockers\n\nNone.' — do not omit the header." This prevents 4.7 from silently dropping empty sections.
- **No other changes.** The issue's recon summary explicitly calls this skill "no changes needed documented explicitly" for the conciseness audit — the synthesis step's structured output is already robust. The audit acknowledgement must be recorded in the PR body / plan so reviewers see the skill was inspected, not skipped.

#### C. `/do-pr-review` (`.claude/skills/do-pr-review/SKILL.md`)

This is the highest-risk skill under 4.7's conciseness shift because the structured File/Code/Issue/Severity/Fix block is required verbatim.

- **Step 5 "Issue Identification & Classification"** — the existing format block (lines ~232-243) says *"For each issue found, use this format:"* followed by a fenced template. Promote to: *"For every issue found you MUST emit exactly this block, with every field present. A finding missing any field is invalid and MUST be dropped, not shortened:"* Then the template.
- **Explicit empty-section rule** — add one sentence: "If a category has zero findings, emit '### Blockers\n- None' (or the equivalent for the other categories). Do NOT omit the heading — downstream parsing depends on it."
- **Keep the existing "Code: field MUST be a verbatim quote" sentence** (line ~242) — already good 4.7 form.
- **Step 5.5 "Verify Findings"** — keep verbatim. Already hard-directive.
- **Step 6 three-tier decision tree** — keep verbatim. The `gh pr review --approve` / `--request-changes` logic is mechanical and not at risk from conciseness.
- **Sub-skill `sub-skills/code-review.md`** — the Pre-Verdict Checklist (lines ~112-135) uses a markdown table with 12 rows and requires PASS/FAIL/N/A verdicts. This is good 4.7 form. Add one sentence above the table: "Every row MUST be filled. Blank cells invalidate the review." This converts the existing soft-enforcement ("Every item must receive a PASS/FAIL/N/A") into a hard output-format requirement.

#### D. `daily-integration-audit` (`.claude/skills/daily-integration-audit/SKILL.md`)

- **Step 2 "Run the integration audit"** — currently briefs the Opus subagent (via Agent tool) with a bulleted list of context items. Under 4.7's reduced clarification, the brief must be self-contained — if anything is missing, the subagent won't ask. Rewrite the brief block into an explicit structured block:
  ```
  FEATURE_TOPIC: <slug>
  SEED_DOC_PATH: docs/features/<slug>.md
  VERIFICATION_PASS: required — re-read cited file:line; grep project for negative claims; trace dynamic-behavior claims into function bodies before writing findings
  OUTPUT_FORMAT: standard do-integration-audit format + separate "## Documentation Audit" section + "## Meta-observations" section
  FINAL_LINE: must be exactly "SUMMARY: PASS=<n> WARN=<n> FAIL=<n>" so the parent skill can extract counts
  ```
- **Scan for vague hedges** in the guardrails section. "Never spawn more than 3 hotfix dev sessions" is already hard. Check that "Skip the entire triage step if..." remains imperative; it does — no change needed.
- **No changes** to Steps 1, 3, 4, Scheduling. These are mechanical.

#### E. `/sdlc` (`.claude/skills/sdlc/SKILL.md`) — dispatch table, rows 234/235/239

- The three rows still say `opus` (not `claude-opus-4-7`). The issue explicitly **drops** version-pinning from scope: *"Anthropic's model resolution already routes `opus` to the current Opus version; pinning creates maintenance burden without predictability gain."* Leave unchanged. No edit to `/sdlc`.

### Why this approach (vs. alternatives)

- **Why not version-pin to `claude-opus-4-7`?** The issue explicitly dropped this — Anthropic's alias routes `opus` to current Opus. Pinning locks us to a specific version and requires maintenance work every release.
- **Why not rewrite each skill end-to-end?** Risks changing output format field names / external interface, which would break downstream consumers (SDLC router, parity tests, review-posting logic). Constraint: internal wording only.
- **Why not add a single "Opus 4.7 preamble" block to each skill?** Preambles are the exact kind of length scaffolding 4.7 is designed to elide. Better to make the existing instructions harder-edged than to add new prose.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No code paths changed — only Markdown prompt text. No `except Exception` blocks in scope.

### Empty/Invalid Input Handling
- [ ] N/A — prompt edits do not change input handling. The skills' existing guardrails (plan file validation in `/do-plan-critique` Step 0; PR number validation in `/do-pr-review` Step 1) remain.

### Error State Rendering
- [ ] Manual smoke-test (per acceptance criteria): invoke one updated skill under Opus 4.7 and verify the output still contains every required structural section. If a section is silently elided, the edit to that skill is insufficient and must be strengthened further.

## Test Impact

Prompt-only edits do not change any external interface or behavior tested by the existing test suite. Verification below.

- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE NOT REQUIRED: checks the `/sdlc` dispatch table's State + Skill columns, not the Model column and not per-skill prompt text. No edits to `/sdlc` in scope.
- [ ] `tests/unit/test_pr_review_audit.py` — UPDATE NOT REQUIRED: tests parse/produce `**File:**` / `**Code:**` / `**Severity:**` field *values*, not the prose describing them. Field names are preserved verbatim (frozen by the constraint "same output format").
- [ ] `tests/unit/test_skills_audit.py` — UPDATE NOT REQUIRED: checked for no matches on `opus|conciseness|tool.?check|clarif` — this test does not reference the prompt text being edited.
- [ ] `tests/unit/test_post_tool_use_sdlc.py`, `tests/unit/test_sdlc_router_*.py` — UPDATE NOT REQUIRED: these test the SDLC router logic (Python), not skill prompt content.

No existing tests need changes. Justification: the constraint "changes must not alter the external interface of any skill" means field names, output structure, and command invocations are all preserved. Tests exercise those interfaces, not the surrounding explanatory prose.

**Positive coverage (for smoke-test):** One of the four updated skills is manually invoked against a real target and the output is spot-checked against this plan's expected structural fields. This is the sixth acceptance criterion.

## Rabbit Holes

- **Rewriting each skill from scratch for "consistency."** The constraint is internal wording only; a rewrite risks output-format drift and breaks the external-interface freeze. Avoid.
- **Adding new "Opus 4.7 compatibility" preambles.** Preambles are exactly the kind of length scaffolding 4.7 elides. Every edit should *tighten* existing instructions, not add prefatory prose.
- **Auditing non-Opus skills.** The issue scope is the four Opus-dispatch skills. Sonnet-dispatch skills are not in scope even if similar patterns appear — their behavioral model is different and a separate audit.
- **Version-pinning `opus` → `claude-opus-4-7`.** Explicitly dropped by the issue's Recon Summary. Do not reintroduce.
- **Tweaking `CRITICS.md` critic prompts for 4.7.** Critics run on Sonnet (`model: "sonnet"` at SKILL.md line 152). Out of scope.
- **Adding deferred-tool schema-loading calls where they aren't needed.** `ToolSearch("select:X")` is a schema-loading mechanism, not a "4.7 compatibility" pattern. Only keep existing calls; do not add new ones.

## Risks

### Risk 1: An edit accidentally changes a field name or output-format structure
**Impact:** Downstream consumer (SDLC router, parity test, PR-comment parser) breaks silently.
**Mitigation:** Before committing, `grep` the four updated files for every structural field name that existed before (e.g., `**File:**`, `**Code:**`, `**Severity:**`, `## Verdict`, `SUMMARY: PASS=`, `READY TO BUILD`, `NEEDS REVISION`, `MAJOR REWORK`, `SEVERITY:`, `LOCATION:`, `FINDING:`, `SUGGESTION:`, `IMPLEMENTATION NOTE:`, `VERIFICATION_PASS:`, `OUTPUT_FORMAT:`, `FINAL_LINE:`). If any grep returns fewer matches post-edit than pre-edit, the edit has deleted a field and must be reverted/corrected.

### Risk 2: The hardening makes prompts too literal and loses nuance Opus 4.5/4.6 provided via inference
**Impact:** Under older Opus versions (if used for rollback or A/B), output quality regresses.
**Mitigation:** The four skills are gated behind `--model opus` which Anthropic routes to the current Opus (4.7). Older Opus versions are not in the production routing path. The smoke-test (Acceptance #6) runs against current Opus.

### Risk 3: The smoke-test passes on one skill but others are still under-specified
**Impact:** Partial fix; one or more skills regress under 4.7 despite the audit.
**Mitigation:** The acceptance criteria require *reviewing* each of the four skills individually, even when no edits result. The critique review record (AC #2 for `/do-plan-critique`) explicitly captures the "no changes needed" rationale in the PR body. This way, reviewers can challenge the rationale if it seems weak.

## Race Conditions

No race conditions identified — prompt edits are a pure-text change with no concurrency, no async, no shared state. The only ordering consideration is "edit → commit → PR" which is handled by the normal git workflow.

## No-Gos (Out of Scope)

- Version-pinning `opus` to `claude-opus-4-7` in frontmatter (explicitly dropped by the issue's Recon Summary).
- Rewriting any of the four skills wholesale.
- Editing output-format field names, section headers, or command-invocation shapes.
- Auditing any non-Opus-targeting skill (`/do-build`, `/do-test`, `/do-patch`, `/do-docs`, `/do-merge`, etc.). Sonnet's behavioral model is different and out of scope.
- Editing critic subagent prompts in `CRITICS.md` (critics run on Sonnet).
- Adding new `ToolSearch` calls not already present — schema-loading is a technical requirement, not a hardening pattern.
- Writing a new "skill authoring guide for Opus 4.x." The acceptance criteria and this plan capture the principles inline where they apply.

## Update System

No update system changes required — this edit only modifies files under `.claude/skills/`, which ship via the normal git update path (`scripts/remote-update.sh` → git pull). No new deps, no config migration, no env changes. Every machine that runs `/update` after this merges will pick up the new prompt text on next skill invocation.

## Agent Integration

No agent integration required — the four skills are already registered and invoked by the PM session via the existing SDLC router (`agent/sdlc_router.py` + `.claude/skills/sdlc/SKILL.md`). No new tools, no new MCP registrations, no `.mcp.json` changes. The prompts are loaded by the Claude Code harness at skill invocation time.

Integration validation for the smoke-test (AC #6): after the edit merges, invoke one of the four updated skills against a real target (e.g., `/do-pr-review` against an existing PR, or `/do-plan-critique` on an existing plan doc) and confirm:
1. The invocation succeeds end-to-end.
2. Every required output field from the pre-edit interface is present in the post-edit output.
3. No downstream consumer (SDLC router, parity test) reports a format error.

## Documentation

This is an internal prompt-text edit. No user-facing feature documentation is needed, but a brief note in the SDLC skill docs captures the audit.

### Feature Documentation
- [ ] Add a short note to `docs/features/sdlc-skills-audit.md` (or create if not present) recording: the four skills were audited for Opus 4.7 behavior on 2026-04-20, the three behavioral deltas checked, and the outcome (which skills were edited, which weren't). This gives future readers a pointer when `/do-plan-critique` or `/do-pr-review` is re-audited for a future Opus version.
- [ ] No update to `docs/features/README.md` index — this is an addendum to an existing feature doc (or a new audit-record doc, which does not need an index entry since it is not a feature).

### External Documentation Site
- [ ] N/A — repo does not publish to Sphinx/RTD/MkDocs for this area.

### Inline Documentation
- [ ] Not applicable — no code changes. Markdown edits are self-documenting.

## Success Criteria

- [ ] `/do-plan` reviewed: Phase 1 Step 1 expanded to a 3-step evidence-gathering checklist; vague hedges in instruction text replaced with direct directives; Phase 0.7 ToolSearch line preserved verbatim (AC #1 from issue).
- [ ] `/do-plan-critique` reviewed: synthesis step confirmed adequate for conciseness shift; an "emit empty sections literally" note added above Step 5; rationale for "no further changes" recorded in the PR body (AC #2).
- [ ] `/do-pr-review` reviewed: Step 5 format block promoted from "use this format" to hard "you MUST emit exactly this block" directive; explicit empty-section rule added; code-review.md Pre-Verdict Checklist tightened (AC #3).
- [ ] `daily-integration-audit` reviewed: Opus subagent brief rewritten into explicit self-contained structured block (FEATURE_TOPIC / SEED_DOC_PATH / VERIFICATION_PASS / OUTPUT_FORMAT / FINAL_LINE) (AC #4).
- [ ] Behavioral tool-announcement instructions (distinct from `ToolSearch("select:X")` schema-loading) identified and removed across the four skills. Output of grep confirming zero remaining instances in the four files (AC #5).
- [ ] Manual smoke-test of one updated Opus skill executed and documented in the PR body (AC #6).
- [ ] Grep verification: every structural field name present before the edit is still present after. Pre/post match counts recorded in PR body.
- [ ] Tests pass (`/do-test`) — expected to be a no-op since tests don't exercise prompt text.
- [ ] Documentation updated (`/do-docs`): the audit record in `docs/features/sdlc-skills-audit.md`.

## Team Orchestration

This is a Small-appetite prompt-edit. One builder, one validator. No specialist needed.

### Team Members

- **Builder (skill-edits)**
  - Name: `skill-edits-builder`
  - Role: Apply the per-skill edits from Technical Approach to the four SKILL.md files; run pre/post grep verification for field-name preservation.
  - Agent Type: `builder`
  - Resume: true

- **Validator (smoke-test)**
  - Name: `smoke-test-validator`
  - Role: Invoke one updated skill end-to-end against a real target; confirm output structure matches pre-edit contract; report pass/fail.
  - Agent Type: `validator`
  - Resume: true

- **Documentarian (audit record)**
  - Name: `audit-record-documentarian`
  - Role: Add audit-record note to `docs/features/sdlc-skills-audit.md`.
  - Agent Type: `documentarian`
  - Resume: true

### Available Agent Types

Default tier-1 types are sufficient: `builder`, `validator`, `documentarian`. No specialist needed.

## Step by Step Tasks

### 1. Pre-edit grep snapshot

- **Task ID**: pre-grep
- **Depends On**: none
- **Validates**: n/a (records baseline)
- **Informed By**: Risk 1 mitigation
- **Assigned To**: skill-edits-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `grep -n` for every structural field name listed in Risk 1 against the four skill files, save counts to a temp file (commit message will reference).

### 2. Edit `/do-plan`

- **Task ID**: build-do-plan
- **Depends On**: pre-grep
- **Validates**: field-name preservation via post-grep in task `validate-greps`
- **Informed By**: Research (conciseness + reduced-clarification deltas); Technical Approach section A
- **Assigned To**: skill-edits-builder
- **Agent Type**: builder
- **Parallel**: true (with steps 3, 4, 5)
- Expand Phase 1 Step 1 to a 3-step evidence-gathering checklist (read issue body, read Recon Summary, follow sibling issues).
- Scan Phase 1, Phase 3 for vague hedges in instruction text; replace with hard directives where intent is mandatory.
- Preserve Phase 0.7 `ToolSearch("select:WebSearch")` line verbatim.
- Do NOT touch Phases 0, 0.5, 1.5, 2, 2.5, 2.6, 2.7, 4.

### 3. Edit `/do-plan-critique`

- **Task ID**: build-critique
- **Depends On**: pre-grep
- **Validates**: field-name preservation via post-grep
- **Informed By**: Research (conciseness delta); Technical Approach section B
- **Assigned To**: skill-edits-builder
- **Agent Type**: builder
- **Parallel**: true
- Add one sentence above Step 5 asserting: "Emit every section header literally; empty categories emit '## Blockers\n\nNone.' — do not omit the header."
- Do NOT touch critic prompts in `CRITICS.md` (those run on Sonnet).
- Record rationale for minimal-change in the PR body.

### 4. Edit `/do-pr-review`

- **Task ID**: build-pr-review
- **Depends On**: pre-grep
- **Validates**: field-name preservation via post-grep; test `tests/unit/test_pr_review_audit.py` passes
- **Informed By**: Research (conciseness delta, hard-directive pattern); Technical Approach section C
- **Assigned To**: skill-edits-builder
- **Agent Type**: builder
- **Parallel**: true
- Step 5: promote format block to hard "you MUST emit exactly this block, every field present" directive.
- Add explicit empty-section rule for the three severity categories.
- `sub-skills/code-review.md` Pre-Verdict Checklist: add "Every row MUST be filled; blank cells invalidate the review" above the 12-row table.
- Preserve all `**File:**`, `**Code:**`, `**Severity:**`, `**Fix:**`, `**Issue:**` field names verbatim.

### 5. Edit `daily-integration-audit`

- **Task ID**: build-audit
- **Depends On**: pre-grep
- **Validates**: field-name preservation via post-grep
- **Informed By**: Research (reduced-clarification delta); Technical Approach section D
- **Assigned To**: skill-edits-builder
- **Agent Type**: builder
- **Parallel**: true
- Step 2: rewrite the Opus subagent brief into a self-contained structured block (FEATURE_TOPIC / SEED_DOC_PATH / VERIFICATION_PASS / OUTPUT_FORMAT / FINAL_LINE).
- Preserve the `SUMMARY: PASS=<n> WARN=<n> FAIL=<n>` machine-parseable footer verbatim.
- Do NOT touch Steps 1, 3, 4, Scheduling.

### 6. Post-edit grep verification

- **Task ID**: validate-greps
- **Depends On**: build-do-plan, build-critique, build-pr-review, build-audit
- **Assigned To**: skill-edits-builder
- **Agent Type**: builder
- **Parallel**: false
- Re-run the grep snapshot from task `pre-grep`; confirm every structural field name has the same or greater count.
- If any field name count decreased: treat as a BLOCKER and revert/correct the offending edit before proceeding.

### 7. Smoke-test one updated skill

- **Task ID**: validate-smoke
- **Depends On**: validate-greps
- **Assigned To**: smoke-test-validator
- **Agent Type**: validator
- **Parallel**: false
- Invoke one of the four updated skills end-to-end against a real target (recommend `/do-plan-critique` on an existing plan because it's the fastest round-trip).
- Spot-check that every required output field from the pre-edit interface is present.
- Record pass/fail in the PR body, including the invocation command and the first 200 chars of the output.

### 8. Run test suite

- **Task ID**: validate-tests
- **Depends On**: validate-greps
- **Assigned To**: smoke-test-validator
- **Agent Type**: validator
- **Parallel**: true (with validate-smoke)
- Run `pytest tests/unit/ -n auto` to confirm no test regressions.
- Expected: no changes (test suite does not exercise prompt text).

### 9. Documentation

- **Task ID**: document-audit
- **Depends On**: validate-greps
- **Assigned To**: audit-record-documentarian
- **Agent Type**: documentarian
- **Parallel**: true (with validate-smoke, validate-tests)
- Create or append to `docs/features/sdlc-skills-audit.md` with a short audit-record entry (date, four skills, three deltas checked, which were edited, which weren't, link to this plan and PR).
- If `docs/features/sdlc-skills-audit.md` does not exist, create it with a minimal header and this as the first entry.

### 10. Final validation

- **Task ID**: validate-all
- **Depends On**: validate-smoke, validate-tests, document-audit
- **Assigned To**: smoke-test-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all six acceptance criteria from the issue are addressed.
- Verify Success Criteria checklist items are all checkable against the PR.
- Generate final report as the PR body's validation section.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Field preservation: File field in do-pr-review | `grep -c '\*\*File:\*\*' .claude/skills/do-pr-review/SKILL.md .claude/skills/do-pr-review/sub-skills/code-review.md` | output > 0 each |
| Field preservation: Severity field | `grep -c '\*\*Severity:\*\*' .claude/skills/do-pr-review/SKILL.md .claude/skills/do-pr-review/sub-skills/code-review.md` | output > 0 each |
| Field preservation: Verdict strings in critique | `grep -E 'READY TO BUILD\|NEEDS REVISION\|MAJOR REWORK' .claude/skills/do-plan-critique/SKILL.md` | output contains all three |
| Field preservation: Audit SUMMARY footer | `grep 'SUMMARY: PASS=' .claude/skills/daily-integration-audit/SKILL.md` | exit code 0 |
| Opus model routing unchanged | `grep -n 'opus' .claude/skills/sdlc/SKILL.md` | output contains PLAN, CRITIQUE, REVIEW rows |
| No version-pin accidentally added | `grep 'claude-opus-4-7' .claude/skills/` | exit code 1 (none) |
| Tool-announcement phrasing removed | `grep -i -E '(first announce you have\|verify you have access to)' .claude/skills/do-plan/SKILL.md .claude/skills/do-plan-critique/SKILL.md .claude/skills/do-pr-review/SKILL.md .claude/skills/daily-integration-audit/SKILL.md` | exit code 1 (none) |
| ToolSearch schema loading preserved | `grep 'ToolSearch("select:WebSearch")' .claude/skills/do-plan/SKILL.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **`docs/features/sdlc-skills-audit.md` — create new or append to existing?** The directory listing shows `sdlc-skills-audit.md` already exists. Should the audit record be appended to the existing doc (preserving prior audit-record entries) or should a new dated sub-section be added? Default: append a new dated sub-section with a `## Opus 4.7 audit (2026-04-20)` heading. Confirm or override.

2. **Smoke-test skill choice — `/do-plan-critique` or `/do-pr-review`?** The plan recommends `/do-plan-critique` because the round-trip is faster (runs against a plan doc, no PR needed) and the synthesis step is the most conciseness-sensitive surface. Alternative: `/do-pr-review` gives a richer validation because it exercises the most structural-output fields. Default: `/do-plan-critique` on this plan doc itself (self-referential but fast). Confirm or override.

3. **Behavioral tool-announcement phrasing — pre-audit scan.** Before editing, the builder should grep the four files for patterns like "first announce," "verify you have access to," "let the user know you have," "check that [tool] is available." The issue and this plan assume some such phrasing exists. If the pre-edit grep returns zero matches across the four files, AC #5 is satisfied trivially and the PR body should note "no tool-announcement phrasing found — no edits required for this criterion." Confirm this fallback is acceptable, or whether more aggressive rewriting is warranted.
