# Sub-Skill: Outcome Contract

Emit a typed OUTCOME as the **very last line** of output, after the review is
posted and verified and, if the context file declares a substrate, after the
verdict is recorded: the OUTCOME block is the last line, not the last action.
It is an HTML comment, invisible when rendered but parsed by the pipeline.

## Verdict taxonomy

| Verdict | When | OUTCOME status | `next_skill` |
|---------|------|----------------|--------------|
| `APPROVED` | Preflight clean, zero findings | `success` | `/do-docs` |
| `CHANGES_REQUESTED` | Findings exist | `partial` (tech_debt/nits only) or `fail` (any blocker) | `/do-patch` |
| `BLOCKED_ON_CONFLICT` | Preflight short-circuit: `CONFLICTING`, `DIRTY`, or `UNKNOWN` after retry | `fail` | `null` |
| `PR_CLOSED` | Preflight short-circuit: `state != OPEN` | `fail` | `null` |

Use `partial`, never `success`, whenever tech_debt or non-subjective nits exist,
so the pipeline routes to `/do-patch` before `/do-docs`. `next_skill: null`
stops auto-advance: the author must rebase, or the PM handles the closed PR.

## Single-reviewer variants (the generic default)

```
<!-- OUTCOME {"status":"success","stage":"REVIEW","verdict":"APPROVED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":0,"nits":0},"notes":"Approved with no findings.","next_skill":"/do-docs"} -->
```

```
<!-- OUTCOME {"status":"partial","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":2,"nits":1},"notes":"Changes requested: 2 tech_debt and 1 nit findings. Routing to /do-patch.","next_skill":"/do-patch"} -->
```

```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":2,"tech_debt":1,"nits":0},"notes":"Changes requested: 2 blockers found.","failure_reason":"2 blockers must be fixed before merge","next_skill":"/do-patch"} -->
```

```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"BLOCKED_ON_CONFLICT","artifacts":{"review_url":"{comment_url}","mergeStateStatus":"DIRTY","mergeable":"CONFLICTING"},"notes":"Branch has merge conflicts; rebase required before review.","failure_reason":"mergeStateStatus=DIRTY — author must rebase/resolve conflicts before review can proceed","next_skill":null} -->
```

```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"PR_CLOSED","artifacts":{"review_url":"{comment_url}","state":"CLOSED"},"notes":"PR is not open; review skipped.","failure_reason":"state=CLOSED — no review performed on a closed PR","next_skill":null} -->
```

## Multi-judge variants (only when the context file declares consensus)

When two or more judges ran, `artifacts` also carries `judges_run` and
`consensus_disagreement`. Single-judge, docs-only, and preflight short-circuit
paths MUST NOT include these fields (they would mislead consumers into thinking
multi-judge ran).

```
<!-- OUTCOME {"status":"success","stage":"REVIEW","verdict":"APPROVED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":0,"nits":0,"judges_run":2,"consensus_disagreement":false},"notes":"Approved via 2-of-2 consensus (code-quality, risk).","next_skill":"/do-docs"} -->
```

```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":1,"tech_debt":0,"nits":0,"judges_run":2,"consensus_disagreement":true},"notes":"Changes requested via 2-of-2 consensus: risk judge raised 1 blocker, code-quality approved.","failure_reason":"1 blocker must be fixed before merge","next_skill":"/do-patch"} -->
```

```
<!-- OUTCOME {"status":"partial","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","blockers":0,"tech_debt":2,"nits":1,"judges_run":2,"consensus_disagreement":false},"notes":"Changes requested via 2-of-2 consensus: 2 tech_debt and 1 nit findings. Routing to /do-patch.","next_skill":"/do-patch"} -->
```

**Quorum shortfall** (fewer distinct judges reported than the declared roster):

```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"CHANGES_REQUESTED","artifacts":{"review_url":"{review_url}","judges_run":1,"quorum_shortfall":true},"notes":"Quorum shortfall: 1 of 2 declared judges reported (risk did not return). Not read as consensus.","failure_reason":"quorum_shortfall — declared roster of 2 fell short, rule did not run","next_skill":"/do-patch"} -->
```

A shortfall omits `consensus_disagreement`: the rule never ran, so reporting
`false` would read as "the judges agreed". Its `notes` names the degraded run in
the first clause.

## Consensus invariants

When the context file declares multi-judge consensus, orchestrate it as the
context file specifies, and in every configuration:

- Each judge subagent RETURNS its findings; it does not post or record state.
- The parent posts per-judge comments under a heading distinct from the
  aggregate `## Review:` comment, and posts the aggregate comment **last**.
- ONE verdict-record call writes the verdict plus consensus metadata, after the
  review is posted and before the OUTCOME block (single writer).
- A failed or skipped optional judge is a skip, never a crash, unless the repo
  marks it fail-closed.
