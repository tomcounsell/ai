---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-06-26
tracking: https://github.com/tomcounsell/ai/issues/1783
last_comment_id:
---

# Generalize all global skills to be fully repo-agnostic

## Problem

This repo is the canonical source for skills that ship to *every* machine. Skills under
`.claude/skills-global/` are hardlinked into `~/.claude/skills/` on every machine by
`scripts/update/hardlinks.py::sync_claude_dirs`, so they load and run in **every repo** the
user opens — not just this one. But many `skills-global/` skills are written as the
heavyweight ai-repo template: they hard-code this repo's tooling (`sdlc-tool`,
`reflections.*`, `tools.*` Python modules, `valor-*` CLIs), paths (`docs/features/`,
`docs/plans/`, `config/identity.json`), and architecture (Telegram bridge, worker, PM/Eng
sessions).

**Current behavior:** Run a global skill in any other repo and the ai-specific steps either
error (`sdlc-tool`, `python -m tools.doc_impact_finder`, `import reflections.docs_auditor`)
or silently misfire (writing to `docs/features/`, looking for `session/{slug}` branches).
A session in another repo running `/do-docs` flagged exactly this: "the skill is the
heavyweight ai-repo template."

**Desired outcome:** Every skill in `.claude/skills-global/` works correctly in **any** repo
with only the baseline a generic project provides (`git`, optionally `gh`, a conventional
layout). Repo-specific behavior is sourced from a **defined, discoverable seam** rather than
baked into the skill body, so the same skill stays lean everywhere and gets richer only where
the repo opts in. This repo's own rich SDLC automation is preserved — relocated into the seam,
not deleted.

## Freshness Check

**Baseline commit:** `3c2080d4189334084bea39b11a868b15b787d41d`
**Issue filed at:** `2026-06-24T11:18:15Z` (~2 days before planning)
**Disposition:** Unchanged

**Re-verified against main at plan time:**
- 50 skills in `.claude/skills-global/` — matches the audit count exactly.
- Coupling distribution matches the audit: heavyweight bodies are `do-build` (24 hits),
  `do-pr-review` (23), `do-plan` (21), `sdlc` (19), `do-docs` (18), `setup` (12),
  `do-debrief`/`do-sdlc`/`prime` (~9-10). Bucket A skills show only incidental single-token
  hits (`tdd`, `mermaid-render`, `reclassify`, `claude-standards`) — confirmed noise.
- Sync wiring confirmed: `PROJECT_ONLY_SKILLS` (set) + `RENAMED_REMOVALS` (list of
  `(kind, name)` tuples) in `scripts/update/hardlinks.py`; `sync_claude_dirs()` (line 104)
  hardlinks `skills-global/` into `~/.claude/skills/`.

**Commits on main since issue was filed (touching referenced areas):** None touched
`skills-global/` bodies or `hardlinks.py`. (`a523f85f`, `04621530`, `37303b35`, `be323872`
are SDLC/feature work in unrelated areas.)

**Active plans in `docs/plans/` overlapping this area:** None — grep matches on "skills-global"
in other plans are incidental token hits, not repo-agnostic skill work.

**Major recon finding (reshapes the design):** The proposed "repo-context convention" is
**NOT greenfield**. `docs/sdlc/do-X.md` addenda already implement exactly this seam for the
8 SDLC skills (`do-build`, `do-docs`, `do-merge`, `do-patch`, `do-plan`, `do-plan-critique`,
`do-pr-review`, `do-test`). Each is headed `# do-X addendum — this repo only` with the
instruction "Do not duplicate content from the global skill. Only include what is unique to
this repo. Max 300 lines." The convention should **generalize this existing seam**, not invent
an unrelated parallel. Gaps in today's seam: (a) coverage is SDLC-only; (b) skills don't
*uniformly* probe it (only `do-merge` and `do-build/PR_AND_CLEANUP.md` reference `docs/sdlc/`);
(c) skill bodies still hard-code ai-repo specifics rather than deferring to the addenda.

## Prior Art

