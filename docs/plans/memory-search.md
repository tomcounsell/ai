# Memory search in a turn

Tom, 2026-10-27: "memory search is critical. it should be a readily
avialable skill just like chat history search". Done when Tom asks Valor in
a real chat about something from an earlier task or chat that is not in the
current context, and Valor finds it with the memory search and answers,
recorded with the date. Covers "memory search in a turn" of
tomcounsell/ai#3640.

## What is built

1. **One search, recall's.** `memory/recall.py` splits `render` into
   `candidates` (unchanged rules) and a ranking step both callers use:
   `backend.keyword_search(Record spec, "words", tokenize(query), allowed=
   candidates, stats="corpus")`. `render` keeps its ten records within 4000
   tokens. A new `search(dsn, *, ledger, project, client, query, limit,
   offset)` asks for `offset + limit` and returns that page in rank order,
   each as `{record: <ledger>/<key>, origin, project, at (ISO), label,
   text}`, `label` being recall's `label()`. No second search is written.
2. **Scope exactly as recall, with no start cutoff.** Candidates: the
   asking task's own project's records on its own ledger, plus every record
   marked a standard of working from any client and ledger, less
   corrections, and a confined record only for its own client.
   `candidates` gains `cutoff=None` meaning "everything ingested so far":
   recall's "before the task began" would hide everything a long-lived chat
   session has learned since it opened, which is what search is for. The
   asker's own earlier turns are included (what compaction dropped).
3. **Delivered as `valor-record memory`.** A fourth verb in
   `core/record.py`'s `VERBS`, so it rides the existing gateway route
   `GET /t/{token}/record/{verb}`, the turn's own token, the kernel's
   choice of project (the task's spec: `repo`, `client`), the same CLI and
   `$VALOR_RECORD_URL` every harness already sets. One mechanism: no new
   route, env var, binary or harness change. `core/memory.py` gains
   `search(conn, spec, query, limit, offset)`: memory off, or a store that
   cannot be reached (`unreachable`), answers `NotFound` with the reason;
   popoto runs in `asyncio.to_thread` as recall does. It reads only:
   popoto `query.filter`/`keyword_search`, no `save`, no ledger append, and
   no ingest (the turn sees what the last ingest took).
4. **CLI.** `valor-record memory "<words>" [--limit N] [--offset N]
   [--json]`: default 10, at most 50. Prints each hit as recall's entry
   (`- <label>:` then the text quoted `> ` line by line, escaped), so a
   result reads as data exactly as the Remembered section does; "nothing
   found" otherwise. Standard library, Python 3.9, as now.
5. **Turns are told.** `skills/sdlc/converse.md`: before saying you don't
   know or don't remember something from before, search memory, as with
   chat history. `skills/sdlc/channel.md` (rendered into every task stage)
   gains the line beside chat history; `plan.md` and `build.md` one line
   each (search memory for earlier decisions on the same code before
   choosing). `docs/memory.md` (a Search paragraph after Recall),
   `docs/harnesses.md` (Reading the project's record), `tools/README.md`,
   the `valor_record.py` and `core/record.py` docstrings. A comment on
   #3640 for the part this covers, in the docs stage.

## Out of scope

Living per-person and per-project records, better recall ranking, writing
or marking memory from a turn, an ingest on demand, cross-project search.
No change to the sandbox deny, the route's GET-only answer, or recall's
Brief section.

## Governance

None added. The scope rules are recall's, reused; read-only is the absence
of a write path, not a new check. No grant needed.

## Tests (tests/test_memory_search.py, real Postgres as test_record.py)

- A turn of project A finds A's record by a word in it, and a standard of
  working from project B of another client; it does not find B's unmarked
  record, though the words match.
- A record marked a standard and then confined by a correction is found
  from its own client's project and not from another client's.
- Corrections are never results; a record ingested after the asking task
  started is found (the chat-session case).
- Paging: `--limit 1` then `--offset 1` return the first two hits in rank
  order, disjoint; limit is clamped to 50.
- Nothing writes: counts of `events`, `documents` and every memory table
  are equal before and after a search, through the live gateway.
- The CLI through a live gateway prints quoted entries, `--json` the
  fields; memory off and an unreachable store print the reason, exit 1.
- `test_memory_import`'s recall test still passes (render unchanged).
