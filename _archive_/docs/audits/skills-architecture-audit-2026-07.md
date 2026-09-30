# Fleet-Wide Skills Architecture Audit — 2026-07

Tracking issue: [#1883](https://github.com/tomcounsell/ai/issues/1883)
Plan: `docs/plans/skills-architecture-audit.md`
Rubric: `.claude/skills-global/do-skills-audit/references/rubric.md`

## Summary

This audit covers **65 rows** — 60 live skills plus 5 tracking-artifact orphans
(`audit-next-tool`, `do-design-review`, `get-telegram-messages`, `searching-message-history`,
`sentry-cli`) that appeared in prior tracking notes but do not exist as skill directories on
disk. Eight cluster analysts (one per domain: SDLC core, SDLC periphery, Audit family, Design &
media, Comms & channels, Thinking & meta, CMA & external, Machine ops) each read only the rubric
and their cluster's files, producing 65 findings rows with proposed dispositions. Ten of those
rows proposed a non-`keep` action (6 script extractions, 1 merge, 1 workflow conversion, and 4
"retire" claims on the orphans); every one of the ten went to a fresh-context adversarial
verifier whose job was to try to refute it. **All ten were refuted.** Zero fleet-restructuring
actions (merge / split / workflow / subagent / script / retire) survived adversarial review this
cycle.

That is a real, load-bearing result, not a null finding. The bulk of the restructuring work this
audit would have recommended was already executed by PR #1894's prior renovation (SKILL.md/body
splits, template dedup, skill-context probe adoption, model-tier corrections) — several analyst
rows below are explicitly "CONFIRMED already fixed" against stale PR #1894 tracking notes. The
adversarial layer's actual job this cycle was catching analysts who checked those stale
pre-computed claims against files that had moved on since, and catching four ghost dispositions
(claimed "retire this skill" targets that were never skills, or were merged away years ago and
should be a documentation fix, not a live disposition). The fleet is healthy: every live skill
this pass keeps its current shape. What remains are ~14 minor findings noted below (script-worthy
snippets, a few shared-reference extraction opportunities, doc drift) that are candidates for
small, separately-scoped follow-up issues — not executed here, per this audit's own No-Gos.

Headline numbers: 65/65 rows covered, 0 restructuring actions survived, 10 disputed dispositions
all downgraded to keep, ~14 minor script/consolidation findings noted as future minor cleanup,
6 cross-cluster observations flagged for human triage.

## Rubric & tier-mapping preamble

The rubric applies five lenses per skill, with every non-`keep` disposition subjected to
adversarial verification by a fresh-context reviewer whose default posture is to refute:

1. **Context economy.** A skill costs context in three tiers with different economics: the
   description ships in every session, the body loads per invocation, sub-files load on demand.
   Findings are misplacements across those tier boundaries.
2. **Primitive fit.** Is this the right shape? *Skill* = task guidance one context window
   applies end to end. *Workflow* = independent stages/fan-out/deterministic control flow where
   structured handoffs beat one long context (must name stage boundaries + handoff schema).
   *Subagent* = only for parallelism or fresh-mind context isolation — name which one; everything
   else folds back into the main agent. *Script* = the body is mostly deterministic procedure a
   model should never be asked to re-derive.
3. **Consolidation.** Overlapping trigger surfaces, shared skeletons, thin wrappers. Every merge
   recommendation must name the surviving skill, include merged description text, and argue
   trigger precision survives.
4. **Model tier**, by task property not fashion:
   - **sonnet** — mechanical: runs scripts, formats output, moves messages.
   - **opus** — standard multi-step reasoning: build, test triage, docs, review legwork.
   - **fable** — frontier judgment where a wrong call is expensive: plan critique, architecture
     decisions, adversarial verification, client-facing design.
5. **Efficiency.** Estimated tokens pulled into context per invocation (body + eagerly-loaded
   sub-files; ~10 tokens/line is sufficient to rank). Flags unconditional full-sub-file reads and
   over-long descriptions (>200 chars).

Dispositions: **keep**, **merge → {survivor}**, **split**, **workflow**, **subagent**, **script**,
**retire** — each with a named rationale. Any disposition a verifier sustains a refutation against
is downgraded to **keep + note**, preserving the original proposal and the refutation reasoning in
the record.

## Disposition table (65 rows)