- **#19937d75 (commit) `refactor: split .claude/skills/ into skills/ (project-only) +
  skills-global/ (cross-repo)`** — The foundational split that created the two-directory model
  this issue builds on. Established `PROJECT_ONLY_SKILLS` + `skills-global/` sync. Relevant:
  defines the exact boundary we're now enforcing at the skill-body level.
- **#724b1a59 (commit) `fix(skills): anchor all tools.* invocations to AI_REPO_ROOT (Layer 2)`**
  — A *partial* prior attempt at the same problem: it made `tools.*` calls resolve from the ai
  repo via `AI_REPO_ROOT` so they don't break when cwd is another repo. This is a band-aid, not
  a cure — it makes the ai-repo tooling *reachable* from other repos, but the other repo still
  doesn't *have* those tools, so the steps remain ai-specific. Our work removes the dependency
  from the body entirely rather than re-anchoring it.
- **#04621530 (commit) `fix(sdlc): 3-layer cross-repo plan resolution fix`** — Established the
  `GH_REPO` / `SDLC_TARGET_REPO` / `AI_REPO_ROOT` env-var resolution model. Confirms the SDLC
  skills are *designed* to orchestrate from the ai repo even for cross-repo targets — which is
  why Bucket C (sdlc, do-sdlc) can safely move to project-only: they only ever *run* from the ai
  repo's orchestrator context.
- **Established Bucket-C-style moves:** `linkedin`, `x-com`, `officecli`, `update`, `telegram`
  were each relocated from `skills-global/` to project-only `.claude/skills/` with a
  `RENAMED_REMOVALS` entry. This is the proven mechanism for the Bucket C disposition.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `724b1a59` anchor `tools.*` to `AI_REPO_ROOT` | Made ai-repo Python tooling *reachable* from other repos' cwd | Reachability ≠ portability. Another repo still has no `tools.doc_impact_finder` / `sdlc-tool` / `reflections.*`. The step "works" only on machines that also have the ai repo checked out at `AI_REPO_ROOT` — it does nothing for a clean repo with no ai checkout, and it reads as "this skill belongs to some other project." |
| `docs/sdlc/` addenda (incremental) | Created per-stage repo-context files for 8 SDLC skills | The seam exists but is (a) SDLC-only, (b) not uniformly probed by skills, (c) the skill bodies were never leaned to *defer* to it — they kept the coupling AND gained an addendum. |

**Root cause pattern:** Repo-specific behavior lives *in the global skill body* and leaks to
every repo. Past fixes tried to make the leaked behavior *reachable* (anchor to `AI_REPO_ROOT`)
or *supplemented* it (docs/sdlc addenda) instead of *relocating* it. The cure is to invert
ownership: generic baseline in the body, repo specifics in a discoverable per-repo seam that
the body probes for.

## Architectural Impact

- **New dependencies:** None (no new libraries). One new convention directory:
  `.claude/skill-context/`.
- **Interface changes:** Every Bucket B global skill gains a uniform early "probe" step. No
  function signatures change.
- **Coupling:** *Decreases* — removes ai-repo coupling from global skill bodies; relocates it
  into this repo's `.claude/skill-context/` files (which are project-local and not synced to
  other repos because they live alongside the repo, not in the skill).
- **Data ownership:** Inverts — repo-specific skill nuances move from the (shared) skill body
  to the (per-repo) skill-context seam.
- **Reversibility:** High. Each skill is edited independently; the convention is additive
  (absent file ⇒ generic behavior, so nothing breaks if a repo ships no skill-context files).

## Appetite

**Size:** Large

**Team:** Solo dev (lead orchestrator) + parallel builders for Bucket B batches + validator +
documentarian. Critique round (opus) before build; PR review round after.

**Interactions:**
- PM check-ins: 2-3 (the two load-bearing design decisions need confirmation before bulk edits)
- Review rounds: 2+ (convention design review, then code review of the 17-skill sweep)

## Prerequisites

No prerequisites — this work has no external dependencies. All operations are file edits,
`git`, and `gh` (already available).

## Solution

### Key Elements

- **Skill-context convention (`.claude/skill-context/{skill-name}.md`)**: The defined,
  discoverable seam. A per-skill markdown file where a repo declares that skill's nuances
  ("plans live in `docs/plans/`", "stage markers via `sdlc-tool`", "TTS via `valor-tts`").
  Generalizes the existing `docs/sdlc/` addenda pattern to *all* global skills, not just SDLC
  stages. Contract: **absent file ⇒ lean generic behavior; present file ⇒ rich repo behavior.**
- **Uniform probe step**: A single standardized early step added to every Bucket B skill body:
  *"If `.claude/skill-context/{this-skill}.md` exists, read it and honor its declarations;
  otherwise use generic defaults."* Identical wording across skills so it's greppable and
  enforceable.
- **Worked example (`do-docs`)**: The first fully-leaned skill, used as the copy-paste template
  for the rest. Generic body (find docs referencing the change, make surgical updates) +
  `.claude/skill-context/do-docs.md` carrying the ai-repo specifics (`docs/features/` index,
  `sdlc-tool stage-marker`, `reflections.docs_auditor`, `tools.doc_impact_finder`).
- **Bucket C disposition**: Move `setup`, `prime`, `sdlc`, `do-sdlc`, `do-deploy` from
  `skills-global/` to project-only `.claude/skills/`, with `PROJECT_ONLY_SKILLS` +
  `RENAMED_REMOVALS` entries so stale user-level hardlinks are cleaned up on every machine.
- **Regression guard (`rule_13_coupling_signals`)**: A new rule in
  `.claude/skills-global/do-skills-audit/scripts/audit_skills.py` that greps each
  `skills-global/` body for coupling signals and flags any that lack the uniform probe step.
  This is the invariant that prevents regression (per "prevention over cleanup").

### Flow

```
Other repo opens /do-docs
  → probe step: .claude/skill-context/do-docs.md exists? NO
  → run generic baseline (git-based doc discovery, surgical edits)  ✅ works

