---
status: Complete
type: critique
round: 4
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3177
plan: docs/plans/recursive-self-improvement.md
reviewed_commit: 2ec4c20c5
verdict: READY TO BUILD (with concerns)
---

# Plan Critique: Recursive self-improvement controller (round 4)

**Plan**: `docs/plans/recursive-self-improvement.md`
**Issue**: #3177
**Critics**: Risk & Robustness, Scope & Value, History & Consistency (FULL depth)
**Mode**: independent roster (3 critics)
**Findings**: 6 total (0 blockers, 4 concerns, 2 nits)

## Scope of this round

Rounds 1 through 3 critiqued the plan up to and including Gap G. Round 3 returned READY TO BUILD (no concerns) with seven nits. The charter v2 reconciliation of 2026-09-07 landed afterwards, in `f1c04b30a` (charter v2 plus the reconciliation block) and `2ec4c20c5` (the reconciliation carried through design, tasks and verification). Round 4 exists to critique that delta: the compounding-capacity north star and downstream-signs table, Gap D's three budget units, Gap F's amendment path, Gap G, resource-acquisition authority, provider eligibility, Risks 7 and 8, merge authority, lanes 5 through 7, and seven new Verification rows. Critics were instructed not to re-raise anything already recorded in the plan's Critique Results or accepted in `recursive-self-improvement-revision-proposal.md`.

## Blockers

None.

## Concerns

### Unit 2's per-call dollar metering has no source in the code it cites
- **Severity**: CONCERN
- **Critics**: Risk & Robustness (independently re-verified by the aggregator)
- **Location**: Gap D Unit 2; Reuse map; spike-5 impact (`:158`, `:283`, `:356`)
- **Finding**: The plan names `tools/cross_vendor_judge.py` three times as an OpenRouter caller that returns per-call usage in the response envelope. It is neither. `tools/cross_vendor_judge.py:209` constructs `OpenAI(api_key=settings.api.openai_api_key)` with no `base_url`, so it calls `api.openai.com` rather than `OPENROUTER_URL` (`config/models.py:56`), and `:223-224` read only `prompt_tokens` and `completion_tokens`, never a cost field. `config/settings.py:33` confirms `openai_api_key` is a separate setting from `OPENROUTER_API_KEY`. "Settled per call" for Unit 2, which Risk 5 and Risk 7 mitigations depend on, therefore rests on a metering source that does not exist today.
- **Suggestion**: Correct the three claim sites and spike the real settlement path before lane 3 or lane 5 builds it.
- **Implementation Note**: Either point the OpenAI-SDK client at `base_url=OPENROUTER_URL` keyed with `OPENROUTER_API_KEY` and pass `extra_body={"usage": {"include": True}}` so `response.usage.cost` is populated, or maintain a dated per-model price table and compute `cost = prompt_tokens * price_in + completion_tokens * price_out`, marking the row `metering="estimated"` per Gap D's own fallback language.

### Four "lanes 3 through 6" strings survive the addition of lane 7
- **Severity**: CONCERN
- **Critics**: Scope & Value and History & Consistency (independent convergence), plus the structural sweep
- **Location**: `:624` Success Criteria subheading, `:676` Team Orchestration, `:680` Step by Step Tasks preamble, `:769` task 6 title
- **Finding**: The reconciliation added lane 7 (cloud execution) and updated Appetite `:201`, Success Criteria `:620` and `:632`, and task 6's body `:780`, but four headings and sentences still say lanes 3 through 6. A builder skimming task 6's title files four child issues rather than five, silently dropping the charter §2 cloud-execution commitment, and lane 7 is left with no stated staffing model.
- **Suggestion**: Change the four strings to "lanes 3 through 7".
- **Implementation Note**: Four one-line string edits at `:624`, `:676`, `:680`, `:769`. Nothing else changes; the Verification row "Five child issues exist for lanes 3 through 7" already asserts `output > 4`.

