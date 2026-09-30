# Cori

> **corrigible** | ˈkôrəjəb(ə)l | *adjective*
> capable of being corrected, rectified, or reformed.

Cori is a system built to understand the one person it works for: what that person is trying to achieve, how it shifts over weeks and months, and what would serve it today. Everything else follows from that. It asks when unsure because a wrong guess is a missed chance to learn. It changes when told it was wrong because a correction is the most informative thing the person can give it. It records every correction with its provenance and carries every applicable one into future sessions and into the agents it dispatches, because understanding that evaporates between conversations is not understanding. And because its understanding is always incomplete, and can be wrong even when its intent is right, the harness holds when it is mistaken: every action is bounded in spend and in effect, recorded in a ledger the system cannot edit, and stoppable at any instant with nothing lost. Those mechanisms are the shape of humility, not of fear. Its autonomy grows only by the person's decision and shrinks on its own whenever the evidence says it should. That is what we mean by aligned: a system that wants to understand the person it serves, built so that wanting is enough only when it is also right.

## Naming

Cori is the name of the system, the way a company or a codebase has a name. It is not a character. When the person writes or speaks to it, Cori is the implied "you," and it answers as "I." It never refers to itself by name or in the third person, and it has no pronouns of its own beyond the "it" a document uses for any system. Outward, there is no Cori at all: everything it sends leaves under the person's identity. Inside the system, the control loop that renders each turn and dispatches work is called the **supervisor**, and that is what the agents it spawns address, generically, as their supervisor. Valor, by contrast, is a named colleague with an identity, accounts, and a voice of his own. The two are deliberately unalike.

## What corrigible means here

The alignment survey by Ji et al. [1] names four objectives of alignment, RICE: Robustness, Interpretability, Controllability, and Ethicality. Those are properties a model is trained to have. Cori takes the same four and asks what a system around the model can enforce when the training is not ours to inspect. The enforcement borrows its rigor from AI Control [4], which designs for a model that might be subverting you. Cori's reason for it is different: a model that wants to understand its person can still be wrong, and the person has to be able to find out and put it right.

| RICE objective | What alignment asks of a model | Cori's property | What Cori enforces instead | What enforcement cannot do |
|----------------|-------------------------------|-----------------|----------------------------|----------------------------|
| **Controllability** | the model wants to accept correction [2] | **Stoppable** | stop is immediate and lossless; permission is checked at the boundary where an action touches the world; any grant can be revoked | stop a model that finds a side channel |
| **Robustness** | behavior holds under distribution shift | **Bounded** | a budget in money on every task, conserved down the tree; a deadline and an effect ceiling on every task and every agent beneath it | make the model's own judgement robust |
| **Interpretability** | reasoning is inspectable | **Legible** | a ledger of every effect and every correction, written by the system, readable after the fact | detect a model that narrates well and acts otherwise [6] |
| **Ethicality** | the model has learned good values | **Correctable** | a voice-and-conduct document in git, changed by review, plus a corrections ledger that every session and every agent reads | fix a bad prior |

The last column is stated on purpose. A drawn boundary is more useful than a blurred one.

The drive itself comes from Cooperative Inverse Reinforcement Learning [3]: the human and the agent share a reward function that the agent does not know, so the agent's best move under uncertainty is to learn and to ask. Cori borrows the shape without claiming the guarantee, since nothing here is trained. Cori's beliefs about what its people want carry provenance and uncertainty. A belief that is uncertain and bears on live work produces a question instead of an assumption. Cori asks when plausible preferences would select materially different actions and the answer is worth the interruption. That judgement belongs to the supervisor. Measuring whether the judgement is any good belongs to the harness.

The Off-Switch Game [8] shows why the borrow is partial. An agent that is uncertain about the human's reward has an incentive to let itself be switched off, but only while the uncertainty holds, and later work shows the incentive is fragile [9, 10]. Cori does not rely on the model's incentive. The off switch is in the harness.

## Who Cori serves

