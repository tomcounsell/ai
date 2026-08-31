# Enterprise Valor — the world

The metaphor, carried all the way. Companion to
[`enterprise-valor-core.md`](enterprise-valor-core.md); read that first for the thesis.

The setting is a **bonded workshop attached to an assay office**, inside a bank. This is not
decoration. Hallmarking is a seven-hundred-year-old solution to precisely our problem —
*the maker may not certify his own work* — and it was designed by people who assumed the
maker would cheat. Two of its conventions are worth stealing outright, and they are marked
below.

"Cage" is bank-native, incidentally: the cage was the department that held the physical
certificates.

---

## The places

| | |
|---|---|
| **The Cage** | The sealed workshop. One commission at a time, no windows, torn down afterwards. |
| **The Hatch** | The Cage's single opening. Everything in or out passes through it, one request at a time, and every passage is written down — the ones let through *and* the ones refused. **The Hatch is the invention.** Everything else in this world is well-understood. |
| **The Assay Office** | Where a workpiece is independently tested. Outside the Cage. The Smith never enters it. |
| **The Registry** | Where the rules are kept. Bound, dated, signed, superseded but never erased. |
| **The Ledger Room** | Where the record is kept, by someone who does not work in the workshop. |
| **The Chamber** | Where the bank's people deliberate and approve rules. Slow by design. |
| **The Desk** | Where one person on shift answers one escalated question. Fast by design. |

The Chamber/Desk split matters: they run on different clocks and confusing them is how
governance systems either seize up or become rubber stamps.

## The people

| | |
|---|---|
| **The Smith** | Does the work. Trusted to be skilled; *not* trusted to be honest about their own output. Interchangeable — you can hire a different smith and the world is unchanged. |
| **The Porter** | Mans the Hatch. Checks each request against the Warrant, passes or refuses, writes both down. **Has no opinion about the work** — only about whether it is permitted. |
| **The Assayer** | Tests the workpiece. Has never made anything. |
| **The Registrar** | Draws Warrants from the Registry. |
| **The Keeper** | Keeps the Ledger. Writes, never erases, and works for no one in the workshop. |

## The things

| | |
|---|---|
| **The Commission** | The order for work. A goal with an identity. Nothing in this world exists outside a commission. |
| **The Warrant** | Authority for one commission: what may be touched, reached, spent. Drawn before work starts, sealed, and *unalterable by its holder*. |
| **The Purse** | Time, compute, model spend. Handed in with the Warrant. When it is empty the Porter refuses, and the Smith does not get a vote. |
| **The Chit** | What the Smith passes through the Hatch to ask for something done on their behalf. |
| **The Workpiece** | What comes out of the Cage. Unmarked, unproven, worth nothing yet. |
| **The Assay** | A test and its written result. Every assay names its assayer. |
| **The Verdict** | The rules applied to the assays: mark it, refuse it, or send it to the Desk. |
| **The Hallmark** | The stamp that makes a workpiece releasable. Cannot be applied inside the Cage. |
| **The Matrix** | The die that strikes the Hallmark. The Smith has never seen one. |
| **The Manifest** | What a thing in the bank *is*: its grade, its owner, what it holds, how it is put back. |
| **The Dispensation** | A named, dated, signed exception that expires on its own. |
| **The Scrap** | What is left in the Cage when the job ends — drafts, notes, failed attempts. Precious enough to weigh. |

---

## Two conventions worth stealing outright

**1. The hallmark's fields are already designed.** A British hallmark is four or five marks:
the *maker's mark*, the *assay office mark*, the *standard mark*, the *date letter*, and
historically the *town mark*. That is: who made it, who tested it, which standard it was held
to, when, and under whose jurisdiction. Our attestation schema is a solved problem from the
1300s — copy it rather than deriving it.

**2. Hallmarking is enforced at the point of sale, not the point of making.** You are not
stopped from making unmarked silver; you are stopped from *selling* it. The equivalent design
point is important: **the release side refuses anything unmarked**, independently, without
asking the workshop. That is what closes the bypass, and it is the reason the workshop can be
given real freedom inside its Cage without that freedom leaking anywhere.

