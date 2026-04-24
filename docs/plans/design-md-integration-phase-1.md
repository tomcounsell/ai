---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1162
last_comment_id:
---

# DESIGN.md Integration on top of `.pen` Ground Truth (Phase 1)

## Problem

The `do-design-system` skill currently drives design-system changes through a hand-written pipeline: the agent reads `docs/designs/design-system.pen`, manually mirrors tokens into `brand.css` and Tailwind's `source.css`, and hand-composes a variables/components changelog table in `docs/designs/gap-audit.md` each moodboard pass. Four gaps result:

- **No validation.** Broken token references, WCAG contrast misses, orphaned tokens, and missing primaries are caught by eyeball or not at all. `SKILL.md:417-418` explicitly warns that token names "MUST match `brand.css` exactly, or the system diverges silently" — there is no tool enforcing that mirror.
- **CSS sync is hand-written.** Step 6 of the skill instructs the agent to retype tokens from `.pen` into two separate CSS files. Typos and missed edits are invisible until a template breaks.
- **Gap-audit tables are hand-written.** Step 7 asks the agent to hand-compose a dated changelog of variable/component changes. Drift between passes is invisible until a human notices.
- **No ecosystem export.** `.pen` tokens cannot be consumed by Figma, Style Dictionary, or Tailwind tooling without bespoke conversion.

**Current behavior:** Agent edits `.pen` → agent manually retypes into `brand.css` → agent manually retypes into `source.css` → agent manually drafts changelog table. No linter runs. No drift check fires at commit time.

**Desired outcome:** `.pen` stays the only human-editable file. A deterministic generator emits `design-system.md` (DESIGN.md-compliant), `brand.css`, `source.css`, and DTCG / Tailwind exports from it. `@google/design.md lint` runs against the emitted DESIGN.md; `@google/design.md diff` drives the gap-audit changelog. A pre-commit validator fails commits that leave the derived artifacts out of sync with `.pen`.

## Freshness Check

**Baseline commit:** `75f613b8` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-04-24T08:43:45Z (same day as plan)
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills/do-design-system/SKILL.md` (Steps 6 & 7, safety gate at L314-322) — still holds. File mtime 2026-04-23, unchanged since issue filed.
- `docs/designs/` — confirmed empty except for `SKILL.md` and `charter-template.md`. No `.pen` file, no CSS artifacts, no `gap-audit.md`. Seed creation is a genuine prerequisite, not a legacy-file cleanup.

**Cited sibling issues/PRs re-checked:** None cited in issue body.

**Commits on main since issue was filed (touching referenced files):** None. Issue and plan share the same day.

**Active plans in `docs/plans/` overlapping this area:** None. No other plan touches `docs/designs/` or `do-design-system`.

**Notes:** No drift. Plan proceeds on the issue's premises as written.

## Prior Art

- **No prior issues or PRs found** targeting `docs/designs/`, DESIGN.md, `.pen` tooling, `brand.css`, or the `do-design-system` skill automation surface. `gh issue list --state closed --search "design-system"` and `gh pr list --state merged --search "design.md"` both return empty.
- **Reference implementation:** Commit `a702484` on `yudame/cuttlefish` (2026-04-20, moodboard pass cited in `SKILL.md:487-496`) produced `.pen` + hand-written `brand.css` / `source.css` without any linter. That pass is the closest precedent — Phase 1 automates exactly the hand-work that commit did manually.

## Research

**Queries used:**
- `@google/design.md npm package version latest 2026`
- `google-labs-code design.md github README linting rules DTCG export`
- `design.md YAML frontmatter schema colors typography rounded spacing components google-labs-code`
- `npm view @google/design.md versions` (authoritative source for pinning)

**Key findings:**
- **Latest published version on npm:** `@google/design.md@0.1.1` (versions `0.1.0`, `0.1.1` — `npm view @google/design.md versions --json`, verified 2026-04-24). Pin to **`0.1.1`** exactly. The spec header field is `version: alpha`; the npm package uses semver separately.
- **CLI surface:** `lint`, `diff`, `export` (formats: `tailwind`, `dtcg`), `spec`. Linter exits 1 on errors, 0 otherwise. Eight linting rules documented in issue: `broken-ref`, `missing-primary`, `contrast-ratio`, `orphaned-tokens`, `token-summary`, `missing-sections`, `missing-typography`, `section-order`.
- **YAML schema:** Frontmatter between `---` fences. Required top-level keys: `version`, `name`, `colors`, `typography`, `rounded`, `spacing`, `components`. Optional: `description`. Cross-references use `{path.to.token}` syntax (e.g. `{colors.primary.500}`). Colors are `#RRGGBB` hex in sRGB. `fontWeight` is numeric (400/700). `lineHeight` is either Dimension (`24px`, `1.5rem`) or unitless number (`1.6`).
- **Component properties:** `backgroundColor`, `textColor`, `typography`, `rounded`, `padding`, `size`, `height`, `width` — fixed list.
- **Format status:** Apache 2.0, released 2026-04-21 by Google Labs. Format is explicitly `alpha`; spec, token schema, and CLI are under active development. **Re-running the generator against a new format version within ~6 months is expected.**
- **Node availability on this machine:** `node v25.9.0`, `npm 11.12.1`, `npx` at `/opt/homebrew/bin/npx` — confirmed via `which npx`. `npx @google/design.md` is a valid invocation path; no global install required.

No new Python libraries needed; `.pen` is plain JSON (stdlib `json`), YAML emission uses the repo's existing `pyyaml` dep (already in `requirements.txt`), CSS emission is string formatting.

