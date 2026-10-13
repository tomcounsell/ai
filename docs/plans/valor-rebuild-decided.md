---
tracking: none
slug: valor-rebuild-decided
type: plan
status: draft
---

# Valor rebuild: decided by default

The calls in [valor-rebuild.md](valor-rebuild.md) that Valor made without
asking Tom. Tom can overturn any of these; each is reversible.

- The judgement port, the blind verifier, the emulator, and the GitHub
  credential for pushes are built inside milestone 1, since the pipeline
  needs them before takeover.
- Milestone 2 binds a message to a task without a classifier: a reply to
  one of Valor's messages goes to that task, and any other message from Tom
  starts a new task. Judgement use shapes 2 and 3 wait for a misrouted
  message.
- The objective tree is built at the start of milestone 4, where routines
  first need it.
- Stage instructions live as plain files in `skills/sdlc/` until the skill
  system is designed.
- The read-only status page in `ui/` comes with routines.
- Stakes per milestone as stated in [valor-rebuild.md](valor-rebuild.md).
- Polls are dropped from the Telegram bridge, per Tom's ruling on `main`
  (#3550).
- The inbound Telegram record carries the forum topic id.
- The rebuild runs on Valor's Mac, where the old system also runs.
- The answer keys for cuttlefish #646, popoto #191, and popoto #188 stand
  as written ([m1-5-emulator-records.md](m1-5-emulator-records.md)).
- The old system's spend on the replayed PRs is not needed: every
  milestone's evidence compares against the bare baseline and the emulator,
  not against the old system's cost.

