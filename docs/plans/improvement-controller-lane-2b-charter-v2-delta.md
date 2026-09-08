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

### Key Elements

- **A charter seed.** `ImprovementCharter` gains `digest`, `effective`, and `text`; a `load_from_file` classmethod creates one immutable row per unseen digest and refuses a file Tom does not own.
- **A case vocabulary that cites the charter.** `priority_area` (indexed), `ranking_rationale`, and `charter_digest` on `ImprovementCase`; `charter_digest` on `ImprovementInvestigation` and `ImprovementRelease`; `objective` deleted.
- **Three budget units in settings.** `daily_paid_inference_usd` (renamed), `weekly_infrastructure_usd`, `budget_day_boundary`, `budget_week_start`; `portfolio_allocation` deleted.
- **Two fail-closed guards.** `is_open_source(project_key)` for charter §7, `probe()` for charter §8.
- **A goals partial.** The §11 record with honest empty states.
- **Corrected prose.** The feature doc, the `.env.example` comment, and the capability matrix.
- **Three narrowly amended schema-gate assertions**, each with a named reason and a mutation check.

### Flow

A builder works outside-in: records first (everything else reads them), then settings (independent), then the two guards (leaves), then the dashboard (reads the records), then the docs (describe all of it), then the follow-through issues and comments.

Nothing in this lane runs on a tick, holds a lease, or writes a Redis key outside the eight existing model keyspaces.

### Technical Approach

#### 1. Charter seed — `models/improvement_charter.py`

Three new fields:

```python
digest = Field(null=True)              # "sha256:<hex>" — PLAIN, not indexed (spike-1)
effective = Field(null=True)           # ISO date string from frontmatter
text = ContentField(store=verifying_artifact_store)
```

`digest` is a plain `Field` and this is deliberate, against the parent plan's `IndexedField` wording. Spike-1 measured that `test_improvement_model_indexes_are_low_cardinality` rejects an indexed field whose name contains `"digest"`, on the name alone. Rather than punch a hole in a name-based guard that exists precisely to catch fields added without thought, the loader matches the digest in Python over the project's charter rows — the same technique the module docstring already prescribes for `version`. At one row per charter version, the scan is single-digit.

`text` reuses the module singleton `verifying_artifact_store` from `models/verifying_artifact_store.py:137`, exactly as `ImprovementExperiment.manifest` and `ImprovementEvaluation.judge_records` already do. No new store instance, no new retention root.

The loader:

```python
@classmethod
def load_from_file(cls, path=Path("docs/improvement-charter.md"), project_key="valor") -> "ImprovementCharter | None":
```

1. `compute_plan_hash(path)` from `tools/sdlc_verdict.py` for the digest. It reads bytes, normalizes CRLF and stray CR to LF, returns `"sha256:<hex>"`, and returns `None` on any read failure (spike-5). No second hasher is written.
2. Parse the YAML frontmatter for `owner`, `version`, `effective`.
3. **Refuse** unless `owner` is exactly `Tom Counsell` — return `None`, write nothing, log at warning. This is the code half of "Tom owns the charter"; the grep-based Verification row is the other half.
4. `ImprovementCharter.query.filter(project_key=project_key)`, match `digest` in Python. On a hit, return the existing row untouched.
5. On a miss, `create()` one row with `version`, `effective`, `digest`, `text`, `state="active"`, `created_at=datetime.now(UTC)`.

**Never** `save()` on an existing row, never flip a prior row to `superseded`, never delete. A changed byte therefore produces a second `active` row and leaves the first exactly as it was, which is what the acceptance criterion requires. The pinned charter is the newest by `created_at` in the partition; a `pinned(project_key)` helper beside the loader returns it so no caller re-derives that rule. The module docstring's amendment paragraph is corrected to describe this, since it currently describes a supersede flow the loader does not perform.

#### 2. Case vocabulary — `models/improvement_case.py`, `_investigation.py`, `_release.py`

```python
PRIORITY_AREAS: tuple[str, ...] = (
    "inference", "token_efficiency", "skills", "personas", "cloud_execution",
    "research_process", "evaluators", "memory", "orchestration", "infrastructure",
    "other",
)
```

Five from charter §3's priority list, five from §3's closing sentence ("Research process improvements, better evaluators, memory, orchestration, and new infrastructure are all eligible means"), plus `other`. `other` is load-bearing: it is what keeps the set from reading as a fixed allocation.

On `ImprovementCase`: `priority_area = IndexedField(default="other")`, `ranking_rationale = Field(null=True)`, `charter_digest = Field(null=True)`. Delete `objective = Field(null=True)` at `:91` and its docstring line at `:75`. `charter_version` (`IntField(default=0)`) **stays** — charter §12 asks for a versioned reference, the digest is the identity and the version is the human-readable name, and the loader writes both.

On `ImprovementInvestigation` and `ImprovementRelease`: `charter_digest = Field(null=True)`, plus its docstring line.

**Migration:** none needed for the deletion. `objective` is a plain `Field` with no index and no writer, which is precisely the contract at `models/agent_session.py:879-883`. The additions are additive. A registered marker migration follows the `_migrate_confirm_improvement_models_readable` precedent at `scripts/update/migrations.py:1384`: read-only, imports the four models, runs one bounded project-scoped query each, writes nothing, and exists so a machine carries a durable record of the schema version that introduced the v2 fields. Register it in `MIGRATIONS` — a defined-but-unregistered function never runs.

#### 3. Schema-gate amendments — the three narrow exemptions

Each is a declared map entry with a reason, not a loosened constant.

- `tests/unit/test_improvement_models.py::test_declared_vocabularies_are_small`: replace the flat `<= 8` with a per-field maximum map defaulting to 8, carrying one entry — `(ImprovementCase, "priority_area"): 11`, reason "charter §3 vocabulary; eleven index sets per project partition, membership reads only". The `>= 2` floor and the duplicate check are untouched.
- `tests/unit/test_agentsession_index_guard_generalized.py::test_improvement_models_are_enumerated_by_the_runtime_derivation`: replace the flat `1 <= len(indexed) <= 2` with a per-model maximum defaulting to 2, carrying one entry — `ImprovementCase: 3`, reason "`state` is lifecycle, `priority` is urgency, `priority_area` is charter §3 classification; the goals partial reads all three".
- `INDEXED_VOCABULARIES[ImprovementCase]` gains `"priority_area": PRIORITY_AREAS`, which is what makes `test_every_indexed_field_has_a_declared_vocabulary` and `test_index_defaults_are_inside_their_vocabulary` cover it. `"other"` is in the tuple, so the default is inside its vocabulary.

`unbounded_markers` and `FORBIDDEN_INDEX_NAMES` are **not** touched, because spike-1 removed the only reason to touch them.

#### 4. Settings — `config/settings.py::ImprovementSettings`

| Field | Change | Default |
|---|---|---|
| `daily_external_llm_usd` | renamed to `daily_paid_inference_usd` | `10.00` |
| `weekly_infrastructure_usd` | new | `50.00` |
| `budget_day_boundary` | new | `"UTC"` |
| `budget_week_start` | new | `"monday"` |
| `portfolio_allocation` | deleted (`:630`, no reader) | — |

