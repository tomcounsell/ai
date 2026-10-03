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
