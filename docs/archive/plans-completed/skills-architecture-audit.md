---
status: Ready
type: chore
appetite: Medium
owner: Tom Counsell
created: 2026-07-03
tracking: https://github.com/tomcounsell/ai/issues/1883
last_comment_id: 4880668532
---

# Skills Architecture Audit

## Problem

The skill fleet has grown by accretion: 46 global skills in `.claude/skills-global/` and 14 project-only skills in `.claude/skills/`, roughly 17,000 lines of instruction text. Each skill was added one business requirement at a time, which is exactly the accretion trap Anthropic's agent-decomposition guidance warns about. Nobody has stepped back to ask, per skill: is this the right primitive (skill vs. workflow vs. subagent), is it the right size, and which model should run it?

**Current behavior:**
- The `/do-skills-audit` lint was renovated as groundwork for this audit (commit `56124515`, 2026-07-03): 20 deterministic rules over both repo roots plus user-level orphan detection, a revived nightly FAIL→issue reflection, and an `--arch` rubric at `.claude/skills-global/do-skills-audit/references/rubric.md`. The lint now catches hygiene (rot, husks, budget, trigger collisions) — but the architecture judgments themselves (dispositions, model tiers) remain this plan's job. A skill can pass every lint rule and still be the wrong shape.
- Tiny skills with overlapping domains coexist (`do-debrief` + `do-voice-recording` are both TTS; `analyze` / `grill-me` / `zoom-out` are all "think harder" prompts; four `audit-*` skills share one skeleton).
- Very large multi-stage skills (`do-pr-review` 1,627 lines total, `do-plan` 1,190, `do-build` 879, `x-com` 728, `linkedin` 800, `setup` 620) run as single-context monoliths even where their stages are independent and could pipeline as a Workflow or isolate as subagents.
- Every skill runs on whatever model the session happens to use. Only 5 skills carry any `model:` hint. Mechanical skills burn frontier-model tokens; judgment-heavy skills (critique, war-room) sometimes run on cheaper tiers.
- Drift artifacts exist: `do-design-review`, `audit-next-tool`, `get-telegram-messages`, `searching-message-history`, and `sentry-cli` survive only as user-level copies in `~/.claude/skills/` with no repo source (now auto-flagged by lint rule 20); their dispositions are this audit's to make.

**Desired outcome:**
A per-skill architecture audit that produces, for every one of the 60 skills: (1) concrete improvement suggestions, (2) a disposition — keep / merge-with-named-sibling / split / convert-to-Workflow / convert-to-subagent / retire, and (3) a recommended model tier (sonnet / opus / fable) with rationale. Delivered as a durable report plus one follow-up GitHub issue per accepted consolidation, so execution can proceed slug-by-slug.

## Freshness Check

**Baseline commit:** `56124515` (originally); **re-verified 2026-07-10** at the current `main` HEAD before finalization.
**Issue filed at:** plan-initiated (tracking issue created alongside this plan; no pre-existing issue to re-verify)
**Disposition:** Minor drift — the renovation groundwork landed and PR #1894 merged; premises hold and the audit re-runs live inventory at execution, so nothing blocks build.

**File:line references re-verified:** Inventory re-counted at HEAD on 2026-07-10 — **46 global + 14 project-only skills = 60** confirmed (`.claude/skills/_shared` is a shared reference dir, not a skill). The audit's constitution `.claude/skills-global/do-skills-audit/references/rubric.md` still present. Fleet count unchanged since plan time; no net-new or removed skills.

**Cited sibling issues/PRs re-checked (2026-07-10):**
- #1783 / PR #1806 — "Generalize all global skills to be repo-agnostic" — merged 2026-06-26. The skill-context seam (probe sentences, `docs/sdlc/*.md` addenda) is the established mechanism; this audit respects it, does not undo it.
- #1299, #1395 — skills-audit reflection wiring (FAIL findings file issues via the two-run gate) — closed; the nightly reflection runs the *lint* audit, not an architecture audit.
- **PR #1894** — "Renovate skill fleet (60 skills)" — now **MERGED 2026-07-05** (was in-flight at plan time). Its post-renovation disposition recommendations (tracking-issue comment `4880668532`) are folded in below as pre-computed seed input.

