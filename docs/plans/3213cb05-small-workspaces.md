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
| `repo/.venv` | 480-530 MB | 500 MB (a submodule's) | clones of `cache/uv` files (uv's macOS default link mode is clone) |
| `repo/.git`, `origin.git`, `kernel.git` | 97 MB each | 2-66 MB, up to 316 MB | the same history three times; the monorepo's `kernel.git` grows by each check worktree's submodule clone |
| `checks/*` | 133 MB each | 108 MB each | check worktrees (plus, for the monorepo, its submodules fetched again per check) |
| `checks/seed/` | (no live task has one) | 0 B (setup installs nothing) | the base suite run's package cache, which starts empty and downloads everything again: one more full uv cache per task that runs a suite |
| `out/` | 1.1 GB | - | a deliverable (a video) |

The 5-19 GB workspaces of 2026-11-06 were reclaimed before this task; the
live ones show the same parts. The Mac-wide `~/.cache/uv` (2.8 GB) already
exists and the overnight update already runs `uv cache prune` on it
(`core/__main__.py` `update_runner`).

**Why the per-task caches were there.** They date from the 1.4 sandbox,
whose profile write-denied uv's shared cache (f77f0157b); Strip B
(c86f22c39) removed that rule, so nothing needs them now. The checks' own
cache copies (`check_harness`, `checks.suite`'s seed) have a live reason,
"nothing one run writes reaches another", and keep it; only where their
first copy comes from changes.

## What will be built

1. **Packages.** `harness_env` stops setting `UV_CACHE_DIR`,
   `UV_PYTHON_INSTALL_DIR` and `npm_config_cache`, so a turn and its setup
   use the Mac's own caches (`~/.cache/uv`, `~/.local/share/uv/python`,
   `~/.npm`), as a developer does. uv and npm caches are safe for
   concurrent use; venvs are APFS clones of the cache (uv's default on
   macOS, no `UV_LINK_MODE` needed). Growth is bounded by the nightly
   `uv cache prune` that already runs. `provision` stops making `cache/`
   and `Layout.cache` goes (nothing would use it).
2. **The checks' first cache.** Where `checks/seed/` does not exist yet
   (`checks.suite` and the review in `core/fresh.py`, the two places that
   today `mkdir` an empty cache), the check's `cache/uv`, `cache/python`
   and `cache/npm` are `clone_tree` copies of the Mac's three caches: APFS
   clones, near free, and a write to a clone never reaches the original,
   so runs stay apart. The base run's setup then downloads only what the
   Mac lacks, and the seed cloned from it stays near free. One helper in
   `core/workspace.py` beside `clone_tree`, used by both places; a Mac
   cache that does not exist is skipped (an empty directory, as today).
3. **Git.** `repo` is cloned with `--reference <cache/<name>.git>`;
   `origin.git` and `kernel.git` get `objects/info/alternates` naming the
   cache's objects before their push/fetch, so each holds only the task's
   own new objects. `_cache` sets `gc.pruneExpire never` on the cache (also
   on existing caches) so an object a workspace borrows is never pruned
   after a force-push upstream. `docs_clone` keeps its deliberate
   object-free copy.
4. Docs: the layout in `core/workspace.py`'s docstring and
   `docs/workspace.md` say the caches are the Mac's and history is
   borrowed from `cache/<name>.git`; `docs/sdlc-checks-test.md` and
   `docs/machine.md` say a check's first cache is a clone of the Mac's.

Existing workspaces keep their Brief's environment; only new ones change.

## Out of scope (named in the report)

The monorepo's
per-check submodule fetch (a spec setup line, `--reference`), node_modules
(npm copies, it cannot clone), deliverables in `out/`, the reclaim window.
No check, gate, quota or limit.

## Tests (`tests/test_kernel.py`, real git, no network)

- A provisioned workspace's harness env has none of the three cache
  variables; `provision` makes no `cache/` in the task root.
- `repo`, `origin.git`, `kernel.git` each name the cache's objects in
  `objects/info/alternates`, hold no copy of the base's history
  (`count-objects` tiny against the cache's pack), and `fsck` clean.
- A candidate commit from `repo` reaches `kernel.git` through
  `fetch_into_mirror` and a check worktree of it checks out.
- Upstream force-push, cache refetched, then `git gc` in the cache: the
  workspace still reads its base (`fsck` clean); the cache has
  `gc.pruneExpire=never`, also when it existed before.
- A suite check with no seed starts with `cache/uv` holding the Mac's
  cache's files (a test uv cache dir set as the Mac's), its writes do not
  appear in the Mac's cache, and the seed made from it is used by the
  head run; with no Mac cache present the check starts empty, as today.
  The review's check (`core/fresh.py`) gets the same first cache.
- Removing a workspace leaves `cache/<name>.git` and its objects intact,
  and a second task provisions from it.
- Existing tests on `harness_env` updated.

Then the full suite, merge, rollout to the four Macs, and on the Captain a
throwaway workspace (own work dir, removed after) of the Django client
project and of the monorepo provisioned at the old and the new main, with
setup run and then one base suite run (`checks.suite` at the base, which
makes the seed): free-disk delta (`df`, which counts clones correctly) and
`du` per part, the seed included. From those, how many active tasks fit in
the Captain's free space, and whether an attached SSD is still worth
buying.
