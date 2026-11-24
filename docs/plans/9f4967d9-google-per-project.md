# A project can work as its own Google account, beside valor@yuda.me

**Request.** Tom, 2026-11-24 08:29 UTC (valor team chat, 1795508956.681389): a client's projects use the
Google Workspace account it gave Valor for mail, calendar, Sheets, Drive and Meet. Every other project keeps
valor@yuda.me unchanged. The chat session's answers settled the defaults. An invited call joins as the
invited address. The hours calendar and recordings stay on valor@yuda.me. Valor makes the OAuth client and
stops with admin clicks if blocked. Spec fields stay private. Tom's reply at 08:41 UTC (1795509683.748349),
passed on as a steer, **supersedes** two answers ("no bridge change", "no copy"): the client's email is
received and sent as the new account, and the client's Sheets owned by valor@yuda.me end up in its domain.

**Build.**
1. **`Spec.google`**: an address with exactly one `@`; absent means own. **`Account(email)`**
   (`bridges/meet/google.py`) derives each value, own first: `config_dir` (`~/.config/gws`, or
   `~/.config/gws-<email>`, absolute); `env` (`{}`, or `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`); `profile`; `item`
   `Google <email>`. `of(project)` gives own for no field or a refused spec. `ready()` is
   `call.account(profile) == email`. `secret`, `_answer` and `renew` take the account.
2. **Turns:** `_client_env` adds the env. The Brief says gws is that account, how to reach own's, and that
   Slides go through own. Other projects get today's env and Brief.
3. **meet.join:** `account` (string or null) in `usage` and `refuse`; payload, then project, then own.
   `_open` checks `ready()` or the item and names `meet-sign-in --account`. The env reaches `create_space`,
   the call marker, `Page(profile)`, `Call.joined`'s renewal and message, and the Sheet read.
4. **invites:** own plus each placed `google`, all listed first. A failed list is `calendar.unlisted` and
   the rest go on. Grouped by iCalUID, the account is the project's when invited. One wake per
   `invite:<iCalUID>:<start>` names "Join as", which converse.md **Calls** passes as `account`. `_own` holds
   every account. Accepts go through the listing account, keyed with it when not own.
5. **Email, one bridge:** a `Config` per mailbox: own, plus each placed account whose keys are in
   `mail-keys`. Each has its own `since`: `EMAIL_SINCE.<email>`, set at setup to the day its watch starts.
   Each has its own `imap.watch`. Mail in an account's mailbox goes to that account's project. Invite and
   Drive notices from `calendar-notification@google.com` and `drive-shares-dm-noreply@google.com` are not
   received there, since `invites` handles invites. `_copy` stamps `payload.from` with the project's
   account, which `reply_all`, `copied` and `email_cc` use. A send whose mailbox isn't here fails, naming
   `python -m bridges.email keys`, and never falls back.
6. **`meet-sign-in --account`**, and the weekly notice keyed `meet-profile:<machine>[:<email>]:<week>`
   (own's key unchanged). **`sheet.py`** passes `supportsAllDrives=true`. **Docs** name no client.

**Rollout (the Captain), in order.** Steps 1–4 change no spec.
1. Rename the item, then run `meet-sign-in --account`.
2. The Captain already has the client's OAuth client (made by hand 2026-11-24 in a hand-named gws folder),
   kept: its folder becomes `~/.config/gws-<email>` (the old name a link to it). With the file keyring
   backend, log in again through that client with only `calendar`, `spreadsheets`, `drive`, `documents`,
   `gmail.readonly`, `meetings.space.*`, `openid` and `userinfo.email` (the hand login has `gmail.modify`,
   `cloud-platform` and `pubsub`, and no Meet scope), export to a plain `credentials.json`, remove
   `credentials.enc`, revoke the old token, then read own's calendar.
3. Make an app password, typed into `mail-keys`, and set the mailbox's `since` to today. If blocked, stop
   with clicks.
4. Report to the chat session, by sender and subject only: the unanswered Meet invites for the next 14
   days, and the inbox's existing mail, which no watch will receive.
5. Sheets: the backlog (with its folder and Doc) first. The new account makes a shared drive with
   valor@yuda.me as manager, and the backlog moves into it, proved with a `sheet.py` read. Then the coach
   prompts copy. If the move is refused: copy as the new account, re-share with no notice, and put a note
   on the old first row. Any link change in the spec and docs is listed to the chat session; nothing is
   posted in the client's chats.
6. Once Tom has heard step 4: `google`, `rooms.email` and `chat_roles.email` go into both client specs.

**Out of scope.** Client mail sent to valor@yuda.me; the client's Docs (listed for Tom); recordings; hours;
two calls at once. No check or gate is added: `ready()`, `calendar.unlisted`, per-mailbox watches and the
`from` stamp correct code that assumed one account.

**Tests** (fakes, made-up names):
- **Account:** `of` falls back to own; `ready()` is false for the other address.
- **Turns:** a project with the field gets the env and Brief lines; without it, or with no project, as today.
- **invites:**
  - a second list failing still wakes own's;
  - one iCalUID on both calendars, own listed first, wakes once as the project's account;
  - two ids across two runs, one failed, wake once;
  - the second address does not match;
  - old accepted rows still suppress.
- **meet.join:** `account` type; precedence; own's profile from a client chat; a wrong-address profile fails
  naming `--account`.
- **Email:**
  - client mailbox mail reaches the client project, own's routes as today;
  - mail before the mailbox's `since` is not received;
  - Google notices are not received;
  - a client send is stamped and uses its `Config`, replacing a turn's `from`;
  - a missing mailbox fails;
  - one watch failing leaves the other running.
- **Notice and `sheet.py`:** notice keys per account; `supportsAllDrives`. Then the full suite.

**Live proof, the Captain.**
- A plain gws calendar read as each account.
- Two test Meets asked for by Valor's own engineering room (Tom and his team only): `new` with `account`
  set to the new address, and own, joined on the same Mac.
- A read of the new inbox.
- One `email.send` under the client project to valor@yuda.me, said to the session first.
- `sheet.py` reads of the moved Sheets.

No real invite is answered and nothing reaches the client's people. Done means Tom uses it on a real call.
