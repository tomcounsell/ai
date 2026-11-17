# Chrome's leaked launch copies fill the Macs' disks (task 7f96f2f1)

## What fills it (observed 2026-11-17 on the Cowboy, 12 GiB free of 228 at plan time)

| Where | Size (du) | What it is |
|---|---|---|
| `~/valor-tasks` | 29 G | 46 workspaces; reclaim runs (178 "removed its finished workspace" lines in kernel.log); what is left finished < 24 h ago or is live |
| `$DARWIN_USER_TEMP_DIR/../X/com.google.Chrome.code_sign_clone` | 16 G | 22 copies of Chrome.app (~730 MB each), one per Chrome launch since 2026-10-20; one is held by the live Chrome, 21 are leaked |
| `~/.ollama` | 12 G | gemma4:e2b 7.2 (named in `~/Desktop/Valor/.env`), granite3.2-vision 2.4, granite4.1:3b 2.1, tev1:0.8b 0.8, nomic-embed-text 0.3, cloud stubs |
| `~/Library/Caches` | 10 G | Homebrew 6.1, Google 1.4, ms-playwright 1.4 (`VALOR_BROWSER`, keep) |
| `~/.cache` | 9.9 G | huggingface 3.4, uv 3.2 (shared, workspaces clone from it), codex-runtimes 1.6 |
| `~/src` | 9.9 G | checkouts |
| `/Applications` 19 G, `/opt/homebrew` 14 G, `Application Support` 8.5 G | | apps, Homebrew |
| unreadable without admin (`/private/var`, Spotlight) | ~12 G | system |

No local snapshots on the data volume (`diskutil apfs listSnapshots disk3s5`). Copies of
one Chrome version are APFS clones and share blocks, so the leaked copies' real cost
is nearer one copy per old Chrome version (6 versions, ~4-5 G); deleting them measures it.

The other Macs (df, same time): Pirate 34 GiB free (23 copies, 17 G du), Captain
**9.3 GiB** (10 copies, 7.2 G), Bald 54 GiB (2 copies, 2.2 G).

## Cause, reproduced

Chrome copies its app bundle at launch and deletes the copy only on a clean exit. With
Playwright (`channel="chrome"`, as `bridges/meet` launches it) on a scratch profile:
`context.close()` and a killed driver (SIGTERM or SIGKILL of the Python process) leave
nothing; SIGTERM or SIGKILL to Chrome itself, and SIGTERM to all of its processes at
once (what `runs._stop` does when a turn or service is reaped), each leave one copy.
Valor stops Chromes by signal: the turn reap, a task's services, `bridges/meet`'s
SIGTERM to a plain Chrome on the call profile; the user's Chrome also dies under the
nightly Homebrew upgrade. Nothing ever removes the copies.

## Fix

`core/workspace.py`: `chrome_clones(root=None)` removes each directory under
Chrome's `code_sign_clone` root (`getconf DARWIN_USER_TEMP_DIR`, parent, `X/…`) that
no process holds open and that is older than ten minutes (a launching Chrome holds
its copy within seconds), with the existing `rmtree`.
- The root is `Path.resolve()`d before lsof runs and before matching: `getconf` gives
  `/var/folders/…`, lsof prints `/private/var/folders/…` (`/var` is a link). Each name
  lsof prints is mapped to its copy by its first path part under the resolved root.
  The live Chrome holds its copy only as its executable (FD `txt`, pid 30700).
- Held is read from what `lsof -Fn +D root` printed, whatever its exit code (it exits 1
  on some errors while still printing matches). It raises only when the exit is
  non-zero, stdout is empty and stderr is not. A missing root is nothing to do.
- `Kernel.reclaim` runs it off the event loop (`git.threaded`, as `remove` runs), after
  the workspace loop, so an lsof failure never skips removing finished workspaces;
  the caller's existing handler logs it to Sentry and the next pass retries.
No new job, check or limit: the existing reclaim pass gains one more thing it
reclaims. Bug fix under hard rule 9.

## Free now (build stage, by hand, measuring `df` before and after each)

Cowboy, then the Captain (under 10 GB) by the same rules:
1. The leaked Chrome copies (the new function, run once by hand).
2. `brew cleanup -s --prune=all`, `uv cache prune`, `npm cache clean --force`, pip
   cache, `~/Library/Caches/Google`, Xcode DerivedData if any, logs older than 30 days.
3. Ollama: remove granite3.2-vision and granite4.1:3b if a grep of configs (`.env`,
   `~/src/*` code/config, launchd plists) finds them only in benchmark result files.
   Keep gemma4:e2b (in `.env`), nomic-embed-text. tev1:0.8b may be locally built (not
   re-pullable): listed for Tom, not removed.
4. Workspaces of merged tasks, and ended/stopped tasks whose delivery names no file
   in the workspace, by `core workspace remove` (the kernel's own removal, ledgered),
   from the kernel's job environment. Never: a waiting or working task, sessions
   (2696e9fb4923, bcfdda8e1251 and other parents), ended tasks with file deliverables
   (0cd8d83cea60, cc21a8902276; the 24 h reclaim takes them as it does today).
5. Swift build of `~/src/yudame-tools` on an APFS copy (its checkout has uncommitted
   edits; task 85b4425898c9 noted main may not compile). A compile error is reported
   as such, not as done.

Does it reach 30 GB? From 12 GiB: Chrome copies 4-5 (shared blocks; 16 du at most),
Homebrew cache up to 6.1 (`brew cleanup -s --prune=all`, then the rest of
`$(brew --cache)`, all re-downloadable), Google cache 1.4, Ollama 4.5, uv prune ~1, npm
0.7, merged and qualifying ended workspaces ~8 (0ba539d217b1, 32168c317ab5, eecfb20133de,
356ab49a64e1, 3d6ea8902d1a, b79a39ee853d, babffe7623c2, 9f86499288a2, 96a09c7b8a53,
85b4425898c9, 2db971a45354, plus any others the ledger shows finished). Total 37-38,
lower bound ~35. If df still reads under 30, the build does not report done: it writes
`.valor/question.md` with the measured gap and the out-of-scope items below, sized,
for Tom to pick from. The bar stays at 30.

## Out of scope (listed for Tom, not deleted)

tev1:0.8b; huggingface 3.4 G and codex runtimes 1.6 G (may be in use); `~/src`
checkouts; `~/valor-retired`; a staged macOS update (Preboot volume 18.6 G).
Changing how turns reap Chrome (a graceful close per process type): more code than
the sweep, and the user's Chrome and Homebrew kills would still leak.

## Tests (`tests/test_chrome_clones.py`, real lsof, temp root)

- an old unheld copy is removed; an old copy a child process holds a file open in is
  kept; a copy younger than ten minutes is kept; files beside the copies untouched;
- the held case again with the root passed through a symlink to `tmp_path` (the
  `/var` case: fails if names are matched unresolved);
- lsof exiting 1 with matches printed keeps them; non-zero, empty stdout and stderr
  raises; a missing root does nothing;
- `Kernel.reclaim` calls it on its pass, and a raising `chrome_clones` still lets the
  pass remove a finished workspace.

Full suite before merge. After rollout: each Mac's copy count drops to its live
Chromes on the next pass, the user's Chrome keeps running, and a Meet call still joins.
