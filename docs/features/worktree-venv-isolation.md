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

The existing-worktree path in `create_worktree` always calls
`provision_worktree_venv`, which decides for itself whether there is anything
to do. It re-provisions when the marker is absent, healing two cases on reuse:
lanes created before this feature shipped, and lanes whose provisioning sync
was interrupted (timeout, OOM, kill). The retroactive healing is an
intentional scope addition beyond the issue's literal "at creation time" ask.

### The interpreter pin: `.python-version` is authoritative (#2572, #2617)

**`.python-version` at the repo root is the single authoritative pin.** It is
committed, and deliberately **not** gitignored — `.gitignore` carries a note
saying so, because the file's absence is what caused this. `pyproject.toml`
keeps `requires-python = ">=3.11"` as the dependency-resolution floor only; it
is not a pin and it is not consulted for interpreter selection.

There is no second mechanism to drift against. `[tool.uv]` has no key for this:
uv rejects `python` as an unknown field, and its documented interpreter-request
file *is* `.python-version`. So every `uv sync`, `uv venv`, and `uv run` in this
repo or any of its worktrees resolves to the pinned `MAJOR.MINOR` with no flag
and no wrapper, including the bare commands an agent types by hand.

Without it, an unpinned `uv sync` builds each venv on whatever interpreter uv
happens to find newest. Worktrees drifted away from the checkout their results
were compared against: one host ran its main checkout on 3.14.3 and its
worktrees on 3.13.2, another on 3.12 and 3.13. That silently invalidates the
pipeline's core verification step — "reproduce it on main" and "diff against
the baseline" both assume the two runs differ only in the code. It already cost
a clean test-only diff a false accusation, and a 536 MB off-pin venv appearing
inside a worktree broke that lane's pre-commit hook into reporting a lint block
with no findings.

- `repo_interpreter_pin` reads the committed `.python-version` (this working
  tree's, falling back to the main checkout's for a worktree predating the
  pin), accepting `3.14`, `3.14.3`, and `cpython@3.14`.
- `worktree_interpreter_pin` returns that pin. The main checkout's own
  `.venv/pyvenv.cfg` version is a **fallback only**, for a checkout predating
  #2617: a checkout venv that has itself drifted must not propagate that drift
  into every worktree provisioned from it.
- `provision_worktree_venv` passes `--python <pin>` to `uv sync`, and re-syncs
  a marker-present venv whose `MAJOR.MINOR` no longer matches, logging
  `[worktree-venv-interpreter-drift]`. That is what heals worktrees already on
  disk when a lane next touches them.
- `MAJOR.MINOR` is the comparison granularity because it is the axis
  interpreter behavior varies on, and because uv writes `3.13` for an env
  built from its own managed download and `3.12.13` for one built from a
  system interpreter.

**Changing the fleet's Python** is therefore one edit: bump `.python-version`,
then `rm -rf .venv && uv sync --all-extras` per checkout. Doctor names every
env still on the old version.

#### A pin bump strands the old interpreter's bytecode (#2883)

Replacing the venv does **not** clean the source tree. Bytecode caches are
namespaced per interpreter (`module.cpython-314.pyc`), so a pin bump *orphans*
the previous interpreter's caches rather than replacing them. CPython never
stats, validates, or deletes a cache whose magic tag is not its own, which makes
an orphaned `.pyc` immortal — nothing invalidates it and no import heals it.