This (ai) repo runs /do-docs
  → probe step: .claude/skill-context/do-docs.md exists? YES
  → layer ai specifics (docs/features/ index, sdlc-tool markers, doc_impact_finder)  ✅ unchanged
```

### Technical Approach

- **Convention location: `.claude/skill-context/{skill-name}.md`** (one file per skill that
  needs nuance). Rationale: lives under `.claude/` alongside the skills it modifies (not in
  `docs/`, which is feature documentation for humans); per-skill files keep each skill's probe
  trivially greppable (`.claude/skill-context/do-docs.md`); absent file is the lean default.
  **Relationship to existing `docs/sdlc/`:** the 8 `docs/sdlc/do-X.md` addenda are SDLC-pipeline
  *runtime* addenda (read mid-pipeline by the SDLC stages). They are NOT duplicated. For SDLC
  skills, the `.claude/skill-context/{skill}.md` file is thin and *points to* `docs/sdlc/do-X.md`
  for the pipeline-runtime detail, carrying only the skill-body-level coupling that isn't already
  in docs/sdlc/. No churn to docs/sdlc/. (This boundary is the #1 Open Question for confirmation.)
- **Probe step wording** is identical across all skills (one canonical sentence) so
  `rule_13` can assert its presence mechanically.
- **Bucket B leaning is mechanical but per-skill**: extract each skill's ai-repo specifics into
  its skill-context file, replace the body steps with generic defaults + the probe step. Group
  the 17 skills into parallelizable batches by domain (docs/build pipeline; voice/media;
  comms; misc) so builders don't collide.
- **Bucket C move** uses the proven `PROJECT_ONLY_SKILLS` + `RENAMED_REMOVALS` mechanism. The
  SDLC skills (`sdlc`, `do-sdlc`) only ever *run* from the ai repo's orchestrator (confirmed by
  the `SDLC_TARGET_REPO` cross-repo model), so project-only is correct — they remain available
  where they actually execute.
- **`rule_13`** lists coupling signals as a constant set, greps each body, and emits a finding
  for any skill that has coupling signals but lacks the canonical probe sentence. Bucket A and
  Bucket C skills are exempt (Bucket C is no longer in `skills-global/`; Bucket A has no
  coupling). The rule's allowlist of intentionally-coupled-but-probed skills is the enforcement
  surface.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `audit_skills.py` `rule_13` must not raise on a skill with no body or malformed
  frontmatter — add a test feeding an empty/garbage SKILL.md and assert it returns a Finding
  (not an exception).
- [ ] No new `except Exception: pass` blocks introduced. The probe step in skill *bodies* is
  instructional prose (executed by the agent), not code, so it has no exception path.

### Empty/Invalid Input Handling
- [ ] `rule_13` with an empty coupling-signal list, a skill whose body is empty string, and a
  skill-context file that is empty — each must produce a deterministic Finding, not a crash.
- [ ] Skill-context probe: a skill body's probe step must specify the absent-file path
  explicitly (generic behavior) — test by running a leaned skill's logic mentally against a repo
  with no `.claude/skill-context/` dir (covered by the cross-repo integration check below).

### Error State Rendering
- [ ] `audit_skills.py --json` must include `rule_13` findings in its output structure; verify
  the JSON formatter renders the new rule's findings (test asserts the rule id appears).
- [ ] A coupling violation must surface as a non-zero audit exit (or a FAIL finding) — test that
  a deliberately-coupled skill body *without* the probe step trips `rule_13`.

## Test Impact

- [ ] `tests/` — search for existing tests of `audit_skills.py` (likely
  `tests/unit/test_skills_audit.py` or similar): UPDATE to add `rule_13` coverage. If none
  exists, REPLACE-as-create a new test module for the coupling rule.
- [ ] Any test asserting the *count* of skills in `skills-global/` (e.g. a parity/inventory
  test): UPDATE — moving 5 Bucket C skills to `.claude/skills/` changes the count from 50 to 45.
  Grep `tests/` for hardcoded `skills-global` counts before building.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` (referenced by the sdlc SKILL.md): VERIFY — moving
  `sdlc` to project-only must not break the parity test; confirm it resolves the skill from the
  new path or update its path constant.
