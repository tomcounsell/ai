# A second mentions-only Telegram team group for one client, as a teammate

Tom (2026-11-18): a new Telegram basic group for one client's company-website
operations is to be served from the Mac that runs that client, in the teammate
role. It follows `1d5bf975-team-group.md` exactly (live 2026-11-12); team
chats wait to be mentioned (Tom, 2026-10-17). Names and ids stay in the
private spec and the people records, not here.

## What is built

1. **Config only, in the private folder.** In the client's spec under
   `~/Desktop/Valor/projects/`: `telegram:-<id>` added to `chats` and to
   `mentions`; `chat_roles` = `"teammate"`; a provenance comment quoting Tom;
   a `rooms` line in the style of the earlier team group: the group for the
   client's company website operations, who is in it (Tom, his marketing
   engineer, and the client's growth-marketing lead), answer whoever mentions
   you, engineering requests go to the client's Eng Telegram group or its
   Slack dev channel, and the backlog Sheet kept in the product room.
   Verified with `Spec.load` on the path; other chats' entries unchanged.
2. **People.** Look up each of the three with `valor-record person`; record
   the client-side person's role and organisation from Tom's intro as dated
   facts (external), and the marketing engineer's role if missing.
3. **No code change.** Basic groups, the teammate role and `mentions` are live
   on that Mac today.
4. **Taking effect.** On the Captain: confirm iCloud synced the file and the
   Telegram bridge's `owns` says yes for the chat (it starts tracking it);
   restart the bridge, then the kernel, only if it does not.
5. **Confirmation and backlog.** From the Captain's ledger, the chat is owned
   by the client's project with the teammate role. Read the group's history
   (`valor-telegram read`) for any message naming Valor before it was
   connected; report each one as unanswered, not answered after the fact.

## Out of scope

No posting in the group; no new mechanism, check or gate; no change to other
chats' roles or rooms; no client name, id or person in this repository.

## Tests

None added unless a defect is found; then a fix with a test on a made-up id.
Boundary checks: `Spec.load` shows the id in `chats` and `mentions` with role
`teammate`; the diff of the spec shows only additions; no other spec claims
the id; ownership confirmed on the Captain, not on this Mac.
