---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-07-16
tracking: https://github.com/tomcounsell/ai/issues/2052
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-07-17T04:38:21Z
---

# Per-Worktree Venv Isolation (UV_PROJECT_ENVIRONMENT)

## Problem

Build lanes in `.worktrees/{slug}/` and `.claude/worktrees/{agent}/` have **no
provisioned Python environment**. `agent/worktree_manager.py::create_worktree`
only runs `git worktree add` and copies `.claude/settings.local.json`. Agent
shells inherit `VIRTUAL_ENV=<repo-root>/.venv`, so lane commands (`python`,
`pytest`, `ruff`) resolve to the single shared repo-root env — one lane's
installs collide with siblings and main.

**Current behavior:** Real incident from the 2026-07-16 batch: a lane ended up
with a partial worktree-local venv that lacked `ruff`, blocking its pre-commit
hook until a manual `uv pip install`. The #2050 guard blocks `uv sync` from
worktrees entirely (correct while envs are shared), so a lane cannot legally
self-provision either — the only sanctioned path is scoped `uv pip install`
into the *shared* env, which is exactly the cross-lane coupling this issue
targets.

**Desired outcome:** Every worktree created by `create_worktree` gets its own
complete `.venv` (all extras, from the lockfile) at creation time, so
`uv sync` from a worktree is non-destructive to sibling lanes and the main
checkout **by construction**. The #2050 guard relaxes from "block" to "allow +
notice" for worktrees that have an isolated env, and keeps blocking for
unprovisioned ones.

## Freshness Check

