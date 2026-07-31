---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-30
tracking: https://github.com/tomcounsell/ai/issues/2448
build_gated_on: https://github.com/tomcounsell/ai/pull/2453
critique_verdict: APPROVE WITH CHANGES
critique_applied: true
critique_applied_at: 2026-07-30
last_comment_id: 5115851007
---

# Guardrail: forbid destructive whole-tree git ops in the shared main checkout

## Problem

During a live parallel `/do-sdlc` batch (2026-07-29, shipping #2436 / PR #2441), a MERGE-stage
subagent ran `git checkout origin/main -- .` **in the shared main checkout** to compare its branch
against main. That whole-tree checkout transiently overwrote uncommitted in-flight work belonging to
other concurrent SDLC lanes. The subagent then ran `git reset --hard HEAD` to restore — a command
just as dangerous, because in a shared checkout it discards *every* lane's uncommitted work, not just
the damage. No data was lost this time: the run survived on timing (the clobbered plan content was
already committed). Two lanes mid-plan-edit at that instant would have silently lost one.

This is #887 (shared-checkout contamination) recurring with a sharper edge. This repo's convention
**deliberately puts plan edits on `main`** (plans commit directly to main; code goes on the branch),
so under a parallel batch every lane edits `docs/plans/*.md` in the *same* working tree by design.
Worktree isolation protects code, not plans — so the shared checkout is exactly where concurrent,
mutually-invisible uncommitted work lives, and exactly where a whole-tree destructive git op does the
most damage.

**Current behavior:** nothing stops a stage subagent from issuing a whole-tree destructive git shape
against the shared main checkout. The existing `validate_no_destructive_git_in_worktree.py` (#2137)
guards the *inverse* surface — destructive git **inside** a dirty `.worktrees/{slug}/` — and never
fires for the shared checkout.

**Desired outcome:** a PreToolUse/Bash validator blocks the whole-tree destructive git shapes when the
effective working directory is the shared main checkout, points the agent at the correct alternative
(`git worktree add --detach` for baseline comparisons), and **never** fires inside `.worktrees/` or in
an unrelated repo. Path-scoped operations on a single file a stage owns stay allowed.

## Design constraints (from the owner, issue #2448)

- **Be thoughtful about hardening — do not create bureaucracy that cripples multi-agent
  orchestration.** This is the cheap, good kind of guardrail: it adds no coordination between agents,
  no waiting, no approval step. It fails a command that was already wrong and points at the right
  alternative.
- **Narrow deny-list.** Block only the whole-tree destructive forms: `git reset --hard`,
  `git clean -f[d...]` / `--force`, `git checkout <ref> -- .` / `git checkout .` / `git checkout -- .`,
  `git restore .`.
- **Path-scoped operations must stay allowed.** `git checkout <ref> -- file.py`,
  `git restore path/`, `git checkout -- file.py` — a developer reverting one file is never blocked.
- **NEVER fire inside `.worktrees/`.** Agents legitimately reset and clean inside their own worktree
  all the time. If the validator ever blocks work in a worktree, it is **wrong** — this is a
  build-failing condition, captured as an explicit acceptance test below.

## Build is gated on PR #2453 merging

**This plan targets the post-#2453 world and the build stage MUST NOT start until PR #2453 is merged.**

PR #2453 (Closes #2435, currently **OPEN** at `docs_complete`) is restructuring the exact registration
surface this guardrail plugs into: it introduces `.claude/hooks/manifest.toml` as the single source of
truth for hook registration and modifies `validate_no_destructive_git_in_worktree.py` — the file this
guardrail mirrors and wants to share shape-detection with. Building this guardrail against today's
`main` would target a registration model that is about to disappear and would collide with #2453's edit
to the sibling validator.

### The actual post-#2453 registration surface (verified against the PR diff)

The PM's routing note assumed the build would be "a module plus one entry in the dispatcher's in-process
validator list." **That assumption does not match PR #2453 as it currently stands** — verify at build
time and follow whichever is true:

- `.claude/hooks/dispatch/pre_tool_use_bash.py` (the in-process dispatcher with the `_VALIDATORS` list)
  **exists and is unit-tested, but is NOT wired into anything.** The string `dispatch/pre_tool_use_bash`
  appears only in that file, its docs, and its tests — never in `manifest.toml` and never in a generated
  `settings.json` command.
- `manifest.toml` declares **all 7 PreToolUse/Bash validators individually** (`script =
  "validators/validate_*.py"`), and `generate_project_hooks()` (in `scripts/update/hardlinks.py`)
  groups them by `(event, matcher)` and emits one `settings.json` command **per validator** —
  reproducing the current hand-maintained block byte-for-byte (the "empty diff on regen" guarantee).
  The PR's `settings.json` diff touches only the PostToolUse blocks; the PreToolUse/Bash block is
  unchanged.

So in the post-#2453 world **as merged today**, the live registration surface for a new Bash validator
is **the manifest + an individual validator script**, not the dormant dispatcher's `_VALIDATORS` list.
The build step is therefore "**new validator module + one `[[hook]]` entry in `manifest.toml`**,"
then regenerate `settings.json` via the generator. See Technical Approach for the exact recipe and the
branch that adapts if #2453 wires the dispatcher before it merges.

## Technical Approach

### 1. New validator: `.claude/hooks/validators/validate_no_destructive_git_in_shared_checkout.py`

Mirror the structure of the sibling `validate_no_destructive_git_in_worktree.py`, which already solves
the hard parts (command-position anchoring so `git commit -m "reset --hard bug"` is not blocked; shell
control-operator splitting; `cd <path> && git …` cwd resolution; the `# allow-destructive-git` override
token; fail-open on any parse/subprocess error). The new validator inverts only the **directory gate**:

- **Reuse the shape-detection, do not re-implement it.** The destructive-shape predicate
  (`_is_destructive_git` / `_split_simple_commands` / `_git_tokens` / `_subcommand_and_args`) is
  identical between the two validators. Duplicating it would violate NO-LEGACY/DRY and guarantees drift.
  Extract the shared shape logic into a small helper module (proposed:
  `.claude/hooks/hook_utils/destructive_git_shapes.py`) and import it from **both** validators.
  - **#2453 coordination:** PR #2453 edits `validate_no_destructive_git_in_worktree.py`. The extraction
    must be based on **#2453's merged version** of that file, not today's, to avoid a conflicting
    rewrite. Because the build is gated on #2453 merging, this is a sequencing note, not a blocker.
- **Directory gate (the inversion) — bind positively to THIS repo, do not infer from the absence of
  `.worktrees/`.** Resolve the effective working directory (honoring a `cd <path> &&` prefix, exactly as
  the sibling does). Then classify:
  - **Positive identity bind (fixes critique blocker B1).** Derive *this* repo's main-checkout root from
    the hook script's own location: the validator lives at `<repo>/.claude/hooks/validators/…`, so
    `Path(__file__).resolve().parents[3]` is this repo's root. Fire **only** when the effective cwd's
    `git -C <cwd> rev-parse --show-toplevel` resolves to **exactly that root**. Defining the shared
    checkout as merely "a git toplevel with no `/.worktrees/` segment" is **wrong** — that matches any
    unrelated repo's toplevel and would over-fire in a foreign repo (`cd /other/repo && git reset
    --hard`), violating both the foreign-repo-ALLOW criterion and the owner's "never fire in an
    unrelated repo" constraint.
  - If the effective cwd is inside a `.worktrees/` segment (of this repo) → **ALLOW** (the sibling
    validator's domain; this one must never fire there).
  - If `rev-parse --show-toplevel` does not equal this repo's root (foreign repo, non-repo, user-scope
    context) → **ALLOW** (fail-open; not our surface).
  - Only when the toplevel **equals this repo's root and the path is not under `.worktrees/`** → evaluate
    the destructive-shape predicate and **BLOCK** on a match.
  - Any git error / missing git / parse error → **fail-open** (allow). Mirror the sibling's fail-open
    discipline exactly: this guard must never crash a legitimate Bash call.
- **Injectable classification seam for testability (fixes critique blocker B2).** The sibling stays
  unit-testable because it injects `is_dirty` into `find_violation(command, cwd, is_dirty)` — tests never
  spawn git. The new validator's hard part is repo-root/worktree *classification*, so expose the pure
  predicate with the classification injected, e.g. `find_violation(command, cwd, *, repo_root,
  in_worktree) -> str | None`, and confine the live `git rev-parse` / `Path(__file__)` resolution to a
  thin `find_violation_from_hook_input(command, hook_cwd)` wrapper (mirroring the shape #2453 adds to the
  sibling). Unit tests then exercise BLOCK / ALLOW-in-worktree / ALLOW-foreign-repo deterministically by
  passing `repo_root`/`in_worktree` directly, without constructing throwaway git repos (which the
  positive-identity bind would not even recognize as "this repo").
- **Unconditional in the shared checkout — no dirty-tree gate, no `git status` at all.** The sibling
  only fires on a *dirty* worktree (a reset on a clean tree loses nothing there). This validator
  deliberately blocks the whole-tree shapes in the shared checkout **regardless of apparent dirty
  state**, and therefore must **not** call any `is_tree_dirty`/`git status` probe: the danger is to
  *other lanes'* concurrent uncommitted work, which the issuing agent cannot see as "its own" dirtiness,
  and a `git checkout <ref> -- .` clobbers other lanes' plan edits even when the issuing agent believes
  the tree is clean. Dropping the dirty probe also keeps a subprocess off the hot path. (Grain of salt:
  this is the intentional difference from the sibling; if it proves too aggressive it can be softened via
  the override token, but the issue's near-miss argues for the stricter rule.)
- **Known accepted gap (documented, not a bug):** cd-prefix resolution is textual (via `Path.parts`
  membership of `.worktrees`), so a contrived `cd .worktrees/slug/../.. && git reset --hard` classifies
  as a worktree and is ALLOWED even though it effectively runs in the shared checkout. This is an
  *under*-block (fail-open), consistent with the "never fire in `.worktrees/`" priority, and is
  accepted rather than chased.
- **Override token.** Honor the same `# allow-destructive-git` inline token the sibling honors, so there
  is a single mental model for a genuine, deliberate override.
- **Error message.** Point at the correct alternative verbatim from the issue: use
  `git worktree add --detach` for baseline comparisons (reference the existing
  `.worktrees/_merge_baseline_main`), and note the path-scoped variant is allowed if the intent was to
  revert a single file. No em-dashes in the user-facing string.
- **Hook protocol + CLI shim.** Standalone Claude Code PreToolUse hook: read JSON from stdin
  (`tool_name`, `tool_input.command`, `cwd`); to BLOCK print `{"decision": "block", "reason": "…"}` to
  stdout and exit 0; to ALLOW print nothing, exit 0. Provide the same
  `python validate_no_destructive_git_in_shared_checkout.py <command> <cwd>` manual/test shim the
  sibling exposes.

### 2. Register it in `.claude/hooks/manifest.toml`

Add one `[[hook]]` entry in the PreToolUse section (declaration order is load-bearing; append it after
`validate_no_destructive_git_in_worktree` so the two related guards sit together):

```toml
[[hook]]
manifest_id = "validate_no_destructive_git_in_shared_checkout"
event = "PreToolUse"
matcher = "Bash"
script = "validators/validate_no_destructive_git_in_shared_checkout.py"
timeout = 15
scope = "project"
blocking = true
```

Then regenerate `.claude/settings.json` from the manifest (the generator does a full regen of the
`hooks` block; the new entry appends one command to the existing `(PreToolUse, Bash)` matcher group).

**Adaptation branch — check the merged state of #2453 at build time.** If, by the time #2453 merges, its
dispatcher has actually been wired (i.e. `manifest.toml` registers `dispatch/pre_tool_use_bash.py` for
`(PreToolUse, Bash)` and the individual validators were collapsed into the `_VALIDATORS` list), then
instead of a standalone manifest entry, add the predicate to the dispatcher's `_VALIDATORS` list and a
thin `_run_*` wrapper — matching whatever pattern is then live. The plan's intent is invariant ("one
new predicate on the shared-checkout surface"); the mechanical wiring follows the merged reality.

**Do NOT trust #2453's shipped prose over its shipped wiring.** #2453's own feature docs
(`docs/features/hook-manifest.md`, `hooks-best-practices.md`) already assert "the manifest now registers
a single dispatcher entry that fans out in-process" and "new hooks should be added as another predicate
inside the dispatcher, not as a second standalone registration." That prose describes an **aspirational,
currently-unwired** future state — it is false against the merged manifest/settings (7 individual
entries, dormant dispatcher). A builder who trusts the docs and adds the predicate *only* to
`_VALIDATORS` would ship a **silent no-op guardrail**. This is exactly why Success Criterion 9 mandates a
**live-fire** check (issue a real blocked Bash call and observe the block), not just green unit tests.
Fixing #2453's docs is out of this appetite (see No-Gos).

### 3. Explicitly out of scope (Rabbit Holes)

- **Provisioning the `_merge_baseline_main` worktree up front in `/do-merge`.** The issue and the owner
  both mark this "optional polish, not required scope." Not in this appetite.
- **Refactoring the sibling worktree validator's behavior** beyond the shared shape-detection
  extraction. Its dirty-gate and `.worktrees/` semantics are unchanged.
- **Broadening the deny-list** beyond the five whole-tree shapes. No new shapes, no path-scoped
  blocking.

## Success Criteria

1. `git reset --hard`, `git reset --hard HEAD`, `git clean -fd` (and any force variant), `git checkout
   origin/main -- .`, `git checkout .`, `git checkout -- .`, and `git restore .` issued with an effective
   cwd of the shared main checkout are **BLOCKED**, with an error message pointing at
   `git worktree add --detach`.
2. The same shapes issued with an effective cwd inside any `.worktrees/{slug}/` are **ALLOWED** (the
   guard never fires in a worktree). **Build-failing if violated.**
3. Path-scoped variants (`git checkout origin/main -- docs/plans/x.md`, `git restore docs/plans/x.md`,
   `git checkout -- file.py`) are **ALLOWED** in the shared checkout.
4. Command-position anchoring holds: `git commit -m "reset --hard notes"` and similar are **ALLOWED**.
5. `cd .worktrees/slug && git reset --hard` is **ALLOWED** (cd-prefix resolves into a worktree);
   `cd /repo/root && git reset --hard` is **BLOCKED**.
6. The `# allow-destructive-git` override token allows an otherwise-blocked command.
7. Any parse error, git error, or unexpected exception **fails open** (allows); the guard never crashes a
   Bash call.
7b. An effective cwd inside a **foreign** git repo (a toplevel that is not this repo's root, bound
   positively via the hook script's own location) is **ALLOWED** — the guard never fires in an unrelated
   repo. **Build-failing if violated.**
8. Shape-detection is shared with `validate_no_destructive_git_in_worktree.py` via a single helper
   module (no duplicated shape logic across the two validators).
9. The validator is registered through the post-#2453 surface (manifest entry regenerated into
   `settings.json`, or dispatcher predicate if that surface is live at build time) and fires on real
   Bash calls.

## Update System

- **Registration:** add one `[[hook]]` entry to `.claude/hooks/manifest.toml`, then regenerate
  `.claude/settings.json` via the manifest generator (`scripts/update/hardlinks.py`
  `sync_project_hooks()`, invoked by `/update`). No hand-edit of `settings.json`.
- **Propagation:** the validator is a **project-scope** hook (`scope = "project"`), so it lands only in
  this repo's `.claude/settings.json`; it is not hardlinked to `~/.claude/`. No `RENAMED_REMOVALS` entry
  is needed for a net-new file.
- **Deploy:** merged via the normal `/do-merge` path; `/update` on each machine regenerates
  `settings.json` from the manifest, wiring the new validator into the running agent's PreToolUse chain.
- If the adaptation branch applies (dispatcher is live), the "update" is adding the predicate to
  `_VALIDATORS` in `dispatch/pre_tool_use_bash.py`; no manifest/settings change beyond what #2453 shipped.

## Agent Integration

- Fires transparently on **every** Bash call in this repo via the project PreToolUse/Bash chain,
  regardless of which skill or subagent issues the command (same posture as
  `validate_no_raw_redis_delete.py`) — this is what makes it robust against a future stage or subagent
  reaching for the destructive shortcut.
- The block is advisory-with-teeth: it returns a `{"decision": "block", …}` the harness surfaces to the
  agent with the corrective alternative (`git worktree add --detach`), so the agent self-routes to a
  disposable baseline worktree exactly as the near-miss subagent eventually did.
- Zero coordination cost: no inter-agent locking, no approval step, no waiting. It only ever fails a
  command that was already unsafe in the shared checkout.
- The `# allow-destructive-git` escape hatch keeps a deliberate, informed override one token away.

## Test Impact

New unit test module: `tests/unit/test_validate_no_destructive_git_in_shared_checkout.py` (mirror the
existing `tests/unit/` coverage for the worktree validator). Cases, one per Success Criterion:

- BLOCK: each of the five whole-tree shapes with effective cwd = shared main checkout root.
- ALLOW (build-failing if it blocks): each of the five shapes with effective cwd inside
  `.worktrees/{slug}/`, both directly and via a `cd .worktrees/slug &&` prefix.
- ALLOW: path-scoped variants in the shared checkout (`checkout <ref> -- file`, `restore path/`,
  `checkout -- file`).
- ALLOW: command-position false-positive guards (`git commit -m "reset --hard …"`, subcommand as
  message/arg text).
- ALLOW (**build-failing if it blocks** — this is the exact case that catches a B1 over-fire regression):
  effective cwd is a *foreign* git repo's toplevel (a git repo that is not this repo's root) → must
  fail-open. List this as its own case, not lumped with non-repo cwd.
- ALLOW: non-repo cwd (fail-open).
- ALLOW: `# allow-destructive-git` override on an otherwise-blocked command.
- Fail-open: malformed hook JSON, unparseable command, git error → allow.
- Shared-helper: a test asserting both validators import the same shape predicate (guards against future
  drift).

Because classification is injected via the pure `find_violation(command, cwd, *, repo_root,
in_worktree)` seam (see Technical Approach B2 fix), the BLOCK / ALLOW-in-worktree / ALLOW-foreign-repo
cases are exercised **deterministically** by passing `repo_root`/`in_worktree` directly — no throwaway
git repos, which the positive-identity bind would not recognize as "this repo" anyway. A separate,
thinner smoke test may exercise `find_violation_from_hook_input` against a real temp layout for the
resolver wrapper only.

Registration/regen guard: extend the manifest/settings regeneration test (post-#2453
`tests/unit/test_hook_manifest.py` / the `generate_project_hooks` coverage) to assert the new entry
appears in the regenerated `(PreToolUse, Bash)` block, so an out-of-sync `settings.json` fails CI.

Run only the targeted new/affected unit tests (narrow-scope; do not run the full suite from a worktree).

**Dispositions:**

- [ ] `tests/unit/test_validate_no_destructive_git_in_shared_checkout.py` — REPLACE (net-new module):
  full case matrix above, one case per Success Criterion.
- [ ] `tests/unit/test_hook_manifest.py` (and/or the `generate_project_hooks` coverage) — UPDATE: extend
  to assert the new `(PreToolUse, Bash)` entry is present in the regenerated `settings.json` block.
- [ ] `tests/unit/test_validate_no_destructive_git_in_worktree.py` — UPDATE only if the shared-shape
  extraction changes its import path; its behavioral assertions must otherwise pass **unmodified** (the
  sibling's behavior is unchanged — only its private shape-detection is lifted into a shared helper it
  re-imports). Treat any behavioral regression there as a build failure.

No other existing tests are affected: this adds a net-new project-scope validator on a surface no
current test exercises, and touches no runtime code outside the two hook validators.

## No-Gos (Out of Scope)

- **Provisioning the `_merge_baseline_main` worktree up front in `/do-merge`.** The issue and the owner
  both mark this "optional polish, not required scope." Deferred.
- **Changing the sibling worktree validator's behavior** (its dirty-tree gate, its `.worktrees/`
  semantics, its blocked-shape set) beyond lifting the shared shape-detection into a common helper it
  re-imports. Deferred.
