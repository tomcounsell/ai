# Rebuild: open questions for Tom

Every question the Step 5 Pass 2 docs left open, deduplicated and grouped by
theme. Each names the doc(s) it touches and whether it **blocks** planning
the rebuild (Step 6 of `valor-cori-rebuild-setup.md`) or **can wait** until
the component it concerns is built. Questions that only need a measurement,
not a decision, are listed last.

Where the docs disagreed, the consistency pass picked the reading that
matches the current code and made the docs agree; those picks are marked
**(consistency pick)** and need Tom's confirmation.

## A. The plan's own Step 6 questions

| # | Question | Docs | Blocks? |
|---|---|---|---|
| A1 | **Orchestration.** A lead agent with builders in worktrees, or one session per component driven by Tom: one plan, one build, one blind verification per component, no findings ledger? | architecture.md, sdlc-state-machine.md | blocks |
| A2 | **Order.** Recommended: `core/` kernel and data first, `bridges/` second, `harnesses/` third, `persona/` and `routines/` fourth, `tools/` on demand, `memory/` last when popoto ships Postgres. Confirm or change. | all | blocks |
| A3 | **Skills.** Requirements for the versioned skill system and the cross-repo refactoring skills. | skills/README.md, harnesses.md (Skill rendering) | blocks `skills/` only |
| A4 | **Emulator seeding.** Repos, date range, the human-originated filter, budget per sweep. | emulator.md (Growing the item set) | blocks the emulator, not the kernel |
| A5 | **Judgement vendor.** Jev or OpenAI's Judgements API as primary; which provider hosts the open-weight fallback. See B1. | judgement-layer.md, tech-stack.md, machine.md | blocks |
| A6 | **Archive deletion.** Confirm the docs and the demonstration are sufficient to rebuild from. | README.md, CLAUDE.md | blocks the rebuild start |

## B. Judgement tier

| # | Question | Docs | Blocks? |
|---|---|---|---|
| B1 | **Fallback placement (consistency pick).** The docs now say the design hosts the open-weight fallback at a second provider and never runs it resident. The alternative is a local copy loaded only while no turn holds the slot (offline, waits for the slot, about 5 GB). Which? | judgement-layer.md (The router), machine.md (Judgement), tech-stack.md (5) | blocks |
| B2 | **Client text to a vendor.** May request text from client repositories go to a judgements vendor, or does client work stay on the frontier provider already in use? The answer may add a routing rule. | judgement-layer.md (Open) | blocks |
| B3 | **Low-confidence routing on the request judge.** The docs route an abstain to `clarify` (costs Tom nothing when inspection finds no material question). The alternative is `build`. | sdlc-state-machine.md (Open 2), judgement-layer.md | can wait |
| B4 | **Tier bars.** Minimum `n` and Brier ceilings per error-cost tier, set from the first calibration records. | judgement-layer.md (Calibration discipline) | can wait |
| B5 | **Guard expiry after firing.** A guard that did fire by its ninety-day expiry: renewed by a new grant, or kept until it stops firing? | judgement-layer.md (Open), mission.md | can wait |
| B6 | **Feedback through the judge.** Should thin feedback ("closer, but not right") be judged like a request? No evidence of thin feedback yet. | sdlc-state-machine.md (Open 4) | can wait |
| B7 | **Code summary as a judge input.** The demonstration's estimate assumed the classifier also reads a short summary of the code; producing it needs an inspection step. Add only if the emulator shows it helps? | judgement-layer.md (Inputs), architecture.md | can wait |

## C. SDLC and verification

