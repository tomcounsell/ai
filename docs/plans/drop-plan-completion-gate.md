---
status: Planning
type: chore
appetite: Medium
owner: Tom Counsell
created: 2026-04-29
tracking: https://github.com/tomcounsell/ai/issues/1207
last_comment_id:
revision_applied: true
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

### spike-4: Which heading do real plans use for the criteria list?
- **Assumption**: "The repo uses a single canonical heading (`## Acceptance Criteria`) for the criteria list."
- **Method**: code-read (grep over `docs/plans/*.md`)
- **Finding**: **Refuted.** `grep -l "^## Success Criteria" docs/plans/*.md | wc -l` returns 138 plans; `grep -l "^## Acceptance Criteria" docs/plans/*.md | wc -l` returns 1 plan (`docs/plans/sdlc-1065.md`). The plan-under-edit itself uses `## Success Criteria` at line 305. `/do-pr-review`'s step 4b at `code-review.md:209-210` already names BOTH headings as the targets of plan-checkbox validation. If the helper only matches `## Acceptance Criteria`, it will `MATCH_NOT_FOUND` on virtually every real plan and the new tick/untick behavior will never fire.
- **Confidence**: high
- **Impact on plan**: The helper MUST accept BOTH `## Acceptance Criteria` AND `## Success Criteria` as criterion section headings (case-insensitive, whitespace-tolerant). All references to "the section the helper reads" (Edits B, C, D, the test names, the Verification table, Step-by-Step Tasks, No-Gos) are reworded to say "criteria section (Acceptance Criteria or Success Criteria)" rather than "Acceptance Criteria" alone. This keeps the helper symmetric with the validator that produces the verdict (`code-review.md` step 4b).

## Solution

### Key Elements

- **Edit A (deletion)**: Remove the Plan Completion Gate from `.claude/commands/do-merge.md` (lines 179-249, plus the line below at 251). Remove every downstream reference to it (PM persona, merge-troubleshooting playbook, feature docs, tests).
- **Edit B (do-pr-review tick/untick)**: When `/do-pr-review` emits Approved, walk the plan's criteria section (whichever of `## Acceptance Criteria` or `## Success Criteria` is present) and for each criterion, write `[x]` if the rubric/step-4b judgment confirmed it satisfied, write `[ ]` if it confirmed not-satisfied or acknowledged-deferred. Commit and push to the PR branch BEFORE posting the review (see B3 ordering rule in Technical Approach).
- **Edit B-1 (terminology cleanup)**: Rename Pre-Verdict Checklist item 1 from "All plan acceptance criteria checked against diff" to "All plan acceptance/success criteria validated against diff" in `.claude/skills/do-pr-review/sub-skills/code-review.md:257`. The word "checked" was previously colloquial ("examined") but now collides with the new technical meaning ("ticked with `[x]`"); "validated" is unambiguous. Sweep `code-review.md` and `post-review.md` for any echoes of the old string.
- **Edit C (do-patch tick on fix)**: When `/do-patch` fixes a review blocker, the builder identifies which criterion (if any) the fix addresses. The patch skill writes `[x]` to that criterion in the same commit as the fix. The builder is explicitly forbidden from claiming a `criterion_addressed` mapping for cosmetic-only fixes (lint, formatting, comments, typos, `__pycache__` / `.gitignore` / `.gitkeep`).
- **Edit D (helper module)**: New `tools/plan_checkbox_writer.py` — a small Python module + CLI that performs the read-modify-write of plan checkboxes safely. Accepts BOTH `## Acceptance Criteria` and `## Success Criteria` headings. Used by both Edit B and Edit C.
- **Edit E (docs cleanup)**: Delete `docs/features/plan-completion-gate.md`, remove its index entry, migrate the obsolete `docs/plans/plan_completion_gate.md` to `docs/plans/completed/`, prune `docs/sdlc/merge-troubleshooting.md` (delete the "Unchecked Plan Checkboxes" section), update `docs/features/self-healing-merge-gate.md` cross-links.
- **Edit F (PM persona + tests)**: Remove the COMPLETION_GATE row from the PM persona's blocker→remediation table and the `allow_unchecked` prohibition; delete the `test_allow_unchecked_prohibited` test, remove `"COMPLETION_GATE"` from `test_blocker_categories_enumerated`, and update `test_seven_sections_present` to drop `"Unchecked Plan Checkboxes"` from its asserted-headings tuple.

### Flow

**Before this work** (current behavior on a clean PR):
PR Approved → tick-off commit → review staleness → `/do-pr-review` re-run → Approved (again) → `/do-merge` → completion gate passes → merged.

**After this work** (desired behavior):
PR Approved (review writes ticks) → `/do-merge` → no completion gate → merged.

For a PR with patched-out blockers:
Initial review → blockers found → `/do-patch` fixes (and ticks the relevant criterion in the same commit) → re-review → all clear (review writes any remaining ticks, unticks any dishonest ticks) → `/do-merge` → merged.

### Technical Approach

- **Edit A — Pure deletion in `do-merge.md`.** Delete lines 179-251 (the `### Plan Completion Gate` heading, the bash+Python script, and the failure-handling line below). Verify the surrounding gates (review-comment gate at lines 100-178, lockfile gate at line 253+) remain intact and correctly sequenced. The merge gate ladder remaining: review-comment, lockfile, docs (per `docs/sdlc/do-merge.md`), CI checks. No new gate added.
- **Edit B — Tick/untick writes in `/do-pr-review`.** The existing skill flow: Step 4 (Plan Validation) → Step 4b (Plan Checkbox Validation) → Step 5 (Verification Checks) → Pre-Verdict Checklist → Rubric → Verdict derivation → Post the Review (the `gh pr review --approve` / `gh pr comment` call). The tick/untick step is added in `.claude/skills/do-pr-review/sub-skills/post-review.md` BETWEEN Step 2 ("Format Review Body") and Step 3 ("Post the Review"). It only fires for `APPROVED` verdicts.

  **Per-criterion mapping (rubric → plan write):** Rubric item 1 produces a per-criterion verdict drawn from the four-value set `pass | fail | acknowledged | n/a`. The mapping to plan-file writes is:
  - `pass` → `tick [x]` (criterion satisfied by diff)
  - `fail` → `untick [ ]` (criterion not satisfied — closes the dishonest-tick loophole)
  - `acknowledged` → `untick [ ]` (verified deferral disclosure exists, but criterion is still unmet — see Resolved Decision 2)
  - `n/a` → no plan write (existing checkbox state preserved)

  If the rubric is silent for a criterion (no value emitted), the helper is NOT invoked for that criterion. The plan never invents a "confidence" gradient — the four rubric values are the contract. (See C4 in Critique Results.)

  **Special-case override (per C6):** If a criterion is BOTH covered by a verified disclosure AND demonstrably satisfied by the diff, the rubric MUST emit `pass`, not `acknowledged`. The disclosure is informational only. The plan-file write reflects the `pass` (tick `[x]`). The reviewer prompt at `.claude/skills/do-pr-review/sub-skills/code-review.md:205` is updated to make this explicit so a follow-up `/do-patch` round that satisfies a deferred criterion is not unticked again on the next review.

  **Helper invocation and ambiguity handling:** For each criterion the rubric judged, the skill invokes `python -m tools.plan_checkbox_writer tick|untick {plan_path} --criterion "<exact text>"`. On `MATCH_AMBIGUOUS` or `MATCH_NOT_FOUND`, the existing checkbox state is preserved AND a comment line is appended to the review body: `> Could not auto-tick "{criterion text}" — please review manually.` (For `MATCH_NOT_FOUND` when the rubric judged `pass` or `fail`, emit the stronger warning: `> Rubric judged criterion "{text}" {verdict} but no matching item in plan — investigate.`)

  **Commit-then-post-review ordering (BLOCKER B3 mitigation):** This invariant is non-negotiable and must be encoded verbatim in the skill prompt: **EVERY git operation that produces a tick MUST complete (with `git push origin HEAD:{branch}` succeeded) BEFORE the `gh pr review --approve` / `gh pr comment` call.** The exact sequence in `post-review.md` Step 2.5 (new step inserted between Step 2 and Step 3):
  1. Iterate the rubric's per-criterion verdicts; invoke the helper for each `pass` / `fail` / `acknowledged`.
  2. If any helper invocation actually mutated the plan file: `git add docs/plans/{slug}.md && git commit -m "docs(#{N}): sync plan checkboxes with review verdict" && git push origin HEAD:{branch}`. If the push fails (network, branch protection, conflict), the skill MUST abort posting the review, log the failure, and emit a `next_skill: /do-patch` outcome — NEVER silently approve without ticks.
  3. Only after the push succeeds does Step 3 fire (the `gh pr review --approve` / `gh pr comment` call). The review's `created_at` is then strictly after the latest commit's `committer.date`, so do-merge.md's review-comment freshness filter (`.claude/commands/do-merge.md:149-158`) passes on the next merge attempt.

  **Step does NOT fire when** the verdict is any of: `CHANGES_REQUESTED`, `BLOCKED_ON_CONFLICT`, `PR_CLOSED`, or any non-APPROVED state. The `Tier 2 (Tech Debt)` and `BLOCKER` paths produce no commits; this step is silent on those paths. If the rubric produced zero `pass`/`fail`/`acknowledged` values (all `n/a`), the helper is never invoked, no commit is made, and the original ordering is unaffected.

  **Note:** The earlier draft phrase "fold into the review-comment commit" was incorrect — `/do-pr-review`'s post-review step calls the GitHub API (`gh pr review` / `gh pr comment`), which produces no git commit. There is no commit to fold into. The tick commit is its own separate commit, pushed to the PR branch BEFORE the API call. The phrase has been removed.