- **Broadening the deny-list** beyond the five whole-tree shapes, or adding any path-scoped blocking.
  Deferred — the narrow deny-list is a hard design constraint from the owner.
- **Wiring or activating the dormant `dispatch/pre_tool_use_bash.py` dispatcher.** That belongs to
  #2453's scope, not this guardrail; this plan only adapts to whichever registration surface #2453
  leaves live at merge.
- **Correcting #2453's aspirational-but-false hook docs** (`hook-manifest.md` /
  `hooks-best-practices.md` claim a single dispatcher entry that is not actually wired). Out of this
  appetite; noted here so the build stage does not follow that prose over the merged wiring.

## Documentation

### Feature Documentation
- [ ] Update the existing feature doc that covers the #2137 worktree guard — verified to be
  `docs/features/session-isolation.md` (its "Uncommitted-Work Preservation & Destructive-Git Guard"
  section) — to document the new shared-checkout sibling alongside it: the two together cover both
  directions (destructive git inside a dirty worktree vs. whole-tree destructive git in the shared main
  checkout). Also update `docs/features/hook-manifest.md`'s dispatcher predicate list/count, since the
  new validator registers as an eighth `_VALIDATORS` entry there. Prefer extending these existing pages
  over creating a near-duplicate new file.
- [ ] If no such page exists, add a short `docs/features/destructive-git-guardrails.md` covering both
  validators and add an entry to `docs/features/README.md` index table.
