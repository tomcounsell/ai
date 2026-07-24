# Skill Context Convention

The `.claude/skill-context/` directory is the discoverable seam that lets a single global skill stay lean everywhere and get richer only where a repo opts in.

Skills under `.claude/skills-global/` are hardlinked into `~/.claude/skills/` on every machine by `scripts/update/hardlinks.py::sync_claude_dirs`, so they load and run in every repo the user opens — not just this one. Their bodies are written as a generic baseline that depends on nothing more than `git` (and optionally `gh`). Repo-specific behavior lives in per-skill context files rather than baked into the shared skill body.

## The Contract

> **Absent file ⇒ lean generic behavior. Present file ⇒ rich repo behavior.**

A global skill body probes for its context file early. If the file is absent (the common case in a foreign repo), the skill runs its generic baseline. If the file is present, the skill reads it and layers the declared repo-specifics back in. Nothing breaks when a repo ships no context files — the convention is purely additive.

## The Canonical Probe Sentence

Every coupled global skill body contains exactly one standardized probe step, worded identically (minus the path) across all skills so it is greppable and enforceable. The exact template:

```
If `<CONTEXT_PATH>` exists, read it and honor its declarations; otherwise use the generic defaults described below.
```

The invariant suffix — `exists, read it and honor its declarations; otherwise use the generic defaults described below.` — is identical in every skill. Only `<CONTEXT_PATH>` varies. The `rule_13_coupling_signals` audit guard greps for this suffix to confirm every coupled body carries the probe.

## Which Path Does a Skill Probe?

The context path depends on skill type:

| Skill type | Probe path | Why |
|------------|------------|-----|
| **The 8 SDLC pipeline skills** (`do-build`, `do-docs`, `do-merge`, `do-patch`, `do-plan`, `do-plan-critique`, `do-pr-review`, `do-test`) | `docs/sdlc/{skill}.md` | These already have per-stage addenda under `docs/sdlc/`. That directory is their skill-context seam. The probe points directly at it — no thin pointer file is created (avoids a redundant hop). |
| **All other coupled (Bucket B) skills** (media/comms, `do-issue`, etc.) | `.claude/skill-context/{skill}.md` | No pre-existing `docs/sdlc/` addendum, so their repo-specifics live here. |

### do-docs — the worked example

`do-docs` is the convention's worked example and the copy-paste template for all non-SDLC Bucket B skills. Even though `do-docs` is one of the 8 SDLC pipeline skills, its operational ai-specifics (the stage-marker commands, the semantic doc-impact finder, the auto-fix substrate, the plan-completion marker, this repo's doc inventory locations) are carried in a rich `.claude/skill-context/do-docs.md` — not a thin pointer. The higher-level SDLC-pipeline guidance for docs remains in `docs/sdlc/do-docs.md` (referenced, not duplicated). This makes `do-docs` the cleanest demonstration of the `.claude/skill-context/{skill}.md` pattern that other skills copy.

## Relationship to docs/sdlc/

`docs/sdlc/{skill}.md` is the pre-existing per-skill context seam for the SDLC pipeline stages, headed `# {skill} addendum — this repo only` and capped at 300 lines. The `.claude/skill-context/` directory generalizes that same idea to all global skills. The two are not migrated into one another: SDLC skills keep reading `docs/sdlc/`; non-SDLC skills read `.claude/skill-context/`. Do not move or restructure the `docs/sdlc/*.md` files.

## How a New Repo Opts In

To make a global skill behave richly in some other repo, create `.claude/skill-context/{skill}.md` (or, for an SDLC skill, `docs/sdlc/{skill}.md`) in that repo and declare the repo's nuances. The skill body already probes for it — no skill edits needed.

The file format is freeform markdown. Use section headers aligned to the numbered steps in the global `SKILL.md`, so readers can cross-reference. See `.claude/skill-context/do-docs.md` for a complete example.

## Not Synced

`.claude/skill-context/` is repo-local. `sync_claude_dirs()` only hardlinks `skills-global/`, `commands/`, and `hooks/` into `~/.claude/`, so `.claude/skill-context/` never leaves this repo — exactly like `docs/sdlc/`. That is what makes the "absent file ⇒ generic" contract hold in foreign repos: they never receive this repo's context files.

## The rule_13_coupling_signals Audit Guard

`rule_13_coupling_signals` in `.claude/skills-global/audit-skills/scripts/audit_skills.py` enforces the convention:

