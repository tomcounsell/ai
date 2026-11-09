# Narrated video: a year of memory for Valor as project manager

Tom (valor team chat, 2026-11-09 06:40 UTC): "present with a video, focused on
what we care about, building Valor's PM skills over the next year", after the
agent-memory research (task 1c3002a3a8f3). A deliverable, not a code change:
this plan is the only commit; no PR, no change to the `present` skill.

## What gets built

A 2 to 3 minute landscape MP4 (1920×1080 H.264/AAC, faststart, under 50 MB)
made with the yudame `present` skill's narrated video path (`VIDEO.md`,
`tts.py`, the Remotion template), voice `$VALOR_TTS_VOICE` (am_michael).
No new claims: sources are the research report
(`~/Desktop/Valor/research/2026-11-09-agent-memory-hermes-rag/report.md`),
task 8805aff7fd1b's score, `docs/memory.md`, `docs/persona.md` "Run the project".

1. **Work dir** `/Users/valorengels/valor-tasks/f2ab47a6fa17/video` (outside
   the repo): copy task 3cf7a8584cc9's rendered video project (template plus
   its diagram components; reuse before writing), replace `scenes.json`, add
   only the scene kinds below that it lacks.
2. **Scenes** (titles are conclusions under 15 words; 12–20 s each):
   1. *answer*: a year of memory is a PM question: does the right record
      reach the job? Recall stays capped at ten, so it can't flood context;
      the risk is old records crowding out the current one.
   2. *jobs map* (diagram, the centrepiece): PM job → memory type → today's
      score. The current decision and what replaced it → what changed 73%;
      what was promised when → around a dated event 66%; the weekly update and
      month-end routine → calendar cycles 91% (only 11 questions); a client
      topic back after months → resurfacing 96%; clients kept apart → 0 of 40
      leaks. Weak rows in the accent colour. Footnote: who waits on whom, and
      since when, is computed from the record, not recalled.
   3. *what works* (five tiles): raw text plus plain search (Hermes Agent
      works the same way; summaries lost to raw in popoto); capped context;
      dates and newer-beats-older for the two weak types; meaning search only
      where the question and answer use different words; large chunks:
      nobody has measured them.
   4. *Hermes*: its contributors measured no pruning, decay or consolidation;
      those are proposals. Don't copy them yet.
   5. *why the tie*: keyword+meaning tied keyword at ~20× slower because our
      questions were written with the answer in view.
   6. *timeline* (diagram, Nov 2026 → Nov 2027): now, score project facts
      (#3684) on what changed before writing any ranking change; 1 Dec, first
      monthly score; then rank newer and date-matched memories higher, judged
      on the two weak types (aim ~82% and ~75%; moves under ~6 points are
      noise); then ~40 reworded "find related ideas" questions, meaning search
      or large chunks only if they win there; monthly, the score watches
      growth; autumn 2027, a year of history makes yearly-cycle questions
      testable.
   7. *close*: recommendation in one line; each step is kept or dropped by
      the monthly score.
3. **De-slop** the script in a fresh subagent given only the draft, medium and
   audience; act on every flag. Chart marks and colours per the dataviz skill.
4. **Voice, render, faststart** as VIDEO.md; output
   `/Users/valorengels/valor-tasks/f2ab47a6fa17/deliverables/valor-memory-pm-year.mp4`.

## Out of scope

Code or doc changes, a PR, a portrait cut, music, subtitles, sending it.
No client or person names other than Tom and Valor, and no mention of Tom's
earlier company, on screen or in narration. The roadmap is a recommendation,
not dated commitments.

## Stakes

Low: one re-renderable file Tom watches, nothing stored or merged; the real
risks, a client or earlier-company name or a misstated score, are checked below.

## Verification

- `ffprobe` duration equals the sum of scene frames and lies in 120–180 s;
  manifest voice am_michael; `volumedetect` mean well above -60 dB.
- A frame from the middle and end of each scene, looked at: text fits,
  nothing overlaps, every number matches scene 2 and 6 above.
- Grep `scenes.json`, the components and a Whisper transcript of the audio for
  the client project names (from the private specs) and Tom's earlier
  company; none appear. The transcript also checks spoken numbers.
- Delivery gives the MP4 path, duration and `shasum -a 256`.