| Skill | Cluster | Lines | Disposition (final) | Model Tier | Est. Tokens | Verifier Verdict | Key Finding |
|---|---|---|---|---|---|---|---|
| do-plan | SDLC core | 1162 | keep | fable | 4648 | — | SKILL.md body is 446 lines, always loaded on every invocation, with 11 phases carrying explicit "Skip if..." escape clauses that keep realistic per-invocation cost well under the full total. |
| do-plan-critique | SDLC core | 598 | keep | fable | 2392 | — | Seed claim of a stale in-body version-history section was refuted — no such section exists anywhere in SKILL.md or CRITICS.md. |
| do-build | SDLC core | 567 | keep | opus | 2268 | — | PR #1894 already split the former monolithic body into SKILL.md (179) + WORKFLOW.md (178) + PR_AND_CLEANUP.md (210), with an explicit load-sub-files table. |
| do-patch | SDLC core | 312 | keep | sonnet | 1248 | — | Single 312-line file with no sub-files, entirely loaded on every invocation — dense but no clearly separable rarely-used section. |
| do-merge | SDLC core | 188 | keep (analyst proposed script; REFUTED) | sonnet | 752 | REFUTED | Steps 1–3 look like pure deterministic gates, but the empty-rollup and issue-link checks depend on control-flow state resolved earlier in the skill, not context-free regex — ordinary prose, not systematic model failure. |
| do-sdlc | SDLC core | 173 | keep | opus | 692 | — | Seed claim that a repo doc mislabels do-sdlc's Bucket-C status was refuted — the doc correctly distinguishes `sdlc` (project-only) from `do-sdlc` (global). |
| sdlc | SDLC core | 273 | keep | opus | 1092 | — | Seed "REDUNDANT-AFTER-#1558" marker claim was refuted — no such marker exists anywhere in the file. |
| do-test | SDLC periphery | 709 | keep | sonnet | 1700 | — | Confirmed: `docs/sdlc/do-test.md` holds the repo-specific source-to-test mapping table generalized out of the body, per the skill-context convention. |
| do-pr-review | SDLC periphery | 1397 | keep | opus | 5588 | — | Workflow-conversion hypothesis refuted: the five phases are strictly sequential and tightly coupled with no independent stages to fan out; real parallelism (multi-judge consensus) is already an optional gated sub-step. |
| do-docs | SDLC periphery | 305 | keep | opus | 1220 | — | Agent A/B (always) + C/D (conditional) fan-out is legitimate parallelism — multiple independent Explore agents dispatched concurrently. |
| do-issue | SDLC periphery | 403 | keep | opus | 1612 | — | Step 6 duplicates an identical ~35-line mktemp/anchor-verify/gh-issue-create/cleanup block against `do-investigation-issue` Step 2 — a script-extraction candidate for a shared `scripts/gh_issue_publish.sh`. |
| do-investigation-issue | SDLC periphery | 141 | keep | sonnet | 564 | — | Shares the same mktemp/anchor/publish/cleanup duplication with `do-issue` — the other half of the shared-script opportunity above. |
| do-skills-audit | Audit family | 3029 | keep | opus | 800 | — | Best-in-cluster primitive fit: the deterministic 20-rule lint is fully scripted and never loaded into model context; only the `--arch` judgment layer is prompt-driven. |
| audit-hooks | Audit family | 260 | keep (analyst proposed script; REFUTED) | sonnet | 1040 | REFUTED | All 9 checks look mechanical, but several require semantic judgment (advisory-vs-validator classification, control-flow reasoning) and the skill is rarely invoked — a second script isn't clearly worth maintaining. |
| audit-models | Audit family | 92 | keep | opus | 368 | — | Overlap-with-`do-oop-audit` claim from PR #1894 refuted: only 2 of 6 checks meaningfully overlap; the other 4 are relational/ORM-specific concepts `do-oop-audit`'s single-class scope doesn't cover. |
| audit-tools | Audit family | 279 | keep (analyst proposed script; REFUTED) | sonnet | 1116 | REFUTED | `CHECKS.md`'s snippets are already directly runnable inline via Bash; splitting into a scripted pre-pass plus a manual judgment pass would be a worse two-step UX for a rarely-invoked skill. |
| do-integration-audit | Audit family | 208 | keep | fable | 832 | — | Correctly-scoped existing subagent dispatch: the optional second-pass subagent for CRITICAL findings cites fresh-mind isolation explicitly — a clean example of the two-reason test passing. |
| do-oop-audit | Audit family | 158 | keep | fable | 632 | — | 14 checks mix scriptable structural counts with genuinely semantic judgment that can't be cleanly separated without losing the judgment that makes findings useful. |
| new-audit-skill | Audit family | 368 | keep (analyst proposed merge->new-skill; REFUTED) | opus | 1472 | REFUTED | The audit-specific interview/naming/testing logic (6-dimension interview, severity levels, approach-selection matrix) has no equivalent in `new-skill`'s generic 7-step flow — folding it in either bloats every generic request or is cosmetic relocation. |
| audit-next-tool | Audit family | 0 | keep (no live skill — stale tracking reference / doc fix only, see verifier note) | sonnet | 0 | REFUTED | Confirmed absent on disk; git history shows it was renamed into today's `audit-tools` (PR #156) long ago — a stale `.claude/README.md` row is the only real bug. |
| do-design-audit | Design & media | 229 | keep | opus | 916 | — | Single-file skill; all content (10-dimension rubric + output format) is legitimately needed every invocation — length alone is not a context-economy violation here. |
| do-design-review | Design & media | 0 | keep (orphan not found — no live skill to assess) | sonnet | 0 | — | Confirmed absent at the expected user-level path; recommend closing this tracking line as stale/already-resolved rather than opening a merge issue against nothing. |
| do-design-system | Design & media | 685 | keep | opus | 2740 | — | Good context-economy skeleton, but a typical full end-to-end pass sequentially triggers all 4 sub-files, so realistic per-invocation cost approaches the full 685-line total. |
| frontend-design | Design & media | 869 | keep | opus | 252 | — | Exemplary context economy: SKILL.md body is only 63 lines and explicitly loads references only for the area in play — a typical invocation touches 1–3 of 7 reference areas. |
| pencil-design | Design & media | 194 | keep | sonnet | 776 | — | Confirmed: no residual "Distribution"/hardlink-sync prose remains from the earlier cleanup — it landed and stayed landed. |
| mermaid-render | Design & media | 229 | keep (analyst proposed script; REFUTED) | sonnet | 916 | REFUTED | Workflow B is >90% `mcp__byob__browser_*` tool calls, which only the model's own tool-calling loop can issue — no external script can call an MCP tool, so "extract to script" is a category error for the bulk of the body. |
| do-presentation | Design & media | 795 | keep (analyst proposed workflow; REFUTED) | opus | 3180 | REFUTED | The pipeline is one continuous authoring task where later steps directly consume in-context decisions from earlier ones; formalizing stage boundaries would force shared context through a costly handoff schema for no parallelism gain, and the claimed rework problem doesn't actually exist. |
| do-debrief | Design & media | 126 | keep | opus | 504 | — | Refutes a seed merge hypothesis: `do-voice-recording` is an intentionally shared reusable synthesis primitive with three independent call sites (this skill, `do-presentation --video`, and PR review). |
| do-voice-recording | Design & media | 38 | keep | sonnet | 152 | — | Refutes the same seed merge from the other side: this skill's entire reason for existing separately is corroborated by three independent callers deferring to it rather than duplicating synthesis logic. |
| telegram | Comms & channels | 217 | keep | sonnet | 2170 | — | Entirely mechanical CLI reference; cheap enough (~2.2k tokens) as one flat file that progressive disclosure isn't worth it yet — split only if it grows materially. |
| email | Comms & channels | 96 | keep | sonnet | 960 | — | Correctly uses the skill-context probe seam: generic body stays provider-agnostic, repo-specific `valor-email` detail lives in the seam file, loaded only when present. |
| google-workspace | Comms & channels | 134 | keep | opus | 1340 | — | Correctly narrows its own mail lane to "defer to /email" via the skill-context probe rather than duplicating the ladder — overlapping domain without content duplication. |
| checking-system-logs | Comms & channels | 79 | keep | sonnet | 790 | — | Body mixes a reference table with short deterministic Python one-liners dressed as inline shell — a minor script-extraction candidate, not fleet-restructuring scale. |
| reading-sms-messages | Comms & channels | 38 | keep | sonnet | 380 | — | Minimal, entirely mechanical CLI reference — no misplaced content, no split candidate. |
| sentry | Comms & channels | 70 | keep | sonnet | 700 | — | A claimed diverged user-level copy does not exist on this machine — `~/.claude/skills/sentry/` is absent entirely here; flagged to check other machines before treating as settled. |
| authenticity-pass | Comms & channels | 129 | keep | sonnet | 1290 | — | Confirmed as the correct decomposition (opposite of a consolidation target): a legitimately separate gate reused by both `/linkedin` and `/x-com`. |
| linkedin | Comms & channels | 799 | keep | opus | 7990 | — | Refuted as currently stated, confirmed as historically correct: PR #1894's split (133-line thin-router SKILL.md + on-demand references) already fixed the seed's premise; per-invocation cost dropped sharply. |
| x-com | Comms & channels | 705 | keep | opus | 7050 | — | Same pattern as `linkedin`: seed's premise already addressed by the existing split (99-line thin-router SKILL.md + on-demand references). |
| get-telegram-messages | Comms & channels | 0 | keep (no live skill — stale tracking reference / doc fix only, see verifier note) | sonnet | 0 | REFUTED | Confirmed absent; `docs/features/telegram-messaging.md` and commit `bb609298` show deliberate deletion/merge into today's `telegram` skill years ago. |
| searching-message-history | Comms & channels | 0 | keep (no live skill — stale tracking reference / doc fix only, see verifier note) | sonnet | 0 | REFUTED | Same evidence as `get-telegram-messages` — merged into `telegram` via commit `bb609298`; already retired with a documented paper trail. |
| sentry-cli | Comms & channels | 0 | keep (no live skill — stale tracking reference / doc fix only, see verifier note) | sonnet | 0 | REFUTED | Never a skill at all — it is Sentry's external CLI binary, actively used by the live `sentry` skill and auto-installed by `/update`; a category/schema error, not a valid disposition. |
| analyze | Thinking & meta | 41 | keep | fable | 164 | — | Refutes a stale pre-computed disposition: SKILL.md already has an explicit graceful-degradation path if `strategic-analyst` isn't available. |
| grill-me | Thinking & meta | 57 | keep | fable | 228 | — | Refutes merge-into-`analyze`/`zoom-out`: incompatible interaction shapes (live Q&A vs. batch report vs. status summary) — a primitive-fit collision, not just trigger overlap. |
| zoom-out | Thinking & meta | 59 | keep | opus | 236 | — | Correctly implements the skill-context probe for repo-specific memory/messaging CLIs, degrading cleanly to `git log` + summary in a foreign repo. |
| ontologies | Thinking & meta | 79 | keep | opus | 316 | — | Step 3 explicitly delegates its interview loop to `/grill-me` rather than duplicating Socratic logic inline — the desired composition pattern. |
| weekly-review | Thinking & meta | 104 | keep | sonnet | 416 | — | Almost entirely mechanical (fixed git commands, fixed output template) except one category-selection judgment step a script cannot do — full script extraction isn't clean. |
| reclassify | Thinking & meta | 32 | keep | sonnet | 128 | — | Close to a pure script candidate (find-active-plan / gate-on-status / edit-yaml-field / commit is deterministic procedure) but small enough that extraction isn't worth a separate file yet. |
| skillify | Thinking & meta | 91 | keep | opus | 364 | — | Refutes (as already-fixed) an embedded-template-divergence claim: Step 3 already reads `new-skill`'s `WORKFLOW_TEMPLATE.md` as canonical rather than embedding its own copy. |
| new-skill | Thinking & meta | 250 | keep | opus | 1000 | — | Confirmed: now has both `SKILL_TEMPLATE.md` and `WORKFLOW_TEMPLATE.md` as separate on-demand sub-files, correctly routed by skill shape. |
| pthread | Thinking & meta | 26 | keep | opus | 104 | — | Confirmed lean (26 lines, matches the real Agent tool API); confirms a genuine duplication with CLAUDE.md Principle 8 restating this skill's Decide/Aggregate sections almost verbatim — a documentation-placement finding, not a reason to change the skill. |
| tdd | Thinking & meta | 91 | keep | opus | 364 | — | One context window's worth of task guidance with no sub-files — correct Skill primitive fit; stages share full context and don't need structured handoffs. |
| deepen | Thinking & meta | 55 | keep | opus | 220 | — | Cleanly partitioned from `observability` via explicit mutual cross-reference anti-patterns — not a merge candidate. |
| observability | Thinking & meta | 91 | keep | opus | 364 | — | Mirrors `deepen`'s cross-reference discipline exactly — confirms these are correctly split, genuinely different deliverables. |
| claude-standards | Thinking & meta | 1649 | keep | opus | 6596 | — | Excellent context-economy discipline (typical invocation loads only ~229 of 1649 lines); a real but unresolved cross-cluster domain overlap with `do-skills-audit` is flagged, not asserted as a merge. |
| imagine-agent | CMA & external | 101 | keep | fable | 404 | — | Well-scoped single-context Skill: four sequential phases, one named parallel Explore dispatch correctly citing parallelism. |
| build-agent | CMA & external | 453 | keep | opus | 1812 | — | Refutes a PR #1894 fable-tier proposal: the core stage→launch→grade&iterate→schedule→close loop is a build+test-triage loop against a well-defined API contract, not open-ended design judgment. |
| do-discover-paths | CMA & external | 106 | keep | opus | 424 | — | Refutes (confirmed-fixed) a claim that this skill lacks a skill-context probe — it already carries the canonical probe sentence verbatim, with the seam file present. |
| computer-use | CMA & external | 36 | keep | sonnet | 144 | — | Excellent context economy: 36-line body, one of the leanest in the fleet; CLI surface/prerequisites correctly deferred to the seam file, loaded only in this repo. |
| officecli | CMA & external | 419 | keep | sonnet | 1676 | — | Largest single-file body in its cluster (419 lines, no sub-files) — exhaustive reference tables are entirely eagerly loaded; a candidate to split into on-demand references, not urgent. |
| ebook-ingest | CMA & external | 469 | keep | opus | 1876 | — | Confirmed: both referenced scripts exist, compile cleanly, and pass ruff with zero findings; SKILL.md itself matches the claimed 444→289-line reduction. |
| setup | Machine ops | 708 | keep | sonnet | 2832 | — | Context-economy split (620→154 SKILL.md + 5 reference sub-files) already landed; what remains is a primitive-fit gap — Phase 5/6 re-derive logic `update` already owns (see cross-cluster notes). |
| prime | Machine ops | 94 | keep | sonnet | 376 | — | Refutes a claim of a stale "cross-repo shared" comment — zero matches for "cross-repo"/"shared" in the current body; correctly lists itself under project-only skills. |
| update | Machine ops | 334 | keep | sonnet | 1336 | — | Confirms the sonnet tier call: the skill's own instructions frame it as "the orchestrator does the work — your job is to run it and report," a textbook mechanical/script-runner shape. |
| do-deploy | Machine ops | 142 | keep | sonnet | 568 | — | Correctly scoped as the repo-specific instantiation of `do-deploy-example` — appropriately thin because this repo's deploy model has no manual promotion step. |
| do-deploy-example | Machine ops | 193 | keep | opus | 772 | — | Confirmed: the template copy-source fix landed — correct global-source-to-project-destination direction is the only `cp -r` instance, alongside the customization instruction. |

