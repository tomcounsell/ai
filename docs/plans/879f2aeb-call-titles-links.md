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
   invite's title and the asking chat's topic) in the prompt before the transcript. `_title()`
   cleans it: newlines to spaces, `/`, `:` and `\` dropped, whitespace collapsed, at most 60
   characters; empty or missing gives "Valor call".
2. **The names.** `watch()` (keyed by Meet's participant id, the one keyspace that marks
   Valor's other device) records each new participant's first name (first word of the display
   name) in order of first appearance, skipping a blank name, a name equal to Valor's own
   (another device of his account) and a first name already listed; Valor's first name
   (`persona.identity`) ends the list. `people()`'s device-keyed names are not used.
3. **The title.** `_title()` is `"<topic> - <names>"`: meta.json's `title`, the heading of
   transcript.md and notes.md, and the folder (`folder_name`, unchanged: "YYYY-MM-DD HHMM
   <title>" in the Mac's zone, so date sorting holds). The summary document is renamed from
   `call-<code>.md` to `"<YYYY-MM-DD> <title>.md"`, the date in the Mac's zone as the folder's
   (`started_at.astimezone()`), so the two agree near midnight UTC. Its heading is
   `# <YYYY-MM-DD> <title>` (replacing the model's first H1, if any), then a line
   `Google Meet <code>`, so the code stays findable; the ledger still keys the call by code
   (`valor-record call <code>`). `end()`, `skills/sdlc/converse.md` (Calls) and
   `docs/bridges/meet.md` tell the chat session to send the file under its own name.
4. **The links, one deterministic linker** `linked(text, repo, links)`, applied to the summary
   document and to the `summary` paragraph before it is written to summary.json (notes.md is
   built from that field, raw, as Yudame Tools' `NoteFormat.markdown` writes it; the app
   rewrites notes.md from summary.json on any edit, so links live in summary.json). Decisions
   and actions are left unlinked: the app escapes them, so a link there would show as literal
   brackets. Not in the transcript. Chosen over telling the model: testable, never invents a
   URL, catches what the model writes either way.
   - The call's project spec, read live at the save (`Spec.load(self.project)`); no project, or
     a spec that does not load, links nothing. `repo`, when on github.com (`.git` dropped).
   - Only prefixed references: `PR 12`, `PR #12`, `pull request 12` → `<repo>/pull/12`;
     `issue 12`, `issue #12` → `<repo>/issues/12`. A bare `#12` is left alone ("#1 priority").
     The matched words are the link's text.
   - A new spec field `links = {"ENG-" = "https://linear.app/<workspace>/issue/ENG-"}`: each
     prefix followed by digits links to the URL plus those digits (Linear ids and any other
     numbered reference whose URL form is known). A table of strings, documented in
     `projects/README.md` and `projects/example.toml`.
   - Text already inside a Markdown link, an autolink, a bare URL or a code span is untouched.

## Tests (`tests/test_call.py`, made-up names and repos)

- The end-to-end save: participants "Logan Reyes" and "Mara Ode", the fake model's topic
  "Launch plan" and a summary mentioning `PR 12`: the folder is `<date> <time> Launch plan -
  Logan, Mara, Valor`; meta/notes/transcript carry that title; the summary file is `<date>
  Launch plan - Logan, Mara, Valor.md` with that heading and `Google Meet <code>`; summary.json's
  `summary` and notes.md carry the PR link; the ended report names the file; the model's prompt
  carries the prep.
- A topic with `/`, `:` and a newline saves under the cleaned title; no topic gives "Valor call
  - …"; a blank name, Valor's other device and a repeated first name are left out.
- The linker: `PR 12`, `PR #12`, `issue 7`, `ENG-633` under a `links` prefix link right; a bare
  `#1 priority`, an existing link, a URL containing `#12` and a code span are untouched; a
  non-GitHub repo, no project and an unloadable spec link nothing.
- The spec: `links` loads; a non-string value is refused like any malformed field.
- Moved to the new names: `tests/test_live_meet.py` (folder and file), the format test
  (`test_call.py:922`) and `tests/fixtures/meetings/{notes,transcript}.md` with a new-style title.

## Out of scope

Yudame Tools' code; renaming calls already saved; linking the transcript, decisions or actions;
any new check.

## Then

Full suite, merge, `core update --wait` on the four Macs (each kernel job's launchctl
environment; the Cowboy via `ssh localhost`, `PATH=/opt/homebrew/bin`, detached), each kernel
confirmed on the merged head. Only then add `links` for the client project with Linear ids to
its private spec (an unknown key would drop the spec on a Mac still on old code), and check it
loads from a turn on its Mac. Done when Tom sees a real call's notes saved under its topic and
names, with its references as links.