Every field keeps a description ending in an `Env: IMPROVEMENT__<KEY>.` sentence and a `PROVISIONAL/TUNABLE.` marker, matching the block's existing shape. The `Env:` sentences are the reader leg that `tests/unit/test_env_declaration_readers.py` walks; only `IMPROVEMENT__ENABLED` is actually declared in `.env.example`, so no declaration changes, but the convention stays intact.

`.env.example:358-359` names "daily external-LLM dollars, portfolio allocation" in the `IMPROVEMENT__ENABLED` comment block. Rewrite that clause to name the three budget units. Missing this is what would fail the parent plan's `git grep portfolio_allocation` confirmation row.

#### 5. `tools/improvement_eligibility.py`

```python
def is_open_source(project_key: str, *, ttl_seconds: int = 900) -> bool
```

Charter §7: any provider for open-source work; subscriptions for client work. **Fails closed to client on every uncertainty.**

- Resolve `projects[project_key]["github"]` through `bridge.routing.load_config()`. Missing project, missing `github` block, missing `org` or `repo` → `False`.
- **Pass the repository positionally**: `gh repo view "<org>/<repo>" --json visibility`. `GH_REPO` is set process-wide by `agent/sdk_client.py` and `gh` reads it before cwd, so a bare `gh repo view` would silently answer about the wrong repository and exit 0. The positional argument overrides it. This is the correctness detail most likely to be lost in implementation.
- Bounded `subprocess.run(..., timeout=10, capture_output=True, text=True)`. Non-zero exit, timeout, empty stdout, unparseable JSON, or an absent `visibility` key → `False`.
- `str(data["visibility"]).strip().upper() == "PUBLIC"` (spike-4: the API returns `"PUBLIC"`, uppercase).
- **Cache in-process**, not in Redis: a module-level `dict[str, tuple[bool, float]]` keyed by `project_key` with a `time.monotonic()` expiry, default 900 seconds, and a `_clear_cache()` for tests. This answers the issue's one open question. A durable cache would mean a Popoto model (a migration, a schema-gate entry, a TTL decision) or a control-namespace key this lane is forbidden to create; a repository's visibility changes on a scale of months; and lane 3 may promote it into the control namespace later if cross-process sharing is ever shown to matter. Cheapest correct thing, no namespace, decision reversible.

#### 6. `tools/improvement_resources.py`

```python
def probe(*, runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None) -> dict[str, dict]
```

One entry per resource charter §8 names: `workspace_personal`, `workspace_work`, `virtual_debit_card`, `cloudflare_account`, `cloudflare_cli`, `vault_write`. Each value is `{"state": "verified" | "absent" | "unknown", "detail": <str>, "fingerprint": <str | None>}`.

- **Presence from titles only.** `op item list --vault m-valor --format json` returns metadata — title, id, vault, category, timestamps — and never a field value (Research finding 1). The whole presence leg therefore handles no credential at all.
- **Fingerprint, never value.** Where a fingerprint is wanted, read the credential, hash it immediately, and return `"sha256:<hex>"`. The plaintext is never returned, never logged, never formatted into a message, and never placed in an argv. `CLAUDE.md`'s "compare by SHA-256 fingerprint" rule, applied.
- **`unknown` is the honest default.** Non-zero exit, timeout, empty output, unparseable JSON, or an unexpected shape → `unknown`, never `absent`. Older `op` builds could exit 0 on unrecognized server errors (Research finding 2), so exit 0 alone proves nothing. Reporting `absent` for a resource that exists would send a builder chasing something that was already there.
- **Never raises.** Every branch returns a dict. A probe that throws inside a controller tick is worse than one that reports `unknown`.
- **No masking backstop.** This repo runs `op run --no-masking` by design, so op's own masking is unavailable (Research finding 3). The probe's output discipline is the only guard, which is why `runner` is injectable: the test seeds a fake credential through it and asserts the string appears nowhere in the returned structure, at any depth.
- **`wrangler` and the vault inventory are expected to report `unknown` on this machine today.** That is a correct result, not a failing test.

#### 7. Dashboard — the goals partial

- `ui/data/improvement.py::get_goals(project_key="valor") -> dict`: the pinned charter's `version`, `effective`, and `digest`; the charter §3 priority list; open `ImprovementCase` rows with `priority_area` and `ranking_rationale`; and the §11 headings (acquired abilities, evaluations, rejected approaches, unresolved assumptions, resource use by budget unit) each with an explicit empty state.
- `ui/templates/improvement/goals.html`, following `coverage.html`'s shape, root element id `improvement-goals`.
- `@app.get("/_partials/improvement/goals/")` in `ui/app.py`, beside the two existing partial routes.
- A third `hx-get` card in `ui/templates/index.html`.
- **Honest empty states.** Until lanes 3 through 6 land, most §11 headings have nothing to render. Each says what would appear there and which lane writes it — "no releases yet (lane 6)" — rather than showing a zero. A zero claims a measurement was taken.
- `test_dashboard_never_offers_experiment_or_patch_counts` keeps its exact-list form with `get_goals` added. It is what stops a future lane from quietly adding an activity counter, so it stays exact.

## Failure Path Test Strategy

### Exception Handling Coverage

Both new guards are fail-closed leaves, so their exception paths are the product, not an edge case.

- `is_open_source`: `subprocess.TimeoutExpired`, `FileNotFoundError` (no `gh` on PATH), `json.JSONDecodeError`, `KeyError` on `visibility`, and any non-zero exit each return `False`. Tested individually, not as one blanket "error" case — a single `except Exception` with one test would let a typo in the JSON key masquerade as a caught timeout.
- `probe`: the same five failure shapes each resolve to `unknown` for the affected resource while every other resource still reports. One dead `op` call must not blank the whole report.
- `load_from_file`: a missing file, an unreadable file, absent frontmatter, malformed YAML, and a wrong `owner` all return `None` and write nothing. `compute_plan_hash` returning `None` short-circuits before any query.
- `get_goals`: an empty namespace, a charter row whose `text` reference is missing from the content store, and a `ContentField` load raising all render the partial with an empty state rather than a 500.

### Empty/Invalid Input Handling

- `is_open_source("")`, an unknown project key, a project with no `github` block, and a `github` block missing `org` or `repo` → `False`.
- `probe()` against a vault with zero matching titles → every resource `absent` or `unknown`, never a raise, never an empty dict.
- `load_from_file` on a charter with `version` absent or non-numeric → the row is still created with the digest and text; the digest is the identity and a malformed version number is a display problem, not a seeding one. `owner` is the only field whose absence refuses.
- `load_from_file` called twice on an unchanged file → one row, the second call returning the first.
- `get_goals` with no charter row → the partial renders with "no charter seeded", not a `None` dereference.

### Error State Rendering