- [ ] Any test importing/asserting `PROJECT_ONLY_SKILLS` membership: UPDATE to include the 5
  newly-moved Bucket C skills.

If, after grepping, no existing tests touch skill counts or the audit script, state so in the
PR and rely on the new `rule_13` tests as the only test delta.

## Rabbit Holes

- **Rewriting Bucket A skills.** They're already generic (incidental single-token hits). Do NOT
  touch them beyond confirming `rule_13` doesn't false-positive on them.
- **Perfecting docs/sdlc ↔ skill-context boundary.** Do not migrate or restructure the 10
  existing `docs/sdlc/*.md` files. The skill-context files for SDLC skills *point to* them. Any
  reorganization of docs/sdlc/ is a separate concern.
- **Building a runtime "context loader" abstraction.** The probe step is instructional prose the
  agent follows — resist building a Python framework to "load skill context." The seam is a file
  convention, not a code module.
- **Over-generalizing `email`/`computer-use`/`do-voice-recording`.** These wrap `valor-*` CLIs
  that genuinely only exist in this repo. Their generic baseline is "explain that this skill
  requires a repo-provided CLI; if `.claude/skill-context/{skill}.md` declares one, use it."
  Don't try to reimplement TTS/IMAP/Accessibility generically.
- **Bikeshedding the probe-step wording.** Pick one sentence, lock it, reuse verbatim.

## Risks

### Risk 1: Bucket C move breaks the SDLC pipeline in *this* repo
**Impact:** If `sdlc`/`do-sdlc`/`do-build` etc. can't resolve after the move, the pipeline that
ships this very PR breaks mid-flight.
**Mitigation:** Bucket B SDLC skills (`do-build`, `do-plan`, etc.) stay in `skills-global/` —
only `sdlc`, `do-sdlc`, `setup`, `prime`, `do-deploy` move. Verify the moved skills still load
in this repo (they live in `.claude/skills/`, which this repo loads) before committing the
`RENAMED_REMOVALS` change. Run `/sdlc` end-to-end as a smoke check.

### Risk 2: `rule_13` false-positives on Bucket A or legitimately-probed skills
**Impact:** Audit becomes noisy and gets ignored, defeating the guard.
**Mitigation:** `rule_13` only flags skills that have coupling signals AND lack the canonical
probe sentence. Bucket A (no coupling) and properly-leaned Bucket B (coupling moved out, probe
present) both pass. Red-state proof: run `rule_13` against a deliberately-coupled body without
the probe step and confirm it FAILS; then against the leaned version and confirm it PASSES.

### Risk 3: Leaning a skill silently drops ai-repo behavior here
**Impact:** This repo's automation regresses (e.g. `do-docs` stops updating `docs/features/`
index).
**Mitigation:** For each leaned skill, the extracted specifics land in
`.claude/skill-context/{skill}.md` *in the same commit*. Validator diffs old body vs (new body +
skill-context file) to confirm no behavior was lost, only relocated.

