# 2.2 Telegram bridge: checks and delivery

The plan is `m2-2-telegram.md`; its Build record holds the build and patch
round 1.

## Checks, round 2 of 2, at 6b2ccb794

- Docs: updated, fd466882e (`tests/README.md`: the emulator binds a free
  port and prints it).
- Test: red. Base 566 passed, head 621 passed, 9 skipped; ruff clean. One
  tick between connects after a failure, cleared only by a completed fill;
  a flood wait after part 1 waited out; missing parts completed with no
  duplicates; a changed or deleted file gives `failed`; both fsyncs; an
  unreadable file set aside; the reply chain walked 61 hops to the root; a
  stalled download blocks no later message; the emulator on port 0.
- Review: changes. Governance boolean: no. The 20 hops, the media timer,
  the 256 s backoff and the 5 s margin are gone and nothing replaced them.
  Completing a partial send stays within the approved effect.

Findings:

1. Red, both checks reproduced it. With `telegram-sends.json` lost, lookup
   scans the whole chat and adopts an earlier identical own message, so a
   send that never went out reads `done`, and the old message is claimed
   by the task, so a reply to it binds there. With the file intact the
   same case is `failed`. Fix: with no record for the key, return
   `Unknown`, or start the scan above the newest message id claimed in the
   chat before the intent.
2. A download from another data centre has no Telethon ping. A silent
   stall ends only when the bridge stops, and the message is not recorded
   until a restart; later messages flow. The docs say the 60 s ping covers
   it, which is untrue for these downloads. Fix: on each tick, cancel a
   download that received no bytes since the last tick, so the gap fill
   retakes it; correct the doc passages.
3. When finishing a part is refused, or the file is gone, lookup returns
   None and the effect is `failed` while part of the send is on screen.
   Fix: return the parts found, or raise `Unknown`.
4. For a chat with no ledger rows, the first gap-fill pass marks it seen
   without holding a download still in progress; if the download fails,
   no pass retakes the message.
5. A seen file with a wrong-typed value wedges every gap-fill pass.
6. Docs: telegram.md "One attempt" says no connection on the first message
   gives `failed`; the code leaves it in flight until a later reconcile.

## Delivery: delivered, not passed

Review rounds are spent. Recommendation to Tom: one more patch for the six
findings; finding 1 is a wrong outcome. The merge also waits on 2.1.

## Patch round 2, for the six findings of checks round 2

1. Lost sends file. A send in flight with no record of its start is now
   `Unknown`, never looked up over the whole chat: Telegram's history cannot
   tell it from an earlier identical message of the account's own. The mark
   (`LOST`, -1) is written for each send in flight when the lost file is
   found, so it survives the next restart. A notice's text carries its own
   id, so a notice with no record is still looked up over the whole chat.
   Test: an earlier identical own message, a send that never reached
   Telegram, the file emptied; reconcile stays in doubt, across a restart,
   and the old message is not claimed.
