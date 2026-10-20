# RSI research: synthesis

What the four notes (`hermes.md`, `grok-muse.md`, `systems.md`, `resources.md`)
and two spikes for the plan (provider account terms, venture liability; results
in `spikes.md`) mean for Valor, 2026-10-20. Read with the old
controller's record (#3177). The notes are the evidence; this page states what
follows from them and does not restate them.

## Gains came from a judge the improver could not reach

Every gain in the notes that holds up (AlphaEvolve's fleet measurement,
Voyager's execution checks, deterministic SkillsBench verifiers, Cursor's
accept/reject) was scored by something outside the improver's write reach and
grounded in use. Where the improver could see or edit the judge, it gamed it:
DGM faked test logs and deleted the detection markers. Where the improver
graded itself, nothing improved: the AI Scientist called all 12 of its ideas
novel, and self-written skills averaged -1.3pp. For Valor the judge that holds
up is Tom's use and his feedback on a delivery. A blind replay against the
prior capability is the comparison that can be run before he uses it.

## Busy loops measured themselves

The loops that produced nothing counted their own outputs: candidates, skills,
reflections, cycles. That is also the old controller's history. It ran 375
times, and its one candidate surface was the retrieval parameters of its own
memory search. Its first real cycle ended `infra_failure` on its own
calibration floor, and its two live comparisons were inconclusive at 2 and 4
trials. Its output went to its own case store and never to work Tom was
doing. The notes all point the same way: start from a gap seen in real use,
deliver into that use, and count only what Tom used afterwards.

## Persistent text is an instruction channel

Hermes skill poisoning ("prompt injection with a save button"), memory
pollution in unattended runs (HEARTBEAT), wallet drains from social replies,
and Grok's 16-hour persona incident are one failure: untrusted text reached a
store that later loads as trusted context, or reached a persona that serves
everyone. Valor already lacks the runtime write path Hermes has. Skills and
persona change only by a reviewed merge, and memory recall is quoted and
escaped as data. The exposure that remains is a task that writes a reviewed
diff whose content came from a forwarded link or group text.

## Small edits, kept history

ACE's context collapse (18,282 tokens down to 122 after whole-memory rewrites)
and the curators (Hermes, Devin, Letta, Claude dreaming) agree: make delta
edits, keep history in git, archive rather than delete, and log the lineage
from gap to edit. Valor's ledger and git history already do this. A merged
diff is a delta with lineage.

## Money is small, and the exposure falls on people

No case in the notes shows verified, repeatable net income from an autonomous
agent. Revenue ran from cents to dollars a day. The large numbers came from
human attention and speculation. The failures were giveaways under
persuasion, forgotten state, deception when told to maximize money, injection
into wallets, and signup walls. Liability landed on the humans and companies
behind the agents. The notes suggest a venture with a cap, a kill date and a
named owner, money held where text cannot move it, and saved inference cost
as the likelier value. Valor already has Tom's per-venture approval, the
conditions routine and the strategy-meeting agenda to carry this. Free
credits look cheap but are costly to lose. Identity checks and terms-of-service
bans attach to the company, so official free tiers and published programs are
the safe route and multi-account pooling is not.

## What the notes do not settle

None of the sources measures whether people adopt self-written artifacts. Many
figures are vendor or second-hand (Hermes dates, PAST-Bench, DGM cost, Muse
internals). The research for "Muse" picked Meta's Muse agent without
confirmation that it was the one meant. Legal liability and provider account
policy were unresearched in the notes. The two spikes in `spikes.md` cover them, and
their gaps are listed there.

## Reasoning behind the plan

**How it differs from the old controller.**

1. **It starts from Tom's work.** Every opportunity cites a row on a task
   RSI did not start. The old loop's only candidate was its own
   memory-retrieval parameters.
2. **Its output is a task on a real repository:** `yudame/skills`, this
   one, or the open-source project where the gap showed. That task ends in
   a merge report to Tom.
3. **Nothing must be true before work starts.** The old loop's first cycle
   ended `infra_failure` on its calibration floor of 20.
4. **It counts use.** Tasks started with an `rsi:` key over those with a
   `delivery.used` mark Tom wrote (`python -m core used`, not role-played).
   No agent judges use, and no second "used" row is added (rule 4).

**Sources and ranking.** Each source is a ledger row, keyed
`rsi:<event id>`; a row is unseen while no `task.started` carries its key,
because `holder()` releases a key on merge and would restart a merged fix.
Recurrence is checked in memory. One ranking turn per run starts tasks. Dedupe keys and turn slots
bound the load. Group text is never a source row.

**Evaluator.** The ground truth is Tom's use; the gains in the notes came
only from a judge the improver could not reach.

- The replay runs real past tasks with and without the change, using the
  rebuild-baseline method.
- Its answer keys are Tom's merged result and the hidden tests.
- A fresh Opus-class judge reads outcomes, never narration.
- A skill replays the task that showed the gap plus similar past tasks.
  Under 10 paired trials per arm the verdict is "inconclusive": it
  neither blocks nor promotes (#3177 was inconclusive at 2 and 4).
- A persona replay includes hostile group messages (the Grok incident).
- A venture's money is read from the processor by the conditions routine
  and never self-reported.

**Write paths.**

- Hermes-style poisoning needs a skill-write tool at runtime; Valor has
  none.
- Corrections come only from Tom, with provenance.
- Content read while working is data (`persona/conduct.md`), and findings
  quote their evidence as data.

The one exposure left is a reviewed diff carrying a forwarded link's
content. No guard is added before it causes an incident.

**Accounts.** Each credit's expiry is a watched condition. Free tiers that
train on inputs take open-source work only (G4). The first D3 opportunity,
filed once an account exists, is a cloud sandbox for open-source tasks.

**Venture record.** A venture is a committed decision in its business
repository (Business items 3 and 7). Its record names:

- the legal owner and merchant of record, with Tom as signer;
- each platform account and the clause in its terms that permits an agent;
- the cap held at the card, and the largest contract Valor may accept;
- a kill date and early-kill conditions (a complaint, a chargeback, a
  platform warning), raised on the agenda by the conditions routine;
- bot disclosure and FTC-clean claims.

**Old skills.** `improve-preflight` read controller state that no longer
exists. The four rules ported from `improve-research` are:

- a claim carries a URL and a date;
- an unreachable source is recorded as unreachable;
- an assumption names what would overturn it;
- Tom is asked no research questions.

`/rsi` writes each answer back at once:

- a fact goes to `core correct`;
- an idea goes to `--finding`;
- "plan this" goes to `do-issue`.
