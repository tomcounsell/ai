---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-30
tracking: https://github.com/yudame/ai/issues/2447
last_comment_id:
---

# SDLC Verdict Findings Persistence Contract

## Problem

Observed live during a parallel `/do-sdlc` batch on issue #2435 (2026-07-29):
`/do-plan-critique` produced 2 build-blockers plus several tech-debt findings and
recorded verdict `NEEDS REVISION` — but **only the verdict was persisted.** The
finding bodies were written nowhere the next stage could read them:

- The plan's `## Critique Results` table was still the empty template placeholder
  (HTML comment + a single bracketed example row).
- The `_verdicts.CRITIQUE` substrate record held `{verdict, recorded_at, artifact_hash}`
  and nothing else. `artifact_hash` is an integrity fingerprint, not a retrievable copy.

**Current behavior:**
The revision agent was handed "NEEDS REVISION" with no durable record of what
needed revising. It reconstructed plausible findings from the plan's gaps and
populated the table with rows explicitly labeled `reconstructed`, plus a count
("five tech-debt concerns") nobody can verify. The pipeline structurally invites
fabricated review records because the alternative is stalling. A critique verdict
whose reasoning does not survive to the next stage is close to worthless: REVIEW
later reads that table as the record of what the critics actually said.

A sibling defect from the same batch (#2439 / PR #2450): `/do-pr-review` did not
self-finalize its REVIEW verdict on either pass — the supervisor's `verdict
selfcheck` backstop confirmed correctness only after the fact. Same root shape:
the stage that owns a verdict does not reliably persist it, and correctness
depends on a later actor noticing.

**Desired outcome:**
Treat this as **one persistence contract, not two stage patches**. The stage that
records a verdict must persist the evidence that justifies it, at the tool level,
in the same finalize step. A verdict must never land without its findings.

- **CRITIQUE:** the critics' finding bodies are written into the plan's
  `## Critique Results` table together with (in the same Step 5.5 finalize block
  as) recording the verdict.
- **CRITIQUE fail-closed:** a `NEEDS REVISION` verdict recorded against a plan
  whose Critique Results table is empty is rejected with a named error — the
  specific contradiction the incident produced.
- **REVIEW:** `/do-pr-review` finalizes its own verdict rather than relying on a
  later stage. The tool-level fail-closed mechanism (`sdlc-tool verdict finalize`)
  already exists; this lane makes CRITIQUE symmetric to it and consolidates both
  under one documented contract.

## Freshness Check

**Baseline commit:** `6d78fdea625a589ad24f7315664f4a35fe6077da`
**Issue filed at:** 2026-07-29T08:46:36Z
**Disposition:** Unchanged (all cited paths re-verified against baseline during recon)

**File:line references re-verified:**
- `.claude/skills-global/do-plan-critique/SKILL.md` Step 5.5 — confirmed: records the
  verdict + completion marker (READY path) only; never writes the plan table.
- `docs/sdlc/do-plan-critique.md` Step 5.5 block — confirmed: same, repo-substrate invocations only.
- `.claude/skills-global/do-plan/PLAN_TEMPLATE.md:477` — confirmed: `## Critique Results`
  ships as HTML-comment placeholder + one bracketed example row.
- `tools/sdlc_verdict.py::_cli_record` / `record_verdict` — confirmed: single CRITIQUE
  verdict writer; already resolves the plan file via `_find_plan_path(issue_number)` in
  `_compute_artifact_hash`. `record_verdict` is graceful-failure (returns `{}`, never
  raises) — so the fail-closed refusal must live in the CLI `_cli_record` path, not in
  `record_verdict`, to avoid changing the Python-API contract for `classify_outcome`.
- `tools/_sdlc_utils.py::find_plan_path` — confirmed: resolves `docs/plans/` via
  `SDLC_TARGET_REPO` → cwd git-toplevel → `~/src/ai` fallback. `sdlc-tool` forces cwd to
  `~/src/ai`, so the CLI reads the **main checkout's** plan file.
- `agent/pipeline_state.py::_plan_has_critique_results` — confirmed: treats ANY
  non-whitespace (including the template placeholder) as non-empty; too loose to back the
  fail-closed gate. A stricter real-finding-row parser is required.
