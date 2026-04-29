---
status: Planning
type: chore
appetite: Medium
owner: Tom Counsell
created: 2026-04-29
tracking: https://github.com/tomcounsell/ai/issues/1207
last_comment_id:
---

# Drop Plan-Completion Gate; Shift Checkbox Responsibility to /do-pr-review and /do-patch

## Problem

The Plan Completion Gate at `.claude/commands/do-merge.md:179-249` runs a 71-line bash+Python script that scans the plan markdown for any `- [ ]` outside `## Open Questions` and `## Critique Results`. It produces zero real catches in the audited window — every "failure" is a stale checkbox on a deliverable that has actually shipped.

**Current behavior:**

1. `/do-merge` reads the plan, walks every `- [ ]`, and fails the merge if any unchecked checkbox remains outside the two excluded sections.
2. To satisfy the gate, agents emit a docs-only "tick off completed plan items" commit on the session branch.
3. That tick-off commit lands AFTER the prior `/do-pr-review` approval, which invalidates the approval under the review-comment gate's commit-SHA freshness check.
4. `/do-pr-review` re-runs. If clean, the agent dispatches `/do-merge` again; otherwise the cycle repeats.
5. ~15% of recent SDLC PRs contain a "tick off" commit visible in `git log --grep="tick off|check off"` — confirmed: `79d9f8dc` (#1139), `6b6bf868` (#1140), `9142dade` (#1032), `8a0cda69` (#1067), `d4e4b15f` (#1064), `77519777` (#1023), `6a522469`, `8696ba0b` (#1156), `d5be75e3` (#1157), `18742489` (#1156).

The gate is also a worse, dumber duplicate of `/do-pr-review`'s plan validation:
- `code-review.md:207-228` — Step 4b iterates per-criterion against the diff, classifies blocker/warning per section.
- `code-review.md:288` — Rubric item 1 ("Plan vs. implementation match") is critical and must be evaluated.
- The anti-rubber-stamp rubric (PR #1045) makes the review skill a stronger plan validator than a checkbox-counter.

**Desired outcome:**

1. The Plan Completion Gate (lines 179–249 of `.claude/commands/do-merge.md`, plus the failure-handling line below) is removed.
2. The `allow_unchecked: true` plan-frontmatter override — which exists only to bypass this gate — is removed from every consumer.
3. `/do-pr-review` writes ticks directly when emitting an Approved verdict, **and** unticks any criterion it confirms NOT satisfied or acknowledged-deferred. This closes the dishonest-tick loophole where a prior round's premature `[x]` survives into the next review.
4. `/do-patch` ticks any acceptance criterion it addressed when fixing a review blocker, in the same commit as the fix — no separate "tick off" commit.
5. No new gate is added. The existing per-criterion plan validation in `/do-pr-review` is sufficient.

## Freshness Check

**Baseline commit:** `54ae55d7` (main HEAD at plan time, 2026-04-29)
**Issue filed at:** 2026-04-29T06:41:28Z (today)
**Disposition:** Minor drift — the issue's recon underestimates the blast radius. Several downstream artifacts reference the gate that the issue's "Recon Summary" did not enumerate. The plan corrects this.

**File:line references re-verified:**
- `.claude/commands/do-merge.md:179-249` — Plan Completion Gate section — confirmed exact lines, content matches issue description.
- `.claude/skills/do-pr-review/SKILL.md:228-237` — Plan Validation step — still present.
- `.claude/skills/do-pr-review/SKILL.md:239-257` — Verification Checks (4.5) — still present.
- `.claude/skills/do-pr-review/sub-skills/code-review.md:207-228` — Step 4b "Plan Checkbox Validation" — confirmed; iterates per-criterion against the diff. **This is the additive write site for tick/untick** (issue's Edit B).
- `.claude/skills/do-patch/SKILL.md:94-143` — Step 2 builder agent prompt — receives full plan + PR review comments. **This is the additive write site for tick on fix** (issue's Edit C).

**Cited sibling issues/PRs re-checked:**
- #443 (closed 2026-03-24) — original plan-completion-gate motivator — closed; gate shipped via #506.
- #506 (merged 2026-03-24) — implementation PR — merged.
- #1155 (closed 2026-04-24) — self-healing merge gate epic — merged via #1160. Hardened the gate; this issue removes one of its rungs.
- #1186 (open) — three-gaps issue. Plan at `docs/plans/sdlc-workflow-three-gaps.md`. **Finding 2 of #1186 is the explicitly opposite approach** (have `/do-build` tick the boxes). #1186 has no PR yet; `scripts/tick_plan_checkboxes.py` does NOT exist on main. This work supersedes #1186 Finding 2.
- #1045 (closed) — anti-rubber-stamp rubric — merged. Cited as evidence `/do-pr-review` is the stronger plan validator.

**Commits on main since issue was filed (touching referenced files):** None — issue filed today, only commit since is `54ae55d7` (worker heartbeat fix, unrelated).

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/sdlc-workflow-three-gaps.md` — overlap on plan-checkbox-tick concern. **Decision:** This plan's PR will note that #1186 Finding 2 is superseded; #1186 Findings 1 (TEST guard) and 3 (ruff baseline) are unaffected and should still ship via #1186.

**Notes — additional references not enumerated by the issue's recon:**
- `agent/pipeline_state.py:813-818` — comment references how `/do-merge` returns `GATES_FAILED`. Read-only context. No code change needed; the comment is generic about "gates", not specifically the plan-completion gate.
- `tests/unit/test_pm_persona_guards.py:170-190` — `TestGateRecoveryBehavior` class enumerates `COMPLETION_GATE` as a required blocker category and asserts `allow_unchecked` mention with "never/human" enforcement. **Both assertions need to be deleted** when the gate is removed.
- `tests/unit/test_do_merge_review_filter.py:18,97` — references `GATES_FAILED` but in the context of the **review-comment** gate, not the plan-completion gate. No change needed.
- `config/personas/project-manager.md:114-160` — PM persona's "Gate-Recovery Behavior" section enumerates `COMPLETION_GATE` (line 134) and forbids `allow_unchecked: true` (lines 134, 157-160). Both must be removed.
- `docs/features/self-healing-merge-gate.md:5,57,103-110` — feature doc cross-links `plan-completion-gate.md`, lists `COMPLETION_GATE` as a blocker category, and says the section "Explicitly forbids setting `allow_unchecked: true`". All references must be updated.
- `docs/features/merge-gate-baseline.md:54` — diagram has `exit 1 -> GATES_FAILED` but in the review-comment-gate context. No change needed.
- `docs/sdlc/merge-troubleshooting.md` — has a full "Unchecked Plan Checkboxes" section (lines 50-76) AND a row in the troubleshooting table (line 242). Both must be removed.
- `docs/features/README.md:89` — index entry for plan-completion-gate.md. Must be removed.

## Prior Art

- **#506 (merged 2026-03-24)** — *Add plan completion gate to prevent premature plan completion.* Implemented the gate that this work removes. Closed by adding the bash+Python checkbox scanner to `do-merge.md`.
- **#443 (closed 2026-03-24)** — *Pipeline drops plan requirements.* The motivating issue; the original concern was that `/do-docs` set `status: Complete` regardless of unfinished work. That concern is now handled by `/do-pr-review`'s per-criterion validation in step 4b — far stronger than checkbox-counting.
- **#1155 / #1160 (closed/merged 2026-04-24)** — *Self-healing SDLC merge gate.* Hardened the gate ladder (review-comment, completion, lockfile, full-suite, merge-conflict). This issue removes one rung (completion) but leaves the rest intact.
- **#1045 (closed)** — Anti-rubber-stamp rubric in `/do-pr-review`. Made the review skill produce a mechanically-derived verdict per criterion. This is the foundation that makes dropping the gate safe.
- **#1186 (open)** — *SDLC workflow three gaps.* Finding 2 proposes the OPPOSITE approach: have `/do-build` tick the boxes. **Mutually exclusive with this work.** #1186 has not shipped. This issue's plan supersedes Finding 2 there.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #506 (the gate itself) | Counted unchecked `- [ ]` items in plan and blocked merge if any remained. | Blocked on cosmetic state (a checkbox that was never ticked) instead of on substantive state (whether the deliverable shipped). The actual deliverable is the `docs/features/{slug}.md` page and the diff itself; the plan checkbox is a transient artifact. |
| #1155 / #1160 (self-healing hardening) | Added recovery paths for COMPLETION_GATE failures (PM persona, troubleshooting playbook, "never set allow_unchecked" guardrail). | Hardened the gate without questioning whether the gate should exist. The recovery paths still treat a stale checkbox as a real blocker. The agent still needs a "tick off" commit to recover. |
| Per-PR "tick off" commits (recurring) | Manually ticked the boxes after the work shipped. | Each commit invalidates the prior approval (review-comment gate freshness check), forcing re-review. Net effect: the gate creates the very oscillation it claims to prevent. |

**Root cause pattern:** The gate validates a *side artifact* (checkbox state) instead of the *substance* (does the diff deliver the criterion). Substance validation is `/do-pr-review`'s job, and it already does it per-criterion. The fix is not to harden the gate further but to delete it and let the upstream validator be the source of truth.

## Architectural Impact

- **New dependencies:** None. Pure deletion + skill-prompt edits.
- **Interface changes:** `/do-pr-review`'s skill prompt gains a tick/untick write step; `/do-patch`'s builder prompt gains a "tick the criterion in the same commit" instruction. No CLI or Python interface changes.
- **Coupling:** Decreases. `/do-merge` no longer depends on plan markdown structure for the merge decision. The plan markdown becomes a build-time artifact (used by `/do-build`, `/do-pr-review`) rather than a merge-time artifact.
- **Data ownership:** `docs/features/{slug}.md` becomes the unambiguous durable artifact for "what shipped." Plans remain transient (they migrate to `docs/plans/completed/` post-merge).
- **Reversibility:** High. Restoring the gate is a markdown copy-paste from the deleted block. The tick/untick writes in `/do-pr-review` and `/do-patch` are also pure prompt edits; reversing them is a one-line revert per skill.

## Appetite

**Size:** Medium

**Team:** Solo dev (one builder for the skill prompt edits; one validator for verification)

**Interactions:**
- PM check-ins: 1 (criterion-mapping approach was decided in spike resolution; one human checkpoint before build to confirm the LLM-judge approach is acceptable)
- Review rounds: 1 (this is a chore — no novel architecture, no new dependencies)

The work is mechanical (delete a block, edit two skill prompts, update docs/tests). The risk is in *forgetting* a downstream artifact — addressed by the freshness check above.

## Prerequisites

No prerequisites — this work has no external dependencies. All edits are inside the repo.

## Spike Results

### spike-1: Where in `/do-pr-review` does the per-criterion judgment already happen?
- **Assumption**: "/do-pr-review already iterates each acceptance criterion against the diff, so the tick/untick write is a free additive step."
- **Method**: code-read
- **Finding**: Confirmed at `.claude/skills/do-pr-review/sub-skills/code-review.md:207-228` (Step 4b) — walks each unchecked `- [ ]` in `## Acceptance Criteria` / `## Success Criteria`, classifies each as BLOCKER (unaddressed) or silently passes (addressed). The Rubric item 1 at line 288 ("Plan vs. implementation match") then mechanically derives a per-criterion pass/fail/acknowledged. The LLM has already produced the per-criterion verdict.
- **Confidence**: high
- **Impact on plan**: Edit B is purely an additive write step (read the per-criterion verdicts, write the corresponding `[x]` / `[ ]` to the plan file, commit). No new judgment logic is needed.

### spike-2: Can `/do-patch`'s builder agent reliably identify which criterion a blocker addresses?
- **Assumption**: "The builder agent can map a review blocker to a specific acceptance criterion (or correctly say 'no mapping') given the full plan + review comments + failure context."
- **Method**: code-read
- **Finding**: At `.claude/skills/do-patch/SKILL.md:94-143` (Step 2), the builder receives PLAN CONTEXT (full plan with all criteria), TRACKING ISSUE, BUILD HISTORY, FAILURE TO FIX, and PR REVIEW COMMENTS. The builder is already instructed to "stay aligned with the plan" and to report what it changed. Adding a step to identify the matching criterion is a small prompt extension — the builder has all the inputs.
- **Confidence**: medium-high. The risk is that the builder over-fits (claims a mapping where none exists) or under-fits (misses an obvious match). Mitigated by the failure path: when the builder is uncertain, it MUST emit "no criterion match — manual tick required" rather than guess.
- **Impact on plan**: Edit C extends the builder prompt with a tick-the-criterion step at fix time. The builder reports its mapping; the patch skill writes the tick to the plan file in the same commit as the code change. If the mapping is null, no tick is written and the patch commit message notes the gap.

### spike-3: How do consumers parse plan checkbox sections today?
- **Assumption**: "There is a shared regex/parser for `- [ ]` items in plan sections; both `/do-pr-review`'s step 4b and the existing gate use the same approach."
- **Method**: code-read
- **Finding**: The existing gate uses inline Python in `do-merge.md:203-248` (regex `r'^[ \t]*- \[ \] (.+)'` plus section-name tracking, excluding `Open Questions` and `Critique Results`). `/do-pr-review`'s step 4b walks "the following plan sections" but does not embed Python — it leaves the parsing to the LLM. There is no shared helper module.
- **Confidence**: high
- **Impact on plan**: For Edit B and C, we will introduce a small helper at `tools/plan_checkbox_writer.py` (Python module + CLI) that performs the read-modify-write of a single criterion's checkbox state. This is so that:
  - The skill prompts don't have to embed regex Python (cleaner skill files).
  - The behavior is unit-testable (Test Impact section requires this).
  - The same module is used by both `/do-pr-review` and `/do-patch`, ensuring identical semantics.
  This is NOT a new gate; it's a write-side helper. No validation logic.

## Solution

### Key Elements

- **Edit A (deletion)**: Remove the Plan Completion Gate from `.claude/commands/do-merge.md` (lines 179-249, plus the line below at 251). Remove every downstream reference to it (PM persona, merge-troubleshooting playbook, feature docs, tests).
- **Edit B (do-pr-review tick/untick)**: When `/do-pr-review` emits Approved, walk the plan's `## Acceptance Criteria` section and for each criterion, write `[x]` if the rubric/step-4b judgment confirmed it satisfied, write `[ ]` if it confirmed not-satisfied or acknowledged-deferred. Commit to the PR branch.
- **Edit C (do-patch tick on fix)**: When `/do-patch` fixes a review blocker, the builder identifies which acceptance criterion (if any) the fix addresses. The patch skill writes `[x]` to that criterion in the same commit as the fix.
- **Edit D (helper module)**: New `tools/plan_checkbox_writer.py` — a small Python module + CLI that performs the read-modify-write of plan checkboxes safely. Used by both Edit B and Edit C.
- **Edit E (docs cleanup)**: Delete `docs/features/plan-completion-gate.md`, remove its index entry, migrate the obsolete `docs/plans/plan_completion_gate.md` to `docs/plans/completed/`, prune `docs/sdlc/merge-troubleshooting.md`, update `docs/features/self-healing-merge-gate.md` cross-links.
- **Edit F (PM persona + tests)**: Remove the COMPLETION_GATE row from the PM persona's blocker→remediation table and the `allow_unchecked` prohibition; delete the `test_allow_unchecked_prohibited` test and the `COMPLETION_GATE` assertion from `test_pm_persona_guards.py`.

### Flow

**Before this work** (current behavior on a clean PR):
PR Approved → tick-off commit → review staleness → `/do-pr-review` re-run → Approved (again) → `/do-merge` → completion gate passes → merged.

**After this work** (desired behavior):
PR Approved (review writes ticks) → `/do-merge` → no completion gate → merged.

For a PR with patched-out blockers:
Initial review → blockers found → `/do-patch` fixes (and ticks the relevant criterion in the same commit) → re-review → all clear (review writes any remaining ticks, unticks any dishonest ticks) → `/do-merge` → merged.

### Technical Approach

- **Edit A — Pure deletion in `do-merge.md`.** Delete lines 179-251 (the `### Plan Completion Gate` heading, the bash+Python script, and the failure-handling line below). Verify the surrounding gates (review-comment gate at lines 100-178, lockfile gate at line 253+) remain intact and correctly sequenced. The merge gate ladder remaining: review-comment, lockfile, docs (per `docs/sdlc/do-merge.md`), CI checks. No new gate added.
- **Edit B — Tick/untick writes in `/do-pr-review`.** The existing skill flow: Step 4 (Plan Validation) → Step 4b (Plan Checkbox Validation) → Step 5 (Verification Checks) → Pre-Verdict Checklist → Rubric → Verdict derivation. The tick/untick step is added between "Verdict derivation" and "Post review" (final commit/comment). It only fires for `APPROVED` verdicts. For each criterion in `## Acceptance Criteria`:
  - If Rubric item 1 marked it `pass` (or item-level pass for that criterion): write `- [x]`.
  - If Rubric item 1 marked it `fail` and there is no acknowledged-deferral verified for it: write `- [ ]` (untick prior dishonest tick).
  - If marked `acknowledged` (verified disclosure): write `- [ ]` (acknowledged-deferred is not "satisfied").
  - If the LLM cannot map a criterion to a verdict (low signal — e.g., the criterion's text is ambiguous or the diff is too large to assess), leave the existing checkbox state alone AND emit a comment line in the review body: `> Could not auto-tick "{criterion text}" — please review manually.`
  This commits to the PR branch as `docs(#{N}): sync plan checkboxes with review verdict`. If there is already a review comment commit in this round, fold the plan write into that commit (no separate commit).
- **Edit C — Tick on fix in `/do-patch`.** The builder agent (Step 2 prompt) is extended: "If your fix addresses a specific acceptance criterion from the plan's `## Acceptance Criteria`, identify which criterion (by exact text or first-line summary). Report this in your completion summary as `criterion_addressed: <text>` (or `criterion_addressed: null` if no clear match)." After the builder reports, the patch skill (Step 4 onward) reads the builder's `criterion_addressed` value:
  - If non-null and the criterion is currently `[ ]`: invoke the helper to write `[x]`. The plan-file edit is included in the same `git add -A && git commit` as the code fix. Commit message: `fix(#{N}): {one-line summary} — addresses "{criterion text}"`.
  - If null: no plan write. Commit message: `fix(#{N}): {one-line summary}`.
  - If the builder reports `criterion_addressed` but the criterion text doesn't match any item in the plan (string mismatch): treat as null, log a warning, no plan write.
- **Edit D — Helper module `tools/plan_checkbox_writer.py`.** Single-purpose CLI:
  ```
  python -m tools.plan_checkbox_writer tick   <plan_path> --criterion "<exact text>"
  python -m tools.plan_checkbox_writer untick <plan_path> --criterion "<exact text>"
  python -m tools.plan_checkbox_writer status <plan_path>  # JSON: list of criteria + current tick state
  ```
  Behavior:
  - `tick` / `untick` find the matching `- [ ] {text}` or `- [x] {text}` line in `## Acceptance Criteria` (case-insensitive match on the text portion, fuzzy whitespace tolerance) and rewrite it.
  - Returns exit 0 if the line was found and updated (or already in target state).
  - Returns exit 2 with a `MATCH_AMBIGUOUS` or `MATCH_NOT_FOUND` message if the criterion can't be uniquely identified — the caller skips the write and emits the manual-review comment.
  - `status` outputs JSON `[{"criterion": "...", "checked": true|false}, ...]` for the LLM to consume in `/do-pr-review`.
  The module is pure Python (no Markdown library), uses regex on the `## Acceptance Criteria` section block. No side effects beyond the file write. Easily unit-testable.
- **Edit E — Docs cleanup.** Mechanical:
  - Delete `docs/features/plan-completion-gate.md`.
  - Remove the row from `docs/features/README.md:89`.
  - `git mv docs/plans/plan_completion_gate.md docs/plans/completed/plan_completion_gate.md` (catching up the spec drift the issue noted).
  - In `docs/sdlc/merge-troubleshooting.md`: delete the "Unchecked Plan Checkboxes" section (lines 50-76) and the COMPLETION_GATE row (line 242).
  - In `docs/features/self-healing-merge-gate.md`: remove the `[Plan Completion Gate]` cross-link (line 5), remove `COMPLETION_GATE` from the blocker-category enumeration (line 103), delete the `allow_unchecked` paragraph (lines 109-111).
- **Edit F — PM persona + tests.**
  - In `config/personas/project-manager.md`: delete the COMPLETION_GATE row from the table at line 134 and the standalone `Never set allow_unchecked: true` paragraph at lines 157-160.
  - In `tests/unit/test_pm_persona_guards.py`: remove `"COMPLETION_GATE"` from the `test_blocker_categories_enumerated` assertion list (line 175) and delete the `test_allow_unchecked_prohibited` method entirely (lines 185-189).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/plan_checkbox_writer.py` — if the plan file is missing, malformed (no `## Acceptance Criteria` section), or unreadable, the CLI must exit non-zero with a clear error message (NOT silently no-op). Test: `test_plan_checkbox_writer_missing_file` and `test_plan_checkbox_writer_no_acceptance_section`.
- [ ] `/do-pr-review` tick/untick step — if the helper exits with `MATCH_AMBIGUOUS` or `MATCH_NOT_FOUND`, the skill must emit the manual-review comment and continue (do NOT fail the review). The skill prompt must explicitly state this fallback.
- [ ] No new `except Exception: pass` blocks in any touched file.

### Empty/Invalid Input Handling
- [ ] `tools/plan_checkbox_writer.py tick --criterion ""` — must reject empty criterion text with non-zero exit.
- [ ] If the plan file has zero `- [ ]` items in `## Acceptance Criteria` (already all ticked, or the section is empty), tick/untick is a no-op and exits 0.
- [ ] If the rubric verdict is `APPROVED` but there are zero criteria in the plan, the skill skips the tick/untick step entirely without error.

### Error State Rendering
- [ ] When the helper returns `MATCH_AMBIGUOUS`, the review comment includes the literal text `> Could not auto-tick "{criterion}" — please review manually.` (visible to humans on GitHub, not swallowed).
- [ ] When the helper returns `MATCH_NOT_FOUND` for a criterion the rubric judged satisfied, that's a real bug (the criterion was on the rubric but isn't in the plan file). The skill emits a stronger warning: `> Rubric judged criterion satisfied but no matching item in plan — investigate.`

## Test Impact

- [ ] `tests/unit/test_pm_persona_guards.py::TestGateRecoveryBehavior::test_blocker_categories_enumerated` — UPDATE: remove `"COMPLETION_GATE"` from the assertion list. Other categories (PIPELINE_STATE, REVIEW_COMMENT, LOCKFILE, FULL_SUITE, MERGE_CONFLICT, PARTIAL_PIPELINE_STATE) remain.
- [ ] `tests/unit/test_pm_persona_guards.py::TestGateRecoveryBehavior::test_allow_unchecked_prohibited` — DELETE: this test enforces that the PM persona explicitly forbids `allow_unchecked: true`. With the flag removed everywhere, the test has nothing to assert.
- [ ] `tests/unit/test_do_merge_baseline.py` — REVIEW: scan for any test asserting the existence of the Plan Completion Gate section in `do-merge.md` or the `allow_unchecked` keyword. If found, DELETE those assertions; if absent, no change. (Initial inspection suggests the file tests other gates — confirm at build time.)
- [ ] `tests/unit/test_do_merge_review_filter.py:18,97` — NO CHANGE: these `GATES_FAILED` references are about the review-comment gate, not the plan-completion gate.
- [ ] `tests/unit/test_validate_merge_guard.py` — REVIEW: `validate_merge_guard.py` is the merge-guard tokeniser referenced in `self-healing-merge-gate.md`. Confirm at build time whether it tokenises COMPLETION_GATE strings; if so, prune that token. Disposition: UPDATE if it does, no change if it doesn't.
- [ ] `tests/unit/test_plan_checkbox_writer.py` — CREATE (new): unit tests for `tools/plan_checkbox_writer.py` covering tick / untick / status / missing-file / malformed / ambiguous / not-found / empty-criterion / no-op-when-already-ticked / case-insensitive-match / whitespace-tolerance.
- [ ] `tests/unit/test_do_pr_review_tick_writes.py` — CREATE (new): test that when `/do-pr-review` emits APPROVED with a rubric verdict that judged a criterion satisfied, the helper is invoked with `tick` and the plan file ends up with `[x]` for that criterion. Critically, also test the **dishonest-tick unticking case**: a plan starts with `[x]` for an unsatisfied criterion, the rubric judges it FAIL, and the resulting plan-file mutation has `[ ]` for that criterion. (This is the issue's explicit verification requirement.)
- [ ] `tests/unit/test_do_patch_ticks.py` — CREATE (new): simulate a patch run where the builder reports `criterion_addressed: "X"` after fixing a blocker. Assert the resulting commit has BOTH the code change AND the plan-file checkbox flip in the same commit (no separate "tick off" commit). Also test the null-criterion path: builder reports `criterion_addressed: null`, no plan write happens, commit contains only the code change.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — REVIEW: this asserts SKILL.md and Python router stay in sync. Confirm at build time it doesn't reference COMPLETION_GATE; prune if it does.

## Rabbit Holes

- **Building a richer criterion-to-finding cross-reference protocol.** Tempting to design a structured `criterion_id` system in plan frontmatter so reviews and patches can map findings to criterion IDs unambiguously. **Avoid.** This is a separate and much larger project. The LLM-judge approach (with the manual-review fallback for ambiguous cases) is sufficient for this issue.
- **Replacing the gate with a "smarter" gate.** The whole point of this issue is to remove the gate, not replace it. Adding any pre-merge validator that scans the plan markdown — for any reason — is out of scope.
- **Migrating all old plans to a new format.** Some old plans may have plan-completion-gate-specific frontmatter (`allow_unchecked`). Touching them is a separate cleanup; the new `/do-pr-review` and `/do-patch` writers ignore unknown frontmatter fields, so old plans don't break.
- **Generalizing `tools/plan_checkbox_writer.py` to handle other plan sections.** Tempting to also support `## Test Impact`, `## Documentation`, etc. **Avoid.** This issue is about acceptance criteria specifically. Other sections can be added later if needed; the helper's API is forward-compatible.
- **Touching the `/do-build` plan-checkbox behavior.** `/do-build` already touches the plan during execution (e.g., adding spike results). Whether `/do-build` should ALSO tick boxes is exactly the question #1186 Finding 2 asked. This issue takes the opposite stance: only `/do-pr-review` and `/do-patch` tick — `/do-build` does not. Do NOT change `/do-build`'s plan-touching behavior in this work.

## Risks

### Risk 1: LLM mis-mapping a criterion in `/do-pr-review` causes a wrong tick/untick
**Impact:** A criterion gets `[x]` when the diff doesn't actually satisfy it (or `[ ]` when it does). Could mask a real gap (false positive tick) or cause a patch loop (false negative untick).
**Mitigation:** The tick/untick fires only when Rubric item 1 produced a confident `pass` / `fail` / `acknowledged` for that criterion. If the LLM is uncertain (the helper returns `MATCH_AMBIGUOUS` or the criterion text fuzzy-match score is too low), the existing checkbox state is preserved and a manual-review comment is emitted on the PR. The PR review comment makes any uncertainty visible to the human reviewer; ticks/unticks are a side-effect of the verdict, not a substitute for it.

### Risk 2: `/do-patch`'s builder agent over-claims a criterion mapping
**Impact:** Builder fixes a typo and claims it "addresses criterion X" — criterion X gets `[x]` when X actually requires more work.
**Mitigation:** The builder's `criterion_addressed` value is reported in the patch commit message and the next `/do-pr-review` round will untick if the criterion is actually unmet. The dishonest-tick unticking in Edit B is the safety net for any over-claim from Edit C. Plus: the patch skill prompt explicitly instructs the builder to err on the side of `criterion_addressed: null` when uncertain.

### Risk 3: Removing the gate exposes a class of stale-checkbox PRs that the gate was actually catching
**Impact:** The gate's audit shows zero real catches in the audited window — but the audit window may be biased toward shipped PRs. Hypothetical: the gate could have been catching cases that now silently slip through.
**Mitigation:** `/do-pr-review`'s step 4b already produces a BLOCKER for any unaddressed acceptance criterion regardless of checkbox state. That validation is per-criterion against the diff, not against the checkbox. So the substance check is unchanged; only the cosmetic check is dropped. If a substantive gap slips through, it's a `/do-pr-review` bug, not a gate-removal bug.

### Risk 4: Tests that assert the gate's existence break in unexpected places
**Impact:** A test in `tests/` that we didn't audit asserts the presence of the COMPLETION_GATE string or the `allow_unchecked` flag. Build fails on CI.
**Mitigation:** Test Impact section enumerates all known assertion sites. The build's test step (full pytest run) catches any missed sites. If a missed site is found at build time, add a test-impact entry and update the test in the same PR.

### Risk 5: `/do-pr-review` writes a dishonest-tick untick when the criterion was correctly ticked by `/do-patch` mid-cycle
**Impact:** `/do-patch` fixes a blocker, ticks criterion X. Re-review runs, the rubric is uncertain about X (because X depends on a sibling criterion not yet addressed), unticks X. Now criterion X looks unfinished even though it's done.
**Mitigation:** The unticking only fires when the rubric judgment is **confident-fail or confident-acknowledged** — not when uncertain. Uncertain criteria preserve existing state. Plus: the next round's review will re-tick if the criterion is genuinely satisfied. No oscillation amplifier — at worst, one extra tick/untick cycle, which is bounded by the number of review rounds (typically ≤2).

## Race Conditions

No race conditions identified — all operations are synchronous and single-threaded. The helper module performs read-modify-write on a single file; both `/do-pr-review` and `/do-patch` invoke it serially from the agent's command sequence. The PR branch is a serialized single-author timeline (the agent is the only writer); there is no concurrent commit risk inside a single SDLC pipeline run.

## No-Gos (Out of Scope)

- **No new merge-time validator.** No replacement gate. The merge gate ladder shrinks by one rung permanently.
- **No changes to `/do-build`'s plan-touching behavior.** `/do-build` does not tick acceptance criteria. (#1186 Finding 2 takes that approach; this work supersedes it.)
- **No changes to `/do-docs`'s plan status field handling.** `/do-docs` already writes `status: docs_complete` per `docs/features/plan-completion-gate.md`. That behavior is unrelated to checkbox state and stays as-is.
- **No retroactive cleanup of old plans.** Old plans with `allow_unchecked: true` in frontmatter are ignored by the new writers (unknown fields are tolerated).
- **No expansion of the helper to other plan sections.** `tools/plan_checkbox_writer.py` handles `## Acceptance Criteria` only.
- **No batching/optimization of the helper.** Each tick/untick is a separate call. Plans rarely have more than ~10 criteria; performance is not a concern.

## Update System

No update system changes required — this work is purely internal. No new dependencies, no new config files, no new services. The skill files (`.claude/`) and tools (`tools/`) live in the repo and are deployed by `git pull` on each machine. The next `/update` invocation propagates the changes automatically.

## Agent Integration

No new CLI entry point in `pyproject.toml [project.scripts]` is required. The new `tools/plan_checkbox_writer.py` is invoked as `python -m tools.plan_checkbox_writer ...` from inside the `/do-pr-review` and `/do-patch` skill flows — these skills already use `python -m tools.*` invocations regularly. The bridge does not need to import this module directly; it is exclusively a build-time helper used by the SDLC sub-skills.

Integration tests:
- The new `test_do_pr_review_tick_writes.py` simulates the full review flow with a real plan file and asserts the helper is invoked correctly.
- The new `test_do_patch_ticks.py` simulates a builder report and asserts the helper is invoked with the correct arguments.

These are unit-test-style integration tests (they exercise the prompt-output contract) — sufficient because the helper is pure Python and the skill flow's other components are already covered by existing tests.

## Documentation

### Feature Documentation
- [ ] Delete `docs/features/plan-completion-gate.md`.
- [ ] Remove the row at `docs/features/README.md:89` that links to plan-completion-gate.md.
- [ ] Create `docs/features/plan-checkbox-writers.md` describing the new tick/untick behavior in `/do-pr-review` and `/do-patch`, including the criterion-mapping approach (LLM-judge with manual-review fallback) and the helper module.
- [ ] Add the new feature doc to the `docs/features/README.md` index table.
- [ ] Update `docs/features/self-healing-merge-gate.md` to remove the `[Plan Completion Gate]` cross-link, remove `COMPLETION_GATE` from the blocker-category list, and delete the `allow_unchecked` paragraph.

### SDLC Documentation
- [ ] Update `docs/sdlc/merge-troubleshooting.md`: delete the "Unchecked Plan Checkboxes" section (lines 50-76) and the COMPLETION_GATE row in the troubleshooting table (line 242).
- [ ] Migrate `docs/plans/plan_completion_gate.md` to `docs/plans/completed/plan_completion_gate.md` (catching up the spec-drift the issue noted).

### Inline Documentation
- [ ] `tools/plan_checkbox_writer.py` — module docstring explaining purpose, CLI usage, and the failure-path semantics.
- [ ] `.claude/skills/do-pr-review/sub-skills/code-review.md` — comment block above the new tick/untick step explaining when it fires and when it falls back to manual review.
- [ ] `.claude/skills/do-patch/SKILL.md` — comment block above the builder prompt extension explaining the `criterion_addressed` reporting contract.

### Issue Cross-Link
- [ ] After this PR merges, post a comment on issue #1186 noting that Finding 2 is superseded by this work. Findings 1 (TEST guard) and 3 (ruff baseline) remain in scope for #1186.

## Success Criteria

- [ ] The Plan Completion Gate section at `.claude/commands/do-merge.md:179-249` is removed in full, along with the failure-handling line immediately below it. No remaining reference to "plan completion gate", "allow_unchecked", or "GATES_FAILED" from a checkbox check exists in `.claude/commands/do-merge.md`.
- [ ] `docs/features/plan-completion-gate.md` is deleted (or replaced with a 1-paragraph stub noting the feature was removed in this PR), and the entry is removed from `docs/features/README.md`.
- [ ] `docs/plans/plan_completion_gate.md` is migrated to `docs/plans/completed/` (catching up the spec-drift mentioned above) or deleted; either is acceptable.
- [ ] `/do-pr-review` writes ticks for each acceptance criterion it confirms satisfied when emitting an Approved verdict, and writes unticks for criteria it confirms not-satisfied or acknowledged-deferred. Verified by a unit test that runs review against a plan with one deliberately-mistakenly-ticked unsatisfied criterion and asserts the criterion gets unticked in the resulting plan-file mutation.
- [ ] `/do-patch` ticks any acceptance criterion it addressed when fixing a review blocker, in the same commit as the fix. Verified by a unit test that simulates a patch run and asserts the relevant criterion is ticked in the same commit as the code change (no separate "tick off" commit).
- [ ] `docs/sdlc/merge-troubleshooting.md` is reviewed and any reference to the plan-completion gate is removed or rewritten. (One reference confirmed by grep.)
- [ ] After the change, no "tick off completed plan items" / "check off completed deliverables" docs-only commit appears in any new PR's merge history. Verified by inspecting `git log` of the next 3 PRs after this one merges.
- [ ] Issue #1186 receives a comment from this PR's merge linking back here and noting that #1186 Finding 2 is superseded by this work.
- [ ] `tools/plan_checkbox_writer.py` exists with `tick`, `untick`, and `status` subcommands and is unit-tested.
- [ ] `tests/unit/test_pm_persona_guards.py` no longer asserts COMPLETION_GATE or `allow_unchecked` (one assertion updated, one test deleted).
- [ ] PM persona at `config/personas/project-manager.md` no longer references COMPLETION_GATE in its blocker→remediation mapping or contains the `allow_unchecked` paragraph.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` exit 0.
- [ ] `pytest tests/` passes with the updated test file and new test files.

## Team Orchestration

### Team Members

- **Builder (skill edits)**
  - Name: skill-builder
  - Role: Edit `do-merge.md` (delete gate), `do-pr-review/sub-skills/code-review.md` (add tick/untick step), `do-patch/SKILL.md` (extend builder prompt), `config/personas/project-manager.md` (remove COMPLETION_GATE row + allow_unchecked paragraph), and the docs cleanup files.
  - Agent Type: builder
  - Resume: true

- **Builder (helper module)**
  - Name: helper-builder
  - Role: Implement `tools/plan_checkbox_writer.py` with the tick / untick / status subcommands, plus the new test files (`test_plan_checkbox_writer.py`, `test_do_pr_review_tick_writes.py`, `test_do_patch_ticks.py`).
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: lead-validator
  - Role: Verify all Success Criteria, run pytest + ruff, confirm grep-counts for residual references are zero.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder, validator (default Tier 1).

## Step by Step Tasks

### 1. Build helper module
- **Task ID**: build-helper
- **Depends On**: none
- **Validates**: tests/unit/test_plan_checkbox_writer.py (create)
- **Informed By**: spike-3 (helper API decided)
- **Assigned To**: helper-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `tools/plan_checkbox_writer.py` with `tick` / `untick` / `status` subcommands.
- Use regex to parse the `## Acceptance Criteria` section block.
- Return non-zero on `MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND` / missing file / malformed / empty criterion.
- Write `tests/unit/test_plan_checkbox_writer.py` covering the failure-path test strategy items above.

### 2. Edit `/do-merge` (delete gate)
- **Task ID**: build-edit-merge
- **Depends On**: none
- **Validates**: grep returns zero matches in `.claude/commands/do-merge.md` for "plan completion gate", "allow_unchecked", "COMPLETION GATE FAILED"
- **Informed By**: freshness check (lines 179-251 confirmed)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete lines 179-251 from `.claude/commands/do-merge.md` (gate heading, bash+Python script, failure-handling line).
- Verify surrounding gates (review-comment gate above, lockfile gate below) remain intact.

### 3. Edit `/do-pr-review` (tick/untick on Approved)
- **Task ID**: build-edit-pr-review
- **Depends On**: build-helper
- **Validates**: tests/unit/test_do_pr_review_tick_writes.py (create)
- **Informed By**: spike-1 (per-criterion verdict already happens in step 4b)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a "Plan Checkbox Sync" step in `.claude/skills/do-pr-review/sub-skills/code-review.md` between the rubric and the post-review steps, fired only on `APPROVED` verdicts.
- For each criterion in `## Acceptance Criteria`, invoke `python -m tools.plan_checkbox_writer tick` or `untick` based on the rubric verdict.
- On `MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND`, emit the manual-review comment in the review body and skip the write.
- Commit the plan changes to the PR branch (fold into the review-comment commit if one exists).
- Write the dishonest-tick-unticking test case (key verification per acceptance criteria).

### 4. Edit `/do-patch` (tick on fix)
- **Task ID**: build-edit-patch
- **Depends On**: build-helper
- **Validates**: tests/unit/test_do_patch_ticks.py (create)
- **Informed By**: spike-2 (builder has full plan + review comments)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend the Step 2 builder prompt in `.claude/skills/do-patch/SKILL.md` to instruct the builder to report `criterion_addressed: <text>` or `criterion_addressed: null` after the fix.
- Add a Step 3.5 ("Sync Plan Checkbox") that reads the builder's report and invokes the helper if non-null.
- Ensure the plan-file edit is staged and committed in the SAME commit as the code change (no separate tick-off commit).
- Write the test verifying single-commit behavior.

### 5. PM persona + tests cleanup
- **Task ID**: build-edit-pm-tests
- **Depends On**: none
- **Validates**: tests/unit/test_pm_persona_guards.py passes after edits; grep returns zero matches in `config/personas/project-manager.md` for "COMPLETION_GATE" and "allow_unchecked"
- **Informed By**: freshness check (locations enumerated)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove the COMPLETION_GATE row from the table at `config/personas/project-manager.md:134`.
- Remove the `Never set allow_unchecked: true` paragraph at lines 157-160.
- In `tests/unit/test_pm_persona_guards.py`: remove `"COMPLETION_GATE"` from the `test_blocker_categories_enumerated` assertion list; delete the `test_allow_unchecked_prohibited` method.

### 6. Docs cleanup
- **Task ID**: build-docs-cleanup
- **Depends On**: build-edit-merge, build-edit-pr-review, build-edit-patch
- **Validates**: grep in `docs/` returns zero non-historical matches for "plan-completion-gate" / "Plan Completion Gate" / "COMPLETION_GATE" / "allow_unchecked"
- **Informed By**: freshness check (downstream artifacts enumerated)
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `docs/features/plan-completion-gate.md` and remove the row from `docs/features/README.md:89`.
- `git mv docs/plans/plan_completion_gate.md docs/plans/completed/plan_completion_gate.md`.
- Prune `docs/sdlc/merge-troubleshooting.md`: delete "Unchecked Plan Checkboxes" section and the COMPLETION_GATE table row.
- Update `docs/features/self-healing-merge-gate.md`: remove cross-link, prune blocker-category list, delete `allow_unchecked` paragraph.
- Create `docs/features/plan-checkbox-writers.md` describing the new behavior; add to the README index.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: all of the above
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/` — must pass.
- Run `python -m ruff check . && python -m ruff format --check .` — must pass.
- Grep for residual references: `grep -rn "plan-completion-gate\|allow_unchecked\|COMPLETION_GATE\|Plan Completion Gate" .claude/ config/ docs/ scripts/ agent/ tools/ tests/` — should return zero non-historical matches (some prior plan files in `docs/plans/completed/` may legitimately reference the old gate; those are OK).
- Confirm all Success Criteria items are met.
- Report final pass/fail summary.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Gate text removed from do-merge | `grep -c "Plan Completion Gate" .claude/commands/do-merge.md` | output 0 |
| allow_unchecked removed from PM persona | `grep -c "allow_unchecked" config/personas/project-manager.md` | output 0 |
| COMPLETION_GATE removed from PM persona | `grep -c "COMPLETION_GATE" config/personas/project-manager.md` | output 0 |
| plan-completion-gate.md deleted | `test ! -f docs/features/plan-completion-gate.md` | exit code 0 |
| plan_completion_gate.md migrated | `test -f docs/plans/completed/plan_completion_gate.md` | exit code 0 |
| Helper module exists | `python -m tools.plan_checkbox_writer status docs/plans/drop-plan-completion-gate.md` | exit code 0 |
| Helper tests exist | `test -f tests/unit/test_plan_checkbox_writer.py` | exit code 0 |
| Review tick/untick test exists | `test -f tests/unit/test_do_pr_review_tick_writes.py` | exit code 0 |
| Patch tick test exists | `test -f tests/unit/test_do_patch_ticks.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Resolved Decisions

These were the four open questions raised at plan time. All were resolved by the human reviewer (Tom Counsell) on 2026-04-29 prior to dispatching critique. The plan body above already encodes these decisions; this section preserves the rationale for auditability.

1. **Criterion-mapping approach — LLM-judge: APPROVED.** Use the existing per-criterion verdict from `/do-pr-review`'s rubric (Step 4b + Rubric item 1). For `/do-patch`, extend the builder agent's report to include `criterion_addressed: <text>`. When the LLM is uncertain, leave the existing checkbox state alone and emit a manual-review comment. Lexical match and explicit cross-reference protocols are explicitly **rejected** as fragile and over-engineered respectively. (Encoded in the Solution → Technical Approach section, Edits B and C.)

2. **Acknowledged-deferred = unticked: APPROVED.** The plan-doc tick answers "is this criterion satisfied?" — a verified disclosure means the deferral was legitimate, but the criterion is still unmet. This is exactly the failure mode the issue is closing (dishonest ticks for mocked APIs / deferred tests). Acknowledged-deferred criteria get unticked. (Encoded in Edit B's third bullet under "For each criterion in `## Acceptance Criteria`".)

3. **Helper location — `tools/plan_checkbox_writer.py`: APPROVED.** Repo convention per `CLAUDE.md` ("`tools/` — Local Python tools" for shared library code imported by skills). NOT `scripts/`. (Encoded in Edit D and the Step-by-Step Tasks; the alternative location was never written into the plan body.)

4. **Documentation — single combined doc at `docs/features/plan-checkbox-writers.md`: APPROVED.** Both behaviors share the helper module, the LLM-judge mapping rationale, and the file-write semantics; splitting creates redundant cross-referencing. (Encoded in the Documentation section.)