**Commits touching skill dirs since plan (self-correcting):** `do-merge`, `sdlc`, `do-sdlc`, and husk-prune (#1909) edits landed on skills since 2026-07-03. These shift exact line counts for a few decompose candidates but do not change the audit's shape — Task 1 re-runs `--json --no-sync` against live HEAD, so the inventory substrate is always current at execution.

**Active plans in `docs/plans/` overlapping this area:** none — recent active plans touch runtime systems (delivery paths, session lifecycle, reflection scheduler), not skills.

**Notes:** Groundwork performed between plan creation and finalization (issue comments 4877617569, 4877872940, and 4880668532 — all incorporated): a blind-draft comparison of the existing lint, a full renovation shipped in `56124515`, then the 60-skill fleet renovation (PR #1894) whose leftover disposition recommendations feed this audit. Lint baseline at renovation (`--no-sync`): 60 skills · 828 PASS · 73 WARN · 7 FAIL (all seven FAILs are line-count monoliths — this plan's decompose candidates).

## Prior Art

- **#1783 / PR #1806**: Generalize all global skills to be repo-agnostic — succeeded. Established the probe-sentence + skill-context convention and the `rule_13_coupling_signals` guard. This audit's merge/split recommendations must preserve probe sentences and the global/project split.
- **#1299**: Skills-audit reflection files GitHub issues on FAIL findings (two-run gate) — succeeded. Gives us the pattern for audit-finding → issue automation this plan reuses.
- **#1416/#1417/#1474/#1618**: Doc-reference drift issues from the skills-audit move to `skills-global/` — evidence that skill moves/renames leak stale references; any merge executed as follow-up must include a doc sweep and `RENAMED_REMOVALS` entries.
- **/do-skills-audit** (renovated in `56124515` as this plan's groundwork): 20-rule deterministic lint over both repo roots + user-level orphan detection + Anthropic best-practices sync + the `--arch` rubric this audit executes. The blind-draft comparison that motivated the renovation is recorded on the tracking issue (comment 4877617569).

## Research

Primary source: Anthropic engineering talk, "Right agentic primitives at the right time" (youtu.be/mWvtOHlZM-I — Will, Applied AI, Stock Pilot case study). Full transcript reviewed; findings that shape this audit's rubric:

**Queries used:**
- Full transcript of youtu.be/mWvtOHlZM-I via `valor-youtube-transcribe`

**Key findings:**
- **Skills vs. always-on context:** "Leave the system prompt only for the information Claude needs in its mind *regardless of the task*. Skills package information Claude needs *some of the time*." → Audit check: does each skill body contain always-true repo policy that belongs in CLAUDE.md, or one-time task guidance that belongs in the skill?
- **Only two reasons for a subagent:** (1) parallelism ("throw a lot of Claude at a problem"), (2) fresh-mind context isolation ("I don't want the Claude that wrote the code to review the code"). Everything else: fold back into the main agent — "frontier models have gotten intelligent enough that you just don't need as many subagents." → Audit check: every convert-to-subagent recommendation must cite one of the two reasons; every existing subagent dispatch inside a skill gets the same test in reverse.
- **The named failure mode of decomposition** is orchestrator↔subagent communication breakdown (eval F2: subagent right, handoff wrong). → Any Workflow-conversion recommendation must specify the structured handoff (schema), not just "split it."
- **Primitives beat custom tooling:** the demo went 12 tools → 3 (bash/read/write), 400-line prompt → 15 lines, 200K+ tokens/task → dramatically less, eval pass 62% → ~92%. Ladder: Claude Code primitives → local custom tools → MCP only for multi-client standardized toolsets. → Audit check: skills that wrap what bash + the valor-* CLIs already do get flagged.
- **Hill climbing:** baseline → change → re-measure; efficiency (tokens, turns, latency) is a pass/fail criterion, not a nicety; "turn count staying flat is fine if tokens and cost drop." → The audit records a token-cost estimate per skill (body size + typical sub-file loads) so consolidations can be verified as wins, not vibes.
- **Model/effort:** the talk's one concrete setting — high effort on the strongest model as a set-and-forget default for *coding* work; latency is tradeable for quality on high-intelligence tasks, and cheap mechanical work shouldn't ride the expensive path. → Basis for the three-tier recommendation this audit assigns.

## Spike Results

### spike-1: Inventory and size census
- **Assumption**: "The fleet is large and bimodal — many tiny skills, a few monoliths."
- **Method**: code-read (`wc -l` census of every SKILL.md + sub-files)
- **Finding**: 46 global + 14 project-only skills. 18 skills ≤ 100 total lines; 8 skills > 600 total lines (do-pr-review 1,627 · claude-standards 1,649 · do-skills-audit 1,572 · do-plan 1,190 · frontend-design 938 · do-build 879 · do-presentation 812 · linkedin 800 · x-com 728 · do-test 679). Bimodal confirmed.
- **Confidence**: high
- **Impact on plan**: cluster-based analyst fan-out (below) sized to this census; merge lens focuses on the ≤100-line tail, decompose lens on the >600-line head.

### spike-2: Duplicate and orphan detection
- **Assumption**: "Some skills exist in both directories or only at user level."
- **Method**: code-read (`comm` across dirs, `ls ~/.claude/skills/`)
- **Finding**: originally read `do-skills-audit`/`do-test` in both dirs as an intentional scoped-variant pattern — **REFUTED on deep read**: both were husks from the skills-global migration (stale `__pycache__`/metadata; an orphaned `PYTHON.md` nothing loaded). Deleted in the `56124515` renovation; lint rule 19 now FAILs any future husk. User-level orphans confirmed and expanded: `do-design-review`, `audit-next-tool`, `get-telegram-messages`, `searching-message-history`, `sentry-cli` (rule 20 flags them each run; `sentry` additionally has a diverged user copy).
- **Confidence**: high (mechanically enforced now)
- **Impact on plan**: inventory reconciliation is automated by the renovated lint; the audit consumes rule 19/20 findings and owns the orphan dispositions. Orphan removals are candidate `RENAMED_REMOVALS` entries.

### spike-3: Model-hint precedent
- **Assumption**: "Per-skill model recommendations have somewhere to live."
- **Method**: code-read (`grep -l 'model:'` across skill bodies)
- **Finding**: 5 skills already carry model guidance (`do-plan-critique`, `do-test`, `do-sdlc`, `setup`, `imagine-agent`); `do-sdlc` already dispatches stages to opus/sonnet per stage. Convention exists; the audit generalizes it.
- **Confidence**: high
- **Impact on plan**: recommendation output format is a `model:` guidance line per skill + a dispatch table amendment in `do-sdlc`/agent definitions, not a new mechanism.

## Data Flow

1. **Entry point**: operator runs the audit (a one-shot orchestrated run in a local Claude Code session, per Step by Step Tasks below).
2. **Inventory stage**: deterministic script output from the renovated `/do-skills-audit --json --no-sync` (both roots, size census, budget stats, rot/junk/collision WARNs, husk FAILs, user-orphan flags) → normalized inventory JSON.
3. **Analyst fan-out**: one analyst per skill cluster (8 clusters, below) reads every SKILL.md + sub-files in its cluster and emits structured findings per skill: improvements[], disposition{action, target, rationale}, model_tier{tier, rationale}, token_cost_estimate.
4. **Adversarial verify**: every non-"keep" disposition (merge/split/convert/retire) goes to a fresh-context verifier prompted to refute it (the video's fresh-mind rule applied to our own audit). Majority-refuted dispositions downgrade to "keep + note".
5. **Synthesis**: single synthesizer merges cluster reports, resolves cross-cluster merges (e.g., a voice skill merging into a media skill spans clusters), produces the report.
6. **Output**: `docs/audits/skills-architecture-audit-2026-07.md` (report) + one `gh issue create` per accepted consolidation/conversion + a summary comment on the tracking issue.

## Appetite

**Size:** Medium

**Team:** Solo operator (Tom) + orchestrated analyst/verifier subagents

**Interactions:**
- PM check-ins: 1-2 (disposition sign-off before issues are filed)
- Review rounds: 1 (report review)

The audit itself is read-only; the appetite is spent on analysis quality and the human disposition review, not code.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | Filing follow-up issues |
| Lint audit runs (exit 0 or 1; 1 = findings, not breakage) | `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --json --no-sync > /dev/null; test $? -le 1 && echo ok` | Inventory stage reuses its JSON output |

## Solution

### Key Elements

- **Rubric (the audit's constitution)**: **shipped** — lives at `.claude/skills-global/do-skills-audit/references/rubric.md` (written during the renovation from this plan's Research section). Analysts load that file verbatim; the plan text below is its summary, the skill file is authoritative. Five lenses applied to every skill:
  1. **Context economy** — is body content needed *every* invocation, or should it progressively disclose into sub-files? Is any content always-true policy that belongs in CLAUDE.md instead?
  2. **Primitive fit** — SKILL (task guidance, single context) vs. WORKFLOW (multi-stage with independent stages needing deterministic control flow) vs. SUBAGENT (only for parallelism or fresh-mind isolation — must cite which) vs. SCRIPT (deterministic logic pretending to be prose — belongs in a `scripts/` file the skill calls).
  3. **Consolidation** — overlapping trigger domains, shared skeletons, or one skill being a thin wrapper over another (merge direction must be named: which survives, which becomes a section or an argument).
  4. **Model tier** — **sonnet**: mechanical/deterministic (script-runner skills, formatting, message I/O); **opus**: standard multi-step reasoning (build, docs, test triage); **fable**: frontier judgment where a wrong call is expensive (plan critique/war-room, architecture analysis, adversarial review, client-facing agent design).
  5. **Efficiency** — estimated tokens pulled into context per invocation (body + eagerly-loaded sub-files); flag skills whose description alone does documentation work (>200 chars) and skills that inline data a bash one-liner could fetch.
- **Cluster map** — 8 analyst clusters sized so no analyst reads more than ~3,500 lines:
  1. *SDLC core*: do-plan, do-plan-critique, do-build, do-patch, do-merge, do-sdlc, sdlc (project)
  2. *SDLC periphery*: do-test, do-pr-review, do-docs, do-issue, do-investigation-issue
  3. *Audit family*: do-skills-audit, audit-hooks, audit-models, audit-tools, do-integration-audit, do-oop-audit, new-audit-skill (+ orphan audit-next-tool)
  4. *Design & media*: do-design-audit, do-design-system, frontend-design, pencil-design, mermaid-render, do-presentation, do-debrief, do-voice-recording (+ orphan do-design-review)
  5. *Comms & channels* (project-heavy): telegram, email, google-workspace, checking-system-logs, reading-sms-messages, linkedin, x-com, authenticity-pass, sentry (+ orphans get-telegram-messages, searching-message-history, sentry-cli)
  6. *Thinking & meta*: analyze, grill-me, zoom-out, ontologies, weekly-review, reclassify, skillify, new-skill, pthread, tdd, deepen, observability, claude-standards
  7. *CMA & external*: imagine-agent, build-agent, do-discover-paths, computer-use, officecli, ebook-ingest
  8. *Machine ops* (project): setup, prime, update, do-deploy, do-deploy-example
- **Seed hypotheses** (analysts must confirm or refute, not rubber-stamp):
  - Merge: do-debrief + do-voice-recording (both TTS; debrief = collect+draft+speak, voice-recording = speak — one skill, two entry modes); do-design-audit absorbs the orphan do-design-review; the audit-* family shares one skeleton — candidates for a single parameterized `audit` skill or generation from `new-audit-skill` templates; analyze/grill-me/zoom-out → one "think" skill with modes is *plausible* but they have distinct triggers — verifier must weigh trigger-precision loss.
  - Decompose: do-pr-review (multi-dimension review + screenshot capture is a natural Workflow: dimensions fan out, findings adversarially verified — mirrors the built-in /code-review shape); do-build (already delegates to builder agents; audit whether its 526-line body is orchestration prose that should be a Workflow script); linkedin/x-com (800/728 lines of mixed reference + procedure — split reference tables into sub-files with progressive disclosure rather than convert; browser-driving is inherently sequential, a Workflow buys nothing); setup (620 lines, deterministic — most of it should be a script the skill runs).
  - Model tiers (seed, per-skill final call is the audit's job): fable → do-plan-critique, analyze, grill-me, imagine-agent, do-plan (architecture judgment); opus → do-build, do-pr-review, do-docs, do-design-system, frontend-design; sonnet → telegram, email, reading-sms-messages, update, do-skills-audit, weekly-review, do-voice-recording, get-telegram-messages-style I/O skills.
- **Pre-computed disposition input (PR #1894 renovation pass, tracking comment `4880668532`)**: the 60-skill renovation surfaced concrete disposition recommendations that were deliberately *not* executed (frontmatter/placement changes were out of scope). These are handed to the audit as **verifier claims to refute**, exactly like the seed hypotheses — never as analyst defaults. Grouped:
  - *Placement / seam*: `do-sdlc` (doc says Bucket C project-only but it lives in `skills-global/` and syncs everywhere — resolve the contradiction); `do-discover-paths`, `do-pr-review` sub-skill, `reclassify`, `analyze`, `do-test` all invoke ai-repo toolchain from global bodies via signals rule 13 misses — probe-guard, Bucket C move (+ RENAMED_REMOVALS), or extend the coupling-signal set.
  - *Merge / consolidation*: audit-models → do-oop-audit; do-design-audit ≈ do-design-review (orphan); linkedin + x-com shared skeleton; skillify + new-skill; new-audit-skill → new-skill `references/audit-template.md`; claude-standards ↔ do-skills-audit overlap; audit-tools vs audit-next-tool.
  - *Rewrite / cleanup*: pthread (pseudo-code vs real Agent API; CLAUDE.md Principle 8 overlap); x-com `references/posting.md` duplicate "Iterate" sections; do-plan-critique dead version-history + repo-policy recitation; sdlc G1–G7 guard-table duplication; do-merge Steps 1–3 scripts extraction; setup Phases 5/6 vs `/update --full`; prime stale cross-repo comment.
  - *Model-tier proposals*: build-agent → fable (client-facing, billing consequences); authenticity-pass → sonnet (classification).
  - *User-level orphans needing disposition*: audit-next-tool, do-design-review, get-telegram-messages, searching-message-history, sentry-cli (no repo source) + diverged user-level `sentry` copy.
  - *Always-true policy in skill bodies (CLAUDE.md candidates)*: pthread ↔ Principle 8; linkedin's em-dash prohibition (kept inline because drafter subagent prompts need it verbatim).
- **Disposition report**: one table row per skill (60 rows): current lines/files, cluster, findings summary, disposition, model tier, token estimate, verifier verdict. Dispositions are recommendations; nothing is executed in this slug.
- **Issue generation**: after human sign-off on the report, one `gh issue create` per accepted merge/split/conversion, each self-contained with the affected paths, the `RENAMED_REMOVALS` requirement, and the doc-sweep requirement (prior art: #1416-#1618 drift). The revived nightly skills-audit reflection will independently file issues for the 7 standing rule-1 FAILs after 2 consecutive runs (streak gate) — the issue-generation step must check for those auto-filed issues and absorb or close them against the audit's own decompose issues rather than leaving duplicates.

### Flow

Operator invokes audit → inventory script (deterministic) → 8 analyst subagents in parallel (structured JSON findings per skill) → adversarial verifier per non-keep disposition (fresh context, prompted to refute) → synthesis report written to `docs/audits/` → **human disposition review (checkpoint)** → accepted items become GitHub issues → tracking issue updated.

### Technical Approach

- Run as an orchestrated multi-agent pass in a local session (Workflow tool or parallel Task dispatch — operator's session decides; the plan mandates only: parallel analysts, structured findings schema, fresh-context verification, synthesis barrier before report).
- Reuse `/do-skills-audit --json --no-sync` output as the inventory substrate; the architecture audit layers on top, it does not fork the lint.
- Findings schema per skill: `{skill, dir, lines, files, findings[], disposition: {action: keep|merge|split|workflow|subagent|retire, target?, rationale}, model: {tier: sonnet|opus|fable, rationale}, est_tokens}` — schema-enforced so synthesis is mechanical.
- Verifier prompt is refutation-framed with the two-reasons rule and trigger-precision loss as explicit refutation grounds; ≥2-of-3 refute → downgrade to keep.
- Model-tier recommendations land as: (a) a column in the report, (b) for skills the SDLC router dispatches, a proposed amendment to `do-sdlc`'s stage→model table, (c) for standalone skills, a `model:` guidance line in frontmatter *proposed in the follow-up issues*, not applied here.
- Respect the repo-agnostic convention from #1783: no recommendation may reintroduce repo coupling into a global skill body; merges of a global + project pair must keep the probe-sentence seam.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this slug produces a report and issues; it modifies no runtime code.

### Empty/Invalid Input Handling
- [ ] Analyst agents that return malformed/empty findings are retried once, then their cluster is marked INCOMPLETE in the report (no silent gaps — the report must enumerate any skill left unanalyzed).
- [ ] Orphan skills with no repo source (do-design-review, audit-next-tool) are analyzed from their user-level copies and flagged as inventory anomalies, not skipped.

### Error State Rendering
- [ ] The synthesis step fails loudly (report section "Coverage gaps") if the per-skill row count ≠ 60 + orphans, rather than shipping a partial report that reads as complete.

## Test Impact

No existing tests affected — this slug is a read-only analysis producing a report document and GitHub issues; no runtime code, skill bodies, or test fixtures change in this plan's scope. Each issue this audit files carries its own Test Impact section for the work it describes.

## Rabbit Holes

- **Executing merges in this slug.** A merge touches hardlink sync (`RENAMED_REMOVALS`), doc references (prior art shows they leak), and every machine's `~/.claude/skills/`. Strictly follow-up work.
- **Building a permanent "architecture lint" into `audit_skills.py` now.** Architecture judgment is not deterministic; wait until the first audit shows which checks are mechanizable.
- **Re-litigating the global/project split.** #1783 settled it; the audit works within it.
- **Auditing `.claude/agents/` and MCP servers with the same depth.** The agent roster deserves the same treatment but is a separate slug; this audit only notes where a skill's disposition *implies* agent-roster changes.
- **Token-perfect cost accounting.** Line-count × rough tokens-per-line is sufficient to rank; do not build instrumentation.

## Risks

### Risk 1: Recommendation quality is anchored on seed hypotheses
**Impact:** Analysts rubber-stamp the seeds instead of judging; audit becomes confirmation bias with extra steps.
**Mitigation:** Seeds are given to verifiers as *claims to refute*, not to analysts as defaults; analysts receive only the rubric and their cluster's files.

### Risk 2: Merge recommendations destroy trigger precision
**Impact:** Consolidated skills fire less reliably (a merged "think" skill matches worse than three sharp descriptions), quietly degrading daily use.
**Mitigation:** Trigger-precision loss is an explicit refutation ground in verification; every merge recommendation must include the merged description text and argue it preserves the trigger surface.

### Risk 3: Model-tier table goes stale as models change
**Impact:** Recommendations reference sonnet/opus/fable capabilities that shift with releases.
**Mitigation:** Tier rationale is written against task *properties* (mechanical / multi-step / frontier-judgment), not model names; the tier→model mapping lives in one place (report preamble + do-sdlc table).

## Race Conditions

No race conditions identified — the audit is a read-only pass over a git tree at a pinned commit, run in a single operator session; report and issues are written once at the end. Concurrent SDLC sessions touching `docs/plans/` do not contend with `docs/audits/`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #TBD] Executing any merge/split/conversion disposition — each accepted disposition becomes its own issue filed by this audit's final step (issue numbers created at that time; the tracking issue will index them).
- [EXTERNAL] Removing the orphan `~/.claude/skills/do-design-review` from other machines by hand — the correct mechanism is a `RENAMED_REMOVALS` entry shipped by a follow-up slug and propagated by `/update`; a human confirms propagation on the bridge machine.
- [SEPARATE-SLUG #TBD] Agent-roster (`.claude/agents/`) and MCP-server audit under the same rubric — noted in the report where skill dispositions imply it; filed as its own issue by the final step.

## Update System

No update system changes required in this slug — the report lands in `docs/audits/` (not synced) and issues live on GitHub. However, the plan explicitly requires every *follow-up* consolidation issue to include: (a) a `RENAMED_REMOVALS` entry in `scripts/update/hardlinks.py` for any renamed/merged/retired skill, and (b) a docs sweep for stale skill-path references (prior art #1416/#1618). This requirement is written into the issue template used by the issue-generation step.

## Agent Integration

No agent integration required — no MCP servers, `.mcp.json` entries, or bridge changes. The audit runs with existing primitives (Read/Grep/Bash + subagent dispatch). Model-tier wiring proposals target skill frontmatter and the `do-sdlc` dispatch table in follow-up slugs.

## Documentation

### Feature Documentation
- [ ] Create `docs/audits/skills-architecture-audit-2026-07.md` — the audit report itself (durable artifact)
- [ ] Update `docs/features/do-skills-audit.md` with a short "Architecture audit" section pointing at the report and describing the rubric, so the lint and the architecture pass are documented side by side

### Inline Documentation
- [ ] Report preamble documents the rubric and the tier→model mapping so future audits re-run against the same constitution

## Success Criteria

- [ ] Report exists at `docs/audits/skills-architecture-audit-2026-07.md` with exactly one row per skill (60 + orphans), each row carrying findings, disposition, model tier, and verifier verdict
- [ ] Every non-keep disposition shows a fresh-context verifier verdict (CONFIRMED or downgraded)
- [ ] Every merge recommendation names the surviving skill and includes proposed merged description text
- [ ] Every convert-to-subagent/Workflow recommendation cites parallelism or fresh-mind isolation explicitly
- [ ] Model tier assigned to all 60 skills with task-property rationale
- [ ] Inventory anomalies (both-dir variants, user-level orphans) are dispositioned
- [ ] After human sign-off: one GitHub issue per accepted disposition, each with RENAMED_REMOVALS + doc-sweep requirements
- [ ] Tracking issue updated with report link and issue index
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

- **Analyst (per cluster ×8)**
  - Name: cluster-analyst-{1..8}
  - Role: read every skill in one cluster, emit schema-conforming findings
  - Agent Type: general-purpose (read-only instructions)
  - Resume: false (fresh context is the point)

- **Verifier (per non-keep disposition)**
  - Name: disposition-verifier-{n}
  - Role: refute one disposition from a fresh context using the two-reasons rule and trigger-precision grounds
  - Agent Type: validator
  - Resume: false

- **Synthesizer**
  - Name: audit-synthesizer
  - Role: merge cluster findings + verdicts into the report; enforce coverage completeness
  - Agent Type: general-purpose
  - Resume: false

## Step by Step Tasks

### 1. Inventory & rubric
- **Task ID**: build-inventory
- **Depends On**: none
- **Validates**: report "Inventory" section row count == fleet size
- **Informed By**: spike-1, spike-2
- **Parallel**: false
- Run the renovated `/do-skills-audit --json --no-sync`; normalize into inventory JSON (both roots + rule-20 user-level orphans; husks are already lint FAILs, not audit rows)
- Copy the rubric summary + tier definitions from `.claude/skills-global/do-skills-audit/references/rubric.md` (already written) into the report skeleton preamble

### 2. Analyst fan-out
- **Task ID**: build-analysis
- **Depends On**: build-inventory
- **Assigned To**: cluster-analyst-{1..8}
- **Parallel**: true
- Each analyst reads its cluster's SKILL.md + sub-files, applies the rubric, returns schema-conforming findings; retry once on malformed output, else mark cluster INCOMPLETE

### 3. Adversarial verification
- **Task ID**: validate-dispositions
- **Depends On**: build-analysis
- **Assigned To**: disposition-verifier-{n}
- **Agent Type**: validator
- **Parallel**: true
- One fresh-context refutation pass per non-keep disposition; seed hypotheses from this plan are handed to verifiers as claims to refute

### 4. Synthesis & report
- **Task ID**: build-report
- **Depends On**: validate-dispositions
- **Assigned To**: audit-synthesizer
- **Parallel**: false
- Merge findings + verdicts; resolve cross-cluster merges; write `docs/audits/skills-architecture-audit-2026-07.md`; fail loudly on coverage gaps

### 5. Human disposition review (checkpoint)
- **Task ID**: review-checkpoint
- **Depends On**: build-report
- **Parallel**: false
- Present the disposition table to Tom; collect accept/reject per non-keep disposition. **Hard stop — no issues filed without sign-off.**

### 6. Issue generation & docs
- **Task ID**: build-issues
- **Depends On**: review-checkpoint
- **Agent Type**: documentarian
- **Parallel**: false
- File one issue per accepted disposition (template includes RENAMED_REMOVALS + doc-sweep requirements); update `docs/features/do-skills-audit.md`; update tracking issue with report link and issue index

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-issues
- **Agent Type**: validator
- **Parallel**: false
- Run Verification table; confirm all Success Criteria

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Report exists | `test -f docs/audits/skills-architecture-audit-2026-07.md && echo ok` | output contains ok |
| Full coverage | `grep -c '^| ' docs/audits/skills-architecture-audit-2026-07.md` | output > 60 |
| Model tier on every row | `grep -Ec 'sonnet\|opus\|fable' docs/audits/skills-architecture-audit-2026-07.md` | output > 59 |
| No skill bodies changed in this slug | `git diff --stat main -- .claude/skills .claude/skills-global \| tail -1` | output does not contain changed |
| Auditor self-audit green | `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --no-sync --skill do-skills-audit > /dev/null; echo $?` | output contains 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Resolved Decisions

The four open questions raised during drafting are resolved on their documented defaults for this slug — none blocks the read-only audit's execution; each governs downstream policy the audit merely records. Revisit at the disposition-review checkpoint (Task 5) if the operator wants to override.

1. **Auto-apply threshold** — *Everything waits for human sign-off.* No issues are filed before the Task 5 checkpoint, including verifier-CONFIRMED trivial dispositions. Preserves the single hard stop; trivial items are cheap to approve in the same review.
2. **Model-tier wiring** — *Advisory only for this slug.* Recommendations land as a report column + proposed frontmatter `model:` guidance + a `do-sdlc` stage→model table amendment, all deferred to follow-up issues. A stronger mechanism (session runner reading a tier field to switch models) is explicitly out of scope and can be its own issue if the first pass proves demand.
3. **Fable tier scope** — *Frontier-judgment default; client-facing skills default to fable for quality.* Consistent with PR #1894's `build-agent → fable` proposal (client-facing launch has billing/account consequences where a wrong call is expensive). Per-skill final call remains the audit's job with task-property rationale.
4. **Audit cadence** — *One-shot for now.* No scheduled re-run is wired in this slug. Whether to schedule a quarterly re-run (reflection or cron) is deferred to a follow-up decision once the first pass proves the rubric out.
