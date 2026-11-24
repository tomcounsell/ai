# A project can work as its own Google account, beside valor@yuda.me

**Request.** Tom, 2026-11-24: a client gave Valor a Google Workspace account in its own domain. That
client's projects (PM and eng) should use it for mail reads, calendar, Sheets, Drive and Meet. Every
other project keeps valor@yuda.me with no change. Answers (the chat session, on the stated defaults): an invited call
joins as the address invited. The hours calendar, the email bridge and call recordings stay on
valor@yuda.me. The client's product Sheet stays where it is, with the new account added as an editor and no notification.
Valor makes the OAuth client itself in the client's Workspace and stops with written clicks if blocked.
The spec fields live in the private Valor folder.

**Today.** One account everywhere: gws's single `~/.config/gws`, used by turns, `invites`, `hours`,
`tools/meet.py` `create_space` and the call's Sheet read. One `settings.meet_profile`. `bridges/meet/google.py`
is fixed to the item "Google valor@yuda.me". `invites` reads one primary calendar, and its `_own` holds one address.

**Build.**
1. **`Spec.google`** (`core/workspace.py`): optional account address. It is refused unless it holds exactly one
   `@`. Absent means Valor's own (`persona.identity` email).
2. **`bridges/meet/google.py`: `Account(email)`**, the one place an account's paths are derived:
   - `config_dir`: `~/.config/gws` for the own account, else `~/.config/gws-<email>`;
   - `env`: `{}` for own, else `{"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": config_dir}`;
   - `profile`: `settings.meet_profile` for own, else `<meet_profile>-<email>`;
   - `item`: `"Google <email>"` in `m-valor`;
   - `Account.of(project)`: from the spec, else own.

   `secret`, `_answer` and `renew` take the account in place of the module constants. The client's
   existing 1Password item is renamed to the `Google <email>` form at rollout (a rename, no new item).
3. **Turns:**
   - `_client_env` (`core/__main__.py`) adds `Account.of(project).env`.
   - The Brief (`core/tasks.py`, beside the 1Password line) says, for a project with `google`: "Google: gws
     is <email> here; Valor's own is `GOOGLE_WORKSPACE_CLI_CONFIG_DIR=~/.config/gws gws ...`".
   - A turn of any other project gets nothing new.
4. **Kernel uses:**
   - `tools/meet.py`: `create_space` runs with the project's account env.
   - `tools/meet.py` `_open`: the account is `payload.account`, else the project's. It checks that
     account's profile and item, and `call.begin` records it on the call marker.
   - `core/__main__.py` `_call`: opens `Page(account.profile)`, and `Page.renew` renews with that account.
   - `core/call.py` Sheet read: runs with the call's account env.
   - `hours`: unchanged; it runs in the kernel's own env, so it stays on own.
5. **`invites`:**
   - Its accounts are own plus each `google` of a spec placed on this Mac. It lists each account's primary
     calendar with that account's env.
   - `_own` holds every one of those addresses, plus Yudame's domain and Tom's as today. A client's domain
     still matches its project.
   - Accepting patches through the account that listed the event. The `calendar.accepted` key adds the account
     when it is not own, so existing rows still match.
   - An event seen on two accounts (same `iCalUID` and start) wakes once, as the project's account when that
     account was invited, else the first.
   - The wake text names "Join as: <email>". `skills/sdlc/converse.md` **Calls** passes it as meet.join's
     `account`.
6. **`meet-sign-in [--account EMAIL]`** (`core/call.py` `sign_in`): the profile, item and messages come from the account.
   The update routine's weekly notice is sent per account placed here, keyed by the account.
7. **Docs, with no client name:** `docs/machine.md`, `docs/rollout.md` step 6 (a second account: its
   Cloud project and Internal client, scopes, `gws auth login` with the config dir, the export, 1Password copy),
   `docs/bridges/meet.md`, `docs/performers.md` rows.

**Rollout (the Captain), after merge:**
- **Sign-in app:** as the new account in byob Chrome, a Cloud project "Valor" in the client's org, with an Internal
  consent screen and a Desktop client. Enable the Calendar, Sheets, Drive, Gmail and Meet APIs. The client JSON goes in
  `m-valor` as "Google OAuth client <email>".
- **gws sign-in:** `gws auth login` with only `calendar`, `spreadsheets`, `drive`, `gmail.readonly`,
  `meetings.space.created`, `meetings.space.readonly`, `openid` and `userinfo.email`, exported to the config dir.
- **Blocked:** if project creation or the client is blocked, stop and report the admin's clicks, written for
  someone other than Tom. Steps 1–6 and the Meet profile are proved regardless.
- **Spec and Sheet:** `google` goes in both of the client's specs (private folder). The Sheet is shared to the new
  account as writer with `sendNotificationEmail=false`, as its owner.

**Out of scope.** Moving or copying the Sheet. The email bridge, app passwords and sending from the new
address. Yudame Tools recordings. The hours calendar. Moving the one client event Valor organizes as
valor@yuda.me. Two calls at once on one Mac. No check, gate or validator is added: the spec field's parse
refusal is the spec loader's existing pattern for a malformed value, not a new limit.

**Tests** (fakes only, made-up names and domains):
- **Spec:** `google` is parsed; a value without exactly one `@` is refused; absent means own.
- **`Account`:** own keeps today's paths, item and empty env; another derives each value.
- **`_client_env` and Brief:** a project with `google` gets the var and the line; a project without, and no
  project, get exactly today's env and Brief (regression for every other project).
- **invites:**
  - two accounts are each listed with their own env;
  - the second address is not a match, but its domain still matches the client;
  - an event on both accounts wakes once, with the right "Join as";
  - accept patches via the listing account;
  - an own-account `calendar.accepted` row from before still suppresses a re-accept;
  - an own-only Mac's calls are unchanged.
- **meet.join:**
  - `account` payload beats the project, which beats own;
  - `create_space` and the Sheet read get the account env;
  - the profile check and the failure text name the account's sign-in command.
- **Sign-in and renewal:** `sign_in` and `renew` use the account's item and email. Without the item, nothing is
  touched (as today).
- **Update notice:** per account, own unchanged.
- Existing tests updated where they assumed module constants. Then the full suite.

**Live proof, the Captain (done):**
- **As the new account:** a calendar read; `meet-sign-in --account` reaching Meet; a test Meet Valor creates
  itself, joined with that profile.
- **As valor@yuda.me** for another project on that Mac: the same calendar read and test join.
- **Limits:** no real invite is answered and no email is sent.
- Done means Tom uses it on a real call (rule 5).
