# Skill Context — per-repo nuances for global skills

This directory is the **discoverable seam** that lets a single global skill stay lean
everywhere and get richer only where a repo opts in.

Skills under `.claude/skills-global/` are hardlinked into `~/.claude/skills/` on every
machine (by `scripts/update/hardlinks.py::sync_claude_dirs`), so they load and run in
**every** repo the user opens — not just this one. Their bodies are therefore written as a
**generic baseline** that depends on nothing more than `git` (and optionally `gh`). Any
repo-specific behavior lives here, in a per-skill context file, rather than baked into the
shared skill body.

## The contract

> **Absent file ⇒ lean generic behavior. Present file ⇒ rich repo behavior.**

A global skill body probes for its context file early. If the file is absent (the common
case in a foreign repo), the skill runs its generic baseline. If the file is present (as in
this repo), the skill reads it and layers the declared repo-specifics back in. Nothing breaks
when a repo ships no context files — the convention is purely additive.

## The canonical probe sentence

Every coupled global skill body contains ONE standardized probe step, worded identically
(minus the path) across all skills so it is greppable and enforceable. The exact template:

```
If `<CONTEXT_PATH>` exists, read it and honor its declarations; otherwise use the generic defaults described below.
```

The invariant suffix — `exists, read it and honor its declarations; otherwise use the
generic defaults described below.` — is identical in every skill. Only `<CONTEXT_PATH>`
varies. `rule_13_coupling_signals` in
`.claude/skills-global/audit-skills/scripts/audit_skills.py` greps for this suffix to
confirm that every coupled body carries the probe.

## Which path does a skill probe?

The context path is parameterized by skill type:

| Skill type | Probe path | Why |
|------------|-----------|-----|
| **The 8 SDLC pipeline skills** (`do-build`, `do-docs`, `do-merge`, `do-patch`, `do-plan`, `do-plan-critique`, `do-pr-review`, `do-test`) | `docs/sdlc/{skill}.md` | These already have per-stage addenda under `docs/sdlc/`. That directory **is** their skill-context seam. The probe points directly at it — no thin pointer file is created (avoids a redundant hop). |
| **All other coupled (Bucket B) skills** (media/comms, `do-issue`, etc.) | `.claude/skill-context/{skill}.md` | No pre-existing `docs/sdlc/` addendum, so their repo-specifics live here. |

### `do-docs` — the worked example

`do-docs` is the convention's worked example and the copy-paste template for all
non-SDLC Bucket B skills. Even though `do-docs` is one of the 8 SDLC pipeline skills, its
**operational** ai-specifics (the stage-marker commands, the semantic doc-impact finder, the
auto-fix substrate, the plan-completion marker, this repo's doc inventory locations) are
carried in a rich `.claude/skill-context/do-docs.md` — not a thin pointer. The higher-level
SDLC-pipeline guidance for docs remains in `docs/sdlc/do-docs.md` (referenced, not
duplicated). This makes `do-docs` the cleanest demonstration of the
`.claude/skill-context/{skill}.md` pattern that the other skills copy.

## Relationship to `docs/sdlc/`

`docs/sdlc/{skill}.md` is the **pre-existing** per-skill context seam for the SDLC pipeline
stages, headed `# {skill} addendum — this repo only` and capped at 300 lines. This
`.claude/skill-context/` directory **generalizes that same idea to all global skills**. The
two are not migrated into one another: SDLC skills keep reading `docs/sdlc/`; non-SDLC
skills read `.claude/skill-context/`. Do not move or restructure the `docs/sdlc/*.md` files.

## Not synced

This directory is repo-local. `sync_claude_dirs()` only hardlinks `skills-global/`,
`commands/`, and `hooks/` into `~/.claude/`, so `.claude/skill-context/` never leaves this
repo — exactly like `docs/sdlc/`. That is what makes the "absent file ⇒ generic" contract
hold in foreign repos: they never receive this repo's context files.

## Adding a context file for a new repo

To make a global skill behave richly in some other repo, create
`.claude/skill-context/{skill}.md` (or, for an SDLC skill, `docs/sdlc/{skill}.md`) in that
repo and declare the repo's nuances. The skill body already probes for it — no skill edits
needed.
