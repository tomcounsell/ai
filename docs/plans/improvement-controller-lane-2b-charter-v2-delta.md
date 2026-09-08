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

One external question mattered: the resource probe is the only new surface in this lane that touches a credential store, and charter §8 plus the issue's constraints demand it report state without ever emitting a credential or a prefix of one.

**Query:** `1Password CLI "op item list" service account vault titles without revealing secrets exit codes`

**Findings:**

1. **`op item list --format=json` returns metadata only** — title, id, vault, category, timestamps. Field values are never included; retrieving a value requires an explicit `op read` or `op item get --fields`. ([1Password CLI reference](https://developer.1password.com/docs/cli/reference/management-commands/item/), [worked examples](https://msull.github.io/1password-cli-examples.html))
   *How this informs the approach:* the probe's **presence** leg handles no secret at all. `verified` / `absent` for each named resource is decided from titles, so the only code path that ever holds a credential is the optional fingerprint leg. That is a structural reduction of the leak surface, not a discipline one, and it is what makes the "never leaks" test cheap to write.

2. **Older `op` versions could exit 0 on unrecognized server errors**; a recent release corrected this to exit 1. ([1Password CLI release notes](https://app-updates.agilebits.com/product_history/CLI2))
   *How this informs the approach:* the probe must not treat exit 0 as proof. Empty, unparseable, or unexpected-shape output resolves to `unknown`, never to `absent` — reporting "absent" for a resource that exists is the failure mode that would send a builder chasing a resource that was there all along.

3. **`op run` masks secrets in its own output**, and `OP_RUN_NO_MASKING` disables that.
   *How this informs the approach:* this repo runs `op run --no-masking` by design (`CLAUDE.md`, Secrets), so op's masking is explicitly **not** available as a backstop here. The probe's own output discipline is the only guard, which is why its test seeds a fake credential and asserts the string never appears anywhere in the returned structure.

Nothing else in this lane is external. Popoto, `gh`, and the repo's schema gate are all internal and were resolved by reading code (see Spike Results).

## Spike Results

Every assumption below was resolved by reading the code or running the command at the baseline commit, inside this planning pass. No agent was dispatched: each question had a one-command answer, and dispatching would have cost more than it measured. Appetite is Medium, so the cap is four spikes; six are recorded because four of them are single greps that came free with the blast-radius pass.

### spike-1: Does an indexed `digest` on `ImprovementCharter` pass the schema gate?
- **Assumption**: "`digest` can be an `IndexedField`, as the parent plan's task 9 says, because its cardinality is one value per charter version."
- **Method**: code-read (`tests/unit/test_agentsession_index_guard_generalized.py:468-490`)
- **Result**: **False.** `test_improvement_model_indexes_are_low_cardinality` rejects any indexed field whose *name* contains `"digest"` — the marker list is `("_id", "_at", "digest", "version", "revision", "count", "trials")` and the check is a substring test. An indexed `digest` fails that test on the name alone, regardless of its real cardinality. The guard is deliberately name-based so a future field added without touching the vocabulary map still fails.
- **Confidence**: high
- **Impact if false**: none — this is the finding, and it changes the design. See Technical Approach: `digest` is a plain `Field`, matched in Python over the project's charter rows, exactly as the module docstring already prescribes for `version` ("Version lookups go through the recency sort plus a Python filter"). The parent plan's `IndexedField` wording is superseded here with cause.

### spike-2: Does the 11-value `priority_area` vocabulary fit the declared bound?
- **Assumption**: "`priority_area` can be added to `INDEXED_VOCABULARIES` like any other index."
- **Method**: code-read (`tests/unit/test_improvement_models.py:157-165`)
- **Result**: **False.** `test_declared_vocabularies_are_small` asserts `2 <= len(vocabulary) <= 8`. Gap G's vocabulary has eleven values (`inference`, `token_efficiency`, `skills`, `personas`, `cloud_execution`, `research_process`, `evaluators`, `memory`, `orchestration`, `infrastructure`, `other`), so it fails at 11.
- **Confidence**: high
- **Impact if false**: none. The vocabulary is charter-derived — five priorities from §3's list, five eligible means from §3's closing sentence, plus `other` — so shrinking it loses charter fidelity. The gate is amended narrowly instead: a per-field declared maximum, defaulting to 8, with one named exemption carrying its reason.

### spike-3: Does a third index on `ImprovementCase` fit the per-model index bound?
- **Assumption**: "`ImprovementCase` has room for another `IndexedField`."
- **Method**: code-read (`tests/unit/test_agentsession_index_guard_generalized.py:452-466`; `models/improvement_case.py:82-83`)
- **Result**: **False.** The case already indexes `state` and `priority`. `test_improvement_models_are_enumerated_by_the_runtime_derivation` asserts `1 <= len(indexed) <= 2` for every improvement model, so a third index fails.
- **Confidence**: high
- **Impact if false**: none. Same treatment as spike-2: a per-model declared maximum, defaulting to 2, with `ImprovementCase: 3` carrying its reason. The alternative — deleting the writerless `priority` index to make room — is considered and rejected in Rabbit Holes.

### spike-4: What does `gh repo view --json visibility` actually return?
- **Assumption**: "Comparing the result to `"public"` decides eligibility."
- **Method**: prototype (`gh repo view tomcounsell/ai --json visibility`)
- **Result**: **Partly false.** It returns `{"visibility":"PUBLIC"}` — uppercase. A guard comparing against lowercase `"public"` would return `False` for every repository on earth, which is the *fail-closed* direction, so it would never raise an alarm and its "private returns False" test would pass vacuously.
- **Confidence**: high
- **Impact if false**: this is the single most dangerous detail in the lane. The guard normalizes with `.strip().upper()` and compares to `"PUBLIC"`, and the mutation check named in Success Criteria is precisely the one that bites here: flip the comparison and the *public* case must go red.

### spike-5: Is `tools/sdlc_verdict.py::compute_plan_hash` reusable for the charter file?
- **Assumption**: "Gap G's 'normalized `sha256:<hex>` form' means a new hasher has to be written."
- **Method**: code-read (`tools/sdlc_verdict.py:133-157`)
- **Result**: **False, happily.** `compute_plan_hash(path)` takes any path, reads bytes, normalizes CRLF and stray CR to LF, and returns `f"sha256:{hexdigest}"`, returning `None` on any read failure. It is not plan-specific. The charter loader calls it directly.
- **Confidence**: high
- **Impact if false**: a second hasher would have drifted from the first. Reuse also means a `\r\n` checkout of the charter yields the same digest as an `\n` one, which is the property that keeps the seed idempotent across machines.

### spike-6: Which existing tests break on the new dashboard getter?
- **Assumption**: "Adding a goals partial is additive and breaks nothing."
- **Method**: code-read (`tests/unit/test_ui_app.py:721-736`)
- **Result**: **False.** Two tests pin the current surface exactly: `test_dashboard_never_offers_experiment_or_patch_counts` asserts `exported == ["get_coverage", "get_intervention_burden", "get_provisional_assumptions"]` as a literal list, and `test_index_page_links_both_improvement_partials` asserts on "both". Adding `get_goals` and a third `hx-get` fails both.
- **Confidence**: high
- **Impact if false**: none — both are on the Test Impact list as UPDATE. The exact-list assertion is a feature, not an obstacle: it is what stops a future lane from quietly adding an activity-counter tile, and it must stay an exact list after the update.

## Data Flow

Two flows change. Neither crosses a process boundary in this lane; both are read paths that later lanes will write against.

**Flow 1 — charter file to pinned record.**

```
docs/improvement-charter.md  (Tom edits and commits; nothing else writes it)
        │  read_bytes()
        ▼
tools/sdlc_verdict.py::compute_plan_hash   →  "sha256:<hex>"   (CRLF-normalized)
        │
        ├─ frontmatter parse → owner, version, effective
        │        └─ owner != "Tom Counsell"  →  refuse, return None, write nothing
        ▼
models/improvement_charter.py::load_from_file
        │  ImprovementCharter.query.filter(project_key=…)   ← bounded: one row per version
        │  digest match in Python (digest is NOT indexed — spike-1)
        ├─ digest already present  →  return the existing row, write nothing
        └─ digest unseen           →  create() one immutable row:
                                        version, effective, digest, text (ContentField),
                                        state="active", created_at
        ▼
ui/data/improvement.py::get_goals  →  ui/templates/improvement/goals.html
```

The pinned row is the newest by `created_at` within the project partition. Rows are never updated and never deleted; `save()` is never called on an existing charter row.

**Flow 2 — project key to provider eligibility.**

```
project_key  ("valor", "cyndra", …)
        ▼
bridge.routing.load_config()["projects"][project_key]["github"]  →  {"org": …, "repo": …}
        │  missing key, missing github block, missing org or repo  →  False
        ▼
process-local TTL cache  (hit → return cached bool, no subprocess)
        ▼
gh repo view "<org>/<repo>" --json visibility     ← repo passed POSITIONALLY, never via cwd or GH_REPO
        │  non-zero exit, timeout, unparseable JSON, absent key  →  False
        ▼
value.strip().upper() == "PUBLIC"   →  True     (spike-4: the API returns uppercase)
                            otherwise → False
```

Every arrow that is not the happy path lands on `False`. Charter §7 makes "client" the safe default: routing client context to a non-subscription provider is the harm, and refusing to route open-source work merely costs an experiment.

The resource probe has no flow to trace — it is a leaf that shells out, classifies, and returns a dict. Its shape is in Technical Approach.

## Architectural Impact

The lane changes no architecture. It corrects vocabulary inside a structure PR #3224 already established, and it adds two leaf modules with no callers yet.

Three shapes are worth naming because a builder could accidentally change them:

1. **The charter is a file, and the record is a projection of it.** The direction is one-way and stays one-way. `models/improvement_charter.py` reads `docs/improvement-charter.md`; nothing in `models/`, `tools/`, `reflections/`, or `ui/` ever writes that file. A Verification row in the parent plan asserts exactly this by grep, and `docs/improvement-charter.md` is on lane 6's candidate-surface denylist. The loader is a reader with a `create()` on the miss path, not a sync.

2. **`ImprovementCharter` rows are append-only and this lane does not supersede.** The existing module docstring describes an amendment flow that flips the previous row's `state` to `superseded`. Gap G and the issue's acceptance criterion both require that a changed byte "leaves the first untouched", which means `load_from_file` performs no such flip. This is a real divergence and it is deliberate: a loader that mutates a prior row is a `save()` on an immutable record, and the pinned version is already unambiguous from `created_at` within the partition. `state` keeps its two-value vocabulary for a human-driven supersede through `valor-improve` later; the loader simply never uses it. The docstring is corrected to say so.

3. **Two new leaf modules, zero new namespaces.** `tools/improvement_eligibility.py` and `tools/improvement_resources.py` import from `bridge.routing` and the standard library, and are imported by nothing in this lane. They exist so lane 3 can call them. Neither creates a Redis key, a Popoto model, a CLI entry point, or a control namespace.

The one coupling introduced is `tools/` → `bridge.routing.load_config`. That direction already exists throughout `agent/` (`agent/session_completion.py:1171`, `agent/session_executor.py:1935`, `agent/session_revival.py:310`), so it adds no new cycle and no new precedent.

## Appetite

**Medium.**

The unit of work is a delta, not a build: four model files gain seven fields between them and lose one, one settings block is renamed and extended, two small leaf modules are written from a specification the parent plan already fixed, one dashboard partial is added beside two that exist, and two documents are corrected. Every seam it plugs into shipped three weeks — three days, in fact — ago in PR #3224, and the schema gate that constrains it is already written.

What makes it Medium rather than Small is the guard work, not the field work. Three schema-gate assertions have to be amended rather than satisfied (spikes 1 through 3), and each amendment has to stay narrow enough that the gate still bites everywhere else. Every new guard carries a mutation check. That is where the time goes.

**Time-box:** one build session plus one validation session. If the guard amendments start requiring a fourth exemption, or if the charter loader grows a projection, stop and re-scope — both are signs the lane is absorbing work that belongs to #3215.

## Prerequisites

**None.**

This was checked rather than assumed. The issue's Recon Summary records zero pre-requisites, and the two candidates both resolve to "not needed here":

- **#3220 (session execution lease)** gates lane 3's fenced dispatch. Nothing in this lane dispatches, admits, or reserves anything.
- **The improvement control namespace** does not exist and is #3215's to create. The one design decision that could have reached for it — where the eligibility cache lives — is resolved in Technical Approach in a way that does not (a process-local TTL cache, not a Redis key).

Everything this lane builds on is already on `main` at the baseline: the eight `Improvement*` models, `ImprovementSettings`, `verifying_artifact_store`, `ui/data/improvement.py`, the schema-gate tests, and `tools/sdlc_verdict.py::compute_plan_hash`.

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
