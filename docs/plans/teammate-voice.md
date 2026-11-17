# Teammate voice: short and casual in team chats; a long answer as a made thing

Tom, valor team chat, 2026-11-17 05:36 UTC: the teammate chat "needs its
tone of voice edited to be more succinct and less verbose ... in general
chitchat in a Team short messages are best in the same casual style that
other people chat. your anthropic models tend to be extra punchy and
Information-dense, which is the opposite of the casual down to earth that
people prefer"; long answers to simple questions as "a shorter answer with
an attached PDF". 05:52: a PDF made with the present skill, "expected that
you use that medium well by incorporating more than just paragraphs of
text". 05:56: the medium is Valor's call: a tech comparison may merit "a
full video presentation", a quick explainer stays "nearly as simple as the
ones you already send (typically 2-4 lists of 2-4 bullet points and a final
recommendation)". Intake (when Valor speaks) is task 3138d3b5f818, untouched.

Done when Tom reads Valor in a team chat and its messages are about as
long as everyone else's.

## Measured (observed, 2026-11-17, words per message, Valor vs the people)

| Room (role) | Valor median / p75 / >100 words | People median / p75 / >100 |
|---|---|---|
| Agent Builders, 599 msgs Aug-Nov (teammate) | 183 / 277 / 77% | 10 / 22 / 3% |
| Outside build-comp group (teammate) | 36 / 94 / 20% | 7 / 15 / 0% |
| A teammate's DM (teammate) | 53 / 196 / 40% | 8 / 13 / 2% |
| Eng: Valor, Telegram (engineer) | 79 / 138 / 37% | 20 / 38 / 3% |
| #valor-dev (engineer) | 146 / 245 / 70% | 21 / 60 / 15% |
| #valor-product (product) | 8 / 14 / 17% | 9 / 32 / 0% |
| Tom's DMs, Telegram / Slack (Tom's own) | 108 / 195 / 56%; 108 / 206 / 59% | 19 / 39 / 5%; 19 / 31 / 0% |

Engineer rooms and Tom's DMs show the same gap; they are left as they are
(the brief) and reported to Tom. Six Agent Builders picks (ids, lengths,
what good looks like) are in `~/Desktop/Valor/tone-b4c0d2aa/picks.md`,
private: a shared build, banter, a one-line claim, a correction, a "what
does this mean", and a "compare thoroughly". One reader there asked for a
cartoon because a reply was "a lot to stomach" (observed).

## What is built (wording only; no check, limit, gate or validator)

1. `skills/sdlc/roles/teammate.md`, a new "How you sound here" section,
   the one home of the team-chat voice: write like the people around you;
   about their length (look at the last few messages); casual, plain,
   lowercase is fine where they write so; one thought per message; no
   bullets, headers, bold labels or summaries in chitchat; not punchy or
   dense for its own sake; banter gets banter, a correction gets "you're
   right", a shared build gets a warm line and one real point. A hard
   question still gets care: a few lines in the chat with the answer, and
   when the full answer is longer than the chat's own messages, the
   detail as a made thing (pointer to the persona, item 2). Two invented
   before/after pairs (no chat text, no names): a one-line claim, a
   "what does this mean" link.
2. `persona/delivery.md`, "What you make for people": one paragraph for a
   team chat, kept here because the task that makes the file reads the
   persona, not the room's role: the bar is "longer than the chat's own
   messages", not 600 words; it is made with the present skill and uses
   the medium (diagrams, tables, images where they carry the point, not
   paragraphs on a page); the form is Valor's call fitted to the ask, from
   a quick explainer (2-4 short lists and a recommendation) to a deck or
   narrated video for something like a tech comparison, unasked. The
   "deck or video only when someone asks" line names this exception.
   The ordinary-document rule stays for every other room.
3. `persona/voice.md` "In a chat, a teammate": unchanged in force; gains
   only "(a team chat: your role's 'How you sound here')", so the detail
   has one home. `skills/sdlc/converse.md` lines 26-27 and 405-407 (when a
   task makes a document or deck) point to item 2 for a team chat, and a
   task started from a team chat says so in its context.
4. `docs/persona.md` "What Valor makes for people": Tom's three
   2026-11-17 messages and what they change.

Out of scope: intake; engineer, product and Tom's rooms; any length cap.

## Tests and trial

- `tests/test_kernel.py`, beside the persona test: the teammate role text
  names matching the chat's length, no bullets or headers in chitchat,
  and the made thing past the chat's own length; the rendered persona
  carries the team-chat bar, the present skill, the medium used well and
  Valor choosing the form; `voice.md` points to the role, not a copy.
  Existing `test_act` (teammate.md names `valor-record act`) still holds.
- Trial (scratch, nothing posted): for each of the six picks, render a
  teammate chat prompt (persona + converse + teammate role + the room
  line) from the old commit and the new, give it the ask and the 15
  messages before it, run `claude -p` with no send tools, and count words.
  Pass: the five chitchat/simple picks land near the people's median
  (under ~40 words); the comparison gives a few lines and names a made
  thing. Before/after counts go in the report; outputs stay private.
- Full suite under 5 minutes.

Rollout: merge, then the update skill on the four Macs; persona and role
text are read when a turn renders; the builder confirms that against the update skill, so no running turn or call is restarted.
