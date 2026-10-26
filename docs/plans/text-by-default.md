# Text by default; real presentations on request, landscape; a needed document as an ordinary document

Tom, valor team chat, 2026-10-26 07:40 UTC: "the present skill is being
overused ... don't present with slides unless specifically asked to. And,
all text on a mobile vertical layout just comes out really unprofessional."
His example, `postgres-backend.pdf`: 30 pages, 306 x 544 pt, all text, made
by HeadlessChrome (only an example of the format; its content is ignored).
07:44 UTC: "when the present skill is used, it should actually [do] a real
presentation, which means appropriate balance of text, diagrams, images.
and let's move the default back to landscape with traditional power-point
style layouts. I was wrong to make the default mobile-vertical because it's
too tight a space for 1-shot or even few-shot production of presentations".

These replace his 2026-10-17 "PDF for everything, mobile friendly by
default" for written work and his 2026-10-22 vertical default for decks
and videos (task 2d8268519259).

Done when Tom next asks for something of this kind and gets it as text or
an ordinary document, not slides, and finds it right.

**Cause, checked.** 306 x 544 pt is `108mm 192mm`, the page present's PDF
section sets for "Page, document, report", printed by present's headless
Chrome command. Present's description says "Use for ... any document made
for someone", so a written review triggers it. Valor's own text says the
same: `persona/delivery.md` "What you make for people" (rendered into every
turn by `core/persona.py`), `docs/persona.md` "What Valor makes for
people", `skills/sdlc/converse.md` (line 52 lists "a document, a deck" as
chat work; line 115 "A made thing goes as a file"), and `site/chat.html`
("Documents come as phone-readable PDFs."). The vertical deck and video
came from yudame/skills PR #5: present's Deck line, VIDEO.md, and
`video/src/Root.tsx` (1080x1920) with font sizes tuned in `Explainer.tsx`.
No test asserts any of this wording.

## What is built (wording and two template values; no check, gate or step is added)

**In this repo:**

1. `persona/delivery.md`, "What you make for people", rewritten:
   - An answer, a findings report or a review is a reply in the chat, in
     text. Effort matches the ask: a question gets an answer, not an
     outline, a design pass, a style choice or a render.
   - Too long for the chat (past about 600 words, or it needs tables): an
     ordinary document, A4 or Letter portrait, normal margins and type,
     headings, sent as a PDF file, not a link; the present skill's
     Document section says how to print it.
   - A deck or narrated video only when someone asks: a real presentation
     (text balanced with diagrams and images), landscape 16:9 with
     PowerPoint-style layouts; vertical only when asked.
2. `docs/persona.md`, "What Valor makes for people": the same, quoting
   Tom's two 2026-10-26 messages and saying which earlier words they
   replace.
3. `skills/sdlc/converse.md`: line 52 unchanged (it lists reactions). The
   "start a task" bullet (line 22) and line 115 say: a question, review or
   findings report is answered in the chat, or the task is asked to return
   text; a task makes a document or deck only when one is asked for or the
   answer is too long for a chat. A made document or deck goes as its PDF
   file (copy, sha256, `files`), as now.
4. `site/chat.html` line 93: "Answers come as text; a document, when one
   is needed, as an ordinary PDF."
5. Memory and corrections: the build lists `python -m core corrections`
   and recalls memory for "PDF", "phone", "vertical". Corrections are
   Tom's; a correction stating the old default is named in the delivery
   for him to retire. Memory records are append-only history recalled as
   records, not instructions, and this task's instruction is recalled
   beside them; the delivery names what it found.

**In yudame/skills** (branch `valor/text-by-default` from `main`, PR from
valorengels; never copied here):

6. `present/SKILL.md`:
   - Description: a deck, a one-page explainer, a spoken brief or a
     narrated video *when one is asked for*; not for a written answer,
     report or review, which goes as text; it also gives the print recipe
     for a needed document (A4/Letter, no design pass).
   - "Pick the form": the default is no form. Text-only material is never
     a deck. Choosing a form unasked is only for an occasion that is a
     presentation.
   - A line under "What every form shares": effort matches the ask; a
     Document is the text laid out plainly, with no outline, no
     headline-as-conclusion rewrite and no style choice.
   - "A real presentation": every slide or scene carries a visual (a
     diagram, image, chart or table) or is a deliberate statement slide;
     a run of text-only slides means the material wanted a document.
   - Deck: landscape 16:9, `@page { size: 254mm 142.9mm }` (10 x 5.63 in,
     PowerPoint's widescreen), body at least 18pt, headlines at least 28pt,
     PowerPoint-style layouts named: title, title and content, two
     content, picture or diagram with caption, section header. Vertical
     (`108mm 192mm`) only when asked.
   - PDF section: "Page, document, report" splits into **Page** (the
     one-page explainer, portrait, readable at phone width) and
     **Document**: `@page { size: A4; margin: 20mm }` (`letter` for a US
     reader), body 10.5 to 11pt, headings 14 to 18pt, one column, ordinary
     tables; `pdfinfo` shows 595 x 842 pt. The phone-width check stays for
     Page only.
7. `present/VIDEO.md` and `video/src/Root.tsx`: landscape 1920x1080 by
   default, vertical when asked; `Explainer.tsx` font sizes back to PR
   #5's landscape values (title 92, points 48).
8. `house-style/SKILL.md`: the phone-width sentence becomes: the look
   holds in print, on a landscape slide and an ordinary A4/Letter page,
   background printed.

## Out of scope

Any new check, hook or gate that blocks a deck; plugin version or install
path; re-sending Tom's tech-stack review (another project's chat).

## Tests

No code logic changes, so no new unit test. Evidence:

- Valor suite (`pytest`) passes; the rendered persona holds the new
  section and none of "read on a phone", "phone-readable", "vertical".
- `git grep -niE "phone-readable|read on a phone|vertical"` over persona,
  docs/persona.md, skills and site states no old default.
- yudame/skills: `python3 tests/check_skills.py` and `tests/check_tts.py`
  pass; the same grep over present and house-style leaves vertical only
  as the on-request option.
- Document recipe, run: about five pages of real prose (this plan and
  docs/persona.md's section) laid out by the Document line and printed with
  present's Chrome command: `pdfinfo` 595 x 842 pt; pages looked at.
- Deck recipe, run: a five-slide deck with a diagram and a two-content
  slide prints at 720 x 405 pt; slides looked at.
- Video: `npx remotion still` of one scene at 1920x1080; frame looked at.

## Rounds

Stakes: wording that decides the shape of everything Valor sends Tom,
reversible by a revert in two repos. One critique round, no review round.
