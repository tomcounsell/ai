# A project can work as its own Google account, beside valor@yuda.me

**Request.** Tom, 2026-11-24: a client gave Valor a Google Workspace account in its own domain. That
client's projects (PM and eng) use it for mail reads, calendar, Sheets, Drive and Meet. Every other project
keeps valor@yuda.me unchanged. Answers (the chat session, on the stated defaults): an invited call joins as
the address invited. The hours calendar, the email bridge and call recordings stay on valor@yuda.me. The
client's product Sheet stays where it is, its folder shared to the new account with no notification. Valor
makes the OAuth client itself in the client's Workspace, and stops with written clicks if blocked. The spec
fields live in the private Valor folder.

**Today.** One account everywhere: gws's single `~/.config/gws` (turns, `invites`, `hours`, `create_space`,
the call's Sheet read), one `settings.meet_profile`, `bridges/meet/google.py` fixed to "Google valor@yuda.me",
and `invites` listing one primary calendar, with one address in `_own`.

**Build.**
1. **`Spec.google`** (`core/workspace.py`): an optional account address, refused unless it holds exactly one
   `@` (the loader's existing parse pattern). Absent means Valor's own (`persona.identity` email).
2. **`Account(email)`** in `bridges/meet/google.py`, the one place paths are derived:
   - `config_dir`: `~/.config/gws` for own, else `~/.config/gws-<email>`;
   - `env`: `{}` for own, else `{"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": config_dir}`;
   - `profile`: `settings.meet_profile` for own, else `<meet_profile>-<email>`;
   - `item`: `"Google <email>"` in `m-valor`;
   - `Account.of(project)`: from the spec, else own; `ready()`: `call.account(profile) == email`, so a profile
     signed in as the other address is not ready.

   `secret`, `_answer` and `renew` take the account in place of the module constants.
3. **Turns:** `_client_env` adds `Account.of(project).env`. The Brief, beside the 1Password line, says for a
   project with `google`: "Google: gws is <email> here; Valor's own is
   `GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/gws gws ...`". Other projects get nothing new.
4. **meet.join** (`tools/meet.py`):
   - `usage` and `refuse` take `account`, a string or null, like the other keys.
   - The account is `account`, else the project's.
   - `_open` checks `Account.ready()` or its item. Its failure names `meet-sign-in --account <email>` when
     the account is not own.
   - `create_space` runs with the account env, and `call.begin` records the account on the call marker.
   - `_call` opens `Page(account.profile)`. `Call.joined`'s renewal and its message (`core/call.py` ~2226)
     use that account and its command, and so does the Sheet read. `hours` is unchanged (own).
5. **`invites`:**
   - Its accounts are own plus each `google` of a spec placed on this Mac, each calendar listed with its
     account's env.
   - A list that fails is a `calendar.unlisted` row (account, why), and the other accounts still run.
   - `_own` holds every one of those addresses, plus Yudame's domain and Tom's as today. A client's domain
     still matches.
   - Accepting patches through the account that listed the event. The `calendar.accepted` key adds the
     account when it is not own, so existing rows still match.
   - Wakes key on the event's `iCalUID` (shared by every attendee's copy), not its per-calendar `id`:
     `invite:<iCalUID>:<start>`. So one event seen on two calendars, or on one in one run and the other in
     the next, wakes once.
   - The wake names "Join as: <email>": the project's account when it was invited, else one that was.
     `skills/sdlc/converse.md` **Calls** passes it as `account`.
   - An old `invite:<id>` row of a call in its window at rollout could wake again, so rollout is timed
     outside any call window.
6. **`meet-sign-in [--account EMAIL]`:** profile, item and messages come from the account, and it reports
   success only when `account(profile)` is that email. The update routine's weekly notice goes per account
   placed here, using `ready()`.
7. **Docs, with no client name:** `docs/machine.md`, `docs/rollout.md` step 6 (a second account),
   `docs/bridges/meet.md`, the `docs/performers.md` rows.

**Rollout (the Captain), after merge, in order:**
1. Rename the client's 1Password item to `Google <email>`, then `meet-sign-in --account <email>`, which
   proves the Meet profile and renewal with no spec field.
2. Run `gws auth status` on the Captain, then `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file`. As the new
   account in byob Chrome, create a Cloud project "Valor" in the client's org: an Internal consent screen,
   a Desktop client, and the Calendar, Sheets, Drive, Gmail and Meet APIs enabled. Its JSON goes to `m-valor`.
   Sign in with only `calendar`, `spreadsheets`, `drive`, `gmail.readonly`, `meetings.space.created`,
   `meetings.space.readonly`, `openid` and `userinfo.email`, exported to a plain `credentials.json` in its
   own folder. Then a valor@yuda.me calendar read, to show it is untouched. If blocked, stop there and
   report the admin's clicks; the specs stay unset.
3. As the new account, list its calendar's `needsAction` events with a Meet link in the next 14 days, and
   report them to the chat session before anything accepts.
4. As owner, share the Sheet's parent folder with the new account as writer (`sendNotificationEmail=false`),
   then a `sheet.py` read as the new account (Sheet and Doc).
5. Only once the session has told Tom of step 3's list: `google` in both of the client's specs.

**Out of scope.** Moving or copying the Sheet. The email bridge, app passwords and sending from the new
address. Yudame Tools recordings, the hours calendar, and the client event Valor organizes as valor@yuda.me.
Two calls at once on one Mac. No check, gate or validator is added. The `ready()` comparison and the
`calendar.unlisted` path correct existing code (a check that accepted any account; one account's failure
ending the run) rather than adding a limit.

**Tests** (fakes; made-up names and domains):
- **Spec and `Account`:** spec parse, refusal and default; `Account`'s own values unchanged and another's
  derived; `ready()` false for a profile signed in as the other address.
- **Turns:** `_client_env` and the Brief carry the var and line for a project with `google`. A project
  without one, and no project, get exactly today's env and Brief.
- **invites:**
  - each account is listed with its env;
  - the second account's list fails and own's events are still accepted and woken, with a
    `calendar.unlisted` row;
  - the second address is not a match but its domain is;
  - one iCalUID under two `id`s, across two runs where the first account's list failed on run one, wakes
    once;
  - "Join as" names the invited account;
  - accept patches via the listing account;
  - an old own `calendar.accepted` row still suppresses;
  - an own-only Mac is unchanged.
- **meet.join:**
  - `account` is refused unless it is a string or null;
  - payload beats project beats own;
  - a client call chat's join with `account` = own opens the own profile;
  - `create_space` and the Sheet read get the env;
  - a wrong-address profile without its item fails naming `--account`.
- **Sign-in and renewal:** `sign_in` and `renew` use the account's item and email, and touch nothing without
  the item. The update notice goes per account, own unchanged.
- Existing tests are updated where they assumed module constants. Then the full suite.

**Live proof, the Captain (done):** as the new account, a calendar read and a join of a test Meet Valor
creates. As valor@yuda.me for another project there, the same. No real invite is answered and no email is
sent. Done means Tom uses it on a real call (rule 5).
