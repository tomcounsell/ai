# Enterprise Valor — the core

Direction-orienting note. Setting: an institutional bank, greenfield, no tech debt yet.
Technology choices are deliberately absent — see
[`enterprise-valor-architecture-alternatives.md`](enterprise-valor-architecture-alternatives.md),
which is the appendix to read *after* this is settled, not before.

---

## The thesis

**The product is the record of authority, not the agent.**

Coding capability is commoditizing and the bank can buy it from anyone. What it cannot buy
is a defensible answer to "who permitted this change, under which rule, on what evidence,
and could the thing that made the change have altered any of that?"

So the center of gravity of the codebase is the record and the gate. The agent is a plug.

---

## Five nouns

The whole system is five nouns and one spine. If a proposed feature does not create, read,
or constrain one of these, it is not core.

| Noun | Definition | The invariant that makes it real |
|---|---|---|
| **Work Item** | A goal, with an identity | Every artifact, fact, decision, and log line carries exactly one work item id. Nothing exists outside a work item |
| **Envelope** | The grant — what this work item may touch — computed from the goal and the rules, *before* anything runs | Immutable once issued. The executing side has no path to modify it |
| **Run** | One bounded execution against one envelope | Cannot start without an envelope, cannot exceed it, and the limit is enforced by infrastructure rather than by code the agent could route around |
| **Fact** | A claim about what a run produced, carrying its producer | Every fact names who produced it and whether the agent could have influenced it. A fact the agent could have written is a different class of fact |
| **Decision** | Rules applied to facts | Allow, deny, or ask-a-human — always with the trace of which rule read which fact |

And the spine:

**The Record.** Append-only, written by every component, outside every component's authority
to alter. The invariant: *the entire history is reconstructable from the record alone*, with
no live system running and no agent memory consulted.

That last one is the load-bearing property. Everything else is in service of it.

---

## Four subsystems

1. **The Cage** — where work runs, and the *single door out of it*. The agent lives here with
   no privilege. Every privileged act — reaching the network, using a credential, pushing a
   commit, calling a model — goes through that one door, which checks the envelope and writes
   to the record. The door is the whole invention. Everything else is well-understood.

2. **The Gate** — rules in, decisions out. Deterministic, explainable, versioned.

3. **The Record** — everything, forever, unfalsifiable.

4. **The Console** — where humans approve rules, answer the questions the rules do not cover,
   and read the record. This is where the bank actually lives, and it is routinely
   underbuilt.

The agent is not on this list. It is a component *inside* the cage, and it should sit behind
an interface from day one — partly so it can be replaced, and partly because the bank's own
third-party concentration-risk rules will require that it can be.

---

## What a bank gives you that a startup does not

This is the part that makes the bank the *easier* customer, not the harder one.

**The control library already exists.** A bank has hundreds of written, approved, audited
controls governing change. You are not authoring policy — you are *transcribing* it. That
reframes the product: the first serious surface is a control transcription workbench, and the
first non-engineering hire is from second-line risk. It also means coverage can be measured
against a finite known list instead of discovered by trial.

**Segregation of duties is already the architecture.** The person who writes a change cannot
approve its deployment — that is not a design principle you have to argue for, it is an
existing audit requirement. So "the thing that executes is not the thing that judges is not
the thing that signs" needs no justification. It is SoD expressed in infrastructure, and it
maps one-to-one onto something the bank's auditors already know how to examine.

**"Self-tested is not tested" is already an audit finding.** The hardest idea in the proposal
— that evidence produced inside the sandbox is weaker evidence because the agent could have
influenced it — is a conversation a bank has already had about control testing. They will get
it immediately. It also means the independent re-run is core, not a later phase.

**The model is a model.** Banks have a whole regime for model risk (inventory, validation,
ongoing monitoring). An LLM making engineering decisions will be pulled into it. Cheap to
accommodate now — pin the model version into every decision record and retain the exchange —
and expensive to retrofit.

---

## Three things to mandate on day one

Greenfield is a wasting asset. These cost nothing now and are effectively impossible later.

1. **A service manifest in every repository** — tier, data classification, owner, rollback
   method, deployment target. This is the single highest-leverage item in this note. A rule
   like "tier 1 requires verified rollback" has no inputs unless something authoritative says
   what tier a service is. In a bank with history, that data lives in a CMDB that is
   substantially wrong; greenfield, you can make it a merge gate on repository one. Programs
   like this fail at backfill across four thousand repositories, and this is how you never
   have that problem.

2. **Hermetic, reproducible builds.** Otherwise "the artifact you authorized" is not a
   well-defined thing, and the entire authorization chain is decoration.

3. **One identity plane** covering humans, services, runs, and artifacts. Every later
   guarantee is stated in terms of identity; two identity systems means no guarantees.

---

## The hello world

Deliberately trivial, and it proves the thesis or it does not:

> One internal repository. One control, transcribed from the bank's existing change-management
> policy. One work item that satisfies it and gets a signed authorization. One work item that
> violates it and is refused. Then delete the running system and reconstruct both stories from
> the record alone.

If that last step works, the architecture is right and everything after is scale. If it does
not, nothing else matters yet.

---

## What v1 is explicitly not

- Not autonomous production deployment. Not in v1, and saying so early is what buys the room.
- Not a policy language project. Rules are configuration; the moment they become a platform,
  the program has changed shape.
- Not a replacement for the bank's scanners, CI, or IAM. It orchestrates them and records what
  they said.
- Not an agent-capability project. If the agent gets better, good; the system's value does not
  move.