- `goals.html` renders three distinguishable states per §11 heading: seeded content, "nothing yet, written by lane N", and "unavailable" when the underlying read raised. Collapsing the second and third into one blank would let a broken query read as an honest zero — which is Risk 2 of the parent plan, on a smaller surface.
- The charter block renders `version`, `effective`, and the digest in full. A truncated digest cannot be compared against the file by a human, and comparing it is the point.

## Test Impact

- [ ] `tests/unit/test_improvement_models.py` — UPDATE: `INDEXED_VOCABULARIES[ImprovementCase]` gains `"priority_area": PRIORITY_AREAS`; `test_declared_vocabularies_are_small` gains a per-field maximum map (default 8, one entry at 11 with its reason); import `PRIORITY_AREAS` from `models.improvement_case`
- [ ] `tests/unit/test_agentsession_index_guard_generalized.py::test_improvement_models_are_enumerated_by_the_runtime_derivation` — UPDATE: per-model index maximum (default 2, `ImprovementCase: 3` with its reason). `test_improvement_model_indexes_are_low_cardinality` is deliberately **not** changed — spike-1 removed the reason to
- [ ] `tests/unit/test_ui_app.py::test_dashboard_never_offers_experiment_or_patch_counts` — UPDATE: the exact list gains `"get_goals"`; it stays an exact list
- [ ] `tests/unit/test_ui_app.py::test_index_page_links_both_improvement_partials` — REPLACE: rename to `..._links_all_improvement_partials` and assert the third `hx-get` alongside the two existing ones
- [ ] `tests/unit/test_settings.py` — UPDATE: `ImprovementSettings` currently has no coverage there at all (`git grep ImprovementSettings -- tests/` returns only `test_improvement_evidence.py`). Add the defaults case — `daily_paid_inference_usd == 10.00`, `weekly_infrastructure_usd == 50.00`, `budget_day_boundary == "UTC"`, `budget_week_start == "monday"` — and an absence case asserting `daily_external_llm_usd`, `portfolio_allocation`, and `daily_question_ceiling` are not fields on the model
- [ ] `tests/unit/test_improvement_charter.py` — CREATE: load twice creates one row; a changed byte creates a second and leaves the first untouched; a file without `owner: Tom Counsell` is refused and writes nothing; a CRLF copy digests identically to an LF copy; `pinned()` returns the newest row
- [ ] `tests/unit/test_improvement_eligibility.py` — CREATE: public `True`; private `False`; missing `github` block `False`; missing `org`/`repo` `False`; non-zero `gh` exit `False`; timeout `False`; unparseable JSON `False`; cache hit issues no subprocess
- [ ] `tests/unit/test_improvement_resources.py` — CREATE: every resource classified; a seeded fake credential never appears anywhere in the returned structure at any depth; a non-zero `op` exit yields `unknown` and not `absent`; one failing resource does not blank the others; `probe()` never raises
- [ ] `tests/unit/test_env_declaration_readers.py` — NO CHANGE, but must be re-run: only `IMPROVEMENT__ENABLED` is declared in `.env.example` and the rename touches no declaration. Verified at plan time; re-verified by the Verification table
- [ ] `tests/unit/test_reflection_register.py`, `tests/unit/test_improvement_evidence.py`, `tests/unit/test_length_safe_content_store.py` — NO CHANGE: this lane touches neither the collection tick, the evidence adapters, nor the content store. Listed so the validator confirms they still pass rather than assuming it

## Rabbit Holes

- **Deleting `ImprovementCase.priority` to make room for `priority_area`.** Tempting, since `priority` has no writer either and the vocabulary bound would then need no exemption. Rejected: `priority` is urgency and `priority_area` is charter §3 classification; they are orthogonal and lane 3 writes both. Removing a field the parent plan never sanctioned removing, to avoid a two-line test amendment, is the wrong trade. If a critique disagrees, it is a one-line change either way.
- **Making `digest` indexed anyway by adding an exemption to `unbounded_markers`.** The guard is name-based on purpose, so that a field added later without thought still fails. Punching a hole in it to save a Python-side match over single-digit rows spends a durable guard on a non-problem.
- **Building the paid-inference meter.** `daily_paid_inference_usd` is a number in a settings file after this lane; nothing reads it and nothing settles a dollar. The meter is `tools/paid_inference_meter.py` and belongs to #3215. This lane's only obligation is to stop the feature doc from claiming metering exists.
- **Writing the ranking snapshot.** Gap G's "the ordered list is a durable artifact" is lane 5 (#3217). The issue's Dropped bucket names it explicitly. `ranking_rationale` is a free-text field on a case; it is not a snapshot.
- **Creating the improvement control namespace for the eligibility cache.** Forbidden by the issue and resolved by making the cache process-local instead. If a builder finds themselves designing a Redis key schema, they have left the lane.
- **Adding a `downstream_sign` tag to replace `objective`.** Considered upstream and rejected by Tom (issue Dropped bucket). `objective` is deleted, full stop.
- **Rewriting the charter model's supersede semantics into a full amendment flow.** The loader appends and never supersedes; the docstring is corrected to match. Building the human-driven supersede path — `valor-improve` writing `state="superseded"` — is lane 3's, and doing it here would mean designing a CLI this lane may not create.
- **Editing `docs/improvement-charter.md`.** Tom owns it. Not a rabbit hole so much as a wall; it is also a No-Go and a Verification row.
- **Chasing every `git grep objective` hit.** Two of the four files that match are ordinary English in unrelated modules (`tools/memory_eval/query_set.py:9`, `tools/valor_session.py:1141`). Touching them is scope creep with a rename's disguise.
- **Backfilling `charter_digest` onto existing rows.** There are none. `ImprovementCase` has no writer on `main`, so the tables are empty and a data migration would migrate nothing.

## Risks

### Risk 1: The eligibility guard fails open and client context reaches a foreign provider

The worst outcome this lane can enable. `is_open_source` returning `True` by mistake is how private client code and its context reach a non-subscription provider (charter §7, parent plan Risk 8).

**Mitigation:** every branch that is not an explicit uppercase `"PUBLIC"` returns `False` — missing project, missing `github`, missing `org`/`repo`, non-zero exit, timeout, unparseable JSON, absent key, unexpected value. Each of those is a separate test rather than one blanket case. The mutation check named in Success Criteria flips the default to `True` and requires the private, missing-field, and `gh`-failure tests to go red together; if only one bites, the others were passing vacuously.

### Risk 2: The guard answers about the wrong repository and exits 0

`GH_REPO` is set process-wide by `agent/sdk_client.py`, and `gh` reads it before cwd. A bare `gh repo view --json visibility` inside a session would report on whatever `GH_REPO` names, exit 0, and look entirely healthy — the failure has no symptom.

**Mitigation:** the repository is passed positionally, `gh repo view "<org>/<repo>"`, which overrides the environment. A test sets `GH_REPO` to a decoy public repository, asks about a private project, and asserts `False`. Without that test the defect is undetectable by reading the code, because the correct and incorrect versions differ by one argument.

### Risk 3: The resource probe emits a credential