- It scans every global skill body for **coupling signals** — executable or import references that actually error or silently misfire in a foreign repo: `sdlc-tool`, `python -m tools.*`, `reflections.*`, `valor-*`, `config/identity.json`.
- If any signal is found and the body does NOT contain the canonical probe suffix, the rule emits a `FAIL` finding. A `FAIL` causes the audit's `main()` to exit non-zero, blocking CI.
- If any signal is found AND the probe suffix is present, the rule emits `PASS` — the body correctly defers to the skill-context seam.
- If no coupling signals are found, the rule emits `PASS` unconditionally (the skill is already generic).

Weak doc-path or branch-name mentions (`docs/features/`, `docs/plans/`, `session/{slug}`) are deliberately excluded from the signal set. A see-also markdown link does not break execution in a foreign repo.

## The rule_21_bucket_c_coupling Audit Guard (issue #2079)

`rule_21_bucket_c_coupling` is rule 13's stricter sibling. It catches the two signal classes that shipped straight past rule 13 (the five leaks hand-fixed in `61b55ce7`):

- **Signal A — Bucket-C slash-invocations.** A slash token (e.g. `` `/sdlc` ``, `` `/setup` ``, or a bare `/sdlc` outside backticks — the regex scan runs over the whole fence-stripped line with no backtick gating) whose **full captured token** exactly matches a project-only skill name. The name set is derived live from the repo's `.claude/skills/` directory listing — never hardcoded — so the guard is repo-agnostic: a foreign repo with no project-only root yields an empty set and Signal A never fires. Full-token matching is hyphen-safe on both edges: `` `/do-deploy-example` `` (a global skill) never matches `do-deploy`, and `` `/sdlc` `` never matches inside `` `/do-sdlc` ``.
- **Signal B — curated infra tokens.** `sdk_client.py` and `SDLC_TARGET_REPO` — repo-specific filenames/env-vars in the same curated style as `COUPLING_SIGNALS`.

**Escape hatch — same physical line only.** A match is covered when the same line carries conditional framing: `in this repo`, `this repo's` (case-insensitive), or the canonical probe sentence. A whole-file probe does **not** cover a bare reference elsewhere — two of the `61b55ce7` leaked skills carried the probe and leaked anyway, which is exactly why rule 21 is line-scoped where rule 13 is file-scoped.

**Scan surface.** Frontmatter-stripped body only; fenced code blocks are skipped (a token inside a fence is a usage demonstration that cannot carry same-line prose framing). An unclosed fence extends to end-of-file, matching CommonMark rendering.

## Sub-File Scanning and Self-Exemption

Both coupling rules (13 and 21) scan **every `*.md` sub-file** under a global skill dir, not just `SKILL.md` — sub-files hardlink to every machine too. Probe/conditional coverage for rule 13 is read from `SKILL.md`: a planted coupling token in a `CHECKS.md` is covered only when the parent `SKILL.md` carries the probe. Non-`.md` files (scripts, `.py`) are excluded so the audit script's own signal-token literals are never self-flagged. The `audit-skills` skill itself is self-exempt — its rule-inventory docs describe the very signals the rules match.

## Bucket C Skills (Project-Only)

Some skills are too tightly coupled to this repo's infrastructure to generalize at all. Rather than carrying a probe step that points at a context file with every behavioral detail, these skills were moved from `.claude/skills-global/` to the project-only `.claude/skills/` directory (issue #1783):

| Skill | Reason for project-only placement |
|-------|-----------------------------------|
| `setup` | Configures this repo's specific bridge, launchd plists, and vault paths |
| `prime` | Onboards developers into this repo's exact architecture |
| `sdlc` | SDLC router tightly coupled to this repo's stage model and pipeline state |
| `do-deploy` | Deploys to this repo's specific bridge machines |

`do-sdlc` stays in `.claude/skills-global/` deliberately: it drives the pipeline entirely through `sdlc-tool` (synced to `~/.local/bin` on every machine), `gh`, `git`, and the global `do-*` stage skills — it never invokes the project-only `/sdlc` router skill. Its probe seam is `docs/sdlc/do-sdlc.md`.

Skills in `.claude/skills/` are never synced. Moving a skill from `skills-global/` to `skills/` requires a `RENAMED_REMOVALS` entry in `scripts/update/hardlinks.py` so the stale hardlink is cleaned up on every machine.

## See Also

- `.claude/skill-context/README.md` — Full convention reference with authoring details
- `.claude/skill-context/do-docs.md` — Worked example: the richest context file in this repo
- `docs/features/sdlc-repo-addenda.md` — The pre-existing `docs/sdlc/` seam this convention generalizes
- `docs/features/skills-global.md` — Global skill library overview and sync mechanism
- `.claude/skills-global/audit-skills/scripts/audit_skills.py` — `rule_13_coupling_signals` and `rule_21_bucket_c_coupling` implementations
