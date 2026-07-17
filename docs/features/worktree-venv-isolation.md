# Per-Worktree Venv Isolation

**Issue:** [#2052](https://github.com/tomcounsell/ai/issues/2052) · **Plan:** `docs/plans/worktree-venv-isolation.md` · **Predecessor:** [#2050](https://github.com/tomcounsell/ai/issues/2050) (`uv sync` worktree guard — see [uv-sync-worktree-guard.md](uv-sync-worktree-guard.md))

Every SDLC lane worktree created by `agent/worktree_manager.create_worktree`
gets its own complete Python environment at creation time, so `uv sync` from a
worktree is non-destructive to sibling lanes and the main checkout **by
construction**.

## Design

### Eager provisioning (chosen over lazy)

`create_worktree` calls `provision_worktree_venv(worktree_dir)` immediately
after `git worktree add`:

- Runs `uv sync --all-extras` with `cwd=<worktree>`, `VIRTUAL_ENV` stripped
  from the subprocess env, and `UV_PROJECT_ENVIRONMENT` pinned to the absolute
  `<worktree>/.venv` (cwd-independent; empirically equivalent to uv's default
  project discovery, verified on uv 0.6.10).
- `--all-extras` mirrors the canonical machine env (`uv venv && uv sync
  --all-extras` in `scripts/remote-update.sh`), so `pytest`, `pytest-xdist`,
  and `ruff` are present — pre-commit hooks and test runs never fall back to
  the shared env.
- Timeout: `settings.timeouts.uv_sync_s` (default 600s, env
  `TIMEOUTS__UV_SYNC_S`, provisional/tunable).

Lazy (first-use) provisioning was rejected because it was exactly the incident
class this fixes: on 2026-07-16 a lane ended up with a lazily-created *minimal*
worktree venv that lacked `ruff`, blocking its pre-commit hook until a manual
`uv pip install`.

### Success marker: `.venv/.provisioned`

`pyvenv.cfg` is written near the **start** of env creation, before packages
install — its existence cannot distinguish a complete env from one interrupted
mid-sync. `provision_worktree_venv` therefore touches
`.venv/.provisioned` (`PROVISIONED_MARKER`) only after `uv sync` exits 0.

The existing-worktree early-return path in `create_worktree` re-provisions
whenever the marker is absent. This heals two cases on reuse: lanes created
before this feature shipped, and lanes whose provisioning sync was interrupted
(timeout, OOM, kill). The retroactive healing is an intentional scope addition
beyond the issue's literal "at creation time" ask.

### Fail-open provisioning, fail-safe guard

Provisioning failures (uv missing, sync error, timeout, marker write failure)
log a WARNING tagged `[worktree-venv-provision-failed]` — greppable by
`checking-system-logs` and log-scanning reflections — with the worktree path
and a stderr tail, then return `False`. Worktree creation never fails on a
provisioning error: the lane still works against the shared env, and the
#2050 guard keeps blocking `uv sync` there because no worktree-local `.venv`
exists.

### Guard relaxation (#2050 coordination)

`.claude/hooks/validators/validate_no_uv_sync_in_worktree.py` relaxes from
block to **allow + notice** for isolated worktrees:

- **Isolated** (`<worktree-root>/.venv/pyvenv.cfg` exists): `uv sync` is
  allowed; the hook emits a non-blocking `{"systemMessage": ...}` notice
  (CLI/test mode prints the notice to stderr, exit 0).
- **Unprovisioned** (no worktree-local `.venv`): still blocked, with the
  message now teaching the bootstrap path (below) alongside the scoped
  `uv pip install` alternative.

The guard probe deliberately keys on `pyvenv.cfg`, NOT the `.provisioned`
marker: allowing `uv sync` against a partial worktree venv is the *repair*
action (uv completes that env in place), and requiring the marker would
dead-end the bootstrap path (`uv venv` never writes the marker). The
partial-env hazard is closed at the reuse path instead (marker-keyed
re-provisioning). The repo root is never a worktree path, so the shared env
keeps full block protection.

### `.claude/worktrees/` (harness-created agent worktrees)

The Claude Code harness creates these directly — there is no
`worktree_manager` seam in their creation path, so eager provisioning there is
**out of scope**. They get the sanctioned two-command bootstrap instead:

```bash
uv venv .venv      # creates the worktree-local env (allowed — not `uv sync`)
uv sync --all-extras   # now allowed: the worktree is isolated
```

### Teardown

The env lives inside the worktree directory (`.venv` is gitignored), so the
existing `remove_worktree` / worktree-gc paths delete it with the worktree. No
new cleanup wiring.

## Measured cost (2026-07-17, uv 0.6.10, macOS/APFS)

One-off manual validation — the automated test gates are mocked-subprocess
proxies; these numbers came from a real provisioning run on a warm uv cache:

- **Wall time:** 68s for a fresh worktree (includes building the editable
  wheel); a subsequent sync with everything cached completes in under a
  second.
- **Disk:** apparent size 844 MB per worktree `.venv`, but uv's default link
  mode on macOS/APFS is copy-on-write **clones** from its global cache
  (`st_nlink` stays 1, blocks are shared) — physical incremental cost for
  cached packages is near zero.
- **Interrupted-sync probe** (validates the marker design): `uv sync` killed
  (SIGKILL) 0.25s in left `pyvenv.cfg` present, site-packages partially
  populated (360 of 365 entries), and no `.provisioned` marker — confirming
  `pyvenv.cfg` alone cannot signal completeness, and that the reuse path's
  marker check re-provisions exactly this state.

## Operator notes

- Provisioning failures: `grep worktree-venv-provision-failed logs/worker.log`
- Force re-provisioning of a lane: delete `<worktree>/.venv/.provisioned` (or
  the whole `.venv`) and call `create_worktree`/`get_or_create_worktree` for
  the slug again.
- The shared repo-root `.venv` is still backstopped by `tools/venv_health.py`
  at lane exit (`cleanup_after_merge`), unchanged from #2050.

## Update system

No `/update` changes: worktree envs are runtime artifacts, `uv` is already a
machine prerequisite, and the only config touch is the `TIMEOUTS__UV_SYNC_S`
placeholder in `.env.example` (propagates like any other settings field).
