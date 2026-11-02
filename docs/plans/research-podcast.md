# Research to podcast: report first, episode on Tom's word

Task 4341141f63ef. Tom, Slack DM, 2026-11-02 03:59 UTC: "If I give you a topic,
can you publish it to the Yudame Podcast while bringing the final report pdf back
to me as well?" 04:02 UTC: "confirm with me about publishing if I don't specify up
front. For private topics, we might ... publish to the Yudame Staff podcast instead
of the public Research one. Either way, full research and report generation
shouldn't delay."

Done when Tom has had a report PDF and an episode he asked for from one topic (rule 5).

## Found (2026-11-02)

- Production podcasts: `yudame-research` (Yudame Research, public) and
  `yudame-staff` (Yudame Staff Podcast, unlisted). Both pass Cuttlefish's publish
  payment check (`payment_gate.publish_block_reason`).
- Cuttlefish's pipeline: `episode create --defer-setup` → `attach --require` →
  `setup` writes the brief. Its research phases run only through its own Claude Code
  skill `/research-workflow <slug> --auto-advance`. Audio comes from its
  `notebooklm-audio` skill (NotebookLM through the BYOB Chrome's Google session).
  After that, the Render worker transcribes, makes assets and publishes with no
  approval step. No HTTP API covers create/produce/publish.
- A turn reaches production with Valor's `RENDER_API_KEY` (1Password `Valor env`):
  the Render API returns the `cuttlefish` env group (`DATABASE_URL`, Supabase,
  keys). Nothing in Cuttlefish needs a `.env.local`.
- Research reaches Tom through a task: the chat starts it, it runs
  `valor:researcher`, and its delivery carries the PDF (`converse.md`).

## Build

1. **`run.py episode <dir> --podcast <slug>` produces and publishes.** This
   replaces today's draft-only verb, and `--podcast` becomes required. It returns at
   once and runs a detached worker, the same pattern as the research worker, writing
   `<dir>/episode.json` (`state`, `step`, `slug`, `url`, `error`). The worker:
   - waits for `<dir>/status.json` to reach `done`, so it can start alongside the
     research; on `failed`, it records that and stops;
   - takes the production env from the Render API's `cuttlefish` env group, looked up
     by name, into the subprocess env only (never written to disk);
   - uses the managed checkout (`find_cuttlefish`), so `CUTTLEFISH_DIR` is no longer
     needed;
   - runs Cuttlefish's verbs in this order: `create --defer-setup` (title from the
     question, description from the report's Summary), `attach report.md` (into the
     research corpus, not `--require`), then `setup`. Built 2026-11-02: a required
     input turns on Cuttlefish's tables-only analyst mode, and with `web_scope:
     "none"` the live run failed at Targeted Research (1 of 3 sources). So
     Cuttlefish runs its own research again, with the report as one more source;
   - runs `claude -p "/research-workflow <slug> --auto-advance"` in the checkout;
   - for audio, uses Cuttlefish's primary path, `manage.py local_audio_worker
     --base-url https://yudame.ai --max-concurrent 1`. It has no single-episode
     option, so it's started only when `GET /api/podcast/pending-audio/` lists this
     episode alone, and stopped once the episode leaves that list. If any other
     episode is waiting, the run records it as blocked, naming those episodes, and
     doesn't start the worker, so no other episode gets audio it didn't ask for.
     I'm not using the `notebooklm-audio` skill: it calls itself the fallback, and it
     needs BYOB cookie pulls and wakeups that headless `claude -p` may not do;
   - gets NotebookLM auth before the worker starts. The publish task's turn pulls
     the BYOB Chrome's Google cookies and runs the checkout's
     `scripts/notebooklm_refresh_auth.py`, which writes
     `~/.notebooklm/storage_state.json`. If the worker stops on expired auth, the run
     records `failed: notebooklm auth`;
   - polls `episode show <slug> --json` until `published_at` is set. `published_at` is
     the only signal of done; it doesn't matter which machine published. The
     workflow's expected `paused_for_human` with `blocked_on=audio_generation` is the
     signal to start audio. Any other pause or a `failed` workflow records `failed`
     with its step and `blocked_on`, and stops;
   - allows 4 hours in total: about 40 min for the workflow, 15-45 for audio, 5-30
     for transcription, assets and publish. Past that, it records `failed: timeout`
     with the step;
   - `url` is the episode's public page.

   Reuse: every step is Cuttlefish's own command, skill or worker. The only new code
   is the sequencing and env loading.
