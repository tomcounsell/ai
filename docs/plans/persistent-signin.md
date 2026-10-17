# Persistent sign-in for Valor's accounts

**Ask.** Tom, Telegram DM 2026-10-17 12:02 UTC (message 11632): "Let's set
up a persisting sign in option so you always have access to your
accounts". Done when Tom asks something that needs my email or calendar,
I answer it, and it still works after the Mac restarts.

**Premise, checked on Valor the Cowboy (2026-10-17).**
- `gws` 0.22.5. `gws auth status`: oauth2 `authorized_user` in
  `~/.config/gws/credentials.enc` (Jun 15), `storage: encrypted`. Its
  AES key is in the login keychain (service `gws-cli`), and
  `encryption_valid` is true. `token_error: invalid_rapt`.
  `token_cache.json` (Mar 12, mode 644) predates the current key, which
  is the "could not decrypt" warning. gws logs that failure, skips the
  cache, and rebuilds it. The outage is the `invalid_rapt`.
- `gcloud` (valor@yuda.me) fails the same way: "Reauthentication failed".
  Google Cloud session control (admin answer 9368756) forces reauth only
  for tokens holding Cloud Platform scopes. gws's default scope list
  includes `pubsub`, so a default login carried one. Gmail in Chrome, a
  plain Google session, is still signed in. **Inferred, not shown:** the
  old token's scopes can't be read because it won't refresh. A testing
  app's 7-day expiry doesn't fit: the token worked from June to October.
- `gws` removed domain-wide delegation and impersonation (CHANGELOG
  e1505af). A service account would read its own empty mailbox.
  **Rejected.**
- BYOB Chrome is signed in as valor@yuda.me, so the build can approve the
  one-time consent there (gws README, "agent-assisted flow").

## What is built

1. **Keep the OAuth client.** Copy `~/.config/gws/client_secret.json` to
   `~/.config/valor-gws-client.json` (mode 600) before anything else. If
   it were lost, getting it back would mean the Cloud console, which sits
   behind the policy that is failing now. After logout, confirm it is
   still in place, and restore it if not.
2. **Re-consent with Workspace scopes only.** `gws auth logout`, then
   `gws auth login --scopes` with exactly gmail.modify, calendar, drive,
   documents, spreadsheets, presentations and tasks, plus openid and
   userinfo.email. No `pubsub`, no `cloud-platform`. Approve it in BYOB
   Chrome (explicit `tabId`).
3. **Store the credential in plaintext.** Run `gws auth export --unmasked`
   first, because export reads only the encrypted store. Write the output
   to `~/.config/gws/credentials.json` (mode 600), gws's own plaintext
   path, held the way `gh`'s `hosts.yml` is. Then delete
   `credentials.enc`. The refresh token no longer depends on the
   keychain. `token_cache.json` stays encrypted with the keychain key,
   but it is a disposable access-token cache: on a Mac with another key
   or none, gws skips it and writes a new one. No env var is needed.
4. **Every Mac.** Rollout step 6 copies `credentials.json` and
   `client_secret.json` from the Cowboy, the way step 5 copies keys, at
   mode 600. Then `gws gmail users getProfile --params '{"userId":"me"}'`
   must name valor@yuda.me.
5. **Docs.** `docs/rollout.md` step 6 gets the copy, the check, and why
   the scopes stay Workspace-only. `docs/machine.md`'s credentials table
   gets a Google row covering:
   - Who reads it: turns, through `gws`.
   - Where it lives: the refresh token in plaintext, mode 600. The access
     cache is keychain-encrypted and disposable.
   - What ends it: revocation, a password change, or 2-Step Verification
     changes.
6. **Inventory, reported in the delivery.** The full list is
   `docs/machine.md`'s credentials table plus the turn's own accounts:
   - GitHub CLI token: classic PAT, expires 2026-12-31 07:27 UTC, no
     `read:org`.
   - `github-keys` push token: is it the same PAT?
   - Sentry CLI token: no expiry. The Chrome Sentry session has lapsed.
   - Telegram session: persistent.
   - Slack: rotation is off, so no expiry.
   - Mail-keys Gmail app password: doesn't lapse. A password or 2SV
     change revokes it, as it does the new refresh token.
   - Anthropic `claude-token`: one-year life; report its creation date.
     Without it, the Keychain fallback lasts about 8 hours and lapses
     the same way.
   - OpenAI and judgement keys: no expiry.
   - `gcloud`: keeps lapsing; turns don't use it.
   - Chrome: Claude Platform and Cloudflare are on login pages.

   The kernel key directory is closed to turns ("Operation not
   permitted"). For the entries held there, the build reports what a
   turn can observe (does the gateway's frontier call or the OpenAI
   route answer?). Where it can't, it names the file and the one command
   that answers it.

**Out of scope.** Rotating the GitHub PAT (named, with steps). Web
sessions that Valor's work doesn't use. `gcloud`. Any wrapper script or
refresh job: gws refreshes the token itself.

**Fallback, kept until the premise is shown.** The delivery labels the
premise unverified until the Workspace-only token has refreshed at least
24 hours after consent: that would be Tom's real question after a
restart, or a rerun of the checks. If `invalid_rapt` returns, Tom gets
one question with these clicks:
- admin.google.com → Security → Access and data control → API controls →
  Manage Third-Party App Access. Add the client
  `376587595481-2ehqq9o8jb8t1s8ucb7ovsj57pnu4vrp.apps.googleusercontent.com`
  as **Trusted**.
- Security → Access and data control → Google Cloud session control →
  tick **Exempt trusted apps** → Save.

## Tests

No code changes, so no unit test. The rule is checked live:
- `gws auth status` after step 3 shows:
  - `storage: plaintext`
  - `encrypted_credentials_exists: false`
  - `plain_credentials_exists: true`
  - `token_valid: true`, with no decrypt warning
  - no `pubsub` or `cloud-platform` in `scopes`
- Gmail threads list, Calendar events list and Drive files list each
  return 200, run from a fresh turn process, the way launchd starts one
  after a restart.
- **A Mac with no keychain key.** Point `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`
  at a temp dir holding only `credentials.json` and `client_secret.json`,
  plus a garbage `token_cache.json`. Set
  `GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND=file`, so gws can't reach the
  login keychain and makes a fresh `.encryption_key`. The three calls
  succeed, and `token_cache.json` is rewritten (mtime changes).
- Write path: create and delete a Gmail draft, and create and delete a
  calendar event on valor@yuda.me.
- `client_secret.json` is byte-identical to the copy from step 1.
- Open, named in the delivery: a real Mac restart, the 24-hour refresh,
  and Tom's real question (rule 5).