Charter §8 forbids exposing secrets in logs, messages, or committed files, and this repo runs `op run --no-masking`, so op's own masking is not a backstop.

**Mitigation:** the presence leg reads `op item list --format json`, which returns metadata only and never a field value, so most of the module cannot leak by construction. The fingerprint leg hashes immediately and returns `"sha256:<hex>"`; the plaintext never enters a return value, a log line, or an argv. The `runner` parameter is injectable so the test seeds a distinctive fake credential and asserts the string appears nowhere in the returned structure at any depth — a recursive scan, not a top-level key check.

### Risk 4: `unknown` gets reported as `absent`

A resource reported `absent` when it exists sends the next lane to acquire something already sitting in the vault, and older `op` builds could exit 0 on unrecognized server errors, so exit 0 proves nothing on its own.

**Mitigation:** `absent` is written only on a successful, parseable listing that does not contain the title. Every other shape — non-zero exit, timeout, empty stdout, unparseable JSON, unexpected structure — is `unknown`. A test drives each shape and asserts the classification.

### Risk 5: A schema-gate exemption quietly becomes a loophole

Three assertions are amended. A flat constant raised from 8 to 12, or from 2 to 3, would stop the gate biting for every model at once, and nothing would announce it.

**Mitigation:** each amendment is a per-key map with an explicit default and exactly one entry carrying its reason in a comment. A second exemption requires a second named line, which a reviewer sees. The mutation check adds a fourth indexed field to another improvement model and requires the gate to fail.

### Risk 6: The loader mutates an existing charter row

Charter §12 forbids retroactively rewriting evidence. A loader that flips a prior row to `superseded`, or updates a row in place on a digest change, destroys the lineage that makes an old release auditable — and the module's current docstring describes exactly that flow.

**Mitigation:** `load_from_file` calls `create()` or returns an existing row, and nothing else. No `save()`, no `delete()`, no `state` write. A test corrupts one byte, reloads, and asserts two rows exist with the first's fields byte-identical to before. The docstring is corrected in the same change so the prose stops describing behavior the code does not have.

### Risk 7: The goals partial shows a zero where it should show "not measured"

Charter §11 warns against treating an absence of detection as a result. Six of the §11 headings have no writer until lanes 3 through 6 land, so this lane ships a surface that is mostly empty.

**Mitigation:** every heading renders one of three distinguishable states — content, "nothing yet, written by lane N", or "unavailable" when the read failed. No count renders as `0` unless a query actually ran and returned nothing. `test_ui_app.py` asserts the empty-namespace render contains the lane attribution string and no bare zero.

## Race Conditions

Nothing in this lane runs concurrently. There is no tick, no lease, no reservation, no shared mutable state, and no cross-process handoff — the two guards are synchronous leaves, the loader runs on demand, and the dashboard partial is a read. The one timing question worth writing down is the loader's, because a later lane will call it from a tick.

### Race 1: Two callers seed the same charter digest simultaneously

`load_from_file` reads (query for the digest) and then writes (`create`). Two callers arriving between the read and the write both miss and both create, producing two rows for one digest.

**Why it is tolerable here, and what makes it safe later:** this lane has exactly one caller — a test — so the race cannot occur on `main` as shipped. When lane 3's controller tick calls the loader on every run, the tick is bounded by `max_concurrent_research_sessions = 1`, so it is single-flighted by the concurrency unit that already exists.

**What this lane does about it anyway:** duplicate rows are harmless by construction, because rows are immutable and identical for a given digest, and `pinned()` resolves by newest `created_at` regardless of how many rows share a digest. Making the seed a compare-and-set would need an atomic primitive in the control namespace this lane is forbidden to create. The behavior is documented on `load_from_file` so lane 3 inherits the reasoning rather than rediscovering it.

### Race 2: The charter file changes while a session is mid-decision

Tom commits an amended charter while an action admitted under the previous digest is still running.

**Not this lane's to solve, and named so it is not accidentally solved here.** Charter §12 states the rule — actions already admitted complete under the digest they carry, a new digest pins for actions admitted after it — and enforcing it requires the admission path, which is #3215's. This lane makes the rule *expressible* by putting `charter_digest` on the case, the investigation, and the release. It enforces nothing.

## No-Gos (Out of Scope)

- [EXTERNAL] **Editing `docs/improvement-charter.md`.** Tom owns it and only Tom edits it. The builder reads it and never writes it. Asserted by a Verification row (`grep -rn "improvement-charter.md" models/ tools/ reflections/ ui/` finds no write call) and by lane 6's candidate-surface denylist.
- [ORDERED] **The improvement control namespace.** `improve:{project_key}:*`, its Lua transition, and every reservation key are #3215's. This lane creates no Redis key outside the eight existing model keyspaces, which is why the eligibility cache is process-local.
- [ORDERED] **The `valor-improve` CLI.** No `[project.scripts]` entry, no subcommand, no argparse. `propose`, `budget`, `propose-amendment`, `ranking`, `pause`, `resume`, and `doctor` are all #3215's or #3217's.
- [ORDERED] **`tools/paid_inference_meter.py` and any dollar settlement.** This lane adds two dollar *settings* and removes a false metering claim from a document. It meters nothing. #3215.
- [ORDERED] **`tools/vault_write.py`.** The one sanctioned `op item create` wrapper is #3215's, along with its `resource_acquired` evidence row. This lane's probe reads and classifies; it never writes to the vault.
- [ORDERED] **The ranking snapshot.** The durable ordered artifact is #3217's (issue Dropped bucket). `ranking_rationale` is free text on a case and is not a snapshot.
- [ORDERED] **The `serves_charter` judge**, blinding, and the corrected statistics. #3216.
- [ORDERED] **Release promotion, rollback drills, and the candidate manifest denylist.** #3218.
- [HUMAN] **The `upvote` label.** Human-owned, unchanged, untouched. Nothing in this lane reads it, writes it, or reasons about it.
- [HUMAN] **Adding a question path to Tom.** Charter §9 permits exactly one message class, the amendment request, and it lives in #3215's `tools/improvement_amendment.py`. No poll, no `AskUserQuestion`, no `investigation_id`. The parent plan's no-routine-question anti-criterion row covers this lane too.
- **Reopening PR #3224's evidence adapters, its collection-tick registration, its `TaskTypeProfile` retirement, or its content store.** All shipped and all correct; they are not pre-v2 in any way this charter touches.
- **Deleting `ImprovementCase.priority` or any other field the parent plan did not sanction removing.** `objective` is the only deletion.
- **Backfill migrations.** No `Improvement*` model has a writer on `main`, so every table is empty. The registered migration is a read-only marker.

## Update System

One change, and it is the marker migration.

