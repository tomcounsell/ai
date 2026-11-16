# A client's second product room, for its admin tools

Tom (2026-11-16): one client's Slack channel for its admin web tools is to be
served by Valor as product manager, "just like" that client's existing
product room, but for improving the admin tools and helping people use them.
Product rooms already work end to end on the client's Mac (plan
`product-role.md`); this is config. Names and ids stay in the private spec.

## What is built

1. **Config only, in the private folder.** In the client's spec under
   `~/Desktop/Valor/projects/`: the channel added to `chats`, not to
   `mentions` (a product room answers every message); `chat_roles` =
   `"product"`; a comment line citing Tom's words and date, as the spec's
   other entries do.
2. **Its `rooms` line**, in the form of the existing product room's: the
   product room for the client's admin tools at their web address, with two
   jobs: improving them (ideas, problems and requests become backlog items,
   rated, ranked and passed to engineering when ready) and helping people use
   them (answer how-to questions from the tools' docs and code in the client's
   repo; a question that shows a gap becomes a backlog item). It names the
   tools briefly from the client repo's own docs (checked there at build, not
   copied from the spec's other lines), the same backlog Sheet (one per
   project) and the same dev chat.
3. **The existing product room's line** stays accurate beside it: the room
   for the coaching app and overall strategy, with one phrase that admin-tools
   items are worked in the new room, so the two don't both claim them. The
   build reads the Sheet (read only) for an area or component column and says
   in the delivery how admin-tools items are told apart; the Sheet is not
   restructured.
4. **Load and take effect.** `Spec.load` on the path; the diff and result go in
   the delivery, with a backup of the file before the edit. On the client's
   Mac: the synced file matches (size and mtime), the Slack bridge's ownership
   answers yes for the channel with the product role, and the room line is
   what a turn there would get. `Spec.all()` is reread per call, so no restart
   is expected; if the bridge does not take it, restart the Slack bridge (then
   the kernel) only once no turn or call is running there.
5. **Live confirmation.** From that Mac's ledger, a real message in the
   channel recorded as received and answered in the product role. Valor does
   not post there to test it; if no one posts before the build ends, the
   delivery says so.

## Out of scope

No code change, new mechanism, check or gate; no change to other chats'
roles; no edit to the Sheet; no message in the client's Slack.

## Tests

None added unless a defect is found; then a test on the failing path with
made-up ids. Boundary checks, live: all specs still load; the new channel is
owned by the client's project with role product and no mentions filter; the
existing product room keeps its role and its line differs only by the one
phrase; the spec's other chats are unchanged (the diff shows only that).