- **Edit C — Tick on fix in `/do-patch`.** The builder agent (Step 2 prompt) is extended with both an instruction and a closed exclusion list:

  > "If your fix addresses a specific criterion from the plan's criteria section (`## Acceptance Criteria` or `## Success Criteria`), identify which criterion (by exact text). Report this in your completion summary as `criterion_addressed: <text>` (or `criterion_addressed: null` if no clear match).
  >
  > **You MUST report `criterion_addressed: null` when** your fix only changes any of the following (cosmetic-only fixes never tick a criterion):
  > 1. lint or formatting-only edits (whitespace, import order, ruff fixes)
  > 2. test-file-only edits where the test exercises pre-existing behavior
  > 3. comment-only or docstring-only edits
  > 4. typo fixes
  > 5. edits that touch only `__pycache__/`, `.gitignore`, `.gitkeep`, or generated artifacts
  >
  > Edits outside this list MAY tick a criterion if the criterion's text references the runtime behavior the edit changes. When uncertain, prefer `criterion_addressed: null`."

  **Patch flow with new Step 3.5 ("Sync Plan Checkbox"):** The patch skill currently has Steps 1 (Parse Failure), 2 (Builder Agent), 3 (Verify Tests Pass), 4 (Report Completion). A new Step 3.5 is inserted between Step 3 and Step 4 — AFTER the test-pass verification but BEFORE Report Completion. Insertion point: between current `do-patch/SKILL.md:166` (end of Step 3) and the Step 4 heading.

  **Step 3.5 logic (commit-the-fix-with-the-tick):**
  1. Read the builder's reported `criterion_addressed` from Step 2's output.
  2. If `criterion_addressed` is non-null and non-empty: run `python -m tools.plan_checkbox_writer tick {plan_path} --criterion "$VAL"`.
  3. Stage everything: `git add -A`. This is critical — the same `git add -A` captures BOTH the builder's code edits AND the helper's plan-file edit (if any). The plan write and the code fix go into the SAME commit.
  4. Commit: `git commit -m "fix(#{ISSUE_N}): {summary}"` if `criterion_addressed` was null OR the helper exited non-zero, OR `git commit -m "fix(#{ISSUE_N}): {summary} — addresses \"$VAL\""` if the helper succeeded.
  5. Push: `git push origin HEAD:{branch}`.

  **Helper-failure handling:** If the helper exits non-zero (`MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND`), the commit STILL happens (with the code change only), the failure is logged to the patch skill's output, and the patch flow does NOT abort. The next `/do-pr-review` round will untick or re-tick as needed; over-claims are caught by the review's tick/untick contract (Edit B). This matches the test-then-commit invariant: a failed test in Step 3 aborts the flow before Step 3.5; an ambiguous criterion in Step 3.5 is non-fatal.

  **Why same-commit (and not amend, not separate):** A separate "tick off" commit is exactly the symptom this plan deletes — it would invalidate the prior approval (review-comment freshness gate) and force re-review. `git commit --amend` is forbidden in `/do-patch` (every fix is a fresh commit, per do-patch's existing convention at `do-patch/SKILL.md:131` "Do NOT commit — the caller will handle commits"). The clean answer is one new commit per fix that contains both the code change and the criterion tick atomically.

  **Builder authorship invariant:** `do-patch/SKILL.md:131` instructs the builder agent NOT to commit; the patch skill is the commit author. Step 3.5 preserves that — the helper invocation and the commit happen at the patch-skill level, not at the builder-agent level.
- **Edit D — Helper module `tools/plan_checkbox_writer.py`.** Single-purpose CLI:
  ```
  python -m tools.plan_checkbox_writer tick   <plan_path> --criterion "<exact text>"
  python -m tools.plan_checkbox_writer untick <plan_path> --criterion "<exact text>"
  python -m tools.plan_checkbox_writer status <plan_path>  # JSON: list of criteria + current tick state + matched heading
  ```

  **Section discovery (B1 mitigation):** The helper finds the criteria section using the regex `r'^##\s+(Acceptance Criteria|Success Criteria)\s*$'` (case-sensitive on the canonical capitalization, whitespace-tolerant on the surrounding spacing). The section ends at the next `^##\s` heading or end-of-file. If BOTH headings appear in the same plan (rare but legal), the helper exits with `MATCH_AMBIGUOUS_SECTION` and stderr names both line numbers — caller must disambiguate manually. If neither heading appears, exits with `NO_CRITERIA_SECTION` (clearer than `MATCH_NOT_FOUND` for this distinct failure).

  **Match algorithm (C3 mitigation — eliminates silent-corruption risk on near-duplicate criteria):**
  1. Extract every line in the criteria section that matches `^[ \t]*- \[[ x]\] (.+)$`.
  2. Normalize each criterion line's text portion: `re.sub(r'\s+', ' ', text.strip())`. Normalize the input `--criterion` value the same way. (Whitespace-only normalization — collapse runs of spaces, strip leading/trailing whitespace. Case-sensitive. NO word-level fuzziness, NO punctuation-stripping, NO substring matching.)
  3. Compare normalized strings for exact equality.
  4. If exactly one line matches: rewrite that line's checkbox. Exit 0.
  5. If zero lines match: exit 2 with `MATCH_NOT_FOUND` and the input criterion echoed to stderr.
  6. If 2+ lines match: exit 2 with `MATCH_AMBIGUOUS` and ALL matching line numbers echoed to stderr.

  **Why exact-match-after-normalization, not fuzzy:** Plans frequently contain near-duplicates differing by punctuation or a single character (e.g., "Tests pass" vs "Tests pass.", or "`pytest tests/` passes" vs "`pytest tests/unit/` passes"). Word-level fuzziness would silently rewrite the wrong line and the LLM caller would never know (helper exits 0). Exact-match-after-whitespace-normalization is the strictest safe contract: catches innocuous indentation differences without admitting semantic-shifting matches.

  **Other behavior:**
  - Returns exit 0 if the line was found and updated (or already in target state — idempotent).
  - Returns exit 2 with a clear stderr message on any failure mode (`MATCH_AMBIGUOUS`, `MATCH_NOT_FOUND`, `MATCH_AMBIGUOUS_SECTION`, `NO_CRITERIA_SECTION`, `MISSING_FILE`, `EMPTY_CRITERION`, `MALFORMED_PLAN`) — the caller skips the write and emits the appropriate manual-review comment.
  - `status` outputs JSON `{"matched_heading": "Acceptance Criteria"|"Success Criteria", "criteria": [{"criterion": "...", "checked": true|false, "line": N}, ...]}`. The `matched_heading` field surfaces which heading was found so the LLM caller can disambiguate.

  The module is pure Python (no Markdown library), uses regex on the criteria section block. No side effects beyond the file write. Easily unit-testable.
- **Edit B-1 — Pre-Verdict Checklist terminology cleanup.** Rename Pre-Verdict Checklist item 1 to remove the collision between the colloquial "checked" (meaning "examined") and the new technical "checked" (meaning "ticked with `[x]`").
  - In `.claude/skills/do-pr-review/sub-skills/code-review.md:257`: change `- **1. All plan acceptance criteria checked against diff** — PASS/FAIL/N/A — *notes*` to `- **1. All plan acceptance/success criteria validated against diff** — PASS/FAIL/N/A — *notes*`.
  - Sweep `code-review.md` and `post-review.md` for any echoes of the old string (`grep -rn "All plan acceptance criteria checked"`); replace each with the new wording. The sweep is a build-time check, not a separate task.
  - This rename is purely cosmetic but eliminates a real ambiguity introduced by Edit B's tick/untick semantics.

- **Edit E — Docs cleanup.** Mechanical:
  - Delete `docs/features/plan-completion-gate.md`.
  - Remove the row from `docs/features/README.md:89`.
  - `git mv docs/plans/plan_completion_gate.md docs/plans/completed/plan_completion_gate.md` (catching up the spec drift the issue noted).
  - In `docs/sdlc/merge-troubleshooting.md`: delete the "Unchecked Plan Checkboxes" section (lines 50-76) and the COMPLETION_GATE row (line 242).
  - In `docs/features/self-healing-merge-gate.md`: remove the `[Plan Completion Gate]` cross-link (line 5), remove `COMPLETION_GATE` from the blocker-category enumeration (line 103), delete the `allow_unchecked` paragraph (lines 109-111).
- **Edit B-2 — Acknowledged-deferral override (C6 mitigation).** In `.claude/skills/do-pr-review/sub-skills/code-review.md:205`, append to the Acknowledged Deferrals classification rule: "If a criterion is BOTH covered by a verified disclosure AND demonstrably satisfied by the diff, classify as `pass` — the disclosure is informational only. The plan-file write reflects the `pass` (tick `[x]`)." This prevents the oscillation pathway where `/do-patch` ticks a previously-deferred criterion (because the patch satisfies it) but the next `/do-pr-review` round unticks it again merely because the disclosure is still in the PR body. Add a unit test in `test_do_pr_review_tick_writes.py` covering this case.

- **Edit F — PM persona + tests.**
  - In `config/personas/project-manager.md`: delete the COMPLETION_GATE row from the table at line 134 and the standalone `Never set allow_unchecked: true` paragraph at lines 157-160.
  - In `tests/unit/test_pm_persona_guards.py`:
    - `TestGateRecoveryBehavior::test_blocker_categories_enumerated` (line 175): remove `"COMPLETION_GATE"` from the assertion list. Other categories (PIPELINE_STATE, REVIEW_COMMENT, LOCKFILE, FULL_SUITE, MERGE_CONFLICT, PARTIAL_PIPELINE_STATE) remain.
    - `TestGateRecoveryBehavior::test_allow_unchecked_prohibited` (lines 185-189): delete the method entirely.
    - `TestMergeTroubleshootingDoc::test_seven_sections_present` (lines 206-217): remove `"Unchecked Plan Checkboxes"` from the asserted-headings tuple. The remaining six headings (`Merge Conflict`, `G4 Oscillation`, `Stale Review`, `Lockfile Drift`, `Flake False Regression`, `Partial Pipeline State`) stay. Update the method docstring and any "seven sections" comment to reflect the new count of six. (BLOCKER B2 mitigation.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/plan_checkbox_writer.py` — if the plan file is missing, malformed, or unreadable, the CLI must exit non-zero with a clear error message (NOT silently no-op). Tests: `test_plan_checkbox_writer_missing_file`, `test_plan_checkbox_writer_no_criteria_section` (covers both heading variants absent), `test_plan_checkbox_writer_both_headings_present` (returns `MATCH_AMBIGUOUS_SECTION`).
- [ ] `/do-pr-review` tick/untick step — if the helper exits with `MATCH_AMBIGUOUS`, `MATCH_NOT_FOUND`, `MATCH_AMBIGUOUS_SECTION`, or `NO_CRITERIA_SECTION`, the skill must emit the manual-review comment and continue (do NOT fail the review). The skill prompt must explicitly state this fallback for each error code.
- [ ] `/do-pr-review` post-review push step — if `git push origin HEAD:{branch}` fails after the tick commit, the skill must abort posting the review and emit `next_skill: /do-patch`, NEVER silently approve without ticks. (B3 invariant.)
- [ ] `/do-patch` Step 3.5 — if the helper exits non-zero, the patch flow continues with a code-only commit; the helper failure is logged but does NOT abort the patch.
- [ ] No new `except Exception: pass` blocks in any touched file.

### Empty/Invalid Input Handling
- [ ] `tools/plan_checkbox_writer.py tick --criterion ""` — must reject empty criterion text with non-zero exit `EMPTY_CRITERION`.
- [ ] If the plan file's criteria section has zero `- [ ]` / `- [x]` items (empty section), tick/untick is a no-op and exits 0 with a stderr note.
- [ ] If the rubric verdict is `APPROVED` but there are zero criteria in the plan, the skill skips the tick/untick step entirely without error and skips the tick commit.
- [ ] Helper accepts BOTH `## Acceptance Criteria` and `## Success Criteria` headings. Test: `test_plan_checkbox_writer_finds_acceptance_heading`, `test_plan_checkbox_writer_finds_success_heading`. (B1 mitigation.)
- [ ] Helper rejects ambiguous near-duplicate criteria (e.g., "Tests pass" vs "Tests pass.") with `MATCH_AMBIGUOUS` rather than silently picking one. Test: `test_plan_checkbox_writer_near_duplicate_criteria_ambiguous`. (C3 mitigation.)

### Error State Rendering
- [ ] When the helper returns `MATCH_AMBIGUOUS`, the review comment includes the literal text `> Could not auto-tick "{criterion}" — please review manually.` (visible to humans on GitHub, not swallowed).
- [ ] When the helper returns `MATCH_NOT_FOUND` for a criterion the rubric judged satisfied, the skill emits a stronger warning: `> Rubric judged criterion "{text}" {verdict} but no matching item in plan — investigate.`
- [ ] When the helper returns `MATCH_AMBIGUOUS_SECTION` (both headings present), the review comment names both line numbers and asks the human to remove one heading.
- [ ] When the helper returns `NO_CRITERIA_SECTION` for a plan that should have one, the skill logs a warning but does not block — some plans (small-appetite chores) may legitimately omit the section.

## Test Impact

- [ ] `tests/unit/test_pm_persona_guards.py::TestGateRecoveryBehavior::test_blocker_categories_enumerated` — UPDATE: remove `"COMPLETION_GATE"` from the assertion list. Other categories (PIPELINE_STATE, REVIEW_COMMENT, LOCKFILE, FULL_SUITE, MERGE_CONFLICT, PARTIAL_PIPELINE_STATE) remain.
- [ ] `tests/unit/test_pm_persona_guards.py::TestGateRecoveryBehavior::test_allow_unchecked_prohibited` — DELETE: this test enforces that the PM persona explicitly forbids `allow_unchecked: true`. With the flag removed everywhere, the test has nothing to assert.
- [ ] `tests/unit/test_pm_persona_guards.py::TestMergeTroubleshootingDoc::test_seven_sections_present` (lines 206-217) — UPDATE: remove `"Unchecked Plan Checkboxes"` from the asserted-headings tuple. The remaining six headings stay. Update the docstring/comments referring to "seven sections" to say "six". (B2 mitigation — without this the build breaks CI for everyone.)
- [ ] `tests/unit/test_do_merge_baseline.py` — REVIEW: scan for any test asserting the existence of the Plan Completion Gate section in `do-merge.md` or the `allow_unchecked` keyword. If found, DELETE those assertions; if absent, no change. (Initial inspection suggests the file tests other gates — confirm at build time.)
- [ ] `tests/unit/test_do_merge_review_filter.py:18,97` — NO CHANGE: these `GATES_FAILED` references are about the review-comment gate, not the plan-completion gate.
- [ ] `tests/unit/test_validate_merge_guard.py` — REVIEW: `validate_merge_guard.py` is the merge-guard tokeniser referenced in `self-healing-merge-gate.md`. Confirm at build time whether it tokenises COMPLETION_GATE strings; if so, prune that token. Disposition: UPDATE if it does, no change if it doesn't.
- [ ] `tests/unit/test_plan_checkbox_writer.py` — CREATE (new): unit tests for `tools/plan_checkbox_writer.py` covering tick / untick / status / missing-file / malformed / `MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND` / `MATCH_AMBIGUOUS_SECTION` (both headings present) / `NO_CRITERIA_SECTION` / `EMPTY_CRITERION` / no-op-when-already-ticked / dual-heading recognition (`## Acceptance Criteria` and `## Success Criteria`) / whitespace-normalization (leading/trailing whitespace collapsed) / near-duplicate criteria rejected as ambiguous / case-sensitive exact match (NOT case-insensitive — see C3).
- [ ] `tests/unit/test_do_pr_review_tick_writes.py` — CREATE (new): test that when `/do-pr-review` emits APPROVED with a rubric verdict that judged a criterion satisfied, the helper is invoked with `tick` and the plan file ends up with `[x]` for that criterion. Critically, also test the **dishonest-tick unticking case**: a plan starts with `[x]` for an unsatisfied criterion, the rubric judges it FAIL, and the resulting plan-file mutation has `[ ]` for that criterion. (Issue's explicit verification requirement.) Plus: test the **commit-then-post-review ordering invariant** — assert the tick commit is pushed BEFORE the `gh pr review --approve` call. Plus: test the **C6 disclosure-vs-pass override** — criterion has a verified disclosure but the diff satisfies it; rubric returns `pass`, helper writes `[x]`, no untick. Plus: test the `n/a` rubric value produces no plan write.
- [ ] `tests/unit/test_do_patch_ticks.py` — CREATE (new): simulate a patch run where the builder reports `criterion_addressed: "X"` after fixing a blocker. Assert the resulting commit has BOTH the code change AND the plan-file checkbox flip in the same commit (no separate "tick off" commit). Also test the null-criterion path: builder reports `criterion_addressed: null`, no plan write happens, commit contains only the code change. Plus: test the **cosmetic-only exclusion** (C5) — builder fixes a typo, reports `criterion_addressed: null`, no plan write. Plus: test the **helper-failure-non-fatal path** — builder reports a criterion, helper exits with `MATCH_NOT_FOUND`, the patch commit STILL happens with the code change only.
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

### Risk 5: `/do-pr-review` writes an incorrect untick when the criterion was correctly ticked by `/do-patch` mid-cycle
**Impact:** `/do-patch` fixes a blocker, ticks criterion X. Re-review runs, the rubric returns `fail` or `acknowledged` for X (despite the patch satisfying it), unticks X. Now criterion X looks unfinished even though it's done.
**Mitigation:** The four-value rubric contract is explicit (Edit B): `pass` ticks, `fail` unticks, `acknowledged` unticks, `n/a` does nothing. There is no "confidence" state — if the LLM cannot judge a criterion, the rubric forces a value, and `n/a` (no plan write) is the safe default. Plus: the disclosure-vs-pass override (Edit B-2 / C6 mitigation) ensures that a satisfied criterion with a stale disclosure is classified `pass`, not `acknowledged`. Plus: the next round's review will re-tick if the criterion is genuinely satisfied. Bounded oscillation: at worst, one extra tick/untick cycle, capped by `MAX_REVIEW_ROUNDS` in the SDLC pipeline.

## Race Conditions

No concurrency races — all operations are synchronous and single-threaded. The helper module performs read-modify-write on a single file; both `/do-pr-review` and `/do-patch` invoke it serially from the agent's command sequence. The PR branch is a serialized single-author timeline (the agent is the only writer); there is no concurrent commit risk inside a single SDLC pipeline run.

**Crash-recovery caveat (N2):** A worker crash between the helper's file-write and the subsequent `git add && git commit` leaves the plan file in a dirty working-tree state. Mitigation: the next session's `/do-build` lifecycle commit-step or a manual `git restore docs/plans/{slug}.md` recovers; this matches the existing crash-recovery story for any other plan-touching skill (e.g., `/do-build` writing spike results). No new mitigation needed.

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
- [ ] `tools/plan_checkbox_writer.py` exists with `tick`, `untick`, and `status` subcommands, accepts BOTH `## Acceptance Criteria` and `## Success Criteria` headings, and is unit-tested.
- [ ] `tests/unit/test_pm_persona_guards.py` no longer asserts COMPLETION_GATE or `allow_unchecked`, and `test_seven_sections_present` no longer asserts `Unchecked Plan Checkboxes` (one assertion updated for blocker categories, one test deleted, one assertion updated for sections).
- [ ] PM persona at `config/personas/project-manager.md` no longer references COMPLETION_GATE in its blocker→remediation mapping or contains the `allow_unchecked` paragraph.
- [ ] `.claude/skills/do-pr-review/sub-skills/code-review.md` Pre-Verdict Checklist item 1 reads "validated against diff", not "checked against diff" (B-1).
- [ ] `.claude/skills/do-pr-review/sub-skills/post-review.md` has a new Step 2.5 enforcing the commit-then-post-review ordering invariant.
- [ ] `.claude/skills/do-patch/SKILL.md` has a new Step 3.5 placing the criterion-tick in the same commit as the code fix.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` exit 0.
- [ ] `pytest tests/` passes with the updated test files and new test files.

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
- **Informed By**: spike-3, spike-4 (helper API + dual-heading requirement)
- **Assigned To**: helper-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement `tools/plan_checkbox_writer.py` with `tick` / `untick` / `status` subcommands.
- Use regex `r'^##\s+(Acceptance Criteria|Success Criteria)\s*$'` to find the criteria section start; section ends at next `^##\s` heading. Accept BOTH headings (B1 mitigation).
- Match algorithm: extract `- [ ]`/`- [x]` lines, normalize whitespace (`re.sub(r'\s+', ' ', text.strip())`), require exact case-sensitive equality after normalization. Multiple matches → `MATCH_AMBIGUOUS`. Zero matches → `MATCH_NOT_FOUND`. Both headings present in plan → `MATCH_AMBIGUOUS_SECTION`. No heading present → `NO_CRITERIA_SECTION`. (C3 mitigation.)
- Return non-zero on every failure mode: `MATCH_AMBIGUOUS`, `MATCH_NOT_FOUND`, `MATCH_AMBIGUOUS_SECTION`, `NO_CRITERIA_SECTION`, `MISSING_FILE`, `EMPTY_CRITERION`, `MALFORMED_PLAN`. Stderr message names the failure mode and the offending input.
- `status` subcommand outputs `{"matched_heading": "...", "criteria": [{"criterion": "...", "checked": bool, "line": N}, ...]}`.
- Write `tests/unit/test_plan_checkbox_writer.py` covering the failure-path test strategy items above (dual heading, near-duplicate ambiguity, exact-match-after-normalization, etc.).

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
- **Informed By**: spike-1 (per-criterion verdict already happens in step 4b), spike-4 (dual-heading requirement), Critique B3/C2/C4/C6
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Insert a new Step 2.5 ("Plan Checkbox Sync") in `.claude/skills/do-pr-review/sub-skills/post-review.md` BETWEEN Step 2 ("Format Review Body") and Step 3 ("Post the Review"). Fires only on `APPROVED` verdicts.
- For each criterion the rubric judged (`pass` / `fail` / `acknowledged` / `n/a`), invoke `python -m tools.plan_checkbox_writer tick` (for `pass`), `untick` (for `fail` and `acknowledged`), or skip (for `n/a`). The four-value mapping is the contract — no "confidence" gradient. (C4 mitigation.)
- Search the plan for either `## Acceptance Criteria` or `## Success Criteria` — the helper handles both.
- On `MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND` / `MATCH_AMBIGUOUS_SECTION` / `NO_CRITERIA_SECTION`, emit the appropriate manual-review comment in the review body and skip the write for that criterion. Do NOT abort the review.
- **Commit-then-post-review ordering (B3 invariant — non-negotiable):**
  1. After all helper invocations complete, if any plan file was mutated: `git add docs/plans/{slug}.md && git commit -m "docs(#{N}): sync plan checkboxes with review verdict" && git push origin HEAD:{branch}`.
  2. If push fails (network / branch protection / conflict), abort the review post and emit `next_skill: /do-patch`. Do NOT call `gh pr review --approve` without ticks pushed.
  3. Only on push success does Step 3 fire (the `gh pr review --approve` / `gh pr comment` call).
- Edit B-1 (in `code-review.md:257`): rename Pre-Verdict Checklist item 1 to `**1. All plan acceptance/success criteria validated against diff** — PASS/FAIL/N/A — *notes*`. Sweep `code-review.md` and `post-review.md` for `grep -rn "All plan acceptance criteria checked"` and replace each. (C2 mitigation.)
- Edit B-2 (in `code-review.md:205`): append the disclosure-vs-pass override sentence so a satisfied-but-disclosed criterion is classified `pass`, not `acknowledged`. (C6 mitigation.)
- Write `tests/unit/test_do_pr_review_tick_writes.py`: dishonest-tick unticking, `n/a` no-write, commit-then-post-review ordering, disclosure-vs-pass override, `MATCH_AMBIGUOUS_SECTION` graceful skip, push-failure aborts review.

### 4. Edit `/do-patch` (tick on fix)
- **Task ID**: build-edit-patch
- **Depends On**: build-helper
- **Validates**: tests/unit/test_do_patch_ticks.py (create)
- **Informed By**: spike-2 (builder has full plan + review comments), Critique C1/C5
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend the Step 2 builder prompt in `.claude/skills/do-patch/SKILL.md` to instruct the builder to report `criterion_addressed: <text>` or `criterion_addressed: null` after the fix.
- Embed the closed cosmetic-only exclusion list in the builder prompt (C5 mitigation): builder MUST report `criterion_addressed: null` for (1) lint/formatting-only edits, (2) test-file-only edits where the test exercises pre-existing behavior, (3) comment/docstring-only edits, (4) typo fixes, (5) edits touching only `__pycache__` / `.gitignore` / `.gitkeep` / generated artifacts.
- Insert a new Step 3.5 ("Sync Plan Checkbox") in `.claude/skills/do-patch/SKILL.md` BETWEEN Step 3 (test-pass verification) and Step 4 (Report Completion). Insertion point: between current `do-patch/SKILL.md:166` and the Step 4 heading. (C1 mitigation — explicit insertion point.)
- Step 3.5 logic: read builder's `criterion_addressed`; if non-null and non-empty, run `python -m tools.plan_checkbox_writer tick {plan_path} --criterion "$VAL"`. Then `git add -A && git commit -m "fix(#{N}): {summary}{ — addresses \"$VAL\"}" && git push origin HEAD:{branch}`. The plan file edit is captured by the same `git add -A` as the code change → atomic single commit. (No `--amend`, no separate "tick off" commit.)
- Helper failure (`MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND` / others): commit STILL happens with the code change only; failure logged but non-fatal. The next `/do-pr-review` round will reconcile via tick/untick.
- Test ordering: the test-pass check in Step 3 happens BEFORE the commit in Step 3.5, so a failing fix never produces a commit.
- Write `tests/unit/test_do_patch_ticks.py`: single-commit invariant (code + plan in one commit), null-criterion path (no plan write), cosmetic-only exclusion (typo fix → null → no plan write), helper-failure-non-fatal path.

### 5. PM persona + tests cleanup
- **Task ID**: build-edit-pm-tests
- **Depends On**: build-docs-cleanup (because `test_seven_sections_present` must be updated AFTER `merge-troubleshooting.md` is edited; otherwise the test temporarily fails on a half-state)
- **Validates**: `pytest tests/unit/test_pm_persona_guards.py` passes after edits; grep returns zero matches in `config/personas/project-manager.md` for "COMPLETION_GATE" and "allow_unchecked"
- **Informed By**: freshness check (locations enumerated), Critique B2
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove the COMPLETION_GATE row from the table at `config/personas/project-manager.md:134`.
- Remove the `Never set allow_unchecked: true` paragraph at lines 157-160.
- In `tests/unit/test_pm_persona_guards.py`:
  - `TestGateRecoveryBehavior::test_blocker_categories_enumerated` (line 175): remove `"COMPLETION_GATE"` from the assertion list.
  - `TestGateRecoveryBehavior::test_allow_unchecked_prohibited` (lines 185-189): delete the method entirely.
  - `TestMergeTroubleshootingDoc::test_seven_sections_present` (lines 206-217): remove `"Unchecked Plan Checkboxes"` from the asserted-headings tuple. The remaining six headings stay. Update the docstring/comments referring to "seven sections" to say "six". (B2 mitigation.)

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
| Helper module exists (Acceptance heading) | `python -m tools.plan_checkbox_writer status docs/plans/sdlc-1065.md` | exit code 0 (plan uses `## Acceptance Criteria`) |
| Helper module exists (Success heading) | `python -m tools.plan_checkbox_writer status docs/plans/drop-plan-completion-gate.md` | exit code 0 (plan uses `## Success Criteria`) |
| Pre-Verdict Checklist renamed | `grep -c "All plan acceptance criteria checked" .claude/skills/do-pr-review/sub-skills/code-review.md .claude/skills/do-pr-review/sub-skills/post-review.md` | output 0 (B-1 sweep) |
| Seven-section test updated | `grep -c "Unchecked Plan Checkboxes" tests/unit/test_pm_persona_guards.py` | output 0 (B2 mitigation) |
| Helper tests exist | `test -f tests/unit/test_plan_checkbox_writer.py` | exit code 0 |
| Review tick/untick test exists | `test -f tests/unit/test_do_pr_review_tick_writes.py` | exit code 0 |
| Patch tick test exists | `test -f tests/unit/test_do_patch_ticks.py` | exit code 0 |

## Critique Results

**Plan**: docs/plans/drop-plan-completion-gate.md
**Plan commit**: d74db08f
**Artifact hash (sha256 of plan)**: b8815ef044c7a5e52c5c405e90452cc40678c4546228eb5bc758b388fd8f2433
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User, Consistency Auditor + structural checks
**Findings**: 11 total (3 blockers, 6 concerns, 2 nits)

### Blockers

#### B1. Helper module targets `## Acceptance Criteria` but the repo overwhelmingly uses `## Success Criteria` (138 plans vs 1)
- **Severity**: BLOCKER
- **Critics**: Consistency Auditor, Skeptic, Operator
- **Location**: Solution → Edits B/C/D (lines 147, 167, 173, 184, 188), Spike-3 (line 136), Failure Path Test Strategy (lines 202, 207-208), No-Gos (line 267), Step-by-Step Tasks (lines 358, 382), Verification (line 450)
- **Finding**: The plan exclusively names `## Acceptance Criteria` as the section the helper module reads/writes, yet `grep -l "^## Success Criteria" docs/plans/*.md | wc -l` returns 138 plans using "Success Criteria" against only 1 plan using "Acceptance Criteria" (`docs/plans/sdlc-1065.md`). The plan-under-edit itself uses `## Success Criteria` at line 305. The helper would `MATCH_NOT_FOUND` on virtually every real plan, silently skipping every tick/untick and emitting a manual-review comment for every criterion — net effect: the new behavior never actually fires.
- **Suggestion**: Specify that the helper accepts BOTH `## Acceptance Criteria` and `## Success Criteria` headings (case-insensitive, whitespace-tolerant). Update Edits B, C, D, the spike-3 finding, the Step-by-Step Tasks, the Verification table, and the test names to reflect "criteria" sections by either heading. Add an explicit unit test that asserts the helper finds criteria under either heading. Note: `code-review.md:209-210` already names BOTH headings as the targets of Step 4b, so the new helper must do the same to stay symmetric with the validator that produced the verdict.
- **Implementation Note**: Use a regex like `r'^##\s+(Acceptance Criteria|Success Criteria)\s*$'` to anchor the section start; section ends at the next `^## ` heading. The helper's `status` subcommand should also surface which heading it matched so the LLM caller can disambiguate when both happen to be present (rare but legal — fail loudly with `MATCH_AMBIGUOUS` and a stderr message naming both sections).

#### B2. Plan deletes `## Unchecked Plan Checkboxes` from `merge-troubleshooting.md` but does NOT update the test that asserts that heading exists
- **Severity**: BLOCKER
- **Critics**: Adversary, Operator, Skeptic
- **Location**: Edit E (line 193), Documentation section (line 294), Test Impact section (lines 217-225)
- **Finding**: `tests/unit/test_pm_persona_guards.py:206-217` (`TestMergeTroubleshootingDoc::test_seven_sections_present`) asserts that exactly seven `## ` headings — including `Unchecked Plan Checkboxes` — exist in `docs/sdlc/merge-troubleshooting.md`. The plan removes that section in Edit E but the Test Impact section enumerates only `test_blocker_categories_enumerated` and `test_allow_unchecked_prohibited` from that file. `test_seven_sections_present` will fail at build time, breaking CI for everyone, and the plan's structural test-coverage claim is incomplete.
- **Suggestion**: Add an explicit Test Impact entry: `tests/unit/test_pm_persona_guards.py::TestMergeTroubleshootingDoc::test_seven_sections_present` — UPDATE: remove `"Unchecked Plan Checkboxes"` from the asserted-headings tuple, leaving the other six. Verify the assertion list count adjusts to six. This is a separate test class (`TestMergeTroubleshootingDoc`) from the gate-recovery class — easy to miss in a textual scan. Test Impact must enumerate ALL three classes that read this file: `TestGateRecoveryBehavior`, `TestMergeTroubleshootingDoc`, and any other test that reads `docs/sdlc/merge-troubleshooting.md`.
- **Implementation Note**: The exact code to change is the tuple at `tests/unit/test_pm_persona_guards.py:208-217` — drop the `"Unchecked Plan Checkboxes",` line. Do NOT delete the whole `test_seven_sections_present` method; the other six headings (`Merge Conflict`, `G4 Oscillation`, `Stale Review`, `Lockfile Drift`, `Flake False Regression`, `Partial Pipeline State`) remain valid. The test docstring "seven sections" comment becomes wrong — change to "six sections" or just delete the count.

#### B3. Tick-commit ordering is unspecified and "fold into the review-comment commit" is incoherent — risks re-introducing the very oscillation this plan claims to remove
- **Severity**: BLOCKER
- **Critics**: Adversary, Operator, Archaeologist
- **Location**: Edit B technical approach (lines 167-172, especially line 172: "If there is already a review comment commit in this round, fold the plan write into that commit (no separate commit).")
- **Finding**: (1) Edit B never specifies whether the tick commit must be pushed BEFORE the `gh pr review --approve` / `gh pr comment` call. The do-merge.md review-comment gate filters reviews where `created_at < LATEST_COMMIT_DATE` (`.claude/commands/do-merge.md:149-158`) — if the review is posted first, then tick commit pushed, the review immediately becomes stale, and the next `/do-merge` invocation sees no valid Approved review, forcing re-review. This is exactly the "tick-off oscillation" symptom the plan claims to delete. (2) The phrase "fold into the review-comment commit if one exists" is semantically confused: `/do-pr-review`'s post-review step (sub-skill `post-review.md`) does not produce a git commit — it calls `gh pr review` / `gh pr comment` (a GitHub API operation, not a git operation). There is no "review-comment commit" to fold into. The current Tier 2 ("Changes Requested — Tech Debt") path also produces no commit; commits come only from `/do-patch`. (3) For the BLOCKER-found path (`Tier 1`), the tick step never fires anyway because the plan only fires on APPROVED — but the plan should say so explicitly under "when does the tick step NOT fire."
- **Suggestion**: Rewrite Edit B's commit-ordering paragraph to: (a) For APPROVED verdicts on a non-self-authored PR, the order is: write tick edits to the plan file → `git add docs/plans/{slug}.md && git commit -m "docs(#{N}): sync plan checkboxes with review verdict" && git push origin {branch}` → THEN call `gh pr review --approve --body "$REVIEW_BODY"`. The review's `created_at` is then strictly after the latest commit's `committer.date`, so the review-comment gate's freshness filter passes. (b) For self-authored PRs (which use `gh pr comment` rather than `gh pr review`), the same order applies and the same invariant holds. (c) Delete the "fold into the review-comment commit" sentence — it is incoherent. (d) Add an explicit "this step does NOT fire when verdict is `CHANGES_REQUESTED`, `BLOCKED_ON_CONFLICT`, or `PR_CLOSED`" line for clarity.
- **Implementation Note**: The invariant to enforce in the skill prompt: "EVERY git operation that produces a tick MUST complete (with `git push origin HEAD:{branch}` succeeded) BEFORE the `gh pr review --approve` / `gh pr comment` call." A natural place to anchor this is `post-review.md` Step 3 (line 217-238) — the tick logic should run between Step 2 ("Format Review Body") and Step 3 ("Post the Review"). If the push fails (network, branch protection), the skill must abort posting the review and emit a `next_skill: /do-patch` outcome, not silently approve without ticks.

### Concerns

#### C1. Edit C's same-commit-as-fix invariant conflicts with /do-patch's actual commit flow
- **Severity**: CONCERN
- **Critics**: Adversary, Skeptic
- **Location**: Edit C (lines 173-176), Step-by-Step Tasks step 4 (lines 393-398)
- **Finding**: The plan's Edit C requires the plan-file edit to be in the SAME commit as the code change (line 174: "The plan-file edit is included in the same `git add -A && git commit` as the code fix"). However, `do-patch/SKILL.md:131` instructs the builder agent: "Do NOT commit — the caller will handle commits." That means the patch skill (not the builder) is the commit author. There is no current concrete commit step in `do-patch/SKILL.md` Step 4 ("Report Completion") — Step 4 just reports success and does not commit. To honor "same commit" the patch skill must (a) introduce or expose the commit step explicitly, (b) stage `tools/plan_checkbox_writer.py`'s output alongside the builder's edits, and (c) commit them atomically. The plan does not specify where this commit happens or who owns it.
- **Suggestion**: Add a new Step 3.5 in `do-patch/SKILL.md` that runs AFTER the test-pass verification but BEFORE Step 4 ("Report Completion"): (1) read builder's reported `criterion_addressed`, (2) if non-null and non-empty, run `python -m tools.plan_checkbox_writer tick {plan_path} --criterion "$VAL"`, (3) `git add -A && git commit -m "fix(#{N}): {one-line summary}{ — addresses \"$VAL\"}" && git push`. If the helper exits non-zero (`MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND`), the commit still happens (with the code change only) and the failure is logged but does not abort the patch flow. Be explicit: the test-run in Step 3 happens BEFORE the commit so a failed fix never produces a commit; the commit only happens once tests pass.
- **Implementation Note**: The exact location to add Step 3.5 is between current `do-patch/SKILL.md:166` (end of Step 3) and `do-patch/SKILL.md:167` (Step 4). The commit message format should be `fix(#{ISSUE_N}): {summary}` plus an optional ` — addresses "$CRITERION"` suffix when the helper succeeded. Use `--allow-empty` only if the builder reported no edits (rare; should not happen because Step 3 would have caught no-op fixes). Do NOT use `git commit --amend` — every fix is a fresh commit.

#### C2. Pre-Verdict Checklist item 1 wording becomes ambiguous after Edit B
- **Severity**: CONCERN
- **Critics**: Consistency Auditor, User
- **Location**: Pre-Verdict Checklist item 1 (in `code-review.md:257`, not in the plan body but referenced by Edit B at line 167)
- **Finding**: The Pre-Verdict Checklist item 1 reads "**1. All plan acceptance criteria checked against diff** — PASS/FAIL/N/A — *notes*". The word "checked" was previously colloquial ("examined"). After Edit B, the same word collides with the new technical meaning ("ticked with `[x]`"). A reviewer could read this either way and there is no specification in the plan for which meaning is now intended. Worse: if a reviewer reads "checked" as "ticked", item 1 becomes circular — the rubric verdict drives the tick, and item 1 then asks whether the tick happened, which is a side-effect of item 1 itself.
- **Suggestion**: In Edit B, add a sub-bullet: "Edit B-1 (terminology cleanup): rename Pre-Verdict Checklist item 1 from `All plan acceptance criteria checked against diff` to `All plan acceptance/success criteria validated against diff`." The word "validated" carries the original colloquial meaning unambiguously and avoids collision with the new tick semantics. Update `code-review.md:257` and `post-review.md:42-43` accordingly.
- **Implementation Note**: The exact strings to replace are in `.claude/skills/do-pr-review/sub-skills/code-review.md:257` and any echo of the same string in `post-review.md`. Use a `grep -rn "All plan acceptance criteria checked"` sweep before committing to catch any duplicates.

#### C3. Edit B's "fuzzy whitespace tolerance" + "case-insensitive match" creates a silent-corruption risk on near-duplicate criteria
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Edit D (line 184: "case-insensitive match on the text portion, fuzzy whitespace tolerance"), Failure Path Test Strategy
- **Finding**: If a plan contains two near-duplicate criteria (e.g., "Tests pass" and "Tests pass." — one with a trailing period), the helper's case-insensitive + fuzzy-whitespace match could match the wrong line on rewrite. Plans frequently have similar-but-not-identical criteria (e.g., "`pytest tests/` passes" and "`pytest tests/unit/` passes"). A wrong rewrite would silently tick the wrong criterion and is invisible to the LLM caller (helper exits 0).
- **Suggestion**: Tighten the match contract: helper requires EXACT text match by default; "fuzzy whitespace" tolerance is limited to leading/trailing whitespace and run-of-spaces collapse, never word-level fuzziness. If multiple lines case-insensitively match the same criterion text, return `MATCH_AMBIGUOUS` with both line numbers in the stderr message. Add a unit test for this exact case (two criteria differing only by trailing punctuation or a single character).
- **Implementation Note**: Match algorithm: (1) Normalize the section block: `re.sub(r'\s+', ' ', line.strip())`. (2) Normalize the input criterion the same way. (3) If exactly one line matches, rewrite it. (4) If zero match, exit `MATCH_NOT_FOUND`. (5) If 2+ match, exit `MATCH_AMBIGUOUS` with line numbers.

#### C4. Risk 5 mitigation handwaves "confident-fail" without specifying how confidence is computed
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Risks → Risk 5 (lines 253-255)
- **Finding**: Risk 5 says "the unticking only fires when the rubric judgment is **confident-fail or confident-acknowledged** — not when uncertain." But the rubric values are `pass`/`fail`/`acknowledged`/`n/a` — there is no "confidence" gradient. Either the criterion is `fail` (untick) or it isn't. The plan invents a confidence threshold the rubric doesn't expose, leaving the actual mitigation undefined.
- **Suggestion**: Either (a) drop the word "confident" — if the rubric says `fail`, the helper unticks; if the rubric is silent or n/a, the existing state is preserved. Or (b) add a fifth rubric value like `uncertain` that explicitly maps to "no plan write" — but this is an over-engineering risk. Pick (a). Update Risk 5's mitigation to read: "The unticking fires when the rubric value for that criterion is `fail` or `acknowledged`. Rubric values of `pass` or `n/a` produce no plan write. The rubric does not expose 'uncertainty' as a distinct state — if the LLM cannot judge, the rubric forces a value (typically `n/a`), and `n/a` means no plan write."
- **Implementation Note**: The mapping is: `pass` → `tick [x]`; `fail` → `untick [ ]`; `acknowledged` → `untick [ ]` (per Resolved Decision 2); `n/a` → no plan write. This four-value mapping is the contract — codify it in the skill prompt and in Edit B's bullet list.

#### C5. Edit C lets the builder over-fit by reporting `criterion_addressed` even on cosmetic fixes
- **Severity**: CONCERN
- **Critics**: User, Skeptic
- **Location**: Edit C (lines 173-176), Risk 2 (lines 241-243)
- **Finding**: A builder that fixes a typo, a lint warning, or a `__pycache__` cleanup blocker has no business ticking an acceptance criterion. Risk 2's mitigation depends on the next `/do-pr-review` round catching the over-claim by unticking — but that round only re-runs because the patch creates a new commit, and the plan's whole point is to reduce review oscillation. The over-claim → tick → re-review-untick cycle is one full review round of waste per over-claim.
- **Suggestion**: Strengthen the builder prompt to require that `criterion_addressed` is non-null ONLY when the fix changes runtime behavior referenced by the criterion. Cosmetic changes (typo, formatting, comment) → `criterion_addressed: null`. Add an explicit instruction: "If your fix only changes whitespace, comments, formatting, or test-only lines, `criterion_addressed: null`." Codify this in the Step 2 builder prompt extension as a closed list of exclusions.
- **Implementation Note**: The exclusion list to embed in the prompt: "(1) lint/formatting-only edits, (2) test-file-only edits where the test is checking pre-existing behavior, (3) comment-only edits, (4) typo fixes, (5) edits that touch only `__pycache__` / `.gitignore` / `.gitkeep`." Anything else may legitimately tick a criterion if the criterion text references that behavior.

#### C6. Acknowledged-deferred unticking creates a new oscillation pathway
- **Severity**: CONCERN
- **Critics**: Adversary, Archaeologist
- **Location**: Edit B's third bullet (line 170), Resolved Decision 2 (line 467)
- **Finding**: Per Resolved Decision 2, an acknowledged-deferred criterion gets `[ ]` after Approval. But on the NEXT review round, the same rubric value (`acknowledged`) produces the same untick — which is a no-op (it's already unticked). However, if `/do-patch` later ticks that criterion (because a follow-up fix actually resolved the deferred work), the next `/do-pr-review` round must NOT untick it again merely because the disclosure is still in the PR body. The plan doesn't specify how the rubric distinguishes "still-deferred" from "now-resolved" once the patch happens.
- **Suggestion**: Add to Edit B: "If a criterion has a verified disclosure AND the diff now appears to satisfy it, the rubric value is `pass`, not `acknowledged` — the disclosure becomes stale. The reviewer prompt must be explicit that satisfied criteria override their own disclosures." Add a unit test: criterion with disclosure + diff now satisfies it → rubric returns `pass`, helper writes `[x]`, no untick.
- **Implementation Note**: Update `code-review.md:205` (the Acknowledged Deferrals classification rule) to add: "If a criterion is BOTH covered by a verified disclosure AND demonstrably satisfied by the diff, classify as `pass` — the disclosure is informational only. The plan-file write reflects the `pass` (tick `[x]`)."

### Nits

#### N1. Critique Results section header existed but with no findings — operator confusion risk
- **Severity**: NIT
- **Critics**: Operator
- **Location**: Critique Results section (lines 455-457 before this rewrite)
- **Finding**: The plan ships with a `## Critique Results` section that contains only an HTML comment placeholder. This is the second critique pass; the first one had verdict NEEDS REVISION but the findings were not persisted. A future operator inspecting just the plan file (not the SDLC verdict store) cannot tell that prior critique happened, what it found, and whether revisions occurred.
- **Suggestion**: Whenever a re-critique runs, append a dated entry rather than overwriting silently. (This critique pass has done so — the timestamp and commit are in the header above.)

#### N2. "No race conditions identified" oversells the analysis
- **Severity**: NIT
- **Critics**: Adversary
- **Location**: Race Conditions section (line 259)
- **Finding**: The race-condition analysis claims "all operations are synchronous and single-threaded" — but `/do-pr-review` and `/do-patch` are skill flows that execute under an Agent that can be steered, killed, or restarted mid-flow. A worker crash between "write `[x]` to plan file" and "git commit" leaves a dirty working tree the next session will inherit. This is not a race in the traditional sense, but the section's blanket dismissal is too strong.
- **Suggestion**: Reword: "No concurrency races; however, mid-flow worker crashes between the helper's file-write and the subsequent git commit can leave a dirty working tree. Mitigation: the next session's `/do-build` lifecycle commit-step or a manual `git restore docs/plans/{slug}.md` recovers; this matches the existing crash-recovery story for any other plan-touching skill."

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | All four required sections present and non-empty (Documentation, Update System, Agent Integration, Test Impact). |
| Task numbering | PASS | Tasks 1-7 numbered consecutively, no gaps. |
| Dependencies valid | PASS | All `Depends On` references resolve to a valid task ID or `none`. |
| File paths exist | PARTIAL | All currently-referenced files exist; `tools/plan_checkbox_writer.py`, `tests/unit/test_plan_checkbox_writer.py`, `tests/unit/test_do_pr_review_tick_writes.py`, `tests/unit/test_do_patch_ticks.py`, `docs/features/plan-checkbox-writers.md`, `docs/plans/completed/plan_completion_gate.md` are NEW (intentional creates). |
| Prerequisites met | PASS | Plan declares no prerequisites; consistent with the chore type. |
| Cross-references | PARTIAL | Solution → Edits B/C/D say `## Acceptance Criteria`; Success Criteria heading at line 305 says `## Success Criteria`; the inconsistency is the substance of B1. |

### Verdict

**NEEDS REVISION** — 3 blockers must be resolved before build:

- **B1**: Helper must accept both `## Acceptance Criteria` AND `## Success Criteria` headings, or no real plan ever gets a tick.
- **B2**: Test Impact must enumerate `test_seven_sections_present` so the build doesn't break CI.
- **B3**: Edit B must specify the tick-commit-then-post-review ordering and delete the incoherent "fold into the review-comment commit" sentence.

The 6 concerns and 2 nits are recorded for the revision pass. Once B1-B3 are addressed in the plan body, the next `/do-plan-critique` run is expected to return `READY TO BUILD (with concerns)` if the concerns remain unaddressed, or `READY TO BUILD (no concerns)` if the concerns are also folded into the plan during the revision pass.

---

*Critique recorded 2026-04-29 against plan commit `d74db08f`, plan sha256 `b8815ef044c7a5e52c5c405e90452cc40678c4546228eb5bc758b388fd8f2433`. Prior critique on this plan (verdict NEEDS REVISION) failed to persist findings; this pass replaces it.*

---

### Revision Notes (applied 2026-04-29)

This revision pass addresses ALL three blockers and ALL six concerns from the critique above. Frontmatter `revision_applied: true` set so the SDLC router (Row 4c) routes the next dispatch to `/do-build`.

**Blockers resolved:**
- **B1 (dual heading):** spike-4 added documenting the 138-vs-1 heading mismatch. Edits B, C, D, Step-by-Step Tasks, Verification table, and Test Impact updated to require the helper accept both `## Acceptance Criteria` and `## Success Criteria`. Helper returns `MATCH_AMBIGUOUS_SECTION` if both appear and `NO_CRITERIA_SECTION` if neither.
- **B2 (test_seven_sections_present):** Test Impact and Edit F (Step 5) now enumerate the `TestMergeTroubleshootingDoc::test_seven_sections_present` update — drop `"Unchecked Plan Checkboxes"` from the asserted-headings tuple, leaving six headings.
- **B3 (commit-then-post-review ordering):** Edit B's technical approach rewritten with an explicit non-negotiable invariant: every tick commit MUST `git push origin HEAD:{branch}` succeed BEFORE the `gh pr review --approve` / `gh pr comment` call. The earlier "fold into the review-comment commit" sentence has been removed. Push failure aborts the review post and emits `next_skill: /do-patch`.

**Concerns resolved:**
- **C1 (do-patch commit flow):** Edit C and Step 4 (Step-by-Step Tasks) now specify the exact insertion point for the new Step 3.5 in `do-patch/SKILL.md` (between Step 3 and Step 4) and the precise commit sequence (`git add -A && git commit && git push`). Builder authorship invariant preserved (helper invocation and commit happen at the patch-skill level).
- **C2 (Pre-Verdict Checklist terminology):** New Edit B-1 added — rename `code-review.md:257` from "checked" to "validated". Sweep `code-review.md` and `post-review.md` for echoes.
- **C3 (helper match contract):** Edit D's match algorithm tightened to exact case-sensitive equality after whitespace normalization. Near-duplicate criteria differing by punctuation or case now return `MATCH_AMBIGUOUS` (not silent corruption). Test added: `test_plan_checkbox_writer_near_duplicate_criteria_ambiguous`.
- **C4 (Risk 5 confidence wording):** Risk 5 mitigation rewritten to use the four-value rubric contract (`pass | fail | acknowledged | n/a`) explicitly. The word "confidence" is removed; `n/a` is the silent-default that produces no plan write.
- **C5 (cosmetic-only over-claims):** Edit C and Step 4 now embed a closed exclusion list in the builder prompt (lint, test-only, comments, typos, generated artifacts). Test added: `test_do_patch_ticks` covers the typo-fix → null path.
- **C6 (acknowledged-deferred oscillation):** New Edit B-2 added — append the disclosure-vs-pass override to `code-review.md:205` so a satisfied-but-disclosed criterion classifies as `pass`, not `acknowledged`. Test added: `test_do_pr_review_tick_writes` covers the override.

**Nits resolved:**
- **N1:** Revision Notes (this block) preserves the audit trail. Future re-critiques are instructed to append dated entries.
- **N2:** Race Conditions section now includes a crash-recovery caveat covering the dirty-working-tree window between helper write and git commit.

**Net structural impact on the plan:** New spike-4 added; Edits B-1 and B-2 introduced; Step 5's task dependencies updated (now depends on `build-docs-cleanup` to avoid breaking `test_seven_sections_present` mid-build); Test Impact gained 4 new entries; Verification table gained 4 new rows; Failure Path Test Strategy expanded to cover the new error codes.

The next `/do-plan-critique` run is expected to return `READY TO BUILD` (no concerns or with-concerns disposition acceptable).

---

## Resolved Decisions

These were the four open questions raised at plan time. All were resolved by the human reviewer (Tom Counsell) on 2026-04-29 prior to dispatching critique. The plan body above already encodes these decisions; this section preserves the rationale for auditability.

1. **Criterion-mapping approach — LLM-judge: APPROVED.** Use the existing per-criterion verdict from `/do-pr-review`'s rubric (Step 4b + Rubric item 1). For `/do-patch`, extend the builder agent's report to include `criterion_addressed: <text>`. When the LLM is uncertain, leave the existing checkbox state alone and emit a manual-review comment. Lexical match and explicit cross-reference protocols are explicitly **rejected** as fragile and over-engineered respectively. (Encoded in the Solution → Technical Approach section, Edits B and C.)

2. **Acknowledged-deferred = unticked: APPROVED.** The plan-doc tick answers "is this criterion satisfied?" — a verified disclosure means the deferral was legitimate, but the criterion is still unmet. This is exactly the failure mode the issue is closing (dishonest ticks for mocked APIs / deferred tests). Acknowledged-deferred criteria get unticked. (Encoded in Edit B's third bullet under "For each criterion in `## Acceptance Criteria`".)

3. **Helper location — `tools/plan_checkbox_writer.py`: APPROVED.** Repo convention per `CLAUDE.md` ("`tools/` — Local Python tools" for shared library code imported by skills). NOT `scripts/`. (Encoded in Edit D and the Step-by-Step Tasks; the alternative location was never written into the plan body.)

4. **Documentation — single combined doc at `docs/features/plan-checkbox-writers.md`: APPROVED.** Both behaviors share the helper module, the LLM-judge mapping rationale, and the file-write semantics; splitting creates redundant cross-referencing. (Encoded in the Documentation section.)
