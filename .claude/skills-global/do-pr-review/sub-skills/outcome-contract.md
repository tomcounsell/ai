# Sub-Skill: Outcome Contract

Exact OUTCOME blocks for every verdict variant. Loaded when emitting the final
outcome. After posting the review, verifying it was posted, and recording the
verdict if a substrate is declared, emit a typed outcome as the **very last
line** of output. If the context file declares a verdict substrate, the verdict
record must already be written before you emit this block — the OUTCOME block
is the last line, not the last action.

## Verdict taxonomy

| Verdict | When | OUTCOME status |
|---------|------|----------------|
| `APPROVED` | Preflight clean + zero findings + pre-verdict checklist all PASS/N/A | `success` |
| `CHANGES_REQUESTED` | Preflight clean but findings (blockers, tech_debt, or nits) exist | `partial` (tech_debt/nits only) or `fail` (blockers) |
| `BLOCKED_ON_CONFLICT` | Preflight detected `mergeable=CONFLICTING` or `mergeStateStatus=DIRTY` — short-circuited, no code review performed | `fail` |
| `PR_CLOSED` | Preflight detected `state != OPEN` — short-circuited, no code review performed | `fail` |

## Single-reviewer variants (the generic default)

**Success (APPROVED — no blockers, no tech_debt, no nits):**
```
<!-- OUTCOME {"status":"success","stage":"REVIEW","verdict":"APPROVED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":0,"nits":0},"notes":"Approved with no findings.","next_skill":"/do-docs"} -->
```

**Partial (CHANGES_REQUESTED — no blockers, but has tech_debt and/or nits that need patching):**
```
<!-- OUTCOME {"status":"partial","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":2,"nits":1},"notes":"Changes requested: 2 tech_debt and 1 nit findings. Routing to /do-patch.","next_skill":"/do-patch"} -->
```

**Fail (CHANGES_REQUESTED — blockers found):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":2,"tech_debt":1,"nits":0},"notes":"Changes requested: 2 blockers found.","failure_reason":"2 blockers must be fixed before merge","next_skill":"/do-patch"} -->
```

**Fail (BLOCKED_ON_CONFLICT — preflight short-circuit):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"BLOCKED_ON_CONFLICT","artifacts":{"review_url":"{comment_url}","mergeStateStatus":"DIRTY","mergeable":"CONFLICTING"},"notes":"Branch has merge conflicts; rebase required before review.","failure_reason":"mergeStateStatus=DIRTY — author must rebase/resolve conflicts before review can proceed","next_skill":null} -->
```

**Fail (PR_CLOSED — preflight short-circuit):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"PR_CLOSED","artifacts":{"review_url":"{comment_url}","state":"CLOSED"},"notes":"PR is not open; review skipped.","failure_reason":"state=CLOSED — no review performed on a closed PR","next_skill":null} -->
```

**Important**: The outcome block uses HTML comment syntax (`<!-- ... -->`) so it's invisible in rendered markdown but parseable by the pipeline. Always emit it as the very last line of output. Use `"partial"` — not `"success"` — whenever tech_debt or non-subjective nit findings exist. This ensures the pipeline routes to `/do-patch` before advancing to `/do-docs`. For `BLOCKED_ON_CONFLICT` and `PR_CLOSED`, `next_skill` is `null` — the pipeline should NOT auto-advance; the author must rebase or the PM must handle the closed-PR case manually.

## Multi-judge OUTCOME variants (only when a consensus model is active)

When the multi-judge path runs (≥2 judges dispatched), include `judges_run` and
`consensus_disagreement` inside `artifacts` so operators can grep session state
for disagreement events. Single-judge (the generic default) / docs-only /
preflight short-circuit paths MUST NOT include these fields (they would mislead
consumers into thinking multi-judge ran).

**Multi-judge success (APPROVED via 2-of-2 consensus, all judges aligned):**
```
<!-- OUTCOME {"status":"success","stage":"REVIEW","verdict":"APPROVED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":0,"nits":0,"judges_run":2,"consensus_disagreement":false},"notes":"Approved via 2-of-2 consensus (code-quality, risk).","next_skill":"/do-docs"} -->
```

**Multi-judge fail (CHANGES_REQUESTED — judges disagreed, any-blocker-wins triggered):**
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":1,"tech_debt":0,"nits":0,"judges_run":2,"consensus_disagreement":true},"notes":"Changes requested via 2-of-2 consensus: risk judge raised 1 blocker, code-quality approved.","failure_reason":"1 blocker must be fixed before merge","next_skill":"/do-patch"} -->
```

**Multi-judge partial (CHANGES_REQUESTED — judges aligned on tech_debt/nits, no blockers):**
```
<!-- OUTCOME {"status":"partial","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":2,"nits":1,"judges_run":2,"consensus_disagreement":false},"notes":"Changes requested via 2-of-2 consensus: 2 tech_debt and 1 nit findings. Routing to /do-patch.","next_skill":"/do-patch"} -->
```

## Multi-judge & cross-vendor consensus (optional, only if the context file declares it)

The generic baseline is a **single reviewer**: you evaluate the diff, classify
findings, and post one verdict. The multi-judge OUTCOME variants above apply only
when a repo opts into consensus review.

If the context file declares a multi-judge consensus model (≥2 parallel review
judges aggregated into one verdict, an optional cross-vendor judge, a PR-diff
shape classifier for cost containment, and a single-writer verdict recorder),
orchestrate it exactly as the context file specifies. The invariants that hold in
every consensus configuration:

- Each judge fork RETURNS its dict — it does not post a PR comment or record state itself.
- The parent posts per-judge comments under a heading prefix distinct from the aggregate `## Review:` comment, then posts the aggregate comment **last**.
- ONE verdict-record call writes the scalar verdict plus any consensus metadata (single-writer invariant).
- A failed/skipped optional judge is treated as a skip, never a crash (unless the repo marks it fail-closed).

In the generic case, skip all of this — one reviewer, one verdict. Multi-judge:
exactly ONE single-writer verdict-record call after `compute_consensus`, run
immediately after the review is posted and **before** the OUTCOME block.