2. Downloads from another data centre. `Wire.download` takes a progress
   callback (Telethon's `progress_callback`); each tick cancels a download
   that received no bytes since the previous tick, and the same tick's gap
   fill retakes it. Docs corrected (`telegram.md`, `bridge.py`). Test: a
   stuck and a moving download; only the stuck one is cancelled and retaken.
3. Refused or impossible completion. `lookup` returns the parts found when a
   file of the send is gone or finishing is refused, so the send settles
   `done` with what is on screen. Test: text sent, file send lost, file
   deleted; reconcile writes `done` with the one message.
4. No ledger rows. The first pass holds a chat's seen mark below a download
   still running, as the pass with rows does. Test: a lone slow download;
   after `fill` the seen mark is the id below it.
5. Wrong-typed state values. A state file holding anything but whole numbers
   is set aside as unreadable, so the pass recovers from the ledger. Test:
   a seen file with a string value; the pass completes.
6. Doc: `telegram.md` "One attempt" now says no connection on the first
   message leaves the send in flight until a connection lets `lookup`
   settle it as failed.

Decision the lead may change: for finding 1, `Unknown` (a send in doubt
with the file lost waits for a human) over starting above the newest claimed
message, which has no floor when the chat holds no claims.

Suite and lint: see the head commit's report in valor-build-notes.

## Patch round 3: onto the merged 2.1, and L3

Rebased: 2.2's ten own commits (79b63700b through ee784c54e) now sit on
fba1da1da, the merged 2.1; the eight older 2.1 commits are dropped.

Conflicts and how each went:
- `docs/bridges/telegram.md`, two hunks (the performer line and the
  idempotency paragraph): kept 2.2's text. It states the bridge's
  `lookup(action, key, since)` of the merged `LookupFn`, and the
  record-based scan.
- `tests/test_replay.py`: kept the merged version whole; 2.2's only change
  there was an import order.
- `docs/README.md`: the merged list of top-level docs, with
  `objective-tree.md` added. `architecture.md` kept its earlier splits and
  holds only a pointer to `objective-tree.md`.

Follow-ups to the merged port, in tests only: `reconcile_after_s` and the
settle time are gone, so the telegram port helpers call `broker.request`,
`release` and `reconcile` with `performers` second, `release` takes the
task's workspace for sends that name files, and the killed-before-send test
now expects `failed` at the first reconcile. `test_emulator_metering.py`
imports `tests.conftest`, since 2.2's `--import-mode=importlib` has no
top-level `conftest`.

L3 (lookup against the port's "None only when final"): the code already
returns None for a record that finds nothing and never scans a send over the
whole chat; only a notice with a lost record is. The plan said "None: None"
and "a send with no record is looked up over the whole chat"; both now say
what the code does and why a miss is final (the record precedes the first
message). Test added: a record in an empty chat with nothing sent finds
None. It passes without a code change; there was no code defect to fail it.
The port doc's paragraph on the Telegram lookup, which 2.2 added, stays.

## Round 3 checks

- test-2-2-p3: pass. 1376 passed, 25 skipped; ruff clean; every earlier
  test file collects as at base, plus 62 Telegram tests. Gap: no committed
  test for a notice whose record was lost scanning the whole chat; the
  probe found the notice and resent nothing, and two equal messages gave
  `Unknown`.
- review-2-2-p3: pass. Governance boolean no. D1 and D2 (two doc
  sentences) fixed by the lead in f50b3ce24. Notes O1 a download is held
  whole in memory (stream it to a file), O2 thread entries carry no
  sender id, O3 a deleted sends file reads as no record.
- docs-2-2-p3: updated, f0b16aac1.

## Merged

- Lead suite on f50b3ce24: 1376 passed, 25 skipped; ruff clean.
- Backup `valor_rebuild-20261004T143014Z.dump`.
- `valor-cori-rebuild` fast-forwarded fba1da1da to f50b3ce24.
- Rollout step 1: `uv sync` and `python -m bridges.telegram keys` ran
  (API id and hash written). The test servers' live test and steps 3 to
  11 run in the test window with 2.1's steps 2 to 6.
- Follow-ups: O1 to O3; the lost-record notice test.

## Rollout, test window of 2026-10-04

- Step 1: the test servers refuse the documented test login (a
  `99966` 2 `xxxx` number with code `22222` returns PhoneCodeInvalid, three
  tries), so `tests/test_live_telegram_dc.py` has not run. Probed
  (probe-tg-testdc, 2026-10-04): the bridge's wiring reaches the test
  servers (`help.getConfig` returns `test_mode`, DCs 1 to 3 at the test
  addresses), `send_code_request` succeeds and migrates to the phone's DC,
  and `sign_in` with the DC digit five times still returns
  PhoneCodeInvalid, for the repository's API id and for a public one, on
  ports 80 and 443, with other suffixes and code lengths. The refusal is
  the server's, not the code's. Not runnable from this machine; the lead
  recorded step 1 as not run, since the emulator suite and step 8's live
  send carry the evidence. It runs if the API development tools panel for
  this api id shows test settings that make the documented login work.
- Step 3: the group "Valor rebuild" holds Tom and Valor's account, id
  `-1003890616618`, created from the bridge's session. `main`'s
  `projects.json` names no group the title contains. `projects/valor.toml`
  lists it in `chats` with `machine = "Mac"` (this Mac's default machine
  name, so the command line and the kernel share slot keys);
  `VALOR_OPERATOR_CHAT` is its id.
- Step 4: the running system disabled by label; its bridge ended.
- Step 5: the bridge's session signed in from the vault phone and
  password, user id 6914249008. The login code was read through a copy of
  the stopped old bridge's session, deleted after; the old bridge
  reconnected afterwards with no auth key error.
- Step 6: `com.valor.kernel.telegram` bootstrapped from
  `~/src/valor-build-notes/plists/`; it connected.
- Step 7: open. It needs Tom's messages in the group, in a window.
- Step 8: `tests/test_live_telegram_window.py` passed: one real send, the
  child killed after Telegram accepted it, the lookup found exactly one.
- Step 9: the bridge job booted out; the running system enabled and
  started (bridge, worker, email).
- Step 10: open, with step 7.
