# What a call settled lives with the project's record (task f7bc1d7a)

Tom, 2026-11-19, #valor-dev: 15:33 "as long as you can look them up, I will just
ask you"; 15:36 "more important is actually having the answer and it living
with all other PM data"; the source line is nice to have.

**Today (observed in code and on the Cowboy, 2026-11-19).** Gemini's summary
returns `decisions` and `actions` (`core/call.py` SUMMARY). Decisions go only
into notes.md in the Mac's Meetings folder (mixed across clients) and the
summary file posted in the thread; the call's report to the chat session lists
the actions, not the decisions. Open questions Tom settles by voice (`settle`)
go to his DM for a 👍 and are answered there; nothing else becomes a fact.
Memory ingests no `call.heard` rows, and `messages --search` reads chat
messages only: a phrase said only in the 2026-11-16 14:39 UTC call is not found.

**Build, one mechanism each, size small (about 100 production lines).**
1. **Decisions become facts.** SUMMARY_SCHEMA's decisions become
   `{decision, by}` (`by`: the deciders' names as the transcript shows them);
   saved notes keep the text only. `end()`, given a summary with decisions,
   writes each through `memory.fact_write` (existing; keyed, so a repeated end
   adds nothing): kind `decision`, the claim (with the deciders' names in it),
   `since` the call's start, `source` "Meet call <code> <date>, <title>",
   `about` the deciders that resolve to a person entity of the project (a new
   read-only `memory.known(spec, names)` over `facts.entity`; unknown names
   stay in the claim only). `facts`, `person` and `memory` then answer from
   them. Memory off or unreachable: nothing written, the report says so.
2. **The report carries them.** `end()`'s report lists each decision with its
   fact name, and one line: carry each that changes a backlog item or open
   question to where it lives (its Sheet row, the task waiting on it); an open
   question is answered only by Tom's 👍 as now. No action becomes a task
   without Tom's say-so (existing rule, unchanged).
3. **Words, the lesser part.** `call.ended` also gets `title` and `notes`
   (summary paragraph and decision texts). `core/record.py` `call()` with `q`:
   every call session in `reach()`, or only that code's when `code` is also
   given; one escaped `ILIKE` per word (as `messages()` does) over heard and
   typed text, spoken/posted results and `call.ended` notes; newest first,
   `limit` as now. Each hit: record, time, who, how, text, the call's code,
   start date and title (omitted when none). With `before` and `code`,
   `call()` reads the session of that code holding records below it (codes
   recur); a hit prints `valor-record call <code> --before <record+1>` as how
   to read around it. CLI: `code` optional, `--search`; neither is an error.
4. **Guidance, one place each** (`skills/sdlc/converse.md`): "What Valor
   learned before": asked what was said or agreed in a past call, `facts
   --kind decision` and memory first, then `call --search`, answering with the
   call's date. "Calls": after a call's report, carry its decisions as step 2.

**Not built.** No backfill of past calls (their lines are searchable; they
have no title, summary or decision facts), no Meetings-folder reads, no new
table, no memory ingest of call rows. Adds no check, gate or limit: scoping is
the existing `reach()` and fact project scoping.

**Tests.** test_call: end() with decisions writes kind-decision facts dated
the call, citing it, about a known person and not an unknown one; the report
lists them with the carry line; a second end adds none; memory off, the
report says not recorded; call.ended holds title and notes. test_meet: a word
said in one call finds it with code and date, not another call; all words,
any order/case; a word in notes finds the call with its title; a past call
without title prints none; two sessions of one code, `--before` reads the
older; another project's session finds nothing. CLI line format; no code and
no search errors. Full suite, merge; Tom rolls out.

Stakes: decisions written as project facts (additive, keyed) and a read-only
search; reversible. Critique rounds 0, review rounds 0.
