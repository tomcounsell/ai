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