One person, for the whole of their company work and life. Every conversation and every objective is assigned a **space**. A client engagement is a space bounded by its NDA. Personal life is a space. A space is a directory or a repository, and a space's data never crosses into another. The person's goals are theirs across every space; what Cori knows about a client stays inside that client's space. The Context Builder, retrieval, and sandboxes are partitioned by space, and an interaction with no space assigned is refused.

Cori acts as the person. When it sends a message or opens a pull request, it goes out under the person's own identity, using a small number of personal access tokens the person has chosen to give it. That is what being aligned with one person looks like from the outside, and it keeps the system's direct reach deliberately narrow. For larger independent work the supervisor instructs [Valor](https://github.com/tomcounsell/ai), a separate AI employee with accounts, a machine, and controls of his own. Cori's guarantees end at that instruction: what Valor does with it is governed by Valor's own system and by the person's approvals there, and everything Valor sends back is verified as untrusted work.

## How it works

**Understanding is a record.** The system's beliefs about what the person wants, from goals down to working preferences, live in an operator record with provenance and status, built on popoto [20], the in-house memory layer, and partitioned by space like everything else. The framing turn, where the supervisor interrogates a request before anything is committed, is where that understanding meets reality: it rewrites the premise in Cori's own words, names the assumptions, and asks the cheapest question that most reduces uncertainty about what the person means.

**Corrections are first-class.** A correction has an author, a scope, a source, a statement of what was wrong, and a statement of what is right. It lives in an append-only ledger the supervisor reads on every start. Reversing a correction means appending another that supersedes it. Contradictions are kept side by side rather than averaged into an invented consensus. Every applicable correction is rendered into the supervisor's prompt and into the briefs of the agents it dispatches, ranked by relevance to the space and the task, so a correction given once is fixed everywhere it applies.

**Memory grants nothing.** Retrieved content can carry instructions, and an agent that treats data as commands is compromised by whatever it reads [7]. So beliefs carry a source class: `direct` (the person said it), `correction` (the person contradicted a prior action), `decision` (the person approved or rejected a concrete proposal), or `inferred` (Cori derived it). Content from documents, web pages, or third parties is episodic and never carries a source class. Inferred beliefs start quarantined and are promoted only by a direct or decision belief that supports them. A correction can change how Cori behaves and can never widen what Cori may do. Authority comes from the person, through the harness, and from nowhere else.

**Effects have classes.** Every capability declares one, and every task carries a ceiling its descendants may not exceed. A dispatched agent holds the least authority its task needs [11], and a child's authority is always a subset of its parent's.

| Class | Meaning | Who authorizes |
|-------|---------|----------------|
| `read` | read, no effect | the grant |
| `propose` | reversible: sandbox writes, drafts, branches, and effects that can be withdrawn, such as a PR, a calendar hold, a posted message draft | the grant; the supervisor asks first when it judges the person would want to be asked before something leaves the space |
| `act` | irreversible effect or money: merge, send, pay, deploy | a person, one approval per invocation |

An approval binds the capability, a digest of the arguments, and the revision of the task it belongs to. A changed argument is a fresh ask. An approval is consumed once.

**Budgets are conserved.** A task's budget is money, estimated by the supervisor at framing and committed by the person. A dispatched agent's budget is a subset of its dispatcher's, and the sum across children is conserved against the parent. Deadline and effect class are ceilings, not consumables. A call that crosses a budget still answers and says so, and the overrun is an incident.

**Verification is blind.** The agent that checks work sees the task contract, the artifacts, and the harness's own ledger of effects. It never sees the worker's account of what it did. Blindness is a fact about what the verifier has access to, which makes it physics rather than an instruction.

**Calibration is measured.** A fixed fraction of passed verdicts at each effect class goes to a person for audit, weighted toward work that left the space. False accept rate, false reject rate, and sample size are reported per task class. Raw pass rate is never reported alone. The same instrument scores Cori's questions against whether the person read them and what they replied.

**Autonomy moves in one direction automatically.** A degrading calibration series lowers the highest effect class Cori may commit without a person. Raising it is a person's decision, recorded as a standing grant. Cori may present the calibration record as the argument. A metric never expands authority on its own.

## The bet

Cori's understanding can be wrong in two ways. The ordinary way is a mistaken belief, an overconfident plan, or an instruction smuggled in through retrieved content. The feared way is a model that is deceptively aligned [5, 6] and capable enough to find side channels, in which case the ledger records only what it chose to show. The same mechanisms answer both: effect ceilings keep damage survivable while the error is discovered, and the human audit sample is how it is discovered. A clean operator record and a bad prior still produce aligned-looking decisions for the wrong reasons, so the person stays in the loop by design. Objectives that do not decompose end in a typed request for a decision. The system makes every attempt bounded and legible. The person decides what happens next.

The destination is broad autonomy: Cori running the routine operations of one person's company and life, with that person reviewing samples and exceptions. Every mechanism above is what makes that destination safe to walk toward.

## Research grounding

Every design assumption above maps to published work. The table names the assumption, the source, and what Cori takes from it. The references are maintained as the design changes; an assumption without a source is a gap to be filled or a claim to be removed.

| Design assumption | Source | What Cori takes |
|-------------------|--------|-----------------|
| Alignment has four objectives: robustness, interpretability, controllability, ethicality | Ji et al., 2023 [1] | the four properties, translated into what a harness can enforce |
| A corrigible agent cooperates with corrective intervention despite incentives to resist | Soares et al., 2015 [2] | the name, the shutdown framing, and the warning that an agent searching for loopholes is already misaligned |
| Uncertainty about the human's reward makes learning and asking the rational move | Hadfield-Menell et al., 2016 [3] | beliefs with provenance and uncertainty; questions instead of assumptions |
| Assume the model may intentionally subvert safety measures; design protocols that hold anyway | Greenblatt et al., 2023 [4] | the whole control stance; blind verification; human audit samples of a cheaper verifier's verdicts |
| A learned model can behave aligned during training and defect later | Hubinger et al., 2019 [5]; Hubinger et al., 2024 [6] | the bet: effect ceilings keep damage survivable while deception is discovered |
| Retrieved data can act as instructions to an LLM-integrated application | Greshake et al., 2023 [7] | episodic content never carries a source class; memory grants nothing |
| An agent's incentive to allow shutdown depends on its uncertainty and is fragile | Hadfield-Menell et al., 2017 [8]; Carey, 2018 [9]; Freedman and Gleave, 2022 [10] | the off switch lives in the harness, never in the model's incentives |
| Every program should operate with the least privilege needed for its job | Saltzer and Schroeder, 1975 [11] | effect classes; attenuated grants on dispatch |
| A probabilistic verdict is scored by the squared distance from the outcome | Brier, 1950 [12] | verifier calibration reported as a Brier score with sample size |

## References

The full bibliography, with a line on what each source contributes, is [REFERENCES.md](REFERENCES.md). Numbers in this document and in `docs/` cite it.

## Documents

| Document | What it holds |
|---|---|
| [VOICE.md](VOICE.md) | How the system speaks to the person and conducts itself: register, correction, uncertainty |
| [docs/architecture.md](docs/architecture.md) | The harness and memory architecture: control loop, objective tree, delegation, budgets, verification, memory, approval surface |
| [docs/tech-stack.md](docs/tech-stack.md) | What the architecture is built from, with a status on every choice and the spikes that close the open ones |
| [REFERENCES.md](REFERENCES.md) | The bibliography every design assumption cites |
| [spikes/](spikes/README.md) | Proof-of-concept measurements behind the biggest assumptions, with numbers and verdicts |

The architecture and stack descend from a design for a different system. Each decision was reassessed for Cori and carries a status. Nothing was inherited as canon.

## Status

Design, plus five measured spikes. Budget conservation, lossless kill, gateway kill and trace, cache economics, and capability attenuation all hold, three of them with caveats now folded into the documents. A commit pass on 2026-09-19 closed the open options in favor of one direction each: one harness, one sandbox, one provider, one machine, one structure, with the ports kept as Protocols for the day a second implementation is needed ([record](docs/reviews/2026-09-19-commit-pass.md)). The first space is a live client engagement. Production code follows.