- **`scripts/update/migrations.py`** gains `_migrate_confirm_improvement_v2_fields(project_dir)` and its `MIGRATIONS` registration. It follows `_migrate_confirm_improvement_models_readable` (`:1384`) exactly: read-only, imports `ImprovementCharter`, `ImprovementCase`, `ImprovementInvestigation`, and `ImprovementRelease`, runs one bounded project-scoped query per model to prove the keyspace resolves under the new fields, writes nothing, returns `None` on success and an error string otherwise. Idempotent by construction and recorded once in `data/migrations_completed.json`. **Registration is the whole point** — a defined-but-unregistered function never runs, and without a durable marker no machine carries a record of the schema version that introduced the v2 fields for a later subtractive migration to reason from.
- **No new dependency.** Everything imports from the standard library, `popoto`, or existing repo modules.
- **No new config file, no new `.env` key, no new vault entry.** `.env.example` changes by one comment clause and declares nothing new.
- **No service restart.** Nothing in `bridge/`, `worker/`, or `agent/` changes, so `./scripts/valor-service.sh restart` is not required by this lane.
- **No reflection registration.** The collection tick shipped in PR #3224 and is untouched; `scripts/update/reflection_register.py` and `scripts/update/run.py` are not edited.
- **Existing installations** need nothing beyond a normal `/update`: the models gain nullable fields, the deleted `objective` has no rows, and the renamed setting has no `.env.example` declaration to migrate.

## Agent Integration

**No agent integration in this lane, and that is a decision rather than an omission.**

The agent reaches new functionality through a `[project.scripts]` CLI entry point it invokes with Bash, or through a direct import from the bridge. This lane adds neither, deliberately:

- **No CLI entry point.** `valor-improve` and all of its subcommands are #3215's, and creating one here would violate this lane's No-Gos. `pyproject.toml` is not edited.
- **No bridge import.** Nothing in `bridge/` calls `load_from_file`, `is_open_source`, or `probe`. The bridge is I/O only and this lane changes no message path.
- **`tools/improvement_eligibility.py` and `tools/improvement_resources.py` are invisible to the agent by design.** They are libraries written against a specification lane 3 consumes. Wiring them to a CLI before a caller exists would ship a surface with no behavior behind it.
- **The one agent-visible change is the dashboard**, which is a human surface, not an agent one: `GET /_partials/improvement/goals/` renders in the browser and is exercised by `tests/unit/test_ui_app.py` through the test client, not by the agent.

The integration test that matters here is therefore the UI partial test, not a CLI invocation test. When #3215 adds `valor-improve`, its plan owns the `[project.scripts]` entry and the agent-invocation test for all of these modules at once.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/improvement-controller.md`: replace the `portfolio_allocation` row at `:166` with the charter §3 ranking rule and `priority_area`; replace the "There is no `daily_question_ceiling`. The ceiling is zero..." paragraph at `:168-171` with charter §9's actual rule (no routine research questions, plus the amendment-request path that lane 3 delivers); rename the `daily_external_llm_usd` row at `:165` and add rows for `weekly_infrastructure_usd`, `budget_day_boundary`, and `budget_week_start`; remove any claim that dollars are metered today
- [ ] Add a **Charter** section to `docs/features/improvement-controller.md` naming `docs/improvement-charter.md` as the north star, the digest seed and its immutability, the `owner: Tom Counsell` refusal, the pinning rule, and the candidate-surface denylist. Link the charter from the top of the file
- [ ] Link `docs/improvement-charter.md` from `docs/README.md`
- [ ] Document the goals partial in `docs/features/improvement-controller.md` beside the two existing partials, including which §11 headings are empty and which lane fills each
- [ ] Add a **Lane 2b** section to `docs/plans/critiques/recursive-self-improvement-capability-matrix.md` marking each component of this lane implemented / deployed / measured / unknown, and record the resource probe's actual verified set as run on this machine
- [ ] No new file in `docs/features/`: this lane corrects an existing feature doc rather than describing a new feature. `docs/features/README.md` needs no new row

### External Documentation Site

Not applicable. Nothing here is user-facing outside this repository.

### Inline Documentation

- [ ] `models/improvement_charter.py` module docstring: add `digest`, `effective`, and `text` to the `Fields:` block, and **correct the amendment paragraph** — it currently describes flipping a prior row to `superseded`, which `load_from_file` deliberately does not do. Keep the `TTL decision` phrase; `test_ttl_decision_is_recorded_in_the_docstring` greps for it
- [ ] `models/improvement_case.py` docstring: add `priority_area`, `ranking_rationale`, `charter_digest`; delete the `objective` line at `:75`
- [ ] `models/improvement_investigation.py` and `models/improvement_release.py` docstrings: add `charter_digest`
- [ ] `tools/improvement_eligibility.py`: module docstring stating charter §7, the fail-closed contract, why the repository is passed positionally rather than through `GH_REPO`, and why the cache is process-local
- [ ] `tools/improvement_resources.py`: module docstring stating charter §8, the three-state vocabulary, why `unknown` is the default on any uncertainty, and the never-emit-a-credential rule with the fingerprint form
- [ ] `.env.example:358-359`: rewrite the "daily external-LLM dollars, portfolio allocation" clause to name the three budget units

## Success Criteria

Each criterion below is a row in the Verification table or a named test. None of them can pass on `main` at the baseline, which was checked rather than assumed.

- [ ] `ImprovementCharter` rows carry `digest`, `effective`, and `text`; loading `docs/improvement-charter.md` twice creates one row; a changed byte creates a second row and leaves the first byte-identical; a file without `owner: Tom Counsell` is refused and writes nothing; a CRLF copy digests identically to an LF copy (`tests/unit/test_improvement_charter.py`)
- [ ] `ImprovementCase` has `priority_area` (indexed, defaulting to `other`), `ranking_rationale`, and `charter_digest`, and has no `objective`; `ImprovementInvestigation` and `ImprovementRelease` have `charter_digest`; both schema-gate suites pass with the three narrow exemptions in place
- [ ] `ImprovementSettings` exposes `daily_paid_inference_usd=10.00`, `weekly_infrastructure_usd=50.00`, `budget_day_boundary="UTC"`, `budget_week_start="monday"`, and has no `daily_external_llm_usd`, `portfolio_allocation`, or `daily_question_ceiling`; `tests/unit/test_env_declaration_readers.py` still passes
- [ ] `tools/improvement_eligibility.py::is_open_source` returns `True` for a public repository and `False` for private, missing `github` block, missing `org`/`repo`, non-zero `gh` exit, timeout, and unparseable JSON; a decoy `GH_REPO` pointing at a public repository does not make a private project report open source (`tests/unit/test_improvement_eligibility.py`)
- [ ] `tools/improvement_resources.py::probe` reports `unknown` when `op` cannot authenticate, reports `unknown` rather than `absent` on any unparseable result, never raises, and its output contains no credential bytes when fed a seeded fake through the injected runner (`tests/unit/test_improvement_resources.py`)
- [ ] The goals partial renders with an empty namespace and with a seeded charter row, and its empty §11 headings name the lane that will fill each rather than showing a zero (`scripts/pytest-clean.sh tests/unit/test_ui_app.py -q -k improvement`)
- [ ] `docs/features/improvement-controller.md` contains no `portfolio_allocation` and no "ceiling is zero", and does contain the three budget units, charter §9's rule with the amendment path, and a Charter section; `.env.example` names the three budget units; `docs/plans/critiques/recursive-self-improvement-capability-matrix.md` has a Lane 2b section
- [ ] Every parent-plan Verification row that passed for PR #3224 still passes, and the v2 rows pass: charter seed round-trip, eligibility guard, resource probe, budget windows, both dollar settings, charter version 2
- [ ] `git grep -n 'portfolio_allocation'` returns matches only in `docs/plans/` and `git grep -n '\bobjective\b' -- '*.py'` returns matches only in `tools/memory_eval/query_set.py` and `tools/valor_session.py` (ordinary English, out of scope)

