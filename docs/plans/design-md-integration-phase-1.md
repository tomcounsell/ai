---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-04-24
revised: 2026-04-24
revision_applied: true
tracking: https://github.com/tomcounsell/ai/issues/1162
last_comment_id:
---

# DESIGN.md Integration on top of `.pen` Ground Truth (Phase 1)

## Problem

The `do-design-system` skill (defined in this ai/ repo, run against *consumer* repos like `yudame/cuttlefish`) currently drives design-system changes through a hand-written pipeline: the agent reads the consumer repo's `docs/designs/design-system.pen`, manually mirrors tokens into the consumer's `<css-root>/brand.css` and Tailwind `<css-root>/source.css`, and hand-composes a variables/components changelog table in `docs/designs/gap-audit.md` each moodboard pass. Four gaps result:

- **No validation.** Broken token references, WCAG contrast misses, orphaned tokens, and missing primaries are caught by eyeball or not at all. `SKILL.md:417-418` explicitly warns that token names "MUST match `brand.css` exactly, or the system diverges silently" — there is no tool enforcing that mirror.
- **CSS sync is hand-written.** Step 6 of the skill instructs the agent to retype tokens from `.pen` into two separate CSS files. Typos and missed edits are invisible until a template breaks.
- **Gap-audit tables are hand-written.** Step 7 asks the agent to hand-compose a dated changelog of variable/component changes. Drift between passes is invisible until a human notices.
- **No ecosystem export.** `.pen` tokens cannot be consumed by Figma, Style Dictionary, or Tailwind tooling without bespoke conversion.

**Current behavior:** Agent edits consumer-repo `.pen` → agent manually retypes into `brand.css` → agent manually retypes into `source.css` → agent manually drafts changelog table. No linter runs. No drift check fires at commit time.

**Desired outcome:** `.pen` stays the only human-editable file. A deterministic generator (hosted in this ai/ repo under `tools/`, taking `--pen` and `--css-root` path args so it works against any consumer repo) emits `design-system.md` (DESIGN.md-compliant), `brand.css`, `source.css`, and DTCG / Tailwind exports from it. `@google/design.md lint` runs against the emitted DESIGN.md; `@google/design.md diff` drives the gap-audit changelog. A validator hook fails commits (in the consumer repo or in ai/ itself if anyone mutates the fixture) that leave the derived artifacts out of sync with `.pen`.

## Freshness Check

**Baseline commit:** `12438918` (`git rev-parse main` at revision time, 2026-04-24)
**Issue filed at:** 2026-04-24T08:43:45Z (same day as plan)
**Disposition:** Minor drift (revision pass corrects three drifts flagged by critique)

**File:line references re-verified:**
- `.claude/skills/do-design-system/SKILL.md` — **Safety gate header is at L313** (`### Safety gate (required before any write)`), with the Python assertion body at L315-322. The initial plan misstated this as L314-322. Fixed throughout this revision.
- **`docs/designs/` does NOT exist in this ai/ repo.** The only `docs/designs/`-like content in the skill is `.claude/skills/do-design-system/charter-template.md`. The skill is authored in ai/ but is designed to run against **consumer repos** (cuttlefish and equivalents) that have their own `docs/designs/` + `<css-root>/`. See Key Clarification below.
- `.claude/hooks/validators/validate_no_raw_redis_delete.py:108-125` — verified the existing validator pattern: hooks read JSON from **stdin**, not `$CLAUDE_HOOK_INPUT`. The initial plan misstated this; corrected in Technical Approach.
- `.claude/settings.json:26-56` — verified PreToolUse registration shape: `matcher: "Bash"` block currently lists `validate_commit_message`, `validate_merge_guard`, `validate_no_raw_redis_delete`; adding two new entries here is additive.

**Cited sibling issues/PRs re-checked:** None cited in issue body.

**Commits on main since issue was filed (touching referenced files):** None. Issue and plan share the same day.

**Active plans in `docs/plans/` overlapping this area:** None. No other plan touches `docs/designs/`, `do-design-system`, or design-system tooling.

### Key clarification — repo that hosts the tooling vs. repos that consume it

The initial plan assumed `docs/designs/design-system.pen`, `brand.css`, and `source.css` live in this ai/ repo. **They don't, and they shouldn't.** The `do-design-system` skill is designed to operate in consumer repos (e.g., `yudame/cuttlefish`) that ship user-facing CSS. The ai/ repo hosts the skill definition and this Phase 1 tooling, but is NOT itself a design-system consumer.

