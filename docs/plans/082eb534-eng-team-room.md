# A client's engineers' Slack channel as a mentions-only teammate room

Tom (2026-11-24): Valor was invited to one client's engineering team channel
in the client's own Slack, "to act as a teammate". It joins the spec the way
that client's other Slack team rooms did (plans for 2026-11-03 and
`1d5bf975-team-group.md`). Names, ids and the room text stay in the private
spec under `~/Desktop/Valor/projects/`, not here.

Found at planning (read only, Valor's own token for that workspace, never
printed): the channel exists, is private, Valor is a member (joined
2026-11-24), 17 members. Its purpose and its last 60 messages show what it is
for: daily engineering coordination. The head of engineering posts new Linear
tickets to named engineers; backend, app and design engineers post their
fixes and pull requests (the backend and mobile-app repositories) for review;
rule calls get settled in threads. Traffic is high, so mentions-only matters.

## What is built

1. **Config only, in the private spec.** The channel added to `chats` and to
   `mentions`; `chat_roles` = `"teammate"`; a dated comment quoting Tom; a
   `rooms` line in the pattern of the client's other team rooms: what the
   room is for (from the reading above), who is in it by role, "Answer
   whoever mentions you or replies to you; an untagged message meant for you
   reaches you too.", that engineering work asked of Valor there starts as a
   task from the project's Eng chat (its Slack dev channel in the Yudame
   Slack, or its Eng Telegram group), and the product backlog Sheet kept in
   the product room.
2. **Only these lines.** Another task (9f4967d9bc1c) edits the same file. The
   edit is made with exact-string insertions on the current file right before
   saving (no rewrite from an earlier copy), and the diff against a backup
   taken just before shows only the added lines. `Spec.load` on the path.
3. **Taking effect on the Captain**, where the project runs (this plan is
   written on another Mac): over SSH, confirm iCloud has synced the file
   (same content hash as here), that `Spec.load` there parses it, and that the
   kernel lists the channel as owned by the project with the teammate role and
   the mentions filter. `Spec.all()` is reread per call, so no restart is
   expected; if the Slack bridge does not take it, restart the bridge (then
   the kernel) only once no turn or call is running there, as the update
   skill does.
4. **Received, read-only.** From the Captain's ledger or bridge log, a real
   message in the channel received after the change (the channel is busy, so
   one should come within hours). Nothing is posted there and no one at the
   client is messaged; if none arrives before the build ends, the delivery
   says so.

## Out of scope

No code change, check, gate or new mechanism; no change to other chats'
roles or rooms; no message in the client's Slack.

## Stakes, loops

A reversible config change in a private file; the worst case is a busy room
answered wrongly or not at all, undone by removing the lines. Critique 0,
review 0.

## Tests

None added unless a defect is found; then a test on the failing path with
made-up ids. Live boundary checks on the Captain: every spec still loads; the
channel is owned with role teammate and is in `mentions` (an unmentioned
message wakes no turn); the other task's lines are intact; the spec's other
chats keep their roles and rooms (the diff shows only additions).
