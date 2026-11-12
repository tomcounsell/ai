# A saved call's title and links

Tom's ask (valor team chat, 2026-11-12 03:48 UTC): a Meet call's saved recording and notes are
titled by the call's topic and the first names of the people in it, and references in the
notes (PR numbers and the like) are links.

**Job.** For Tom and the call's team: find a call in Yudame Tools' meetings and the chat by what
it was about and who was there, and open what it refers to in one click. Doing nothing: every
call is "Valor call <meet code>", and "PR 42" means looking it up.

**Where.** The save is Valor's own code, not Yudame Tools': `core/call.py` `Call.summarise`
writes the folder in the app's meeting format and moves it into the store (`keep`). Nothing
changes in yudame/yudame-tools.

## What will be built (`core/call.py`, `core/workspace.py`)

1. **The topic.** `SUMMARY_SCHEMA` gains `topic`: what the call was about, two to five words, as
   a title. `Gemini.summarise(audio, transcript, prep)` puts the call's prep (which carries an
   invite's title and the asking chat's topic) in the prompt before the transcript, so the
   model names the topic from the prep and what was said. Empty or missing: "Valor call".
2. **The names.** First names (first word of each Meet display name, the "(another device)"
   suffix and "someone" dropped, deduplicated) of everyone seen in the call, in the order they
   appeared, then Valor's (`persona.identity` name's first word).
3. **The title.** `_title()` becomes `"<topic> - <names>"`. It is meta.json's `title`, the
   heading of transcript.md and notes.md, and the folder (`folder_name`, unchanged: the
   store's "YYYY-MM-DD HHMM <title>", so date sorting holds). The summary document the chat
   posts is renamed from `call-<code>.md` to `"<YYYY-MM-DD> <title>.md"`; its heading is
   `# <YYYY-MM-DD> <title>` (replacing the model's own first H1, if any) followed by a line
   `Google Meet <code>`, so the code stays findable. `end()` tells the chat session to copy
   the file under its own name, not `call-<code>.md`. The ledger still keys the call by code
   (`valor-record call <code>`), which nothing here changes.
4. **The links, one deterministic linker** (`linked(text, repo, links, escape)`), applied when
   the files are written to the summary document and notes.md's summary, decisions and
   actions (notes.md escapes the text around each link, as today). Not in the transcript.
   Chosen over telling the model: it is testable, never invents a URL, and catches what the
   model writes either way.
   - The call's project spec, read live at the save (`Spec.load(self.project)`; a spec that
     does not load means no links): `repo`, when on github.com (`.git` dropped).
   - `PR 12`, `PR #12`, `pull request 12` → `<repo>/pull/12`; `issue 12` → `<repo>/issues/12`;
     a bare `#12` → `<repo>/issues/12` (GitHub redirects an issue URL to the PR). The matched
     words are the link's text.
   - A new spec field `links = {"ENG-" = "https://linear.app/<workspace>/issue/ENG-"}`: each
     prefix followed by digits becomes a link to the URL plus those digits (Linear ids and any
     other numbered reference whose URL form is known). A table of strings, documented in
     `projects/README.md` and `projects/example.toml`.
   - Text already inside a Markdown link, an autolink, a bare URL or a code span is untouched.

## Tests (`tests/test_call.py`, made-up names and repos)

- The end-to-end save test: with two people named "Logan Reyes" and "Mara Ode" and the fake
  model's topic "Launch plan", the folder is `<date> <time> Launch plan - Logan, Mara, Valor`,
  meta/notes/transcript headings carry that title, the summary file is
  `<date> Launch plan - Logan, Mara, Valor.md` with that heading and `Google Meet <code>`, and
  the ended report names that file. The fake model's prompt carries the prep.
- No topic from the model: "Valor call - …". A device with no name and Valor's other device
  are left out of the names; a repeated first name appears once.
- The linker: `PR 12`, `PR #12`, `issue 7`, bare `#7`, `ENG-633` under a `links` prefix become
  the right links; an existing link, a URL containing `#12`, and a code span are untouched; a
  non-GitHub repo and a missing spec link nothing; in notes.md the text around a link stays
  escaped. A PR number in the model's summary is a link in the saved file.
- The spec: `links` loads; a non-string value is refused like any malformed field.
- Existing tests naming `call-<code>.md` move to the new name.

## Out of scope

Yudame Tools' code; renaming calls already saved; linking in the transcript; any new check.

## Then

Full suite, merge, `core update --wait` on the four Macs (each kernel job's launchctl
environment; the Cowboy via `ssh localhost`, `PATH=/opt/homebrew/bin`, detached), each kernel
confirmed on the merged head. Only then add `links` for the client project with Linear ids to
its private spec (an unknown key would drop the spec on a Mac still on old code), and check it
loads from a turn on its Mac. Done when Tom sees a real call's notes saved under its topic and
names, with its references as links.