## Findings detail by cluster

### SDLC core (do-plan, do-plan-critique, do-build, do-patch, do-merge, do-sdlc, sdlc)

- **do-plan** (1162 lines, fable): the largest core-SDLC body, but its 11 phases carry explicit
  "Skip if..." escape clauses (e.g. Phase 0.5 line 51, Phase 0.7), so realistic per-invocation
  cost is well under the raw total for most requests. No restructuring recommended.
- **do-plan-critique** (598, fable): a seed claim about a stale in-body version-history section
  was checked directly against `SKILL.md` and `CRITICS.md` and found to not exist — refuted.
- **do-build** (567, opus): PR #1894's SKILL.md/WORKFLOW.md/PR_AND_CLEANUP.md split already
  landed with an explicit load table; confirmed as-is.
- **do-patch** (312, sonnet): single dense file, no clean sub-file boundary; also carries the
  Lint Discipline duplication with `do-build` noted in cross-cluster notes below.
- **do-merge** (188, sonnet): analyst proposed extracting Steps 1/3 (mergeability/CI/review-state
  checks, the Closes-#N regex) into a `scripts/merge_gate_check.py`. **Verifier refuted**: the
  empty-rollup handling requires inferring branch-protection state not present in the fetched
  JSON, and the issue-link check depends on control-flow state resolved earlier in the skill —
  ordinary deterministic-check prose, not systematic model failure. Downgraded to keep.
- **do-sdlc** (173, opus) / **sdlc** (273, opus): both had specific stale-marker claims (a
  doc-mislabeling claim for `do-sdlc`, a "REDUNDANT-AFTER-#1558" marker for `sdlc`) checked and
  refuted — neither exists in the current files.

### SDLC periphery (do-test, do-pr-review, do-docs, do-issue, do-investigation-issue)

- **do-test** (709, sonnet): confirmed the repo-specific source-to-test mapping table has already
  moved to `docs/sdlc/do-test.md` per the skill-context convention.
- **do-pr-review** (1397, opus): a Workflow-conversion hypothesis was tested against the rubric's
  own stage-boundary/handoff-schema requirement and refuted — the five phases are strictly
  sequential with no independent stages to fan out; the one genuine parallelism opportunity
  (multi-judge consensus) is already an optional gated sub-step, not evidence for full conversion.
- **do-docs** (305, opus): the Agent A/B (always) + C/D (conditional) fan-out is legitimate
  parallelism — a positive pattern example, not a finding requiring action.
- **do-issue** (403, opus) and **do-investigation-issue** (141, sonnet): share an identical
  ~35-line mktemp/anchor-verify/gh-issue-create/cleanup block. This is a genuine
  script-extraction opportunity (shared `scripts/gh_issue_publish.sh`) — noted as a candidate
  follow-up, not executed here (see cross-cluster notes).

### Audit family (do-skills-audit, audit-hooks, audit-models, audit-tools, do-integration-audit, do-oop-audit, new-audit-skill, audit-next-tool)

- **do-skills-audit** (3029, opus): the cluster's best primitive-fit example — its deterministic
  20-rule lint is fully scripted and never loaded into model context; only the `--arch` judgment
  layer (this very audit's own rubric) is prompt-driven. Also the source of the
  `do-skills-audit`/`claude-standards` cross-cluster overlap flag (see below).
- **audit-hooks** (260, sonnet) and **audit-tools** (279, sonnet): both were proposed for script
  extraction on the strength of `do-skills-audit`'s precedent. Both **refuted**: `do-skills-audit`
  operates on a closed, self-referential corpus (never arbitrary external hook/tool content), and
  several checks in each (advisory-vs-validator classification, control-flow reasoning, error-doc
  quality judgment) require semantic judgment beyond string/threshold matching. Given both are
  rarely (human-triggered) invoked, a second maintained script isn't clearly worth it. Downgraded
  to keep.
- **audit-models** (92, opus): a PR #1894 overlap claim with `do-oop-audit` was checked and
  refuted — only 2 of 6 checks meaningfully overlap; the rest are relational/ORM-specific
  concepts outside `do-oop-audit`'s single-class scope.
- **do-integration-audit** (208, fable) and **do-oop-audit** (158, fable): both correctly use
  judgment-preserving primitives — `do-integration-audit`'s optional fresh-mind subagent dispatch
  for CRITICAL findings is a clean positive example of the rubric's two-reason subagent test.
- **new-audit-skill** (368, opus): analyst proposed folding this into `new-skill` as
  audit-specific reference files, citing an existing cross-reference and a shared skeleton in
  `AUDIT_TEMPLATE.md`. **Verifier refuted**: `new-skill`'s trigger description is deliberately
  narrow (an explicit skill/agent/tool noun in every trigger phrase); the audit-oriented trigger
  phrases this analyst wanted to add ("I want to check X for problems") contain no such noun and
  describe intent to run a check, not author new infrastructure — a real trigger-precision loss.
  The 128-line audit-specific interview/naming/severity logic also has no equivalent in
  `new-skill`'s generic 7-step flow. Downgraded to keep.
- **audit-next-tool** (orphan): confirmed absent on disk anywhere reachable via `find` and a full
  `~/.claude/skills/` listing. Git history (commits `b3ad939c`, `16c1bd1d`, PR #156) and
  `docs/features/skills-reorganization.md:21` show it was renamed into today's `audit-tools`
  skill long ago. `.claude/README.md:69` still lists `/audit-next-tool` as a live command — that
  stale row is the actual bug, not a skill retirement decision.

### Design & media (do-design-audit, do-design-review, do-design-system, frontend-design, pencil-design, mermaid-render, do-presentation, do-debrief, do-voice-recording)

- **do-design-audit** (229, opus) and **frontend-design** (869, opus): both praised as correctly
  scoped for their length — `do-design-audit`'s single-file 10-dimension rubric is legitimately
  needed every invocation, and `frontend-design`'s 63-line body with on-demand reference loading
  is an exemplary context-economy pattern (typical invocation touches only 1–3 of 7 reference
  areas).
- **do-design-review** (orphan): confirmed absent at the expected user-level path; recommend
  closing this tracking line as stale/already-resolved rather than opening a merge issue against
  a file that isn't there.
- **do-design-system** (685, opus): a good pipeline skeleton on paper, but a typical full pass
  sequentially triggers all 4 sub-files, so realistic per-invocation cost approaches the full
  685-line total for a first-time run — a soft context-economy finding, not a restructuring case.
- **pencil-design** (194, sonnet): confirmed the earlier "Distribution"/hardlink-sync cleanup
  landed and has stayed landed (no residual prose on re-check).
- **mermaid-render** (229, sonnet): analyst proposed extracting the deterministic 9-step browser
  automation sequence (Workflow B) into a `scripts/mermaid_to_png.py`. **Verifier refuted**:
  Workflow B is over 90% `mcp__byob__browser_*` tool calls, which only the model's own
  tool-calling loop can issue — no external script can call an MCP tool, making "extract to
  script" a category error for the bulk of the body. The analyst also undercounted judgment
  checkpoints (dynamic selector resolution plus at least 6 documented troubleshooting/retry
  branches). Downgraded to keep.
- **do-presentation** (795, opus): analyst proposed a Workflow conversion, naming stage
  boundaries (research → outline → themed markdown → reviewed markdown → exports).
  **Verifier refuted**: this is a single continuous authoring task where later steps directly
  consume in-context decisions from earlier steps; formalizing stage boundaries would force
  shared context through a serialized handoff schema — the rubric's own named
  orchestrator↔stage failure mode — for no parallelism gain. The two places genuine subagent
  isolation adds value (Step 2 research, Step 8 self-review) are already implemented today, and
  the claimed rework problem (re-research after export failure) doesn't exist because Step 7
  already persists the full deck to disk before Step 9 export runs. Downgraded to keep; the
  verifier suggested an optional lightweight durability improvement (persist the outline at end
  of Step 3) with no Workflow primitive required.
- **do-debrief** (126, opus) and **do-voice-recording** (38, sonnet): a seed merge hypothesis
  between the two was refuted from both directions — `do-voice-recording` is an intentionally
  shared reusable synthesis primitive with three independent call sites (`do-debrief`,
  `do-presentation --video`, and PR review voice notes).

### Comms & channels (telegram, email, google-workspace, checking-system-logs, reading-sms-messages, sentry, authenticity-pass, linkedin, x-com, get-telegram-messages, searching-message-history, sentry-cli)

- **telegram** (217, sonnet) and **reading-sms-messages** (38, sonnet): entirely mechanical CLI
  references; cheap enough that progressive disclosure via sub-files isn't worth it yet.
- **email** (96, sonnet) and **google-workspace** (134, opus): both correctly use the
  skill-context probe seam to keep generic bodies provider-agnostic while deferring repo-specific
  detail; `google-workspace` explicitly narrows its own mail lane to "defer to /email" rather
  than duplicating the CLI ladder — a clean instance of overlapping domain without content
  duplication.
- **checking-system-logs** (79, sonnet): mixes a reference table with short deterministic Python
  one-liners dressed as inline shell — a minor script-extraction candidate, well below
  fleet-restructuring scale.
- **sentry** (70, sonnet): a claimed diverged user-level copy does not exist on this machine
  (`~/.claude/skills/sentry/` absent entirely) — flagged to check other machines before treating
  as settled, since this audit only covers the one machine's worktree.
- **authenticity-pass** (129, sonnet): confirmed as the correct decomposition — a legitimately
  separate gate reused by both `/linkedin` and `/x-com`, i.e. the opposite of a consolidation
  target.
- **linkedin** (799, opus) and **x-com** (705, opus): both had seed merge/split premises refuted
  as *currently* stated but confirmed as historically correct — PR #1894's split into thin-router
  SKILL.md files (133 and 99 lines respectively) plus on-demand references already fixed the
  problem the seed was describing; per-invocation cost has already dropped sharply. Both also
  contribute to the shared `references/posting.md` extraction opportunity noted in cross-cluster
  notes below.
- **get-telegram-messages**, **searching-message-history**, **sentry-cli** (orphans): all
  confirmed absent on disk. The first two have documented merge histories into `telegram`
  (commit `bb609298`, "Add unified telegram skill, remove old fragmented skills"). `sentry-cli`
  was never a skill at all — it is Sentry's external CLI binary, actively used by the live
  `sentry` skill (`.claude/agents/sentry.md:359`) and auto-installed by `/update`; listing it as a
  skill to retire is a category/schema error.

### Thinking & meta (analyze, grill-me, zoom-out, ontologies, weekly-review, reclassify, skillify, new-skill, pthread, tdd, deepen, observability, claude-standards)

- **analyze** (41, fable): refutes a stale pre-computed disposition — the skill already has an
  explicit graceful-degradation path if `strategic-analyst` isn't available.
- **grill-me** (57, fable): refutes a merge-into-`analyze`/`zoom-out` hypothesis on primitive-fit
  grounds — live Q&A vs. batch report vs. status summary are incompatible interaction shapes, not
  just overlapping triggers.
- **zoom-out** (59, opus) and **ontologies** (79, opus): both cited as positive composition
  patterns — `zoom-out` degrades cleanly via the skill-context probe in a foreign repo;
  `ontologies` explicitly delegates its interview loop to `/grill-me` rather than duplicating
  Socratic logic inline.
- **weekly-review** (104, sonnet) and **reclassify** (32, sonnet): both near-pure script
  candidates (fixed git commands / fixed output templates, or find-active-plan / gate-on-status /
  edit-yaml-field / commit), but small enough — and, for `weekly-review`, carrying one genuine
  category-selection judgment step a script can't do — that full extraction isn't a clean win.
- **skillify** (91, opus) and **new-skill** (250, opus): confirm PR #1894's template-dedup
  claim — `skillify` now reads `new-skill`'s `WORKFLOW_TEMPLATE.md` as canonical rather than
  embedding its own copy, and `new-skill` now correctly routes `SKILL_TEMPLATE.md` vs.
  `WORKFLOW_TEMPLATE.md` by skill shape.
- **pthread** (26, opus): confirmed lean and matching the real Agent tool API (no
  spawn_subagent/thread_metrics pseudo-code remains). Also confirms a genuine duplication finding
  with CLAUDE.md Principle 8 (see cross-cluster notes) — a documentation-placement issue, not a
  reason to change the skill.
- **deepen** (55, opus) / **observability** (91, opus): mirror each other's cross-reference
  discipline exactly, confirming a correct split rather than a merge candidate.
- **claude-standards** (1649, opus): excellent context economy (typical invocation loads only
  ~229 of 1649 lines); flags a real but unresolved cross-cluster domain-overlap question with
  `do-skills-audit` — see cross-cluster notes.

### CMA & external (imagine-agent, build-agent, do-discover-paths, computer-use, officecli, ebook-ingest)

- **imagine-agent** (101, fable): well-scoped single-context Skill, one named parallel Explore
  dispatch correctly citing parallelism.
- **build-agent** (453, opus): refutes a PR #1894 fable-tier model proposal — the core
  stage→launch→grade&iterate→schedule→close loop is a build+test-triage loop against a
  well-defined API contract, not open-ended design judgment; opus is the right tier.
- **do-discover-paths** (106, opus): refutes (as already-fixed) a claim it lacks a skill-context
  probe — it already carries the canonical probe sentence verbatim with the seam file present.
- **computer-use** (36, sonnet): one of the leanest bodies in the fleet; CLI surface correctly
  deferred to the seam file.
- **officecli** (419, sonnet): the largest single-file body in its cluster with exhaustive
  reference tables entirely eagerly loaded — a soft candidate to split into on-demand references
  eventually, not urgent.
- **ebook-ingest** (469, opus): confirms both referenced scripts exist, compile cleanly, and pass
  ruff with zero findings; SKILL.md matches the claimed 444→289-line reduction.

### Machine ops (setup, prime, update, do-deploy, do-deploy-example)

- **setup** (708, sonnet): the context-economy split (620→154 SKILL.md + 5 reference sub-files)
  already landed. What remains unaddressed is a primitive-fit gap: Phase 5 Step 8 and Phase 6
  re-derive logic that `update` already owns (see cross-cluster notes).
- **prime** (94, sonnet): refutes a claimed stale "cross-repo shared" comment — zero matches in
  the current body; correctly lists itself under project-only skills.
- **update** (334, sonnet): confirms the sonnet tier — its own instructions frame it as "the
  orchestrator does the work, your job is to run it and report," a textbook mechanical/
  script-runner shape, and the single source of truth `setup`'s Phase 5/6 should delegate to.
- **do-deploy** (142, sonnet) and **do-deploy-example** (193, opus): `do-deploy` is correctly the
  thin, repo-specific instantiation of `do-deploy-example` (no manual promotion step in this
  repo's deploy model); `do-deploy-example` confirms the template copy-source fix landed (correct
  global-source-to-project-destination `cp -r` direction).

## Disputed dispositions & adversarial review

All 10 non-keep dispositions proposed by cluster analysts were refuted by a fresh-context
verifier. Recorded here so a future audit doesn't re-litigate settled questions.

| # | Skill | Analyst's original proposal | Verifier's refutation |
|---|---|---|---|
| 1 | `do-merge` | script — extract Steps 1/3 (mergeability/CI/review-state checks, Closes-#N regex) into `scripts/merge_gate_check.py` | Step 1's empty-rollup handling requires inferring branch-protection state absent from the fetched JSON; Step 3's issue-link check depends on control-flow state resolved earlier in the skill, not context-free regex. The boundary also collides with the Dependabot Exemption block's overlapping `gh pr view` call. Ordinary deterministic-check prose, not systematic model failure. |
| 2 | `mermaid-render` | script — extract Workflow B's 9-step browser automation sequence into `scripts/mermaid_to_png.py` | Workflow B is >90% `mcp__byob__browser_*` tool calls that only the model's own tool-calling loop can issue — no external script can call an MCP tool, so "extract to script" is a category error for the bulk of the body. Judgment checkpoints (selector resolution, ≥6 troubleshooting/retry branches) were also undercounted. |
| 3 | `audit-hooks` | script — extract the 9 mechanical checks into an `audit_hooks.py`, following `do-skills-audit`'s precedent | `do-skills-audit`'s script operates on a closed, self-referential corpus, never arbitrary external hook content — a poor precedent. Several checks require semantic judgment (advisory-vs-validator classification, control-flow reasoning after a bare exec). Given rare, human-triggered invocation, a second maintained script isn't clearly worth it. |
| 4 | `audit-tools` | script — extract `CHECKS.md`'s 8-of-10 ready-to-run snippets into an `audit_tools.py` | The snippets are already directly runnable inline via Bash; the "~2 checks needing judgment" claim undercounts (closer to 5 of 10 carry a prose-quality tail). Splitting into a scripted pre-pass plus manual judgment pass is a worse two-step UX for a rarely-invoked skill. |
| 5 | `new-audit-skill` | merge → `new-skill` (fold audit-specific interview/template into `new-skill`'s reference files) | `new-skill`'s trigger description is deliberately narrow (explicit skill/agent/tool noun); the audit trigger phrases contain no such noun — real trigger-precision loss. The cited cross-reference is ordinary progressive-disclosure delegation, not evidence of intended merge. The 128-line audit-specific interview/naming/severity logic has no equivalent in `new-skill`'s generic 7-step flow. |
| 6 | `do-presentation` | workflow — formalize research→outline→theme→diagrams→write→self-review→export→verify as independent Workflow stages | This is one continuous authoring task where later steps directly consume in-context decisions from earlier steps; formalizing stage boundaries forces shared context through a costly handoff schema for no parallelism gain — the rubric's own named orchestrator↔stage failure mode. The two genuine subagent-isolation opportunities (research, self-review) are already implemented, and the claimed rework problem (re-research after export failure) doesn't exist since the deck is already persisted before export runs. |
| 7 | `audit-next-tool` (orphan) | retire — no directory exists at the expected path | Not a fresh disposition: git history (`b3ad939c`, `16c1bd1d`, PR #156) and `docs/features/skills-reorganization.md:21` show it was converted into today's `audit-tools` skill long ago. `.claude/README.md:69` still lists `/audit-next-tool` as live — a stale doc bug, not an open retire item. |
| 8 | `get-telegram-messages` (orphan) | retire — no directory exists | `docs/features/telegram-messaging.md:7` and commit `bb609298` ("Add unified telegram skill, remove old fragmented skills") show it was deliberately deleted and merged into `telegram` years ago. Stale tracking reference to an already-completed consolidation. |
| 9 | `searching-message-history` (orphan) | retire — no directory exists | Same evidence as #8 — merged into `telegram` via commit `bb609298`. Already retired with a documented paper trail; listing as a pending retire disposition is redundant. |
| 10 | `sentry-cli` (orphan) | retire — no directory exists | Never a skill — it is Sentry's external CLI binary, actively used by the live `sentry` skill (`.claude/agents/sentry.md:359`) and auto-installed by `/update`. A category/schema error, not a valid disposition. |

## Coverage

- **Row count: 65 of 65** (60 live skills + 5 tracking-artifact orphans), matching the plan's
  required coverage.
- **Zero INCOMPLETE clusters.** All 8 cluster analysts returned full rows for every skill in
  their assigned domain (cluster row counts: 7 + 5 + 8 + 9 + 12 + 13 + 6 + 5 = 65).
- **`do-design-review`'s empty row is a legitimate orphan-not-found, not an analyst failure.**
  The analyst confirmed absence via direct path check and a full listing of the other 33 skills
  present at that machine's user level, and recorded the row per the shared coverage
  instructions rather than skipping it silently.
- **No coverage gaps to report** — every skill named in the cluster assignment plan appears
  exactly once in the merged dataset; no invented data was needed for any row.

## Cross-cluster notes for follow-up

These are candidate follow-up issues for a human to accept or reject — nothing here has been
executed, per this slug's No-Gos.

1. **`do-skills-audit` vs. `claude-standards` domain overlap** (raised independently by the
   Audit-family and Thinking & meta analysts). Both audit the same five asset classes (commands,
   hooks, MCP, skills, subagents) but at different depth: `claude-standards` runs a syntactic/
   structural conformance check, `do-skills-audit` applies five-lens architectural/semantic
   judgment (this rubric). A plausible resolution is a workflow handoff — conformance pass feeds
   the architecture pass as an early deterministic stage — rather than a merge, but neither
   analyst had visibility into both skills' full bodies simultaneously to confirm. Worth a
   dedicated cross-skill read before deciding.
2. **`linkedin` / `x-com` shared `references/posting.md` extraction.** Both skills' posting
   reference files share near-identical scaffolding prose (cold-read-loop mechanics, self-review
   checklist). A shared `references/social-posting-pattern.md` both files point to would remove
   the duplication while keeping platform-specific voice/DOM/persona content local and each
   skill's own SKILL.md/description untouched — a pure reference-file extraction, not a skill
   merge (a full merge was independently refuted by both analysts as blunting distinct,
   non-overlapping trigger phrases for zero benefit).
3. **`do-build` / `do-patch` "Lint Discipline" duplication.** Both skills carry a near-verbatim
   duplicate "Lint Discipline" section. Consolidating into one shared reference would avoid the
   two copies drifting independently.
4. **`do-issue` / `do-investigation-issue` shared `gh_issue_publish` duplication.** `do-issue`
   Step 6 and `do-investigation-issue` Step 2 both run an identical ~35-line mktemp/
   anchor-verify/gh-issue-create/cleanup bash block. A shared `scripts/gh_issue_publish.sh` both
   skills invoke would be a straightforward script extraction of genuinely deterministic
   procedure — a good target for the rubric's Script primitive, unlike the refuted `do-merge`/
   `mermaid-render`/`audit-hooks`/`audit-tools` proposals above.
5. **`setup` Phase 5/6 vs. `update` delegation.** `setup`'s Phase 5 Step 8 re-runs the same three
   installers `update`'s "Reinstall Launchd Services" step runs, and Phase 6 duplicates the same
   bridge-health-check pattern `update`'s own catchup step performs. `update` is confirmed as the
   single source of truth this logic should delegate to; `setup` could shell out to
   `scripts/update/run.py --full` instead of re-deriving the same steps inline.
6. **`pthread` vs. CLAUDE.md Principle 8 duplication.** Principle 8 in this repo's CLAUDE.md
   restates `pthread`'s Decide/Aggregate sections almost verbatim with no cross-reference either
   direction — policy genuinely lives in two places. The `pthread` analyst's suggested fix
   (Principle 8 shrinks to a one-line pointer to `/pthread`) is a documentation-placement change,
   not a change to the skill itself.

## Model tier summary

| Tier | Count | Rationale pattern |
|---|---|---|
| **sonnet** | 28 | Mechanical: runs scripts, formats output, executes deterministic CLI/gate procedures, moves messages. Includes all 5 orphan rows (no live skill to run) and skills whose own body frames the work as "the orchestrator does the work, your job is to run it and report" (`update`) or pure CLI reference (`telegram`, `reading-sms-messages`, `computer-use`, `officecli`). |
| **opus** | 30 | Standard multi-step reasoning: build/test-triage loops, docs generation legwork, PR review phases, structured interviews and template-filling, cross-referenced compositional skills (`deepen`/`observability`, `zoom-out`/`ontologies`). The largest tier — most of the fleet's work is exactly this shape. |
| **fable** | 7 | Frontier judgment where a wrong call is expensive: plan critique (`do-plan`, `do-plan-critique`), architecture-adjacent audit judgment (`do-integration-audit`, `do-oop-audit`), live open-ended Q&A (`grill-me`), strategic analysis (`analyze`), and client-facing design scoping (`imagine-agent`). |

Total: 65 rows (60 live skills + 5 orphans), tier assignments summing to 65.
