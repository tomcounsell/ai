---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2831
revised: 2026-08-17
revision_applied: true
revision_applied_at: 2026-08-17T06:36:00Z
---

# Retire the inert multi-judge review kill switches

## Problem

`SDLC_REVIEW_JUDGES` and `SDLC_REVIEW_K` are documented as the operator's controls over the REVIEW stage's judge count and cost. Nothing reads either one. An operator who sets `SDLC_REVIEW_JUDGES=none` to force a single-judge review gets two judges anyway, with no diagnostic, while three separate documents tell them it should have worked.

Verified on `main` at `994c723f3`:

- **No Python reader.** `grep -rn "SDLC_REVIEW_JUDGES\|SDLC_REVIEW_K" --include="*.py"` outside `docs/plans/` returns exactly two hits, both docstrings: [`agent/sdlc_review_consensus.py:4`](../../agent/sdlc_review_consensus.py) and [`tests/unit/test_skill_agent_tool_consistency.py:12`](../../tests/unit/test_skill_agent_tool_consistency.py). There is no `os.getenv` / `os.environ` for either name, and no field for either in `config/settings.py` (`grep -n "sdlc_review_judges\|sdlc_review_k" config/settings.py` returns nothing).
- **No prose reader.** `grep -rn "SDLC_REVIEW_JUDGES\|SDLC_REVIEW_K" .claude/` returns zero hits. This is the decisive one: in this repo a skill reads configuration by being *told to in prose*, and no prose tells it to.
- **No `.env.example` entry.** The four real `SDLC_REVIEW_CROSS_VENDOR*` toggles are declared at `.env.example:81-88`. Neither of these two appears anywhere in that file, so an operator has no discovery path to them either.
- **Wrong for 100 days.** The claim landed with the feature in `00a958484` (2026-05-09) and has never been true.

The documentation lives in **three** places, not the two the issue names:

| Location | Line(s) | False claim |
|---|---|---|
| `docs/features/multi-judge-consensus.md` | 96-99 | env-var table with defaults for both |
| `docs/features/multi-judge-consensus.md` | 103-111 | "Three independent layers" of cost containment; layers 2 and 3 are these vars |
| `docs/sdlc/do-pr-review.md` | 235-236 | "This repo opts in ... (`SDLC_REVIEW_JUDGES=code-quality,risk`, `SDLC_REVIEW_K=2`)" — reads as configuration that is *set* |
| `docs/sdlc/do-pr-review.md` | 249-250 | "Operators can also set `SDLC_REVIEW_JUDGES=none` or `SDLC_REVIEW_K=1` as independent kill switches" |
| `docs/features/README.md` | 122 | "Optional opt-in **K-of-N** parallel review judges" |

**Desired outcome:** the three documents, the module docstring, and actual behavior agree. Multi-judge consensus keeps running by default on non-trivial PRs, unchanged.

### The two variables are not symmetric — and that decides the direction

**`SDLC_REVIEW_K` names an algorithm that does not exist.** `compute_consensus` ([`agent/sdlc_review_consensus.py:69-72`](../../agent/sdlc_review_consensus.py)) takes exactly `(judges, rule)`. There is no `k` parameter to override. The `"k": n` in its output (`:136`) *reports* how many judges ran — `k` and `n` are assigned the same `len(deduped)` on adjacent lines. The only valid rules (`:21`) are `any-blocker-wins` and `unanimous-approved`; neither is K-of-N, and `any-blocker-wins` (`:119-122`) is 1-of-N by construction and cannot consult a threshold. Honoring `SDLC_REVIEW_K` is not wiring — it is inventing new consensus arithmetic that the active rule makes meaningless.

**`SDLC_REVIEW_JUDGES` names something real** — the roster `code-quality`, `risk` — so making it overridable would be a small, coherent change with a working template already in the same file (see Prior Art).

## Freshness Check

