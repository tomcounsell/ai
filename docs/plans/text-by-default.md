# Text by default; slides on request; a needed document is an ordinary document

Tom, valor team chat, 2026-10-26 07:40 UTC: "the present skill is being
overused ... don't present with slides unless specifically asked to. And,
all text on a mobile vertical layout just comes out really unprofessional."
His example, `postgres-backend.pdf` (a mastery-be backend review): 30 pages,
306 x 544 pt, all text, made by HeadlessChrome. This replaces his
2026-10-17 "PDF for everything, mobile friendly by default" for written
work; his 2026-10-22 vertical default for decks and videos stays.

Done when Tom next asks for something of this kind and gets it as text or
an ordinary document, not slides, and finds it right.

**Cause, checked.** 306 x 544 pt is `108mm 192mm`, the page present's PDF
section sets for "Page, document, report", printed by the headless Chrome
command present gives. Present's description says "Use for ... any document
made for someone", so a written review triggers it. Valor's own text says
the same: `persona/delivery.md` "What you make for people" (rendered into
every turn by `core/persona.py`), `docs/persona.md` "What Valor makes for
people", `skills/sdlc/converse.md` "A made thing goes as a file", and
`site/chat.html` "Documents come as phone-readable PDFs." No test asserts
any of this wording.

## What is built (wording only; no check, gate or step is added)

**In this repo:**

1. `persona/delivery.md`, "What you make for people", rewritten: an answer,
   a findings report or a review is a reply in the chat, in text. When it
   is too long for a chat (past about 600 words, or it needs tables), it is
   an ordinary document: A4 or Letter portrait, normal margins and type,
   headings, sent as a PDF file, not a link. Slides (a deck, a narrated
   video) only when someone asks; then vertical, landscape when asked. The
   effort matches the ask: a question gets an answer, not a design pass,
   an outline, a style choice or a render.
2. `docs/persona.md`, "What Valor makes for people": the same, with Tom's
   2026-10-26 words quoted, saying it replaces the 2026-10-17 phone-PDF
   default for text and keeps 2026-10-22 for decks and videos.
3. `skills/sdlc/converse.md` line 115: "A made thing goes as a file"
   becomes: an answer goes in the chat as text; a document a task made
   goes as its PDF file (copy, sha256, `files`), a deck only when asked.
4. `site/chat.html`: "Documents come as phone-readable PDFs." becomes
   "Answers come as text; a document, when one is needed, as an ordinary
   PDF."
5. `skills/sdlc/build.md` "A document for someone" stays (it names the
   path; it says nothing of shape).

**In yudame/skills** (branch `valor/text-by-default` from `main`, PR from
valorengels; never copied here):

6. `present/SKILL.md`:
   - Description: present is for a deck, a one-page explainer, a spoken
     brief or a narrated video *when one is asked for*; drop "any document
     made for someone"; add "not for a written answer, report or review,
     which goes as text".
   - "Pick the form": a first line, the default is no form: a question,
     review or findings report is answered in text by the caller, and this
     skill is not used. Choosing a form unasked is only for an occasion
     that is a presentation (a room, a call, a listener away from a
     screen). Text-only material is never a deck.
   - "PDF by default" bullet becomes "PDF when delivered": a made deck or
     page goes as a PDF unless another format is asked.
   - PDF section: "Page, document, report" becomes **Page** (the one-page
     explainer, still phone-sized, since it is a visual made on request)
     and a new **Document** line: `@page { size: A4; margin: 20mm }`
     (`letter` for a US reader), body 10.5 to 11pt, headings 14 to 18pt,
     one column, normal tables; `pdfinfo` shows 595 x 842 pt. The deck
     line is unchanged (vertical, landscape on request).
7. `house-style/SKILL.md`: the phone-width sentence becomes: the look
   holds in print: one column on a phone-sized page or deck, an ordinary
   A4/Letter page for a document, the background printed.

## Out of scope

Any new check, hook or gate that blocks a deck; the plugin's version or
install path; re-sending Tom's tech-stack review (it belongs to another
project's chat).

## Tests

No code changes, so no new unit test. Evidence:

- Valor suite (`pytest`) passes; `core.persona` renders and the rendered
  text holds the new section and no "laid out to read on a phone".
- `git grep -niE "phone-readable|read on a phone|mobile"` over persona,
  docs/persona.md, skills and site returns nothing stating the old default.
- yudame/skills: `python3 tests/check_skills.py` passes; the same grep over
  present and house-style leaves "phone" only on decks, pages and videos.
- The Document recipe, run for real: the text of Tom's example
  (`pdftotext`) laid out by the new Document line and printed with
  present's Chrome command. `pdfinfo` shows 595 x 842 pt and far fewer than
  30 pages; I look at pages 1 and 2 as images. A deck rendered from a short
  outline still comes out 306 x 544 (vertical unchanged).

## Rounds

Stakes: wording that decides the shape of everything Valor sends Tom,
reversible by a revert in two repos. One critique round, no review round.