**Mutation checks — each guard is proven to bite, and re-measured after every review round:**

- [ ] Flip `is_open_source`'s failure default from `False` to `True`: the private, missing-field, `gh`-failure, timeout, and unparseable-JSON tests all go red. If fewer than all five bite, the rest were passing vacuously
- [ ] Change `is_open_source`'s comparison from `"PUBLIC"` to `"public"`: the public-repository test goes red. This is the spike-4 defect and it is invisible without this check
- [ ] Remove the positional repository argument from the `gh` call and set a decoy `GH_REPO`: the wrong-repository test goes red
- [ ] Corrupt one byte of the charter fixture: the loader produces a second digest and a second row, and the first row's fields are unchanged
- [ ] Seed a distinctive fake credential through the probe's injected runner: it appears nowhere in the returned structure at any depth. Then remove the hashing step and confirm the test goes red
- [ ] Make `probe` classify an unparseable `op` result as `absent` instead of `unknown`: its test goes red
- [ ] Add a fourth `IndexedField` to any improvement model: the per-model index-maximum assertion goes red, proving the exemption did not become a loophole
- [ ] Remove the `owner` check from `load_from_file`: the refusal test goes red

**Follow-through:**

- [ ] The lane 7 child issue exists, references `Refs #3177`, and carries charter §2, the resource probe's measured verified set, and Gap D's unit-3 rules
- [ ] #3215, #3216, #3217, and #3218 each carry a comment naming the v2 items they inherit from this lane

## Team Orchestration

### Team Members

| Member | Agent Type | Owns | Parallel with |
|---|---|---|---|
| `delta-builder` | builder | Tasks 1 through 4 and task 6 | none — the tasks share four model files and two test files |
| `lane-validator` | validator | Task 5 | none — runs after the build lands |

**One builder, serialized.** The temptation is to fan out records, settings, guards, and dashboard as four parallel lanes, since they look independent. They are not: tasks 1, 2, and 3 all edit `tests/unit/test_improvement_models.py`, tasks 1 and 4 both read `models/improvement_charter.py`, and two builders sharing a worktree have livelocked on this repository before. The lane is Medium; the serialization costs a session and buys a clean history.

If the lane is ever split, the only safe seam is `tools/improvement_eligibility.py` plus `tools/improvement_resources.py` plus their two new test files, which touch nothing else in the repository. Everything upstream of that seam shares files.

### Available Agent Types

- **builder** — writes code and tests in the lane worktree, commits in small logical checkpoints
- **validator** — runs the Verification table and every mutation check, reports pass/fail per row without editing code

## Step by Step Tasks

