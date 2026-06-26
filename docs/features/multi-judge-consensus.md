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
agg = compute_consensus(judges, rule="any-blocker-wins")
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

Two env vars control the multi-judge surface; both default to safe values.

| Env var | Default | Purpose |
|---|---|---|
| `SDLC_REVIEW_JUDGES` | `code-quality,risk` | Comma-list of enabled judge IDs. Set to `none` or empty to use the legacy single-judge path. |
| `SDLC_REVIEW_K` | `2` | K-of-N for consensus arithmetic. Auto-clamped to `min(SDLC_REVIEW_K, len(enabled_judges))`. |

### Cost containment

Three independent layers limit cost:

1. **Shape classifier** (reused from `do-merge.md`): docs-only and
   lockfile-only PRs force the legacy single-judge path.
2. **Per-judge disable**: `SDLC_REVIEW_JUDGES=code-quality` runs only the
   code-quality judge, halving cost without losing K-of-N math entirely.
3. **K kill switch**: `SDLC_REVIEW_K=1` reverts to legacy behavior even
   if `SDLC_REVIEW_JUDGES` lists multiple.

## Consensus rules

Two rules, implemented in `agent/sdlc_review_consensus.py`:

- **`any-blocker-wins`** (default). If any judge returned blockers > 0 or
  a non-`APPROVED` verdict, the consensus is `CHANGES REQUESTED` with
  `blockers = max(judge.blockers)`. Otherwise `APPROVED`. This makes
  disagreement at K=2 always resolve to the conservative outcome — no
  human escalation, no fourth judge.
- **`unanimous-approved`** (opt-in). Top-level `APPROVED` only if all K
  judges approved with zero blockers.

The `_consensus.tied` flag is `true` when judges disagreed (i.e. at least
one judge approved AND at least one blocked). It is descriptive — the
verdict is already conservative under either rule.

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
2. The PR shape is `feature` (from `python -m scripts.pr_shape_classify`).
   Trivial shapes (`docs-only`, `lockfile-only`, `small-patch`, `mixed`)
   never pay the cost.

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
- `disabled`: the gate was off or the shape was not `feature`; logged by the
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
- `.claude/skills/sdlc/SKILL.md` G6 — downstream consumer
  (unchanged).
- `tests/unit/test_review_multi_judge.py` — consensus rules + ordering
  regression.
