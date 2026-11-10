# Email CC by project spec

Tom's ask (valor team chat, 2026-11-10 07:31 UTC): a project names one or more of its team's
contacts to copy on all of its email. In a chat app the group's membership does this; by email
it has to be config, because a remembered rule can be missed. Today a client project's chat
session keeps "always CC <the client's lead> on stakeholder emails" as a memory.

**Job.** For Tom and each project's team: every email Valor sends for a project reaches the
people who need to see it, without anyone asking or Valor remembering. Doing nothing: a
stakeholder email goes out as a DM and the lead hears about it second-hand.

## What will be built

1. **The field.** `email_cc = ["a@x", ...]` in `workspace.Spec` (a tuple of addresses,
   lowercased and stripped; each must hold one `@`, else the spec is refused like every other
   malformed field). Documented in `projects/README.md` and shown in `projects/example.toml`.
2. **The send (one mechanism).** In `core/session.py:record`, after `_reply_all` and before
   `broker.request` (the one path all of Valor's email leaves by), every `email.send` of a task whose Brief names a project gets
   `mail.copied(payload, spec_cc, own)`: the spec's addresses appended to `cc`, in spec order,
   skipping any already in `to` or `cc` (case-insensitive) and Valor's own address
   (`settings.email_address`). The spec is read live by name (`workspace.Spec.load`, as
   `intake.room` does), so a long-running chat session picks up an edited list at its next
   send; the `Spec` docstring names `email_cc` as the exception to "read once at start". A
   Brief with no project sends unchanged. A spec that does not load sends unchanged too, and
   its outcome line says `, not copying: <the loader's reason>`, so a missed CC is never silent. Reply-to-all
   goes through the same line, since it runs after `_reply_all` builds its payload. Nothing is
   refused or held: it only adds recipients.
3. **The outcome line.** The effect entry records `copied` (the addresses added, when any), and
   `_effects_report` appends `, copying a@x, b@y` to that send's line, so the session can tell
   its chat who was copied.
4. **The Brief.** `tasks.prompt` adds `Email CC: a@x, b@y (copied on every email.send under this
   project; a draft for someone to send lists them on its Cc line)` to the head of every Brief
   whose project has a list: chat sessions and tasks alike, live from the spec.
5. **The private spec (no commit), only after rollout.** `Spec.from_dict` refuses unknown
   keys and `Spec.all` drops a spec that does not load, and the specs sync to every Mac by
   iCloud: adding `email_cc` while any Mac runs pre-merge code would drop the whole project
   there. So this step runs only once `core update --wait` has finished on all four Macs and
   each kernel is confirmed on the merged head (see Then). The client project Tom named gets `email_cc` = Tom's
   primary operator address and the client lead's address, the one he actually writes from
   (Gmail: his messages to Valor and his CC'd thread with the client, 2026-11-09/10), loaded
   with `Spec.load` and the diff in the delivery.
6. **The memory rule.** The rule was saved by the project's email/chat session as a Claude
   Code memory file in that session's memory dir on the serving Mac
   (`~/valor-tasks/<session task>/state/work/claude/projects/*/memory/`, readable over ssh;
   the index `core.record.memories` renders to its project's other sessions). The build finds
   the file by grepping those dirs for the rule, rewrites it to say the CC is now config
   (`email_cc` in the spec, copied on every send) and updates its `MEMORY.md` line, and names
   the file and session in the delivery. A copy of it in the ledger's memory records, if one
   was ingested, stays as history (records are not edited).

## Tests (`tests/test_record.py` / the existing email-send tests, made-up addresses)

- A send under a project with `email_cc` gets them added to `cc`, in order, and the outcome
  line names them.
- Deduplication: an address already in `to`, or in `cc` in another case, is not added twice;
  Valor's own address in the list is never added; with every address already present, `cc` is
  unchanged and the line names no one.
- A reply-to-all (`reply_to`) under that project gets the list too, after the thread's own cc.
- A project whose spec does not load: the send goes unchanged and its line says
  `not copying: <reason>`.
- A project without `email_cc`, and a Brief with no project, send exactly the payload asked for.
- A non-email effect (`telegram.send_message`) under the same project is untouched.
- The spec loader: the field loads lowercased; a non-address entry is refused.
- The Brief: a project with a list renders the `Email CC:` line; one without renders none.

## Out of scope

- Lists for other projects: they get one when Tom names the people. The delivery lists the
  projects that email stakeholders and have none yet.
- Bcc, per-thread or per-recipient rules, any check on who may be emailed.
- No new check, gate or guard. The address shape check is spec loading, as for every field.

## Then

Full suite, merge, `core update --wait` on the four Macs (each kernel job's launchctl
environment over ssh; the Cowboy via `ssh localhost`, `PATH=/opt/homebrew/bin`, detached), and
confirm each kernel runs the merged head. Only then steps 5 and 6, and a check from a turn
on the serving Mac that the edited spec loads and every chat it lists still binds there. Done when Tom sees a real stakeholder email of that
project go out with the lead copied without anyone asking.