Consequences (applied throughout the revision):
- **Tooling code lives in ai/** (`tools/design_system_sync.py`, validators, `package.json`). Committed to ai/.
- **Tooling is exercised against a fixture `.pen`** under `tests/fixtures/design_system/design-system.pen` — never a "seed" at `docs/designs/design-system.pen` in ai/, because that path should not exist in ai/.
- **Consumer repos adopt the tooling** by installing `@google/design.md@0.1.1` (matching `package.json`) and invoking `python -m tools.design_system_sync --generate --pen <path> --css-root <path>` from within their repo. The generator takes explicit path args — no hardcoded `docs/designs/` assumptions.
- **Cuttlefish adoption is out of Phase 1 scope.** Phase 1 ships the tooling + skill rewrite + validators. A follow-up Phase 2 ticket will land the first real consumer adoption (cuttlefish) and surface any mapping gaps.

This is the critique-flagged blocker resolved.

### Acceptance-criteria interpretation (revised)

The issue body's Acceptance Criteria list references `docs/designs/design-system.pen` and `docs/designs/exports/` paths. After the Freshness Check clarified that the `do-design-system` skill operates against **consumer repos** (not ai/), those hardcoded paths are re-read as "the consumer repo's `docs/designs/` tree, addressed via generator args." For Phase 1 — which ships tooling only — the ACs map as follows:

| Issue AC (verbatim path) | Phase 1 interpretation | Phase 1 deliverable |
|---|---|---|
| "A seed `docs/designs/design-system.pen` exists and opens cleanly in Pencil." | Ship a test fixture that exercises the full mapping. Consumer-repo seed lives in the consumer repo and is out of Phase 1 scope. | `tests/fixtures/design_system/design-system.pen` (fixture, Pencil-openable). |
| "A generator converts `design-system.pen` into `docs/designs/design-system.md`." | Generator must emit `design-system.md` next to the `.pen` at any path the `--pen` arg points to. | `python -m tools.design_system_sync --generate --pen <path>` emits `<pen-dir>/design-system.md`. Exercised against the fixture. |
| "`npx @google/design.md lint docs/designs/design-system.md` exits 0 on the seed file." | Lint exits 0 on the fixture-emitted DESIGN.md. | Integration test asserts `npx --no-install @google/design.md lint tests/fixtures/design_system/design-system.md` exits 0. |
| "The same generator emits `brand.css` and `source.css` from the same `.pen`." | Generator takes `--css-root <path>` (or TOML) and emits both files under that root. | Integration test asserts both files present under fixture's `<css-root>` with byte-identical token names. |
| "`npx @google/design.md export --format dtcg docs/designs/design-system.md > docs/designs/exports/tokens.dtcg.json` runs as part of the pipeline and the output is committed." | Exports sit under `<pen-dir>/exports/`; in Phase 1 that means committed under the fixture tree. | `tests/fixtures/design_system/exports/tokens.dtcg.json` and `tailwind.theme.json` are committed. |
| "`@google/design.md export --format tailwind` equivalent is wired and committed." | Same — wired into `--all` flow, committed to fixture tree. | Same as above. |
| "The `do-design-system` skill's Step 6 and Step 7 are rewritten to invoke the generator and `@google/design.md diff`." | Skill prose edits — repo-agnostic — live in ai/. | `.claude/skills/do-design-system/SKILL.md` Step 6 and Step 7 rewritten. |
| "A drift-detection mechanism … fails the commit if any generated artifact is out of sync with `design-system.pen`." | Two-surface: (a) ai/ PreToolUse hook guards the fixture, (b) CLI `--check` is the cross-repo enforcement path. Consumer repos adopt via their own pre-commit hook or settings registration (documented in the feature doc). See **Enforcement reach — ai/ hook vs consumer repos** under Technical Approach. | Drift validator registered in ai/'s `.claude/settings.json`; CLI `--check` mode documented as the cross-repo path. |
| "The skill's safety gate continues to reject any attempt to write to a `.pen` file other than `design-system.pen`, and additionally rejects any direct edit of `design-system.md`, `brand.css`, `source.css`, or files under `docs/designs/exports/`." | Two layers: inline SKILL.md assertion (agent-python-level) + Write/Edit PreToolUse hook (tool-level), both repo-agnostic. | Inline assertion augmented in SKILL.md; `validate_design_system_readonly.py` registered in ai/. Consumer repos get the read-only hook when they adopt the skill's settings fragment (documented). |
| "Documentation: `docs/features/design-system-tooling.md` explains the one-way pipeline…" | ai/ feature doc. | `docs/features/design-system-tooling.md` authored. |

**Explicit Phase 1 scope deviation from the literal issue text:** no `docs/designs/` directory is created in the ai/ repo. This is a deliberate architectural correction — the skill runs against consumer repos; ai/ hosts the tooling. All `docs/designs/` paths in the issue body refer to the consumer-repo tree the tooling operates on. Phase 2 (consumer-repo adoption, explicitly dropped from Phase 1 scope) will land the first real `docs/designs/design-system.pen` in a consumer repo (e.g., cuttlefish).

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
- **Assumption:** "This ai/ repo has an existing `.pen` we can sample."
- **Method:** `ls docs/designs/` (path does not exist); `ls .claude/skills/do-design-system/` (skill body only).
- **Finding (revised):** `docs/designs/` does not exist in the ai/ repo, and **shouldn't** — the skill operates against consumer repos (e.g. cuttlefish). Phase 1 ships the tooling hosted in ai/ + a **test fixture** `.pen` under `tests/fixtures/design_system/design-system.pen`. The generator spec is derived from the skill's documented conventions (tier-aware naming, `Category/Variant` components, `$--name` variable refs, `reusable: true` component frames) and from the reference-implementation commit `a702484` on `yudame/cuttlefish`.
- **Confidence:** High.
- **Impact on plan:** First build task is **fixture `tests/fixtures/design_system/design-system.pen`** (minimal — 3 colors, 2 typography presets, 1 radius, spacing scale, 1 component). Mapping spec and generator are built against the fixture. Design invariants come from `SKILL.md`, not from reverse-engineering a sample. **No `docs/designs/` is created in the ai/ repo.**

### spike-4: Pre-commit hook vs. PostToolUse validator — which fits this repo's pattern?
- **Assumption:** "Other validators in this repo are registered as PostToolUse hooks."
- **Method:** Read `.claude/settings.json` hook registrations and `ls .claude/hooks/validators/`; read `validate_no_raw_redis_delete.py:108-125` as a reference implementation.
- **Finding:** The repo's native drift-detection pattern is a Python validator under `.claude/hooks/validators/` wired into `PostToolUse` or `PreToolUse` blocks in `.claude/settings.json`. Hooks **read JSON from stdin** via `sys.stdin.read()` + `json.loads()` (not from `$CLAUDE_HOOK_INPUT`) and return `{"decision": "block", "reason": ...}` on stdout.
- **Confidence:** High.
- **Impact on plan:** Drift-detection mechanism is implemented as `python` validator at `.claude/hooks/validators/validate_design_system_sync.py`, registered as a **PreToolUse** hook on `Bash` (catching `git commit` / `git add` commands whose **combined command path arguments** match `.pen` or the consumer repo's `brand.css`/`source.css` conventions) **and** as a standalone CLI entrypoint runnable via `python -m tools.design_system_sync --check --pen <path> --css-root <path>`. This gives us both agent-path enforcement and a CLI path for human/CI verification. A git `pre-commit` hook (optional, documented in the feature doc) can invoke the CLI for non-agent edits.

### spike-5: How does the skill's existing Safety gate interact with the new Write/Edit hook?
- **Assumption:** "The Safety gate at L314-322 can be 'extended' to cover generated artifacts."
- **Method:** Read `.claude/skills/do-design-system/SKILL.md:313-322`.
- **Finding:** The "Safety gate" at L313 is **not a hook** — it is an inline Python assertion block the agent is instructed to paste and execute in the session before any `.pen` write. It checks `target.name == "design-system.pen"`. It runs *in the agent process*, not as an enforcement hook. Adding new file restrictions to THIS Python block covers only paths the agent itself edits via its own Python invocations; it does NOT block Write/Edit tool calls to generated artifacts.
- **Confidence:** High.
- **Impact on plan:** **Two distinct mechanisms are needed, with clear scope:**
  1. **Update the inline assertion block** in SKILL.md to also refuse direct writes to `design-system.md`, `brand.css`, `source.css`, and `docs/designs/exports/**` — this catches agent-authored Python edits within the skill context.
  2. **Add a new PreToolUse hook** `validate_design_system_readonly.py` on the `Write`/`Edit` tool matchers — this catches direct Write/Edit tool calls anywhere in the repo, whether the agent is running the skill or not.
  These are separate artifacts, not an "extension" of a single gate. The revision renames the section to "Safety-gate updates (two parts)" to reflect this.

## Data Flow

**Note:** Paths below use `<pen-path>` (defaults to `docs/designs/design-system.pen` in a consumer repo) and `<css-root>` (e.g. `static/css/`, consumer-dependent). The generator takes these as explicit `--pen` / `--css-root` arguments — no hardcoded paths. In the ai/ repo itself the generator is only exercised against `tests/fixtures/design_system/design-system.pen` with a fixture `<css-root>` under the same tests/ tree.

```
Human edits design-system.pen in Pencil desktop app (consumer repo)
        │
        ▼
<pen-path> (e.g. docs/designs/design-system.pen)   (JSON — canonical ground truth)
        │
        ▼  python -m tools.design_system_sync --generate --pen <pen-path> --css-root <css-root>
┌───────┴─────────────────────────────────────────────────────────┐
│ Generator (deterministic, idempotent)                           │
│   1. Load .pen JSON from --pen arg                              │
│   2. Extract variables → categorize (colors / typography /      │
│      rounded / spacing)                                         │
│   3. Extract reusable components → map to DESIGN.md components  │
│      schema                                                     │
│   4. Emit <pen-dir>/design-system.md YAML frontmatter + body    │
│   5. Emit <css-root>/brand.css (:root {} with --vars)           │
│   6. Emit <css-root>/source.css (@theme {} for Tailwind)        │
│   7. Exec `npx @google/design.md export --format dtcg` →        │
│      <pen-dir>/exports/tokens.dtcg.json                         │
│   8. Exec `npx @google/design.md export --format tailwind` →    │
│      <pen-dir>/exports/tailwind.theme.json                      │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────────┘
       ▼          ▼          ▼          ▼          ▼
<pen-dir>/    <css-root>/ <css-root>/ <pen-dir>/  <pen-dir>/
design-      brand.css   source.css  exports/    exports/
system.md                            tokens.     tailwind.
                                     dtcg.json   theme.json
       │
       ▼  npx @google/design.md lint <pen-dir>/design-system.md
Lint exit 0 required
       │
       ▼  (commit in consumer repo)
       │
       ▼  PreToolUse hook on Bash (git commit / git add, when run inside ai/)
validate_design_system_sync.py
  → matches only when the command references a .pen path OR
    a <css-root>/{brand,source}.css path (regex anchors on filenames)
  → re-runs generator to tempdir with detected --pen / --css-root
  → diffs against working-tree artifacts
  → exit 1 if any artifact drifted
       │
       ▼
git commit proceeds
```

**Config discovery in consumer repos:** The generator reads `<pen-dir>/design-system-sync.toml` (optional) for `<css-root>` if the flag is not passed. Missing config + missing flag → error "pass --css-root or create design-system-sync.toml". The fixture in ai/ ships with an explicit TOML so the integration test does not need flag args.

**Hook scope:** The drift validator fires ONLY when the matched Bash command references a known design-system path. In ai/, this means the fixture under `tests/fixtures/design_system/`. The validator never fires on unrelated `git commit` calls in ai/ — this is the critique-flagged false-positive risk, closed by regex anchoring on filenames the plan actually touches.

**Gap-audit diff flow (separate from commit-time drift check):**

```
Previous pass's design-system.md (from git show HEAD~1:<pen-dir>/design-system.md)
           │
           ▼  npx @google/design.md diff <prev> <current>
Diff output (JSON or markdown)
           │
           ▼  reformatter (tools/design_system_sync.py --audit --pen <pen-path>)
Markdown table — Variables changed / Components added
           │
           ▼
Appended to <pen-dir>/gap-audit.md under ## YYYY-MM-DD — <theme>
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
| `@google/design.md@0.1.1` installed in ai/ | `npx --no-install @google/design.md --version \| grep -q 0.1.1` | Pinned DESIGN.md CLI available (pin lives in ai/ `package.json`) |
| Existing `pyyaml` in requirements | `python -c "import yaml"` | YAML emission (no new Python dep) |
| Python 3.11+ `tomllib` | `python -c "import tomllib"` | Read `design-system-sync.toml` (stdlib, no new dep) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/design-md-integration-phase-1.md`

## Solution

### Key Elements

- **Test fixture `tests/fixtures/design_system/design-system.pen`** — minimal, Pencil-openable JSON with a documented set of tokens and one reusable component, plus a companion `tests/fixtures/design_system/design-system-sync.toml` declaring a fixture `<css-root>`. Serves as the generator's test fixture. Lives under `tests/fixtures/` specifically so it does NOT create a `docs/designs/` in the ai/ repo.
- **`tools/design_system_sync.py`** — plain Python CLI module (no new runtime deps). Reads `.pen` JSON from `--pen` arg (or TOML config), emits `design-system.md` next to the `.pen`, emits `brand.css` and `source.css` under `<css-root>` (from `--css-root` arg or TOML), and shells out to `npx @google/design.md export` for DTCG / Tailwind exports. Deterministic — running twice produces byte-identical output. Entrypoints: `--generate`, `--check`, `--audit` (diff for gap-audit), `--all` (generate + lint + export). All entrypoints take `--pen` and `--css-root` path args (or read from `design-system-sync.toml` adjacent to the `.pen`).
- **`.pen` → DESIGN.md YAML mapping spec** — captured in `docs/features/design-system-tooling.md`. Defines how Pencil variable names categorize into `colors` / `typography` / `rounded` / `spacing` and how reusable component frames map to DESIGN.md `components` entries. See **Schema Mapping** section below.
- **`package.json`** — minimal, repo-root of ai/, pins `@google/design.md@0.1.1` only. `package-lock.json` committed. `node_modules/` added to `.gitignore`. Consumer repos that adopt this tooling will add their own `package.json` with the matching pin.
- **Drift-detection validator** — `.claude/hooks/validators/validate_design_system_sync.py`. PreToolUse hook on `Bash`; fires ONLY when the Bash command matches a design-system-touching regex (`(design-system\.pen|/brand\.css|/source\.css|design-system\.md)` within `git (add|commit)` context). Reads hook JSON from stdin (per the existing `validate_no_raw_redis_delete.py` pattern). Re-runs the generator to a tempdir with `--pen`/`--css-root` discovered from the matched path, byte-compares each emitted artifact against the working-tree copy, returns `{"decision": "block", "reason": ...}` on mismatch. Fails open (exits 0) on internal error. Same script runnable as `python -m tools.design_system_sync --check --pen <path> --css-root <path>` for CI or manual verification.
- **Read-only artifact validator** — `.claude/hooks/validators/validate_design_system_readonly.py`. PreToolUse hook on `Write`/`Edit` matchers. Blocks direct Write/Edit to filenames matching `design-system\.md|brand\.css|source\.css|.*\.dtcg\.json|tailwind\.theme\.json` (those must flow through the generator). The `.pen` whitelist is preserved: writes to `design-system.pen` remain allowed; writes to any other `.pen` file under `docs/designs/` are NOT covered by this hook (the existing SKILL.md safety gate handles that).
- **`do-design-system` skill rewrite** — Steps 6 & 7 replaced:
  - Step 6 becomes: "Run `python -m tools.design_system_sync --all` (takes `--pen` / `--css-root` args or reads `design-system-sync.toml`). Verify `npx @google/design.md lint <pen-dir>/design-system.md` exits 0. Do not hand-edit `brand.css`, `source.css`, or `design-system.md` — the read-only validator will block the Write/Edit."
  - Step 7 becomes: "Run `python -m tools.design_system_sync --audit` to produce the variables/components diff table. Append to `<pen-dir>/gap-audit.md` under `## YYYY-MM-DD — <theme>`."
  - **Inline safety-gate assertion at SKILL.md L315-322 is augmented** (not "extended" — a distinct surface from the Write/Edit hook): the Python assertion block gains three additional `assert` lines refusing agent-authored writes to `design-system.md`, `brand.css`, `source.css`, `docs/designs/exports/**`. This is belt-and-braces alongside the Write/Edit hook — the hook catches tool-level writes; the assertion catches raw-Python `Path.write_text()` inside the agent's skill-context Python code.
- **Feature doc** — `docs/features/design-system-tooling.md` documents the one-way pipeline, schema mapping, regenerate-locally workflow (`--pen` / `--css-root` / TOML config), and validator semantics.

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

Presets with missing properties inherit from a `base` preset defined in the `.pen`. If `base` is missing, the generator emits a hard error (caught by `--check` and by the lint rule `missing-typography`).

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

**Ground truth path (consumer repo):** Pencil desktop app → edit `<consumer-repo>/docs/designs/design-system.pen` → save → `python -m tools.design_system_sync --all --pen <pen-path> --css-root <css-root>` (or rely on `design-system-sync.toml`) → generator writes artifacts + lints + exports → `git add docs/designs/ <css-root>/brand.css <css-root>/source.css` → commit (drift validator re-checks) → push.

**Moodboard pass path (via do-design-system skill, run in consumer repo):**
Moodboard URL → agent runs skill → agent edits consumer-repo `design-system.pen` directly (existing Step 5 path) → `python -m tools.design_system_sync --all` (new Step 6) → `python -m tools.design_system_sync --audit` (new Step 7) → skill appends audit table to `gap-audit.md` → commit → drift validator passes.

**ai/-repo fixture path:** `pytest tests/integration/test_design_system_pipeline.py` invokes the generator against `tests/fixtures/design_system/design-system.pen` (with `<css-root>` from the fixture's TOML). The integration test is the ai/ repo's only interaction with the generator.

### Technical Approach

- **Single CLI module** at `tools/design_system_sync.py` exposes `--generate`, `--check`, `--audit`, `--all`. `python -m tools.design_system_sync` is the canonical invocation (matches existing repo tool-module conventions: `python -m tools.analytics`, `python -m tools.memory_search`). All subcommands accept `--pen <path>` and `--css-root <path>`; if absent, the generator looks for `design-system-sync.toml` adjacent to `--pen` (or in `$CWD/docs/designs/`). Missing path resolution → `SystemExit(2)` with actionable message naming both failure modes.
- **stdlib-only Python:** `json`, `pathlib`, `subprocess`, `sys`, `argparse`, `tomllib` (Python 3.11+, already the repo's version), `yaml` (already in `requirements.txt`). No new Python runtime deps.
- **Shell-out to `npx`** for DESIGN.md CLI invocations — lint, diff, export. The generator wraps each `npx` call in a helper `_run_npx(args, *, required)` with this semantic:
  - **`required=True`** (lint, export): `subprocess.run(["npx", "--no-install", "@google/design.md", *args], check=True, capture_output=True, text=True, cwd=ai_repo_root)`. On `FileNotFoundError` (no `npx` / `node` on PATH) or `CalledProcessError` (package not installed, lint-fail, export-fail), re-raise the original exception — the caller decides what to do. For lint/export this means the `--all` path fails with the caller's chosen exit code.
  - **`required=False`** (`--generate` Python-only emission): not used — Python emission doesn't touch `npx`. Kept as a reserved hook for future optional CLI calls.
  - **Degraded-mode fallback:** a new top-level flag `--no-node` (documented in the `--help` output and the feature doc) skips every `npx` call unconditionally and emits a stderr warning (`"Node not available; lint/export skipped. Run python -m tools.design_system_sync --all on a machine with Node to produce exports."`). `--generate` without `--no-node` attempts `npx --version` up front via `_probe_npx()`; if that probe raises `FileNotFoundError` OR the subprocess returns non-zero, the generator auto-enables `--no-node` and emits the same warning. This is the only path on which missing Node does NOT fail — every other subcommand (`--all`, `--check` with exports, `--audit`) exits 2 with a clear "Node required; install Node and rerun, or pass --no-node to skip" message.
  - `--no-install` forces use of the pinned local version when Node IS present. `cwd=ai_repo_root` ensures `npx` resolves the `@google/design.md` pinned in ai/'s `package.json` even when the generator is invoked from a consumer repo's cwd.
  - Unit test covers: (a) Node present + package present → full path works; (b) Node absent → `--generate` falls back with warning, `--all` exits 2; (c) Node present + package missing → both exit 2 with "pin-missing" message.
- **Determinism via canonical emission:** helper `_emit_yaml()` uses `yaml.safe_dump(..., sort_keys=True, default_flow_style=False, allow_unicode=True)` + post-processing to normalize spacing. `_emit_css()` sorts keys before writing. Snapshot tests assert byte-identical output on repeated runs.
- **Drift check algorithm:** `--check` mode writes artifacts to a `tempfile.TemporaryDirectory()`, then compares each byte-for-byte against `<pen-dir>/design-system.md`, `<css-root>/brand.css`, `<css-root>/source.css`, and `<pen-dir>/exports/*.json` (paths resolved from `--pen` / `--css-root` / TOML). Diffs printed as unified diff to stderr; exit 1 on any mismatch; exit 0 on parity.
- **PreToolUse Bash validator wiring:** `.claude/hooks/validators/validate_design_system_sync.py` follows the existing `validate_no_raw_redis_delete.py` pattern verbatim:
  - Reads JSON from **stdin** via `sys.stdin.read()` + `json.loads()`.
  - Early-returns `sys.exit(0)` if `tool_name != "Bash"` or if the command doesn't match the combined regex `r"git (add|commit).*\b(design-system\.(pen|md)|brand\.css|source\.css)\b"`.
  - On match, extracts the closest `.pen` path from the command (or discovers via TOML walk), invokes `python -m tools.design_system_sync --check --pen <detected> --css-root <detected>` with `capture_output=True`.
  - On CalledProcessError (drift detected): prints `json.dumps({"decision": "block", "reason": <unified diff summary>})` and `sys.exit(0)` (hook-spec: the decision is in stdout, exit 0 means the hook ran cleanly).
  - On any internal exception: **fails open** — prints the exception to stderr and `sys.exit(0)` without emitting a block decision. This is the critique-flagged "fail-open vs fail-closed" question, resolved to fail-open for the reasons in Risk 2.
  - Registered in `.claude/settings.json` under `hooks.PreToolUse` → existing `matcher: "Bash"` block, appended after the existing three entries with a 10s `timeout`.
- **PreToolUse Write/Edit validator wiring:** `.claude/hooks/validators/validate_design_system_readonly.py`:
  - Stdin JSON, same pattern.
  - Matches when `tool_name in ("Write", "Edit")` AND `tool_input.file_path` matches the generated-artifact regex (`design-system\.md$|/brand\.css$|/source\.css$|\.dtcg\.json$|tailwind\.theme\.json$`).
  - On match, emits `{"decision": "block", "reason": "<path> is a generated artifact — run python -m tools.design_system_sync --generate to regenerate"}`.
  - Does **not** block `.pen` writes — those are handled by the existing skill-level safety-gate assertion.
  - Registered under `hooks.PreToolUse` with a new `matcher: "Write|Edit"` block (note: settings.json matcher strings are regex; `"Write|Edit"` is not currently used in this repo — if that syntax is unsupported, fall back to registering two separate matcher blocks, `matcher: "Write"` and `matcher: "Edit"`).
- **Inline SKILL.md assertion augmentation:** `do-design-system/SKILL.md:315-322` currently contains a two-line assert block for `target.name == "design-system.pen"`. The revision appends three additional `assert` lines that check the agent isn't about to write to `design-system.md`, `brand.css`, or `source.css` via Python. This is **belt-and-braces** — the Write/Edit hook catches tool-level writes; the assertion catches raw `Path.write_text()` Python in the agent's skill context. The assertion block is intentionally repeated in the skill text for discoverability, even though the hook provides the real enforcement.
- **CLI-first for the generator:** No MCP wrapper. The skill invokes via Bash. This matches the issue's stated constraint ("The generator should be runnable as a plain CLI … so the skill can invoke it via Bash").

- **Enforcement reach — ai/ hook vs consumer repos (explicit).** A Claude Code session loads `.claude/settings.json` from the session's cwd project, NOT from ai/. When the agent runs the `do-design-system` skill inside a consumer repo like `yudame/cuttlefish`, the ai/-repo's `validate_design_system_sync.py` registration does **not** fire on that session's `git commit`. This is a structural property of the hook system, not a bug. Phase 1 closes the gap with three layered surfaces, each scoped to where it actually runs:
  1. **ai/ PreToolUse hook (`validate_design_system_sync.py`)** — fires only inside ai/ Claude Code sessions, only when a Bash command matches the filename regex. In practice, this guards the fixture under `tests/fixtures/design_system/` from going out of sync in ai/ maintenance work. It does NOT protect consumer-repo commits; that is not a regression because nothing today does.
  2. **CLI `--check` as the cross-repo enforcement path** — `python -m tools.design_system_sync --check --pen <path> --css-root <path>` runs in ANY repo, exits 1 on drift, and is the canonical cross-repo verification. The feature doc documents two adoption patterns for consumer repos: (a) a per-repo `.git/hooks/pre-commit` shell script that invokes `--check` (sample script shipped in `docs/features/design-system-tooling.md`); (b) a per-repo `.claude/settings.json` fragment that registers the ai/-hosted validator (sample fragment shipped in the same doc). Either pattern gives consumer repos the same drift protection the ai/ hook gives the fixture.
  3. **Read-only Write/Edit hook** — same reach caveat: fires only in ai/ sessions. Consumer repos that want the "refuse direct edit to generated artifacts" protection add the sample settings fragment from the feature doc, which registers `validate_design_system_readonly.py` in their `.claude/settings.json`. Path to the validator is an absolute `$CLAUDE_PROJECT_DIR` reference when registered in ai/; for consumer-repo registration the feature doc recommends vendoring a symlink or a shell wrapper that resolves the ai/ checkout location via a `DESIGN_SYSTEM_AI_REPO` env var. Vendoring the hook into each consumer repo is a deliberate trade-off (documented, versioned per consumer) over trying to make a single ai/-hosted hook somehow fire cross-repo.
  - **Phase 1 ships the enforcement primitives + adoption docs.** Actual consumer-repo adoption (cuttlefish) lands in Phase 2. The Phase 1 deliverable is that the feature doc contains an "Adopting this in a consumer repo" section with the two sample fragments copy-pasteable.
- **Gap-audit diff generator:** `--audit` mode operates cross-repo safely.
  - **Consumer-repo-root resolution:** the generator walks up from `--pen` until it finds a `.git/` directory; that becomes `<consumer-repo-root>`. If no `.git/` is found before hitting `/`, `--audit` exits 2 with "Could not locate a git repo above <pen-path>; pass --repo-root <path> or run from inside a git worktree." An explicit `--repo-root <path>` flag overrides the walk for CI / detached-worktree scenarios.
  - **Temporal ordering (documented explicitly):** `--audit` MUST run **before** `git commit` of the new `.pen` and its regenerated artifacts. At that moment, `HEAD` in the consumer repo still has the PRIOR pass's `design-system.md`, so `git show HEAD:<pen-dir>/design-system.md` yields the correct "previous" state. If `--audit` runs AFTER commit, `HEAD:...` is already the current pass and the diff collapses to empty. The skill's rewritten Step 7 prose explicitly sequences: `--all` (regenerate) → `--audit` (diff against HEAD) → `git add` → `git commit`. A `--stale-warn` behaviour (emit stderr warning if the current `design-system.md` on disk exactly equals `HEAD:<pen-dir>/design-system.md`, meaning nothing changed OR `--audit` ran post-commit) makes the failure loud instead of silent.
  - **Subprocess call:** `subprocess.run(["git", "show", f"HEAD:{pen_rel_path}/design-system.md"], cwd=consumer_repo_root, capture_output=True, text=True, check=False)`. `cwd=consumer_repo_root` (NOT `ai_repo_root`) so the git resolution targets the consumer's repo history. `check=False` so we can handle the first-pass case (exit 128 "path not in HEAD") gracefully.
  - **Flow:** (a) Resolve `<consumer-repo-root>` from `--pen` or `--repo-root`. (b) `git show HEAD:<pen-rel>/design-system.md` with `cwd=consumer_repo_root`; on exit 128 emit `"(initial pass — no prior diff)"` placeholder and return 0. (c) Write git output to `<tmpdir>/prev.md`. (d) `npx --no-install @google/design.md diff <tmpdir>/prev.md <pen-dir>/design-system.md` with `cwd=ai_repo_root` (for `npx` package resolution). (e) Python reformats structured diff into `### Variables` and `### New components` markdown tables. (f) Prints to stdout for the skill to paste.

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
  - `tests/unit/tools/test_design_system_sync.py` — determinism (twice-run byte equality on the fixture `.pen`), schema mapping (each prefix category), empty-`.pen` handling, unmapped-variable warning path, lint-failure propagation, `--pen`/`--css-root`/TOML resolution precedence, **`--no-node` fallback path** (monkeypatch `shutil.which` to simulate missing Node; assert `--generate` falls back with warning and `--all` exits 2), **`--audit` repo-root walk** (assert exit 2 + actionable message when run from a tempdir outside any git tree), **`--audit` initial-pass placeholder** (mock `git show` to exit 128; assert the placeholder text is emitted and the command exits 0), **`--audit` stale-warn** (run `--audit` twice without mutating `.pen`; assert second run emits the stderr stale warning).
  - `tests/unit/hooks/test_validate_design_system_sync.py` — hook reads stdin JSON (NOT `$CLAUDE_HOOK_INPUT`); fires only on Bash commands matching the filename regex; returns `decision: block` on drift; returns no-op on non-Bash, non-matching, or unrelated commits (`git add README.md`, etc.); fail-opens on internal error.
  - `tests/unit/hooks/test_validate_design_system_readonly.py` — hook blocks Write/Edit on generated-artifact filenames; permits Write on `.pen`; no-ops on unrelated paths; matcher regex (`"Write|Edit"` vs two blocks) registered successfully.
  - `tests/integration/test_design_system_pipeline.py` — full round trip against the fixture: run `--all --pen tests/fixtures/design_system/design-system.pen` → assert linter exits 0 → assert exports present and non-empty → assert `--check` passes → mutate an artifact → assert `--check` exits 1.

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

### Risk 4: Fixture `.pen` doesn't open in Pencil
**Impact:** If the fixture file has a schema mistake, humans can't verify the tooling end-to-end (and consumer-repo `.pen` files produced by Pencil may hit edge cases the fixture didn't cover).
**Mitigation:** Fixture file is built by reading Pencil's documented `.pen` JSON shape from `SKILL.md` Step 5 (which has a working example at L356-386) and validated by (a) opening the fixture in Pencil manually during the build, (b) round-tripping through the generator and confirming lint exit 0. Validation is a task-completion criterion.

### Risk 5: No existing `.pen` sample to validate against
**Impact:** The generator mapping is spec'd abstractly; a real-world moodboard pass may reveal edge cases (unusual token names, nested component frames, primitive tokens the charter forgot).
**Mitigation:** Fixture `.pen` exercises at least one variable from each category (color, typography, rounded, spacing) plus one reusable component. The first real moodboard pass after landing Phase 1 is expected to surface edge cases; the `--drop-unmapped` flag and the warning-to-stderr path for unmapped variables keep the first pass productive even when mapping gaps appear. Follow-up ticket tracks post-first-pass refinements.

### Risk 6: Validator hook fires in ai/ commits that never touch the design system
**Impact:** If the filename regex is too loose, every `git commit` in ai/ runs the drift check — adding 100-500ms per commit even when nothing to validate.
**Mitigation:** Regex anchors on explicit filenames (`design-system\.(pen|md)`, `/brand\.css`, `/source\.css`). The generic pattern `docs/designs/` is deliberately NOT matched — we match filenames, not directories, so unrelated commits in ai/ early-return. Unit test asserts the hook no-ops on commands like `git add README.md`, `git commit -m "foo"`, and `git add tests/unit/test_unrelated.py`.

### Risk 7: Write/Edit hook matcher regex may not parse `Write|Edit` syntax
**Impact:** If `.claude/settings.json` matcher strings don't support `|` alternation, registering the read-only hook with `matcher: "Write|Edit"` silently fails to match either. The hook never fires.
**Mitigation:** The hook task explicitly validates matcher behavior at registration time (run a dummy Write tool call, confirm the hook fires) before closing the build task. If `Write|Edit` doesn't parse, fall back to two registration blocks — one `matcher: "Write"`, one `matcher: "Edit"`. Either form is fine; the generator task verifies which works.

### Risk 8: ai/-registered hooks don't fire in consumer-repo Claude Code sessions
**Impact:** A Claude Code session's hooks come from `$CLAUDE_PROJECT_DIR/.claude/settings.json`, which is the *current project's* settings, not ai/'s. When the `do-design-system` skill runs inside a consumer repo, neither `validate_design_system_sync.py` nor `validate_design_system_readonly.py` fires on that session's commits or Write/Edit calls. A naive reading of the plan presents the hooks as universal enforcement; they aren't.
**Mitigation:** Documented explicitly in Technical Approach's **"Enforcement reach — ai/ hook vs consumer repos"** subsection. Phase 1 ships three enforcement surfaces with clearly labeled scope: (1) ai/ hook guards the ai/ fixture, (2) CLI `--check` is the cross-repo path, (3) adoption docs in `docs/features/design-system-tooling.md` give consumer repos two copy-paste patterns (git pre-commit hook + settings fragment) to opt into the same protection. Phase 2 (cuttlefish adoption) is when we actually land a consumer-repo registration and discover whether the symlink/env-var approach survives contact with reality. Phase 1 cannot over-promise cross-repo coverage without that validation.

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
- **Fallback:** Behaviour specified in Technical Approach's `_run_npx` / `--no-node` section. Specifically: `python -m tools.design_system_sync --generate` auto-detects missing Node (via the `_probe_npx` precheck) and falls back to Python-only emission (`design-system.md`, `brand.css`, `source.css`) with a stderr warning. Every other subcommand (`--all`, `--audit`, `--check` when exports are part of the comparison set) exits 2 with an actionable "Node required" message unless the user explicitly passes `--no-node`. Full pipeline (lint + exports) requires Node; the fallback is only for the core emission path. This ensures `check=True` never silently propagates — the fallback is explicit and tested.

## Agent Integration

The generator is invoked via Bash from the `do-design-system` skill. **No MCP server, no `.mcp.json` changes, no bridge imports.** This matches the issue's stated constraint and the repo's existing pattern (skills invoke Python CLI modules via Bash — see `do-plan` Phase 1's `python -m tools.code_impact_finder`).

- **Skill rewrite:** `.claude/skills/do-design-system/SKILL.md` Step 6 and Step 7 replaced with generator invocations. Inline safety-gate assertion block at L315-322 **augmented** (three new assert lines) — distinct artifact from the new Write/Edit hook.
- **New validator hooks** (registered in `.claude/settings.json`):
  - `.claude/hooks/validators/validate_design_system_sync.py` — PreToolUse on `Bash`, drift check fires only when the command matches the design-system filename regex. Reads JSON from stdin per existing pattern. Fails open on internal error.
  - `.claude/hooks/validators/validate_design_system_readonly.py` — PreToolUse on `Write`/`Edit`, blocks edits to generated artifacts (design-system.md, brand.css, source.css, DTCG export, Tailwind export) by filename regex. Does not block `.pen` writes.
- **Hook ordering risk:** The existing `matcher: "Bash"` block runs `validate_commit_message`, `validate_merge_guard`, `validate_no_raw_redis_delete` in sequence. Adding `validate_design_system_sync` as the fourth entry means it runs AFTER the existing three. The existing three all early-return on non-matching commands, so ordering has no observable effect. Documented here so a future reader doesn't need to re-derive it.
- **No bridge changes.** `bridge/telegram_bridge.py` does not import or call the new code. The agent invokes the skill, the skill invokes the CLI — that's the whole integration.
- **Integration test:** `tests/integration/test_design_system_pipeline.py` exercises the generator directly against `tests/fixtures/design_system/design-system.pen` and asserts all artifacts land correctly (DESIGN.md with passing lint, `brand.css` and `source.css` under the fixture's `<css-root>`, both exports present). (We do not end-to-end test the agent-running-the-skill path; skill execution is agent-runtime-dependent and not part of the automated suite.)

## Documentation

### Feature Documentation
- [ ] Create `docs/features/design-system-tooling.md` covering: the one-way pipeline (with the Data Flow diagram from this plan), the `.pen` → DESIGN.md schema mapping (the full table from the Solution section), how to regenerate locally (`python -m tools.design_system_sync --all`), the drift validator's behavior, the `--check` / `--audit` / `--drop-unmapped` / `--no-node` / `--repo-root` flag reference, and the **Node-absent fallback semantics** (which subcommands fall back, which exit 2).
- [ ] Add an **"Adopting this in a consumer repo"** section to the feature doc with two copy-paste adoption patterns: (a) a `.git/hooks/pre-commit` shell script that invokes `python -m tools.design_system_sync --check --pen <path> --css-root <path>` and blocks the commit on non-zero exit; (b) a `.claude/settings.json` fragment that registers the ai/-hosted validators via a `DESIGN_SYSTEM_AI_REPO` env var or a vendored symlink. Include the temporal ordering note for `--audit` (must run pre-commit so `HEAD:` still has the prior pass).
- [ ] Add entry to `docs/features/README.md` index table under the `skills` or `design` category (whichever fits the existing grouping after `do-docs-audit.md`).

### Inline Documentation
- [ ] Module-level docstring on `tools/design_system_sync.py` summarizing the pipeline and linking to `docs/features/design-system-tooling.md`.
- [ ] One-line comments on the validator hooks explaining the match regex and fail-open behavior.
- [ ] Update `.claude/skills/do-design-system/SKILL.md` version history block (L499-506) with a `v1.2.0 (2026-04-24)` entry covering the Steps 6 & 7 rewrite and the new safety-gate additions.

### INFRA doc
Not needed for Phase 1. One new devDependency (`@google/design.md@0.1.1`, alpha, free, no auth, no rate limits, local CLI only) does not rise to the bar for a dedicated `docs/infra/` entry. The pin is documented in the feature doc; `package.json` is the canonical record.

## Success Criteria

- [ ] `tests/fixtures/design_system/design-system.pen` exists, is minimal, and opens cleanly in the Pencil desktop app (manual verification). **No `docs/designs/` directory is created in the ai/ repo.**
- [ ] `tests/fixtures/design_system/design-system-sync.toml` declares a fixture `<css-root>` so the generator can be invoked without explicit `--css-root`.
- [ ] `python -m tools.design_system_sync --generate --pen tests/fixtures/design_system/design-system.pen` emits `tests/fixtures/design_system/design-system.md` with DESIGN.md-compliant YAML frontmatter, plus `brand.css` and `source.css` under the fixture's `<css-root>` with byte-identical token names in both files.
- [ ] Running `--generate` twice produces byte-identical output (determinism, unit test).
- [ ] `npx --no-install @google/design.md lint tests/fixtures/design_system/design-system.md` exits 0 on the fixture.
- [ ] `npx --no-install @google/design.md export --format dtcg` and `--format tailwind` both succeed and their output is committed to `tests/fixtures/design_system/exports/tokens.dtcg.json` and `tests/fixtures/design_system/exports/tailwind.theme.json`.
- [ ] `python -m tools.design_system_sync --check --pen tests/fixtures/design_system/design-system.pen` exits 0 on a freshly generated tree and exits 1 with a unified-diff stderr message when an artifact is manually mutated out-of-band.
- [ ] `python -m tools.design_system_sync --audit` runs against a prior pass (or emits the initial-pass placeholder) and prints markdown tables suitable for pasting into `gap-audit.md`.
- [ ] `.claude/skills/do-design-system/SKILL.md` Step 6 and Step 7 rewritten to invoke the generator; inline safety-gate assertion at L315-322 augmented with three `assert` lines refusing writes to `design-system.md`, `brand.css`, `source.css`.
- [ ] Drift-detection validator `validate_design_system_sync.py` registered as PreToolUse on Bash (appended to existing matcher block), reads stdin JSON, blocks commit on drift, fail-open on internal error.
- [ ] Read-only validator `validate_design_system_readonly.py` registered as PreToolUse on Write/Edit, blocks edits to generated artifacts by filename regex, preserves `.pen` whitelist.
- [ ] `package.json` at ai/ repo root pins `@google/design.md@0.1.1`; `package-lock.json` committed; `node_modules/` in `.gitignore`.
- [ ] `docs/features/design-system-tooling.md` exists and covers the pipeline (including `--pen` / `--css-root` / TOML resolution), mapping, regenerate workflow, the two-layer safety-gate model (inline assertion + Write/Edit hook), the Node-absent fallback semantics, the `--audit` temporal-ordering requirement, and the "Adopting this in a consumer repo" section with both adoption patterns.
- [ ] `docs/features/README.md` index updated.
- [ ] `python -m tools.design_system_sync --generate --pen <fixture> --no-node` succeeds with a stderr warning when Node is removed from PATH (unit test covers this by monkeypatching `shutil.which`).
- [ ] `python -m tools.design_system_sync --audit --pen <fixture>` exits 2 with an actionable error when no git repo is found above the pen path AND no `--repo-root` is passed (unit test covers this with a tempdir outside any git tree).
- [ ] `python -m tools.design_system_sync --audit` emits the "initial pass — no prior diff" placeholder when `git show HEAD:<pen-dir>/design-system.md` exits 128 (first-pass case, unit test covers).
- [ ] `scripts/remote-update.sh` runs `npm ci --only=prod` when `package.json` exists.
- [ ] Tests pass (`/do-test`) — new unit tests for the generator, hooks (stdin-JSON fixtures), and integration test.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` exit 0.

## Team Orchestration

### Team Members

- **Builder (generator)**
  - Name: `sync-builder`
  - Role: Implement `tools/design_system_sync.py` CLI, fixture `design-system.pen` + TOML, mapping spec, determinism.
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
  - Role: Run full `--all` pipeline on fixture `.pen`, confirm all artifacts, confirm skill invocation path via Bash documented in SKILL.md works.
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

### 2. Create fixture `.pen` + TOML config
- **Task ID**: build-fixture-pen
- **Depends On**: none
- **Validates**: File opens in Pencil (manual check), JSON parses, contains at least one variable per category (color / typography / rounded / spacing) and one reusable component. `tests/fixtures/design_system/design-system-sync.toml` declares a fixture `<css-root>` under `tests/fixtures/design_system/css/` so the generator test doesn't need flag args.
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/fixtures/design_system/design-system.pen` with a minimal but complete example following the JSON shape documented at `.claude/skills/do-design-system/SKILL.md:356-386`. **Do NOT create `docs/designs/` in the ai/ repo** — the fixture is the ai/ test surface; consumer repos keep their own `docs/designs/`.
- Include: `--color-primary`, `--color-surface`, `--text-body-primary` (color), `--font-sans`, `--text-size-md`, `--text-weight-regular`, `--text-lh-md` (typography forming a `body` preset), `--radius-md` (rounded), `--space-md` (spacing), plus a reusable component `Annotation/Mark`.
- Create `tests/fixtures/design_system/design-system-sync.toml` declaring the fixture `<css-root>` path (pointing at `tests/fixtures/design_system/css/`). The test can invoke the generator with only `--pen` and let TOML resolve `--css-root`.
- Open in Pencil manually to verify the file is editable (record the step in the PR description).

### 3. Implement generator (`tools/design_system_sync.py`)
- **Task ID**: build-generator
- **Depends On**: build-fixture-pen
- **Validates**: Unit tests pass; `--generate --pen tests/fixtures/design_system/design-system.pen` produces `design-system.md` (next to the `.pen`) that passes `npx @google/design.md lint`; twice-run output is byte-identical; `--check` detects manually-mutated artifacts.
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `--generate`, `--check`, `--audit`, `--all` entrypoints. All subcommands accept `--pen` and `--css-root` path args.
- Path resolution precedence: `--pen` / `--css-root` CLI args > `design-system-sync.toml` adjacent to `--pen` > `$CWD/docs/designs/design-system-sync.toml` > error.
- Implement the `.pen` → DESIGN.md mapping per the Solution section's mapping spec. Include `--drop-unmapped` flag for ignoring unknown-prefix variables.
- Shell out to `npx --no-install @google/design.md` for lint and export steps via the `_run_npx` helper. Use `cwd=<ai-repo-root>` so `npx` resolves the pinned package.
- Implement `_probe_npx()` pre-flight check; auto-enable `--no-node` fallback on `--generate` when Node is missing; exit 2 on all other subcommands unless `--no-node` is explicit.
- Implement `--audit` cross-repo git resolution: walk up from `--pen` to find `.git/`, or honor `--repo-root`; run `git show HEAD:<pen-rel>/design-system.md` with `cwd=<consumer-repo-root>`. Emit `--stale-warn` on stderr when the current `design-system.md` exactly matches `HEAD`'s copy.
- Emit artifacts deterministically: alphabetical dict ordering, uppercase hex, fixed YAML style.
- Module docstring with pipeline summary and link to the feature doc.

### 4. Implement drift-detection validator
- **Task ID**: build-drift-validator
- **Depends On**: build-generator
- **Validates**: Hook fires on `git commit` / `git add` touching design-system filenames (regex match), returns `{"decision": "block", ...}` on drift, returns no-op on non-matching commands; fail-open on internal error. Tested via stdin-JSON fixtures (NOT `$CLAUDE_HOOK_INPUT` — hooks read from stdin per `validate_no_raw_redis_delete.py:108-125`).
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/validators/validate_design_system_sync.py` following the pattern of `validate_no_raw_redis_delete.py` (stdin JSON, early-return on non-match, print decision JSON on block, fail-open on exception).
- Path regex: `r"git (add|commit).*\b(design-system\.(pen|md)|brand\.css|source\.css)\b"`. Extract `.pen` path from the command or via TOML walk.
- Register in `.claude/settings.json` under `hooks.PreToolUse` → existing `matcher: "Bash"` block, appended as the fourth entry, 10s timeout.

### 5. Implement read-only artifact validator
- **Task ID**: build-readonly-validator
- **Depends On**: build-generator
- **Validates**: Hook blocks Write/Edit to `design-system.md`, `brand.css`, `source.css`, `*.dtcg.json`, `tailwind.theme.json` by filename regex; preserves `.pen` whitelist (writes to `.pen` files pass through).
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_design_system_readonly.py`.
- Register in `.claude/settings.json` under `hooks.PreToolUse` with `matcher: "Write|Edit"`. If that regex syntax is unsupported at runtime, fall back to two separate blocks (`matcher: "Write"` and `matcher: "Edit"`) — the task must verify which pattern actually registers both tool names.

### 6. Rewrite `do-design-system` Steps 6 & 7 + augment inline safety gate
- **Task ID**: build-skill-rewrite
- **Depends On**: build-generator, build-readonly-validator
- **Validates**: SKILL.md diff; manual read; version-history block updated; inline safety-gate assertion augmented (not replaced); scope of the assertion vs. the Write/Edit hook is documented in the skill prose.
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace Step 6 (CSS sync) with "Run `python -m tools.design_system_sync --all --pen <path> --css-root <path>` (or rely on `design-system-sync.toml`). Verify `npx --no-install @google/design.md lint <pen-dir>/design-system.md` exits 0. Do not hand-edit `brand.css`, `source.css`, or `design-system.md` — the read-only validator will block the Write/Edit." instruction.
- Replace Step 7 (gap-audit table) with "Run `python -m tools.design_system_sync --audit --pen <path>` **before `git commit`** — the audit diffs against `HEAD:<pen-dir>/design-system.md`, so it must execute while `HEAD` still holds the PRIOR pass's `design-system.md`. Running `--audit` after commit produces an empty diff; the generator's `--stale-warn` check emits a stderr warning if that case is detected. Paste the resulting markdown tables into `<pen-dir>/gap-audit.md` under `## YYYY-MM-DD — <theme>`, then commit." instruction.
- Augment the inline safety-gate assertion at L315-322 (correct line range — NOT L314-322) with three additional `assert` lines refusing Python-level writes to generated artifacts. Keep the existing `design-system.pen` target assertion verbatim.
- Add a short paragraph in the skill prose clarifying that the inline assertion and the `validate_design_system_readonly.py` hook are **two separate layers** (Python-level vs tool-level), both intentional.
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
- Integration: edit fixture `.pen`, run `--all`, assert lint 0, exports present, `--check` passes.

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
- Run the full `python -m tools.design_system_sync --all` pipeline end-to-end against the fixture.
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
| Fixture pen exists | `test -f tests/fixtures/design_system/design-system.pen` | exit code 0 |
| Fixture TOML exists | `test -f tests/fixtures/design_system/design-system-sync.toml` | exit code 0 |
| No ai-repo docs/designs created | `test ! -d docs/designs` | exit code 0 |
| Package pinned | `npx --no-install @google/design.md --version` | output contains `0.1.1` |
| DESIGN.md emitted | `test -f tests/fixtures/design_system/design-system.md` | exit code 0 |
| Lint passes on fixture | `npx --no-install @google/design.md lint tests/fixtures/design_system/design-system.md` | exit code 0 |
| Determinism | `python -m tools.design_system_sync --generate --pen tests/fixtures/design_system/design-system.pen && cp tests/fixtures/design_system/design-system.md /tmp/a.md && python -m tools.design_system_sync --generate --pen tests/fixtures/design_system/design-system.pen && diff /tmp/a.md tests/fixtures/design_system/design-system.md` | exit code 0 |
| Drift check clean | `python -m tools.design_system_sync --check --pen tests/fixtures/design_system/design-system.pen` | exit code 0 |
| DTCG export present | `test -f tests/fixtures/design_system/exports/tokens.dtcg.json` | exit code 0 |
| Tailwind export present | `test -f tests/fixtures/design_system/exports/tailwind.theme.json` | exit code 0 |
| Feature doc exists | `test -f docs/features/design-system-tooling.md` | exit code 0 |
| Drift validator registered | `grep -q validate_design_system_sync .claude/settings.json` | exit code 0 |
| Read-only validator registered | `grep -q validate_design_system_readonly .claude/settings.json` | exit code 0 |
| node_modules ignored | `grep -q '^node_modules' .gitignore` | exit code 0 |
| package.json at ai/ root | `test -f package.json && grep -q '0.1.1' package.json` | exit code 0 |

## Critique Results

### Revision pass 1 (2026-04-24)

The initial plan went through `/do-plan-critique` twice. Dispatch history (`python -m tools.sdlc_dispatch get --issue-number 1162`) confirms both runs returned **READY TO BUILD (with concerns)** as the recorded verdict. The `stage_states.latest_critique_verdict = "NEEDS REVISION"` field seen at revision time was stale carry-over from an earlier draft, not the actual critique outcome. The war-room findings were not persisted to `_verdicts.CRITIQUE.findings`, so this revision re-derived them against the rubric and resolved 4 blocker-shaped and 4 concern-shaped findings inline.

**Blockers resolved in this revision:**

1. **Wrong repo assumption (BLOCKER, User + Skeptic + Consistency Auditor).** The initial plan assumed `docs/designs/`, `brand.css`, and `source.css` live in the ai/ repo. They don't, and shouldn't — the `do-design-system` skill is defined in ai/ but operates against **consumer repos** (cuttlefish and similar). Every section that hardcoded `docs/designs/` as an ai/-repo path is rewritten to treat paths as generator arguments (`--pen`, `--css-root`) with a fixture under `tests/fixtures/design_system/` for the ai/ test suite. See **Freshness Check → Key clarification**.

2. **Wrong hook input mechanism (BLOCKER, Skeptic).** Initial plan said validators read `$CLAUDE_HOOK_INPUT`. Verified against `validate_no_raw_redis_delete.py:108-125` that validators actually read JSON from **stdin**. All Technical Approach entries corrected.

3. **Ambiguous "safety gate extension" (BLOCKER, Consistency Auditor).** Initial plan conflated two distinct enforcement surfaces: the SKILL.md inline Python assertion (agent-executed, runtime) and a Write/Edit PreToolUse hook (tool-level, runtime). These are separate artifacts with separate scopes. Revision names and scopes each one explicitly — Task 5 ("read-only validator") and Task 6 ("augment inline assertion") now do distinct work instead of one "extend the safety gate" catch-all.

4. **Wrong line range (NIT, Consistency Auditor).** Initial plan cited `SKILL.md L314-322` for the safety-gate block. The actual header is at L313; the assertion body is L315-322. Corrected throughout.

**Concerns resolved in this revision:**

5. **Validator false-positive risk (CONCERN, Operator).** Initial plan said the drift hook matches `^(git commit|git add).*docs/designs`. In ai/, this would match zero commits (no `docs/designs/`); in consumer repos it would match broadly. Revision anchors the regex on explicit filenames (`design-system\.(pen|md)|/brand\.css|/source\.css`), early-returns on non-match, and adds Risk 6 + a unit test asserting the hook no-ops on unrelated commits.

6. **Write/Edit matcher regex uncertainty (CONCERN, Adversary).** `.claude/settings.json` matcher strings may or may not support `"Write|Edit"` alternation. Revision adds Risk 7, documents the fallback (two separate blocks), and makes matcher-verification a build-task acceptance criterion.

7. **Path-arg contract underspecified (CONCERN, User).** Initial plan assumed hardcoded paths; the revision introduces `--pen`, `--css-root`, and `design-system-sync.toml` with an explicit resolution precedence so the generator is usable cross-repo.

8. **`cwd` handling for `npx` when invoked from consumer repos (CONCERN, Archaeologist).** If the generator is called from a consumer repo's cwd, `npx --no-install @google/design.md` won't find the package pinned in ai/'s `node_modules/`. Revision sets `cwd=<ai-repo-root>` on the subprocess call and documents it in Technical Approach.

**Concerns held over to Phase 2 (by design, not blockers for Phase 1):**

- Consumer-repo adoption (cuttlefish) is explicitly deferred — Phase 1 ships tooling + fixture only.
- CI integration (running `--check` on every PR) is deferred — the hook + manual CLI suffice for Phase 1.

### Revision pass 2 (2026-04-24)

A second pass re-ran the war-room rubric against the revised plan and closed four additional BLOCKER-severity findings that survived the first pass. Dispatch history (`python -m tools.sdlc_dispatch get --issue-number 1162`) confirms the recorded critique verdict is **READY TO BUILD (with concerns)** — Revision pass 2 is treated as a deeper concern-revision-pass that surfaced higher-severity findings the first pass missed, not as a NEEDS REVISION loop. The frontmatter `revision_applied: true` flag is set by the reconciled interpretation from commit `618c9580` — all Implementation Notes across both passes are now embedded in the plan text above.

**Blockers resolved in this revision:**

9. **Acceptance-criteria deviation unacknowledged (BLOCKER, User).** Issue body's ACs reference `docs/designs/design-system.pen` and `docs/designs/exports/` as ai/-repo paths. Revision 1 redirected those to `tests/fixtures/design_system/` but never explicitly reconciled the deviation. This revision adds an **"Acceptance-criteria interpretation (revised)"** subsection under Freshness Check with a per-AC mapping table and an explicit Phase 1 scope-deviation note so a reviewer checking plan-vs-issue sees the re-interpretation in one place instead of having to infer it.

10. **Node-absent fallback contradicts the specified `subprocess.run(..., check=True)` (BLOCKER, Operator + Adversary).** Revision 1 claimed that without Node, `--generate` would still emit Python-only artifacts. But the Technical Approach specified `check=True`, which would raise `CalledProcessError` before any fallback could run. This revision (a) specifies a `_run_npx` helper with explicit `required=True/False` semantics, (b) adds a `_probe_npx()` pre-flight check and an explicit `--no-node` flag on `--generate`, (c) specifies that every other subcommand exits 2 with an actionable message when Node is absent, (d) reconciles the Update System prose, and (e) adds unit-test coverage for all three Node-availability matrices (present-and-ok, present-but-package-missing, absent).

11. **`--audit` mode cross-repo git cwd + temporal ordering underspecified (BLOCKER, Operator + Archaeologist).** Revision 1 said "run `git -C <consumer-repo-root> show` when cross-repo" but never specified how `<consumer-repo-root>` was discovered, nor the required temporal ordering against `git commit`. This revision adds: (a) a `.git/`-walk-up algorithm to find the consumer repo root, (b) an explicit `--repo-root` flag for CI / detached-worktree scenarios, (c) the temporal-ordering requirement (`--audit` runs BEFORE `git commit` — after commit, `HEAD:<pen-dir>/design-system.md` is the current pass, diff collapses to empty), (d) a `--stale-warn` stderr warning when the on-disk and `HEAD:` copies match, and (e) the skill's rewritten Step 7 prose explicitly sequences `--all` → `--audit` → `git add` → `git commit`.

12. **Drift validator's actual reach is only ai/ sessions, not cross-repo (BLOCKER, Archaeologist + Adversary).** Revision 1 presented the PreToolUse hooks as universal enforcement. In reality, `$CLAUDE_PROJECT_DIR/.claude/settings.json` is loaded per session, so a consumer-repo Claude Code session does NOT fire ai/'s hooks on its commits. This revision adds an **"Enforcement reach — ai/ hook vs consumer repos"** subsection to Technical Approach that (a) documents the hook scope honestly, (b) specifies three distinct enforcement surfaces with clear reach, (c) requires the feature doc to ship two copy-paste adoption patterns (git pre-commit shell script + `.claude/settings.json` fragment) so consumer repos can opt in, and (d) adds Risk 8 calling out the limitation explicitly.

**Non-blocker tightening applied in this revision:**

- `--audit` initial-pass placeholder path (git exit 128) covered by unit test.
- `--audit` stale-warn path covered by unit test.
- Success Criteria now includes Node-absent unit test, `--audit` repo-root failure test, and `--audit` initial-pass unit test.
- Feature doc now REQUIRED to contain an "Adopting this in a consumer repo" section with both copy-paste patterns.

### Revision artifact marker

`revised: 2026-04-24` and `revision_applied: true` are both set in the frontmatter. The actual critique verdict from dispatch history is **READY TO BUILD (with concerns)** — the router's Row 4b→4c path. Implementation Notes for all 12 findings across the two revision passes (8 from pass 1 — 4 blocker-shaped, 4 concern-shaped; 4 BLOCKER-severity from pass 2) are embedded into the affected plan sections above. The next SDLC invocation should route to Row 4c (`/do-build`).

---

## Resolved Decisions

The five questions that were open in the pre-critique draft are resolved in-plan as follows. Each is a scope or policy call; if any proves wrong at build time, a follow-up revision is cheap.

1. **Exports layout — `<pen-dir>/exports/` (nested).** Issue text says "`docs/designs/exports/` convention" and the plan follows it. Exports sit beside `design-system.md` under a subdirectory so they are easy to locate and easy to `.gitignore`-exclude as a group if a consumer repo wants to stop versioning them.
2. **Drift validator failure mode — fail-open on internal error.** Documented under Risk 2. Rationale: the hook is additive defense, not the only layer. Blocking commits on a hook bug would be worse than silently missing one drift event; the manual `python -m tools.design_system_sync --check` path and the SKILL.md inline assertion remain as second/third lines of defense. A `logger.warning` is emitted on internal error so the failure is visible.
3. **CI integration — deferred.** Phase 1 ships the hook + CLI only. Running `--check` in CI is a valuable extra layer but adds a dependency on the consumer repo's CI config, which is explicitly out of scope here. Folded into the "Phase 2 follow-ups" list in Critique Results.
4. **Fixture `.pen` aesthetic — pure test fixture, no brand intent.** Generic token names (`--color-primary`, `--font-sans`, `--space-4`) are deliberate. The fixture's job is to exercise the generator's mapping logic deterministically, not to model any real product. Consumer repos will author their own `.pen` in Pencil.
5. **Phase 2 scope — consumer-repo adoption (cuttlefish) deferred.** Phase 1 = ai/-hosted tooling + `do-design-system` skill rewrite + fixture + validators. No consumer-repo PR is landed in this phase. A follow-up issue will track cuttlefish adoption and surface any mapping gaps the fixture did not cover.
