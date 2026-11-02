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
   - takes the production env from the Render API's `cuttlefish` env group, looked up
     by name, into the subprocess env only (never written to disk);
   - uses the managed checkout (`find_cuttlefish`), so `CUTTLEFISH_DIR` is no longer
     needed;
   - runs Cuttlefish's verbs in order: `create --defer-setup` (title from the
     question, description from the report's Summary), `attach report.md --require`,
     `manifest --set` with `web_scope: "none"` so the attached report is the
     research, then `setup`;
   - runs `claude -p "/research-workflow <slug> --auto-advance"` in the checkout,
     then the checkout's `notebooklm-audio` skill the same way;
   - polls `episode show <slug> --json` until `published_at` is set, or the workflow
     fails or pauses (its `blocked_on` goes into `error`).
   - `url` is the episode's public page.

   Reuse: every step is Cuttlefish's own command or skill. The only new code is the
   sequencing and env loading.
2. **Wording.**
   - `SKILL.md` "A podcast episode" and `agents/researcher.md`: the line that says a
     person publishes goes. The new text says an episode is made from a finished
     report on Tom's word, to the podcast he named, and that the research run never
     waits for that decision.
   - `skills/sdlc/converse.md`, one paragraph on research for Tom:
     - start the research task at once, whatever is or isn't said about a podcast;
     - send the PDF when it's back;
     - if Tom named publishing and a destination with the topic, start a publish task
       on the run directory right away;
     - if he didn't, ask with the PDF: publish or not, Research or Staff, with a
       recommendation (Staff when the topic names a client, a person, or internal
       business; Research otherwise);
     - start the publish task only on his answer, since silence is not a yes;
     - the publish task delivers the episode link.
   - No new check or gate. Nothing refuses a call; the decision lives in the wording,
     and the capability does what it's asked.
3. **Tests** (`tests/test_research.py`, all stand-ins, no network):
   - *report not delayed:* `start()` returns at once and the research worker never
     calls `episode`. The run reaches `done` with no podcast argument anywhere.
   - *episode verb:* runs create→attach→manifest(web_scope none)→setup→research
     workflow→audio in order, with the given slug on every Cuttlefish call, then
     records `published` and `url`. A failed or paused step records `failed` with
     its reason and stops. The env comes from the stand-in Render response and is
     not written into the run dir.
   - *routing and consent wording:* `converse.md` and `SKILL.md` say:
     - research starts without waiting on the publish decision;
     - publishing happens only on Tom's say-so, with the topic or in his answer;
     - private topics go to `yudame-staff`, otherwise to `yudame-research`;
     - the ask carries a recommendation.
   - Replace `test_episode_drafts_and_never_publishes`. Update the
     `test_orchestration` researcher assertions if the agent text moves.
4. **Live run, on the Cowboy, pre-merge.** One small real topic on how Valor works
   internally, as a private test. Research → PDF → `run.py episode --podcast
   yudame-staff`. Before running it, check whether Cuttlefish's publish emails the
   Staff feed's subscribers. If it would mail anyone but Tom, stop at audio done and
   say so. The PDF and the episode link go in the delivery. If NotebookLM auth or
   the research workflow fails, the delivery says at which step, with evidence.

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
