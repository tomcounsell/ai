# Silence npm warnings in the /update pipeline

## Problem

On bridge machines, the `/update` pipeline's `npm ci` step (in
`scripts/remote-update.sh`) emits two warnings to stderr on every run:

```
npm warn config only Use `--omit=dev` to omit dev dependencies from the install.
npm warn deprecated mdast@3.0.0: `mdast` was renamed to `remark`
```

Both are noise that clutters the update summary and can mask real
warnings. This plan silences both at the source.

## Root Cause

1. **`npm warn config only`** — `scripts/remote-update.sh` invoked
   `npm ci --only=prod`. The `--only` flag is deprecated; npm now wants
   `--omit=dev`.
2. **`npm warn deprecated mdast@3.0.0`** — `@google/design.md@0.1.1` (the
   only production dependency in the repo-root `package.json`) declared a
   direct dependency on the deprecated bare `mdast@3.0.0` package (renamed
   to `remark`). Bumping `@google/design.md` to `0.3.0` drops that
   dependency entirely; the `0.3.0` tree contains only the healthy
   `mdast-util-*` and `@types/mdast@4` packages, no bare `mdast`.

## Freshness Check

Verified against the committed working tree: `git show HEAD:package-lock.json`
shows `@google/design.md@0.1.1` with a direct `"mdast": "^3.0.0"` dep and a
`node_modules/mdast` → `mdast-3.0.0.tgz` entry. The `0.3.0` lock has no bare
`node_modules/mdast` entry.

## Appetite

Small — config/script/docs only. No new modules, no behavioral change to
the generator (the `--all` pipeline still produces byte-identical artifacts
against the fixture under `0.3.0`, verified by the integration test).

## Solution

- `scripts/remote-update.sh`: `npm ci --only=prod` → `npm ci --omit=dev`.
- `package.json`: pin `@google/design.md` `0.1.1` → `0.3.0`.
- `package-lock.json`: regenerated (drops bare `mdast@3.0.0`).
- Update all doc/source references to the old flag and old version pin:
  `.claude/skills/update/SKILL.md`, `docs/features/design-system-tooling.md`,
  `docs/features/README.md`, `tools/design_system_sync.py`.
- `tests/integration/test_design_system_pipeline.py`: read the pinned
  version from `package.json` instead of hardcoding `0.1.1`, so future
  bumps don't require a test edit.

## Failure Path Test Strategy

The npx probe / `--all` pipeline is exercised by the existing integration
test against the committed fixture. If `@google/design.md@0.3.0` changed
emitted format, that test would fail on artifact drift. It passes, so the
bump is format-compatible.

## Test Impact
- [ ] `tests/integration/test_design_system_pipeline.py::_npx_present` — UPDATE:
  read pinned version from `package.json` rather than the `0.1.1` literal.
- [ ] `tests/unit/tools/test_design_system_sync.py` — UPDATE: docstring
  reference `--only=prod` → `--omit=dev` (comment only, no assertion change).

## Rabbit Holes

- Do NOT force-resolve `mdast` via an override — the correct fix is dropping
  the offending direct dependency by bumping `@google/design.md`.
- Do NOT bump `@google/design.md` past a version that changes emitted DTCG /
  Tailwind format without re-verifying the fixture pipeline.

## No-Gos (Out of Scope)

- No changes to the generator's emission logic.
- No changes to the drift-validator hooks.

## Update System

This change IS an update-system change: it edits
`scripts/remote-update.sh` (the cron-mode update entrypoint) and the
`/update` skill doc (`.claude/skills/update/SKILL.md`). No new dependencies
or config files are introduced. The `npm ci` step remains fail-soft
(non-pipefail subshell + `|| echo`), so a missing npm or transient install
failure still never aborts the parent update. Existing installations pick
up the flag change on their next `/update` run — no migration step needed.

## Agent Integration

No agent integration required — this is an update-path / build-tooling
change. No new CLI entry point in `pyproject.toml [project.scripts]`, and the
bridge does not import any of the changed code. The `@google/design.md`
package is invoked only via `npx` by `tools/design_system_sync.py`, which is
already wired.

## Documentation
- [ ] Update `.claude/skills/update/SKILL.md` Node-toolchain block to
  `npm ci --omit=dev`.
- [ ] Update `docs/features/design-system-tooling.md` version pin (`0.3.0`)
  and `--only=prod` references.
- [ ] Update `docs/features/README.md` design-system-tooling row pin to
  `0.3.0`.

## Success Criteria

- `npm ci --omit=dev` at the repo root emits zero `npm warn` lines.
- No bare `mdast@3.0.0` in `package-lock.json`.
- `tests/integration/test_design_system_pipeline.py` and
  `tests/unit/tools/test_design_system_sync.py` pass under `0.3.0`.
- No `--only=prod` or `0.1.1` references remain in docs/scripts/source.

## Step by Step Tasks

1. Switch `remote-update.sh` to `--omit=dev`.
2. Bump `@google/design.md` to `0.3.0`; regenerate lock.
3. Update all doc/source references.
4. Make the integration test read the pin dynamically.
5. Run affected tests; run `npm ci --omit=dev` and confirm clean output.

## Verification

```
$ npm ci --omit=dev
added 92 packages, and audited 93 packages in 528ms
74 packages are looking for funding
found 0 vulnerabilities
# (no npm warn lines)
```
Tests: 20 passed (integration pipeline + design_system_sync unit).