**Baseline commit:** `ebd94886c`
**Issue filed at:** 2026-07-13T06:49:39Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/worktree_manager.py:866-955` (`create_worktree`) — still provisions no
  per-worktree environment — holds.
- `.claude/hooks/validators/validate_no_uv_sync_in_worktree.py` — the #2050
  guard landed after the issue was filed (PR #2057, commit `c472e875f`); it
  blocks all `uv sync` from worktree cwd unconditionally. This plan coordinates
  with (not deletes) that guard.
- `tools/venv_health.py` + `cleanup_after_merge` warn-only backstop — present,
  unchanged by this plan.

**Cited sibling issues/PRs re-checked:**
- #2050 — CLOSED via PR #2057 (merged 2026-07-14). Ships the minimum-bar guard;
  its plan explicitly defers per-worktree isolation to this issue.

**Commits on main since issue was filed (touching referenced files):**
- `c472e875f` Add PreToolUse guard blocking uv sync from a worktree (#2050) —
  partially addresses (guard only; no isolation).

**Active plans in `docs/plans/` overlapping this area:** none (checked
`ls -lt docs/plans/`; `guard-uv-sync-in-worktree` is completed/migrated).

## Prior Art

- **#2050 / PR #2057**: `uv sync` from a worktree strips the shared `.venv` —
  shipped the PreToolUse block guard + `tools/venv_health.py` lane-exit
  backstop. Succeeded as a stopgap; explicitly deferred isolation to #2052.
- **#887**: session-isolation bypass — `valor-session create` without
  `/do-plan` skips tier-2 worktree isolation. Related context: worktree
  provisioning must stay inside `create_worktree` (the single chokepoint) so
  every creation path inherits it.

## Research

**Empirical probe (recorded in issue #2052 Recon Summary)** — non-destructive
`uv sync --dry-run --all-extras` from a `.claude/worktrees/` checkout on
uv 0.6.10, four env configurations:

- uv discovers the project by walking up from cwd, finds the **worktree's own**
  `pyproject.toml`, and targets a **worktree-local** `.venv` ("Would create
  virtual environment at: .venv") in every configuration.
- `VIRTUAL_ENV=<repo-root>/.venv` is explicitly **ignored** with a warning
  ("does not match the project environment path `.venv` … use `--active`").
- `UV_PROJECT_ENVIRONMENT` set to an absolute worktree path or a relative
  `.venv` produces the identical target. Relative values resolve against the
  discovered project/workspace root — for a worktree checkout that root **is
  the worktree** (it contains `pyproject.toml`), so both spellings are safe;
  the plan uses the **absolute** path to remove any dependence on uv's
  project-discovery walking from a subprocess cwd.
- Consequence: on current uv the shared-env stripping requires `--active` or an
  older uv; the *live* failure mode is a lazily-created **minimal** worktree
  env (missing dev extras — the 2026-07-16 `ruff` incident). Eager
  `--all-extras` provisioning closes it.
- uv installs by **hardlinking from its global cache** — N worktree envs cost
  incremental disk (mostly directory metadata + non-linkable files), not N full
  copies, and a warm-cache sync is seconds. Measured numbers go into
  `docs/features/worktree-venv-isolation.md` at build time.

No WebSearch needed beyond this — behavior was verified empirically against
the exact uv binary in production.

## Data Flow

1. **Entry point:** `/do-build` (or worker executor) calls
   `get_or_create_worktree(repo_root, slug)` → `create_worktree`.
2. **`create_worktree`:** `git worktree add` → copy `settings.local.json` →
   **NEW:** `provision_worktree_venv(worktree_dir)`.
3. **`provision_worktree_venv`:** runs `uv sync --all-extras` with
   `cwd=worktree_dir`, env = `os.environ` minus `VIRTUAL_ENV`, plus
   `UV_PROJECT_ENVIRONMENT=<worktree_dir>/.venv` (absolute). Lockfile-driven,
   hardlinked from uv cache. Fail-open on error (WARNING log, worktree still
   returned).
4. **Lane runtime:** commands in the worktree find `<worktree>/.venv`; the
   #2050 guard sees `<worktree-root>/.venv/pyvenv.cfg` and allows `uv sync`
   with a notice instead of blocking.
5. **Teardown:** `.venv` lives inside the worktree dir → removed by the
   existing `remove_worktree` / `git worktree remove --force` path. `.venv` is
   already gitignored.

## Appetite

**Size:** Small

**Team:** Solo dev (Dev session), code reviewer for PR.

**Interactions:**
- PM check-ins: 0-1 (final report)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `uv` on PATH | `uv --version` | Provisioner shells out to uv (already a machine prerequisite — `/update` uses `uv sync`) |

## Solution

### Key Elements

- **`provision_worktree_venv(worktree_dir)`** (new, `agent/worktree_manager.py`):
  provisions a complete worktree-local env from the lockfile. Fail-open.
- **Success marker `.venv/.provisioned`** (critique blocker fix): written by
  `provision_worktree_venv` only AFTER `uv sync` exits 0. Distinguishes a
  complete env from one interrupted mid-sync (`pyvenv.cfg` is written near the
  start of env creation, before packages install).
- **`create_worktree` hook-in**: calls the provisioner after worktree creation
  (eager). Existing-worktree early-return path also re-provisions when
  `.venv/.provisioned` is absent — this heals both pre-existing lanes and
  interrupted syncs on reuse. This retroactive healing is an intentional scope
  addition beyond the issue's literal "at creation time" ask (backward-compat
  healing of already-created lanes).
- **Guard relaxation** (`validate_no_uv_sync_in_worktree.py`): new
  `_worktree_root(path)` + isolation probe (`<root>/.venv/pyvenv.cfg` exists).
  Isolated → allow with a one-line `systemMessage` notice (warn, not block).
  Unprovisioned → block, with the message extended to describe the bootstrap
  path (`uv venv .venv` first, then `uv sync` is allowed because the worktree
  is now isolated). The guard probe deliberately keys on `pyvenv.cfg`, NOT the
  `.provisioned` marker: allowing `uv sync` against a partial worktree-local
  venv is the *repair* action (it completes that env), whereas requiring the
  marker would dead-end the bootstrap path (`uv venv` never writes the marker,
  so `uv sync` would stay blocked forever). The hazard the blocker identified
  — a partial env silently treated as complete — is closed at the reuse path
  (marker-keyed re-provisioning), not by blocking the repair command.
- **`TimeoutSettings.uv_sync_s`** (new field, `config/settings.py`): timeout
  for the provisioning subprocess. Default 600s — provisional/tunable, env
  `TIMEOUTS__UV_SYNC_S` (cold-cache first sync downloads packages; warm-cache
  is seconds).

### Technical Approach

- **Eager over lazy provisioning.** Lazy (first-use) provisioning was exactly
  the incident class: uv lazily created a *minimal* env and the lane broke at
  pre-commit. Eager provisioning at `create_worktree` time makes the lane's
  first command already isolated and complete. Cost: one warm-cache
  `uv sync --all-extras` (~seconds) per worktree creation.
- **`--all-extras` to mirror the machine env** (`scripts/remote-update.sh`
  documents `uv venv && uv sync --all-extras` as the canonical env). A lane
  must be able to run `pytest`/`ruff`/pre-commit without touching the shared
  env.
- **Env hygiene in the subprocess:** strip `VIRTUAL_ENV` (avoids the uv
  mismatch warning and any legacy-uv `--active`-like behavior), set
  `UV_PROJECT_ENVIRONMENT` to the **absolute** `<worktree>/.venv` (verified
  equivalent to relative, chosen for cwd-independence).
- **Fail-open provisioning, fail-safe guard.** If uv is missing or sync fails,
  log a WARNING and return the worktree anyway — the lane still works against
  the shared env, and the guard *keeps blocking* `uv sync` there when no
  worktree-local `.venv` exists at all. A failed sync that left a partial
  `.venv` behind is re-provisioned on next reuse (marker absent) and remains
  `uv sync`-repairable in the meantime (guard allows).
- **Operator-visible failure signal** (critique concern fix): provisioning
  failures log with a greppable tag `[worktree-venv-provision-failed]`
  including the worktree path and a stderr tail, so `checking-system-logs`
  and log-scanning reflections can surface them — not just a generic
  `logger.warning` lost in `logs/worker.log`.
- **Guard warn semantics:** for isolated worktrees the hook prints
  `{"systemMessage": "..."}` (no `decision` key) and exits 0 — Claude Code
  treats that as allow + a visible notice. The CLI/test invocation path prints
  the notice to stderr and exits 0.
- **`.claude/worktrees/` (harness agent isolation) scope:** these worktrees
  are created by the Claude Code harness, not by `worktree_manager` — there is
  no code seam of ours in their creation path, so **eager provisioning there is
  out of scope**. They get the *lazy/manual* path: the guard's bootstrap
  message (`uv venv .venv` → then `uv sync` allowed) makes self-provisioning a
  sanctioned two-command sequence instead of a blocked dead-end. This is the
  explicit disposition the issue asked for.
- **`tools/venv_health.py` stays.** It protects the shared env (main checkout)
  and remains the backstop for exotic bypasses; only its docstring note about
  "no per-worktree isolation" gets updated.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `provision_worktree_venv` catches subprocess failure
  (`CalledProcessError`, `TimeoutExpired`, `FileNotFoundError` for missing uv)
  — test each asserts a `logger.warning` call and a `False` return, and that
  `create_worktree` still returns the worktree path (fail-open).
- [ ] Guard `find_violation` remains fail-open on unparseable input — existing
  tests already cover; new isolation probe wrapped in the same try/except.

### Empty/Invalid Input Handling
- [ ] `_worktree_root` on paths with no worktree component returns None →
  guard allows (unchanged behavior for non-worktree cwds).
- [ ] `provision_worktree_venv` on a nonexistent directory: returns False with
  warning (test).

### Error State Rendering
- [ ] Block message (unprovisioned worktree) includes the bootstrap
  instructions — asserted by test substring match.
- [ ] Provisioning failure logs include the worktree path and stderr tail so
  the operator can diagnose from `logs/worker.log`.

## Test Impact

- [ ] `tests/unit/test_validate_no_uv_sync_in_worktree.py` — UPDATE: existing
  block-case tests use synthetic paths with no real `.venv` on disk, so they
  keep passing (not isolated → still blocked); add new cases: isolated
  worktree (tmp_path with `.worktrees/slug/.venv/pyvenv.cfg`) → allowed;
  unprovisioned tmp worktree → blocked; `.claude/worktrees` variant → allowed
  when isolated; subdirectory-of-worktree cwd resolves to worktree root.
- [ ] `tests/unit/test_worktree_manager.py` — UPDATE: `create_worktree` tests
  now invoke provisioning; patch `provision_worktree_venv` in existing tests
  (unit tests must not shell out to uv); add new tests for
  `provision_worktree_venv` env construction (UV_PROJECT_ENVIRONMENT absolute,
  VIRTUAL_ENV stripped, cwd=worktree, `--all-extras` present) via mocked
  `subprocess.run`, fail-open behavior, and skip-if-already-provisioned.
- [ ] `tests/unit/test_config_settings.py` (or equivalent settings test) —
  UPDATE only if it asserts the full TimeoutSettings field set; otherwise
  additive.

## Rabbit Holes

- **Symlinking/sharing site-packages across worktrees** to save disk — uv's
  hardlink cache already dedupes; a shared site-packages reintroduces exactly
  the coupling this plan removes. Do not.
- **Provisioning `.claude/worktrees/` from inside the harness** — we own no
  creation seam there; anything beyond the guard-bootstrap path is speculative
  harness integration. Do not.
- **Version-pinning or upgrading uv** as part of this change — the empirical
  probe covers the deployed uv; a uv upgrade is orthogonal machine maintenance.
- **Full shell parsing in the guard** — already ruled out by the #2050 plan;
  the relaxation reuses the existing parsing untouched.

## Risks

### Risk 1: Provisioning time on cold uv cache
**Impact:** First worktree creation on a machine with a cold cache downloads
~173 packages; lane creation could take minutes.
**Mitigation:** `TIMEOUTS__UV_SYNC_S` (default 600s, tunable); fail-open on
timeout (lane proceeds on shared env, guard still protects). Machines that ran
`/update` have a warm cache by construction (update runs `uv sync`).

### Risk 2: Disk growth across many worktrees
**Impact:** N worktrees × env. Hardlinks make marginal cost small, but
apparent size may alarm operators; non-hardlinkable artifacts still copy.
**Mitigation:** Envs die with their worktree (existing `remove_worktree` /
worktree-gc). Measure real incremental cost (`du` of one provisioned worktree
vs. hardlink-aware count) at build time and record it in the feature doc.

### Risk 3: Branch-divergent lockfile in a worktree
**Impact:** A worktree on an older/newer branch syncs to *its* lockfile — that
env may differ from main's. This is correct (it's the branch's declared deps)
but could surprise a lane expecting a main-only package.
**Mitigation:** By-construction isolation means the surprise is contained to
the lane; the block-message-turned-notice names the env being targeted.

### Risk 4: Guard relaxation opens a hole for the shared env
**Impact:** If the isolation probe misfires (e.g. matches repo-root `.venv`),
`uv sync` could be allowed somewhere destructive.
**Mitigation:** Probe requires a worktree component match first (existing
`_is_worktree_path` semantics), then checks `.venv/pyvenv.cfg` strictly under
the **worktree root** — the repo root is never a worktree path, so the shared
env keeps full block protection. Covered by explicit tests.

## Race Conditions

No race conditions identified — provisioning is a synchronous subprocess call
inside `create_worktree`, which is already serialized per slug (worktree dir
existence is the idempotency key; concurrent creations of the same slug were
already unsupported before this change). The guard reads immutable-at-check
on-disk state (`pyvenv.cfg` existence) and fails open/blocked exactly as
before on any error.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2052] — this IS the deferred slug from #2050; nothing
  further is deferred out of it. Eager provisioning of harness-created
  `.claude/worktrees/` is intentionally NOT a follow-up issue: it has no code
  seam we own (see Technical Approach) and the guard-bootstrap path fully
  covers it. Nothing else deferred — every relevant item is in scope for this
  plan.

## Update System

One config-catalog touch, no update-flow changes: the new
`TimeoutSettings.uv_sync_s` field requires a commented placeholder in
`.env.example` (`# Timeout for per-worktree uv sync provisioning` +
`TIMEOUTS__UV_SYNC_S=600`) per the repo's completeness check — every existing
`TIMEOUTS__*` field carries one (critique History & Consistency finding).
Otherwise no update system changes: worktree envs are runtime artifacts created
per-lane by `worktree_manager` on whatever machine runs the lane — `/update`
(`scripts/update/run.py`, `remote-update.sh`) continues to manage only the main
checkout's `.venv`, and `uv` is already a machine prerequisite installed and
used by the update flow. No new dependencies, no migrations (no Popoto model
changes). The guard hook and provisioner ship as ordinary repo code via the
normal `git pull` in `/update`.

