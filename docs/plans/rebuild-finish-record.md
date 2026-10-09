---
tracking: none
slug: rebuild-finish-record
type: plan
status: active
---

# Finishing run: record

The record of the run [rebuild-finish-prompt.md](rebuild-finish-prompt.md)
describes. Each task's own plan file holds its stages; this file holds the
lead's decisions across tasks.

## Follow-ups carried or dropped (C2)

From the earlier runs' notes, checked against the code at 1b9cde3ed:

| Follow-up | Decision |
|---|---|
| A cancelled `run_turn` kills no process group, so the harness keeps spending (`core/runs.py`) | Task C3, stakes 2, before cutover |
| `test_gateway_meter` takes port 6561 as one where nothing listens | Task C4 |
| Email log lines carry no timestamps | Task C4 |
| `docs/harnesses.md` "Denied entirely" omits the performing directory | Task C4 |
| Critique and docs read the turn-written `.valor/verdict.json` | Folded into A3, which gives docs its runner |
| Plan window steps that say "Tom sends" (`m2-3-email-rollout.md`) | Dropped: that window is not rerun; the live window here uses stand-ins |
| Cap sweep part two (Redis poll, `_connects`, Jev and open-weight timeouts, Keychain timings, `reap_grace_s`, `client_max_size`, `mirror_fetch_*`) | After cutover: none blocks a real task |
| `core/checks.py` keeps its own setup and suite tails | After cutover |
| `judge.py` `export_final` runs plain `git` | After cutover: emulator only |
| `valor-index-*` left in `output/` after a killed kernel | After cutover |
| Plan files over 600 lines | Dropped: records of merged work |
| Telegram key error names the wrong command | Dropped: fixed |
| Telegram flood-wait flake | Dropped: fixed (m2-2f) |
| Setup plan's governance paragraph | Dropped: byte-identical now |
| A build task idles without `done.md` | Dropped: idling is by design since `idle_turns` was removed |
| `bounded` has no timeout | Dropped: by design |

## Decided by default

- #3609 (live tests in the test check) stays parked: its plan waits on
  Tom's answers on cost per task, and cutover does not need it.
