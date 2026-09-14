# Multi-Judge Consensus at Review

**Status:** Shipped (#1309).
**Plan:** `docs/plans/multi-judge-consensus-gates.md` (rev1).

## Overview

The Review gate can spawn K parallel judges with distinct lenses
(`code-quality`, `risk`) and aggregate their findings into a single verdict.
This catches both false positives (a single judge blocking on a stylistic
concern) and false negatives (a single judge missing a security issue
another lens would catch). The pattern mirrors `/do-plan-critique`'s
existing six-critic war room — see `.claude/skills-global/do-plan-critique/`.

## Verdict shape

`AgentSession.stage_states._verdicts["REVIEW"]` gains two optional
side-fields when multi-judge runs:

```json
{
  "verdict": "CHANGES REQUESTED",
  "recorded_at": "2026-05-08T12:34:56+00:00",
  "artifact_hash": null,
  "blockers": 1,
  "tech_debt": 0,
  "_judges": [
    {
      "judge_id": "code-quality",
      "verdict": "APPROVED",
      "blockers": 0,
      "tech_debt": 0,
      "confidence": 0.85,
      "reasoning_summary": "...",
      "review_url": "https://github.com/.../pull/.../reviews/..."
    },
    {
      "judge_id": "risk",
      "verdict": "CHANGES REQUESTED",
      "blockers": 1,
      "tech_debt": 0,
      "confidence": 0.95,
      "reasoning_summary": "...",
      "review_url": null
    }
  ],
  "_consensus": {
    "rule": "any-blocker-wins",
    "k": 2,
    "n": 2,
    "mean_confidence": 0.9,
    "blocker_aggregation": "max",
    "tied": true,
    "decided_at": "2026-05-08T12:34:56+00:00"
  }
}
```

The scalar `verdict` / `blockers` / `tech_debt` at the top of the record
remain authoritative for existing readers (SDLC router G6, `do-merge.md`).
The `_judges` and `_consensus` side-fields are descriptive only — they
exist for audit and debugging, not for routing.

## Single-writer invariant (preserved)

`tools/sdlc_verdict.py::record_verdict` is the **only** writer of
`_verdicts`. The multi-judge extension is a pure shape extension on the
existing writer: per-judge dicts and consensus metadata flow through the
same single call. There is no `record_judge_verdict` / `finalize_consensus`
fork of the API surface. Single-judge skills (`/do-plan-critique`) call
`record_verdict` with no `judges` / `consensus` kwargs and write today's
shape verbatim.

```python
from tools.sdlc_verdict import record_verdict
from agent.sdlc_review_consensus import compute_consensus

# Parent skill flow — single record_verdict call writes scalar + side-fields:
judges = [judge_a_dict, judge_b_dict]
agg = compute_consensus(judges, rule="any-blocker-wins", expected_judges=2)
record_verdict(
    session,
    "REVIEW",
    agg["verdict"],
    blockers=agg["blockers"],
    tech_debt=agg["tech_debt"],
    judges=judges,
    consensus=agg["consensus"],
)
```

## Configuration

**The judge roster is declared, not configured.** Two judges run on
non-trivial PRs — `code-quality` and `risk` — declared in
[`docs/sdlc/do-pr-review.md`](../sdlc/do-pr-review.md)'s Multi-Judge
Consensus section, which the review skill reads at runtime. Changing the
roster means editing that declaration: a reviewed, versioned change, visible
to everyone, rather than an unversioned per-machine override that would
silently halve review strength on one box.

There is no env var for the roster or the judge count. The only env controls
on this surface are the four cross-vendor toggles, documented in
[Env vars (all provisional/tunable)](#env-vars-all-provisionaltunable) below.

### Cost containment

One layer, and it needs no operator action: **the trivial-diff check.** PRs
whose changed files are all docs (`docs/**`, `**/*.md`) or all lockfile sync
(`uv.lock` / `pyproject.toml` only) force the legacy single-judge path. It is
automatic and per-PR, which is what makes it the right shape for this — cost
scales with what a PR actually is, not with what someone remembered to export.
This path is unaffected by the quorum floor below: it is inline prose (the
"Cost containment" bullet in `docs/sdlc/do-pr-review.md`'s Multi-Judge
Consensus section, not a script — there is no shape classifier module)
applied by the executing agent, and it never calls
`compute_consensus` at all. It posts one judge's verdict directly, with no
`judges`/`consensus` kwargs on `record_verdict` and no `judges_run` in its
OUTCOME, so the floor has nothing to trip on that path.

## Consensus rules

Two rules, implemented in `agent/sdlc_review_consensus.py`:

- **`any-blocker-wins`** (default). If any judge returned blockers > 0 or
  a non-`APPROVED` verdict, the consensus is `CHANGES REQUESTED` with
  `blockers = max(judge.blockers)`. Otherwise `APPROVED`. This makes
  disagreement at K=2 always resolve to the conservative outcome — no
  human escalation, no fourth judge.
- **`unanimous-approved`** (opt-in). Top-level `APPROVED` only if all K
  judges approved with zero blockers.

**`_consensus.k` is a count, not a threshold.** `compute_consensus` takes
`(judges, rule)` and nothing else; it assigns `k` and `n` the same value — the
number of judges whose verdicts it aggregated. Neither rule consults a
threshold: `any-blocker-wins` is 1-of-N by construction, and
`unanimous-approved` is N-of-N. `K` throughout this document means "how many
judges ran", never "how many must agree", and there is no K to tune.

The `_consensus.tied` flag is `true` when judges disagreed (i.e. at least
one judge approved AND at least one blocked). It is descriptive — the
verdict is already conservative under either rule.

## Quorum floor (issue #3197)

`compute_consensus` optionally accepts a keyword-only `expected_judges: int
| None`, the size of the **mandatory declared roster** — today `2`
(`code-quality`, `risk`) — and never the number of judges dispatched. The
review skill passes `expected_judges=2`, derived from the same roster list
it just iterated to dispatch, never a second hardcoded literal
(`docs/sdlc/do-pr-review.md`'s Multi-Judge Consensus section).

When fewer distinct judges report than the floor, `compute_consensus`
refuses to run the rule and returns the conservative `CHANGES REQUESTED`
outcome instead — the same outcome the zero-judge case already used,
generalized into one shared builder (`_conservative_outcome`; there is no
second builder and no orphaned `_empty_conservative_outcome` name). The
consensus metadata carries two additive keys so the shortfall is recorded
rather than merely inferable from `k`/`n`:

| Key | Type | Meaning |
|---|---|---|
| `expected_n` | `int \| None` | The declared roster size the caller asserted, verbatim. `None` when the caller declared no expectation (every pre-#3197 call site). |
| `quorum_shortfall` | `bool` | Always present. `True` iff `expected_n is not None and n < expected_n`. |

**Optional judges are excluded by construction.** The cross-vendor judge
(issue #1626) is designed to skip under normal operation — gate off,
trivial diff, API failure. A skip means the parent appends nothing, so `n`
legitimately sits at the mandatory roster size; that must satisfy the
floor, not trip it. A cross-vendor *return* raises `n` above the floor and
must not be penalized either. Both directions are tested in
`tests/unit/test_review_multi_judge.py::TestComputeConsensusQuorumFloor`
and `tests/unit/test_cross_vendor_orchestration.py`.

**The guard's limit, stated plainly:** it compares cardinality only, never
membership. `n < expected_judges` counts distinct `judge_id`s and never
checks them against the declared roster's contents — a misnamed,
substituted, or optional judge id satisfies the floor identically to a
mandatory one. A shortfall proves under-reporting; a satisfied floor does
not prove the declared roster specifically ran. Closing that gap would
mean passing roster membership into the function, which moves roster
ownership out of the caller and is deliberately out of scope.

**A shortfall does not get a distinct verdict token.** It stays
`CHANGES REQUESTED`, which routes the lane to `/do-patch` and re-dispatches
the full roster at the next round — self-healing when the cause is
transient. The cause actually observed (judges failing to spawn) can also
be persistent, in which case `/do-patch` has no code defect to act on and
the lane cycles REVIEW ⇄ `/do-patch` until the pipeline's round cap stops
it. **`quorum_shortfall: true` recurring across consecutive REVIEW rounds
on the same PR is the signature of a persistent judge-dispatch failure and
warrants human escalation**, as distinct from a single-round shortfall the
next round clears. That recurrence is not readable from a single
`sdlc-tool verdict get` call — `record_verdict` overwrites
`_verdicts["REVIEW"]` on every write (`tools/sdlc_verdict.py`'s `_apply`
closure does `verdicts[stage] = record`), so the verdict record answers for
the current round alone. The surface that actually accumulates is the run
of aggregate `## Review:` PR comments, one posted per round (see
[PR-comment ordering invariant](#pr-comment-ordering-invariant) below),
each naming its own shortfall — that is where the across-rounds evidence
lives.

## PR-comment ordering invariant

`do-merge.md`'s regex (`^## Review: (Approved|Changes Requested)`) picks up
the **latest** matching comment. The Review skill must guarantee the
aggregate is the last `## Review*:` heading on the PR:

1. Per-judge comments use the distinct prefix `## Review (Judge {id}):` —
   this does NOT match the merge regex.
2. The parent posts per-judge comments **sequentially** (each `gh pr comment`
   call awaited).
3. The aggregate `## Review:` comment is posted **last**, strictly after
   all per-judge comments are confirmed posted.

This invariant is asserted by `tests/unit/test_review_multi_judge.py` —
specifically `TestPRCommentOrderingRegression`.

## Monitoring

When multi-judge runs, the OUTCOME block records:

- `judges_run` (int) — number of judges actually dispatched.
- `consensus_disagreement` (bool) — true when judges disagreed (mirrors
  `_consensus.tied`).

These let operators grep session state for cost (judges-per-PR) and signal
quality (disagreement rate) without a dedicated dashboard.

On a quorum shortfall (see [Quorum floor](#quorum-floor-issue-3197) above),
the artifacts instead carry `judges_run` and `quorum_shortfall: true`, and
**omit `consensus_disagreement`** — that field derives from `tied`, which is
only meaningful once the rule ran over a full roster, and the rule never
runs on a shortfall.

## Back-compat

- `/do-plan-critique` continues to call `record_verdict` with no `judges`
  / `consensus` kwargs. The persisted CRITIQUE shape is bit-identical to
  pre-#1309. CRITIQUE is **rejected** if either kwarg is passed — the
  internal critics already aggregate before recording.
- SDLC router guard G6 reads `_verdicts["REVIEW"].verdict` for `APPROVED`.
  Multi-judge does not change this read — the scalar is populated in the
  same single write call.
- `do-merge.md`'s PR-comment check is unchanged. Its regex matches only
  the aggregate, by construction (per-judge headings have a different
  prefix).

## Cross-vendor judge (issue #1626)

An optional third judge (`judge_id="cross-vendor"`) runs a non-Claude model
(default: `gpt-4o`) alongside the existing Claude judges. Because a different
vendor's training distribution yields uncorrelated error patterns, a class of
defect that Claude systematically misses has a structural chance of being
caught by the cross-vendor judge.

### Trigger gate

Two conditions must both hold — if either is false the judge is silently
skipped (logged as `disabled`):

1. `SDLC_REVIEW_CROSS_VENDOR=1` is set in the vault `.env` (default `0`/off).
2. The PR is a substantive code change — the trivial-diff check above does
   not match. Docs-only and lockfile-only PRs never pay the cost.

### Consensus integration

The cross-vendor judge returns a dict in exactly the same shape as the Claude
judges. It is appended to the `judges` list before `compute_consensus` is
called — `any-blocker-wins` therefore treats a cross-vendor blocker identically
to a Claude judge blocker. A single cross-vendor CHANGES REQUESTED verdict
forces the aggregate outcome to CHANGES REQUESTED regardless of how many Claude
judges approved.

The consensus layer (`agent/sdlc_review_consensus.py`) is **unchanged** — it is
vendor-agnostic and consumes only `{judge_id, verdict, blockers, ...}` dicts.

### Failure / degrade behavior

Default (`SDLC_REVIEW_CROSS_VENDOR_REQUIRED=0`): if the cross-vendor judge
fails for any reason (OpenAI API error, bad model id, rate limit, JSON parse
failure, type coercion failure), the CLI emits a `{"status":"skipped",...}`
envelope. The `/do-pr-review` parent does not append the skip envelope to the
judges list. Consensus proceeds with the Claude judges only. The aggregate
comment includes a visible "Note: cross-vendor judge skipped — {reason}".

Fail-closed (`SDLC_REVIEW_CROSS_VENDOR_REQUIRED=1`): a skip injects a
synthetic CHANGES REQUESTED judge dict citing the missing cross-vendor verdict,
so the review fails if the cross-vendor judge could not run.

### Observability

Every CLI invocation emits exactly one `logger.info` tri-state line:
- `ran`: judge returned a verdict; includes model id + raw `prompt_tokens` /
  `completion_tokens` from the API `usage` (no dollar amounts — rates drift).
- `skipped`: judge could not run; includes exception class and model id.
- `disabled`: the gate was off or the diff was trivial; logged by the
  parent, not the CLI.

The same token counts are stored in the judge dict's `meta` field, so the
recorded `_judges` entry is self-describing.

### Env vars (all provisional/tunable)

| Var | Default | Purpose |
|-----|---------|---------|
| `SDLC_REVIEW_CROSS_VENDOR` | `0` | Enable the cross-vendor judge (operator kill switch). |
| `SDLC_REVIEW_CROSS_VENDOR_MODEL` | `gpt-4o` | OpenAI model id. Env-overridable; bad ids degrade to skip. |
| `SDLC_REVIEW_CROSS_VENDOR_MAX_DIFF_TOKENS` | `50000` | Token cap; diffs exceeding this are truncated with a marker. |
| `SDLC_REVIEW_CROSS_VENDOR_REQUIRED` | `0` | Fail-closed: skip forces CHANGES REQUESTED. |

### Key invariants

- `CROSS_VENDOR_JUDGE_ID = "cross-vendor"` is defined once in
  `tools/cross_vendor_judge.py` and is disjoint from `"code-quality"` and
  `"risk"`. `_dedup_last_wins` in `compute_consensus` therefore never
  collapses the cross-vendor entry onto a Claude judge.
- A skip envelope (`{"status":"skipped",...}`) has no path to
  `compute_consensus` or `record_verdict(judges=)` — the parent's
  append-iff-ok guard makes it structurally impossible.
- `agent/sdlc_review_consensus.py` and `tools/sdlc_verdict.py` are
  **unchanged** — the cross-vendor judge is a new dict producer only.

### Tests

- `tests/unit/test_cross_vendor_judge.py` — envelope shape, failure paths,
  type coercion, token cap, logging behavior.
- `tests/unit/test_cross_vendor_orchestration.py` — parent append-iff-ok
  contract, skip-envelope never reaches consensus or verdict recorder.
- `tests/unit/test_review_multi_judge.py::TestCrossVendorJudgeConsensus` —
  hard deterministic assertion: cross-vendor blocker among Claude approvals
  forces CHANGES REQUESTED; constant disjointness check.
- `tests/unit/test_sdlc_verdict.py::TestCrossVendorJudgeRoundTrip` —
  cross-vendor dict round-trips into `_judges` via `record_verdict`.

## Related

- `tools/sdlc_verdict.py` — the single writer.
- `agent/sdlc_review_consensus.py` — pure consensus rule helper.
- `tools/cross_vendor_judge.py` — cross-vendor judge CLI (issue #1626).
- `.claude/skills-global/do-pr-review/SKILL.md` — orchestration site.
- `.claude/commands/do-merge.md` — downstream consumer (unchanged).
- `.claude/skills-global/do-sdlc/SKILL.md` G6 — downstream consumer
  (unchanged).
- `tests/unit/test_review_multi_judge.py` — consensus rules + ordering
  regression.
