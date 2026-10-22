# A new narrated tour of valorengels.com, landscape, desktop site

Tom, valor team chat, 2026-10-22 15:36 UTC: the site's second pass leads with
the intention (alignment, autonomy, safety), then what Valor actually does,
then technical details and comparisons. Record a new presentation from
scratch, landscape, on the desktop site so the diagrams show.
Done when Tom has watched it.

## Premise checked

- Live matches source: `curl` of `/`, alignment, engineering, trust,
  comparisons and philosophy hashes identical to `site/*.html` at 8e9b2df22
  (2026-10-22). Rechecked right before shooting; if they differ, the build
  stops and the delivery says so instead of recording a stale site.
- The diagrams are inline SVGs in a `.split` right column (25rem): home (a
  message to a delivery, over the ledger), trust (stop and correction),
  alignment (stop; correction), engineering (the stage pipeline, plus the
  merged-PRs-by-day chart), rules (adding a limit), durability (ledger;
  effect intent/outcome), chat (the chat session). Comparisons has a table, no SVG.
- Pipeline from task 2d8268519259 (`tour/`): Playwright `shoot.py` stills,
  Remotion `Tour.tsx` panning a page image per scene, present `tts.py`
  (Kokoro, `VALOR_TTS_VOICE=am_michael`, lexicon with Engels `ˈɛŋɡəlz`,
  Yudame `jˈuːdɑmeɪ`, Tom-approved), `cap upload` with the Desktop login.

## What gets built (all under the task workspace, outside the repo)

1. **Copy** `tour/video` into `../tour-landscape/`; composition 1920×1080.
2. **Shoot** the live site with Playwright at a 1440×810 viewport,
   device scale 2 (2880-wide stills, so zooms stay sharp): full-page images
   plus the top of each section and the box of each diagram.
3. **Script, from scratch for the ear**, ~2 minutes, one idea per scene,
   every claim taken from the live text. Order:
   - Intention: who Valor is and the job (home hero: business and engineering
     operations at Yudame, aligned and corrigible); authority to stop, limit
     and correct sits in code outside the model (trust: stop/correction
     diagram); effect classes and the ceiling, acting only as himself; a new
     limit needs Tom's grant (rules diagram); the acceptance question
     (philosophy).
   - What he does and has done: responsibilities (home: own accounts, chats,
     tasks to delivery, home diagram); merged pull requests by day (engineering
     chart); changing his own code under the same process (alignment).
   - Technical: the task stages (engineering pipeline diagram), the ledger and
     crash recovery (durability diagrams), the tech stack.
   - Comparisons: ideas taken from other projects; where each agent runs and
     whose name it uses (table).
   - Close on the URL.
   De-slop pass on the script before TTS.
4. **Frame each scene** on its heading and diagram together; where a diagram's
   smallest label renders under ~18 px tall in the 1080p frame, the scene
   zooms to the `.split` box so it is readable. Scene length from the measured
   narration (manifest seconds), as `Tour.tsx` does.
5. **Render** with present's `tts.py` then Remotion; **upload** with
   `cap upload`; copy the MP4 to `../deliverables/valorengels-tour-landscape.mp4`.

## Tests (how it is shown to work)

- `ffprobe`: 1920×1080, H.264 + AAC, duration ~2 min, equal to the sum of
  the manifest's scene lengths.
- Frame grab per scene: the diagram named in its narration is on screen and
  its labels legible (I read each grab).
- Parakeet transcript of the narration: matches the script; "Engels" and
  "Yudame" come back as in the last approved tour.
- Each sentence of the script traced to a line in `site/*.html` at 8e9b2df22.
- The Cap link opens and plays (fetch the share page, check the video URL
  serves).

## Out of scope

Site edits; the present skill (fix by PR in yudame/skills only if it is found
broken); changing the vertical default, which stays for everything else.
