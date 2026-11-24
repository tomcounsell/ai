# Captain follow-ups: routine runs left open, the screenshot browser, two Whisper models

Follow-ups task 89a67ebc named and left. Stakes: the code change writes
`task.ended` rows (permanent ledger rows) on every Mac's routine runs; the
rest is a reversible install and the deletion of two re-downloadable models.

## 1. A routine's finished run listed as open (bug)

**Observed.** `valor-record tasks` lists each routine run as `judge` for
good (the hours runs; five verify runs on the Cowboy, 11-19 to 11-23).
**Cause, found in build:** a run starts with no `sdlc` marker, so
`machine.fold` reads it as legacy: the kernel never schedules it, and
`tasks.open_ids` and the status page (`node`) already leave it out.
`record._state` alone printed the legacy fold's default state, `judge`.
(The critiqued design, writing `task.ended` on each run, would have
changed nothing: the legacy fold ignores that row.)

**Fix:** `machine.label(rows)`, the status page's labels (`calibration`,
`stopped`, `node`, else the state), used by `tasks.index` and
`record._state`; a chat session that is not stopped still reads `open`.
**Test** (`tests/test_record.py`): a routine's objective and run read
`node`, an SDLC task `judge`, a stopped session `stopped`; it fails on the
old code.

## 2. The screenshot browser on the Captain

Task e54879f9 installed `chromium_headless_shell-1208` on all four Macs on
11-21 with `uv tool run --from playwright==1.58.0 …`. On the Captain it is
gone. **Likely cause (to confirm from the Captain's records):** that
command's Playwright lives in uv's cache, so its `.links` entry, the only
thing naming build 1208, went dead when a cache clean removed it; the next
Playwright install anywhere without `PLAYWRIGHT_SKIP_BROWSER_GC` (a
client app's `pnpm` installs, `.links` entries from 11-24 04:44 Bangkok
time on) deleted every build no live link names. `.links` today has no
1.58 entry. Task 89a67ebc's disk cleanup is ruled out as the direct
cause on its own record: its plan turn (11-24 ~12:35 UTC) already found
1208 missing, before its `uv cache clean --force` (~13:05 UTC), and it
removed no Playwright browser. The build confirms that from its ledger and
transcript, and finds what did: the uv cache clears since 11-21 (any task
or the 11-21 cleanup, task 59166b42) and the first Playwright install after
each, and says observed or assumed.

**Fix:** install from a Playwright that is not a cache:
`uv venv ~/.local/share/valor-browser && uv pip install --python
~/.local/share/valor-browser playwright==1.58.0 &&
PLAYWRIGHT_SKIP_BROWSER_GC=1 ~/.local/share/valor-browser/bin/playwright
install chromium-headless-shell`, and `docs/rollout.md` step 2 and
`docs/browser.md` say so and why. Done on the Captain; on the other three,
check that 1208 is there and its link live, and re-anchor any that is not.
**Verify:** `INSTALLATION_COMPLETE` at the `VALOR_BROWSER` path, a live
`.links` entry for the venv, and `tools/look` saving a screenshot on the
Captain (the image opened and looked at).

## 3. Two Whisper models on the Captain

`mlx-community/whisper-medium.en-mlx` and `whisper-small.en-mlx`, in
`~/.cache/huggingface/hub`, downloaded 11-24 17:43 and 18:07 Bangkok time
by a client task transcribing a video. Neither is required. **Before:** no
process holds their blobs (`lsof`), no `mlx_whisper` process runs, and the
downloading task's state is read; if it is still open, say it re-downloads
on next use. **Delete** with `hf cache rm -y model/…` (the doc's command),
measuring `blobs/` and `df` before and after. Parakeet and Silero stay.
**After:** the voice service transcribes a spoken test sentence, as task
89a67ebc checked it. `docs/local-models.md` gets a dated Deleted table row
with sizes; no client named (public repository).

## Out of scope

The nightly Homebrew script (Tom's folder). Any other model or cache.
Docker. The Captain's checkout lagging `main` (its rollout will carry this).

## Scope additions

None.
