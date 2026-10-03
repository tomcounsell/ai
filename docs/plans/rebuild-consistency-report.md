# Rebuild docs: consistency report

The Step 5 Pass 2 consistency pass over the thirteen docs (`docs/*.md`,
`docs/bridges/*.md`), every directory README, and the root README.

## Checks run

1. **One vocabulary.** Terms for the judgement tier, the SDLC states, the
   constraints, the performers, and the delivery event, compared across the
   set.
2. **One owner per mechanism.** Each mechanism described in more than one
   doc was assigned one owner; the others now point to it.
3. **Every directory README points at its governing doc.** A "Governed by"
   line in each README's Scope section.
4. **Writing rules.** Line counts, "cori" in any case, em dashes, `_archive_`
   paths, history wording, and the governance paragraph's bytes.
5. **Facts against the code and the records.** Performer names, the persona
   string, `task.delivered`, and the baseline's concurrency.

## Vocabulary settled

| Term | Used everywhere as |
|---|---|
| The port for judgement calls | `JudgementPort` (was "decisions port" in architecture.md and core/README.md) |
| The request judge | the request-underspecification classifier, judgement task `intake.underspecified`, run in the `judge` state (was "clarify-or-build classifier" in the bridges docs) |
| Its ledger row | `judgement.answered` (was `request.judged` in sdlc-state-machine.md) |
| A low-confidence call | takes its judgement task's abstain route; never a question to Tom about the classifier (architecture.md, tech-stack.md, machine.md, telegram.md had "goes to Tom") |
| A `done.md` before verification | a **candidate**; `task.delivered` today, verify-gated in the design |
| SDLC states | `judge`, `clarify`, `waiting`, `build`, `breadth`, `verify`, `delivered`, `reopened`, `stopped`; the kernel's run states (`live`, `waiting for Tom`, `delivered`, `stopped`) are named as such and mapped in sdlc-state-machine.md |
| Constraint names | the four bold names in mission.md (emulator.md had "Bounded", "Stoppable", and "Correctable constraint"; the README's four properties stay where they cite the README) |
| The outbox performer | `outbox_send` (`act`), class `OutboxAppend` (architecture.md had `outbox_append`) |
| "Judgement" | the vendor-neutral term; "Jev-class" only for the capability class |

## Ownership settled

| Mechanism | Owner | Others now point to it |
|---|---|---|
| Kernel, broker, approvals, stop, steering, verifier, attention cost | architecture.md | mission.md, data.md, tech-stack.md |
| Which sandbox runs which work | architecture.md | tech-stack.md (candidate table removed), harnesses.md, data.md, machine.md |
| Sandbox profile rules and the reaper's marks | harnesses.md | architecture.md (guarantees only), tech-stack.md (rule list removed) |
| Gateway per-call algorithm | architecture.md (Metered spending) | tech-stack.md (keeps only the stack-specific parts) |
| Bridge port | bridges/telegram.md | architecture.md, bridges/email.md |
| Corrections and exemplar streams | `core/` (architecture.md, data.md) | memory/README.md, core/README.md, README.md |
| Judgement router and fallback placement | judgement-layer.md | tech-stack.md, machine.md (sizes the local alternative) |
| Breadth judgement (use shape 8) | sdlc-state-machine.md decides it is a judgement after a deterministic suite run | judgement-layer.md |
| Storage of transcript copies | data.md (a new document kind) | harnesses.md |

## Conflicts resolved

- **Corrections and exemplar ledger.** Code keeps them as events in `core/`.
  architecture.md, data.md, core/README.md, memory/README.md, and README.md
  now say `core/` owns both streams and `memory/` later reads and curates
  them. The plan's directory table differs; flagged (questions E1).
- **`task.delivered`.** architecture.md, data.md, harnesses.md, and
  sdlc-state-machine.md state the built behaviour (a `done.md` writes it)
  and the design (a candidate, written only after `verify` passes).
- **Attention cost.** mission.md and data.md now follow architecture.md:
  counted in escalations, recorded and shown, never refusing. Flagged (D1),
  with the approvals-as-escalations tension (D3).
- **Bridge port.** One owner, telegram.md; architecture.md points to it.
- **Sandbox choice.** One owner, architecture.md; containers for turns is
  one open item in tech-stack.md. Flagged (F1).
- **Fallback placement.** judgement-layer.md said no leg runs on the Mac;
  machine.md left local loading open. Now: never resident, hosted in the
  design, local load-on-demand the open alternative. Flagged (B1).
- **`role_played` on approvals.** Design in every doc; consistent.
- **Emulator location.** scripts/ today, `tests/` in the design, stated
  once in emulator.md and matched in tests/README.md.
- **Persona rendering.** persona.md, harnesses.md: the kernel renders only
  "You are Valor." today (matches `harnesses/claude_code.py`).
- **Baseline concurrency.** machine.md said "up to about nine `claude`
  processes"; the series ran up to three replays at once
  (`VALOR_DEMO_SLOTS` default 3; the lane logs record at most two others
  live as each run started).
- **Performers.** architecture.md said "two performers"; there are three.
- **bridges/README.md** allowed "a standing grant on the ledger" and
  `propose` reactions; telegram.md has neither. README aligned.
- **Root README.** The memory line, the docs line, a broken `site/README.md`
  link, and the Status paragraph (which still said the kernel came next).

## Changes per doc

- architecture.md: `JudgementPort`; three performers; candidate vs
  `task.delivered`; owns the sandbox split; abstain route; bridge port
  pointer; corrections ownership; pointer to harnesses.md for profile rules.
- sdlc-state-machine.md: `judgement.answered` row; breadth names use shape 8.
- judgement-layer.md: shape 8 note resolved; fallback "never resident".
- mission.md: attention cost follows architecture.md; shape 5 labels axes.
- data.md: `task.delivered` note; attention cost pointer; one sandbox
  owner; corrections ownership; history wording removed.
- harnesses.md: `done.md` is a candidate; sandbox pointer to architecture.md;
  transcript storage pointer; "now" removed.
- tech-stack.md: sandbox rows and section point to architecture.md; profile
  list and gateway steps replaced by pointers; abstain route; metering of
  judgement legs marked design; fallback placement.
- machine.md: concurrency figure corrected; abstain route.
- bridges/telegram.md, bridges/email.md: classifier name; abstain route.
- emulator.md: constraint names; classifier name.
- persona.md, routines.md: no change needed.
- Directory READMEs: a "Governed by" line in each Scope; memory/README.md
  corrections bullet; core/README.md port name, stage list, corrections
  bullet; bridges/README.md effect classes; routines/README.md "kernel
  process"; tests/README.md emulator location; docs/README.md doc list.

## Mechanical verification

- Line counts: every doc under 600 (largest: architecture.md 599 lines by
  `wc -l`, sdlc-state-machine.md 598).
- `grep -rniE 'cori'` over `docs/*.md docs/bridges/*.md */README.md
  README.md` (excluding `_archive_/`): no hits.
- Em dashes: none. En dashes: none.
- `_archive_`: one hit, README.md's directory map, which describes the
  archive that exists on the branch until Step 6 deletes it.
- Governance paragraph: identical SHA-256 (prefix `59260d5d543fd95e`) in
  CLAUDE.md and all twelve directory READMEs.

## Open items

None besides [rebuild-open-questions.md](rebuild-open-questions.md).