That is not merely untidy. These files are real Python to any tool that reads
the filesystem rather than tracked content: a stale pre-fix `.pyc` under
`tools/__pycache__` already failed a clean source tree once (#2807/#2809).
`PYTHONDONTWRITEBYTECODE=1` in `scripts/pytest-clean.sh` prevents new ones but
cannot remove those on disk, and does not apply to a bare `python -m tools.x`.
`tools/disk_reclaim.py` cannot find them either — it is size-ranked and these
are kilobytes.

`python -m tools.doctor` reports them (`stale_bytecode`), broken down by
interpreter tag, with the sweep command. It is reported rather than swept
automatically because deletion is the operator's call; the check skips `.venv/`
and `.worktrees/`, which are replaced wholesale and already covered by
`worktree_interpreters`.

```bash
find . -name '*.pyc' -not -path './.venv/*' -not -path './.worktrees/*' \
  | grep -v cpython-<pin> | xargs rm -f
```

### Enforcement on the ambient paths

Pinning is not enough on its own, because a venv built before the pin landed
stays wrong until something says so. Three checks report it as itself rather
than as a downstream symptom:

- `python -m tools.doctor` (`worktree_interpreters`) measures **every** venv on
  the machine against the pin: the main checkout's own, every
  `.worktrees/*/.venv`, and every `.claude/worktrees/*/.venv` (harness-created
  agent worktrees, which nothing provisions and where the bare `uv sync` this
  issue is about is exactly what gets typed).
- `python -m tools.doctor` (`stale_bytecode`) reports source-tree `.pyc` caches
  orphaned by a pin bump — the dimension the interpreter check does not cover,
  since a wrong *venv* and stranded *bytecode* are different failures. See
  [A pin bump strands the old interpreter's bytecode](#a-pin-bump-strands-the-old-interpreters-bytecode-2883).
- `scripts/pytest-clean.sh` **aborts** before running anything in two cases. If
  the venv is off the pin: a suite that runs to green on the wrong interpreter
  produces a verdict that looks authoritative and is worthless. And if the
  caller is a **linked worktree without an executable `.venv/bin/pytest`**
  (#3033) — absent venv, half-provisioned venv from a failed `uv sync`, or a
  sync without `--extra dev` — see
  [The absent-venv case](#the-absent-venv-case-3033) below.
- `.githooks/pre-commit` blocks with an explicit "broken environment, NOT a
  lint failure" message when `ruff` is missing from the resolved interpreter,
  and warns (without blocking) on an off-pin venv.

The comparison itself lives in `scripts/check-interpreter-pin.sh` — one
implementation, called by both shell callers, so they cannot drift apart.

### Fail-open provisioning, fail-safe guard

Provisioning failures (uv missing, sync error, timeout, marker write failure)
log a WARNING tagged `[worktree-venv-provision-failed]` — greppable by
`checking-system-logs` and log-scanning reflections — with the worktree path
and a stderr tail, then return `False`. Worktree creation never fails on a
provisioning error, and the #2050 guard keeps blocking `uv sync` there because
no worktree-local `.venv` exists.

What such a lane must NOT do is run its test suite. Fail-open provisioning is
why a venv-less worktree can exist at all; it is not a licence to test in one.
The guard below is what makes the fail-open safe.

### The absent-venv case (#3033)

A linked worktree with **no `.venv`** does not fail to import — it resolves
imports through the primary checkout's editable path entry. The branch's tests
then exercise `main`'s code, always find a real module, and never raise. The run
reports green on code it never loaded.

That direction matters: the failure is biased toward green, so it hides exactly
the regressions the run exists to catch. In PR #3028 it produced a confidently
false "1545 unit tests pass" in the PR body, and the one genuinely failing test
surfaced only because a reviewer forced `PYTHONPATH`.

The off-pin check does not cover this. Off-pin means a *wrong* venv; absent
means *no* venv, which degrades silently rather than mismatching.

`scripts/pytest-clean.sh` therefore refuses to run from a linked worktree unless
an executable `.venv/bin/pytest` exists, naming the worktree, what is missing,
and the remedy (`uv sync --extra dev` in the worktree). Mere `.venv` presence is
not enough — that weaker check was bypassed two ways in practice: a failed
`uv sync` creates `.venv` before dying, and a sync without `--extra dev`
provisions a venv with no pytest in it, both of which fell through to PATH and
silently tested the primary checkout. The abort message distinguishes a missing
venv from an incomplete one. The guard keys on `.git` being a **file** — a
linked worktree's gitdir pointer — so a primary checkout is unaffected, and the
wrapper invokes pytest through the venv (`.venv/bin/python -m pytest`), never by
PATH resolution.

The wrapper also exports `PYTHONPATH` with the invoking checkout's root
prepended (preserving any caller value). This closes the third observed
mechanism: even a fully provisioned worktree resolves `tools.*` through the
primary checkout's editable-install `.pth` entry, so without the pin a
venv-equipped worktree can still import `main`'s code. With it, the checkout
being tested always wins import resolution.

Verified live (2026-08-31): in a real venv-less worktree,
`import tools.sdlc_stage_query` resolved to the primary checkout and the
pre-guard script reported "8 passed". `tests/unit/test_worktree_venv_absent_guard.py`
pins that the guard fires, names all three facts, and does not over-reach onto
worktrees that have a venv or onto the primary checkout.

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

The env lives inside the worktree directory (`.venv` is gitignored), so whatever
removes the worktree removes the env with it. Two callers do that: `remove_worktree`
(via `cleanup_after_merge`, on the post-merge path) and the daily `disk-reclaim`
sweep for lanes nobody cleaned up by hand. See
[Scheduled Disk Reclaim](scheduled-disk-reclaim.md).

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

  Re-measured 2026-08-07 with a `df` delta across a real `uv sync` on a warm
  cache: `du` reported **541 MB**, free space fell by **9 MB**. A 60x gap, so
  `du` over a `.worktrees/` tree is an upper bound and not a disk-pressure
  number. The docstring in `agent/worktree_manager.py` that said "hardlinks"
  was corrected to match; both stories imply "near-free", but only clones
  explain `st_nlink == 1`.
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
- Interpreter drift: `grep worktree-venv-interpreter-drift logs/worker.log`, or
  `python -m tools.doctor` for what is on disk right now.
- The shared repo-root `.venv` is still backstopped by `tools/venv_health.py`
  at lane exit (`cleanup_after_merge`), unchanged from #2050.

## Update system

No `/update` changes: worktree envs are runtime artifacts, `uv` is already a
machine prerequisite, and the only config touch is the `TIMEOUTS__UV_SYNC_S`
placeholder in `.env.example` (propagates like any other settings field).
