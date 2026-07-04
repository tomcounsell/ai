---
marp: true
title: Skill Fleet Renovation
theme: default
paginate: true
backgroundColor: #ffffff
color: #0d1117
style: |
  section {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: #ffffff;
    color: #0d1117;
    padding: 56px 64px;
    font-size: 22px;
    line-height: 1.5;
  }
  h1, h2, h3 {
    color: #0d1117;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  h1 { font-size: 2.0em; margin-bottom: 0.2em; }
  h2 { font-size: 1.5em; margin-bottom: 0.6em; border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }
  h2 code { font-size: 0.9em; }
  h3 { font-size: 1.1em; color: #1f2328; }
  a, strong { color: #0969da; }
  code, pre {
    font-family: "SF Mono", "Cascadia Code", "Fira Code", monospace;
    background: #f6f8fa;
    color: #0d1117;
    border-radius: 6px;
  }
  code { padding: 2px 6px; font-size: 0.88em; }
  pre { padding: 14px 18px; font-size: 0.78em; border: 1px solid #d0d7de; }
  blockquote {
    border: 1px solid #b6d7f5;
    border-radius: 6px;
    background: #ddf4ff;
    color: #0a3069;
    padding: 12px 18px;
    margin: 12px 0;
    font-style: normal;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.88em;
    margin: 8px 0;
  }
  th, td {
    border: 1px solid #d0d7de;
    padding: 8px 12px;
    text-align: left;
    vertical-align: top;
  }
  th { background: #f6f8fa; font-weight: 600; }
  section.lead {
    text-align: center;
    justify-content: center;
    background: linear-gradient(135deg, #ffffff 0%, #f6f8fa 100%);
  }
  section.lead h1 { font-size: 2.6em; }
  section.lead p { color: #57606a; font-size: 1.1em; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 10px; }
  .cols-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-top: 10px; }
  .stat {
    background: #ddf4ff; border: 1px solid #b6d7f5; border-radius: 6px;
    padding: 12px 20px; margin: 10px 0;
    font-size: 0.96em; font-weight: 600; color: #0a3069; line-height: 1.5;
  }
  .warn {
    background: #fff8c5; border: 1px solid #eed888; border-radius: 6px;
    padding: 10px 18px; margin: 10px 0;
    font-size: 0.88em; color: #7d4e00; line-height: 1.5;
  }
  .path-card {
    border: 1px solid #d0d7de; border-radius: 6px;
    padding: 14px 16px; font-size: 0.86em; background: #f6f8fa;
  }
  .path-card strong { display: block; margin-bottom: 6px; color: #0d1117; font-size: 1.05em; }
  .delta {
    display: inline-block; background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px;
    font-family: "SF Mono", "Cascadia Code", monospace; font-size: 0.72em;
    padding: 2px 10px; margin-bottom: 8px; color: #57606a;
  }
  section::after { color: #8b949e; font-size: 14px; }
  ul, ol { margin-left: 1.2em; }
  li { margin-bottom: 4px; }
---

<!-- _class: lead -->
# The Skill Fleet Renovation
### PR #1894: all 60 skills, two passes, eight parallel agents
<br>

*Pass 1 fixed the structure. Pass 2 fixed the thinking.*

---

## The problem: skills rot silently

A skill is a markdown instruction file the agent loads on demand. Sixty of them had accumulated two years of drift, and nothing was watching.

- **7 monoliths**: SKILL.md bodies of 500 to 800 lines, fully loaded on every invocation
- **65 content warnings**: bloated descriptions, invalid frontmatter, junk files, orphaned sub-files
- **16,428 characters** of trigger descriptions injected into *every* system prompt
- **Live rot**: dead install scripts, a log format replaced long ago, pseudo-code APIs that never existed, hardcoded paths from another machine

<div class="stat">
Every stale line is a tax: wrong instructions get followed, and dead weight is paid for in tokens on every single call.
</div>

---

## The method: fan out, own your lane

Both passes used the same shape: 8 cluster agents in one shared worktree, each owning a disjoint set of skill directories, judged by the five-lens rubric in `do-skills-audit/references/rubric.md`.

```
              branch: skills-renovation  (one shared worktree)
                                |
    +------+------+------+------+------+------+------+------+
   C1     C2     C3     C4     C5     C6     C7     C8
  SDLC   SDLC   audit  design comms  think   CMA   machine
  core   perim  family &media &chans &meta  &extern  ops
    +------+------+------+------+------+------+------+------+
                                |
              one commit per cluster, explicit paths only
```

No two agents ever touched the same file, and the **skill-context seam** convention kept every global skill portable.

---

## Two passes, two different lenses

| | **Pass 1: lint and structure** | **Pass 2: Opus-level optimization** |
|---|---|---|
| Question asked | Is this skill *well-formed*? | Does this skill *think well*? |
| Targets | Monolith splits, description budgets, frontmatter, junk files, rot | Goal-first framing, over-instruction, duplicated rules, false claims |
| Typical fix | 800-line body → 138 lines + sub-files | 7-step micro-procedure → 4 goal-level steps |
| Held constant | Content (splits are reorganizations) | Determinism: commands, schemas, gates, probe sentences byte-exact |
| Verification | Audit lint, 76 unit tests | Line-by-line claim checks against live code and CLIs |

> Pass 2 assumes a smart reader. Hand-holding written for a weaker model is deleted; contracts and gates stay exact.

---

## Scoreboard

| Metric | Before | After |
|---|---|---|
| Skills audited | 60 | 60 |
| Line-count monolith FAILs | **7** | **0** |
| Content WARNs (rules 4 to 18) | 65 | **0** |
| Fleet description total | 16,428 chars | 10,463 chars |
| Remaining WARNs | 72 | 52 (51 clear on next `/update` sync; 1 is the fleet description budget) |

Verification: self-audit 15/15 PASS, 76 audit unit tests green, probe-sentence grep clean, 3-skill spot-check reads correctly from a Rust/JS repo.

---

## The monolith splits (pass 1)

All content-preserving: bodies became a short SKILL.md plus load-on-demand sub-files with a "load when..." table.

| Skill | Before | After | New structure |
|---|---|---|---|
| linkedin | 800 | 138 | 4 `references/` sub-files |
| x-com | 728 | 101 | 3 `references/` sub-files |
| do-pr-review | 635 | 283 | folded into sub-skills + `outcome-contract.md` |
| setup | 620 | 153 | 5 phase-organized `references/` sub-files |
| do-test | 611 | 206 | 4 sub-files; orphaned `PYTHON.md` reconnected |
| do-design-system | 534 | 295 | 3 `references/` sub-files |
| do-build | 526 | 186 | deduplicated against existing sub-files |
| update (bonus) | 338 | 143 | `modules.md` + `troubleshooting.md` |

---

<!-- _class: lead -->
# Cluster 1
## SDLC core
*do-plan · do-plan-critique · do-build · do-patch · do-merge · do-sdlc · sdlc*

---

## `do-plan`

<span class="delta">476 → 446 lines</span>

- **Pass 1**: genericized the Telegram-bridge note and the xfail search so the skill reads correctly outside this repo
- **Pass 2**: fixed a real **Phase-3 numbering bug** (steps ran 1, 4, 5, 6), deleted a duplicate When-to-Use section, collapsed a 7-step WebSearch micro-procedure into 4 goal-level steps
- The commit-on-main rule was stated three times; now stated once

---

## `do-plan-critique`

<span class="delta">436 → 400 lines · description 222 → 128 chars</span>

- **Pass 1**: description cut; flagged the in-body version history as per-invocation dead weight
- **Pass 2**: version history deleted, Step 2a's repo policy recitation moved to its `docs/sdlc/` seam file, critic cap rules deduplicated from 3 statements to 1
- **Found and fixed**: the seam file was stale, still describing the pre-#1714 six/seven-critic roster. Corrected to the real LITE(1) / FULL(3) split
- 3 barrier invariants from #1690 restored verbatim after the test reconciliation confirmed they were load-bearing

---

## `do-build`

<span class="delta">526 → 186 → 179 lines</span>

- **Pass 1**: the biggest duplication find. Steps in SKILL.md repeated `WORKFLOW.md` and `PR_AND_CLEANUP.md` near-verbatim, so **every invocation paid roughly 2x tokens**
- Split resolved it: 186-line body, probe sentence kept verbatim
- **Pass 2**: deleted the Notes and Example-Execution walkthrough, removed "sent to Telegram chat" from the generic report template (bridge-specific wording in a global skill)

---

## `do-patch`

<span class="delta">351 → 310 lines · description 236 → 191 chars</span>

- **Pass 1**: description trimmed while keeping all 5 trigger phrasings; test verification generalized across pytest, cargo, and npm
- **Pass 2**: deleted a duplicate Commit-Rules section, 24 lines of examples, and a Context-Awareness section
- **Restored** the test-anchored heading "Sync Plan Checkbox...", which fixed 2 of 3 stale content-test failures on the spot

---

## `do-merge`

<span class="delta">178 lines · description 332 → 155 chars</span>

- **Pass 1**: description halved
- **Pass 2**: removed the last repo incantation (`touch data/merge_authorized_{PR}`) from the generic body; it now lives only in the seam file
- Cluster verdict: **reference-quality skill**. The verify-then-merge gate needed no thinking repairs
- Disposition note: Steps 1 to 3 are a plausible future `scripts/` extraction, but UNKNOWN-retry and dependabot branching still carry judgment

---

## `do-sdlc`

<span class="delta">153 lines · description 340 → 176 chars</span>

- **Pass 1**: description halved; surfaced a real governance discrepancy
- **Pass 2**: every claim verified against current code. Nothing to cut

<div class="warn">
Discrepancy for #1883: <code>docs/features/skill-context-convention.md</code> lists do-sdlc as Bucket C project-only, but it lives in <code>skills-global/</code> and syncs to every machine. Either the doc is stale or the #1783 move was never executed. Needs human sign-off.
</div>

---

## `sdlc` (project router)

<span class="delta">263 → 248 lines</span>

- **Pass 1**: no changes; flagged the G1-G7 guard table as duplicating `sdlc-tool next-skill`
- **Pass 2**: verified **#1558 is closed** with `ensure=True` on all write paths, so the Step 1.5 belt-and-suspenders session-ensure and its `REDUNDANT-AFTER-#1558` marker were retired
- Guard table kept byte-exact (a parity test asserts it) but reframed as an interpretation reference: the tool is the sole dispatch authority
- Command discipline rule deduplicated from 3 statements to 1

---

<!-- _class: lead -->
# Cluster 2
## SDLC periphery
*do-test · do-pr-review · do-docs · do-issue · do-investigation-issue*

---

## `do-test`

<span class="delta">611 → 206 → 201 lines</span>

- **Pass 1**: monolith split into 4 sub-files (parallel-dispatch, baseline-verification, quality-gates, special-targets); the orphaned `PYTHON.md` is finally referenced
- **Pass 2**: the repo's source-to-test mappings, generalized out of the body in pass 1, were **restored into `docs/sdlc/do-test.md`** so the knowledge survives at the seam
- Fixed a stale claim in PYTHON.md: lint default is ruff, the doc still said black
- Constants and quality gates verified load-bearing and kept exact

---

## `do-pr-review`

<span class="delta">635 → 283 → 248 lines</span>

- **Pass 1**: content folded into existing sub-skills plus a new `outcome-contract.md`; junk `sub-skills/README.md` deleted; argument-hint added
- **Pass 2**: the **last two repo couplings pulled through the seam** (`plan_checkbox_writer` command and the `requires_real_chrome` flag now live in `docs/sdlc/do-pr-review.md`)
- Fixed a stale reference to `.claude/commands/do-merge.md`; the no-blank-verdicts rule went from 3 statements to 1
- All 8 Hard Rules kept word-for-word

---

## `do-docs`

<span class="delta">307 → 305 lines · description 236 → 139 chars</span>

- **Pass 1**: description cut by 40 percent
- **Pass 2**: mostly a verification pass. Every seam automation hook was checked against live code and confirmed unaffected by the #1828 reflection-scheduler split that merged mid-branch
- One doubly-stated merge rule deduplicated

The quiet skills matter too: confirming a skill is *accurate* is the same work as fixing one that is not.

---

## `do-issue`

<span class="delta">174 → 167 lines · description 256 → 142 chars</span>

- **Pass 1**: description cut; the "track this" trigger promoted from the body into the description where it can actually fire
- **Pass 2**: **restored a lost `### Step 4` heading**. `RECON.md` pointed readers at a step that no longer existed in the body
- Goal statement moved above the probe sentence; duplicate When-to-Use deleted

---

## `do-investigation-issue`

<span class="delta">133 → 110 lines · description 429 → 193 chars</span>

- **Pass 1**: description cut by more than half; overlap with do-issue examined and deliberately kept separate, since a merge would blunt trigger precision
- **Pass 2**: fixed a **live template-drift bug**. The inline heredoc copy of the issue template had silently diverged from `TEMPLATE.md`
- Now `TEMPLATE.md` is the single source of truth; the body references it instead of embedding a copy

---

<!-- _class: lead -->
# Cluster 3
## Audit family
*do-skills-audit · audit-hooks · audit-models · audit-tools · do-integration-audit · do-oop-audit · new-audit-skill*

---

## `do-skills-audit`

<span class="delta">92 lines · untouched in both passes</span>

- The auditor that defined this renovation's rubric was placed under a **light-touch mandate**: you do not rewrite the measuring stick mid-measurement
- Self-audit exits 0 with 15/15 checks passing, before and after
- Its rubric (`references/rubric.md`) drove all 8 clusters in both passes

> One finding *about* it: rule 13's coupling-signal set misses `python tools/...` invocations, which is how do-discover-paths escaped detection. Extending the signal set is a #1883 disposition item.

---

## `audit-hooks`

<span class="delta">105 → 98 lines + new 24-line seam file</span>

- **Pass 1**: description 275 → 156 chars
- **Pass 2**: rewritten goal-first around its real invariant: *no hook may hang a session*
- **Rot fix**: the body listed 7 validators; the repo has **21**. The stale list was replaced with a live directory enumeration declared in a new `.claude/skill-context/audit-hooks.md`
- Now the skill can never drift from the validator roster again

---

## `audit-models`

<span class="delta">102 → 92 lines + new 28-line seam file</span>

- **Pass 1**: added the probe sentence and reframed hardcoded `project_key` / `ReflectionIgnore` specifics as overridable defaults
- **Pass 2**: the seam file was **actually created** (pass 1 had added only the probe). It declares Popoto, the `project_key` KeyField, ReflectionIgnore, and the no-raw-Redis rule; the body got honest generic defaults

<div class="warn">
Merge candidate for #1883: heavy check overlap with do-oop-audit. Either merge in, or move project-only. Both need a RENAMED_REMOVALS entry.
</div>

---

## `audit-tools`

<span class="delta">96 → 81 lines · description 320 → 179 chars</span>

- **Pass 1**: description cut by 44 percent
- **Pass 2**: intro merged goal-first; version-history section deleted
- Trigger collision flagged: "reviewing tool quality" fires both this skill and the user-level `audit-next-tool` orphan. Disposition pass must pick one owner

---

## `do-integration-audit`

<span class="delta">291 → 208 lines</span>

- **Pass 1**: missing trigger phrase added (rule 4)
- **Pass 2**: **83 lines of Django and FastAPI worked examples distilled into a 10-line, 4-finding style reference**. An Opus-class reader needs the shape of a good finding, not three full essays
- The verify-before-report discipline kept word-for-word: it is the skill's contract

---

## `do-oop-audit`

<span class="delta">218 → 158 lines · description 422 → 193 chars</span>

- **Pass 1**: description cut by more than half; the "audit models" trigger deliberately dropped because it collided with the audit-models skill
- **Pass 2**: 55 lines of worked examples became a 5-finding style reference
- All 14 checks kept exact
- Designated the survivor if audit-models merges in under #1883

---

## `new-audit-skill`

<span class="delta">136 → 128 lines · description 439 → 194 chars</span>

- **Pass 1**: `scripts/audit.py` looked like a broken reference but is a template placeholder, so it is now placeholder-fenced; "8 existing audit skills in this repo" genericized
- **Pass 2**: **reference-table rot fixed**: do-skills-audit grew from 12 to 20 rules, audit-tools became a 10-check report-only skill, and a duplicate docs-auditor row was merged
- Disposition: candidate to fold into new-skill as `references/audit-template.md`

---

<!-- _class: lead -->
# Cluster 4
## Design and media
*do-design-system · do-design-audit · frontend-design · pencil-design · do-presentation · mermaid-render · do-debrief · do-voice-recording*

---

## `do-design-system`

<span class="delta">534 → 295 → 261 lines · description 482 → 198 chars</span>

- **Pass 1**: monolith split into 3 `references/` sub-files (file-organization, moodboard-capture, pen-editing) with a load-when table covering all four sub-files
- **Pass 2**: goal-first rewrite with explicit success criteria: 3 to 7 charter-grounded additive edits per pass
- A duplicate charter-HALT paragraph deduplicated; all safety gates verbatim

---

## `do-design-audit`

<span class="delta">258 → 229 lines · description 348 → 198 chars</span>

- **Pass 1**: description trimmed
- **Pass 2**: rewritten deliverable-first; duplicate When-to-Use and Rating-Scale sections deleted
- The 10-dimension rubric contract untouched

<div class="warn">
Near-duplicate pair: do-design-audit and the user-level orphan do-design-review are almost the same skill. #1883 must pick a survivor.
</div>

---

## `frontend-design`

<span class="delta">132 → 63 lines · description 253 → 150 chars</span>

- **Pass 2's philosophical fix**: the body tried to teach *taste by enumeration*, roughly 45 DO/DON'T bullets
- Replaced with **design intent plus sharp anti-goals per section**: say what good looks like and what to refuse, and trust the model's judgment in between
- Every AI-slop fingerprint that do-design-audit cross-references was preserved
- The 7 reference files were already right and went untouched

---

## `pencil-design`

<span class="delta">196 → 194 lines · description 470 → 181 chars</span>

- **Pass 1**: description cut 61 percent, keeping the NOT-trigger boundary that stops it firing on ordinary CSS work
- Removed the repo-specific "Distribution" section: hardlink-sync mechanics are always-true policy already covered by CLAUDE.md, the first `flagged_for_claude_md` finding of the fleet
- **Pass 2**: doubled preamble folded; all failure-history rails kept exact

---

## `do-presentation`

<span class="delta">340 → 323 lines · description 319 → 171 chars</span>

- **Pass 1**: description trimmed; brand-logo step flagged as the first sub-file candidate if the body grows
- **Pass 2**: goal-first opening now tied directly to the Step-10 verify checklist; version history deleted; Marp and seam contracts kept exact
- Fixed a **phantom cross-reference** in CONTENT_GUIDE.md pointing at CSS classes that no longer exist

This deck was produced by the renovated skill.

---

## The quiet ones: `mermaid-render` · `do-debrief` · `do-voice-recording`

| Skill | Pass 1 | Pass 2 |
|---|---|---|
| mermaid-render | description 324 → 179; byob doc link verified live | unchanged: already renovation-grade |
| do-debrief | description 281 → 175; seam indirection untouched | anti-patterns 7 → 3; the rest live once in their owning phases |
| do-voice-recording | description 392 → 189; a 38-line model citizen | unchanged: **reference implementation of the seam pattern**, seam verified accurate |

> The fleet's target state looks like do-voice-recording: tiny, portable, seam-aware, and true.

---

<!-- _class: lead -->
# Cluster 5
## Comms and channels
*email · google-workspace · telegram · checking-system-logs · reading-sms-messages · linkedin · x-com · authenticity-pass · sentry*

---

## `linkedin`

<span class="delta">800 → 138 → 131 lines</span>

- **Pass 1**: the fleet's largest monolith, split into 4 `references/` sub-files (dom-model, messages, posting, feed-engagement); diff-verified content-preserving
- **Pass 2**: duplicate BYOB stack explanation collapsed; Notes deduplicated; all DOM selectors exact
- The em-dash prohibition stays inline even though it duplicates user-global policy, because drafter subagent prompts need it verbatim
- Disposition: linkedin and x-com share a large skeleton (drafter delegation, persona cold-read loop, authenticity gate)

---

## `x-com`

<span class="delta">728 → 101 → 97 lines</span>

- **Pass 1**: second-largest monolith, split into 3 `references/` sub-files (dms, posting, timeline-engagement)
- **Pass 2**: two near-duplicate "Iterate: 3-4 rounds" sections inherited from the original were **consolidated into one 10-step loop** (posting.md 286 → 268)
- Shared-skeleton extraction with linkedin was evaluated and **deliberately rejected**: 5-vs-4 personas and inverted hashtag rules mean the identical residue is about 30 lines, less than the indirection would cost

---

## `google-workspace`

<span class="delta">290 → 107 lines</span>

- **Pass 1**: `user-invocable: false` added, since the skill exists for the model, not the slash menu
- **Pass 2**: the **largest single cut in the fleet**. The body read like a human-facing behavior guide; it is now contracts and recipes
- Kept exact: the tool-selection ladder, draft-first email policy, and composition constraints

> Deleting 63 percent of a skill while keeping 100 percent of its contract is the pass-2 thesis in one slide.

---

## `telegram`

<span class="delta">223 → 217 lines</span>

- **Pass 1**: already PASS, no changes
- **Pass 2**: every documented flag **empirically verified against the live CLI**
- Added the missing `--reply-to`, `--voice-note`, and `--await-reply` documentation
- Corrected a stale claim that sends go "through Telethon directly": they route through the bridge relay now

---

## `checking-system-logs`

<span class="delta">66 → 79 lines: it grew, on purpose</span>

- **Pass 2's clearest rot find**: the skill was **fully rotted**. Every recipe grepped `bridge.events.jsonl`, a file replaced long ago by the BridgeEvent Redis model
- An agent following this skill would have searched a file that no longer receives events
- Rebuilt from live surfaces: `analyze_logs.py`, Popoto queries, the current log table including `reflection_worker.log`, and `valor_session telemetry`
- One of two skills that got *longer* in pass 2. Accuracy beats brevity

---

## `sentry`

<span class="delta">92 → 70 lines</span>

- **Pass 1 rot fix**: hardcoded `/Users/valorengels/src/ai` replaced with `~/src/ai`, so the skill works on machines with other usernames; argument-hint `[--apply]` added
- **Pass 2**: two 20-line `python -c` blocks that differed only by `SENTRY_TRIAGE_APPLY=1` were **consolidated to one**, verified against the real gating in `reflections/sentry_triage.py`
- Disposition: the diverged user-level sentry copy and the `sentry-cli` orphan still need #1883 resolution

---

## `email` · `reading-sms-messages`

| Skill | Pass 1 | Pass 2 |
|---|---|---|
| email | description 268 → 196, all triggers kept, probe untouched | 70 → 67; the BYOB prohibition was stated 3 times, now once; seam verified current against `tools/valor_email.py` |
| reading-sms-messages | already PASS, no changes | every command verified against the live surface; untouched |

Verification without change is still a deliverable: these two are now *known* accurate, not assumed accurate.

---

## `authenticity-pass`

<span class="delta">137 → 129 lines · description 274 → 173 chars</span>

- **Pass 1**: description rewritten with a proper "Use when"; argument-hint `[draft-file-path]` added
- **Pass 2**: rationale prose compressed to one cited paragraph
- The PASS/BLOCK contract kept **byte-exact**: linkedin and x-com gate on it before anything goes live
- Model-tier proposal for #1883: this is a classification task, sonnet would suffice

---

<!-- _class: lead -->
# Cluster 6
## Thinking and meta
*analyze · grill-me · zoom-out · ontologies · weekly-review · reclassify · skillify · new-skill · pthread · tdd · deepen · observability · claude-standards*

---

## `pthread`: the fleet's biggest rewrite

<span class="delta">221-char description → 138 lines → 26 lines</span>

- **Pass 1** trimmed the description and flagged the real problem
- **Pass 2** confirmed it: the body was **pseudo-code fiction**. `spawn_subagent`, `thread_metrics`, and a wrong `run_in_background` claim describe an API that does not exist
- Full rewrite against real Agent-tool mechanics: batch independent calls in one message, self-contained prompts, never parallelize dependent work, worktree isolation for concurrent writers

<div class="warn">
flagged_for_claude_md: CLAUDE.md Development Principle 8 duplicates this skill's core rule. The policy lives in two places.
</div>

---

## `analyze`

<span class="delta">35 → 43 lines · description 556 → 192 chars</span>

- **Pass 1**: the fleet's most bloated description cut by two thirds; overflow triggers and the do-not-trigger guard moved verbatim into the body opening
- **Pass 2**: **graceful degradation added**. The skill depends on the `strategic-analyst` agent that only exists in this repo's `.claude/agents/`; on other machines it now runs the identical 5-lens protocol inline via general-purpose agents
- The second skill that grew in pass 2, and for the same reason: correctness

---

## `skillify` + `new-skill`: template convergence

<span class="delta">skillify 142 → 91 · new-skill 118 → 113 + 56-line WORKFLOW_TEMPLATE.md</span>

- **Pass 1**: skillify carried invalid frontmatter (`when_to_use`, `arguments`), migrated to `description` / `argument-hint`; its embedded generated-skill template updated to valid fields
- **Pass 2**: skillify's **drifted 54-line embedded template deleted**; it now defers to new-skill via a relative link, and both sync together
- new-skill is now the canonical owner of both skill shapes (`SKILL_TEMPLATE.md` + new `WORKFLOW_TEMPLATE.md`)
- Bonus: the version-history prescription was removed from the template, so newly generated skills stop shipping changelogs

---

## `reclassify`

<span class="delta">52 → 32 lines + new 24-line seam file · description 219 → 164 chars</span>

- **Pass 1**: flagged that the skill assumes this repo's `docs/plans/*.md` frontmatter convention while living in `skills-global/`
- **Pass 2**: resolved it with the seam pattern. A probe sentence was added, and `.claude/skill-context/reclassify.md` now declares the enforced type values, the immutability lock, and the validator hooks
- The body shrank 38 percent because the repo-specific half moved to where it belongs

---

## `weekly-review`

<span class="delta">138 → 104 lines · description 292 → 197 chars</span>

- **Pass 1**: description trimmed; minor flags noted (saves to `/tmp/`, offers macOS TextEdit)
- **Pass 2**: a 12-item category menu replaced with **a principle plus 5 examples**. Enumerating every possible category is over-instruction; stating the categorization principle transfers better
- Version history dropped

---

## `claude-standards`

<span class="delta">123 lines · description 359 → 194 chars</span>

- **Pass 1**: description cut by 46 percent
- **Pass 2**: two repo-parochial inventory rows made portable
- Disposition: it overlaps do-skills-audit on the skills domain. Proposal is to carve that domain out and delegate, and its 7 `references/` guides arguably form a separate background reference skill

---

## Already sharp: six skills, near-zero deltas

| Skill | What happened |
|---|---|
| grill-me | desc 230 → 188 in pass 1; zero pass-2 changes |
| tdd | repo-agnostic lint/format generalization only; zero pass-2 changes |
| observability | desc 266 → 197; zero pass-2 changes |
| zoom-out | desc 235 → 189; one duplicated trigger sentence removed |
| deepen | desc 245 → 176; read-only rule deduped 3x → 1x |
| ontologies | desc 223 → 182; repo-domain example swapped for a generic Order checkout example |

> A renovation that changes nothing where nothing is wrong is evidence the rubric measures the right things.

---

<!-- _class: lead -->
# Cluster 7
## CMA and external
*imagine-agent · build-agent · do-discover-paths · computer-use · officecli · ebook-ingest*

---

## `do-discover-paths`

<span class="delta">213 → 106 lines + new 56-line seam file</span>

- **Pass 1** found the governance gap: the body invokes this repo's toolchain (`tools/happy_path_*.py`, Rodney scripts) while living in `skills-global/`, and rule 13 missed it because `python tools/...` is outside the coupling-signal set
- **Pass 2** applied the seam convention: probe added, `.claude/skill-context/do-discover-paths.md` declares the validator, generator, Rodney, and `BYOB_ALLOW_EVAL` specifics
- Generic baseline on foreign machines: write the trace and stop
- Trace schema, actions, and selector rules kept exact; a duplicated minified JS one-liner deleted

---

## `officecli`

<span class="delta">412 → 420 lines: grew via verification</span>

- **Pass 1**: description trimmed to 200 chars; argument-hint added
- **Pass 2**: **every documented command empirically run against the live CLI v1.0.128**. The installed 1.0.29 binary rejects half the documented surface
- Added a stale-binary guard: on "Unrecognized command", re-run the installer
- Documented three commands that existed but were absent from the skill (`save`/`import`/`merge`, plus svg/screenshot/forms views and the resident-mode flush footgun)

---

## `imagine-agent`

<span class="delta">103 → 101 lines · description 495 → 193 chars</span>

- **Pass 1 rot fix**: `references/build-sheet.md` was a **mis-written relative path** that resolved nowhere. Corrected to `../build-agent/references/build-sheet.md`, which works both in-repo and in synced installs
- The imagine-agent → build-agent handoff contract stayed intact
- **Pass 2**: duplicated opening merged; the interview protocol confirmed judgment-first

---

## `ebook-ingest`

<span class="delta">762-char description → 198 · body 444 → 289 lines</span>

- **Pass 1**: the fleet's longest description cut 74 percent, NOT-triggers moved to the body opening
- The interesting fix: two prose blocks saying "save this as `scripts/X.py`" were **extracted into real bundled scripts** (`annas_get.py`, `clean_book.py`), compile- and ruff-verified
- Code that ships as prose is code that silently rots; now it is code
- **Pass 2**: overview rewritten deliverable-first around `library/processed/<slug>.md` with success criteria

---

## Untouched by design: `build-agent` · `computer-use`

| Skill | Pass 1 | Pass 2 verdict |
|---|---|---|
| build-agent | description 369 → 194 | untouched: its API references are **battle-scarred contracts**, every line earned by a real failure |
| computer-use | description 310 → 193; probe verbatim | untouched: **model citizen of the seam convention**, all seam claims verified |

- build-agent carries a #1883 model-tier proposal: fable, because a client-facing launch has billing consequences

---

<!-- _class: lead -->
# Cluster 8
## Machine ops
*setup · update · prime · do-deploy · do-deploy-example*

---

## `setup`

<span class="delta">620 → 153 → 154 lines</span>

- **Pass 1**: monolith split into 5 phase-organized `references/` sub-files; dead `install_reflections.sh` / `reflections.py` references replaced with the then-current worker scheduler reality
- **Pass 2**: that pass-1 fix was **already invalidated**. #1828 merged mid-branch and split reflections into their own subprocess
- Step 8 now installs all three services (worker, reflection-worker, sdlc-reflection)

<div class="stat">
A fresh machine set up from the pass-1 text would never have run reflections. Renovation is not a one-time event.
</div>

---

## `update`

<span class="delta">338 → 143 → 120 lines</span>

- **Pass 1**: bonus monolith split (`modules.md` + `troubleshooting.md`); `disable-model-invocation: true` added; same reflections rot fixed as setup
- **Pass 2**: reconciled with #1828: installs and verifies `com.valor.reflection-worker`, probes with `python -m reflections --dry-run`, watches `reflection_worker.log`
- Stale unbuilt BYOB bump/rollback machinery in modules.md replaced with a design pointer
- New troubleshooting section for the reflection scheduler

---

## `prime`

<span class="delta">104 → 94 lines</span>

- **Pass 1**: no changes, one stale-comment flag
- **Pass 2**: the onboarding guide was **describing a two-year-old architecture**: no Redis queue, no worker, no reflection subprocess, and an orphaned permission-model table
- Rebuilt around the current flow: bridge → Redis queue → worker → SDK / granite-PTY, plus out-of-process reflections
- The C-Thread/L-Thread taxonomy deleted; an agent-integration rule added (`[project.scripts]` visibility)

---

## `do-deploy` · `do-deploy-example`

| Skill | Pass 1 | Pass 2 |
|---|---|---|
| do-deploy | description 231 → 146 | 140 → 142: Step 3 broadened to bridge **and worker** health; an overclaiming comment fixed; report template gains a worker-status line |
| do-deploy-example | trigger phrase added; **wrong template copy source fixed** (`skills/` → `skills-global/`) | unchanged: every line is contract, safety rail, or customization guide |

---

<!-- _class: lead -->
# Aftermath
*What broke, what got flagged, what it taught us*

---

## The test reconciliation

Generalizing skill bodies broke **30 unit tests** that grep skill prose. A dedicated repair agent classified every failure instead of patching blindly:

- **3 were real**: #1690 barrier invariants, restored to do-plan-critique verbatim
- **The rest were test-side**: retargeted to assert the same contract at its new seam location
- Several were **already failing against origin/main**: PR #1806 had genericized a skill without updating them

Result: full 9-file content-test set 141 passed; 11 neighboring files 244 passed. The 76 remaining `tests/unit/` failures are all pre-existing on main and none touch skills.

---

## Dispositions for #1883: placement and seams

Flagged, aggregated, and **deliberately not executed**. These need human sign-off:

| Item | Question |
|---|---|
| do-sdlc | Doc says Bucket C project-only; it lives in skills-global. Which is right? |
| do-discover-paths rule-13 gap | Extend the coupling-signal set to catch `python tools/...`? |
| analyze | Ship the strategic-analyst agent definition, or keep inline fallback only? |
| reclassify | Seam probe landed in pass 2; confirm it stays global |
| Model tiers | build-agent → fable; authenticity-pass → sonnet |

---

## Dispositions for #1883: merges and orphans

<div class="cols">
<div class="path-card">
<strong>Merge candidates</strong>

- audit-models → do-oop-audit
- do-design-audit vs do-design-review: pick a survivor
- new-audit-skill → fold into new-skill
- claude-standards: carve the skills domain out, delegate to do-skills-audit
- audit-tools vs audit-next-tool: one owner
</div>
<div class="path-card">
<strong>User-level orphans</strong>

No repo source, need a decision:

- audit-next-tool
- do-design-review
- get-telegram-messages
- searching-message-history
- sentry-cli
- the diverged user-level sentry copy
</div>
</div>

---

## What the fleet taught us

- **Rot is the default state.** Three skills described systems that no longer exist (checking-system-logs, prime, pthread). Nobody noticed because nothing lints prose against reality
- **Duplication taxes every invocation.** do-build paid 2x tokens per call for months; rules stated 3 times cost 3 times, forever
- **The seam convention works.** Repo specifics move to `.claude/skill-context/` and `docs/sdlc/`; the same skill body now reads cleanly from a Rust repo
- **Verification is a deliverable.** telegram and officecli gained accuracy by running every command; five skills earned "untouched" as a verdict, not a default
- **Renovations decay too.** Pass 1's reflections fix was invalidated by #1828 before the branch merged

---

## Summary

1. **All 60 skills renovated in two passes**: 7 monoliths and 65 content warnings went to zero, and the description budget dropped 36 percent while keeping every trigger phrasing

2. **Pass 2 changed what the skills *say*, never what they *do***: goal-first bodies, deduplicated rules, verified claims, with commands, schemas, gates, and probe sentences kept byte-exact

3. **The judgment calls are queued, not buried**: every merge candidate, placement question, and orphan is aggregated in #1883 for human sign-off

---

## What happens next

- **Human review before merge.** Description and trigger-surface changes affect skill firing on every machine
- After merge, the next `/update` **re-syncs hardlinks**, clearing the 47 transient rule-20 divergence warnings
- **#1883 disposition pass**: merges, moves, orphans, and model tiers, each needing a yes or no

<br>

**Read more**: PR #1894 · issue #1883 · `do-skills-audit/references/rubric.md` · `docs/features/skill-context-convention.md`
