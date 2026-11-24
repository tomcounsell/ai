# Captain follow-ups: routine runs left open, the screenshot browser, two Whisper models

Follow-ups task 89a67ebc named and left. Stakes: the code change writes
`task.ended` rows (permanent ledger rows) on every Mac's routine runs; the
rest is a reversible install and the deletion of two re-downloadable models.

## 1. A routine's finished run stays open (bug)

**Observed.** `routines.hours` starts its run with `tasks.start_child` and
returns `Ran(run_id, "finished", …)`; nothing ever writes a terminal row, so
the run folds to `judge` for good and `tasks.open_ids` lists it. Not only
hours: `summary`, `conditions` and `verify` do the same. On the Cowboy,
`valor-record tasks` lists five verify runs (11-19 to 11-23) in `judge`
though each has `suite.ran` and `workspace.removed`. The open runs count in
every queue line and the reconciler's list of a project's open tasks.

**Fix, in one place: `core/routines.py` `_fire`.** After it appends
`routine.ran`, it ends every run that the objective's `routine.ran` rows
(the new one and earlier ones) name `finished` or `failed` and that is not
yet merged, ended or stopped. The row is written by `routines` itself, not
`verdicts.end` (whose payload is `reason`, `by: kernel`): under
`task:<run>`, folded first as `verdicts.end` does, `task.ended`
`{"kind": "decided", "by": "routine", "text": …}`. `text` is
`Done: <summary>` for `finished`, the summary for `failed`, so
`notices.ended_text` and `record._result` read it as any decided end.
Earlier runs are ended at each routine's next firing on each Mac, so the
backlog (23+ hours runs on the Captain, the verify runs here) clears with
no hand-run script. `started`, `continued`, `running` and `nothing_due`
end nothing, so `open_node`'s sweep continues as now.

**A runner that raises.** Hours (and conditions, summary) start a new child
every firing, so a run whose runner raises after `start_child` gets no
`routine.ran` and would stay open forever. `_fire` notes the ledger's last
event id before calling the runner; when the runner raises, it ends each
child of the objective whose `task.started` (marker naming this routine)
is past that id, with `text` `the run raised: <repr>`, and re-raises. The
error still surfaces as it does today (the command exits non-zero, the
launchd log has the traceback); no runner changes, and the existing
`pytest.raises` test in `tests/test_hours.py` stands, with one more assert.
A verify run continued by a later firing is not past the id, so a raise
in a continued sweep leaves it to its next firing, as now.

Consequence, accepted: each ended run owes its quiet `ended` report
(`notices.owe`, a routine result: wakes nothing). The first firing after
rollout writes one per backlogged run.

**Tests** (with the routine tests that drive `routines.run`, real ledger):
a `finished` run is ENDED with `Done: <summary>`, `by: routine`, and out of
`open_ids`; a `failed` run is ENDED with the bare summary;
`started`/`running` runs stay open and `open_node` still returns them;
`nothing_due` writes nothing; an earlier `finished` run still open is
ended at the next firing; a stopped or already-ended run gets no second
row; another routine's open run is untouched; in
`test_a_run_that_fails_leaves_no_directory` (sync and drain raising) the
run is ENDED with `the run raised: RuntimeError(...)` and the error still
propagates; a runner raising before it starts a child ends nothing.

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
