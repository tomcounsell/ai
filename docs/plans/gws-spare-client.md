# Stale spare copy of Valor's Google OAuth client

**Ask.** Tom, Slack DM 2026-11-16 (thread 1794791985.608979, message
1794832143.904609, found by his Claude Code during the admin change): the
spare `~/.config/valor-gws-client.json` and `persistent-signin.md` name an
old OAuth client. A defect fix: docs and Valor's own config, one commit.

**Premise, checked 2026-11-16 on the Cowboy (observed).**
- The live client is `224102219743-t4kcp78s5887es73qnp5uplbk3rb4sn9`:
  the Cowboy's `~/.config/gws/client_secret.json` (Oct 26), the
  1Password item rollout step 6 reads, and `client_id` in
  `credentials.json` on all four Macs. Local `getProfile` returns
  valor@yuda.me. Tom set this app to Trusted and ticked Exempt trusted apps.
- The spare exists only on the Cowboy (Mar 6), and it holds the old
  client `376587595481-2ehqq9o8...`. `persistent-signin.md:95` tells Tom
  to trust that old ID.
- **Why it went stale:** plan step 1 (2026-10-17) made the spare once.
  On 2026-10-26 the client moved to "Valor Desktop Client" in Yudame
  General, and rollout step 6 started taking it from 1Password
  (8cc7ce58b, Oct 27). No step makes or refreshes the spare, so it kept
  the March client.
- Also found: the live `client_secret.json` on the Bald and the Captain
  (Oct 22) is still the old client. Those Macs' tokens refresh with the
  new client, since `credentials.json` carries its own, but a fresh
  sign-in there would use the untrusted old app. The Pirate has the new
  client and no spare.

## What is built

1. `docs/plans/persistent-signin.md`: the fallback names `224102219743-…`,
   with a dated note that Tom trusted it and ticked Exempt trusted apps on
   2026-11-16 (Slack, message above). Step 1 points to rollout step 6 for
   the spare.
2. `docs/rollout.md` step 6: right after the `op read`, make the spare
   from the client just written:
   `install -m 600 ~/.config/gws/client_secret.json ~/.config/valor-gws-client.json`.
   It follows the client in use, on every Mac, each time step 6 runs.
   `docs/machine.md` names neither the spare nor the old ID, so it is unchanged.
3. On the Cowboy, run that same line, replacing the spare.
4. On the Bald and the Captain, over SSH, run step 6's client lines
   (`op read` from 1Password, then the spare). First move the old file to
   `~/.config/gws/client_secret.json.old-376` (mode 600), so undoing it
   is one `mv`. `credentials.json` and `token_cache.json` are not touched
   and no one signs in again. If `op` is unavailable over SSH there,
   copy the Cowboy's `client_secret.json` over SSH (same bytes), and say so.
5. The Pirate gets only the spare line, so every Mac follows step 6.

**Out of scope.** The live credential, re-consent, Google admin settings,
the over-SSH keychain 401 (already documented), any new check. The client
secret never goes into a commit, a log or the report: only ID prefixes
are printed.

## Tests

Docs and config only, so no unit test. Live checks, printing only
`client_id` prefixes:
- On each of the four Macs, `client_secret.json` and
  `valor-gws-client.json` both report `224102219743-t4kcp78`, are
  byte-identical (`cmp`), and are mode 600.
- `credentials.json` mtime and hash on each Mac are unchanged from before
  the change.
- On the Cowboy, `gws gmail users getProfile` still returns valor@yuda.me.
- `grep -rn 376587595481 docs/` finds only the history line that names
  the old client as replaced.
- The suite (`pytest`) still passes, including `test_live_mail.py`'s
  rollout scope check.
