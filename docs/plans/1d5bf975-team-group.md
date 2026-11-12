# A client's mentions-only Telegram team group, in the teammate role

Tom (2026-11-12): a new Telegram team group for one client is to be served
from the Mac that runs that client, as a teammate. Team chats wait to be
mentioned (Tom, 2026-10-17), mirroring that Mac's other client team group.
The group is a **basic group** (`-<id>`, no `-100`); basic-group ids already
work end to end (plan `product-support-group.md`, live 2026-11-04). Names
and ids stay in the private spec, not here.

## What is built

1. **Config only, in the private folder.** In the client's spec under
   `~/Desktop/Valor/projects/`: `telegram:-<id>` added to `chats` and to
   `mentions`; `chat_roles` = `"teammate"`; a `rooms` line in the style of the
   spec's other team rooms: the client's team group on Telegram, answer
   whoever mentions you, engineering requests go to the client's Eng Telegram
   group or its Slack dev channel, and the product backlog Sheet kept in the
   product room. Load with `Spec.load` on its path; diff and result go in the
   delivery.
2. **No code change.** Nothing new is exercised: a basic group, a teammate
   role and `mentions` are all live on the same Mac today.
3. **Taking effect.** `Spec.all()` is reread on each call. The build checks
   on the Captain that iCloud has synced the file, that the bridge's `owns`
   answers yes for the chat, and restarts the Telegram bridge (then the
   kernel) only if it does not, as the update skill does.
4. **Live confirmation.** From the Captain's ledger: the chat is owned by the
   client's project with the teammate role, and a message there mentioning
   Valor is recorded as received. Valor does not post in the group to test
   it; it waits for a real mention, or reports that none has come yet.

## Out of scope

No new mechanism, check or gate; no change to other chats' roles or rooms;
no posting in the group beyond answering a mention.

## Tests

None added unless a defect is found; then a test on the failing path with a
made-up id. Live boundary checks: a message without a mention wakes no turn;
a mention gets a turn with the teammate role and the room line; the spec's
other chats keep their roles and rooms (the diff shows only additions).
