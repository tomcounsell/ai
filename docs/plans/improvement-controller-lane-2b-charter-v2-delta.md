---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-09-08
parent_plan: docs/plans/recursive-self-improvement.md
charter: docs/improvement-charter.md
charter_version: 2
baseline_commit: 2fb7df519931260c5a7b9e4880c1d8748e863401
tracking: https://github.com/tomcounsell/ai/issues/3255
---

# Improvement controller lane 2b: charter v2 delta

## Problem

`main` carries the records, settings, and prose that lanes 1 and 2 shipped in PR #3224 on 2026-09-07. That build was faithful to the plan revision it was given, and that revision predates charter version 2 (2026-09-07). Six surfaces on `main` now encode decisions the charter has since replaced, and every downstream lane reads at least one of them.

The parent plan states the reconciliation in [Gap D](recursive-self-improvement.md#gap-d-three-reservation-units-one-of-which-is-not-money) (three separately reserved budget units, day and week boundaries disclosed) and [Gap G](recursive-self-improvement.md#gap-g-the-charter-as-north-star) (the charter as a pinned, digest-addressed record every case cites). This lane carries that reconciliation into code. It restates neither gap.

What is on `main` at `2fb7df5199` today:

- `config/settings.py:618` declares `daily_external_llm_usd`, and `:630` declares `portfolio_allocation` with a fixed `objective=weight` split. `git grep portfolio_allocation -- '*.py'` returns its own declaration and nothing else. There is no weekly infrastructure budget and no window-boundary fields, so charter §8's second spending category ($50 per week, sandboxes and storage and Cloudflare) cannot be admitted or reported at all.
- `models/improvement_case.py:91` carries `objective = Field(null=True)` over the withdrawn four-objective vocabulary. It has no writer and no index. Nothing on the case records a priority area, a ranking rationale, or the charter digest the case was ranked under, and `models/improvement_investigation.py` and `models/improvement_release.py` carry no `charter_digest` either.
- `models/improvement_charter.py:70-80` declares `version`, `scope`, `authority`, `budgets`, `approved_by`, `approved_at`, `notes` — no digest, no text, no loader. Nothing seeds the record from the file, so the controller has no pinned charter to cite, and charter §12's "versioned reference to the charter used for decisions and results" has nothing behind it.
- `tools/improvement_eligibility.py` and `tools/improvement_resources.py` do not exist. Charter §7 (any provider for open-source work, subscriptions for client work) and §8 (verify each named resource before relying on it) have no code behind them.
- `docs/features/improvement-controller.md:165-171` documents the `portfolio_allocation` split and states "The ceiling is zero... The controller asks Tom nothing." Charter v2 §9 replaced both: no routine research questions, plus an explicit amendment-request path. `.env.example:358-359` repeats the same withdrawn vocabulary in prose.
- `ui/templates/improvement/` holds `coverage.html` and `intervention_burden.html` and no goals partial, so charter §11's readable record (goals, ranking, acquired abilities, evaluations, rejected approaches, unresolved assumptions, resource use) has no surface at all.

Lane 3 (#3215) is specified against v2 and blocks on this vocabulary. Lanes 4 through 6 read the digest and the settings names. If this lane does not land first, each of them either builds on withdrawn vocabulary or re-derives the same corrections independently, four times over.

**Desired outcome.** `main` matches Gap D and Gap G. The v2 Verification rows in the parent plan pass. Lane 3 consumes `charter_digest`, `priority_area`, and the renamed settings without redefining them. The feature doc describes the v2 status quo and nothing else.

## Freshness Check

**Disposition: Unchanged.** Baseline `2fb7df519931260c5a7b9e4880c1d8748e863401` (`main`, 2026-09-08).

Issue #3255 was filed 2026-09-08T08:13:28Z. Exactly one commit landed on `main` between filing and this plan: `2fb7df519` ("Router: stand row 2b down past the plan stage, add G3's missing DOCS leg (#3246)"), which touches the SDLC router and none of the files this lane changes.

Every file:line the issue cites was re-read at the baseline, not carried forward:

| Citation | Verified at baseline | Result |
|---|---|---|
| `config/settings.py:618` | `daily_external_llm_usd: float = Field(` | exact |
| `config/settings.py:630` | `portfolio_allocation: str = Field(` | exact |
| `models/improvement_case.py:91` | `objective = Field(null=True)` | exact |
| `models/improvement_charter.py:70-80` | field block, no digest / text / loader | exact |
| `docs/features/improvement-controller.md:165-171` | allocation row and "ceiling is zero" paragraph | exact |
| `tools/improvement_eligibility.py`, `tools/improvement_resources.py`, `tools/vault_write.py` | `ls tools/improvement_*.py tools/vault_write.py` → no matches | absent, as stated |
| `ui/templates/improvement/` | `coverage.html`, `intervention_burden.html` only | exact |
| `ui/data/improvement.py` | exists, 197 lines, three `get_*` functions | exact |

Sibling issues and PRs re-resolved at plan time: #3177 OPEN, #3215 OPEN, #3216 OPEN, #3217 OPEN, #3218 OPEN, #3220 OPEN; PR #3224 MERGED 2026-09-07T14:30:38Z, PR #3229 MERGED 2026-09-07T16:24:36Z. Every state matches what the issue asserts.

**Two corrections to the issue's own reference list**, found by reading rather than by trusting the citation:

1. `.env.example:358-359` also carries the withdrawn vocabulary, in the `IMPROVEMENT__ENABLED` comment block: "The other IMPROVEMENT__* knobs (concurrency, daily external-LLM dollars, portfolio allocation, tick cadence)". The issue does not name it, and the parent plan's `git grep portfolio_allocation` confirmation row would fail on it. It is on the task list.
2. `git grep 'objective' -- '*.py'` at the baseline returns matches in four files, only two of which are this lane's: `models/improvement_case.py:75,91` and `config/settings.py:634-635` (prose inside the `portfolio_allocation` description, which goes with the field). The other two — `tools/memory_eval/query_set.py:9` and `tools/valor_session.py:1141` — are ordinary English usage in unrelated modules and must not be touched. A builder running a bare `git grep objective` will see them; they are not in scope.

**Plan overlap:** the only active plan touching this area is the parent, `docs/plans/recursive-self-improvement.md` (Planning, `revision_applied: true`). That is a declared parent relationship, not a collision. No other plan in `docs/plans/` mentions the improvement controller.

## Prior Art

- **PR #3224 — "Recursive self-improvement controller: lanes 1 and 2"** (MERGED 2026-09-07). Shipped the eight flat `Improvement*` models, `ImprovementSettings`, the evidence adapters, the `VerifyingArtifactStore`, the collection-tick registration, the `TaskTypeProfile` retirement, and the two dashboard partials. It is the direct predecessor of this lane and the reason the corrections here are small. **This lane does not reopen its evidence adapters, its migration, or its content store.** It edits the records, settings, prose, and dashboard that #3224 left in their pre-v2 form.
- **PR #3229 (#3183) — ETL-grade pipeline hardening** (MERGED 2026-09-07). Shipped the create-or-bind queue seam at `agent/agent_session_queue.py:233` and `models/dead_letter.py`. Nothing here touches it. It is named only because #3215's body still calls it pending, which task 5 of this plan corrects by comment.
- **#3177 — parent tracking issue and plan.** Decisions Recorded items 4 through 6 (2026-09-07 and 2026-09-08) are the governing authority for this lane: charter v2 governs, lane 2b is its own child issue, and `objective` is deleted outright rather than renamed.
- **#3215 (lane 3), #3216 (lane 4), #3217 (lane 5), #3218 (lane 6).** All open. #3215 blocks on this lane for the vocabulary and settings and separately on #3220 for the fencing lease. The other three read the digest and the settings names once this lands and are otherwise independent.
- **The eight-model schema gate** (`tests/unit/test_improvement_models.py`, `tests/unit/test_agentsession_index_guard_generalized.py:446-490`). Not a prior attempt at this problem, but the prior work that most constrains it: PR #3224 shipped a deliberately strict, structural index gate, and three of this lane's field additions collide with it. See Spike Results.

**Why previous fixes failed** does not apply. There is no prior attempt at charter v2 reconciliation to learn from — #3224 implemented the plan as it stood on 2026-09-07 and the charter changed the same day. This is a decision change propagating forward, not a defect being re-fixed.

## Research

_placeholder_

## Spike Results

_placeholder_

## Data Flow

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Open Questions

_placeholder_
