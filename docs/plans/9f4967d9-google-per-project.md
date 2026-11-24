# A project can work as its own Google account, beside valor@yuda.me

**Request.** On 2026-11-24 Tom said a client gave Valor a Google Workspace account in its own domain.
The client's projects use that account for mail, calendar, Sheets, Drive and Meet. Every other project
keeps valor@yuda.me unchanged. The answers settle the rest:

- An invited call joins as the address that was invited.
- The hours calendar and call recordings stay on valor@yuda.me.
- Valor makes the OAuth client itself, and stops with written clicks if blocked.
- Spec fields stay in the private Valor folder.

Tom, 08:41 UTC, set two more:

- The client's email is the new account's, received and sent.
- The client's Sheets owned by valor@yuda.me end up owned in the client's domain.

**Today.** One account is used everywhere:

- gws reads one `~/.config/gws`;
- there is one `settings.meet_profile`;
- `bridges/meet/google.py` is fixed to "Google valor@yuda.me";
- `invites` lists one calendar;
- the email bridge has one `Config` (`settings.email_address`).

**Build.**
1. **`Spec.google`** (`core/workspace.py`): an account address with exactly one `@`, parsed by the loader.
   Absent means own (`persona.identity` email).
2. **`Account(email)`** (`bridges/meet/google.py`) is where every path and name comes from. Each value
   below is own's, then another account's:
   - `config_dir`: `~/.config/gws`, or `~/.config/gws-<email>`, expanded to an absolute path;
   - `env`: `{}`, or `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` set to `config_dir`;
   - `profile`: `meet_profile`, or `<meet_profile>-<email>`;
   - `item`: `Google <email>` in `m-valor`;
   - `of(project)`: own when there is no project, no field, or a spec that won't load (`workspace.Refused`);
   - `ready()`: true when `call.account(profile) == email`, so a profile signed in as the other address is not ready.

   `secret`, `_answer` and `renew` take the account.
3. **Turns.** `_client_env` adds `Account.of(project).env`. The Brief for such a project says:
   - gws is that account here;
   - Valor's own is `GOOGLE_WORKSPACE_CLI_CONFIG_DIR=<own dir> gws ...`;
   - Slides go through own;
   - the inbox there is the client's, and mail leaves only as `email.send`.

   Other projects get exactly today's env and Brief.
4. **meet.join** (`tools/meet.py`):
   - `usage` and `refuse` take `account` (a string or null).
   - The account is `account`, else the project's.
   - `_open` checks `ready()` or the item, and a failure names `meet-sign-in --account`.
   - The account's env applies to `create_space`, the call marker, `Page(account.profile)`, `Call.joined`'s renewal and message, and the Sheet read.
   - `hours` is unchanged.
5. **invites** (`routines/invites/runner.py`):
   - **Accounts:** own, plus each `google` placed on this Mac.
   - **Gather first:** every account is listed first. A list that fails writes `calendar.unlisted`, and the others go on.
   - **Group by iCalUID:** the account chosen is the project's when it was invited, else another invited one. Then one wake per `invite:<iCalUID>:<start>` names "Join as".
   - **Matching:** `_own` holds every account and Tom's addresses. A client's domain still matches.
   - **Accepting:** goes through the account that listed the event. Its key carries a non-own account, so old rows still match.
   - **Calls:** `converse.md` **Calls** passes the wake's "Join as" as `account`.
   - **Timing:** rollout falls outside any call window, since old `invite:<id>` rows don't dedupe.
6. **Email**, extending the one bridge:
   - **Watch:** `Config` per mailbox: own, plus each placed account whose keys are in `mail-keys` (`IMAP_USER.<email>` and its siblings). Each runs its own `imap.watch`, so one mailbox failing leaves the others running.
   - **Inbound:** carries `mailbox`. `takes_email` and `intake._project_for` send mail arriving in an account's mailbox to that account's project, ahead of the sender rule. None of Valor's addresses is ever received.
   - **Outbound:** the session's `_copy` stamps `payload.from` with the project's account, replacing any value the turn gave. `reply_all`, `copied` and `email_cc` use that address. The bridge sends with that `Config`. One with no mailbox here fails, naming `python -m bridges.email keys`, and never falls back to own.
   - **Keys:** `keys` reports each account's keys.