**Disposition: Unchanged.** Re-verified 2026-08-17 against `main` at `994c723f3`, one day after the issue was filed and hours after the premise-verification comment ([#2831 comment 5312557667](https://github.com/tomcounsell/ai/issues/2831#issuecomment-5312557667)). Every claim in that comment still holds against this tree, and the grep evidence above was re-run here rather than copied.

One finding is **larger than the issue states**: `docs/features/README.md:122` carries the same false "K-of-N" claim, so the half-migration risk spans three files, not two. Fixing only the two named files would leave the index contradicting the feature doc it indexes.

Multi-judge consensus itself is live and healthy: PR #2826 carries six per-judge comments across three rounds, both `code-quality` and `risk`, including genuine disagreement. Nothing in this plan touches it.

## Prior Art

- [#1309](https://github.com/tomcounsell/ai/issues/1309) / [#1345](https://github.com/tomcounsell/ai/issues/1345) — shipped multi-judge consensus. Archived plan: [`docs/plans/completed/multi-judge-consensus-gates.md`](completed/multi-judge-consensus-gates.md). That plan explicitly specified reading both env vars (`:82`, `:361-362`) and a manual smoke test with `SDLC_REVIEW_JUDGES=none` (`:410`). The implementation shipped without them and the docs were written from the plan rather than from the code. **This archived plan is a historical record and is NOT edited by this work** — see No-Gos.
- [#1626](https://github.com/tomcounsell/ai/issues/1626) — the cross-vendor judge. This is the working template for what a *real* env switch costs in this repo, and it is the strongest evidence available on both sides of the choice. `SDLC_REVIEW_CROSS_VENDOR` is honored through four coordinated surfaces:
  1. a `config/settings.py` field with a provisional/tunable comment (`:973-980`),
  2. a `.env.example` placeholder with the required comment line above it (`:79-81`),
  3. addendum prose that instructs the agent to consult it — [`docs/sdlc/do-pr-review.md:152-157`](../sdlc/do-pr-review.md), and
  4. a feature-doc section (`docs/features/multi-judge-consensus.md:177-231`).
- [#2679](https://github.com/tomcounsell/ai/issues/2679) / `tests/unit/test_skill_agent_tool_consistency.py` — the precedent for testing a *declaration-consistency* property of skills and docs rather than executing prose-driven behavior. This plan's new test follows its shape.

## Research

### How configuration actually reaches a skill here

The shape classifier — the cost control that demonstrably works — is **also prose-only**. There is no `classify_pr_shape` in Python (`ls scripts/pr_shape_classify.py` → no such file). It works because [`docs/sdlc/do-pr-review.md:246-249`](../sdlc/do-pr-review.md) tells the agent to run `gh pr diff $PR_NUMBER --name-only` and act on the result, and an agent can act on prose.

So "wire it up" for these vars would mean **prose in the addendum**, not Python. The env vars fail for a different reason than "missing code": no prose anywhere tells the agent to consult the environment, and no code does either.

This also means Option B's deletion is not sufficient on its own. `docs/sdlc/do-pr-review.md:235-236` presents `SDLC_REVIEW_JUDGES=code-quality,risk` as configuration that *is set*. Removing only the kill-switch sentence at `:249-250` would leave that line still implying an env surface. It needs rewording to a plain roster declaration.

### Has anyone ever needed to disable multi-judge?

No, and there is no path by which they could have tried successfully:

- The var is absent from `.env.example`, so it is undiscoverable through the repo's own onboarding surface.
- `git log -S"SDLC_REVIEW_JUDGES" --all` returns 10 commits, all of them plan-writing, doc-writing, or skill-generalization commits. None adds a reader. None sets a value.
- The cost case the vars were invented for — not paying for two judges on cheap PRs — is already served automatically and correctly by the trivial-diff classifier, which needs no operator action at all.

### Why a wider guard was rejected

A repo-wide "every documented `SDLC_*` var must have a reader" test was prototyped and rejected. Running that predicate over `docs/sdlc/` + `docs/features/` finds 12 reader-less tokens, including `SDLC_HOLDER_TOKEN`, `SDLC_STAGES`, `SDLC_WORKFLOW`, and eight `SDLC_STALL_*` names. That is the same defect class at larger scale, but it is a different investigation with different owners, and dragging it in would make this bug fix unbounded. The guard this plan adds is scoped to the multi-judge surface. See No-Gos.

## Technical Approach

**Chosen direction: Option B — delete the claim — applied to both variables.**

The reasoning splits per variable even though the disposition does not:

- **`SDLC_REVIEW_K` is deleted because the mechanism does not exist.** There is nothing to wire. Option A for `K` means designing K-of-N consensus arithmetic, adding a parameter to a stable pure function, and defining what a threshold means under a rule that is 1-of-N by construction — new machinery, for a knob nobody has ever set, to express a policy nobody has asked for. It also has no replacement, because there is nothing it did.
- **`SDLC_REVIEW_JUDGES` is deleted as an *env control*, and the roster it names is kept as a doc-declared roster.** Option A here is genuinely cheap — the cross-vendor template is four surfaces in files already being edited. It is rejected on merit, not cost:
  - The knob's stated cost use case is already handled, automatically and per-PR, by the trivial-diff classifier. An env var is a strictly worse lever for it: it is machine-global and sticky, where the real signal is per-PR.
  - The knob's other use case — isolating a misbehaving judge — is better served by editing the roster declaration in `docs/sdlc/do-pr-review.md`. That edit is versioned, reviewed, and fleet-wide. An env var is unversioned, invisible in review, and per-machine: in a repo whose CLAUDE.md makes single-machine ownership and doc-declared behavior explicit norms, "this machine silently reviews with half the judges" is a worse property than "we changed the roster in a commit".
  - Adding it would make `docs/sdlc/do-pr-review.md` declare exactly one env knob whose only correct setting is the default.

So: both names disappear from documentation entirely, the roster stays declared in prose, and the trivial-diff classifier is documented as the cost control — because it is the one that works. If a real need for a runtime override ever appears, the issue's own suggestion applies: make it PR-shape-driven, matching the control that already works, rather than resurrecting an env var.

### Edits

1. **`docs/features/multi-judge-consensus.md`**
   - `## Configuration` (`:92-99`): replace the two-row env table with a plain declaration that the judge roster is `code-quality` and `risk`, declared in `docs/sdlc/do-pr-review.md`, and that there is no runtime override. Keep the section (the cross-vendor vars below still need a configuration home) but scope its table to vars that exist.
   - `### Cost containment` (`:101-111`): "Three independent layers" becomes one — the trivial-diff check, stated as today's layer 1 (`:105-107`), which is accurate. Delete layers 2 and 3.
   - `## Consensus rules` / verdict-shape example: add one sentence stating that `_consensus.k` and `_consensus.n` are both the number of judges that ran (`agent/sdlc_review_consensus.py:136-137` assigns both from the same `n`) and that there is no K-of-N threshold. This is the accuracy fix that stops the K-of-N reading from regrowing from the JSON example at `:47-56`.
2. **`docs/sdlc/do-pr-review.md`**
   - `:235-236`: reword to a roster declaration — this repo runs multi-judge consensus at REVIEW by default with the judges `code-quality` and `risk` — with no `VAR=value` syntax, so nothing reads as an env surface.
   - `:249-250`: delete the kill-switch sentence. The trivial-PR sentence at `:246-249` stands as written and becomes the sole documented cost control.
3. **`docs/features/README.md:122`**: drop "K-of-N" from the multi-judge row's description. The row's other claims (per-judge comments, single aggregate, OUTCOME side-fields, docs-only/lockfile-only single-judge path, env-gated cross-vendor) are accurate and stay.
4. **`agent/sdlc_review_consensus.py:3-4`**: the module docstring says it is consumed "when `SDLC_REVIEW_JUDGES` enables ≥2 judges". Reword to reference the roster declared in `docs/sdlc/do-pr-review.md`. This is a code-comment claim about behavior that does not exist and is in scope for the same reason the docs are.
5. **`tests/unit/test_skill_agent_tool_consistency.py:11-12`**: the module docstring cites `SDLC_REVIEW_JUDGES=code-quality,risk` as the repo's opt-in. Reword to the plain roster reference. No assertion changes — the test's subject is `allowed-tools` vs. dispatch phrasing and is untouched.
6. **New test** `tests/unit/test_review_judge_env_docs.py` — see Test Impact.

**No magic numbers are introduced by this work.** Nothing gains a threshold, a count, a TTL, or a timeout; the change deletes prose and adds one string-scanning test. The repo's named-env-overridable-constant convention therefore has no application here, and this is stated explicitly rather than silently omitted.

## Architectural Impact

None to runtime behavior. No Python control flow changes; the only Python edits are a module docstring and a new test file. `compute_consensus`, `record_verdict`, the judge dispatch path, the shape classifier, the cross-vendor gate, and every SDLC gate that reads a REVIEW verdict are untouched.

The one durable architectural statement this makes: **in this repo, a documented env var is only real when it has a `config/settings.py` field and addendum prose instructing the agent to consult it.** The new test encodes that for the review surface, which is where the drift happened.

## Risks

### Risk 1: An operator is currently relying on one of these vars

Impossible by construction. Nothing reads them, so no behavior depends on their value on any machine. Deleting the documentation cannot change what any run does. This is the safest possible class of change.

### Risk 2: The reader concludes multi-judge is being weakened

The edits touch only the *override* prose. Mitigation: the reworded `docs/sdlc/do-pr-review.md` line states plainly that multi-judge runs by default with both judges, and the Verification table proves the judge-dispatch instructions are byte-identical before and after.

### Risk 3: The new guard is written so it can never fail

The classic failure mode for an anti-claim test. Mitigation is a demonstrated-red requirement in Task 1, which is ordered FIRST for exactly this reason: the test must be run against unmodified docs and observed to FAIL naming both variables, and only then run against the edited docs and observed to pass. A green-only run is not acceptable evidence. The guard is also written as a *positive* predicate (every `SDLC_REVIEW_*` token in these docs must have a settings field), not a blocklist of two strings, so it cannot be satisfied by the absence of the words alone and it stays useful against a third var added tomorrow.

### Risk 4: The guard trips on its own text or on archived plans

`docs/plans/` is full of legitimate historical mentions of both names (`completed/multi-judge-consensus-gates.md`, `completed/cross_vendor_review_judge.md`, and this plan). Mitigation: the test scans exactly three files by explicit path — `docs/features/multi-judge-consensus.md`, `docs/sdlc/do-pr-review.md`, and `docs/features/README.md` — never a tree walk. It never reads `docs/plans/`, `.claude/`, or itself, so it is free to name the retired variables in its own assertions and comments.

## Step by Step Tasks

**Execute in the order given.** The guard is Task 1 deliberately: its value is the demonstrated-red observation, and that observation is destroyed the moment any doc edit lands. Do not reorder.

### 1. Add the regression guard and observe it RED

Write `tests/unit/test_review_judge_env_docs.py` (contract in Test Impact) and run it against the **unedited** tree. Record that it FAILS naming both `SDLC_REVIEW_JUDGES` and `SDLC_REVIEW_K`. A green-only run is not acceptable evidence that the guard works.

The contract requires a module-level `_scan(paths: tuple[Path, ...]) -> set[str]` seam with a thin test pinning the three real paths. That seam is what makes the red observation repeatable after the fact: point the same predicate at pre-edit copies obtained with `git show main:<path>` written to a tmp dir. Do **not** use `git stash` in this checkout — other lanes are live.

Both the red and the later green output go in the PR body.

### 2. Rewrite the feature doc's configuration and cost-containment sections

Edit `docs/features/multi-judge-consensus.md`: replace the `## Configuration` env table (`:92-99`) with a roster declaration plus a table scoped to the four `SDLC_REVIEW_CROSS_VENDOR*` vars that actually exist, or a pointer to the existing cross-vendor env table at `:223-230` if that avoids duplication. Collapse `### Cost containment` (`:101-111`) to the single working layer. Add the `_consensus.k == _consensus.n == judge count, not a threshold` sentence near the consensus-rules section.

**Bare `K`-as-judge-count prose is accurate and stays.** `:8` ("can spawn K parallel judges"), `:120` ("disagreement at K=2"), `:122` ("all K judges"), and the `# With K=2` comment at `agent/sdlc_review_consensus.py:126` all use `K` to mean the number of judges that ran — which is exactly what the code does. They sit outside every range this task edits. Do not churn them, and do not treat them as escaped instances of the defect: the defect is `K` presented as an operator-settable *threshold*, not `K` used as a count.

### 3. Rewrite the addendum's multi-judge declaration

Edit `docs/sdlc/do-pr-review.md`: reword `:235-236` to a roster declaration with no `VAR=value` syntax.

**The kill-switch deletion boundary is intra-line — do not delete lines 249-250 wholesale.** Line 249 currently reads:

```
  `pyproject.toml` only). Operators can also set `SDLC_REVIEW_JUDGES=none` or
```

The trivial-PR sentence (which stays) and the kill-switch sentence (which goes) share that line. Truncate line 249 after `` `pyproject.toml` only). `` so the trivial-PR bullet ends at that period, and delete line 250 entirely. A whole-line delete of both amputates the trivial-PR sentence and leaves "…or all lockfile sync (`uv.lock` /" dangling — silently destroying the one cost control that actually works, in the same edit that removes the two that don't.

Leave the trivial-PR sentence's surviving text, the in-turn-await contract (`:254-269`), and the cross-vendor paragraph (`:152-157`) untouched.

### 4. Fix the features index

Edit `docs/features/README.md:122` to drop "K-of-N". Keep the row's position — `validate_features_readme_sort.py` enforces ordering, so do not move it.

### 5. Fix the two source docstrings

`agent/sdlc_review_consensus.py:3-4` and `tests/unit/test_skill_agent_tool_consistency.py:11-12`. Docstring text only; no code, no assertions.

### 6. Re-run the guard and observe it GREEN

Run `tests/unit/test_review_judge_env_docs.py` against the edited tree. It must now pass, with the four `SDLC_REVIEW_CROSS_VENDOR*` tokens resolving to real fields. Paste alongside the Task 1 red output.

### 7. Validation

`python -m ruff check .` and `python -m ruff format --check .` clean. Run only the three named test files (`scripts/pytest-clean.sh tests/unit/test_review_judge_env_docs.py tests/unit/test_review_multi_judge.py tests/unit/test_skill_agent_tool_consistency.py`); no full-suite run.

### 8. Documentation

Covered by Tasks 2-4, which ARE the deliverable. Confirm the three files agree with each other by re-running the grep in the Verification table.

## Test Impact

- [ ] `tests/unit/test_review_judge_env_docs.py` — ADD (new file): encodes the property that broke — a doc naming an `SDLC_REVIEW_*` env control that has no `config/settings.py` field. Contract below.
- [ ] `tests/unit/test_skill_agent_tool_consistency.py` — UPDATE (module docstring only): `:11-12` cites `SDLC_REVIEW_JUDGES=code-quality,risk` as this repo's opt-in. Reword to the plain roster reference. Assertions and parametrization untouched; must stay green.
- [ ] `tests/unit/test_review_multi_judge.py` — no change, and that is the point: consensus arithmetic, side-field persistence, PR-comment ordering and cross-vendor consensus are all unaffected. Re-run unmodified as the proof.
- [ ] DELETE: nothing. No test covers these two variables today — `grep -n "environ\|getenv\|monkeypatch" tests/unit/test_review_multi_judge.py` returns no hits — so there is no stale coverage to remove.

**A prose-driven behavior is not executable in a unit test.** There is no function to call that would honor `SDLC_REVIEW_JUDGES`; the "reader" would be a paragraph an agent obeys during a live review. So the issue's conditional criterion "a test asserts that the documented kill-switch values produce a single-judge review" is **not applicable** under the chosen direction, and would not have been satisfiable as written even under Option A — the honest Option A test would also have been a doc/skill consistency assertion, not an execution test. Saying so plainly is part of the deliverable.

What IS testable is the property that actually broke: a document claiming an env control while no reader exists.

### New: `tests/unit/test_review_judge_env_docs.py`

Expose a module-level `_scan(paths: tuple[Path, ...]) -> set[str]` returning the `SDLC_REVIEW_*` tokens found, so the predicate can be pointed at pre-edit copies for the Task 1 red demo. The tests below pin the real paths through it.

- `test_documented_review_env_vars_have_a_settings_field` — read **three** files by explicit path: `docs/features/multi-judge-consensus.md`, `docs/sdlc/do-pr-review.md`, and `docs/features/README.md`. Extract every `SDLC_REVIEW_[A-Z0-9_]+` token. Assert each has a corresponding field in `config/settings.py`. Today: RED, naming `SDLC_REVIEW_JUDGES` and `SDLC_REVIEW_K`. After the edits: GREEN, with the four `SDLC_REVIEW_CROSS_VENDOR*` tokens all resolving to real fields at `config/settings.py:973-1006`.

  **`docs/features/README.md` is in the scan list deliberately.** It is the third defect file, and it is free to include: it already contains `SDLC_REVIEW_CROSS_VENDOR=1` (`:122`), which resolves to `config/settings.py:974`, so it is **green under the guard both before and after** this work and contributes nothing to the Task 1 red output. It is scanned purely to catch a *future* re-addition. Do not try to make the guard match the `K-of-N` prose on that line — `K-of-N` is not an `SDLC_REVIEW_*` token, and turning the predicate into a phrase blocklist is precisely what Risk 3 forbids. Omitting the file would fix the index in Task 4 while leaving a re-addition uncaught by the very test written to prevent it.

- **Match field declarations with an anchor, not a substring.** Use `^\s*{lowered}\s*:` against `config/settings.py` lines. A bare substring test would let `SDLC_REVIEW_CROSS_VENDOR` resolve against the *longer* `sdlc_review_cross_vendor_model` (`:983`) by prefix collision, and — worse — `config/settings.py:978, 987, 996, 1004` carry the uppercase env names inside `description=` strings, so any case-insensitive substring approach would let a doc-only variable pass merely because someone mentioned it in a description. Neither produces a false green for `JUDGES`/`K` today, but both would silently defeat the guard against the third variable added tomorrow, which is its stated purpose.

- `test_scanned_docs_exist` — guard the guard. Assert all three scanned paths exist and are non-empty, so a rename can never make the check vacuously pass.
- `test_settings_field_extraction_finds_a_known_field` — assert the extractor finds `sdlc_review_cross_vendor` in `config/settings.py`, **and** that a plausible-but-absent name such as `sdlc_review_cross` does NOT resolve. The negative half is what proves the anchoring works; without it a regex that matches everything would pass this test too.

### Unchanged

- `tests/unit/test_review_multi_judge.py` — consensus arithmetic, side-field persistence, PR-comment ordering, cross-vendor consensus. No behavior changes, so no edits. Re-run as proof of that.
- `tests/unit/test_skill_agent_tool_consistency.py` — docstring only; the parametrized assertion is untouched and must stay green.

## Update System

No changes to `scripts/update/migrations.py`. No Popoto model changes, no new dependency, no new service, no new env var to propagate — the change removes documentation and adds a test file, both of which land through a normal `git pull`. No `.env.example` edit is needed precisely because neither variable was ever declared there.

## Agent Integration

`docs/sdlc/do-pr-review.md` is the repo-specific addendum read at runtime by `/do-pr-review`, so Task 3 changes what the review agent reads. The change is subtractive: an instruction that no agent could act on (nothing told it to read the environment) is removed, and a roster declaration it already follows is stated without misleading `VAR=value` syntax. The global skill body at `.claude/skills-global/do-pr-review/SKILL.md` is not edited — it correctly says multi-judge "only runs if the context file declares it" (`:254-256`) and stays repo-agnostic.

## Documentation

### Feature Documentation

- [ ] `docs/features/multi-judge-consensus.md` — rewrite `## Configuration` (`:92-99`) and `### Cost containment` (`:101-111`); add the `k == n == judge count` clarification. This is an existing feature doc being corrected, not a new one, so no new file is created.
- [ ] `docs/features/README.md:122` — drop "K-of-N" from the multi-judge row.

### External Documentation Site

Not applicable. This repo publishes no external documentation site; `docs/` is the whole surface.

### Inline Documentation

- [ ] `agent/sdlc_review_consensus.py:3-4` — module docstring no longer claims `SDLC_REVIEW_JUDGES` gates it.
- [ ] `tests/unit/test_skill_agent_tool_consistency.py:11-12` — module docstring cites the roster, not an env var.
- [ ] `docs/sdlc/do-pr-review.md:235-236, 249-250` — the runtime addendum.

## Success Criteria

Mapped to the issue's acceptance criteria:

- [x] **`SDLC_REVIEW_JUDGES` and `SDLC_REVIEW_K` appear in no documentation as operator controls.** `grep -rn "SDLC_REVIEW_JUDGES\|SDLC_REVIEW_K" --include="*.md" --include="*.py" . | grep -v "docs/plans/" | grep -v test_review_judge_env_docs` returns no output. **The `test_review_judge_env_docs` exclusion is required, not optional** — the new guard names both variables in its own assertions and comments, by design (Risk 4). A criterion demanding literally zero hits would push a builder to strip those names out of the test and weaken the very guard this plan adds. Use the exclusion verbatim; it must match the Verification-table command exactly. The path exclusion is deliberately unanchored: GNU grep prints `./docs/plans/...` while this machine's `ugrep` prints `docs/plans/...`, so a `^.` anchor makes the row permanently red on one of the two.
- [x] **`docs/sdlc/do-pr-review.md` and `docs/features/multi-judge-consensus.md` agree with actual behavior, with no half-migration.** Both describe a fixed two-judge roster and the trivial-diff classifier as the only cost control. `docs/features/README.md` is brought along in the same commit so the index does not contradict them.
- [x] **"If Option A" criterion — NOT APPLICABLE.** Option A is not chosen. The plan states why per variable (K names a nonexistent algorithm; JUDGES is better served by a versioned roster declaration than by an unversioned per-machine env var), and why the execution test that criterion asks for is not writable against prose-driven behavior in this repo.
- [x] **"If Option B" criterion — APPLIES and is met.** No remaining prose describes an env-var control that nothing reads, across all three files plus the two module docstrings.
- [x] **Multi-judge consensus is unchanged and still runs by default on non-trivial PRs.** No Python control flow is edited; `tests/unit/test_review_multi_judge.py` passes unmodified; the judge-dispatch instructions in `docs/sdlc/do-pr-review.md` and `.claude/skills-global/do-pr-review/SKILL.md` are byte-identical before and after.
- [x] The new guard was observed RED before the edits and GREEN after, with both outputs recorded in the PR.
- [x] No magic number is introduced, so the named-env-overridable-constant convention has nothing to apply to.

## No-Gos (Out of Scope)

- **Implementing K-of-N consensus arithmetic.** `compute_consensus` keeps its `(judges, rule)` signature and its two rules. Building a threshold mechanism to make a doc sentence true is backwards; the sentence is what is wrong.
- **Adding any runtime override for the judge roster.** Argued against on merit in Technical Approach, not postponed. If cost pressure on large PRs ever materializes, the correct lever is PR-shape-driven and belongs to the shape classifier, not to a new env var.
- **The other 12 reader-less `SDLC_*` names** (`SDLC_HOLDER_TOKEN`, `SDLC_STAGES`, `SDLC_WORKFLOW`, and eight `SDLC_STALL_*`) surfaced while sizing the guard. They are a different subsystem with different owners and would turn a bounded documentation correction into an unbounded audit; the guard added here is deliberately scoped to the two review docs so it stays green and useful. [SEPARATE-SLUG #2831] tracks only the review surface; the wider sweep needs its own recon before anyone can say whether those names are inert or read through a path this grep missed, and asserting either without that recon would be the same unverified-claim defect this plan exists to fix.
- **Editing `docs/plans/completed/multi-judge-consensus-gates.md` or `docs/plans/completed/cross_vendor_review_judge.md`.** Completed plans are the archived record of what was planned at the time, not descriptions of current behavior. Rewriting them would destroy the evidence of how this drift happened. The NO-LEGACY rule targets docs that *describe the status quo*; `docs/plans/completed/` describes history and is explicitly excluded from the new guard's scan.
- **`.claude/skills-global/do-pr-review/SKILL.md`.** Already correct and already generic. Touching it would risk leaking repo-specific roster names into a global skill body.
- **The `shape` derivation in the cross-vendor paragraph** (`docs/sdlc/do-pr-review.md:152-157`). It gates on `shape == feature` without naming what computes `shape`, and no `pr_shape_classify` module exists anywhere in the tree (`ls scripts/pr_shape_classify.py tools/pr_shape_classify.py` → neither). That is arguably the same defect class in the adjacent paragraph, but the mechanism it gates — `python -m tools.cross_vendor_judge` — **does** exist (`tools/cross_vendor_judge.py`), and this plan's Research claim is about the *cost-containment* classifier at `:246-249`, which is genuinely prose-driven and works. Named here so the builder neither "fixes" it nor reads it as contradicting Research. If it deserves attention it deserves its own issue.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No env-var claim survives outside archived plans | `grep -rn "SDLC_REVIEW_JUDGES\|SDLC_REVIEW_K" --include="*.md" --include="*.py" . \| grep -v "docs/plans/" \| grep -v test_review_judge_env_docs` | no output |
| New guard passes | `scripts/pytest-clean.sh tests/unit/test_review_judge_env_docs.py -q` | exit code 0 |
| Consensus behavior unchanged | `scripts/pytest-clean.sh tests/unit/test_review_multi_judge.py -q` | exit code 0, no test edited |
| Skill consistency still green | `scripts/pytest-clean.sh tests/unit/test_skill_agent_tool_consistency.py -q` | exit code 0 |
| Judge dispatch instructions untouched | `git diff main -- .claude/skills-global/do-pr-review/` | no output |
| Consensus module logic untouched | `git diff main -- agent/sdlc_review_consensus.py \| grep -c "^[+-]" ` | only docstring lines changed |
| Features index still sorted | `python .claude/hooks/validators/validate_features_readme_sort.py docs/features/README.md` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Open Questions

None blocking. The one maintainer call the issue reserved — "is an operator override actually wanted?" — is answered No in Technical Approach, on the evidence that the knob was never discoverable (absent from `.env.example`), never set (no commit sets it), and that its only real use case is already served automatically by the trivial-diff classifier. If the owner disagrees and wants a live override, the change is small and the cross-vendor template in the same file shows exactly the four surfaces it needs; say so before BUILD starts and this plan flips to that shape for `SDLC_REVIEW_JUDGES` only — `SDLC_REVIEW_K` is deleted either way, because there is no mechanism behind it to turn on.