2. **Wording.**
   - `SKILL.md` "A podcast episode" and `agents/researcher.md`: the line that says a
     person publishes goes. The new text says an episode is made from a finished
     report on Tom's word, to the podcast he named, and that the research run never
     waits for that decision.
   - `skills/sdlc/converse.md`, one paragraph on research for Tom:
     - start the research task at once, whatever is or isn't said about a podcast;
     - send the PDF when it's back;
     - if Tom named publishing and a destination with the topic, also start a publish
       task at once, on the same run directory (`run.py episode` waits for the
       report);
     - if he didn't, ask with the PDF: publish or not, Research or Staff, with a
       recommendation (Staff when the topic names a client, a person, or internal
       business; Research otherwise);
     - start the publish task only on his answer, since silence is not a yes;
     - the publish task checks `episode.json` in short calls for up to 4 hours, then
       delivers the episode link or the step it failed at.
   - No new check or gate. Nothing refuses a call; the decision lives in the wording,
     and the capability does what it's asked.
3. **Tests** (`tests/test_research.py`, all stand-ins, no network):
   - *report not delayed:* `start()` returns at once and the research worker never
     calls `episode`. The run reaches `done` with no podcast argument anywhere.
   - *episode verb:* waits for research `done` and stops on `failed`. Runs
     create→attach→setup→research workflow→audio step→audio worker→publish, with the
     given slug on every Cuttlefish call. The report is attached without `--require`,
     so it is in the research corpus and not a required input. Records `published`
     and `url` from `published_at`.
   - *pauses and failures:* the `audio_generation` pause leads to audio. Another
     pause, a failed step, expired auth or a timeout records `failed` with its reason.
   - *audio queue:* with other episodes pending, the audio worker is not started and
     the run says which episodes are waiting.
   - *env:* comes from the stand-in Render response and is not written into the run
     dir.
   - *routing and consent wording:* `converse.md` and `SKILL.md` say:
     - research starts without waiting on the publish decision;
     - publishing happens only on Tom's say-so, with the topic or in his answer;
     - private topics go to `yudame-staff`, otherwise to `yudame-research`;
     - the ask carries a recommendation.
   - Replace `test_episode_drafts_and_never_publishes`. Update the
     `test_orchestration` researcher assertions if the agent text moves.
4. **Live run, on the Cowboy, pre-merge.** One small real topic on how Valor works
   internally, as a private test. Research → PDF → `run.py episode --podcast
   yudame-staff`.
   - Before running it, read who `send_new_episode_emails` would mail for
     `yudame-staff`: the owners of its active subscriptions. Publishing triggers that
     email wherever it runs. If it would mail anyone but Tom, stop at audio done and
     say so.
   - The PDF and the episode link go in the delivery. If NotebookLM auth, the audio
     queue or the research workflow stops it, the delivery says at which step, with
     evidence.

## Found while building (2026-11-02)

- Render: the web service's own `LOCAL_WORKER_API_KEY` overrides the env group's,
  so `production_env` reads both, service last.
- Nothing in Cuttlefish queues `step_audio_generation` after `/research-workflow`, so
  the episode sat at Audio Generation, running. `run.py` queues it once.
- The audio worker needs Cuttlefish's `podcast` extra (pydub).
- NotebookLM moved to `notebook.google.com` and its login lapses within about 30
  minutes. A retry makes a new notebook, so the publish task refreshes the login
  while the audio is being made.

## Not in scope

- Cuttlefish code changes. A Cuttlefish defect found on the way is named in the
  delivery and goes to its repo as an issue, not patched here.
- A Staff/Research picker in code. The choice is the session's, in wording.
- Skipping Cuttlefish's phases 2–8 by writing our run's artifacts into its tables
  would be a second pipeline.

## Governance

None added. No grant needed.

This repository is public, so commits name only Yudame, Tom and Valor. The live-run
topic is internal to Valor.
