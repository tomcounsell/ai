# Truth over time: dated, superseding, linked facts per project

Issue #3684 is the spec (Data model, Operations, No-gos, Acceptance). Tom,
2026-11-09: "Yes, happy to use popoto." The first real use is a client
project named in the task's Brief, not here (the repo is public).

## What is built

1. **Models, `memory/facts.py`** (popoto on Postgres, schema `memory`):
   `Fact`, `Entity`, `Alias`, `Link`, `Source`, with the issue's fields.
   Each stores `project` = the spec's `repo` and its `client`, as `Record`
   does, and `ledger`. `Fact.validity` is a `ValidityField` (valid-from =
   when it became true); `Link` carries a kind and a `CoOccurrenceField`
   for the bounded BFS. Spiked on popoto 1.10.1: all of it works, so
   popoto needs no change (one found in build goes to popoto).
2. **Ledger rows, one unit each**, on the `memory` stream: `fact.added`,
   `fact.superseded`, `fact.confirmed`, `entity.added`, `entity.aliased`,
   `link.added`, `source.read`, each payload carrying project, client,
   provenance and a write key. `core/memory.py` adds them to `TYPES`, and
   `_unit` takes project and client from the payload for them, as for
   `memory.imported`. They make no `Record`: recall and search unchanged.
   A fact's name is `<ledger>/<row id>`.
3. **Rules.** Intervals are half-open: at the supersede instant the new
   fact holds, the old does not (spiked). A supersede dated before the old
   fact's start is refused with popoto's reason. Superseding a non-head is
   refused naming the head (spiked: popoto would open the new fact and
   close nothing, leaving two current). Confirming a superseded fact is
   allowed; its interval is unchanged. An alias is stored case- and
   space-folded, unique per project and client: `entity add` whose name or
   alias resolves returns that entity and adds new aliases; an alias held
   by another entity is refused, naming it.
4. **The port, `core/memory.py`** `fact_write(conn, spec, writes)`: a list
   of writes, each with a key and an optional label, naming earlier labels
   as `@label`. Per write it looks the key up on the `memory` stream (a hit
   returns the existing name, appends nothing), checks the rules, appends,
   and ingests, so later writes can supersede or link it. Returns label ->
   name. `facts_read` serves the reads. All filter on the spec's repo and
   client; anything else is "no such fact". Writes go through the gateway,
   not an effect: effects run after the turn, so the writer could not read
   back, supersede or link. The docs saying the record route and the port
   only read change (`core/record.py`, `docs/harnesses.md`,
   `docs/memory.md`, `core/memory.py`).
5. **Turns: `valor-record`** gains the issue's operations (`fact`,
   `entity`, `link`, `source`, `facts`, `sources`), project from the grant;
   writes are POST with a per-call key, so a retry appends nothing. Text
   prints quoted as data, inferred facts labelled.
6. **The kernel CLI** `python -m core facts --project NAME_OR_FILE apply
   FILE | read ...`: JSONL writes, keyed by the file's hash and line label,
   so a re-run after a dropped ssh writes only the rest and prints the same
   map. The only way to write the serving Mac from another Mac. It runs in
   the kernel job's environment (`VALOR_MACHINE`, `VALOR_DEFAULT_MACHINE`,
   `VALOR_PGPORT`, PATH from `launchctl print gui/$UID/com.valor.kernel`),
   since over ssh the hostname is `Mac.local` and `ledger_name` would
   differ from the kernel's, which would ingest every row again.
7. **Docs:** `docs/memory.md` Facts, `memory/README.md`, `tools/README.md`.

## Seeding the first project (after the merge)

The merge report asks the session to steer this task with "seed"; a steer
of a merged task is feedback, reopening it in `patch` in this workspace
and session. That turn commits no code (a defect found is a commit with a
test) and delivers counts, source cursors and the batch. It checks the
Captain runs the merged main (updating it over ssh as rollouts do), reads
the whole git history (`git log --all --name-status -M`) and the dev chat
(`valor-telegram read`, paged to the first message), drafts JSONL files
in this task's workspace under `.valor/` (git-ignored; never iCloud, never
`$TMPDIR`), copies each file and the spec to a non-iCloud path on the
Captain, applies them, and deletes both copies on both Macs after. Facts
cite a sha or message id, are dated when they became true, start
inferred; `source.read` rows record the last sha and message read. It
ends with one numbered batch of the least sure facts, each with my reading
as the default, for the session. Nothing is posted to the dev chat.
The turn exits `ended` with `.valor/stop.md` (no candidate, no review).
Tom's answers come back the same way, steered as feedback: a reopened turn
writes `fact confirm` rows with the CLI on the Captain, each citing the
`feedback.given` row as `<ledger>/<id>` (another ledger's id is accepted).
Done (rule 5) is dated in this file's last line, quoting Tom's question,
by the turn that answers it from the facts.

**Out of scope:** the issue's No-gos, the UI (#3685), stored decay,
ending a fact with no successor.

## Tests (`tests/test_facts.py`, real Postgres, made-up projects)

The issue's Acceptance list, plus: as-of exactly at a supersede instant;
non-head supersede refused, nothing written; confirm of a superseded fact;
entity add with a known alias returns it, a held alias refused; a labelled
file applies in order with `@label`; the same file applied twice writes
nothing the second time and prints the same map; a confirm citing another
task's or ledger's row reads as confirmed with that answer; a partly applied file
re-run writes only the rest; a retried keyed POST appends once; facts
written by the CLI with a spec file are returned by `valor-record facts`
in a turn of that project through a live gateway; the CLI's ledger name and
`Ingested` rows equal the kernel's, so its next ingest takes nothing; fact
rows make no `Record`.
No check, gate or approval step is added: confirmation is a recorded answer.

Done: not yet.
