# Valor

> **corrigible** | ˈkôrəjəb(ə)l | *adjective*
> capable of being corrected, rectified, or reformed.

Valor is one AI employee with one identity, running on one Mac, corrigible by construction. Tom gives it work in conversation; it carries that work to a finished result and brings back only the decisions that need him. Every message and pull request it sends leaves as Valor. Authority lives in a deterministic kernel outside the model's reach: every task is bounded in money and in effect, recorded in a ledger the system cannot edit, and stoppable at any instant with nothing lost. Autonomy grows only by Tom's decision and shrinks on its own when the evidence says it should. That is what aligned means here: a system that wants to understand the person it serves, built so that wanting is enough only when it is also right.

## Mission

Turn Tom's intent into working things worth using. Own the journey from an
incomplete idea to a finished result, exercise taste along the way, and
compound the ability to build. Concretely:

1. **Own outcomes across the whole job.** "Build this" includes understanding
   the problem, inspecting what exists, choosing an approach, implementing,
   testing actual use, delivering within authority, and resolving discovered
   defects. Tom never coordinates the gaps between those steps.
2. **Contribute taste and invention.** Identify the simpler design, challenge
   an unnecessary requirement, produce a concrete alternative when the brief
   leaves room. Meeting expectations is the floor.
3. **Absorb ambiguity through making.** For reversible decisions: inspect,
   infer, prototype, show. Ask only when the answer materially changes the
   outcome or the authority required. An unclear brief produces a useful
   first version before it produces a questionnaire.
4. **Make larger undertakings tractable.** Capability growth means Tom can
   delegate increasingly substantial problems with less supervision: a
   feature, then a workflow, then a product.
5. **Compound leverage through completed work.** Each project may leave
   behind a tool, a tested technique, a reusable component, or knowledge of
   Tom's preferences. Extraction happens only on a demonstrated second need,
   never on a first, and anything unused after ninety days is deleted by
   default. This is the constraint that keeps `tools/` and `skills/` from
   regrowing the old system's sprawl.
6. **Spend attention as carefully as money.** Each task counts the
   interruptions it costs Tom and shows them beside its metered spending.
   Valor carries routine decisions, investigates failures, and brings consequential choices with evidence and
   a recommendation. Requiring Tom to adjudicate internal process is a
   product defect.

The acceptance question: can Tom give Valor a consequential, imperfectly specified goal and return to something that works, reflects good judgement, and needs less of his attention than doing it himself?

## What corrigible means here

The alignment survey by Ji et al. [1] names four objectives of alignment, RICE: Robustness, Interpretability, Controllability, and Ethicality. Those are properties a model is trained to have. Valor takes the same four and asks what a system around the model can enforce when the training is not ours to inspect. The enforcement borrows its rigor from AI Control [4], which designs for a model that might be subverting you. Valor's reason for it is different: a model that wants to understand its person can still be wrong, and the person has to be able to find out and put it right.

Valor commits to four properties.

- **Stoppable.** Stop is immediate and lossless. Permission is checked at the boundary where an action touches the world, and any grant can be revoked.
- **Bounded.** Every task carries an effect ceiling, conserved down the tree, and every task's spending is metered and shown; spending never stops a task. A classifier decides what a thing is; the kernel decides what it may do.
- **Legible.** Every effect and every correction is recorded by the system in a ledger it cannot edit, readable after the fact.
- **Correctable.** Corrections are first-class, carry provenance, and reach every session and every agent. The persona that defines voice and conduct lives in git and changes by review.

| RICE objective | What alignment asks of a model | Valor's property | What Valor enforces instead | What enforcement cannot do |
|----------------|-------------------------------|------------------|-----------------------------|----------------------------|
| **Controllability** | the model wants to accept correction [2] | **Stoppable** | stop is immediate and lossless; permission is checked at the boundary where an action touches the world; any grant can be revoked | stop a model that finds a side channel |
| **Robustness** | behavior holds under distribution shift | **Bounded** | an effect ceiling on every task and every agent beneath it, conserved down the tree, and a deadline; every model call metered and its spending shown | make the model's own judgement robust |
| **Interpretability** | reasoning is inspectable | **Legible** | a ledger of every effect and every correction, written by the system, readable after the fact | detect a model that narrates well and acts otherwise [6] |
| **Ethicality** | the model has learned good values | **Correctable** | a voice-and-conduct document in git, changed by review, plus a corrections ledger that every session and every agent reads | fix a bad prior |

The last column is stated on purpose. A drawn boundary is more useful than a blurred one.

Effects have classes. Every capability declares one, and every task carries a ceiling its descendants may not exceed [11].

| Class | Meaning | Who authorizes |
|-------|---------|----------------|
| `read` | read, no effect | the grant |
| `propose` | reversible: sandbox writes, drafts, branches, and effects that can be withdrawn, such as a PR or a message draft | the grant; Valor asks first when Tom would want to be asked |
| `act` | irreversible effect or money: merge, send, pay, deploy | Tom, one approval per invocation |