### 1. Records: charter seed, case vocabulary, and the schema-gate exemptions
- **Task ID**: build-records-v2
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_charter.py` (create), `tests/unit/test_improvement_models.py`, `tests/unit/test_agentsession_index_guard_generalized.py`
- **Informed By**: spikes 1, 2, 3, 5; Gap G; the issue's acceptance criteria
- **Assigned To**: delta-builder
- **Agent Type**: builder
- **Parallel**: false

- Add `digest` (plain `Field`, **not** `IndexedField` — spike-1), `effective` (`Field`), and `text` (`ContentField(store=verifying_artifact_store)`) to `models/improvement_charter.py`
- Add `load_from_file(path=Path("docs/improvement-charter.md"), project_key="valor")` as a classmethod: `compute_plan_hash` from `tools/sdlc_verdict.py` for the digest, frontmatter parse for `owner`/`version`/`effective`, refuse unless `owner == "Tom Counsell"`, match the digest in Python over `query.filter(project_key=...)`, `create()` on a miss and return the existing row on a hit. Never `save()`, never flip `state`, never delete
- Add a `pinned(project_key)` helper returning the newest charter row by `created_at`, so no caller re-derives the pinning rule
- Correct the module docstring: add the three fields to the `Fields:` block, rewrite the amendment paragraph to describe append-only loading, keep the literal phrase `TTL decision`
- Add `PRIORITY_AREAS` (eleven values) to `models/improvement_case.py`; add `priority_area = IndexedField(default="other")`, `ranking_rationale = Field(null=True)`, `charter_digest = Field(null=True)`; delete `objective` at `:91` and its docstring line at `:75`; keep `charter_version`
- Add `charter_digest = Field(null=True)` and its docstring line to `models/improvement_investigation.py` and `models/improvement_release.py`
- Amend the three schema-gate assertions as per-key maps with defaults and one reasoned entry each (Technical Approach §3). Do **not** touch `unbounded_markers` or `FORBIDDEN_INDEX_NAMES`
- Create `tests/unit/test_improvement_charter.py` covering: load twice → one row; changed byte → two rows, first untouched; missing `owner` → refused, zero rows written; CRLF and LF copies digest identically; `pinned()` returns the newest
- Add `_migrate_confirm_improvement_v2_fields` to `scripts/update/migrations.py` following `_migrate_confirm_improvement_models_readable` (`:1384`), and **register it in `MIGRATIONS`**

### 2. Settings: three budget units
- **Task ID**: build-settings-v2
- **Depends On**: none
- **Validates**: `tests/unit/test_settings.py`, `tests/unit/test_env_declaration_readers.py`
- **Assigned To**: delta-builder
- **Agent Type**: builder
- **Parallel**: false

- In `config/settings.py::ImprovementSettings`: rename `daily_external_llm_usd` (`:618`) to `daily_paid_inference_usd`, keeping `default=10.00` and `ge=0.0`; add `weekly_infrastructure_usd=50.00`, `budget_day_boundary="UTC"`, `budget_week_start="monday"`; delete `portfolio_allocation` (`:630`) and its whole description block
- Every description keeps the block's shape: what the field governs, a `PROVISIONAL/TUNABLE.` marker, and a closing `Env: IMPROVEMENT__<KEY>.` sentence
- Rewrite the `.env.example:358-359` clause naming "daily external-LLM dollars, portfolio allocation" to name the three budget units instead
- Add `ImprovementSettings` coverage to `tests/unit/test_settings.py` — it has none today: a defaults case for the four values and an absence case asserting `daily_external_llm_usd`, `portfolio_allocation`, and `daily_question_ceiling` are not model fields

### 3. Guards: eligibility and resource probe
- **Task ID**: build-guards
- **Depends On**: none
- **Validates**: `tests/unit/test_improvement_eligibility.py` (create), `tests/unit/test_improvement_resources.py` (create)
- **Informed By**: spike-4; Research findings 1 through 3; Risks 1 through 4
- **Assigned To**: delta-builder
- **Agent Type**: builder
- **Parallel**: false (safe seam if the lane is ever split)

- Create `tools/improvement_eligibility.py::is_open_source(project_key, *, ttl_seconds=900) -> bool` per Technical Approach §5. **The repository is passed positionally to `gh`**, never resolved through cwd or `GH_REPO`. Compare `.strip().upper()` against `"PUBLIC"`. Every uncertainty returns `False`. Process-local TTL cache with a `_clear_cache()` for tests
- Create `tests/unit/test_improvement_eligibility.py`: public, private, missing `github`, missing `org`/`repo`, non-zero exit, timeout, unparseable JSON, absent `visibility` key, decoy `GH_REPO`, cache hit issues no subprocess
- Create `tools/improvement_resources.py::probe(*, runner=None) -> dict` per Technical Approach §6, covering the six charter §8 resources with `verified` / `absent` / `unknown`. Presence from `op item list --format json` titles only. Fingerprints as `"sha256:<hex>"`, hashed on read, plaintext never returned, logged, or placed in an argv. `unknown` on every unparseable or non-zero result. Never raises
- Create `tests/unit/test_improvement_resources.py`: every resource classified; a seeded fake credential absent from the returned structure at any depth (recursive scan); non-zero exit → `unknown`, not `absent`; one failing resource does not blank the others; `probe()` never raises

### 4. Dashboard: the goals partial
- **Task ID**: build-goals-partial
- **Depends On**: build-records-v2
- **Validates**: `tests/unit/test_ui_app.py`
- **Informed By**: spike-6; charter §11; Risk 7
- **Assigned To**: delta-builder
- **Agent Type**: builder
- **Parallel**: false

- Add `ui/data/improvement.py::get_goals(project_key="valor") -> dict`: pinned charter version, effective date and full digest; the charter §3 priority list; open cases with `priority_area` and `ranking_rationale`; the §11 headings each with an explicit empty state naming the lane that fills it
- Add `ui/templates/improvement/goals.html` following `coverage.html`'s shape, root id `improvement-goals`, three distinguishable states per heading (content / not yet, lane N / unavailable). No bare zeros
- Add `@app.get("/_partials/improvement/goals/")` to `ui/app.py` beside the two existing partial routes, and a third `hx-get` card to `ui/templates/index.html`
- Update `test_dashboard_never_offers_experiment_or_patch_counts` to the four-item exact list, and replace `test_index_page_links_both_improvement_partials` with an all-three assertion
- Add empty-namespace and seeded-charter render cases

### 5. Validate lane 2b
- **Task ID**: validate-lane-2b
- **Depends On**: build-records-v2, build-settings-v2, build-guards, build-goals-partial
- **Assigned To**: lane-validator
- **Agent Type**: validator
- **Parallel**: false

- Run every row of the Verification table below and report pass/fail per row. The v2 rows that fail on `main` today must pass; every row that passed for PR #3224 must still pass
- Run all eight mutation checks from Success Criteria and report which assertion went red for each. A mutation that changes nothing is a finding, not a pass
- Confirm `objective` and `portfolio_allocation` are gone, allowing for the two out-of-scope English matches recorded in the Freshness Check
- Run `scripts/pytest-clean.sh` (never bare `pytest`) for every test invocation

### 6. Follow-through: lane 7 and the sibling comments
- **Task ID**: file-lane-7
- **Depends On**: validate-lane-2b
- **Assigned To**: delta-builder
- **Agent Type**: builder
- **Parallel**: false

- File the lane 7 child issue through `/do-issue` with `Refs #3177`: cloud execution capacity, charter §2 verbatim, the resource probe's **measured** verified set from task 5, and Gap D's unit-3 rules
- Comment on #3215: the #3183 half of its dependency is satisfied on `main` (`agent/agent_session_queue.py:233`, `models/dead_letter.py`); the remaining blocker is #3220; its `objective` vocabulary is replaced by `priority_area` / `ranking_rationale` / `charter_digest`; the settings it reads are now `daily_paid_inference_usd` and `weekly_infrastructure_usd`; it owns `tools/paid_inference_meter.py`, `tools/vault_write.py`, `valor-improve budget`, and `valor-improve propose-amendment`; the eligibility cache is process-local and may be promoted into the control namespace if cross-process sharing proves necessary
- Comment on #3216, #3217, #3218 naming the v2 items each inherits: the `serves_charter` judge; the §3-ranked first experiment, the §5 skill-acquisition cycle, the ranking snapshot, the assumption digest, the no-promises detector; merge authority through the pipeline and the evaluator-replacement release type

## Verification

Every anti-criterion row was measured against the working tree at the baseline so none of them can pass vacuously, and the baseline count is recorded beside each. Counting rows pipe through `wc -l` and use the `match count == 0` form, which `agent/verification_parser.py:407` treats as a failure on empty stdout — so a row whose target directory vanished fails closed instead of passing.

`scripts/pytest-clean.sh` is used for every test invocation. Bare `pytest` is never used.