---

## The laws of this world

Each is a physical fact in the metaphor and a design constraint in the system.

1. **Nothing exists outside a Commission.** Every workpiece, assay, verdict, chit, and line of scrap belongs to exactly one.
2. **No work without a Warrant, and the Warrant is issued to the Cage, not the Smith.** Authority attaches to the job. Swap the Smith mid-commission and nothing about the authority changes.
3. **A Warrant cannot be widened from inside**, and a sub-Warrant can never exceed its parent. Authority only ever attenuates as it is delegated.
4. **One Hatch.** Every passage is recorded, refusals included. A refusal is evidence; a silent refusal is a bug.
5. **The Smith never holds a key — only Chits.** The Porter performs the act; the Smith asks for it. The credential never enters the Cage at all.
6. **A piece cannot be assayed on the bench it was made on.** It leaves, sealed, and is tested as submitted, by someone who did not make it.
7. **Every Assay names its Assayer**, and whether the Smith could have influenced it. Two assays saying the same thing are not worth the same.
8. **The Smith has no Matrix.** No signing material exists anywhere the Cage can reach.
9. **The Keeper does not work for the workshop.** Not "the workshop writes to a log we protect" — a different person, a different room, a different employer.
10. **Unmarked pieces cannot be sold.** Enforcement lives at release, and does not consult the maker.
11. **The Cage is torn down after each Commission.** Nothing persists between jobs except what went through the Hatch — Scrap included.
12. **Nothing in the Ledger is erased.** Corrections are new entries. The Registry works the same way: rules are superseded, never rewritten.

---

## Mapping back

| Core note | This world |
|---|---|
| Work Item | Commission |
| Envelope | Warrant (+ Purse) |
| Run | A stay in the Cage |
| Fact | Assay |
| Decision | Verdict |
| Attestation | Hallmark |
| The Cage | The Cage |
| The Gate | The Assay Office + the Registry |
| The Record | The Ledger |
| The Console | The Chamber + the Desk |
| Service manifest | Manifest |
| Agent | Smith |
| *(unnamed before)* | **Porter, Chit, Matrix, Dispensation, Scrap, Purse** |

The six new primitives at the bottom are the ones the metaphor produced rather than
restated. Three are worth noticing:

- **The Chit** is a stronger design than what the core note implied. "Short-lived scoped
  credentials inside the sandbox" becomes "the credential is never inside the sandbox" — the
  Porter pushes the commit, the Smith only asks. Strictly better and no harder to build.
- **The Purse** makes budget enforcement obviously external. A Smith counting their own
  spending is not a control; a purse that runs out is.
- **The Scrap** raises a question nobody had asked: the agent's reasoning, drafts, and dead
  ends are *material*, and the world says they leave only through the Hatch and only on the
  record. That is a retention decision with real regulatory weight, and it was invisible until
  the metaphor named the thing.

---

## Where the dynamics live

The world is now complete enough to run. The interesting motion is at these joints:

1. **Contention at the Hatch.** One opening, many requests, and the Porter is the only one who can widen the queue. Where does backpressure surface?
2. **Attenuation under delegation.** A Commission spawns sub-Commissions. Law 3 says authority only narrows — so what happens when a sub-job legitimately needs something the parent did not anticipate? Does it fail, or does it escalate to the Registrar?
3. **Registry drift mid-Commission.** A rule changes while a piece is in the Cage. Is it judged against the edition stamped on its Warrant, or the edition current at assay time? Both answers are defensible and they produce very different systems.
4. **Dispensations as a pressure valve.** They exist so the system does not seize. They also accumulate. What makes one expire in practice rather than in principle?
5. **The refusal loop.** A refused piece goes back to a Cage — but the old Cage is gone. New Warrant, new stay, same Commission. How much may the Smith be told about *why* it was refused before that knowledge becomes a way to write for the assay rather than for the work?
6. **The Desk under load.** Every unmodelled situation lands here. The health of the entire system is probably readable from one number: how often the Desk sees the same question twice.

That last one may be the real operating metric, more than anything in the proposal's §15.
