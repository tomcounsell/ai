# Narrated video: how memory works now and its first score

Tom (valor team chat, 2026-11-09 05:10 UTC): "how does the memory system work
now and are all 2029 memories valueable? will they get condensed,
deduplicated, or pruned?"; 05:19 UTC: "present it to me with video". A
deliverable, not a code change: this plan is the only commit; no PR, no
merge of anything else, no change to the `present` skill.

## What gets built

A 2 to 3 minute landscape MP4 made with the yudame `present` skill's narrated
video path as written (`VIDEO.md`, `tts.py`, the `video/` Remotion template),
voice `$VALOR_TTS_VOICE` (am_michael, Kokoro). The content is the text answer
already given (sources: `docs/memory.md`, task 8805aff7fd1b's delivery), no new
claims.

1. **Work dir** `/Users/valorengels/valor-tasks/3cf7a8584cc9/video` (outside
   the repo; node_modules is ~1 GB): `cp -R <present>/video` there.
2. **Script** `scenes.json`, one idea per scene, answer first:
   1. *answer*: the short answer to his three questions.
   2. *flow* (diagram): turn → raw text kept (instructions, answers, feedback,
      replies; no model summaries, raw beat summaries in popoto's tests) →
      memory → recall.
   3. *recall* (diagram): a task starts with its own project's 10 best keyword
      matches plus marked ways of working, within ~4,000 tokens; any turn can
      search; a wall between clients.
   4. *funnel* (diagram): ~11,500 old records (May–Oct) → ~6,750 document
      excerpts out (Tom's 21 Oct rules), superseded and run logs out → a model
      drops what isn't worth recalling and merges duplicates → 2,029. Only
      numbers the delivery states appear; no invented intermediate counts.
   5. *dates* (stacked bar): 1,486 exact, 268 upper bound, 275 undated; per
      project as counts only (Valor 662, a client 1,076, a client 282, small
      projects 9); not checked one by one.
   6. *score* (bar chart): resurfacing 96%, calendar 91% (11 questions), what
      changed 73%, around a dated event 66%; client leaks 0 of 40 as a tile.
   7. *search*: keyword+meaning scored the same, ~20× slower: stay on keywords.
   8. *growth*: nothing condenses, dedups or prunes; memory only grows; top 10
      keeps that cheap; weak spot: an old decision outranking its replacement.
   9. *next*: no pruning yet; the monthly score (from 1 Dec) watches growth;
      first change to try: prefer the newer of two memories on one subject,
      judged by the score.
   Titles are conclusions under 15 words; narration written for the ear
   (numbers spoken as words Kokoro reads right). Target 12–20 s a scene.
3. **Diagram scenes**: new components in the work dir's `Explainer.tsx`
   (flow, recall, funnel, stacked bar, bar chart), chosen by an optional
   `kind` field, animated in step with `sceneFrames`, house-style colours.
   Chart colours and marks follow the dataviz skill. That is the "write new
   scene components" path VIDEO.md names, in the work dir only.
4. **De-slop** the script in a fresh subagent given only the draft, medium and
   audience; act on every flag.
5. **Voice, render, faststart** as VIDEO.md; output
   `/Users/valorengels/valor-tasks/3cf7a8584cc9/deliverables/valor-memory-video.mp4`
   (1920×1080 H.264/AAC, `-movflags +faststart`, under 50 MB for Telegram).

## Out of scope

Code or doc changes, a PR, a portrait cut, music, burned subtitles, sending it
(the chat session sends the file). No client names anywhere on screen or in the
narration: the two client projects are "a client".

## Stakes

Low: one re-renderable file Tom watches, nothing stored or merged; the one
real risk, a client name on screen, is checked frame by frame below.

## Verification

- `ffprobe` duration equals the sum of `sceneFrames`/30 and lies in 120–180 s;
  manifest engine is kokoro with voice am_michael; `volumedetect` mean well
  above -60 dB.
- A frame from the middle and the end of each scene, looked at: text fits,
  nothing overlaps, fonts loaded, each chart's numbers match the list above.
- No client name: grep `scenes.json` and the components for the client
  project names from the private specs; none appear.
- Listen back to the numbers scenes (4–6) for misreads.
- Delivery gives the MP4 path, duration and `shasum -a 256`.
