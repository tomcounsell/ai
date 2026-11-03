# Cuttlefish: the episode planner opens with an introduction; the audio worker waits out NotebookLM

Tom, Slack DM 2026-11-03 12:44 UTC: the "Building custom LLMs in late 2026" episode opened
mid-anecdote with no introduction and ran half its length; he asked for the cause fixed.
Task 08b2a9022da2 found two faults in Cuttlefish (yudame/cuttlefish, private) and worked
around both by hand. This task lands both fixes there, one PR each, and touches no episode.

## Where the work happens

A worktree of `~/src/cuttlefish` per fix, off `origin/main` (976c109b), branches
`session/episode-opening-intro` and `session/audio-worker-absent-artifact`, as Cuttlefish's
recent PRs are named. Each: `make check` green, PR opened with `gh` against `main`, then
squash-merged as Cuttlefish's PRs are. Validation there is local-first (no CI).

## 1. Planner (the patch as written)

`~/Desktop/Valor/cuttlefish-patches/0001-…patch` (cf9bbabb) applies cleanly to current
`origin/main` (checked with `git apply --check -3`). Apply with `git am`, keeping its
message. `plan_episode.md` then tells the planner to open with an introduction and the core
terms before the hook, never to skip a greeting or definitions, and to carry the planned
length and segments into `opening_instructions`, which NotebookLM follows. Its test,
`apps/podcast/tests/test_ai_tools/test_plan_episode_opening.py`, asserts the prompt says so.

## 2. Audio worker: a NotebookLM artifact briefly absent from the listing is treated as removed

**Cause, from the 2026-11-03 logs.** `notebooklm-py` 0.8.1's `wait_for_completion` polls the
notebook's artifact list by task id, and after 5 consecutive misses over ≥10 s returns
`status="removed"`; it does not raise. The worker (`local_audio_worker.py`,
`_generate_audio_async`) calls it with the library defaults and ignores the returned status.
On attempt 3, audio `bd35d32e` was created 08:22:50 and was being made for ~14 minutes, then
was missing from the listing for 41 s; at 08:36:49 the library called it removed. The worker
went on, `list_audio()` had no completed audio, the download failed, and the worker's retry
started a fresh generation. The same artifact id was later listed as `completed` in that
notebook and was the audio published by hand. Attempt 1 (`3753d42a`) logged the same removal;
task 08b2a9022da2 reports all four attempts ended this way.

**Fix.** Pass `wait_for_completion` not-found limits sized to the generation budget, so only an
absence lasting the whole `gen_timeout` ends the wait: `min_not_found_window=gen_timeout` and
`max_not_found` large enough that the library's window-independent trigger
(`2 × max_not_found` consecutive misses; polls are ≥2 s apart) cannot fire first. A transient
gap then just keeps polling; a real removal reaches the existing timeout path (check
`list_audio()` for finished audio, else preserve the notebook and raise). A short comment
names the incident. No new status handling, no version bump (the pin's own rule needs an
end-to-end run first).

**Tests** (`apps/podcast/tests/test_local_audio_worker.py`):
- The worker calls `wait_for_completion` with those limits for the resolved `gen_timeout`
  (default and a `--gen-timeout` value).
- Through the library's real poll loop (`notebooklm`'s polling service with a scripted
  `poll_status` and a fake clock): processing → absent for 60 s → completed returns completed,
  not removed, with the worker's limits; with the library defaults it returns removed (shows
  the test reproduces the incident).
- Absent until the deadline still raises `TimeoutError`, so the existing recovery path runs.
- Existing worker tests keep passing unchanged.

## Out of scope

Republishing or changing any episode; NotebookLM's chosen length (23½ of 36 planned
minutes); hosts reading pronunciation hints aloud; NotebookLM login lapses; the spare
notebooks and stuck transcription jobs from 2026-11-03; bumping `notebooklm-py`.

## Delivery

`.valor/done.md`: the two PR links, merged, and what is left open.
