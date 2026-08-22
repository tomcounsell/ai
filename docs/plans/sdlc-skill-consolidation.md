---
status: Ready
type: refactor
appetite: Small
owner: Valor Engels
created: 2026-08-22
tracking: https://github.com/tomcounsell/ai/issues/2930
---

# Consolidate the SDLC skills: one substantive `do-sdlc` body + a thin `/sdlc` router shim

Two SDLC skills currently overlap: `.claude/skills/sdlc/SKILL.md` (project-only single-stage
router, 307 lines) and `.claude/skills-global/do-sdlc/SKILL.md` (global supervisor loop,
341 lines). Both teach resolve/assess/dispatch steps and both carry unscoped `gh issue view`
forms (the #2889/#2925 bug class). The user directed: **"migrate all sdlc skill into the
do-sdlc skill so we don't have 2 skills"** (issue #2930), with the shape confirmed: one
substantive body + a thin router shim.

## Why the shim, not a true single file

`/sdlc` is the bridge PM's production contract — "dispatch ONE stage, return; the PM session
re-invokes" — and there is no `.claude/commands/sdlc.md`: the `sdlc` skill *is* the slash
command. Deleting `.claude/skills/sdlc/` outright would break the bridge PM's per-stage
re-invocation until it is retrained, which is out of scope for a hotfix round. The shim
preserves the entry point; the substantive body moves to `do-sdlc`.

`do-sdlc` is global and hard-linked to every machine via `/update` (`scripts/update/hardlinks.py`).
Repo-coupled content (worktree manager, `lane_identity`, `sdlc-tool` invocation details,
`SDLC_TARGET_REPO` semantics, `docs/sdlc/*` pointers) therefore layers into the existing
`docs/sdlc/do-sdlc.md` context file per the skill-context convention — never into the global
body itself.

## Changes

1. **`.claude/skills-global/do-sdlc/SKILL.md`** — absorb the router's Step 1–4 content as its
   own steps (Resolve → Ensure the Tracking Session → Assess → Dispatch ONE stage), keeping
   the existing supervisor loop (3a–3e), Hard Rules, and the Stage→Model table. Generic
   parts (what `/sdlc` does, the guard table's meaning, dispatch-shape JSON) stay in the
   global body; repo-coupled specifics move to `docs/sdlc/do-sdlc.md`. Add the
   `--repo`-when-known pattern to the `gh issue view` / `gh pr view` forms.
2. **`.claude/skills/sdlc/SKILL.md`** — reduce to a thin `context: fork` router shim: assess
   current state (`sdlc-tool stage-query` → `next-skill`), dispatch ONE stage, record the
   dispatch, return. Point at the merged body's step content instead of duplicating it.
   Scope the `gh` forms with `--repo`-when-known.
3. **`docs/sdlc/do-sdlc.md`** — add the moved repo-coupled sections (worktree/branch
   ownership, `sdlc-tool` command discipline, `SDLC_TARGET_REPO`/`GH_REPO` semantics,
   `docs/features/sdlc-*` pointers).
4. **`CLAUDE.md`** — update the "Some skills are too coupled to generalize even with a probe:
   `setup`, `prime`, `sdlc`, and `do-deploy` stay project-only" line: `sdlc` is now a thin
   shim over the global `do-sdlc`; keep `setup`/`prime`/`do-deploy` as project-only.
5. **`scripts/update/hardlinks.py`** — add a `RENAMED_REMOVALS` entry for the removed
   `skills/sdlc` directory (the shim replaces it; no stale hardlink may survive on other
   machines, and repo-local husk residue is swept by the same entry).

## Test plan

- Skill bodies are markdown; no unit tests map. Verify by reading: the merged body's step
  flow, the shim's one-turn contract, and that every `gh issue view` / `gh pr view` form
  carries `--repo`-when-known or a GH_REPO note.
- `python -m ruff check` on touched `.py` (hardlinks.py) if convenient; not required for .md.
- Post-merge: `/update` dry run to confirm the hardlink sweep behaves (no machine other than
  this checkout needs to change for the PR itself).

## Disposition

Closes #2930.