## Race Conditions

No race conditions identified — all operations are file edits and synchronous `git`/`gh`
commands executed sequentially by builders. Parallel Bucket B builders operate on disjoint skill
directories (enforced by batch assignment), so there is no shared mutable state.

## No-Gos (Out of Scope)

Every acceptance-criteria item is in scope for this plan — there is no work pushed to a separate
issue. The entries below are scope *boundaries* (things deliberately left untouched), not
postponed work:

- Restructuring or migrating the existing `docs/sdlc/*.md` addenda — the skill-context files
  reference them in place. This is a deliberate boundary to avoid churn; the existing addenda
  keep working unchanged within this plan.
- Touching Bucket A skills beyond `rule_13` false-positive confirmation — they are already
  generic; editing them is pure risk with no benefit, so this plan leaves them as-is.
- Reimplementing `valor-*` CLI functionality generically (TTS, email, computer-use) — these are
  inherently this-repo tools; the generic baseline only declares the dependency and never
  reimplements it.

## Update System

The update system **does need changes**, specifically `scripts/update/hardlinks.py`:
- Add the 5 Bucket C skills (`setup`, `prime`, `sdlc`, `do-sdlc`, `do-deploy`) to
  `PROJECT_ONLY_SKILLS` so they are no longer synced to `~/.claude/skills/`.
- Add a `RENAMED_REMOVALS` entry (`("skills", "<name>")`) for each of the 5 moved skills so the
  stale user-level hardlink is removed on every machine's next `/update`.
- Physically move each of the 5 skill directories from `.claude/skills-global/` to
  `.claude/skills/`.
- The new `.claude/skill-context/` directory is repo-local and NOT synced (it's per-repo
  context, like `docs/sdlc/`) — confirm `sync_claude_dirs()` does not pick it up (it only syncs
  `skills-global/`, `commands/`, `hooks/`, so this is automatic, but assert it in a test).

No new dependencies or config files beyond the convention directory. Migration for existing
installations is handled entirely by the `RENAMED_REMOVALS` cleanup on next `/update`.

## Agent Integration