- [ ] Note the `# allow-destructive-git` override token and the `git worktree add --detach` alternative
  in whichever page documents the guard.

### External Documentation Site
- Not applicable — this repo has no external Sphinx/MkDocs/RTD site; feature docs live in `docs/`.

### Inline Documentation
- [ ] Module docstring on the new validator mirroring the sibling's (blocked shapes, allowed shapes,
  fail-open contract, the `.worktrees/`-never-fires inversion, direct/manual invocation).
- [ ] Docstring on the extracted shared-shape helper module noting both validators are its consumers.

Use the `documentarian` agent for these tasks during the DOCS stage.

## Risks & Coordination

- **Lane collision (medium).** PR #2453 (#2435) edits `validate_no_destructive_git_in_worktree.py` — the
  file this plan extracts shared shape-detection from. Mitigation: build is gated on #2453 merging; base
  the extraction on the merged version. Because #2448's *new* files (the new validator + its test) don't
  exist elsewhere, the only real overlap is the one-line extraction edit to the sibling and the
  `manifest.toml` append — both small and post-merge.
- **Lane 1 (#2446/#2451 marker-write path) and Lane 2 (#2447 sdlc-tool verdict/marker persistence).**
  Those touch SDLC marker/verdict persistence, a different surface from the Bash PreToolUse validator.
  No file overlap identified with this guardrail. No coordination needed beyond awareness.
- **Dispatcher-wiring uncertainty (medium — see "Build is gated" section).** The single biggest planning
  risk is building to the wrong registration surface. The plan pins the live surface (manifest +
  individual validator) and carries an explicit adaptation branch if #2453 wires the dispatcher before
  merge. Resolve by re-checking the merged `manifest.toml` / `dispatch/pre_tool_use_bash.py` wiring as
  the first build step.
- **Over-aggressive shared-checkout rule (low).** Blocking unconditionally (no dirty-gate) could annoy a
  solo agent doing a legitimate whole-tree reset on the shared checkout. Mitigated by the
  `# allow-destructive-git` override and the corrective error message. The issue's near-miss justifies
  the stricter default.
