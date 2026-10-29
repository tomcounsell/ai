# Yudame code signing on the Cowboy

Task e2992a130418. Tom, valor team chat, 2026-10-28 02:44 UTC (message
1793155479.373219): Valor's own Apple Account can't be set up, so the invite
is dropped; Valor signs in nowhere and never as Tom. Tom put two items in
m-valor: "Apple Developer ID Certificate" (Developer ID Application .p12 and
its password) and "Apple Developer notary key" (an app-specific password on
his Apple ID, tomcounsell@pm.me, team 8BPCM64MX5; App Store Connect would not
issue a team API key, 07:57 UTC message 1793174255.479439).

Done when Tom has run a Valor-built Mac app signed "Developer ID
Application: Yudame, Inc." (rule 5).

## What is done

1. **Dedicated keychain** `~/Library/Keychains/yudame-signing.keychain-db`,
   not the login keychain Tom named: headless codesign needs
   `set-key-partition-list` with the keychain's password, and Valor does not
   hold the Cowboy's login password. Password generated, stored in m-valor as
   "Yudame signing keychain", fed twice on stdin to `create-keychain` (once
   leaves it empty). No timeout. Search list set explicitly, login first.
   .p12 imported with `-T /usr/bin/codesign`, partition list
   `apple-tool:,apple:`; the .p12 lives in `$TMPDIR` mode 600 only for the
   import.
2. **Notary profile** `yudame-notary` in that keychain, saved with
   `--no-validate` because Apple answers 403 "A required agreement is missing
   or has expired" until the Account Holder accepts the pending agreement.
3. **Test build**: Studio Tools through its unchanged `tools/package.sh`
   with `SIGN_IDENTITY`; with `NOTARY_PROFILE` once Apple accepts it.
4. **Agent doc**: `skills/valor/agents/mac-app.md`'s signing paragraph names
   the identity, keychain, unlock on stdin, and the notary state, with
   `op://` references only.

No guard, check, hook or gate is added. Out of scope: Valor's own Apple
Account, Apple Development certificates, signing on other Macs, Brew Status.

## Tests (evidence in the delivery)

- One valid identity; chain Developer ID Application, Developer ID CA, Apple
  Root CA; TeamIdentifier 8BPCM64MX5; runtime flag; secure timestamp;
  `codesign --verify --deep --strict`.
- Locked keychain refuses an empty and a wrong password, opens with the
  m-valor one; after lock and stdin unlock, codesign runs with no prompt.
- Quarantined copy of the delivered zip: `spctl --assess` (Notarized
  Developer ID once notarized), `stapler validate`; a non-quarantined copy
  launches.
- No .p12, .p8 or password in either repo, `$TMPDIR`, or build output; login
  keychain still first. Valor suite passes.