| Check | Command | Expected |
|-------|---------|----------|
| Charter file exists and is human-owned | `grep -c "^owner: Tom Counsell" docs/improvement-charter.md` | output contains 1 |
| Charter pinned is version 2 | `grep -c "^version: 2" docs/improvement-charter.md` | output contains 1 |
| Charter seeds by digest and round-trips | `scripts/pytest-clean.sh tests/unit/test_improvement_charter.py -q` | exit code 0 |
| Charter record carries the three new fields | `.venv/bin/python -c "from models import ImprovementCharter as C; f=set(C._meta.fields); print(len({'digest','effective','text'} - f))"` | output contains 0 |
| Charter digest is NOT indexed (spike-1) | `.venv/bin/python -c "from popoto import IndexedField; from models import ImprovementCharter as C; print(isinstance(C._meta.fields['digest'], IndexedField))"` | output contains False |
| Case carries the v2 vocabulary | `.venv/bin/python -c "from models import ImprovementCase as C; f=set(C._meta.fields); print(len({'priority_area','ranking_rationale','charter_digest'} - f))"` | output contains 0 |
| `objective` is gone from the case (baseline: present at `models/improvement_case.py:91`) | `.venv/bin/python -c "from models import ImprovementCase as C; print('objective' in C._meta.fields)"` | output contains False |
| `priority_area` is indexed and defaults inside its vocabulary | `.venv/bin/python -c "from popoto import IndexedField; from models.improvement_case import PRIORITY_AREAS; from models import ImprovementCase as C; f=C._meta.fields['priority_area']; print(isinstance(f, IndexedField) and f.default in PRIORITY_AREAS and len(PRIORITY_AREAS)==11)"` | output contains True |
| Investigation and release carry the digest | `.venv/bin/python -c "from models import ImprovementInvestigation as I, ImprovementRelease as R; print('charter_digest' in I._meta.fields and 'charter_digest' in R._meta.fields)"` | output contains True |
| Schema gate still bites with the exemptions in place | `scripts/pytest-clean.sh tests/unit/test_agentsession_index_guard_generalized.py tests/unit/test_improvement_models.py -q` | exit code 0 |
| Eight flat models still exported (a missing target cannot masquerade as a pass) | `.venv/bin/python -c "import models as m; print(len([n for n in m.__all__ if n.startswith('Improvement')]))"` | output > 7 |
| Daily paid-inference dollars set | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(int(S().daily_paid_inference_usd))"` | output contains 10 |
| Weekly infrastructure dollars set | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(int(S().weekly_infrastructure_usd))"` | output contains 50 |
| Budget windows disclosed | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(S().budget_day_boundary, S().budget_week_start)"` | output contains UTC monday |
| Withdrawn settings are gone (baseline: both present, `config/settings.py:618` and `:630`) | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; f=set(S.model_fields); print(len({'daily_external_llm_usd','portfolio_allocation','daily_question_ceiling'} & f))"` | output contains 0 |
| Concurrency bound is still one lane | `.venv/bin/python -c "from config.settings import ImprovementSettings as S; print(S().max_concurrent_research_sessions)"` | output contains 1 |
| Settings coverage exists | `scripts/pytest-clean.sh tests/unit/test_settings.py -q` | exit code 0 |
| `.env.example` declaration has a reader | `scripts/pytest-clean.sh tests/unit/test_env_declaration_readers.py -q` | exit code 0 |
| Eligibility guard fails closed | `scripts/pytest-clean.sh tests/unit/test_improvement_eligibility.py -q` | exit code 0 |
| Eligibility guard passes the repo positionally, not through the environment (Risk 2) | `grep -c 'repo view", *f*"' tools/improvement_eligibility.py; grep -rn 'gh", *"repo", *"view", *"--json"' tools/improvement_eligibility.py \| wc -l` | match count == 0 |
| Eligibility guard compares uppercase (spike-4) | `grep -c 'PUBLIC' tools/improvement_eligibility.py` | output > 0 |
| Resource probe never leaks | `scripts/pytest-clean.sh tests/unit/test_improvement_resources.py -q` | exit code 0 |
| Anti-criterion: no controller module handles a credential outside the one sanctioned writer, which this lane does not create (`[EXTERNAL]` No-Go, charter §8; baseline **0**) | `grep -rnE "OP_SERVICE_ACCOUNT_TOKEN\|op run\|op item create\|Desktop/Valor/.env" models/ ui/ tools/ reflections/ \| grep improvement \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Anti-criterion: no improvement module writes the charter file (baseline **0**) | `grep -rn "improvement-charter.md" models/ tools/ reflections/ ui/ \| grep -v "__pycache__" \| grep -vE "read_text\|read_bytes\|load_from_file\|open\(.*'r'" \| wc -l` | match count == 0 |
| Anti-criterion: no routine research-question path (charter §9; baseline **0**) | `grep -rnE "investigation_id\|daily_question_ceiling\|ask_poll\|AskUserQuestion" bridge/ tools/ config/ models/ ui/ reflections/ \| grep -i improvement \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Anti-criterion: this lane creates no control namespace (`[ORDERED]` No-Go; baseline **0**) | `grep -rn "improve:" tools/improvement_eligibility.py tools/improvement_resources.py models/improvement_charter.py models/improvement_case.py \| wc -l` | match count == 0 |
| Anti-criterion: this lane adds no CLI entry point (`[ORDERED]` No-Go; baseline **0**) | `grep -c "valor-improve" pyproject.toml` | match count == 0 |
| Anti-criterion: the withdrawn vocabulary is gone from prose (baseline: `docs/features/improvement-controller.md:166`, `:169`, `.env.example:359` — **3**) | `grep -rn "portfolio_allocation\|ceiling is zero" docs/features/ .env.example \| grep -v "__pycache__" \| wc -l` | match count == 0 |
| Feature doc describes the v2 status quo | `grep -c "weekly_infrastructure_usd" docs/features/improvement-controller.md` | output > 0 |
| Feature doc has a Charter section | `grep -c "^## Charter" docs/features/improvement-controller.md` | output contains 1 |
| Capability matrix records this lane | `grep -c "Lane 2b" docs/plans/critiques/recursive-self-improvement-capability-matrix.md` | output > 0 |
| Dashboard partials render, goals included | `scripts/pytest-clean.sh tests/unit/test_ui_app.py -q -k improvement` | exit code 0 |
| Goals partial is wired into the index | `grep -c "_partials/improvement/goals/" ui/templates/index.html` | output contains 1 |
| PR #3224's evidence adapters still pass | `scripts/pytest-clean.sh tests/unit/test_improvement_evidence.py -q` | exit code 0 |
| PR #3224's content store still passes | `scripts/pytest-clean.sh tests/unit/test_length_safe_content_store.py -q -k verifying` | exit code 0 |
| PR #3224's collection-tick registration still passes | `scripts/pytest-clean.sh tests/unit/test_reflection_register.py -q -k improvement` | exit code 0 |
| The v2 marker migration is registered, not merely defined | `grep -c "confirm_improvement_v2_fields" scripts/update/migrations.py` | output > 1 |
| Six child issues now reference #3177 (five today plus lane 7) | `gh issue list --state all --search "\"Refs #3177\" in:body" --json number --jq length` | output > 5 |
| Format clean | `.venv/bin/python -m ruff format --check .` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check .` | exit code 0 |

## Open Questions

**The one open question the issue raised is answered in this plan, not left for a human.**

> "Whether the eligibility cache lives in the improvement control namespace (which does not exist until #3215) or as a plain Popoto record. Lane 2b must not create the control namespace."

**Neither.** A process-local TTL cache (Technical Approach §5). Both offered options cost a durable structure — a namespace this lane is forbidden to create, or a Popoto model with its migration, schema-gate entry, and TTL decision — to cache a value that changes on a scale of months. A module-level dict with a monotonic expiry is the cheapest correct thing, creates nothing, and lane 3 can promote it into the control namespace if cross-process sharing is ever shown to matter. Recorded here so the decision is visible rather than incidental.

**Three deliberate divergences from the parent plan, flagged for the critique to accept or reverse.** Each has a measurement behind it, and each is a small change either way:

1. **`ImprovementCharter.digest` is a plain `Field`, not an `IndexedField`** as the parent plan's task 9 says. Spike-1 measured that the existing name-based index guard rejects it. Reversing this means punching a hole in `unbounded_markers`; keeping it means a Python-side match over single-digit rows.
2. **`load_from_file` never flips a prior row to `superseded`**, though the charter model's current docstring describes that flow. Gap G and the issue's acceptance criterion both require "leaves the first untouched", so the loader appends only and the docstring is corrected to match.
3. **`ImprovementCase.priority` is kept**, so the per-model index bound gains an exemption rather than the field being deleted to make room. Rejected alternative recorded in Rabbit Holes.

**Nothing here needs Tom.** Charter §9 forbids routine research questions, and every question this lane raised was answerable from the code, the charter, or one command. No amendment request is warranted: the charter grants everything this lane does.