## Agent Integration

No new CLI entry point or MCP surface required. Provisioning is invoked
internally by `create_worktree` — the chokepoint already used by `/do-build`
and the worker executor — so every agent-driven lane inherits it with zero new
wiring. For harness-created `.claude/worktrees/` checkouts (agent Bash tool),
the integration surface is the **guard message itself**: it now teaches the
sanctioned bootstrap (`uv venv .venv`, then `uv sync`), which the agent
executes through its existing Bash tool. The hook is already registered in
`.claude/settings.json`; no settings change needed.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/worktree-venv-isolation.md`: design, eager-vs-lazy
  decision, empirical uv resolution findings, measured disk/time cost, the
  bootstrap path for harness worktrees, fail-open/fail-safe pairing.
- [ ] Update `docs/features/uv-sync-worktree-guard.md`: guard now relaxes to a
  notice for isolated worktrees; block message changed; cross-link.
- [ ] Add entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstrings on `provision_worktree_venv` and the guard's isolation probe
  explaining the shared on-disk fact (`.venv/pyvenv.cfg`) keying both.
- [ ] Update stale "no per-worktree isolation" comments in
  `tools/venv_health.py`, `cleanup_after_merge`, and the guard header.

## Success Criteria

- [ ] `create_worktree` produces a worktree whose `.venv/bin/python` exists and
  imports `pytest` + has `bin/ruff` (integration-style unit test, mocked in
  pure-unit runs).
- [ ] `uv sync` from an isolated worktree is allowed by the guard; from an
  unprovisioned worktree it is still blocked with bootstrap instructions.
- [ ] Repo-root `.venv` is never matched by the isolation probe (explicit test).
- [ ] Provisioning failure does not fail worktree creation (fail-open test).
- [ ] Tests pass (`/do-test`, narrow scope: the two touched test files).
- [ ] Documentation created/updated per Documentation section.

## Team Orchestration

Solo Dev session executes directly (Small appetite, three-file source blast
radius plus docs — fan-out would add coordination overhead, not speed). A `code-reviewer` agent
reviews the PR.

- **Builder**: Dev session (this session) — implementation + tests
- **Reviewer**: code-reviewer agent — PR review at REVIEW stage

## Step by Step Tasks

### 1. Provisioner + settings knob
- **Task ID**: build-provisioner
- **Depends On**: none
- **Validates**: tests/unit/test_worktree_manager.py
- Add `TimeoutSettings.uv_sync_s` (default 600.0, ge=30, le=3600, provisional
  comment, env `TIMEOUTS__UV_SYNC_S`) + commented `.env.example` placeholder.
- Add `provision_worktree_venv(worktree_dir: Path) -> bool` to
  `agent/worktree_manager.py`; write `.venv/.provisioned` marker only after
  `uv sync` exits 0; tag failures `[worktree-venv-provision-failed]` with
  worktree path + stderr tail; wire into `create_worktree` (fresh-create path
  AND existing-worktree early-return path when `.venv/.provisioned` absent).
- Unit tests (mocked subprocess): env construction, all-extras flag, marker
  written on success / not on failure, fail-open on
  CalledProcessError/TimeoutExpired/FileNotFoundError, skip when marker
  present, re-provision when marker absent; patch provisioner in existing
  create_worktree tests.

### 2. Guard relaxation
- **Task ID**: build-guard-relax
- **Depends On**: none (parallel-safe, disjoint files)
- **Validates**: tests/unit/test_validate_no_uv_sync_in_worktree.py
- Add `_worktree_root()` + `_is_isolated_worktree()` to the guard; allow with
  `systemMessage` notice when isolated; extend block message with bootstrap
  instructions; keep CLI mode symmetric (stderr notice, exit 0).
- Tests: isolated-allow, unprovisioned-block, repo-root never isolated,
  subdir-of-worktree resolution, fail-open preserved.

### 3. Real-world provisioning measurement + docs
- **Task ID**: document-feature
- **Depends On**: build-provisioner, build-guard-relax
- Provision one real worktree; record wall time (warm cache) and incremental
  disk (`du -sh` apparent + hardlink-aware) in the feature doc.
- Interrupted-sync probe (critique concern): kill a real `uv sync` mid-install
  in a scratch worktree; confirm `pyvenv.cfg` exists but `.provisioned` does
  not (validates the marker design empirically); record in the feature doc.
- Note in the feature doc that this end-to-end run is a one-off manual
  validation — the automated gates are mocked-subprocess proxies.
- Write/update the three Documentation items.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: all above
- Run Verification table; open PR.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Touched unit tests pass | `.venv/bin/python -m pytest tests/unit/test_worktree_manager.py tests/unit/test_validate_no_uv_sync_in_worktree.py -q -n0` | exit code 0 |
| Lint clean | `python -m ruff check agent/worktree_manager.py .claude/hooks/validators/validate_no_uv_sync_in_worktree.py config/settings.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/worktree_manager.py .claude/hooks/validators/validate_no_uv_sync_in_worktree.py config/settings.py` | exit code 0 |
| Provisioner wired into create_worktree | `grep -c "provision_worktree_venv" agent/worktree_manager.py` | output > 1 |
| Guard knows isolation | `grep -c "pyvenv.cfg" .claude/hooks/validators/validate_no_uv_sync_in_worktree.py` | output > 0 |
| Success marker in provisioner | `grep -c ".provisioned" agent/worktree_manager.py` | output > 1 |
| Tagged failure log | `grep -c "worktree-venv-provision-failed" agent/worktree_manager.py` | output > 0 |
| .env.example placeholder | `grep -c "TIMEOUTS__UV_SYNC_S" .env.example` | output > 0 |
| No shared-env hole (anti-criterion: repo root never treated as worktree) | `python .claude/hooks/validators/validate_no_uv_sync_in_worktree.py "uv sync" "$HOME/src/ai"` | exit code 0 |
| Unprovisioned worktree still blocked | `python .claude/hooks/validators/validate_no_uv_sync_in_worktree.py "uv sync" "$HOME/src/ai/.worktrees/no-such-lane"` | exit code 1 |
| Timeout knob exists | `grep -c "uv_sync_s" config/settings.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Partial-provisioning venv passes the isolation probe and the reuse check (`pyvenv.cfg` written before packages install) | Addressed with modification: `.venv/.provisioned` success marker written only after `uv sync` exit 0; reuse path re-provisions when marker absent. Guard keeps `pyvenv.cfg` probe deliberately — allowing `uv sync` on a partial worktree venv is the repair action; marker-keying the guard would dead-end the `uv venv` → `uv sync` bootstrap path | Marker touch AFTER `check=True` subprocess.run; reuse condition = marker absent, not `.venv` absent |
| CONCERN | Risk & Robustness | Dry-run probe produced no evidence about interrupted-sync on-disk state | Addressed: Task 3 adds a kill-mid-install probe of a real `uv sync`; result recorded in feature doc | Confirms `pyvenv.cfg` exists / `.provisioned` absent after interruption |
| CONCERN | Risk & Robustness | Fail-open provisioning failures are log-only, invisible to operators | Addressed: greppable `[worktree-venv-provision-failed]` tag with worktree path + stderr tail | Discoverable by checking-system-logs / log-scanning reflections |
| CONCERN | History & Consistency | Update System said "no config propagation" but new `TIMEOUTS__UV_SYNC_S` needs an `.env.example` placeholder like all 12 existing `TIMEOUTS__*` fields | Addressed: Task 1 adds commented placeholder; Update System section amended | Comment line above `KEY=` required by completeness check |
| NIT | Scope & Value | Early-return healing exceeds the issue's literal "at creation time" ask | Addressed: marked as intentional backward-compat healing in Key Elements | — |
| NIT | Scope & Value | Only real-world validation is a one-off manual step | Addressed: acknowledged in Task 3; automated gates are mocked proxies | — |
| NIT | History & Consistency | "Two-file blast radius" undercounts | Addressed: corrected to three-file + docs | — |