| # | Question | Docs | Blocks? |
|---|---|---|---|
| C1 | **Test-breadth grant.** The docs treat the 2026-10-01 lean-SDLC decision as the grant for the `breadth` check, ledgered with a ninety-day expiry like the judge step. Confirm, or it needs its own tap. | sdlc-state-machine.md (Open 1), judgement-layer.md (shape 8) | blocks |
| C2 | **Verifier model and strength.** Architecture requires an independent, equally strong model; the baseline's lenient reviewer was Sonnet-class. Frontier-class verifier, and what that adds per task? With one provider, independence rests on a different snapshot. | architecture.md (Verification), sdlc-state-machine.md (Open 3), tech-stack.md (Model seats) | blocks |
| C3 | **`task.delivered` semantics (consistency pick).** Today a `done.md` writes `task.delivered`. The design calls it a **candidate** and writes `task.delivered` only after `verify` passes. All four docs now say so; confirm the design. | architecture.md, data.md, harnesses.md, sdlc-state-machine.md | blocks |
| C4 | **Raising a task's budget.** No command exists; the design makes overrun a question to Tom whose grant raises the root. Confirm the shape. | architecture.md (Budgets), sdlc-state-machine.md (Budget exhaustion) | can wait |
| C5 | **Doc-against-reality sweeps (shape 7).** A `contradicted` label opens a task; it blocks nothing. Confirm it is not a gate. | judgement-layer.md (shape 7) | can wait |

## D. Attention and provenance

| # | Question | Docs | Blocks? |
|---|---|---|---|
| D1 | **Attention budget (consistency pick).** The docs now follow architecture.md: `attention_budget` on the Brief, counted in escalations (questions, feedback rounds, approvals); the kernel never refuses a question for exceeding it; crossing it is a ledger row shown on the delivery. mission.md had left the unit (decisions, PM rounds, minutes) and the behaviour open. Confirm the unit and that it never refuses. | mission.md, architecture.md, data.md | blocks |
| D2 | **`role_played` on approvals.** Design in every doc, not built: approvals gain `via`, `at`, and `role_played`, and join the attention fold. Confirm. | architecture.md, data.md (Gap 4), mission.md, sdlc-state-machine.md | can wait |
| D3 | **Authority taps in the attention count.** mission.md counts PM work and authority taps separately and calls taps the price of bounded authority, not a defect; architecture.md counts approvals as escalations against the budget. Should approvals count against `attention_budget`? | mission.md (How it is read), architecture.md | can wait |

## E. Corrections and memory

| # | Question | Docs | Blocks? |
|---|---|---|---|
| E1 | **Corrections and exemplar ownership (consistency pick).** The plan's directory table puts the corrections ledger in `memory/`. The code keeps corrections as `correction.recorded` events on the `corrections` stream in `core/`, because the demonstration needed them before memory exists. The docs and `memory/README.md` now say: `core/` owns both streams; `memory/` later reads and curates them and never writes them. Confirm, and the plan's table changes accordingly. | architecture.md, data.md, mission.md, memory/README.md, core/README.md, README.md | blocks (affects order A2) |
| E2 | **Correction withdrawal and narrower scopes.** Design: withdrawal by a later row naming the correction; scopes beyond `global` when a correction needs one. | architecture.md (Corrections and exemplars) | can wait |

## F. Sandbox, machine, secrets

| # | Question | Docs | Blocks? |
|---|---|---|---|
| F1 | **Sandbox split (consistency pick).** architecture.md now owns it: turns under `sandbox-exec` (as built), the verifier's re-execution in a fresh Apple container. tech-stack.md had proposed containers for Linux-toolchain turns too; that is now one open item there. Confirm, or move turns into containers. | architecture.md, tech-stack.md, machine.md, harnesses.md | blocks |
| F2 | **A separate macOS user for turns.** Removes the turn's path to the login keychain and Tom's files by structure. | machine.md (Open 3), architecture.md (Limits), harnesses.md (Known openings) | blocks the kernel's isolation design |
| F3 | **The gateway holds the provider key.** Today a turn's own Claude Code login authenticates and the public internet is open, so the gateway is the metered path, not the only path. Build the key-holding gateway in the first kernel milestone? | tech-stack.md (2), architecture.md (Limits) | blocks |
| F4 | **Kernel database credential.** `valor_kernel` with a credential only the kernel holds, so separation is a database fact and not only the sandbox's. | data.md (Gap 3) | can wait |
| F5 | **Is the Air dedicated?** Desktop apps take about half the 6 GB headroom. | machine.md (Open 1) | blocks the RAM budget |
| F6 | **Mains power and sleep.** Kept awake, or a power assertion per turn? | machine.md (Open 4) | can wait |
| F7 | **Retention and backup.** Nightly `pg_dump` to an external disk is chosen, not built; how long the ledger is kept is unset. | data.md (Gap 5), tech-stack.md (3) | can wait |
| F8 | **A resident kernel process.** Needed once a bridge delivers requests without Tom at a terminal; holds the gateway, broker, turn runner, and the cross-task turn slot. | tech-stack.md (2), machine.md, routines.md | blocks bridges |

