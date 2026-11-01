# One voice, mail: Valor's Google token can read mail but not send it

**Why.** `one-voice.md` left one way for a task or routine to email people as Valor: `gws gmail` with Valor's Google token, which every turn holds (`~/.config/gws/credentials.json`, signed in with `gmail.modify`, which lets Gmail send). The yudame:email skill sends that way. The critique of task b2be54bdbee0 (record 76665.0, finding 3) named it, and the delivery listed it as still open.

**Grant (capability removal, rule 3).** The same grant as `one-voice.md`: Tom, Slack DM, 2026-11-01, 04:22-04:30 UTC ("No routine or system should be impersonating you." "How can I trust anything coming from you if something else might be able to speak for you without your permission." "How will we prevent new features from impersonating you?"), his :+1: at 04:34 UTC on the design post, and his "Agreed" at 05:37 UTC to my post "I'd close email the same way, so only I send it. I'd leave GitHub comments alone ... Should I close email?" Instance: Valor's Google token loses Gmail's send-capable scope. Incident: the 2026-10-31 Sentry report in `one-voice.md`, and the gws route that plan left open. Mission item 6. Ledgered with a ninety-day expiry. GitHub comments by tasks stay.

**The rule.** Email under Valor's name leaves only as a chat session's `email.send` (kernel SMTP, whose app password is in the kernel key directory every turn's sandbox profile denies). That is one-voice's existing mechanism; this change takes away the second one.

**Approach: re-scope the one token, no new layer.** After one-voice nothing legitimate sends through gws: sessions send by `email.send`, and the invoices run reads Sent mail with `messages list/get`. So the token every machine copies is signed in again with `gmail.readonly` in place of `gmail.modify`. Every other scope stays (Calendar, Drive, Docs, Meet for `meet.join`, ...). No second token, no sandbox rule, no hook, no env var. Considered and dropped: a deny on `~/.config/gws` plus a separate read token for turns. It adds a profile rule and a second credential, and a determined turn gets around it the same ways as this design (below).

## What is built

1. **The live token.** The build signs Valor out first, revoking the grant at Google (`gws auth logout`), so the new sign-in cannot carry `gmail.modify` over. It deletes the token cache, signs in on the Cowboy with rollout step 6's scope list minus `gmail.modify`, plus `gmail.readonly`, approving through Valor's byob browser as step 6 says, and copies the file over SSH to every Mac it can reach. Each Mac it cannot reach is named in the delivery; its gws fails closed (auth error) until step 6's copy runs there. Recovery from a failed sign-in is step 6 again.
2. **Docs that say so.** Rollout step 6's scope list; `machine.md` (Google sign-in) and `performers.md` (the Google Workspace row: Gmail reads, not drafts or sends); `docs/routines.md` and the mercury skill say the run reads Sent mail with a read-only token.
3. **Words for turns.** In `skills/sdlc/channel.md`, "this task sends no message" now names email by any tool (gws, a connector, Valor's browser): a task that needs an email sent puts the `email.send` in its report with the attachment in its workspace, and the session sends it with the file (one-voice's reporting-task attach). `converse.md` says the session's mail goes only by `email.send`, and gws there only reads, so a session that hits gws's 403 does not fall back to the browser. That fallback is the yudame:email skill's order, and that skill lives in another repo.
4. **Ledger.** A seeded guard `mail.read_only_token` in `core/guards.py` with the incident, mission item 6, granted 2026-11-01, expiring 2027-01-30, `via` this file, and `firing_record` false, since nothing in the code fires.

## Not in scope, named for Tom

- Two routes stay open to a turn that sets out to use them. It can send from Gmail's web page in Valor's logged-in browser. It can sign Valor in again with `gmail.modify`, because the browser approves Google sign-ins (lifted 2026-10-17). Both are the same-user exposure Tom accepted on 2026-10-01. Closing them means taking the browser from tasks.
- Mail Google sends on Valor's behalf (Calendar invites, Drive share notices) and Google Chat messages (`chat.messages` scope) stay. I'd close Chat next, since nothing uses it. It needs its own grant.
- No Gmail drafts or label changes for anyone (`gmail.readonly`). A draft is text in a report.
- The yudame:email skill (yudame/skills) still lists gws for sending. Changing it belongs to that repo.

## Tests (incident first)

- **Invoices end to end.** The invoices run drafts a reminder with its PDF (fake Mercury, `op`, read-only gws), and the woken session's `email.send` naming that PDF goes through the email performer to a fake SMTP server. The message received has the client's address and the PDF's bytes as an attachment. The run's gws calls are only `messages list/get`.
- **A task turn cannot send mail as Valor.** An SDLC task's and a routine run's performers offer no `email.send`, and requesting one is refused as unknown. A session's performers offer it. The kernel's `mail-keys` lies under a path the turn profile denies. Rollout step 6's documented scope list holds none of `mail.google.com/`, `gmail.modify`, `gmail.compose`, `gmail.send`.
- **Live, skipped off a signed-in Mac (like `test_live_meet`).** `gws auth status` lists none of those scopes. `gws gmail users messages send` to valor@yuda.me is refused 403, and `messages list` works.
- **Ledger.** `mail.read_only_token`'s row holds its dates, mission item 6, "Agreed" in its note, and `via`. Suite under 5 minutes; `test_bloat` green.

Stakes: Valor's live Google sign-in on every Mac, so 1 critique round and 1 review round. Governance: the scope removal above, under Tom's grant; nothing else.
