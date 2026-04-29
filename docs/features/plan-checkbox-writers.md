# Plan Checkbox Writers

**Issue:** [#1207](https://github.com/tomcounsell/ai/issues/1207)
**Status:** Shipped
**Replaces:** [Plan Completion Gate] (deleted in this work)
**Related features:** [Self-Healing Merge Gate](self-healing-merge-gate.md) ·
[PM SDLC Decision Rules](pm-sdlc-decision-rules.md)

## Why

The old Plan Completion Gate (in `.claude/commands/do-merge.md`, lines 179–249)
ran a 71-line bash+Python script at merge time that scanned the plan markdown
for any `- [ ]` outside `## Open Questions` and `## Critique Results`. It
produced zero real catches in the audited window — every "failure" was a stale
checkbox on a deliverable that had actually shipped.

To satisfy the gate, agents emitted a docs-only "tick off completed plan items"
commit on the session branch. That tick-off commit landed AFTER the prior
`/do-pr-review` approval, which invalidated the approval under the
review-comment gate's commit-SHA freshness check. The next `/do-pr-review`
re-ran. If clean, the agent dispatched `/do-merge` again; otherwise the cycle
repeated. ~15% of recent SDLC PRs contained a "tick off" commit visible in
`git log --grep="tick off|check off"` — the gate created the very oscillation
it claimed to prevent.

The gate validated a **side artifact** (checkbox state) instead of the
**substance** (does the diff deliver the criterion). Substance validation is
already `/do-pr-review`'s job, and the anti-rubber-stamp rubric (PR #1045)
makes the review skill a stronger plan validator than a checkbox-counter.

## What this work does

The gate is deleted. Plan checkbox writes shift to the two skills that
already produce per-criterion verdicts:

1. **`/do-pr-review`** ticks each criterion it confirms satisfied when emitting
   an Approved verdict, and unticks any criterion it confirms NOT satisfied
   or acknowledged-deferred. Closes the dishonest-tick loophole where a prior
   round's premature `[x]` survives into the next review.
2. **`/do-patch`** ticks any acceptance criterion it addressed when fixing a
   review blocker, in the **same commit** as the fix — no separate "tick off"
   commit.

Both skills share a single helper: `tools/plan_checkbox_writer.py`.

## The helper module

`python -m tools.plan_checkbox_writer <subcommand> <plan_path> ...`

| Subcommand | Effect |
|------------|--------|
| `tick   <plan> --criterion "<text>"`   | Set the matched checkbox to `[x]` |
| `untick <plan> --criterion "<text>"`   | Set the matched checkbox to `[ ]` |
| `status <plan>`                        | Emit JSON: `matched_heading` + `criteria` list (each with `criterion`, `checked`, `line`) |

### Section discovery

The helper accepts BOTH `## Acceptance Criteria` and `## Success Criteria` as
the criteria-section heading. The repo overwhelmingly uses `## Success
Criteria` (138 plans vs 1 plan using Acceptance at plan time), but the
upstream validator at `code-review.md` already names both — the helper stays
symmetric. If both headings appear in the same plan (legal but rare), the
helper exits with `MATCH_AMBIGUOUS_SECTION` and stderr names both line
numbers. If neither appears, exits with `NO_CRITERIA_SECTION`.

### Match contract

- **Whitespace-normalized exact match.** The helper applies
  `re.sub(r'\s+', ' ', text.strip())` to both the criterion line and the
  input `--criterion` value, then compares with case-sensitive equality.
- **No fuzziness.** No word-level fuzziness, no punctuation stripping, no
  substring matching. Plans frequently contain near-duplicates differing
  by punctuation or a single character (e.g., `"Tests pass"` vs
  `"Tests pass."`); fuzzy matching would silently rewrite the wrong line
  and the LLM caller would never know.
- **Multiple matches → error.** Two lines that normalize to the same string
  return `MATCH_AMBIGUOUS` with all matching line numbers in stderr.
- **Idempotent.** Ticking an already-ticked criterion is a no-op exit 0.

### Failure modes

Distinct non-zero exit codes let callers route the right manual-review
comment without re-parsing stderr:

| Tag | When |
|-----|------|
| `MATCH_AMBIGUOUS`         | 2+ criterion lines normalize identically |
| `MATCH_NOT_FOUND`         | Zero criterion lines normalize to the input |
| `MATCH_AMBIGUOUS_SECTION` | Both `## Acceptance Criteria` and `## Success Criteria` headings present |
| `NO_CRITERIA_SECTION`     | Neither heading present (some chore plans legitimately omit) |
| `EMPTY_CRITERION`         | `--criterion` is empty or whitespace |
| `MISSING_FILE`            | Plan path does not exist |
| `MALFORMED_PLAN`          | Plan file unreadable |

## `/do-pr-review` integration

`.claude/skills/do-pr-review/sub-skills/post-review.md` Step 2.5 ("Plan
Checkbox Sync") fires only on `APPROVED` verdicts. The four-value rubric
contract (from Rubric item 1) maps to plan-file writes as:

| Rubric value   | Plan-file action |
|----------------|------------------|
| `pass`         | `tick [x]` (criterion satisfied by diff) |
| `fail`         | `untick [ ]` (closes the dishonest-tick loophole) |
| `acknowledged` | `untick [ ]` (verified deferral exists, but criterion is still unmet) |
| `n/a`          | no plan write (existing checkbox state preserved) |

**Disclosure-vs-pass override.** If a criterion is BOTH covered by a verified
disclosure AND demonstrably satisfied by the diff, the rubric MUST emit
`pass`, not `acknowledged`. The disclosure is informational only — the plan
write reflects the `pass`. This prevents the oscillation pathway where
`/do-patch` ticks a previously-deferred criterion (because the patch
satisfies it) but a later review unticks it again merely because the
disclosure is still in the PR body. The rule is encoded at
`.claude/skills/do-pr-review/sub-skills/code-review.md` Step 4.

**Commit-then-post-review ordering (non-negotiable invariant).** Every git
operation that produces a tick MUST `git push origin HEAD:{branch}` succeed
BEFORE the `gh pr review --approve` / `gh pr comment` call. The
review-comment gate (`.claude/commands/do-merge.md`) filters reviews where
`created_at < LATEST_COMMIT_DATE` — pushing the tick commit FIRST guarantees
the review's `created_at` is strictly newer than every commit on the branch.
On push failure (network, branch protection, conflict), the skill aborts
posting the review and emits `next_skill: /do-patch` — never silently
approves without ticks pushed.

**Helper failure handling.** On `MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND` /
`MATCH_AMBIGUOUS_SECTION`, the existing checkbox state is preserved AND a
manual-review comment is appended to the review body so the human reviewer
can see the uncertainty. On `NO_CRITERIA_SECTION`, the skill logs a warning
but does not block.

## `/do-patch` integration

`.claude/skills/do-patch/SKILL.md` Step 3.5 ("Sync Plan Checkbox") runs
AFTER the test-pass verification in Step 3 but BEFORE Report Completion.

### Builder prompt extension

The Step 2 builder agent reports `criterion_addressed: <text>` (or
`criterion_addressed: null`) in its completion summary. A closed
cosmetic-only exclusion list forces `null` for:

1. lint or formatting-only edits (whitespace, import order, ruff fixes)
2. test-file-only edits where the test exercises pre-existing behavior
3. comment-only or docstring-only edits
4. typo fixes
5. edits touching only `__pycache__/`, `.gitignore`, `.gitkeep`, or
   generated artifacts

When uncertain, the builder is instructed to prefer `criterion_addressed:
null` — the next `/do-pr-review` round will tick it properly if the fix
actually satisfies a criterion (Edit B's tick/untick contract is the
safety net for any over-claim from Edit C).

### Atomic single commit

If `criterion_addressed` is non-null, the patch skill invokes the helper to
flip the matched checkbox, then commits BOTH the code change AND the
plan-file edit in a single `git add -A && git commit && git push`. No
`--amend`, no separate "tick off" commit. The single-commit invariant is
what keeps the merge-gate review-comment freshness check passing on the
next attempt — the latest commit's `committer.date` advances together with
the code change.

### Helper failure is non-fatal

If the helper exits non-zero (`MATCH_AMBIGUOUS` / `MATCH_NOT_FOUND` /
others), the commit STILL happens with the code change only; the failure
is logged but does NOT abort the patch flow. The next `/do-pr-review`
round will reconcile via tick/untick. This matches the test-then-commit
invariant: a failed test in Step 3 aborts the flow before Step 3.5; an
ambiguous criterion in Step 3.5 is non-fatal.

## Flow comparison

**Before this work** (clean PR with the gate):

```
PR Approved → tick-off commit → review staleness → /do-pr-review re-run
  → Approved (again) → /do-merge → completion gate passes → merged.
```

**After this work** (gate deleted):

```
PR Approved (review writes ticks) → /do-merge → no completion gate → merged.
```

**For a PR with patched-out blockers:**

```
Initial review → blockers found → /do-patch fixes (and ticks the relevant
  criterion in the same commit) → re-review → all clear (review writes any
  remaining ticks, unticks any dishonest ticks) → /do-merge → merged.
```

## Crash recovery caveat

A worker crash between the helper's file-write and the subsequent
`git add && git commit` leaves the plan file in a dirty working-tree state.
Mitigation: the next session's `/do-build` lifecycle commit-step or a
manual `git restore docs/plans/{slug}.md` recovers; this matches the
existing crash-recovery story for any other plan-touching skill (e.g.,
`/do-build` writing spike results). No new mitigation needed.

## Reversibility

Restoring the gate is a markdown copy-paste from the deleted block in
`.claude/commands/do-merge.md`. The tick/untick writes in `/do-pr-review`
and `/do-patch` are also pure prompt edits; reversing them is a one-line
revert per skill.