No agent integration required in the MCP/bridge sense — this work changes skill *bodies* and the
sync wiring, not tools the agent invokes via MCP. The skills are already reachable by the agent
(it loads them from `~/.claude/skills/`). The one integration-adjacent concern is the Bucket C
move: after moving `sdlc`/`do-sdlc` to project-only, confirm the agent in *this* repo still
loads and dispatches them (they remain in `.claude/skills/`, which this repo loads). An
integration smoke test runs `/sdlc` against a throwaway issue to confirm the router still
resolves post-move.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/skill-context-convention.md` documenting the
  `.claude/skill-context/{skill}.md` seam: the absent⇒generic contract, the canonical probe-step
  wording, the relationship to `docs/sdlc/`, and how a new repo opts into rich behavior.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update the "Global vs. Project-Only Skills" section of `CLAUDE.md` to document the
  convention and the new Bucket C project-only members.

### Inline Documentation
- [ ] Docstring on `rule_13_coupling_signals` in `audit_skills.py` explaining the coupling-signal
  set and the probe-step requirement.
- [ ] A header comment in `.claude/skill-context/` (e.g. a `README.md` in that dir) explaining
  the convention for future maintainers.

## Success Criteria

- [ ] `.claude/skill-context/` convention defined and documented (location, format,
  absent⇒generic contract, canonical probe wording).
- [ ] `do-docs` fully leaned as the worked example: generic body + `.claude/skill-context/do-docs.md`
  carrying ai specifics; behavior in this repo unchanged.
- [ ] Every Bucket B skill (17) runs cleanly in a plain repo — no `sdlc-tool`/`reflections.*`/
  `tools.*`/`valor-*` hard dependency in the body — and still produces full ai-repo behavior here
  via its skill-context file.
- [ ] Bucket C (`setup`, `prime`, `sdlc`, `do-sdlc`, `do-deploy`) moved to `.claude/skills/` with
  `PROJECT_ONLY_SKILLS` + `RENAMED_REMOVALS` updated; `skills-global/` count drops 50 → 45.
- [ ] `rule_13_coupling_signals` exists in `audit_skills.py`, passes on the leaned tree, and
  FAILS (red-state proof) against a deliberately-coupled body lacking the probe step.
- [ ] `.claude/skill-context/` is confirmed NOT synced by `sync_claude_dirs()`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms: zero `skills-global/` bodies match coupling signals without the canonical
  probe sentence (the `rule_13` invariant, checked in Verification).

## Team Orchestration

The lead agent orchestrates; it never edits skills directly. Builders operate on disjoint skill
batches in parallel.

### Team Members

- **Builder (convention + worked example)**
  - Name: `convention-builder`
  - Role: Define `.claude/skill-context/` convention, write canonical probe wording, lean
    `do-docs` as the template, create `.claude/skill-context/do-docs.md`.
  - Agent Type: builder
  - Resume: true

- **Builder (Bucket B — pipeline batch)**
  - Name: `bucketb-pipeline-builder`
  - Role: Lean `do-build`, `do-plan`, `do-plan-critique`, `do-patch`, `do-issue`, `do-merge`,
    `do-test`, `do-pr-review`; create their skill-context files.
  - Agent Type: builder
  - Resume: true

- **Builder (Bucket B — media + comms batch)**
  - Name: `bucketb-media-builder`
  - Role: Lean `do-presentation`, `do-voice-recording`, `do-debrief`, `email`, `computer-use`,
    `new-skill`, `do-design-system`, `do-skills-audit`; create their skill-context files.
  - Agent Type: builder
  - Resume: true

- **Builder (Bucket C move + guard + wiring)**
  - Name: `bucketc-builder`
  - Role: Move 5 Bucket C skills to `.claude/skills/`, update `hardlinks.py`
    (`PROJECT_ONLY_SKILLS` + `RENAMED_REMOVALS`), add `rule_13_coupling_signals` + tests.
  - Agent Type: builder
  - Resume: true

- **Validator (behavior-parity + cross-repo)**
  - Name: `parity-validator`
  - Role: Verify no ai-repo behavior was lost (only relocated); confirm leaned bodies are
    coupling-free; confirm `rule_13` red/green; confirm Bucket C still loads in this repo.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `convention-doc-writer`
  - Role: Write `docs/features/skill-context-convention.md`, update README index + CLAUDE.md.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

builder, validator, documentarian (Tier 1) suffice; no Tier 2 specialists needed.

## Step by Step Tasks

### 1. Define convention + lean do-docs (worked example)
- **Task ID**: build-convention
- **Depends On**: none
- **Validates**: `.claude/skill-context/do-docs.md` exists; `do-docs/SKILL.md` body has no
  coupling signals + has canonical probe sentence
- **Assigned To**: convention-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm the convention location `.claude/skill-context/{skill}.md` and write
  `.claude/skill-context/README.md` documenting it + the canonical probe sentence.
- Lean `do-docs/SKILL.md`: move ai specifics (`docs/features/` index, `sdlc-tool stage-marker`,
  `reflections.docs_auditor`, `tools.doc_impact_finder`, `config/identity.json`) into
  `.claude/skill-context/do-docs.md`; replace with generic doc-cascade body + probe step.
- This is the template all Bucket B batches copy.

### 2. Bucket B — pipeline batch
- **Task ID**: build-bucketb-pipeline
- **Depends On**: build-convention
- **Validates**: each of the 8 pipeline skill bodies coupling-free + probe present; skill-context
  files created
- **Assigned To**: bucketb-pipeline-builder
- **Agent Type**: builder
- **Parallel**: true
- For each of `do-build`, `do-plan`, `do-plan-critique`, `do-patch`, `do-issue`, `do-merge`,
  `do-test`, `do-pr-review`: extract ai specifics into `.claude/skill-context/{skill}.md`
  (pointing to `docs/sdlc/{skill}.md` where that content already lives), lean the body, add the
  probe step. Use `do-docs` as the template.

### 3. Bucket B — media + comms batch
- **Task ID**: build-bucketb-media
- **Depends On**: build-convention
- **Validates**: each of the 8 media/comms skill bodies coupling-free + probe present;
  skill-context files created
- **Assigned To**: bucketb-media-builder
- **Agent Type**: builder
- **Parallel**: true
- For each of `do-presentation`, `do-voice-recording`, `do-debrief`, `email`, `computer-use`,
  `new-skill`, `do-design-system`, `do-skills-audit`: extract `valor-*`/`tools.*` specifics into
  skill-context files, lean the body to a generic baseline that declares the repo-provided CLI
  dependency, add the probe step.

### 4. Bucket C move + regression guard + wiring
- **Task ID**: build-bucketc-guard
- **Depends On**: build-convention
- **Validates**: `tests/unit/test_skills_audit.py` (rule_13 cases) pass; `skills-global/` count
  == 45; moved skills present in `.claude/skills/`
- **Assigned To**: bucketc-builder
- **Agent Type**: builder
- **Parallel**: true
- Move `setup`, `prime`, `sdlc`, `do-sdlc`, `do-deploy` to `.claude/skills/`.
- Update `hardlinks.py`: add the 5 to `PROJECT_ONLY_SKILLS`; add 5 `RENAMED_REMOVALS` entries.
- Add `rule_13_coupling_signals` to `audit_skills.py` + tests (red-state proof + green on leaned
  tree). Update any existing test asserting a hardcoded skill count or `PROJECT_ONLY_SKILLS`
  membership (per Test Impact).

### 5. Behavior-parity + cross-repo validation
- **Task ID**: validate-parity
- **Depends On**: build-bucketb-pipeline, build-bucketb-media, build-bucketc-guard
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Diff each leaned skill: confirm every removed ai-specific line now lives in its skill-context
  file (no behavior lost, only relocated).
- Run `rule_13` red (deliberately-coupled body) and green (leaned tree).
- Confirm `sync_claude_dirs()` does not sync `.claude/skill-context/`.
- Smoke-test `/sdlc` in this repo to confirm Bucket C still loads post-move.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-parity
- **Assigned To**: convention-doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/skill-context-convention.md`; add to `docs/features/README.md` index.
- Update the "Global vs. Project-Only Skills" section of `CLAUDE.md` (convention + new Bucket C
  members).

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm all success criteria; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Bucket C moved (count drops to 45) | `ls -d .claude/skills-global/*/ \| wc -l` | output contains 45 |
| Bucket C in project-only | `ls -d .claude/skills/setup .claude/skills/prime .claude/skills/sdlc .claude/skills/do-sdlc .claude/skills/do-deploy` | exit code 0 |
| Bucket C no longer in skills-global | `ls .claude/skills-global/setup .claude/skills-global/sdlc 2>/dev/null` | exit code != 0 |
| skill-context convention exists | `test -f .claude/skill-context/do-docs.md` | exit code 0 |
| skill-context NOT synced | `grep -c 'skill-context' scripts/update/hardlinks.py` | match count == 0 |
| rule_13 present | `grep -c 'rule_13_coupling_signals' .claude/skills-global/do-skills-audit/scripts/audit_skills.py` | output > 0 |
| Coupling guard green (no unprobed coupled bodies) | `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --json` | exit code 0 |
| RENAMED_REMOVALS updated for Bucket C | `grep -c '"skills", "do-sdlc"' scripts/update/hardlinks.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Convention location & docs/sdlc boundary (load-bearing).** This plan recommends
   `.claude/skill-context/{skill-name}.md` (per-skill files under `.claude/`), with SDLC skills'
   context files *pointing to* the existing `docs/sdlc/do-X.md` addenda rather than duplicating
   them. Alternatives considered: a single `.claude/skill-context.md`; or generalizing
   `docs/sdlc/` itself into `docs/skill-context/`. Confirm the recommended per-skill `.claude/`
   location and the "point to docs/sdlc, don't migrate it" boundary before bulk edits begin.
2. **Bucket C disposition (load-bearing).** This plan adopts the issue's recommendation: move
   `setup`, `prime`, `sdlc`, `do-sdlc`, `do-deploy` to project-only `.claude/skills/`. Confirm
   all 5 should move (vs. leaving any in `skills-global/` with a self-explaining no-op). In
   particular, confirm `sdlc`/`do-sdlc` moving is acceptable given they support cross-repo
   targets but always *run* from the ai repo's orchestrator.
3. **Batch parallelism.** Builders 2/3/4 run in parallel on disjoint skill sets. Confirm whether
   you want them in separate worktrees (`.worktrees/sdlc-1783-{b1,b2,c}/`) to avoid any edit
   collision, or sequentially on one branch (slower but simpler review).