7. **`meet-sign-in [--account EMAIL]`.** It counts as success only when `account(profile)` equals that email. The weekly notice is per account, keyed `meet-profile:<machine>[:<email>]:<week>`, with own's key unchanged.
8. **`sheet.py`**: every Drive call passes `supportsAllDrives=true`, so a Sheet in a shared drive works.
9. **Docs**, naming no client: `machine.md`, `rollout.md` step 6, `bridges/meet.md`, `bridges/email.md`, and the `performers.md` rows.

**Rollout (the Captain), in order:**
1. Rename the 1Password item to `Google <email>`, then run `meet-sign-in --account`. No spec change yet.
2. Sign gws in:
   - check `gws auth status` first;
   - with `KEYRING_BACKEND=file`, as the new account in byob Chrome, create a Cloud project with an Internal Desktop client, and save its JSON to `m-valor`;
   - log in with the scopes `calendar`, `spreadsheets`, `drive`, `documents` (the client's review Docs), `gmail.readonly`, `meetings.space.created`, `meetings.space.readonly`, `openid` and `userinfo.email`;
   - export to a plain `credentials.json`, then read own's calendar.

   If blocked: stop and send admin clicks written for someone other than Tom.
3. Mail: as the new account, make a Gmail app password and type it straight into `mail-keys` (never echoed). If app passwords or IMAP are disabled, stop with admin clicks. Then read the inbox.
4. Report to the chat session the new calendar's unanswered Meet invites for the next 14 days, before anything accepts them.
5. List the client's Sheets owned by valor@yuda.me: today the product backlog (with its folder and companion Doc), and a read-only copy of the coach prompts. Name any I'm unsure of.

   Move each into a shared drive the new account makes in the client's domain, with valor@yuda.me added as a manager. This keeps the id, link, history and permissions. If either domain's setting blocks it, ask Tom with the admin clicks.

   Otherwise copy each as the new account, re-share it with no notification, update every place the old link is named (the spec's room lines, docs), and put a first-row note on the old Sheet pointing to the new one. Prove each with a `sheet.py` read.
6. After the session has told Tom of step 4's list, add `google` to both of the client's specs, plus `rooms.email` and `chat_roles.email = "teammate"` (as for other client projects).

**Out of scope.**
- Mail from client people to valor@yuda.me: routing stays as it is.
- The client's Docs, which are not Sheets (listed for Tom).
- Recordings, the hours calendar, and the event Valor organizes as own.
- Two calls at once.

No check or gate is added. `ready()`, `calendar.unlisted`, per-mailbox watches and the `from` stamp correct existing code that assumed one account.

**Tests** (fakes, made-up names):
- **Spec and `Account`:**
  - `of` falls back to own for a refused spec;
  - the directory is absolute;
  - `ready()` is false for the other address.
- **Turns:** a project with the field gets the env and the Brief lines. Without it, or with no project, everything is as today.
- **invites:**
  - a second list that fails still accepts and wakes own's, with `calendar.unlisted`;
  - one iCalUID on both calendars with own listed first wakes once, naming the project's account;
  - one iCalUID under two ids, across two runs where run one failed, wakes once;
  - the second address does not match, but its domain does;
  - accepting goes through the listing account;
  - old accepted rows still suppress;
  - an own-only Mac is unchanged.
- **meet.join:**
  - `account` is a string or null;
  - payload beats project beats own;
  - a client chat's join with `account` set to own opens own's profile;
  - the env reaches `create_space` and the Sheet read;
  - a profile signed in as the other address fails, naming `--account`.
- **Email:**
  - mail in the client mailbox reaches the client project whoever sent it;
  - mail in own's routes as today;
  - a client send is stamped from the new address and uses that `Config`, CC exclusion included;
  - a turn's own `from` is replaced;
  - a non-client send is unchanged;
  - a send whose mailbox is missing fails without falling back;
  - one mailbox's watch failing leaves the other running.
- **Sign-in, renewal and notice:** each uses its account. Nothing changes without the item. The notice keys are per account, own's unchanged.
- **`sheet.py`:** Drive calls carry `supportsAllDrives`.

Then the full suite.

**Live proof, the Captain (done):**
- **Calendar:** a read as each account, a plain gws read.
- **Meet:** the test Meets are asked for by Valor's own engineering room's session (Tom and his team only). One as `new` with `account` set to the new address, one with own. Each is joined on the same Mac.
- **Inbox:** a read as the new account.
- **Email:** one `email.send` under the client project to valor@yuda.me, said to the session first.
- **Sheets:** a `sheet.py` read of each moved Sheet.

No real invite is answered and nothing reaches the client's people. Done means Tom uses it on a real call (rule 5).
