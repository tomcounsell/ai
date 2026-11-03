# Publish the custom-LLM report as a Yudame Research episode

Tom, 2026-11-03 06:39 UTC: "have cuttlefish publish this to the yudame research podcast",
about the report "Building custom LLMs in late 2026"
(`~/Desktop/Valor/research/2026-11-02-custom-llm-post-training/report.md`).

## What blocks it today

`run.py episode` waits on the run's `status.json` and refuses anything not `done`.
This run's `status.json` says `failed`: the first run on the whole question hit the
write-up's output limit, and the researcher then wrote the combined `report.md`
into the same directory from three narrower runs. So the episode path would stop at
once with "the research failed" even though the report is there.

## What will be built

1. **Fix (one commit with its test).** `publish()` waits while the research is
   `running`, then goes on when `report.md` exists, whatever the state says; it
   stops with the research's error only when there is no report. The episode only
   ever reads `report.md`, so that file is the right signal.
2. **The live run.** Refresh NotebookLM's login from Chrome (both cookie domains, as
   the skill says), check `yudame-research` has no episode in progress (Cuttlefish
   allows one per podcast), then
   `run.py episode <dir> --podcast yudame-research --title "Building custom LLMs in late 2026"`
   (the report's own title; `title.txt` holds the long one). Watch `episode.json` and
   `audio.log` for up to 4 hours, refreshing the login on `CSRF token not found`.
   If the audio is made but the run fails after it, finish with Cuttlefish's own
   publish-from-audio command, as task 4341141f63ef did, rather than regenerating.
3. **Delivery.** The published page URL from `episode.json`, checked to load, in
   `.valor/done.md`; or what failed and what it needs.

Publishing on the public show sends Cuttlefish's normal new-episode notice to that
show's subscribers. That is what publishing there means, and Tom asked for it.

## Out of scope

Redoing the research; changes to Cuttlefish (its own research pass over the report,
about 1½ hours, stays as built); Gemini's payment refusal; the write-up output limit;
the old draft on the Staff podcast.

## Tests

In `tests/test_research.py`, the wait test grows to cover:
- `running` then `done`: waits, nothing reaches Cuttlefish first, publishes (as now);
- `failed` with a `report.md` (a combined report): goes on and publishes;
- `failed` with no `report.md`: stops with the research's error, no Cuttlefish call.

Then the research tests and the full suite (under 5 minutes). The live run is the
real test: done means the episode is published on Yudame Research and its page loads.

## Governance

Adds none: the fix removes a refusal that was wrong, and adds no check.
