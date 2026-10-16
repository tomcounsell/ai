# Team chat

Tom, 2026-10-16: "a successful teamwork session in a group telegram chat,
where Valor acts properly according to his persona and accomplishes tasks
with Tom and the team. The Eng: PsyOPTIMAL group is a great example of how
well Valor has been working with the team for several months. The rebuild
has to be as good if not better than that." Until it is, the Captain and
the Bald stay on the old system ([rollout.md](../rollout.md)).

**Where the kernel is.** It hears only Tom: any other sender binds to
nothing (`core/intake.py`, `_bind`). Each message of Tom's that is not a
reply starts an SDLC task; there is no way to answer, chat or stay quiet.
Questions and deliveries go to Tom's DM, so in a group Valor is nearly
silent. Since the cutover it has received 20 messages, all Tom's.

**What the old system did** (`backup/pre-sync-main-2026-10-15`, rule 10):
anyone in a listed group could start or steer work, with no per-sender
rights (`bridge/context.py:build_context_prefix`); a reply chain was one
session (`resolve_root_session_id`); the agent saw the chain with names and
times (`format_reply_chain`) and could read recent history; `engineer.md`
sorted each message into answer, status or work; `Eng:` groups got every
message, other groups mentions only; replies were threaded. It took four
classifiers and a read-the-room call to do it. The rebuild takes the
behaviour, not the classifiers.

## Steps

1. **Hear the team.** In a chat a spec lists, every sender binds as Tom
   does: starts, steers, answers, stops. The task records who
   (`by` is the sender's name, `sender_id` beside it). Tom's DM stays his.
2. **One conversation turn.** A message that does not bind to running
   work gets one short turn: the persona, the reply chain and the chat's
   last 20 messages with names and times, the project's `CLAUDE.md`. It
   answers in the chat (threaded), reacts, stays silent, or starts a task
   with the request as written. It replaces the four classifiers (rule 4).
   Non-`Eng:` groups: only messages naming Valor get the turn.
3. **Work in the open.** A task started in a group asks its questions,
   reports progress and delivers there, threaded to the request; anyone in
   the thread answers or gives feedback. Notices about spend and grants
   stay Tom's.
4. **The persona for groups.** `engineer.md`'s buckets and `teammate.md`'s
   tone (short, warm, match the chat, read before replying) go into
   `persona/conduct.md` and `voice.md`.
5. **Media.** Voice notes transcribed and images passed to the turn, as
   `bridge/media.py` did.
6. **The reference.** The Eng: PsyOPTIMAL history from the Captain's old
   store, read-only, as the standard a session is compared with.

## Done

Tom has a teamwork session in a group Telegram chat, with the team, on the
new kernel; he judges it as good as Eng: PsyOPTIMAL or better, recorded with
the date (rule 5). Then the Captain and the Bald roll out.
