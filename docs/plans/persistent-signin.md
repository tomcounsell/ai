# Persistent sign-in for Valor's accounts

**Ask.** Tom, Telegram DM 2026-10-17 12:02 UTC (message 11632): "Let's set
up a persisting sign in option so you always have access to your
accounts". Done when Tom asks something that needs my email or calendar,
I answer it, and it still works after the Mac restarts.

**Premise, checked on Valor the Cowboy (2026-10-17).**
- `gws` 0.22.5. `gws auth status`: oauth2 `authorized_user` in
  `~/.config/gws/credentials.enc` (Jun 15, key in the login keychain,
  `encryption_valid: true`), `token_error: invalid_rapt`. The stale
  `token_cache.json` (Mar 12, mode 644) predates the current key: that is
  the "could not decrypt" warning. It is logged and skipped; the outage
  is the `invalid_rapt`.
- `gcloud` (valor@yuda.me) fails the same way: "Reauthentication failed".
  That is Google Cloud session control (admin answer 9368756). It forces
  reauth only for tokens holding Cloud Platform scopes. `gws`'s scope
  menu includes `pubsub`/`cloud-platform` (`--full`), and Gmail in Chrome
  (a plain Google session) is still signed in. So a Workspace-only token
  is outside the policy.
- The credential worked from June to October. A testing-mode app's
  tokens die after 7 days, so the 7-day expiry is not the cause.
- `gws` removed domain-wide delegation and impersonation (CHANGELOG
  e1505af). A service account would read its own empty mailbox, not
  valor@yuda.me's. Minting delegated tokens ourselves would be a second
  mechanism. **Rejected.**
- BYOB Chrome is signed in as valor@yuda.me (Gmail and Drive tabs open),
  so the one-time consent can be done by the build from that browser
  (gws README, "agent-assisted flow"). No admin change is needed.

## What is built

1. **Re-consent with Workspace scopes only.** `gws auth logout` clears the
   old credential and the stale cache. Then run `gws auth login --scopes`
   with exactly gmail.modify, calendar, drive, documents, spreadsheets,
   presentations and tasks, plus openid and userinfo.email. No `pubsub`,
   no `cloud-platform`. Approve the consent in BYOB Chrome (explicit
   `tabId`).
2. **Store it without a keyring key.** `gws auth export --unmasked` goes
   to `~/.config/gws/credentials.json`, mode 600. That is gws's own
   plaintext path (precedence 4), stored the same way as `gh`'s
   `hosts.yml`. Then delete `credentials.enc`. Nothing depends on a
   keychain key, so a regenerated key can no longer strand the credential.
   No env var is needed.
3. **Every Mac.** Rollout step 6 copies that file from the Cowboy, the
   same way step 5 copies keys:
   `ssh valorengels@valor-the-cowboy cat .config/gws/credentials.json > ~/.config/gws/credentials.json; chmod 600 …`.
   It also copies `client_secret.json`. Then
   `gws gmail users getProfile --params '{"userId":"me"}'` names
   valor@yuda.me. A refresh token is not bound to a machine.
4. **Docs.** `docs/rollout.md` step 6 gets the copy and the check, and
   says why the scopes stay Workspace-only. `docs/machine.md`'s
   credentials table gets a row for the Google credential: where it
   lives, who reads it (turns, through `gws`), and that it does not lapse
   unless revoked or the password changes.
5. **Inventory, reported in the delivery** (checked live):
   - GitHub classic PAT: **expires 2026-12-31 07:27 UTC**. It also lacks
     `read:org`.
   - Sentry CLI auth token: no expiry. Chrome's Sentry web session has
     lapsed, which only matters to the browser.
   - Telegram: Telethon session, persistent.
   - Slack: `token_rotation_enabled: False`, so tokens don't expire.
   - `gcloud`: will keep lapsing under the same policy. Turns don't use
     it.
   - Chrome: Google signed in. Claude Platform and Cloudflare are on
     login pages.

   The build checks whether `github-keys`' push token is the same PAT and
   reports its expiry too.

**Out of scope.** Rotating the GitHub PAT (2.5 months left; named with
steps). Signing back into web sessions that Valor's work doesn't use.
`gcloud`. Any wrapper script or credential-refresh job: gws refreshes
the token itself.

**Fallback, only if `invalid_rapt` comes back on the Workspace-only token.**
That would disprove the premise. The delivery carries the admin clicks:
- admin.google.com → Security → Access and data control → API controls →
  Manage Third-Party App Access. Add the client
  `376587595481-2ehqq9o8jb8t1s8ucb7ovsj57pnu4vrp.apps.googleusercontent.com`
  as **Trusted**.
- Then Security → Access and data control → Google Cloud session control
  → tick **Exempt trusted apps** → Save.

## Tests

No code changes, so no unit test. The rule is checked live:
- After step 2: Gmail threads list, Calendar events list, and Drive files
  list each return 200. `gws auth status` shows `token_valid: true`,
  `credential_source` plaintext, no decrypt warning, and no Cloud scope.
- Fresh process without the keychain key in play: run the same three
  commands from a new turn (as launchd starts it after a restart). Also
  run them with `GOOGLE_WORKSPACE_CLI_CONFIG_DIR` pointing at a temp copy
  holding only `credentials.json` and `client_secret.json`. That
  simulates a second Mac.
- Write path: create and delete a draft. Create a calendar event on
  valor@yuda.me's calendar, then delete it.
- Boundary: the scope list sent to Google contains no
  `cloud-platform`/`pubsub` (read from the consent URL).
- Open, named in the delivery: a real Mac restart, and Tom's real
  question (rule 5).
