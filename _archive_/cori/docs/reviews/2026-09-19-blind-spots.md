# Blind-spot review, 2026-09-19

Run before build planning. Three passes: a red team on the authority and space model, an operator walkthrough of the first two weeks of use, and a consistency pass across the five documents. Twenty-eight distinct findings after merging. Each has a disposition: **patched** (the design now says so, section cited), or **decision** (the architect's call, listed at the end).

## Space and data boundary

| # | Finding | Disposition |
|---|---|---|
| 1 | A space had no definition beyond "a directory or repo"; a client with a stricter AI policy had nowhere to be expressed | patched: architecture §9 `Space` manifest |
| 2 | Space isolation in renders was enforced by agent-mergeable Context Builder code | patched: row-level security keyed per render, tech stack §3; architecture §1 |
| 3 | The active thread and inbox slices were not stated to be space-filtered; a space switch did not seal the thread | patched: architecture §1 |
| 4 | Global belief scope was a cross-space write channel; the Scribe could propose global scope from a client turn | patched: scope inherits the originating space; global only from the personal space or a confirmed card; architecture §3.4, §6 |
| 5 | Executors could receive OPERATOR-class context and write it into a class 2 artifact bound for a client | patched: per-agent-class `max_data_class`; cards show destination audience; architecture §3.1 |
| 6 | Cross-space views (morning brief, inbox) were forbidden by the partition rules | patched: kernel-composed from per-space Scribe summaries; architecture §9 |
| 7 | Inbound mail and calendar items had no space until someone assigned one, and inference from content was forbidden | patched: connectors with deterministic routing rules; `unassigned` header-only space; architecture §9 |
| 8 | Ending a client engagement collided with append-only grants, content-hashed request bodies, and the hash-chained export | patched: per-space envelope encryption from M0; destroy the key; tombstone event; architecture §9, tech stack §3 |
| 9 | Broker credentials were one pool, so one client's action could use another's key or sending identity | patched: credentials keyed by space and target; `allowed_targets`; tech stack §7 |
| 10 | Cost was not attributable per client | patched: usage rows and effects carry space; tech stack §4, architecture §9 |
| 11 | Credentialed class 0 reads (mail, calendar, client systems) had no home; sandboxes hold nothing and the broker held only effect credentials | patched: connector credentials in the broker; reads performed outside the sandbox; tech stack §7 |

## Authority and approvals

| # | Finding | Disposition |
|---|---|---|
| 12 | Class 0 to 2 approvals were minted by adapter code that agents can merge | patched: the kernel authenticates the channel and mints every approval record; tech stack §13, architecture §7 |
| 13 | Everything ran under one OS user on the kernel host, so a class 1 Executor could read kernel credentials and approve its own cards | patched: separate OS user for kernel and approval socket from M0; sandbox never on the kernel filesystem; native macOS deferred; tech stack §2, §6 |
| 14 | A class 3 `merge` bound the approval to a payload whose branch the Executor could change after the tap | patched: payloads bind a target-state digest (head SHA) the broker re-reads; tech stack §7 |
| 15 | A push credential in the sandbox could force-push or delete any unprotected branch, a class 2 effect held at class 1 | patched: `push_branch` is a typed class 1 broker action; tech stack §7 |
| 16 | Attention exhaustion read as permission to proceed | patched: exhaustion never lowers an approval requirement; architecture §4 |
| 17 | An unsupported assumption challenge could cancel every sibling for free | patched: challenges priced against the caller; nearest holding ancestor only; architecture §2 |
| 18 | Nobody was named as the authority that assigns a belief's source class; the Scribe could label an inference as the person's words, or the person's corrections could arrive quarantined | patched: kernel derives the ceiling from the evidence author; corrections and approvals are kernel-typed utterances; architecture §6 |
| 19 | Procedural memory was "changed by review" in one document and agent-mergeable in the other | patched: Cori, Framer, and Verifier prompts, checklists, seat policy, and space manifests join the trust boundary; tech stack §10 |
| 20 | The `verify` sandbox has no network and re-executes evidence, so dependencies had no stated source; artifacts could carry prose aimed at the Verifier | patched: kernel-built image from a lockfile via an allowlisted registry; deterministic checks recorded before the Verifier reads prose; tech stack §6, architecture §5 |

## Operation and time

| # | Finding | Disposition |
|---|---|---|
| 21 | Cori's own turns, standing turns, and the implicit Scribe spent from no budget | patched: per-space standing budget as a standing grant; architecture §4, §9 |
| 22 | A day with the person unavailable had no defined behavior | patched: away state from the read cursor; architecture §7 |
| 23 | Class 3 approval away from the desk had no path, and the passkey relying-party id would be chosen by accident | patched: production RP id at M0; authenticated relay to a phone at M1; cards expire with the objective deadline; tech stack §13 |
| 24 | "Every active correction is rendered" cannot survive a year of corrections in an 8k persona slice | patched: render by relevance; compiled persona records what was rendered; architecture §1, README |
| 25 | Verification was code-shaped; documents, messages, and decision briefs had no defined check | patched: verification by artifact kind; architecture §5 |
| 26 | The project's purpose, understanding the person better over time, had no metric | patched: four understanding metrics beside calibration; architecture §6 |
| 27 | The conversation surface and the approval surface were undefined relative to each other; secrets, backups, and M0's first objective were unspecified | patched: architecture §7; tech stack §3, §10 |
| 28 | The README's corrections ledger and the architecture's operator record read as two stores; "Cori writes nothing" contradicted the Scribe's write capability; Scribe proposals read as extraction | patched: one store, filtered by source class; `memory.*` is a separate capability family; architecture five decisions, §3.4, §6 |

## Decisions for the architect

- **Sending identity.** Decided 2026-09-19: Cori acts as Tom, with a few personal access tokens. Larger independent work is instructed to Valor, who has his own accounts. Architecture decision 7 and §3.4.
- **Key destruction as data destruction.** The design reconciled NDA destruction with append-only storage by destroying the space's encryption key. Resolved by the [commit pass](2026-09-19-commit-pass.md): no client has asked for a destruction certificate, so encryption is removed and ending a space deletes its rows.
- **Second surface timing.** The walkthrough found that a terminal-only first two weeks is right for the state machine and wrong for the morning brief and for cards that arrive away from the desk. Resolved by the commit pass: CLI at M0, a FastHTML page at M1, Telegram a possible third.
- **pyright strict in CI.** Resolved by the commit pass: no type gate in CI.