Sources:
- [google-labs-code/design.md — GitHub](https://github.com/google-labs-code/design.md)
- [design.md spec.md](https://github.com/google-labs-code/design.md/blob/main/docs/spec.md)
- [Google Blog announcement (2026-04-21)](https://blog.google/innovation-and-ai/models-and-research/google-labs/stitch-design-md/)

## Spike Results

### spike-1: npm package version pinnable today?
- **Assumption:** "`@google/design.md` has a stable published version we can pin."
- **Method:** `npm view @google/design.md versions --json` → returned `["0.1.0", "0.1.1"]`; `npm view @google/design.md version` → `0.1.1`.
- **Finding:** Pinnable. Pin to `0.1.1`.
- **Confidence:** High.
- **Impact on plan:** Solution section pins `@google/design.md@0.1.1` in `package.json` at `devDependencies` (Node-only, invoked via `npx`).

### spike-2: Does the repo already have a Node toolchain we're layering onto?
- **Assumption:** "Need to create `package.json` from scratch."
- **Method:** `ls` repo root for `package.json`.
- **Finding:** Repo has no `package.json` at root; no Node toolchain established. Plan must introduce one scoped to design tooling.
- **Confidence:** High.
- **Impact on plan:** Solution adds a minimal `package.json` at repo root containing only `@google/design.md@0.1.1` in `devDependencies`, plus a `package-lock.json` for reproducibility. `.gitignore` entry for `node_modules/` added if missing. No `npm run` scripts — the generator invokes `npx` directly.

### spike-3: Is there a .pen file today to design the generator against?
- **Assumption:** "We can sample an existing `.pen` to design the mapping."
- **Method:** `ls docs/designs/`.
- **Finding:** Only `SKILL.md` and `charter-template.md`. No `.pen` file, no `brand.css`, no `source.css`, no `gap-audit.md`. Phase 1 must create a seed `.pen` as a genuine prerequisite — the generator spec is derived from the skill's documented conventions (tier-aware naming, `Category/Variant` components, `$--name` variable refs, `reusable: true` component frames) rather than an existing file sample.
- **Confidence:** High.
- **Impact on plan:** First build task is **seed `design-system.pen`** (minimal — 3 colors, 2 typography presets, 1 radius, spacing scale, 1 component). Mapping spec and generator are built against the seed. Design invariants come from `SKILL.md`, not from reverse-engineering a sample.

### spike-4: Pre-commit hook vs. PostToolUse validator — which fits this repo's pattern?
- **Assumption:** "Other validators in this repo are registered as PostToolUse hooks."
- **Method:** Read `.claude/settings.json` hook registrations and `ls .claude/hooks/validators/`.
- **Finding:** The repo's native drift-detection pattern is a Python validator under `.claude/hooks/validators/` wired into `PostToolUse` or `PreToolUse` blocks in `.claude/settings.json` (e.g. `validate_no_raw_redis_delete.py`, `validate_file_contains.py`, `validate_features_readme_sort.py`). Git `pre-commit` hooks are not used for validator enforcement — the Claude Code hook surface is the enforcement layer for agent-driven edits.
- **Confidence:** High.
- **Impact on plan:** Drift-detection mechanism is implemented as `python` validator at `.claude/hooks/validators/validate_design_system_sync.py`, registered as a **PreToolUse** hook on `Bash` (catching `git commit` / `git add` commands touching `docs/designs/`) **and** as a standalone CLI entrypoint runnable via `python -m tools.design_system_sync --check`. This gives us both agent-path enforcement and a CLI path for human/CI verification. A git `pre-commit` hook (optional, documented in the feature doc) can invoke the CLI for non-agent edits.

## Data Flow

```
Human edits design-system.pen in Pencil desktop app
        │
        ▼
docs/designs/design-system.pen        (JSON — canonical ground truth)
        │
        ▼  python -m tools.design_system_sync --generate
┌───────┴─────────────────────────────────────────────────────────┐
│ Generator (deterministic, idempotent)                           │
│   1. Load .pen JSON                                             │
│   2. Extract variables → categorize (colors / typography /      │
│      rounded / spacing)                                         │
│   3. Extract reusable components → map to DESIGN.md components  │
│      schema                                                     │
│   4. Emit design-system.md YAML frontmatter + markdown body     │
│   5. Emit brand.css (:root {} with --vars)                      │
│   6. Emit source.css (@theme {} for Tailwind)                   │
│   7. Exec `npx @google/design.md export --format dtcg` →        │
│      exports/tokens.dtcg.json                                   │
│   8. Exec `npx @google/design.md export --format tailwind` →    │
│      exports/tailwind.theme.json                                │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────────┘
       ▼          ▼          ▼          ▼          ▼
docs/designs/  brand.css  source.css  exports/   exports/
design-system.md                      tokens.    tailwind.
                                      dtcg.json  theme.json
       │
       ▼  npx @google/design.md lint docs/designs/design-system.md
Lint exit 0 required
       │
       ▼  (commit)
       │
       ▼  PreToolUse hook on Bash (git commit / git add)
validate_design_system_sync.py
  → re-runs generator to tempdir
  → diffs against working-tree artifacts
  → exit 1 if any artifact drifted
       │
       ▼
git commit proceeds
```

**Gap-audit diff flow (separate from commit-time drift check):**

```
Previous pass's design-system.md (from git show HEAD~1:...)
           │
           ▼  npx @google/design.md diff <prev> <current>
Diff output (JSON or markdown)
           │
           ▼  reformatter (tools/design_system_sync.py --audit)
Markdown table — Variables changed / Components added
           │
           ▼
Appended to docs/designs/gap-audit.md under ## YYYY-MM-DD — <theme>
```

## Appetite

**Size:** Medium

**Team:** Solo dev (Valor). Design system skill is single-owner; no PM handoff.

**Interactions:**
- PM check-ins: 1-2 (schema mapping review, drift-validator review)
- Review rounds: 1 (one PR, one review pass — no staged rollout)

Medium because the work has three distinct moving parts (generator, lint/diff/export pipeline, drift validator) and rewrites the `do-design-system` skill's Steps 6 & 7 in place. Each piece is mechanical; the coordination cost is the bulk of the effort.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Node + npx | `command -v npx >/dev/null` | Invoke `@google/design.md` CLI |
| `@google/design.md@0.1.1` installed | `npx --no-install @google/design.md --version \| grep -q 0.1.1` | Pinned DESIGN.md CLI available |
| Existing `pyyaml` in requirements | `python -c "import yaml"` | YAML emission (no new Python dep) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/design-md-integration-phase-1.md`

## Solution

### Key Elements

- **Seed `docs/designs/design-system.pen`** — minimal, Pencil-openable JSON with a documented set of tokens and one reusable component. Serves as the generator's test fixture and the skill's first-pass starting point.
- **`tools/design_system_sync.py`** — plain Python CLI module (no new runtime deps). Reads `.pen` JSON, emits `design-system.md`, `brand.css`, `source.css`, and shells out to `npx @google/design.md export` for DTCG / Tailwind exports. Deterministic — running twice produces byte-identical output. Entrypoints: `--generate`, `--check`, `--audit` (diff for gap-audit), `--all` (generate + lint + export).
- **`.pen` → DESIGN.md YAML mapping spec** — captured in `docs/features/design-system-tooling.md`. Defines how Pencil variable names categorize into `colors` / `typography` / `rounded` / `spacing` and how reusable component frames map to DESIGN.md `components` entries. See **Schema Mapping** section below.
- **`package.json`** — minimal, repo-root, pins `@google/design.md@0.1.1` only. `package-lock.json` committed. `node_modules/` added to `.gitignore`.
- **Drift-detection validator** — `.claude/hooks/validators/validate_design_system_sync.py`. PreToolUse hook fires on `git commit` / `git add docs/designs/*` Bash commands; re-runs the generator to a tempdir, byte-compares each emitted artifact against the working-tree copy, blocks commit on mismatch. Same script runnable as `python -m tools.design_system_sync --check` for CI or manual verification.
- **`do-design-system` skill rewrite** — Steps 6 & 7 replaced:
  - Step 6 becomes: "Run `python -m tools.design_system_sync --all`. Verify `npx @google/design.md lint docs/designs/design-system.md` exits 0. Do not hand-edit `brand.css`, `source.css`, or `design-system.md`."
  - Step 7 becomes: "Run `python -m tools.design_system_sync --audit` to produce the variables/components diff table. Append to `gap-audit.md` under `## YYYY-MM-DD — <theme>`."
  - Existing safety gate (SKILL.md L314-322) extended: reject direct Write/Edit to `design-system.md`, `brand.css`, `source.css`, `docs/designs/exports/**` — those flow through the generator only. The `.pen` whitelist (only `design-system.pen`) is preserved verbatim.
- **Feature doc** — `docs/features/design-system-tooling.md` documents the one-way pipeline, schema mapping, regenerate-locally workflow, and drift validator semantics.

### `.pen` → DESIGN.md YAML Mapping Spec

This mapping is the load-bearing part of the generator. The `.pen` schema is free-form JSON (authored in Pencil); DESIGN.md is opinionated. The generator bridges them by convention:

**Variable naming convention (`.pen` → DESIGN.md category):**

The `do-design-system` skill already enforces tier-aware, semantically-prefixed variable names (`SKILL.md` L288-299). The generator keys on the prefix:

| `.pen` variable prefix | DESIGN.md category | Example |
|---|---|---|
| `--color-*`, `--accent`, `--status-*`, `--surface-*`, `--text-*`, `--border-*` | `colors` | `--status-operational` → `colors.status.operational` |
| `--font-*`, `--text-size-*`, `--text-weight-*`, `--text-lh-*` | `typography` | `--font-serif` → `typography.serif.fontFamily` |
| `--radius-*`, `--rounded-*` | `rounded` | `--radius-md` → `rounded.md` |
| `--space-*`, `--gap-*`, `--pad-*` | `spacing` | `--space-lg` → `spacing.lg` |
| Any other prefix | Dropped with a lint warning from the generator (surfaced to the skill as "unmapped variable — rename to match a known prefix or add to `--drop-unmapped` allowlist") |

**Typography aggregation:** Typography in DESIGN.md is structured (font family + weight + size + line-height per named preset). `.pen` tends to have flat tokens. The generator aggregates by suffix:
- `--font-{name}` → `typography.{name}.fontFamily`
- `--text-size-{name}` → `typography.{name}.fontSize`
- `--text-weight-{name}` → `typography.{name}.fontWeight`
- `--text-lh-{name}` → `typography.{name}.lineHeight`

Presets with missing properties inherit from a `base` preset defined in the seed `.pen`. If `base` is missing, the generator emits a hard error (caught by `--check` and by the lint rule `missing-typography`).

**Component mapping:** Pencil components with `"reusable": true` and `name: "Category/Variant"` map into DESIGN.md `components`:
- Component key = lowercase slugified `Category-Variant` (e.g. `Annotation/Crosshair` → `annotation-crosshair`).
- DESIGN.md component properties populated from Pencil children: first child with a `fill` variable ref → `backgroundColor`; first text child → `textColor` + `typography`; any `radius` attribute → `rounded`; width/height → `width`/`height`; explicit `padding` attribute → `padding`.
- Non-reusable frames, layout nodes, and the `variables` frame are excluded. Frames without a `Category/Variant` name are excluded with a generator warning.

**Reference syntax:** `.pen` uses `$--name` for variable refs. The generator converts to DESIGN.md's `{colors.x.y}` syntax at emission time. Round-tripping is one-way; DESIGN.md never feeds back into `.pen`.

**Determinism guarantees:**
- Variables, components, and token categories are emitted in **sorted order** (alphabetical by key).
- Color hex values are emitted uppercase with `#RRGGBB` (6-hex, no alpha in Phase 1).
- Numbers are emitted without trailing zeros (1, not 1.0).
- YAML frontmatter uses block style (not flow), double-quoted strings, explicit `---` fences with single trailing newline.
- CSS output: one declaration per line, 2-space indent, `:` followed by single space, trailing semicolon, alphabetical within block, blank line between `:root` and `@theme` sections.

### Flow

**Ground truth path:** Pencil desktop app → edit `docs/designs/design-system.pen` → save → `python -m tools.design_system_sync --all` → generator writes artifacts + lints + exports → `git add docs/designs/ brand.css source.css` → commit (drift validator re-checks) → push.

**Moodboard pass path (via do-design-system skill):**
Moodboard URL → agent runs skill → agent edits `design-system.pen` directly (existing Step 5 path) → `python -m tools.design_system_sync --all` (new Step 6) → `python -m tools.design_system_sync --audit` (new Step 7) → skill appends audit table to `gap-audit.md` → commit → drift validator passes.

### Technical Approach

- **Single CLI module** at `tools/design_system_sync.py` exposes `--generate`, `--check`, `--audit`, `--all`. `python -m tools.design_system_sync` is the canonical invocation (matches existing repo tool-module conventions: `python -m tools.analytics`, `python -m tools.memory_search`).
- **stdlib-only Python:** `json`, `pathlib`, `subprocess`, `sys`, `argparse`, `yaml` (already in `requirements.txt`). No new Python runtime deps.
- **Shell-out to `npx`** for DESIGN.md CLI invocations — lint, diff, export. `subprocess.run(["npx", "--no-install", "@google/design.md", ...], check=True, capture_output=True, text=True)`. `--no-install` forces use of the pinned local version.
- **Determinism via canonical emission:** helper `_emit_yaml()` uses `yaml.safe_dump(..., sort_keys=True, default_flow_style=False, allow_unicode=True)` + post-processing to normalize spacing. `_emit_css()` sorts keys before writing. Snapshot tests assert byte-identical output on repeated runs.
- **Drift check algorithm:** `--check` mode writes artifacts to a `tempfile.TemporaryDirectory()`, then compares each byte-for-byte against `docs/designs/design-system.md`, downstream `brand.css`, `source.css`, and `docs/designs/exports/*.json` (paths discovered by convention). Diffs printed as unified diff; exit 1 on any mismatch.
- **PreToolUse validator wiring:** `.claude/hooks/validators/validate_design_system_sync.py` reads `$CLAUDE_HOOK_INPUT` JSON (per existing validator pattern), inspects `tool_name == "Bash"` and matches `tool_input.command` against `^(git commit|git add).*docs/designs` or `^(git commit|git add).*brand\\.css` etc. On match, invokes `python -m tools.design_system_sync --check`. Non-zero exit returns `{"decision": "block", "reason": "..."}`. Registration added to `.claude/settings.json` under `hooks.PreToolUse` matcher `Bash` list.
- **Safety gate extension:** `do-design-system` SKILL.md safety-gate block (L314-322) augmented with an assertion that refuses Write/Edit to `design-system.md`, `brand.css`, `source.css`, or `docs/designs/exports/**`. Implementation: the skill's guidance becomes agent-readable, and a companion validator `validate_design_system_readonly.py` enforces it as a PreToolUse hook on `Write`/`Edit` matchers (same pattern as existing `validate_no_raw_redis_delete.py`).
- **CLI-first for the generator:** No MCP wrapper. The skill invokes via Bash. This matches the issue's stated constraint ("The generator should be runnable as a plain CLI … so the skill can invoke it via Bash").
- **Gap-audit diff generator:** `--audit` mode: (a) `git show HEAD:docs/designs/design-system.md > /tmp/prev.md`, (b) `npx @google/design.md diff /tmp/prev.md docs/designs/design-system.md` captures structured diff, (c) Python reformats into the markdown tables the existing `gap-audit.md` format expects (`### Variables` and `### New components`), (d) prints to stdout for the skill to paste. If `git show HEAD:...` fails (first pass, no prior version), emits a "(initial pass — no prior diff)" placeholder.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/design_system_sync.py` has no bare `except Exception: pass`. All `subprocess.CalledProcessError` from `npx` invocations bubble with the stderr captured; all `json.JSONDecodeError` from malformed `.pen` input raises `SystemExit(2)` with file:line context.
- [ ] The validator `validate_design_system_sync.py` MUST NOT crash on non-`Bash` tool inputs — it early-returns with no-op when `tool_name != "Bash"` or when the command doesn't match the `docs/designs/**` regex.

### Empty/Invalid Input Handling
- [ ] Empty `.pen` (`{}`) → generator emits minimal DESIGN.md with `version: alpha`, `name: (unnamed)`, empty tokens; linter surfaces `missing-primary` / `missing-typography` errors with clear messages (not silent pass).
- [ ] Missing `.pen` file → `--check` / `--generate` both exit 2 with "design-system.pen not found at <path>".
- [ ] Unmapped variable prefix → generator logs a warning to stderr AND fails `--check` with exit 1 (not a silent drop). User must either rename the variable or pass `--drop-unmapped`.
- [ ] Non-reusable frame with `Category/Variant` name → warning only (not error) — the component was intentionally non-reusable.

### Error State Rendering
- [ ] Lint failure path: `npx @google/design.md lint` exit 1 causes `--all` to exit 1 with the linter's stderr passed through verbatim. Tests assert the linter's output reaches the user.
- [ ] Drift-detection block: when the PreToolUse hook fires `decision: block`, the `reason` string names the specific artifact that drifted (e.g. "brand.css differs from generated output — run `python -m tools.design_system_sync --generate`"). Tested via simulated hook input fixtures.

## Test Impact

- [ ] **No existing tests affected** — `tools/design_system_sync.py`, `.claude/hooks/validators/validate_design_system_sync.py`, and `.claude/hooks/validators/validate_design_system_readonly.py` are new files. The `do-design-system` skill has no existing automated tests (skills are validated by human review and runtime invocation), so rewriting its Step 6 & Step 7 documentation is a doc-only edit with no test coverage to update.
- [ ] New tests to add (covered under Step by Step Tasks):
  - `tests/unit/tools/test_design_system_sync.py` — determinism (twice-run byte equality), schema mapping (each prefix category), empty-`.pen` handling, unmapped-variable warning path, lint-failure propagation.
  - `tests/unit/hooks/test_validate_design_system_sync.py` — hook fires only on matching Bash commands; returns `decision: block` on drift; returns no-op on non-Bash or non-matching commands.
  - `tests/integration/test_design_system_pipeline.py` — full round trip: edit seed `.pen` → run `--all` → assert linter exits 0 → assert exports present and non-empty → assert `--check` passes.

## Rabbit Holes

- **DESIGN.md format churn (explicitly called out by the issue).** The spec is `alpha`; schema keys, component properties, and reference syntax may change in minor versions. Do NOT try to future-proof by abstracting over "the current schema" — pin `@google/design.md@0.1.1`, write the generator against that exact version's emitted format, and plan for a generator refresh within 6 months when Google Labs bumps the version. A Phase 2 ticket will re-run `npx @google/design.md spec` and reconcile drift when we next upgrade.
- **Bidirectional mapping.** Do NOT add a `design-system.md` → `.pen` path. The issue explicitly forbids it; any such code is a scope violation. The safety gate rejects write attempts; the drift validator enforces one-way.
- **Figma / Style Dictionary direct integration.** DTCG export is the interop surface; let downstream consumers do their own import. No bespoke Figma plugin or Style Dictionary transformer in Phase 1.
- **Moodboard pipeline rework.** The issue drops Cosmos scrape, `inspiration/` reorganization, and aesthetic rubric changes from Phase 1 scope. Resist the urge to tighten adjacent pieces of the skill.
- **MCP wrapper for the generator.** The issue's agent-integration constraint is explicit: the skill invokes via Bash; no new MCP server needed. Wrapping in MCP would add surface with no payoff.
- **Pencil MCP round-trip.** The existing `SKILL.md` gotcha (L338-345) that Pencil MCP `batch_design` doesn't persist without the desktop app saving is unchanged — the plan leaves that workflow alone and only automates the post-save pipeline.
- **Semantic diff prettification.** `@google/design.md diff` output may need minor reformatting for the gap-audit table; don't build a full diff-rendering engine. One `--audit` path that pipes the CLI output through a small Python reformatter is enough.

## Risks

### Risk 1: `@google/design.md` 0.1.x minor-version breakage
**Impact:** A point-release bump silently changes the emitted format, generator output diverges, drift validator fires on unchanged `.pen`. Rebuild loops if anyone upgrades.
**Mitigation:** Pin to `0.1.1` in `package.json` AND commit `package-lock.json`. `scripts/check_prerequisites.py` asserts the pinned version via `npx --no-install @google/design.md --version`. Generator does NOT auto-upgrade; version bumps are deliberate, human-reviewed PRs.

### Risk 2: Hook failure silences drift
**Impact:** If the PreToolUse validator crashes or times out, drift slips into the repo and we learn about it downstream.
**Mitigation:** Validator has a 10s timeout (per existing hook pattern). On internal error (exception, not a drift match), it logs to stderr and exits 0 (fail-open) rather than blocking all commits. The standalone `python -m tools.design_system_sync --check` CLI is the backstop — documented in the feature doc as the manual verification command, and intended for CI integration if we add that later.

### Risk 3: Generator non-determinism under dict ordering
**Impact:** Different Python versions or `pyyaml` minor versions emit keys in different orders → spurious drift → false alarms that condition users to ignore the validator.
**Mitigation:** Explicit `sort_keys=True` on every `yaml.safe_dump` call. Explicit `sorted()` on all dict iterations in CSS emission. Unit test asserts byte-identical output across two runs. Snapshot test in `tests/unit/tools/test_design_system_sync.py` catches regressions.

### Risk 4: Seed `.pen` doesn't open in Pencil
**Impact:** If the seed file has a schema mistake, humans can't edit the ground truth — the whole pipeline is blocked at day one.
**Mitigation:** Seed file is built by reading Pencil's documented `.pen` JSON shape from `SKILL.md` Step 5 (which has a working example at L356-386) and validated by (a) opening in Pencil manually during the build, (b) round-tripping through the generator and confirming lint exit 0. Validation is a task-completion criterion.

### Risk 5: No existing `.pen` sample to validate against
**Impact:** The generator mapping is spec'd abstractly; a real-world moodboard pass may reveal edge cases (unusual token names, nested component frames, primitive tokens the charter forgot).
**Mitigation:** Seed `.pen` exercises at least one variable from each category (color, typography, rounded, spacing) plus one reusable component. The first real moodboard pass after landing Phase 1 is expected to surface edge cases; the `--drop-unmapped` flag and the warning-to-stderr path for unmapped variables keep the first pass productive even when mapping gaps appear. Follow-up ticket tracks post-first-pass refinements.

## Race Conditions

**No race conditions identified.** The generator is a synchronous single-process CLI. Sequential steps (read `.pen` → emit artifacts → shell out to `npx` → diff) have no shared mutable state. Concurrent agent edits to `.pen` aren't a scenario — the `do-design-system` skill is human-driven, sequential, and single-owner. The PreToolUse hook runs in the hook process; it re-reads from disk each invocation. No locks needed.

## No-Gos (Out of Scope)

- **Bidirectional generation.** `.pen` is canonical, one-way pipeline. Any code that writes back into `.pen` from DESIGN.md tooling is explicitly forbidden.
- **Moodboard pipeline changes.** Cosmos scrape, `inspiration/` restructure, per-pass READMEs, motif-table format — unchanged in Phase 1.
- **Charter content edits.** No voice, a11y-target, licensing, or taxonomy changes. The charter stays human-authored.
- **Aesthetic "DON'T" catalogue / AI-slop fingerprint detection.** Deferred.
- **`do-design-audit` skill changes.** Out of scope. That skill doesn't touch `.pen` or CSS tokens.
- **MCP server for the generator.** The agent invokes via Bash; no `.mcp.json` changes.
- **Auto-upgrade of `@google/design.md`.** Version bumps are deliberate human-reviewed commits.
- **Figma / Style Dictionary direct integration.** DTCG export is the interop surface; downstream tools consume from there.

## Update System

The `/update` skill (`scripts/remote-update.sh`) currently syncs Python deps, env vars, and restarts services. Phase 1 adds a Node toolchain to the design-system slice only:

- **Update script changes:** `scripts/remote-update.sh` gains one new step after `requirements.txt` sync: if `package.json` exists at repo root, run `npm ci --only=prod` (install from `package-lock.json` exactly). Skipped silently on machines without Node.
- **New deps to propagate:** `@google/design.md@0.1.1` via `package.json` + `package-lock.json`. Committed to the repo; `npm ci` on update pulls them.
- **Migration for existing installations:** On first `/update` after this lands, `npm ci` runs fresh — idempotent, no manual step.
- **Docs update:** `/update` skill's SKILL.md gets a short addendum noting that Node + npm are now soft prerequisites for machines that run the `do-design-system` skill. Machines that only run the bridge/worker are unaffected (the skill is the only path that needs `npx`).
- **Fallback:** If Node is absent on a machine, `python -m tools.design_system_sync --generate` still emits `design-system.md`, `brand.css`, `source.css` (the pure-Python parts). Lint/diff/export steps are skipped with a clear stderr warning; `--check` still runs on the Python-emitted artifacts. Full pipeline requires Node.

## Agent Integration

The generator is invoked via Bash from the `do-design-system` skill. **No MCP server, no `.mcp.json` changes, no bridge imports.** This matches the issue's stated constraint and the repo's existing pattern (skills invoke Python CLI modules via Bash — see `do-plan` Phase 1's `python -m tools.code_impact_finder`).

- **Skill rewrite:** `.claude/skills/do-design-system/SKILL.md` Step 6 and Step 7 replaced with generator invocations. Safety-gate block (L314-322) extended to reject Write/Edit to `design-system.md`, `brand.css`, `source.css`, `docs/designs/exports/**`.
- **New validator hooks** (registered in `.claude/settings.json`):
  - `.claude/hooks/validators/validate_design_system_sync.py` — PreToolUse on `Bash`, drift check on `git commit` / `git add docs/designs/**`.
  - `.claude/hooks/validators/validate_design_system_readonly.py` — PreToolUse on `Write` / `Edit`, blocks edits to generated artifacts.
- **No bridge changes.** `bridge/telegram_bridge.py` does not import or call the new code. The agent invokes the skill, the skill invokes the CLI — that's the whole integration.
- **Integration test:** `tests/integration/test_design_system_pipeline.py` exercises the skill path by invoking `python -m tools.design_system_sync --all` directly against the seed `.pen` and asserting all artifacts land correctly. (We do not end-to-end test the agent-running-the-skill path; skill execution is agent-runtime-dependent and not part of the automated suite.)

## Documentation

### Feature Documentation
- [ ] Create `docs/features/design-system-tooling.md` covering: the one-way pipeline (with the Data Flow diagram from this plan), the `.pen` → DESIGN.md schema mapping (the full table from the Solution section), how to regenerate locally (`python -m tools.design_system_sync --all`), the drift validator's behavior, and the `--check` / `--audit` / `--drop-unmapped` flag reference.
- [ ] Add entry to `docs/features/README.md` index table under the `skills` or `design` category (whichever fits the existing grouping after `do-docs-audit.md`).

### Inline Documentation
- [ ] Module-level docstring on `tools/design_system_sync.py` summarizing the pipeline and linking to `docs/features/design-system-tooling.md`.
- [ ] One-line comments on the validator hooks explaining the match regex and fail-open behavior.
- [ ] Update `.claude/skills/do-design-system/SKILL.md` version history block (L499-506) with a `v1.2.0 (2026-04-24)` entry covering the Steps 6 & 7 rewrite and the new safety-gate additions.

### INFRA doc
Not needed for Phase 1. One new devDependency (`@google/design.md@0.1.1`, alpha, free, no auth, no rate limits, local CLI only) does not rise to the bar for a dedicated `docs/infra/` entry. The pin is documented in the feature doc; `package.json` is the canonical record.

## Success Criteria

- [ ] `docs/designs/design-system.pen` exists, is minimal, and opens cleanly in the Pencil desktop app (manual verification).
- [ ] `python -m tools.design_system_sync --generate` emits `docs/designs/design-system.md` with DESIGN.md-compliant YAML frontmatter, plus `brand.css` and `source.css` with byte-identical token names in both files.
- [ ] Running `--generate` twice produces byte-identical output (determinism, unit test).
- [ ] `npx @google/design.md lint docs/designs/design-system.md` exits 0 on the seed.
- [ ] `npx @google/design.md export --format dtcg` and `--format tailwind` both succeed and their output is committed to `docs/designs/exports/tokens.dtcg.json` and `docs/designs/exports/tailwind.theme.json`.
- [ ] `python -m tools.design_system_sync --check` exits 0 on a freshly generated tree and exits 1 with a clear message when an artifact is manually mutated out-of-band.
- [ ] `python -m tools.design_system_sync --audit` runs against a prior pass (or emits the initial-pass placeholder) and prints markdown tables suitable for pasting into `gap-audit.md`.
- [ ] `.claude/skills/do-design-system/SKILL.md` Step 6 and Step 7 rewritten to invoke the generator; safety-gate block extended to reject edits to generated artifacts.
- [ ] Drift-detection validator `validate_design_system_sync.py` registered as PreToolUse on Bash, blocks commit on drift, fail-open on internal error.
- [ ] Read-only validator `validate_design_system_readonly.py` registered as PreToolUse on Write/Edit, blocks edits to `design-system.md`, `brand.css`, `source.css`, `docs/designs/exports/**`, preserves existing `.pen` whitelist.
- [ ] `package.json` pins `@google/design.md@0.1.1`; `package-lock.json` committed; `node_modules/` in `.gitignore`.
- [ ] `docs/features/design-system-tooling.md` exists and covers the pipeline, mapping, and regenerate workflow.
- [ ] `docs/features/README.md` index updated.
- [ ] `scripts/remote-update.sh` runs `npm ci --only=prod` when `package.json` exists.
- [ ] Tests pass (`/do-test`) — new unit tests for the generator, hooks, and integration test.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` exit 0.

## Team Orchestration

### Team Members

- **Builder (generator)**
  - Name: `sync-builder`
  - Role: Implement `tools/design_system_sync.py` CLI, seed `design-system.pen`, mapping spec, determinism.
  - Agent Type: `builder`
  - Resume: true

- **Builder (validators)**
  - Name: `hooks-builder`
  - Role: Implement the two PreToolUse validators and register them in `.claude/settings.json`. Extend SKILL.md safety-gate block.
  - Agent Type: `builder`
  - Resume: true

- **Builder (skill rewrite)**
  - Name: `skill-builder`
  - Role: Rewrite `do-design-system` Steps 6 & 7. Update version history block. Ensure the rewrite preserves existing skill voice and structure.
  - Agent Type: `builder`
  - Resume: true

- **Test Engineer**
  - Name: `test-author`
  - Role: Author `tests/unit/tools/test_design_system_sync.py`, `tests/unit/hooks/test_validate_design_system_sync.py`, and `tests/integration/test_design_system_pipeline.py`.
  - Agent Type: `test-engineer`
  - Resume: true

- **Documentarian**
  - Name: `doc-author`
  - Role: Author `docs/features/design-system-tooling.md` and update the features README index.
  - Agent Type: `documentarian`
  - Resume: true

- **Validator (generator)**
  - Name: `sync-validator`
  - Role: Verify determinism, byte-identical re-runs, lint exit 0, exports committed.
  - Agent Type: `validator`
  - Resume: true

- **Validator (hooks)**
  - Name: `hooks-validator`
  - Role: Verify drift hook fires correctly, read-only hook blocks edits, fail-open on internal error.
  - Agent Type: `validator`
  - Resume: true

- **Validator (integration)**
  - Name: `integration-validator`
  - Role: Run full `--all` pipeline on seed `.pen`, confirm all artifacts, confirm skill invocation path via Bash documented in SKILL.md works.
  - Agent Type: `validator`
  - Resume: true

## Step by Step Tasks

### 1. Add Node toolchain
- **Task ID**: build-node-toolchain
- **Depends On**: none
- **Validates**: `package.json` exists, `package-lock.json` committed, `npx --no-install @google/design.md --version` returns `0.1.1`, `.gitignore` contains `node_modules/`.
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `package.json` at repo root with `@google/design.md@0.1.1` in `devDependencies` only.
- Run `npm install` to generate `package-lock.json`; commit the lockfile.
- Ensure `.gitignore` contains `node_modules/`.

### 2. Create seed `design-system.pen`
- **Task ID**: build-seed-pen
- **Depends On**: none
- **Validates**: File opens in Pencil (manual check), JSON parses, contains at least one variable per category (color / typography / rounded / spacing) and one reusable component.
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `docs/designs/design-system.pen` with a minimal but complete example following the JSON shape documented at `.claude/skills/do-design-system/SKILL.md:356-386`.
- Include: `--color-primary`, `--color-surface`, `--text-body-primary` (color), `--font-sans`, `--text-size-md`, `--text-weight-regular`, `--text-lh-md` (typography forming a `body` preset), `--radius-md` (rounded), `--space-md` (spacing), plus a reusable component `Annotation/Mark`.
- Open in Pencil manually to verify the file is editable (record the step in the PR description).

### 3. Implement generator (`tools/design_system_sync.py`)
- **Task ID**: build-generator
- **Depends On**: build-seed-pen
- **Validates**: Unit tests pass; `--generate` on the seed produces `design-system.md` that passes `npx @google/design.md lint`; twice-run output is byte-identical; `--check` detects manually-mutated artifacts.
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `--generate`, `--check`, `--audit`, `--all` entrypoints.
- Implement the `.pen` → DESIGN.md mapping per the Solution section's mapping spec. Include `--drop-unmapped` flag for ignoring unknown-prefix variables.
- Shell out to `npx --no-install @google/design.md` for lint and export steps.
- Emit artifacts deterministically: alphabetical dict ordering, uppercase hex, fixed YAML style.
- Module docstring with pipeline summary and link to the feature doc.

### 4. Implement drift-detection validator
- **Task ID**: build-drift-validator
- **Depends On**: build-generator
- **Validates**: Hook fires on `git commit` touching `docs/designs/**`, returns `decision: block` on drift, returns no-op on non-matching commands; fail-open on internal error.
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/validators/validate_design_system_sync.py` following the pattern of `validate_no_raw_redis_delete.py`.
- Register in `.claude/settings.json` under `hooks.PreToolUse` matcher `Bash` with a 10s timeout.

### 5. Implement read-only artifact validator
- **Task ID**: build-readonly-validator
- **Depends On**: build-generator
- **Validates**: Hook blocks Write/Edit to `design-system.md`, `brand.css`, `source.css`, `docs/designs/exports/**`; preserves `.pen` whitelist.
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_design_system_readonly.py`.
- Register in `.claude/settings.json` under `hooks.PreToolUse` matchers `Write` and `Edit`.

### 6. Rewrite `do-design-system` Steps 6 & 7 + extend safety gate
- **Task ID**: build-skill-rewrite
- **Depends On**: build-generator, build-readonly-validator
- **Validates**: SKILL.md diff; manual read; version-history block updated; safety-gate block extended.
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace Step 6 (CSS sync) with "Run `python -m tools.design_system_sync --all`" instruction.
- Replace Step 7 (gap-audit table) with "Run `python -m tools.design_system_sync --audit` and paste output" instruction.
- Extend the safety-gate block (L314-322) to reject direct Write/Edit to generated artifacts.
- Add `v1.2.0 (2026-04-24)` entry to the version-history block.

### 7. Author tests
- **Task ID**: build-tests
- **Depends On**: build-generator, build-drift-validator, build-readonly-validator
- **Validates**: `pytest tests/unit/tools/test_design_system_sync.py tests/unit/hooks/test_validate_design_system_sync.py tests/integration/test_design_system_pipeline.py -q` exits 0.
- **Assigned To**: test-author
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit: generator determinism (two-run byte equality), schema mapping per prefix category, empty-`.pen` handling, unmapped-variable warning, lint-failure propagation.
- Unit: drift validator fires on matching Bash commands, no-op on others; read-only validator blocks edits to generated artifacts.
- Integration: edit seed `.pen`, run `--all`, assert lint 0, exports present, `--check` passes.

### 8. Update `/update` skill + remote-update script
- **Task ID**: build-update-system
- **Depends On**: build-node-toolchain
- **Validates**: `scripts/remote-update.sh` conditionally runs `npm ci --only=prod`; `/update` SKILL.md has addendum.
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `npm ci` step to `scripts/remote-update.sh`, gated on `package.json` existence.
- Add a short addendum to `.claude/skills/update/SKILL.md` noting Node soft-dep for the design-system path.

### 9. Write feature doc
- **Task ID**: document-feature
- **Depends On**: build-generator, build-skill-rewrite, build-drift-validator
- **Validates**: `docs/features/design-system-tooling.md` exists; `docs/features/README.md` index updated.
- **Assigned To**: doc-author
- **Agent Type**: documentarian
- **Parallel**: true
- Author the feature doc covering pipeline, mapping, regenerate workflow, drift validator, flag reference.
- Update the features README index entry.

### 10. Validate generator
- **Task ID**: validate-generator
- **Depends On**: build-generator, build-tests
- **Assigned To**: sync-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `--generate` → lint exit 0, exports committed.
- Confirm twice-run byte equality.
- Confirm `--check` detects drift.

### 11. Validate hooks
- **Task ID**: validate-hooks
- **Depends On**: build-drift-validator, build-readonly-validator, build-tests
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: true
- Confirm drift hook matches regex correctly, blocks on drift, fail-opens on internal error.
- Confirm read-only hook blocks edits to generated artifacts but preserves `.pen` edit path.

### 12. Final integration validation
- **Task ID**: validate-all
- **Depends On**: validate-generator, validate-hooks, build-skill-rewrite, document-feature, build-update-system
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full `python -m tools.design_system_sync --all` pipeline end-to-end against the seed.
- Run `pytest tests/` (unit + integration).
- Run `python -m ruff check .` and `python -m ruff format --check .`.
- Verify every Success Criteria checkbox.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/tools/test_design_system_sync.py tests/unit/hooks/test_validate_design_system_sync.py tests/integration/test_design_system_pipeline.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Generator exists | `test -f tools/design_system_sync.py` | exit code 0 |
| Seed pen exists | `test -f docs/designs/design-system.pen` | exit code 0 |
| Package pinned | `npx --no-install @google/design.md --version` | output contains `0.1.1` |
| DESIGN.md emitted | `test -f docs/designs/design-system.md` | exit code 0 |
| Lint passes on seed | `npx --no-install @google/design.md lint docs/designs/design-system.md` | exit code 0 |
| Determinism | `python -m tools.design_system_sync --generate && cp docs/designs/design-system.md /tmp/a.md && python -m tools.design_system_sync --generate && diff /tmp/a.md docs/designs/design-system.md` | exit code 0 |
| Drift check clean | `python -m tools.design_system_sync --check` | exit code 0 |
| DTCG export present | `test -f docs/designs/exports/tokens.dtcg.json` | exit code 0 |
| Tailwind export present | `test -f docs/designs/exports/tailwind.theme.json` | exit code 0 |
| Feature doc exists | `test -f docs/features/design-system-tooling.md` | exit code 0 |
| Drift validator registered | `grep -q validate_design_system_sync .claude/settings.json` | exit code 0 |
| Read-only validator registered | `grep -q validate_design_system_readonly .claude/settings.json` | exit code 0 |
| node_modules ignored | `grep -q '^node_modules' .gitignore` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Exports live under `docs/designs/exports/` or `docs/designs/` flat?** Issue says "Add a `docs/designs/exports/` convention" — plan follows that. Confirm the subdirectory is acceptable vs. flat.
2. **Should the drift validator run fail-closed or fail-open on internal error?** Plan chose fail-open (risk section, mitigation 2) to avoid blocking unrelated commits on hook bugs. Confirm — or switch to fail-closed and accept the blast radius.
3. **Do we want a CI job that runs `python -m tools.design_system_sync --check` on every PR, independent of the hook?** Plan doesn't add CI in Phase 1; the hook + manual CLI cover it. Confirm Phase 1 intentionally ships without CI, or add it now.
4. **Seed `.pen` aesthetic starting point** — the plan uses generic token names (`--color-primary`, `--font-sans`) that are intentionally characterless. Confirm the seed is meant as a fixture only, not as a starting point for any brand direction.
