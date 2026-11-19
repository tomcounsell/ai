# Find a past call by its words (task f7bc1d7a)

Tom, 2026-11-19 15:33 UTC, #valor-dev: no separate place to ask; "as long as
you can look them up, I will just ask you."

**Checked first (observed, Cowboy, 2026-11-19).** The valor project's call of
2026-11-16 14:39 UTC (`valor-record call pxz-exmv-icy`) holds its heard and
spoken lines. A phrase said only in it finds nothing with
`valor-record messages --search` (it reads `message.received` only), and
`valor-record memory` finds it only where a later task happened to quote it:
memory ingests no `call.heard` rows (`core/memory.py` TYPES), and a second
phrase from Valor's own spoken line finds nothing at all. The call's title and
Gemini summary are not in the ledger; they live only in the Mac's Meetings
folder, which mixes every project's calls. So the read is missing, not only
guidance.

**Build (one mechanism: the existing `call` verb).**
1. `core/record.py` `call()`: with `q` (and no `code`), search every Meet
   session in the project's reach (`reach()`, the same scoping `call <code>`
   uses) for rows whose text holds every word of `q`, case-insensitive: heard
   and typed lines (`call.heard`), Valor's spoken and posted lines
   (`effect.outcome` with `spoken_text` / `posted`), and the call's summary
   (below). Newest first, `limit` as now. Each hit: the record, its time, who,
   how, the text, and its call's code, start date and title.
2. `core/call.py`: the summary step's result also carries the call's `title`
   and its `notes` (Gemini's summary paragraph and decisions, as saved), and
   `end()` writes both into the `call.ended` payload. From then on a call's
   title and summary are in the ledger, under its project; older calls show no
   title and are found by their lines. `docs/data.md` names the two fields.
3. `tools/valor_record.py`: `call [CODE] [--search WORDS]`; a search prints
   each hit as `<date> <title> (meet <code>) #<record> [HH:MM UTC] <who>
   (<how>): <text>`, so the session can then read the whole call by its code.
   Neither given: an error naming both. Docstring usage line updated.
4. Guidance, one place: `skills/sdlc/converse.md`, "What Valor learned
   before": asked what was said or agreed in a past call, search the calls
   (`valor-record call --search "<words>"`) and answer with the call's date and
   the line it comes from; `valor-record call <code>` for the whole call.

**Out of scope.** No index, no new table, no memory ingest of call rows, no
reading of the Meetings folder, no backfill of titles for past calls, no
change to what a call itself can look up.

**Governance.** Adds no check, gate or limit: the project scoping is the
existing `reach()`, unchanged.

**Tests** (`tests/test_meet.py`, beside the transcript test):
- a word said in one call (heard, spoken, typed) finds that line with its
  call's code and date, and not a second call of the project without it;
- several words match a line holding all of them in any order and case; a
  line with only one does not match;
- a word in a call's stored summary finds it, with its title;
- another project's session searching the same word finds nothing;
- `end()` with a summary writes `title` and `notes` into `call.ended`
  (`tests/test_call.py`, where the end report is tested);
- CLI: `--search` prints the hit line format; no code and no search errors.
Full suite under 5 minutes, then merge. Tom rolls out.

Stakes: a read-only search over the project's own record plus two fields on
an existing row; reversible. Critique rounds 0, review rounds 0.
