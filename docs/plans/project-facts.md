# Truth over time: dated, superseding, linked facts per project

Issue #3684. Tom, 2026-11-09: "Keep this issue entirely focused on data and
management of truth about a project over time." "Yes, happy to use popoto."
The first real use is a client project named in the task's Brief (not here:
this repo is public), on the Mac that serves it.

## What is built

1. **Models, `memory/facts.py`** (popoto, Postgres, schema `memory`):
   - `Fact`: `key` (`<ledger id>`), `ledger`, `project`, `client`, `kind`
     (free text), `claim`, `reported`, `evidence`, `why_differs`, `source`
     (cite: `commit:<sha>`, `telegram:<chat>/<msg>`, `ledger:<id>`),
     `source_at`, `status` (`inferred`|`confirmed`), `confirmed_by` (the
     answer's ledger id), `reason` (why it superseded), `validity`
     (`ValidityField`, valid-from = when it became true).
   - `Entity` (`kind`, `name`) and `Alias` (normalized name -> entity), per
     project and client: an alias resolves to its entity.
   - `Link` (`src`, `dst`, `kind`, `source`): the typed link, plus a
     `CoOccurrenceField` `graph` on `Link` whose edges join node refs (fact
     or entity) for the bounded BFS. Spiked on popoto 1.10.1 here: text node
     refs across kinds expand on Postgres; backdated `validity`, `as_of`,
     `save_and_invalidate(closes=)`, `chain`, and a retaken supersede are
     all clean. No popoto change is needed; if the build finds one, it goes
     to popoto and this plan says so.
   - `Source` (`project`, `client`, `channel`): `last_tried`, `last_ok`,
     `upto` (how far it was read).
2. **Ledger rows, one unit each.** `fact.added`, `fact.superseded`,
   `fact.confirmed`, `entity.added`, `entity.aliased`, `link.added`,
   `source.read` on the `memory` stream, each payload carrying its project,
   client and provenance (as `memory.imported` does). `core/memory.py` adds
   them to `TYPES`; `memory/ingest.py` hands them to `memory/facts.py`; they
   make no `Record`, so recall and search are unchanged. A supersede whose
   date precedes the old fact's start is refused with popoto's reason (bad
   input, not a gate). A fact's name is `<ledger>/<row id>`; a bare row id
   means this ledger.
3. **The port, `core/memory.py`.** `fact_write(conn, project, client, verb,
   args)` appends the row, then runs `ingest`, so the writer reads its fact
   back at once and gets its name; `facts_read(...)` serves the reads. Every
   read and write filters on project and client: another project's or
   client's fact is "no such fact".
4. **Turns: `valor-record`** (`tools/valor_record.py`, `core/record.py`,
   `core/gateway.py` takes POST for the write verbs; the project is the
   turn's grant, so `facts` takes no project argument):
   `fact add CLAIM --kind K --source CITE --at DATE [--since DATE]
   [--about ENTITY]... [--reported --evidence --why-differs]`;
   `fact supersede OLD CLAIM --reason R --source CITE --at DATE [--since]`;
   `fact confirm ID --answer LEDGER_ID`; `entity add NAME --kind K
   [--alias A]...`; `entity alias NAME ALIAS`; `link A B --kind K --source`;
   `source CHANNEL --ok|--failed [--upto CURSOR]`; `facts [--as-of DATE]
   [--kind K]`, `facts --history ID`, `facts --related ID|NAME [--depth N]`,
   `sources`. Inferred facts print labelled inferred; text is quoted as data.
5. **The kernel CLI**, `python -m core facts --project NAME_OR_FILE <same
   verbs>`, calling the same port: the one way to write the Mac that serves
   a project from another Mac (ssh, where the iCloud spec folder is
   unreadable, so `--project` takes a copied spec file, `Spec.load`).
6. **Docs:** `docs/memory.md` (a Facts section), `memory/README.md`,
   `tools/README.md`, the harness text that lists `valor-record` verbs.

## Seeding the first project (after the merge)

The serving Mac (the Captain) must run the merged main: check its
`~/src/ai` head over ssh; if behind, update it the way earlier rollouts did
(`core update --wait` in the kernel job's environment). Then, from this
task (reopened by the session after the merge report), read the project's
whole git history (`git log --all --name-status -M`, merges, renames) and
its dev chat (`valor-telegram read`, paging `--before` to the first
message), draft the facts in the private folder (`~/Desktop/Valor/facts/`),
and write them over ssh with the CLI against the Captain's kernel, copying
the spec to a non-iCloud path for `--project` and deleting it after. Every
fact cites its sha or message id, is dated when it became true, starts
inferred; `source.read` rows record the last sha and message read. The
turn ends with one numbered batch of the least sure facts, each with my
reading as the default, to the session. Nothing is posted to the dev chat.

## Out of scope

Briefs, recall, status pages and the UI (#3685); stored confidence decay
(age is printed, not scored); automatic alias matching; an extraction job
that runs on its own; ending a fact with no successor.

## Tests (`tests/test_facts.py`, real Postgres, made-up projects)

Add, supersede twice, read the chain back in order; `--as-of` before the
first, between, and after; a superseded fact absent from current, present
in history; confirm records the answer id and the status; a supersede
dated before the old start is refused, nothing written; an alias resolves,
case and space folded; links expand to depth 1 and 2 and no further;
another project's and another client's facts, entities and links never
return, and their ids are "no such fact"; dropping the schema and ingesting
again restores the same facts, intervals, chains and links; a retaken unit
writes nothing twice; fact rows make no `Record` (search unchanged);
`valor-record` through a live gateway: POST write, GET read, a write on a
revoked token refused as today; the CLI with a spec file.

No check, gate, hook or approval step is added: confirmation is a recorded
answer, and an inferred fact stays readable.
