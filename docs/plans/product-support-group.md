# A client's product-support Telegram group, in the product role

Tom (2026-11-04): a new Telegram group for one client's product support is to
be served from the Mac that runs that client, in the product role, answering
every message. The group is a **basic group**: its marked id is `-<id>`, not
`-100<id>`. Names and ids stay in the private spec, not here.

## What is built

1. **Config only, in the private folder.** In the client's spec under
   `~/Desktop/Valor/projects/`: the group's `telegram:-<id>` in `chats`;
   `chat_roles` = `"product"`; not in `mentions`; a `rooms` line modelled on
   the client's Slack product room (product for the client, the same backlog
   Sheet, its dev chat named as the Eng Telegram group). Load it with
   `Spec.load` on its path; diff and result go in the delivery.
2. **No code change, on the evidence read in this stage.** Every Telegram path
   treats a chat id as an opaque marked id handed to Telethon:
   - owns/owned (`core/intake.py`): string compare against `chats`; the
     channel's pattern is `.+`, case kept.
   - inbound: `utils.get_peer_id` gives `-<id>` for a `PeerChat`, the same
     string the spec lists.
   - gap fill (`bridges/telegram/bridge.py` `fill`): `int(chat)` then
     `history`; works on any marked id.
   - sends and reactions (`wire.py`): `get_input_entity(int)`; Telethon
     1.45 `resolve_id(-<id>)` gives `PeerChat`, and `InputPeerChat` needs no
     access hash, so no cache miss.
   Nothing branches on `-100`. If the live check (below) shows a failure, the
   fix is a commit with a test using a made-up basic-group id, no plan change.
3. **Taking effect.** `Spec.all()` is read on each call, so the kernel and
   bridge should see the new chat without a restart. The build still checks on
   the Captain that iCloud has synced the file and, if the bridge does not
   receive, restarts the Telegram bridge (then the kernel) the way the update
   skill does.
4. **Live confirmation.** From the Captain's ledger: a message in the group is
   recorded as received and Valor's reply in the group is recorded as sent.
   Valor does not post in the group to test it; it waits for a real message
   (Tom's, or one already there that gap fill takes) and reports what the
   ledger shows.

## Out of scope

No new mechanism, check or gate; no change to other chats' roles or rooms; no
posting in the group beyond answering what arrives.

## Tests

None added unless a defect is found. If one is: a test on the failing path
with a made-up basic-group id (owns, gap fill, send or react), plus the
existing suite. The live boundary checks: a message with no mention gets an
answer (not mentions-only); the turn renders with the product role and the
room line; the client's other chats keep their roles and rooms (diff shows
only additions).
