# Small task workspaces: one package cache per Mac, git history borrowed from the cache

**Why.** On 2026-11-06 the Captain fell to 416 MB free with 80+ GB in
`~/valor-tasks`, and a client build stopped. Tom (2026-11-09): make working
copies small enough that several stay active; deploy the quick wins, then
measure how many active tasks the Mac holds before and after.

**Measured on the Captain, 2026-11-09** (`du`, which counts an APFS clone
in full, so venvs overstate):

| Part | Django client task | Client monorepo task | What it is |
|---|---|---|---|
| `cache/uv` | 0.5-2.3 GB | 0.5-1.5 GB | uv's download/wheel cache, one per task, refilled by every task |
| `cache/npm`, `cache/python` | 312 MB, 76 MB | -, 77 MB | npm's cache; a uv-installed Python, per task |
| `repo/.venv` | 480-530 MB | 500 MB (a submodule's) | clones of `cache/uv` files (uv's macOS default link mode) |
| `repo/.git`, `origin.git`, `kernel.git` | 97 MB each | 2-66 MB, up to 316 MB | the same history three times; the monorepo's `kernel.git` grows by each check's submodule clone |
| `checks/*` | 133 MB each | 108 MB each | check worktrees (the monorepo's fetch its submodules again) |
| `checks/seed/` + each check's `cache/` | none live | 0 B (setup installs nothing) | the checks' own package caches: the base run's setup downloads everything again into an empty cache |
| `out/` | 1.1 GB | - | a deliverable (a video) |

The 5-19 GB workspaces of 2026-11-06 were reclaimed before this task; the
live ones show the same parts.

**Why the per-task caches were there.** They date from the 1.4 sandbox,
whose profile write-denied uv's shared cache (f77f0157b) and kept checks
apart from anything the builder wrote. Strip B (c86f22c39) removed that
isolation; a builder's setup already writes what a check then installs
from. With the builder on the Mac's cache, a check's private copy protects
nothing but the downloads it repeats, so it goes too.

## What will be built

1. **One package cache per Mac.** `harness_env` and `check_harness` stop
   setting `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR` and `npm_config_cache`,
   so turns, setup and suites use the Mac's own caches (`~/.cache/uv`,
   uv's python dir, `~/.npm`), as a developer does. uv's and npm's caches
   are safe for concurrent use; venvs are APFS clones (uv's macOS default),
   so pruning the cache never breaks one. The cache is not capped (it was
   19.5 GiB on the Captain on 2026-10-22); the report counts its size as a
   fixed cost per Mac. No cap is added.
2. **The check seed goes.** With no per-check cache there is nothing to
   seed: `checks.SEED`, the setup-only base run that made it
   (`run_suite=False` in `checks.suite`/`base_and_head`), the clones in
   `checks.suite` and the review in `core/fresh.py`, `workspace.clone_tree`
   and `Layout.cache` are deleted; `provision` makes no `cache/`.
3. **The overnight prune no longer blocks the routine.** `uv cache prune`
   waits while any uv process holds the cache (reproduced, uv 0.13.7), and
   after step 1 tasks' long-lived `uv run`s do. `update_runner` starts the
   prune as a detached process (`subprocess.Popen`, new session, output to
   the kernel's log) and returns at once, reporting "uv cache prune
   started"; the prune finishes whenever the cache is free. No timeout,
   no `--force` (uv warns it is unsafe with concurrent use).
4. **Git.** `repo` is cloned with `--reference <cache/<name>.git>`;
   `origin.git` and `kernel.git` get `objects/info/alternates` naming the
   cache's objects before their push/fetch, so each holds only the task's
   own objects. `_cache` sets `gc.pruneExpire never` on the cache (also on
   existing ones), so a borrowed object is never pruned after an upstream
   force-push. `docs_clone` keeps its deliberate object-free copy.
5. **Docs.** `core/workspace.py`'s layout docstring and `check_harness`'s
   comment, `core/fresh.py`'s docstring (what the review reads of the
   builder's), `core/checks.py`'s docstring, `docs/workspace.md`,
   `docs/sdlc-checks-test.md`, `docs/machine.md`: the caches are the Mac's,
   shared by builder and checks; history is borrowed from `cache/<name>.git`.

Existing workspaces keep their Brief's environment; only new ones change.

**Out of scope (named in the report):** the monorepo's per-check
submodule fetch, node_modules (npm copies), deliverables in `out/`, the
reclaim window. No check, gate, quota or limit.

## Tests (`tests/test_kernel.py` and the checks/routine tests; real git, no network)

- A provisioned workspace's env and a suite check's env have none of the
  three cache variables; `provision` makes no `cache/`; a check has no
  `cache/` and no seed is made.
- `repo`, `origin.git`, `kernel.git` name the cache's objects in
  `objects/info/alternates`, hold no copy of the base's history, and
  `fsck` clean; a candidate from `repo` reaches `kernel.git` through
  `fetch_into_mirror` and a check worktree of it checks out.
- Upstream force-push, cache refetched, `git gc` in the cache: the
  workspace still reads its base; `gc.pruneExpire=never` is set, also on a
  cache that existed before. Removing a workspace leaves the cache intact.
- `update_runner` with a stub `uv` that blocks (as a held cache does)
  returns and reports while the stub is still running.
- Existing tests on `harness_env`, the seed and the setup-only run updated
  or removed.

Then the full suite, merge, rollout to the four Macs, and on the Captain a
throwaway workspace (own work dir, removed after) of the Django client
project and of the monorepo at the old and the new main, with setup and one
base suite run: free-disk delta (`df`) and `du` per part, the shared
caches' size as a fixed per-Mac cost, and the base check's time. From
those, how many active tasks fit in the Captain's free space, and whether
an attached SSD is still worth buying.
