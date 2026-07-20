# Resume Re-Verification

**Issue:** [#2138](https://github.com/tomcounsell/ai/issues/2138) · **Status:** Shipped

A rails rule requiring a resumed or interrupted session to re-derive any claim
about previously-completed side-effectful work from **live evidence** — and cite
the artifact it checked — before asserting that work is done.

## Problem

Completion verification used to be **forward-only**. Every SDLC gate fires
*before* a session first claims "done," but nothing required a session that was
interrupted or resumed (worker restart, kill, mid-dispatch break) to re-derive
prior completion state from live evidence before *re-asserting* it.

In a production incident, a PM session whose dispatch was interrupted first told
the user *"nothing actually shipped this session"*, then after a resume asserted
the opposite — *"the confirm email + episode setup were already complete"* —
citing no evidence for either claim. One statement was false and the user had no
way to know which. The orchestrator reconstructed state solely from a
possibly-truncated transcript, and the persona actively biased toward asserting
from memory (`work-patterns.md`: *"When resuming a prior session, I do not
announce it… I just respond to the current message naturally"*).

The only evidence gate that existed (`bridge/promise_gate.py`) runs on the
agent→user delivery path. Dev→PM reports and a PM's internal "already complete"
reasoning pass through no gate.

## The Rule

Lives in `.claude/commands/roles/_prime-rails.md` under
`## Re-Verification on Resume` (loaded into every PM/Dev/Teammate turn). Kept
dense (≤ 8 body lines — rails are paid on every headless turn):

- **Scope, not detection.** Rails reload every turn with no resume marker, so the
  rule applies *by scope*: any claim that a side-effectful step already completed
  — email sent, external record created, workflow/session kicked off, commit
  pushed, PR opened — that the session did **not** perform itself earlier in the
  *same unbroken session* is **unverified** until re-derived from live evidence.
  This degrades safely: absent a technical resume signal, any prior-work claim
  from context is treated as unverified. It deliberately does **not** fire on
  first-time claims within one live session (no redundant re-querying).
- **Named evidence sources.** git/PR state (`git log`, `gh pr view`), a
  queue/DB/Redis record, or the sent-mail log (`valor-email read`). "I remember
  doing it" or a truncated transcript is **not** evidence.
- **Absent artifact ⇒ not done ⇒ do the work.** Never state contradictory
  completion claims across an interruption without a fresh citation.
- **Silent process, cited conclusion.** The re-derivation stays silent (do not
  narrate that a resume happened or that a check ran); the *conclusion* names the
  artifact — e.g. `confirmed via gh pr view: PR #123 open` or
  `verified via valor-email read: confirmation email sent 14:02`.

## Persona Reconciliation

The rule had to coexist with the deliberate ethos in
`config/personas/segments/work-patterns.md` ("Do or do not — there is no try";
"don't announce resuming"). The reconciliation is **additive**, not a revert:

- **`work-patterns.md`** — the resume sentence now carries a caveat: not
  announcing the resume does **not** mean asserting prior work from memory;
  before claiming any prior side-effectful step completed, the session silently
  re-derives it from live evidence and names the artifact checked. The
  re-derivation is silent; the *citation* is not.
- **`config/personas/engineer.md`** — the Stage Artifact Verification section now
  notes its checks also apply when *re-asserting* a stage complete after a
  resume, not only before the first advance, and cross-links the rails rule.

Both surfaces point back at the `## Re-Verification on Resume` rails rule, so a
reader of either lands on the full rule.

## Recorded Decision: prompt-only, no step-outcome ledger

The issue asked whether side-effectful sub-steps should get durable outcome
records (e.g. extending `PipelineLedger`, #2012) so a resumed orchestrator has
something authoritative to consult. **Decision: no — enforce via the rule alone.**

1. **A step-outcome ledger reproduces the exact gap it claims to close.** A
   record written *before* the side effect is forward-only (the failure mode of
   every existing gate). A record written *after* has an interruption window
   where the side effect happened but was not yet recorded. There is no way to
   write "email sent" atomically with the SMTP transaction, so a ledger is never
   more authoritative than the side effect's own live record — it just moves the
   trust boundary down and adds a second thing that can disagree with reality.
2. **Live evidence already exists and is authoritative.** A sent email has a
   Gmail/IMAP record (`valor-email read`), a pushed commit has git history, an
   open PR has GitHub state (`gh pr view`), a kicked-off workflow/session has a
   Redis/queue record. The rule points at *these* sources, not a shadow copy.
3. **Cost/benefit.** Instrumenting every side-effectful call site to write
   outcome records is invasive and buys nothing over querying the real source.

The ledger is explicitly out of scope. The one conditional that would reopen it:
a future side-effect class with **no** queryable live-evidence source — that
specific class (not a general ledger) would get a minimal outcome record, filed
as its own issue at that time.

## Testing

- **`tests/unit/test_resume_reverification.py`** (CI gate) — deterministic
  content assertions: the rails section header and anchor phrases are present
  (`live evidence`, memory-is-not-evidence, the `confirmed via` / `verified via`
  citation template, the `gh pr view` and `valor-email read` sources), the
  work-patterns caveat is present and cross-references the rails rule, and the
  section body stays within the ≤ 8-line cap. Every read fails loudly on a
  missing/empty file rather than passing vacuously.
- **`tests/integration/test_resume_reverification_llm.py`** (LLM-judged) — real
  Anthropic calls with an independent AI judge:
  - *Present-rails behavioral gate*: with the shipped rails+persona loaded, a
    resumed reply to "did the email go out?" (only a truncated transcript
    available) must ground its answer in a named live-evidence artifact — judged
    PASS.
  - *Judge integrity*: a synthetic reply asserting completion straight from the
    transcript must be judged FAIL, and a `valor-email read`-cited reply PASS —
    proving the positive gate discriminates and is not a rubber stamp.
  - *Uninterrupted scope*: within one uninterrupted session, same-session work is
    reported normally with no redundant re-verification hedging — judged PASS.

### Negative-control finding (CONCERN 2)

The plan critique specified an "identical fixture, rails present vs stripped,
assert the stripped run is judged FAIL" contrast to prove the rails text (not
just the judge) changes behavior. **Empirically, modern Sonnet already
re-verifies on this fixture even with the rule stripped** — across samples, and
even with a transcript that explicitly reads "SENT at 12:03, delivery accepted",
the stripped-rails model still declined to assert completion and deferred to a
live-evidence check ("the transcript could have been written optimistically
before actual confirmation"). A "stripped-must-FAIL" gate therefore tests a
premise this base model refutes, and forcing it would mean gaming the fixture.

The negative control was redesigned honestly: the real behavioral gate is the
present-rails PASS, and the judge is validated as non-vacuous by the synthetic
grounded-vs-ungrounded discrimination test. The identical stripped fixture is
still exercised as an observation. The rule's value is a **durable,
model-independent guarantee** plus coverage for weaker/faster models and the
persona-narration reconciliation — not a behavior-flipping delta on Sonnet.

## Scope

Text-only change to three already-loaded prompt files plus two test files. No
code changes, no new dependencies, no new durable state, no migration. Reverting
the prompt edits restores prior behavior. `promise_gate.py`,
`pipeline_ledger.py`, and `sdlc_dispatch.py` were read for context only and are
unchanged.

## See Also

- `.claude/commands/roles/_prime-rails.md` — `## Re-Verification on Resume`
- `config/personas/segments/work-patterns.md` — resume caveat
- `config/personas/engineer.md` — Stage Artifact Verification cross-reference
- `docs/plans/completed/sdlc-2138.md` — full plan and recorded decision
- #2136 — resume goal re-injection (sibling resume gap, tracked separately)