### The promised credential-creation notice to Tom has no delivery seam
- **Severity**: CONCERN
- **Critics**: Risk & Robustness
- **Location**: Gap D resource-acquisition bullet `:390`, against Agent Integration `:590` and Gap F `:394`
- **Finding**: The plan says a credential obtained during resource acquisition is vaulted "with a notice to Tom that the item exists". Agent Integration and Gap F both state the loop sends exactly one message class, the charter-amendment request, and none of the enumerated `valor-improve` subcommands, reflections, or outbound hooks delivers the notice. An autonomous loop that opens financially-capable accounts inside a live $50/week unit has no described, testable channel by which Tom learns a new credential exists.
- **Suggestion**: Wire the notice into an existing channel or delete the promise.
- **Implementation Note**: Cheapest wiring is for `tools/vault_write.py`'s credential-write path to also write a `resource_acquired`-kind `ImprovementEvidence` / provisional-assumption row carrying the vault item title and fingerprint (never the secret), so the existing three-day `improvement-assumption-digest` reflection surfaces it with no new outbound channel, plus a Verification row asserting a seeded `resource_acquired` row reaches the digest's rendered output.

### Task 5's mutation-check list does not cover the Verification rows v2 added
- **Severity**: CONCERN
- **Critics**: History & Consistency
- **Location**: Task 5 "Validate lanes 1 and 2" `:764`, against Verification `:813-816` and `:834-839`
- **Finding**: Task 5 enumerates exactly which guards get a mutation check so a green row that reaches no code cannot pass. The list predates the reconciliation and names only pre-v2 guards. It covers none of the charter-seeding rows, the three budget-default rows, the no-routine-question anti-criterion, or the credential anti-criterion. Several of those are greps that pass vacuously during lanes 1-2 because the lane-3 files they exclude (`tools/improvement_amendment.py`, `tools/vault_write.py`) do not exist yet, which is precisely the failure mode task 5 was written to catch for the older rows.
- **Suggestion**: Extend the enumeration, or state which v2 rows are not meaningfully checkable before lane 3 and why a vacuous pass is acceptable for them.
- **Implementation Note**: Append the charter-loader test (`tests/unit/test_improvement_charter.py`), the three `ImprovementSettings` default assertions, and the two new grep anti-criterion rows to the list at `:764`.

## Nits

### Budget window keying at admission versus settlement is unspecified
- **Severity**: NIT
- **Critics**: Risk & Robustness
- **Location**: Gap D, Units 2 and 3
- **Finding**: A reservation taken at 23:59:59 UTC and settled at 00:00:01 UTC either strands its reservation against a closed day or is credited against the wrong window. Races 1 through 6 and `improvement-intent-reconcile` reconcile stale intents, not window crossings.
- **Suggestion**: State that a reservation's window key is fixed at admission and carried through settlement, and have the reconciliation reflection release any reservation whose window closed unsettled, converting it to a dated `spend_receipt` estimate.

### Pre-v2 "question ceiling at zero" phrasing survives in two places
- **Severity**: NIT
- **Critics**: History & Consistency
- **Location**: Gap G `:398`, Risk 6 `:504`
- **Finding**: Both retain the pre-reconciliation framing. Not false under v2, since routine research questions are indeed zero, but it reads as if no escape valve exists when Gap F's amendment path is exactly that valve. Bullet `:35`'s landing-site list confirms neither spot was touched.
- **Suggestion**: Reword to "no routine research questions", or add a one-clause pointer to the amendment path at first mention.

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-8 contiguous |
| Dependencies valid | PASS | All `Depends On` ids resolve; graph acyclic |
| File paths exist | PASS | Every cited path exists except files this build creates (`models/improvement_*.py`, `tools/improvement_*.py`, `tests/unit/test_improvement_*.py`) |
| Prerequisites met | PASS | All four executed: Redis via Popoto, `gh auth`, interpreter on pin, `OPENROUTER_API_KEY` present |
| Cross-references | FAIL | Lane 7 present in Appetite, Success Criteria and task 6 body but absent from four "lanes 3 through 6" strings (see concern 2) |
| Verification table | PASS | 37 rows parse via `agent/verification_parser.py::parse_verification_table`, 0 malformed, 0 skipped |

## Verdict

**READY TO BUILD (with concerns)** — 0 blockers. Four concerns carry Implementation Notes and enter a revision pass via `/do-plan`, followed by re-critique, before `/do-build`. The `plan_revising` lock is set.

## Open question for Tom

Concern 3 is a judgment call, not an engineering one: should the loop notify you when it creates a credential-bearing account, and if so is a line in the existing three-day assumption digest enough, or do you want a distinct Telegram message at the moment of acquisition? The alternative accepted answer is that no notice is wanted and the promise at `:390` is deleted.
