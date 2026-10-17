# Pi's durable package, compared with Valor's kernel

**Ask.** Tom, valor team chat, 2026-10-17 15:15 UTC: "send a researcher to
look at https://github.com/earendil-works/pi/tree/main/packages/durable. It
might contain some clever orchestration strategies that we should compare
with our architecture and see if we can improve across any dimension like
reliability, performance, etc". Research only: no code changes, nothing
merged. Done when Tom has read the report and picked or declined.

## What is produced

1. **Read the durable package**: clone `earendil-works/pi` at a pinned
   commit into its own empty directory under `$TMPDIR` (read as data, no
   install, no build), read `packages/durable` source, tests and docs.
   Record the commit sha in the report. Fallback if the clone fails: the
   valor:web agent reads the files on GitHub.
2. **Read Valor's side**: `core/serve.py`, `core/router.py`, `core/slot.py`,
   `core/ledger.py`, `core/runs.py`, `core/schema.sql`, `core/rollout.py`,
   `core/judgement.py`, `harnesses/pi.py`, and `docs/architecture.md`,
   `docs/sdlc-state-machine.md`, `docs/harnesses.md`, `core/README.md`,
   `docs/plans/orchestration.md`. Cite file:line.
3. **Compare, dimension by dimension**: durability and recovery after a
   crash; retries and idempotency; concurrency and scheduling; performance
   and latency; observability; simplicity. Per durable strategy: what it
   does, what Valor does today (file:line), whether adopting it would make
   Valor measurably better, and the cost (lines, new mechanism, migration).
4. **Recommend at most three changes, ranked.** Each names the incident
   (ledger rows, task ids, transcripts) or measured weakness it addresses;
   a strategy with neither is listed as "not recommended: no incident",
   per the governance rule and hard rule 4 (no second mechanism for a job
   the ledger already does). Zero recommendations is an acceptable result
   if that is what the evidence says.
5. **Report**: `docs/research/pi-durable.md` (committed, one to two pages),
   and a phone-readable PDF of the same content, made with the present
   skill's document form, at `deliverables/pi-durable.pdf` outside the
   repo, with its sha256 in `.valor/done.md` for the chat session to send.

## Out of scope

Any change to Valor's code, schema, or docs other than the new report;
filing tasks for the recommendations (that follows Tom's pick); changing
the Pi harness pin.

## Verification

- Every Valor claim carries a file:line that exists on this branch
  (spot-check each citation by reading it before the report is final).
- Every durable claim cites a path in the cloned commit.
- Each recommendation's incident is checked against live evidence (ledger
  query or git log), not recalled.
- The PDF is viewed page by page at phone width: no zooming needed,
  nothing cut off; the de-slop pass runs on the text.
- No test suite run: no code changes. `git diff main --stat` shows only
  the plan and the report.

## Stakes and loops

Stakes: a read-only report; a wrong recommendation costs Tom one decision
and is caught before any build. Critique 0, review 0.