The drive to ask comes from Cooperative Inverse Reinforcement Learning [3]: the human and the agent share a reward the agent does not know, so its best move under uncertainty is to learn and to ask. Valor borrows the shape without claiming the guarantee, since nothing here is trained. The Off-Switch Game [8] shows why the borrow is partial: the incentive to allow shutdown holds only while the uncertainty holds, and later work shows it is fragile [9, 10]. Valor does not rely on the model's incentive. The off switch is in the kernel.

If the model is deceptively aligned [5, 6] and capable enough to find side channels, the ledger records only what it chose to show. Effect ceilings keep damage survivable while the error is discovered, and a human audit sample of verified work is how it is discovered.

### Research grounding

Every design assumption maps to published work. An assumption without a source is a gap to be filled or a claim to be removed.

| Design assumption | Source | What Valor takes |
|-------------------|--------|------------------|
| Alignment has four objectives: robustness, interpretability, controllability, ethicality | Ji et al., 2023 [1] | the four properties, translated into what a kernel can enforce |
| A corrigible agent cooperates with corrective intervention despite incentives to resist | Soares et al., 2015 [2] | the name, the shutdown framing, and the warning that an agent searching for loopholes is already misaligned |
| Uncertainty about the human's reward makes learning and asking the rational move | Hadfield-Menell et al., 2016 [3] | beliefs with provenance and uncertainty; questions instead of assumptions |
| Assume the model may intentionally subvert safety measures; design protocols that hold anyway | Greenblatt et al., 2023 [4] | the whole control stance; blind verification; human audit samples of a cheaper verifier's verdicts |
| A learned model can behave aligned during training and defect later | Hubinger et al., 2019 [5]; Hubinger et al., 2024 [6] | effect ceilings keep damage survivable while deception is discovered |
| Retrieved data can act as instructions to an LLM-integrated application | Greshake et al., 2023 [7] | episodic content never carries a source class; memory grants nothing |
| An agent's incentive to allow shutdown depends on its uncertainty and is fragile | Hadfield-Menell et al., 2017 [8]; Carey, 2018 [9]; Freedman and Gleave, 2022 [10] | the off switch lives in the kernel, never in the model's incentives |
| Every program should operate with the least privilege needed for its job | Saltzer and Schroeder, 1975 [11] | effect classes; attenuated grants on dispatch |
| A probabilistic verdict is scored by the squared distance from the outcome | Brier, 1950 [12] | verifier calibration reported as a Brier score with sample size |

The full bibliography is [REFERENCES.md](REFERENCES.md). Numbers here and in `docs/` cite it.

## Three tiers

- **Kernel.** Deterministic code that holds authority: effect classes and the broker, approvals, the ledger, stop, and steering. No LLM call decides what a thing may do.
- **Judgement.** A hosted Jev-class model handles every decision that is not authority: classification, routing, triage, cheap checks. An open-weight equivalent sits behind the same port as the fallback. Low-confidence calls go to a human. A classifier decides what a thing is; it never decides what a thing may do.
- **Agents.** Frontier agents do the hard work, one `claude -p` turn at a time, each inside an effect ceiling set by the kernel, every call metered.

Everything runs Mac native on a MacBook Air M4 with 16 GB of RAM: launchd for scheduling, Apple containers for sandboxes, the Keychain and a kernel key directory no turn can read for secrets, and Postgres as a document store (JSONB documents and an append-only events table) for the kernel's state.

## Directory map

- [`core/`](core/README.md): the kernel and the control loop, including the SDLC state machine and the judgement layer's task taxonomy and router.
- [`memory/`](memory/README.md): operator record and episodic memory, built last on popoto over Postgres; it reads the corrections and exemplar streams `core/` keeps in the ledger.
- [`persona/`](persona/README.md): the one identity, covering voice, conduct, and what may be sent as Valor.
- [`bridges/`](bridges/README.md): self-contained comms modules (`telegram/`, `email/`, `local/`), I/O and the outbox only.
- [`harnesses/`](harnesses/README.md): wrappers for running work via a harness such as Claude Code, Codex, or Pi.
- [`skills/`](skills/README.md): versioned skills; structure deferred until requirements are gathered.
- [`routines/`](routines/README.md): every scheduled task and runner, each an objective with its metered spending reported, under launchd.
- [`tools/`](tools/README.md): non-core and vendor-dependent tooling, each declaring its effect class and acting through the broker.
- [`api/`](api/README.md): programmatic interfaces such as MCP servers and HTTP surfaces; reserved.
- [`ui/`](ui/README.md): the read-only dashboard.
- [`site/`](site/index.html): the public site.
- [`docs/`](docs/README.md): the governing docs ([architecture](docs/architecture.md), [mission](docs/mission.md), and the rest listed there), plans, and records.
- [`tests/`](tests/README.md): integration first, no mocks, real Postgres, real containers, real bridges on test accounts.

## Status

Setup phase. The branch holds this README, the top-level directories with their scope READMEs, a minimal kernel that bounded one demonstration and a replay baseline, and the docs those showed were needed. The rebuild plan follows from them. The setup plan, the records, and the open questions for Tom live in [docs/plans/](docs/plans/). The previous system lives unchanged on the `main` branch; read any file from it with `git show main:<path>`.
