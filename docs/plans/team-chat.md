# Team chat

Tom, 2026-10-16: "a successful teamwork session in a group telegram chat,
where Valor acts properly according to his persona and accomplishes tasks
with Tom and the team. The Eng: Fabrikam group is a great example of how
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
2. **One chat session per chat**, like Hermes Agent (Tom, 2026-10-16):
   each listed chat has one long-lived session with the main agent, and
   every message in it, anyone's, is the next prompt (messages that arrive
   during a turn go in together at the next one). The session replies
   (threaded), reacts, stays quiet, or starts a task with the request as
   written. Its workspace is the project checkout, kept and fetched before
   each turn, with no setup or services, so a reply waits only for the
   model. Compaction is the harness's own (Claude Code's, Pi's, Codex's),
   told by the persona what to keep: who is who, open asks, tasks in
   flight. In a `mentions` chat only a message naming Valor wakes it; the
   rest reach it with the next one.
3. **One voice.** A task the session started reports its questions,
   progress and delivery to the session as a prompt, and the session tells
   the chat; a reply to it reaches the task through the session. Notices
   about spend and grants stay Tom's.
4. **The persona for groups.** `engineer.md`'s buckets and `teammate.md`'s
   tone (short, warm, match the chat, read before replying) go into
   `persona/conduct.md` and `voice.md`.
5. **Media.** Voice notes transcribed and images passed to the turn, as
   `bridge/media.py` did.
6. **The reference.** The Eng: Fabrikam history from the Captain's old
   store, read-only, as the standard a session is compared with.

## Done

The proving ground is Eng: Wingtip Flowers with Rain, on the Pirate (Tom,
2026-10-16), so the Pirate rolls out once steps 1 to 4 are in. Tom has a
teamwork session there on the new kernel; he judges it as good as Eng: Fabrikam or better, recorded with
the date (rule 5). Then the Captain and the Bald roll out.

**Met** (Tom, 2026-10-22): "the bar was met with wingtip group chat
working great yesterday", the session of 2026-10-21 in Eng: Wingtip Flowers
on the Pirate. The Captain and the Bald roll out from 2026-10-22.

**Next: the Agent Builders Chat**, the real test (Tom, 2026-10-16): many
people chatting freely with Valor about his own codebase, in the old
mostly read-only teammate mode. There Valor spoke only when his name
matched; that switch stays for now, and the goal is full human-like
judgement of when to speak and when to stay quiet, made by the
conversation turn alone.
