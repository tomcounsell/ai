# Example narrated video from the PDF-default deck

Tom (valor team chat, 2026-10-17 11:07 UTC): "Go ahead and render a video so we
can see a good example". A deliverable, not a code change: nothing here merges
into Valor beyond this plan, unless the `present` skill proves broken, in which
case the fix is a PR on `yudame/skills`.

## What gets built

The six-slide deck from task 5785115c59f1
(`/Users/valorengels/valor-tasks/5785115c59f1/deliverables/deck.html`) turned
into a narrated MP4 with the `present` skill exactly as written (`VIDEO.md`,
`tts.py`, the `video/` Remotion template), one scene per slide.

1. **Work dir** `/Users/valorengels/valor-tasks/bde12e594236/video`, outside any
   git repo (node_modules is ~1 GB): `cp -R <present>/video` there.
2. **Script** `scenes.json`, six scenes, ids `default, problem, how, phone,
   other, decision`. Each `title` is the slide's headline (a full-sentence
   conclusion under 15 words); `points` are at most three short phrases carrying
   the key number or noun (13pt, 14pt, "Headless Chrome"), never the narration
   word for word; `text` is the slide's content rewritten for the ear: short
   sentences, no symbols or abbreviations Kokoro misreads ("13pt" spoken as
   "about thirteen point"). Target 9 to 15 s per scene, 70 to 100 s total.
   Scene 5 says this video is itself the example of the video option.
3. **De-slop** the script in a fresh subagent given only the draft, the medium
   (narrated explainer video, phone) and the audience (Tom); act on every flag.
4. **Voice** `uv run <present>/tts.py --scenes scenes.json --out-dir public/vo`,
   default voice am_michael (Kokoro models are in `~/.cache/kokoro-onnx`).
   Confirm the manifest says engine kokoro, not `say`. If a scene falls outside
   5 to 15 s, rewrite its narration and re-run; no hand timing.
5. **Look** match the deck Tom liked: change `Explainer.tsx`'s colour constants to
   the deck's cream `#f6f1e7` and red `#c0281c`, and the scene number label to the
   deck's mono labels ("THE PROBLEM", ...) via an optional `label` field. That is
   the "replace the constants when the project has its own style" path VIDEO.md
   names, done in the work dir, not in the skill.
6. **Render** `npm install`, `npx remotion render Explainer out/explainer.mp4`
   (H.264/AAC, 1920x1080, 30 fps). Then
   `ffmpeg -i out/explainer.mp4 -c copy -movflags +faststart` into
   `/Users/valorengels/valor-tasks/bde12e594236/deliverables/valor-pdf-default-video.mp4`
   so Telegram plays it inline before it fully downloads.

## Out of scope

Changes to the `present` skill (unless broken), diagram scenes, a portrait or
square cut, music, subtitles burned in, sending to Tom (the chat session sends
the file from the path in the delivery).

## Stakes

Low: a single throwaway file Tom watches once, re-renderable in minutes, no
code or data touched; so no critique round and no review round.

## Verification (VIDEO.md's Done, plus phone checks)

- `ffprobe` duration equals the sum of `sceneFrames` / 30 and lies in 60 to 120 s.
- `volumedetect` mean_volume well above -60 dB; manifest engine is kokoro.
- One frame from the middle of each of the six scenes, looked at: headline fits,
  points don't overlap, Lora/Inter loaded (not fallback serif).
- Phone legibility: each frame also viewed scaled to 390 px wide (phone in
  portrait); the headline must still read. Points at 48 px become ~10 pt there,
  so if they fail, raise point size in the work dir.
- `ffprobe` shows h264 + aac, yuv420p, and `moov` before `mdat` (faststart);
  file under 50 MB (Telegram bot upload limit).
- Listen to the whole narration once (transcribe it back with the local
  Parakeet process or read the waveform gaps) to catch mispronounced terms
  like "PDF", "HTML", "13pt".

Done when Tom has watched it in the chat.