## G. Bridges

| # | Question | Docs | Blocks? |
|---|---|---|---|
| G1 | **Operator notices outside the broker.** telegram.md treats messages to Tom's operator chat as the approval surface, not `act` effects; the README's effect table names every send `act`. Accept the distinction? | bridges/telegram.md (Gaps), architecture.md | blocks bridges |
| G2 | **Standing grants for sends.** Every reply to anyone but Tom waits for a tap; the broker has no standing grants. Add one (for example "replies in this chat")? It widens authority. | bridges/telegram.md, bridges/email.md | can wait |
| G3 | **Budget and ceiling of a task started from chat.** From settings, never from the message text, until Tom says otherwise. Confirm the defaults. | bridges/telegram.md (Gaps) | blocks bridges |
| G4 | **DMARC as the test of Tom's identity on email.** Is it a check under the governance paragraph needing a grant? | bridges/email.md (Gaps) | can wait |
| G5 | **Approval from a phone.** Through a bridge or a small web page; and whether an `act` approval needs a passkey signature over the payload. | tech-stack.md (9) | can wait |
| G6 | **Bridge libraries.** Telethon for Telegram, `imaplib` and `smtplib` for email, adapted from existing code. | tech-stack.md (9), bridges/*.md | can wait |

## H. Routines and the emulator

| # | Question | Docs | Blocks? |
|---|---|---|---|
| H1 | **Expiry sweep merge.** Should the deletion branch merge without a tap? Every merge is `act` and the kernel has no class for a merge granted in advance. | routines.md (Gaps) | can wait |
| H2 | **Stronger stand-in.** The Sonnet stand-in accepted every run, including fidelity-1 results. Move the stand-in to a stronger model? | emulator.md (About itself) | can wait |
| H3 | **Inferred answer keys.** Confirm the inferred lines of #646, #191, and #188's keys to turn them into recorded ones. | emulator.md | can wait |
| H4 | **Emulator spend under one budget.** Stand-in and judge calls run outside the gateway today; the design charges a sweep as a budgeted routine. | emulator.md (Cost), routines.md | can wait |
| H5 | **Mobile items.** `localsend/localsend` #2765 is the only usable mobile case; it needs Flutter through fvm on the machine. Install? | emulator.md (Mobile), machine.md | can wait |

## I. Persona and harnesses

| # | Question | Docs | Blocks? |
|---|---|---|---|
| I1 | **Persona file.** The kernel renders only "You are Valor." today; the full persona under `persona/`, with identity fields, is design. Build it with the kernel milestone or with `persona/` in A2's fourth slot? | persona.md, harnesses.md | can wait |
| I2 | **Codex and Pi.** A second harness needs a gateway route for its provider's wire format before its wrapper can exist. | harnesses.md, tech-stack.md | can wait |
| I3 | **Harness version pin.** Record the `claude` CLI version in `turn.started` so replays across months compare. | tech-stack.md (6) | can wait |

## J. Measurements, not decisions

Each is a gap until measured; none needs Tom's answer.

- Whether the appended system prompt reaches Claude Code subagents
  (architecture.md, harnesses.md, persona.md).
- Resume versus a fresh session seeded with a summary (harnesses.md).
- A headless browser under the turn's sandbox and its RAM (harnesses.md,
  machine.md, tech-stack.md).
- Memory on the Air: macOS itself, a connected Telegram bridge, a turn's
  peak with its work, an Apple container, a browser (machine.md, Gaps).
- One replay item run end to end in a container (tech-stack.md).
- Event-stream size at which a full fold stops sufficing (data.md, Gap 1).
- `random_id` duplicate behaviour from a user account (telegram.md).
- The provider's sent-folder behaviour for SMTP submissions (email.md).
- Per-run `/tmp` and per-run database credentials in replays (emulator.md,
  harnesses.md); a run's redis-server left running at teardown
  (harnesses.md).
- Training-data contamination of public replay items (emulator.md).
- Whether `sandbox-exec`'s deprecation matters on this system's timescale
  (tech-stack.md).
