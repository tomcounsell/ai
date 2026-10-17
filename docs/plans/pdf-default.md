# PDF by default, laid out for a phone

**Ask.** Tom, valor team chat, 2026-10-17: "Let's make PDF the default for
everything you create, unless something else is requested and we want it
to be mobile friendly also by default." Earlier the same day: atop
artifacts, the add-ons are "default style and export (pdf)", then optional
voice and video.

**Premise, checked.** `present` (yudame/skills,
`plugins/yudame/skills/present/SKILL.md`) builds decks and pages as
artifacts, voices with `tts.py`, renders video with Remotion; it never
mentions PDF. `house-style` is one paragraph on look and says nothing of
pages or phones. In this repo nothing says what format a made thing takes:
`persona/delivery.md` covers SDLC deliveries and outbound wording,
`skills/sdlc/converse.md` says a document is a task. Google Chrome is
installed on this Mac and its headless `--print-to-pdf` is the renderer
(rule 10: no renderer written, no new dependency).

## What is built

**In yudame/skills** (branch from `main`, PR from valorengels; never copied here):

1. `present/SKILL.md`, a new **PDF** section under "What every form
   shares": a deck, page or any document made for someone is delivered as
   a PDF unless another format is asked for. Whether a Slides artifact is
   a local HTML file is not shown anywhere, so the skill says it: the deck
   or page is built as one local self-contained HTML file, that file is
   published as the artifact when the artifact tooling is there, and the
   same file is rendered to PDF with headless Chrome:
   `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --no-pdf-header-footer --print-to-pdf=<out>.pdf file://<abs html>`
   (`google-chrome`/`chromium` on Linux). Page size and breaks come from
   the HTML's print CSS, so the command never changes:
   - **Page / document / report:** portrait, one column,
     `@page { size: 108mm 192mm; margin: 8mm }`, body 11pt (about 15pt on
     a phone, which fits page width), headings 16 to 20pt, tables at most
     3 columns or stacked, images `max-width: 100%`, no side-by-side columns
     in print (`@media print` collapses them).
   - **Deck:** slides stay landscape slides, one per page,
     `@page { size: 192mm 108mm; margin: 0 }`, each slide
     `break-after: page`; minimum body 18pt and headline 28pt, so a slide at
     phone width (about 0.7 scale) reads at 13pt or more. This tightens the
     existing budgets (a slide over budget splits).
   - Check before sending: `pdfinfo` (or `mdls`) for page count and size,
     and open page 1 as an image to see it.
   - Done-is-checkable line gains the PDF path. Description line mentions PDF.
   - **Other formats, on request only** (Tom, 10:33 UTC): *Google Slides
     for editing*: build the .pptx with the officecli skill, upload it with
     `gws drive files create` converting to
     `application/vnd.google-apps.presentation`, share it with whoever
     asked (`gws drive permissions create`), report the link; no converter
     written. *Video with voiceover*: the existing Video form (VIDEO.md,
     `tts.py`, Remotion), listed here, not rebuilt.
2. `house-style/SKILL.md`: one sentence that the look holds in print and on
   a phone-width PDF: single column, the type sizes above, cream page
   background printed (`print-color-adjust: exact`).

**In this repo:**

3. `persona/delivery.md`, a short section "What you make for people": a
   deck, one-page explainer, document, report or brief on paper is a PDF by
   default, laid out to read on a phone without zooming (the present skill
   says how), and sent as a file on the chat's send; another format only
   when asked. `docs/persona.md` gains the matching line.
4. **The handoff.** A send's `files` must be inside the sending
   session's own workspace (`core/bridge.py` `_file_size`,
   `_size_refusal`), and documents are made by tasks. Wording only:
   `skills/sdlc/build.md` says a delivery that made a document names the
   PDF's absolute path in `done.md`; `skills/sdlc/converse.md` says the
   chat session copies that file into its own workspace, takes its
   sha256, and sends it in the reply's `files`, not as a link. Letting a
   send name another workspace's file would change a refusal rule; not
   done.

**Out of scope.** A PDF script or wrapper in either repo; Office formats
(officecli stays for when one is asked); voice and video; a portrait
one-slide-per-page deck mode (named in the delivery as the alternative:
each slide re-flowed onto a 108x192mm page, larger type, but no longer a
slide you can project).

## Tests

- yudame/skills: `tests/check_skills.py` and `tests/check_install.sh` pass.
- This repo: the full suite passes; the change is prose, no new test.
- By hand, the real thing: build one page and one deck through `present`
  on a real topic (this change), render both, `pdfinfo` shows 108x192mm
  portrait and 192x108mm landscape pages, open each page image and check
  type reads at phone width, plus a page with a wide table and a two-column
  section to see them collapse. The deck PDF is copied into this
  workspace, and `done.md` names its absolute path, so the valor team chat
  session exercises the handoff end to end: it copies the file, sends it
  to Tom, and asks him to open it on his phone. The delivery gives the
  figures: on a 390pt-wide phone a 192mm slide shows at about 0.72, so
  18pt body reads at about 13pt (letterboxed, scaled to width); a 108mm
  page shows at about 1.27, so 11pt reads at about 14pt. It names the
  portrait one-slide-per-page alternative.

**Done when** Tom reads that PDF on his phone (rule 5).