- `tools/sdlc_review_finalize.py` (`sdlc-tool verdict finalize`) — confirmed: already
  atomically writes verdict + trailer + REVIEW marker and reads back, failing closed with
  named errors. `.claude/skills-global/do-pr-review/SKILL.md` Step 5 + rules 8/9 and
  `docs/sdlc/do-pr-review.md` already mandate it as terminal.
- `docs/plans/hook-registration-manifest-dispatcher.md:529-554` — confirmed: 5 rows marked
  `reconstructed` + unverifiable "five tech-debt concerns" count. The bug's own artifact.

**Active plans in `docs/plans/` overlapping this area:**
- **Lane 1 (#2446 + #2451)** is rewriting the stage-marker write path. The bug-3 check
  lives in the verdict `record` path (`tools/sdlc_verdict.py`), not the stage-marker path
  — direct collision is low; shared helpers in `tools/_sdlc_utils.py` are a moderate-risk
  overlap to re-check at build time. No Lane 1 PR has landed as of baseline.
- **Lane 4 (#2448)** also touches sdlc-tool verdict/marker code but is plan-only for now.

## Prior Art

- **#2193 (PR, merged)**: REVIEW verdict/trailer/marker fail-closed persistence
  (`tools/sdlc_review_finalize.py`, `docs/features/sdlc-verdict-fail-closed-persistence.md`).
  This lane's CRITIQUE fail-closed check is the deliberate structural twin of that work.
- **#2124 (WS-A)**: `critique-roster-check --plan-path` grounding leg — rejects a
  fabricated critique whose result file cites no real plan text. Related but distinct: that
  gate acts on critic *result files* pre-aggregation; this lane acts on the *plan table* +
  *verdict record* at finalize time.
- **#1690**: artifact-based roster barrier — established that critique completion must be
  mechanically verifiable, not asserted in prose. Same philosophy applied to findings persistence.
- **#2439 / PR #2450**: the sibling REVIEW self-finalize observation motivating the unified contract.

## Data Flow

1. **Entry point:** `/do-plan-critique` runs the war room, aggregates findings (Step 4/5).
2. **NEW — table write (Step 5.5):** the skill renders aggregated findings as
   `## Critique Results` table rows and writes them into the plan doc at its
   main-checkout path, then commits the plan on `main`.
3. **Verdict record (Step 5.5):** the skill calls `sdlc-tool verdict record --stage CRITIQUE`.
4. **NEW — fail-closed gate (`_cli_record`):** for a `NEEDS REVISION` verdict, the CLI
   resolves the plan via `_find_plan_path(issue_number)` (same main-checkout file the writer
   targeted) and refuses with `CRITIQUE_FINDINGS_MISSING` if the table has no real finding
   row. Refusal is raised BEFORE the ledger write — no partial write.
5. **Downstream:** the revision pass (`/do-plan`) and later `/do-pr-review` read the
   populated table as the durable record of what the critics said.

REVIEW path (already implemented, unchanged): `/do-pr-review` → `sdlc-tool verdict
finalize` → atomic verdict + trailer + marker write + readback → supervisor selfcheck backstop.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** a new named-error refusal path in `sdlc-tool verdict record
  --stage CRITIQUE` (CLI only). Python API `record_verdict` signature and graceful-failure
  contract unchanged.
- **Coupling:** the writer (`/do-plan-critique` Step 5.5) and checker (`_cli_record`) both
  resolve the plan file through `_find_plan_path`'s main-checkout resolution — one shared
  resolver, no drift.
- **Data ownership:** the plan's `## Critique Results` table becomes the durable,
  machine-checkable record of critique findings (previously ephemeral).
- **Reversibility:** high. The gate is scoped to one verdict family; the table write is
  additive; both can be reverted independently.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (routing), code reviewer

**Interactions:**
- PM check-ins: 1-2 (Lane 1 dependency coordination, scope confirmation)
- Review rounds: 1

## Prerequisites

No external prerequisites — this work has no new services or secrets. Substrate tools
(`sdlc-tool`, `_find_plan_path`) already exist.

## Solution

### Key Elements

- **Critique table writer (Step 5.5):** `/do-plan-critique` writes the aggregated findings
  into the plan's `## Critique Results` table and commits on `main`, together with recording
  the verdict — one finalize block, mirroring how READY co-locates the completion marker.
- **CRITIQUE fail-closed gate:** a narrow refusal in `_cli_record` — fires ONLY on a
  `NEEDS REVISION` verdict paired with an empty-of-real-findings Critique Results table.
- **Strict real-finding-row parser:** a new helper that treats the template placeholder row
  and HTML comment as empty (the too-loose `_plan_has_critique_results` is not reused).
- **REVIEW self-finalize consolidation:** confirm + regression-test the existing fail-closed
  `finalize` path; tighten the `/do-pr-review` SKILL body's Step 5 framing to "mandatory,
  reached on every exit path" mirroring CRITIQUE Step 5.5; document both stages as one contract.
- **Docs-stage cleanup:** delete the `reconstructed` rows + unverifiable count from
  `docs/plans/hook-registration-manifest-dispatcher.md` (the bug's own artifact).

### Flow

Critique aggregates findings → write findings into plan `## Critique Results` table → commit
plan on main → `sdlc-tool verdict record --stage CRITIQUE` → gate: NEEDS REVISION + empty
table ⇒ refuse `CRITIQUE_FINDINGS_MISSING` (no write); else record → downstream stages read
the durable table.

### Technical Approach

**1. Real-finding-row parser (`tools/sdlc_verdict.py`).**
Add `critique_table_has_findings(plan_path) -> bool`:
- Extract the `## Critique Results` section body (from the header to the next `##` heading).
- Strip HTML comments (`<!-- ... -->`).
- For each `|`-delimited row, split into cells; skip the header row (Severity/Critic/...) and
  the `---` separator row.
- A row counts as a **real finding** iff the Severity cell (trimmed, uppercased) ∈
  {`BLOCKER`, `CONCERN`, `NIT`} **AND** the Finding cell (3rd column) is non-empty and does
  NOT fully match a bracketed placeholder `^\[.*\]$`.
- Return True iff ≥ 1 real finding row. Any parse/read error returns False (fail-closed:
  an unreadable table cannot satisfy the invariant).

This correctly classifies the template placeholder row (`| CONCERN | [agent-type] | [The
concern raised] | ... |`) as empty because its Finding cell is a bracketed placeholder.

**2. Fail-closed gate (`_cli_record` in `tools/sdlc_verdict.py`).**
Add a new exception `CritiqueFindingsMissingError` (message prefixed `CRITIQUE_FINDINGS_MISSING:`).
At the TOP of `_cli_record`, before any ledger write:
- Only when `args.stage.upper() == "CRITIQUE"`.
- Compute `normalized = normalize_verdict(args.verdict)`.
- Fire ONLY when `normalized == "NEEDS REVISION"` (exact family match). Never on any
  `READY TO BUILD` variant; never on `MAJOR REWORK` (including `CRITIQUE INCOMPLETE`, which
  legitimately has no findings).
- Resolve `plan_path = _find_plan_path(args.issue_number)`. If the plan cannot be resolved,
  do NOT fire the gate (a missing plan is a different failure; the gate only judges the
  specific contradiction of a resolvable-but-empty table under NEEDS REVISION) — log and proceed.
- If `plan_path` resolves and `critique_table_has_findings(plan_path)` is False, raise
  `CritiqueFindingsMissingError` with a message naming the plan path and the contradiction.
- `main()` already prints any exception message to stderr and exits non-zero → loud refusal,
  no partial write. `record_verdict` (Python API) is untouched and stays graceful.

**3. Critique table writer (`docs/sdlc/do-plan-critique.md` Step 5.5 + probe in global SKILL Step 5.5).**
In the repo context file's Step 5.5 block, before `sdlc-tool verdict record`:
- Render the Step 5 aggregated findings into `## Critique Results` table rows:
  `| {SEVERITY} | {critics} | {finding} | pending | {implementation note} |`.
  "Addressed By" starts as `pending` (filled by the revision pass). READY TO BUILD (no
  concerns) writes a single explicit `No findings from the war room.` line replacing the
  placeholder (the gate never fires on READY, so this is honesty, not a gate requirement).
- Write to the plan file at its **main-checkout path** (the same file `_find_plan_path`
  resolves under `~/src/ai`) and commit + push on `main` (`git -C <main-checkout>` explicit
  targeting — do NOT rely on the fork worktree's cwd; see Risk 1).
- THEN call `sdlc-tool verdict record --stage CRITIQUE ...`. Ordering guarantees the gate
  sees the populated table.
- The global `do-plan-critique/SKILL.md` Step 5.5 already probes the context file; add one
  sentence to its generic Step 5.5 noting that when the repo declares a findings table, the
  findings are written into it in the same finalize block as the verdict.

**4. REVIEW consolidation (light — no new REVIEW tool code).**
- Tighten `.claude/skills-global/do-pr-review/SKILL.md` Step 5 wording to frame `finalize`
  as "mandatory and reached on EVERY exit path," structurally mirroring CRITIQUE Step 5.5.
- Add/confirm a regression test that `finalize` fails closed (`REVIEW_VERDICT_MISSING`) on an
  empty verdict, if not already covered.
- Document both stages under one "verdict findings persistence contract" in the feature doc.

**5. Docs-stage cleanup.** Delete `docs/plans/hook-registration-manifest-dispatcher.md`
lines 529-554 (the 5 `reconstructed` rows) and the "five tech-debt concerns" count in the
prose above them; note the deletion in the PR body as the bug's own artifact being cleaned up.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `record_verdict` retains its graceful-failure contract (returns `{}`, never raises) —
  a unit test asserts the new gate does NOT change this Python-API behavior.
- [ ] `_cli_record` raises `CritiqueFindingsMissingError` (loud, non-zero exit) — asserted via
  the CLI path, not swallowed.
- [ ] `critique_table_has_findings` returns False (not an exception) on unreadable/missing plan.

### Empty/Invalid Input Handling
- [ ] Empty table (placeholder only) under NEEDS REVISION → gate fires.
- [ ] Empty table under READY TO BUILD (no concerns) → gate does NOT fire.
- [ ] Empty table under MAJOR REWORK / `MAJOR REWORK (CRITIQUE INCOMPLETE)` → gate does NOT fire.
- [ ] Table with only the `No findings.` line under NEEDS REVISION → gate fires (contradiction).
- [ ] Populated table (≥1 real BLOCKER/CONCERN/NIT row) under NEEDS REVISION → gate passes.

### Error State Rendering
- [ ] The named error `CRITIQUE_FINDINGS_MISSING` reaches stderr with the plan path, and the
  process exits non-zero — the operator/supervisor sees a loud, actionable refusal.

## Test Impact

- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: add cases for `critique_table_has_findings`
  (placeholder=empty, real row=non-empty, No-findings=empty, malformed=empty) and for the
  `_cli_record` gate matrix (NEEDS REVISION fires; READY/MAJOR REWORK/CRITIQUE INCOMPLETE do
  not; unresolvable plan does not fire). Assert `record_verdict` Python API is unchanged.
- [ ] `tests/unit/test_sdlc_review_finalize.py` — UPDATE/ADD: assert `finalize` fails closed
  with `REVIEW_VERDICT_MISSING` on empty verdict (confirm existing coverage; add if absent).
- [ ] `tests/unit/test_do_plan_critique_barrier.py` — inspect only; no change expected (the
  roster barrier is orthogonal to findings persistence). Confirm no regression.

## Rabbit Holes

- **Storing findings inside the verdict substrate record** (`_verdicts[CRITIQUE].findings`).
  More "atomic" in a DB sense, but the owner specified the plan doc as the surface reviewers
  read, and adding a substrate field expands the single-writer schema. Out of scope.
- **Broadening the gate to READY TO BUILD (with concerns) or MAJOR REWORK.** Explicitly
  forbidden by the owner — those verdicts have legitimate empty/partial tables. Over-broad
  gating here is a build-failing condition, not a nit.
- **Rewriting `_plan_has_critique_results` in `agent/pipeline_state.py`.** That helper backs a
  different (looser) purpose (DOCS-stage derivation). Leave it; add a separate strict parser.
- **A second REVIEW-side gate.** The finalize path already fails closed. Adding more REVIEW
  machinery is the bureaucracy the owner warned against.

## Risks

### Risk 1: Writer/checker plan-file disagreement across fork worktrees
**Impact:** If `/do-plan-critique` writes the table to a fork worktree's plan copy while
`_cli_record` reads the main checkout via `_find_plan_path`, the gate would see an empty table
even though the skill "wrote" one — a false `CRITIQUE_FINDINGS_MISSING` refusal that stalls a lane.
**Mitigation:** Both writer and checker resolve the SAME main-checkout plan file. `sdlc-tool`
forces cwd to `~/src/ai`, so `_find_plan_path` reads `~/src/ai/docs/plans/`. The Step 5.5 writer
targets that same path explicitly (`git -C <main-checkout>`), commits on `main`, and does so
BEFORE calling `verdict record`. A unit test drives `critique_table_has_findings` against a
fixture at a known absolute path to lock the resolver-agreement contract.

### Risk 2: Lane 1 write-path collision
**Impact:** Lane 1 (#2446/#2451) rewrites the stage-marker write path; if it also refactors
`_cli_record` or shared `_sdlc_utils` helpers, this lane's gate could land on a soon-to-be-replaced path.
**Mitigation:** The gate is confined to `_cli_record` + a new local parser — the verdict path,
not the marker path. Before BUILDING the gate, check whether Lane 1's PR (closing #2446/#2451)
has landed its write-path change; if not landed by Build time, report as a blocker to the PM
rather than building against a path in flux. Plan + Critique proceed regardless.

### Risk 3: `normalize_verdict` family drift
**Impact:** If the gate's exact-match `== "NEEDS REVISION"` misses a normalized variant, it
either under-fires (misses the contradiction) or over-fires.
**Mitigation:** Verify `normalize_verdict("NEEDS REVISION")` and lowercase/spacing variants all
canonicalize to exactly `"NEEDS REVISION"`; assert in a unit test. Use the canonical form as the
sole trigger; anything else passes through (fail-open on the gate = safe, since the gate only
adds a refusal, never an approval).

## Race Conditions

No race conditions identified — the table write, commit, and verdict record are a synchronous
sequence within a single Step 5.5 finalize block on one session. The write precedes the record;
no concurrent reader observes an intermediate state (the revision pass runs in a later stage).

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2446] Stage-marker write-path rewrite — Lane 1 owns it; this lane consumes,
  does not modify, the marker path.
- [SEPARATE-SLUG #2448] Further sdlc-tool verdict/marker refactors — Lane 4 territory; plan-only.
- Storing finding bodies in the verdict substrate record — see Rabbit Holes; the plan doc is
  the specified surface.
- Broadening the fail-closed gate beyond `NEEDS REVISION` — forbidden by the owner.

## Update System

No `/update` script or dependency changes. The skill-body edits under
`.claude/skills-global/do-plan-critique/` and `.claude/skills-global/do-pr-review/`, the
`docs/sdlc/` addendum edits, and the feature doc propagate to every machine via the existing
`/update` hardlink sync (`scripts/update/hardlinks.py`) with no new wiring. No `migrations.py`
change (no Popoto model change).

## Agent Integration

No new agent/tool surface. The gate lives inside the existing `sdlc-tool verdict record`
CLI path (already invoked by `/do-plan-critique`). No new MCP server, no new
`[project.scripts]` entry, and the bridge imports nothing new. The change is internal to an
existing CLI the pipeline already calls.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-verdict-fail-closed-persistence.md` to describe the CRITIQUE
  findings-persistence write + the `CRITIQUE_FINDINGS_MISSING` fail-closed gate, framed as one
  verdict-findings persistence contract spanning CRITIQUE and REVIEW.
- [ ] Confirm `docs/features/README.md` index entry still points correctly (update blurb if needed).

### Inline Documentation
- [ ] Docstring on `critique_table_has_findings` describing the strict real-finding-row rule
  and the fail-closed-on-error contract.
- [ ] Docstring/comment on the `_cli_record` gate stating the NEEDS-REVISION-only scope and
  why it lives in the CLI path (not `record_verdict`).

## Success Criteria

- [ ] `/do-plan-critique` writes aggregated findings into the plan's `## Critique Results`
  table and commits on `main` in the same Step 5.5 finalize block as the verdict record.
- [ ] `sdlc-tool verdict record --stage CRITIQUE --verdict "NEEDS REVISION"` against a plan
  with an empty/placeholder Critique Results table exits non-zero with `CRITIQUE_FINDINGS_MISSING`
  and writes no verdict.
- [ ] The same command against a `READY TO BUILD` verdict (any variant) records normally
  regardless of table contents.
- [ ] The same command against `MAJOR REWORK` / `MAJOR REWORK (CRITIQUE INCOMPLETE)` records
  normally regardless of table contents.
- [ ] `record_verdict` Python API behavior is unchanged (graceful `{}`, never raises).
- [ ] `/do-pr-review` SKILL Step 5 frames finalize as mandatory-on-every-exit-path; a
  regression test asserts `finalize` fails closed on an empty verdict.
- [ ] The `reconstructed` rows and unverifiable count are removed from
  `docs/plans/hook-registration-manifest-dispatcher.md`; noted in the PR body.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Solo dev drives the pipeline; the PM routes and coordinates the Lane 1 dependency. One code
reviewer on the PR.

### Team Members

- **Builder (verdict-gate)**
  - Name: `gate-builder`
  - Role: parser + `_cli_record` fail-closed gate + unit tests in `tools/sdlc_verdict.py`
  - Agent Type: builder
  - Domain: redis-popoto (verdict substrate), untrusted-input (table parsing)
  - Resume: true

- **Builder (skill-docs)**
  - Name: `skill-builder`
  - Role: `docs/sdlc/do-plan-critique.md` Step 5.5 table writer, global SKILL Step 5.5 probe
    sentence, `do-pr-review` Step 5 framing, feature doc, hook-registration cleanup
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `gate-validator`
  - Role: verify the gate matrix, resolver-agreement, and no Python-API regression
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard Tier 1 pool. Disjoint file sets: `gate-builder` owns `tools/sdlc_verdict.py` +
`tests/unit/test_sdlc_verdict.py`; `skill-builder` owns the `.claude/skills-global/`,
`docs/sdlc/`, `docs/features/`, and `docs/plans/hook-registration-*` edits. No file overlap →
commits never interleave.

## Step by Step Tasks

### 1. Parser + fail-closed gate
- **Task ID**: build-gate
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_verdict.py`
- **Assigned To**: gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `critique_table_has_findings(plan_path)` with the strict real-finding-row rule.
- Add `CritiqueFindingsMissingError` and the `_cli_record` gate (NEEDS REVISION only,
  before any write, plan resolved via `_find_plan_path`).
- Unit tests: parser fixtures + gate matrix + `record_verdict` no-regression + `normalize_verdict`
  canonicalization assertion.

### 2. Critique table writer + skill framing + feature doc + cleanup
- **Task ID**: build-skills
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `docs/sdlc/do-plan-critique.md` Step 5.5: render findings into the table, write to the
  main-checkout plan path, commit on main, then record the verdict (ordering explicit).
- Add the one-sentence probe note to global `do-plan-critique/SKILL.md` Step 5.5.
- Tighten `do-pr-review/SKILL.md` Step 5 to mandatory-every-exit-path framing.
- Update `docs/features/sdlc-verdict-fail-closed-persistence.md` for the unified contract.
- Delete the `reconstructed` rows + unverifiable count from
  `docs/plans/hook-registration-manifest-dispatcher.md`.

### 3. Validation
- **Task ID**: validate-all
- **Depends On**: build-gate, build-skills
- **Assigned To**: gate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm the gate matrix, resolver-agreement, and no Python-API
  regression; confirm the hook-registration cleanup left no `reconstructed` rows.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Verdict unit tests pass | `python -m pytest tests/unit/test_sdlc_verdict.py -q` | exit code 0 |
| Review-finalize tests pass | `python -m pytest tests/unit/test_sdlc_review_finalize.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/sdlc_verdict.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/sdlc_verdict.py` | exit code 0 |
| Gate fires on the contradiction | `python -m pytest tests/unit/test_sdlc_verdict.py -q -k "findings_missing or needs_revision_empty"` | exit code 0 |
| No reconstructed rows remain | `grep -c 'reconstructed' docs/plans/hook-registration-manifest-dispatcher.md` | match count == 0 |
| Named error present in tool | `grep -c 'CRITIQUE_FINDINGS_MISSING' tools/sdlc_verdict.py` | output > 0 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — FULL war room (Risk & Robustness, Scope & Value, History & Consistency). 0 blockers, 7 concerns, 0 nits. A revision pass should embed the Implementation Notes below before build.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | The parser splits rows on a naive `\|`; a Finding cell containing a literal pipe (shell pipeline, code snippet) shifts columns so "the 3rd column" no longer aligns, and a real finding can be misread as the empty/placeholder pattern (false `CRITIQUE_FINDINGS_MISSING` that stalls the lane). | pending | In `critique_table_has_findings`, split with `re.split(r"(?<!\\)\|", line)` and unescape `\|`→`\|` per cell before the bracketed-placeholder check; have the writer emit `\|` for literal pipes; add a literal-pipe fixture row to `tests/unit/test_sdlc_verdict.py`. |
| CONCERN | Risk & Robustness | "At the TOP of `_cli_record`, before any ledger write" would place the gate before lease resolution; `_cli_record` already runs `resolve_ledger_lease`/`OwnershipError` and TOCTOU `revalidate_ledger_lease` first. Gate-first lets an unauthorized/stale-`run_id` caller trigger a plan read and get `CRITIQUE_FINDINGS_MISSING` instead of `ISSUE_LOCKED`, masking the ownership failure. | pending | Insert the `critique_table_has_findings` gate between the `revalidate_ledger_lease(...)` block and `PipelineLedger.get_or_create(...)` — top of the write path, not top of the function — so OwnershipError precedence is preserved. |
| CONCERN | Risk & Robustness | The table is committed on `main` BEFORE `verdict record`; a record-time lease failure (foreign takeover, stale run_id) leaves `main` with a populated table and no `_verdicts.CRITIQUE` record — a partial state the "revert independently / additive" reversibility claim does not actually cover (needs an explicit `git revert`). | pending | Add a Failure-Path test simulating `OwnershipError` after a successful table commit; document the recovery procedure (re-run to completion, or revert the orphaned table commit on main) rather than leaning on the general reversibility framing. |
| CONCERN | Scope & Value | Task 2 / Technical-Approach #4 folds REVIEW SKILL-wording edits + "confirm/add if absent" regression work into this plan for a mechanism the plan itself calls "already implemented, unchanged" (#2193 shipped). That is conceptual grouping, not a shared-code dependency (gate-builder and skill-builder have disjoint files) — scope padding that can hold the CRITIQUE fix hostage. | pending | Split Task 2: ship the CRITIQUE writer (`docs/sdlc/do-plan-critique.md` Step 5.5 + global SKILL probe) in one commit; move the `do-pr-review` Step 5 wording + regression-test-confirm to a separate follow-up PR. |
| CONCERN | Scope & Value | Deleting the 5 `reconstructed` rows from the unrelated `docs/plans/hook-registration-manifest-dispatcher.md` is wired into this PR's Success Criteria and the Verification table (`grep -c 'reconstructed'`), making an unrelated doc edit a merge-blocking condition for the code fix. | pending | Land the hook-registration cleanup as its own trivial hotfix commit referenced in the PR body; remove the `grep -c 'reconstructed'` Verification row and the corresponding Success Criteria bullet from this plan. |
| CONCERN | History & Consistency | Architectural Impact asserts writer and checker share "one shared resolver, no drift," but the writer is an LLM-executed skill-body prose instruction targeting `~/src/ai` via `git -C <main-checkout>`, while the checker is the Python `_find_plan_path` with its own `SDLC_TARGET_REPO → cwd → ~/src/ai` chain — two independent implementations of the same resolution, exactly the drift Risk 1 warns about. | pending | Either downgrade the claim to "both target the same main-checkout rule, verified independently," or make it literally shared by having the Step 5.5 skill body resolve the path through a CLI backed by `_find_plan_path` (e.g. `sdlc-tool plan-path --issue-number N`) instead of reconstructing the fallback chain in prose. |
| CONCERN | History & Consistency | The Step 5 cleanup deletes the `reconstructed` rows but never reconciles the incident's earlier ad hoc workaround (posting finding bodies as GitHub issue comments "to close that gap going forward"), leaving two competing source-of-truth claims for critique findings. | pending | When stripping the rows, also strike/annotate the provenance note stating the new `## Critique Results` table-writer path supersedes the issue-comment workaround, per `docs/features/sdlc-verdict-fail-closed-persistence.md`. |

---

## Open Questions

None — scope is fixed by the owner's two binding comments (one persistence contract; narrow
gate on NEEDS REVISION + empty table only; never on READY TO BUILD). The only runtime
dependency is Lane 1's write-path landing, handled as Risk 2 at Build time.
