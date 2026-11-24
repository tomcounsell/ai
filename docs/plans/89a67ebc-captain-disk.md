# The Captain's disk, 2026-11-24: free it, explain it, fix the one leak

Task 89a67ebcf09b. Mostly operations on Valor the Captain over ssh, plus one
bug fix (a commit with a test).

## What the plan stage found (observed, 2026-11-24 ~12:35 UTC, over ssh)

- **Free:** 15 GiB of 228 GiB (`df -h /System/Volumes/Data`).
- **Workspace reclaim is running and keeping up.** The kernel log holds 299
  "removed its finished workspace" lines, the latest today. Of 80 entries
  in `~/valor-tasks`, by each task's ledger fold:
  - 17 merged or ended **today** (~18 GB; earliest 02:38 UTC, so none is
    past the 24 h mark yet). Kept on purpose for feedback and restarts.
  - 33 chat and email sessions, state `clarify` (~5.4 GB). Live by design.
  - 3 waiting on an answer (57505bc4e88b 2.2 GB since 11-19,
    c15366ad8218 0.7 GB since 11-20, 09d91483c2d9 0.7 GB today).
  - 23 left by the daily `hours` routine (one per day since 11-02, state
    `judge`, never provisioned, ~7 MB in all): **the defect below**.
  - `cache`, `bin`, `specialists`.
- **What regrew since the 33 GB reading of 11-21** (that reading followed
  11-21's cache clear, the model deletions and the 15 GB colima deletion):
  `~/.cache/uv` 0 -> 6.2 GB; Homebrew cache 4.6 GB (`brew cleanup -n -s
  --prune=all`: ~5 GB, cache and logs only, no kegs); `~/.colima` 0 -> 3.6
  GB; `~/.cache/huggingface` 0 -> 2.5 GB (the required Parakeet and Silero,
  re-fetched; plus `mlx-community/whisper-medium.en-mlx` and
  `whisper-small.en-mlx`, ~1.9 GB, downloaded today 10:43 and 11:07 UTC by a
  task's turn); and a heavy day of finished workspaces in the 24 h window.
- **Colima:** VM `default` created 2026-11-21 15:58 UTC, stopped 16:20 UTC,
  not running since. That is inside the build turn of task e2a2c0f82154 (a
  client project's demo PDF reports, build turn 14:44-16:21 UTC), the only
  PDF-rendering task active then. It stopped the VM but did not delete it.
  This was before the 11-22 rule that the renderer is removed after use.
- **Voice:** transcribes now (`say` sample -> `POST 127.0.0.1:8714/transcribe`
  returned the sentence exactly).
- **Playwright:** `VALOR_BROWSER` is `chromium_headless_shell-1208`, not
  installed on the Captain (already missing on 11-21). Installed: chromium
  1248/1254, headless_shell 1248/1254/1255, webkit 2388, ffmpeg 1011/1013.

## Build: operations on the Captain

1. Record `df` before. Confirm no turn is mid-install: no `uv`, `pip`,
   `npm`, `npx`, `playwright`, `brew` or `docker` process (`ps -axo
   pid,etime,command`), and no live step whose last tool call is an install.
   If one is, wait for it.
2. Caches: `brew cleanup -s --prune=all`; `uv cache clean` (on a
   `uv cache clean` lock wait, retry once the holder ends); `pip cache purge`
   if a pip cache exists; `npm cache clean --force`. Playwright: map each
   build to the Playwright installs that name it (`browsers.json` in every
   `playwright`/`playwright-core` package under `~/src`, `~/valor-tasks/*`,
   `~/.npm/_npx`, Valor's `.venv`); remove only builds nothing names, never
   one a running process holds (`lsof +D`), never `1208`. `df` after each.
3. Colima: recheck `colima status` / `colima list` shows nothing running,
   then `colima delete -f --data`; confirm `~/.colima` is a few KB (remove
   `~/.colima/_lima/_disks/colima` if a disk remains). Also
   `~/Library/Caches/colima` (0.3 GB, its image cache).
4. Finished workspaces: list tasks merged/ended/stopped more than 24 h ago
   that still have a folder; remove each with `python -m core workspace
   remove <id>` (on the Captain, its own checkout and database). Today the
   list is expected to be empty, since the kernel already did it; say so.
5. The 23 `hours` leftovers: confirm each holds only `titles/` and its run
   has ended (no process under it), then remove them by path. They are not
   workspaces, so `workspace remove` refuses them.
6. Voice: the same `say` -> `/transcribe` check after cleaning; and
   `ls` the Parakeet, Silero and Kokoro files are present.
7. `df` after. Steady state: free space with today's load, minus the
   regrowth rate of uv, Homebrew and the 24 h window (from the 11-21 ->
   11-24 numbers above), stated as a range.

Untouched: `~/src`, Postgres, `~/valor-backups`, `~/.ollama`, Yudame Tools'
models, every model in docs/local-models.md, the two whisper-mlx models
(not on the required list, not mine to delete: reported), the chat and
email sessions' and waiting tasks' workspaces.

## Build: the bug fix

`routines/hours/runner.py` makes `work_dir/<run_id>/titles` for the titling
calls' Claude home and never removes it; the run's child task never reaches
a finished state, so reclaim never sees it. Fix: remove that directory in
the runner's `finally`, after the gateway closes. Test in
`tests/test_hours.py`: after a run (including one whose sync raises), no
`<run_id>` directory is left under the work dir. Adds no check or gate.

## Out of scope

Reclaiming chat or email sessions' workspaces; any quota or new schedule;
installing `chromium_headless_shell-1208` (named in the report); the
client repo's Docker wording.

## Report (text, to the chat session)

Free before/after; each removal with its size; what filled it; the cleanup's
status and the fix; colima's task; the steady-state range.
