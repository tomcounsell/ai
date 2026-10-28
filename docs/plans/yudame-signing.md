# Yudame code signing on the Cowboy

Task e2992a130418. Tom, valor team chat, 2026-10-28 02:44 UTC (message
1793155479.373219): Valor's own Apple Account can't be set up, so the
invite is dropped. Valor accepts nothing, signs in nowhere, and never signs
in as Tom. Tom puts two team items in m-valor:

- **"Yudame Developer ID"**: the Developer ID Application certificate and
  its private key as a .p12, plus the .p12's password.
- **"Yudame notary key"**: an App Store Connect Team API key with the
  Developer role, as the .p8 file, Key ID and Issuer ID.

Done when Tom has run a Valor-built Mac app signed "Developer ID
Application: Yudame, Inc." (rule 5).

## Measured (2026-10-28, the Cowboy)

- No code-signing identity in any keychain. Neither item is in m-valor yet.
- Yudame Tools (`~/src/yudame-tools`, main f790aea) already has
  `tools/package.sh`. With `SIGN_IDENTITY` it signs with hardened runtime
  and a timestamp. With `NOTARY_PROFILE` it also notarizes, staples and
  runs `spctl`. No change to that repo is needed.
- The `valor:mac-app` agent's signing paragraph
  (`skills/valor/agents/mac-app.md:42`) says only "from the shared 1Password
  vaults". It doesn't name the items, the keychain or the profile.

## What is done

1. **Wait for the items.** At the start of the build stage, `op item get`
   both items. If either is missing, end the turn without asking again
   (Tom's instruction).
2. **Import the identity into a dedicated keychain,
   `~/Library/Keychains/yudame-signing.keychain-db`.**
   - Not the login keychain. Letting codesign use a private key without a
     GUI prompt needs `security set-key-partition-list`, which needs that
     keychain's password. I don't hold the Cowboy's login password.
   - The new keychain's password is generated and stored in m-valor as
     "Yudame signing keychain".
   - It has no auto-lock (`set-keychain-settings` with no timeout), is
     added to the user search list, and is imported with `-T
     /usr/bin/codesign`, then given a partition list of `apple-tool:,apple:`.
   - The .p12 is only ever written to `$TMPDIR` with mode 600 and is
     deleted right after import.
   - If Tom's directive to use the login keychain turns out to work without
     a prompt (unlikely), use it instead and drop the extra keychain.
3. **Notary profile `yudame-notary`.** `xcrun notarytool store-credentials
   yudame-notary --key <p8> --key-id <id> --issuer <issuer>`, into the login
   keychain, which is unlocked in the session. If that fails, pass
   `--keychain` to the dedicated one. The .p8 temp file is deleted
   afterwards.
4. **Signed test build.** In `~/src/yudame-tools`, run
   `SIGN_IDENTITY="Developer ID Application: Yudame, Inc. (<TEAMID>)"
   NOTARY_PROFILE=yudame-notary tools/package.sh`. Copy the zip into this
   workspace (`out/`, which is gitignored) for delivery.
5. **Agent doc.** In `mac-app.md`'s signing paragraph, name the identity,
   the keychain and how to unlock it (`security unlock-keychain -p "$(op
   read ...)"` after a reboot), and the `yudame-notary` profile, so later
   builds sign without rediscovering any of this. One paragraph, no new
   mechanism.

## Out of scope

- Valor's own Apple Account, the invite, Xcode sign-in, and Apple
  Development certificates.
- Signing on the other Macs. The Cowboy is the one Mac with Xcode 26.
- Re-signing Brew Status. It runs only on Valor's Macs, and ad hoc is fine
  there.
- Any change to `package.sh` or BAMA's build.
- No guard, check, hook or gate is added.

## Tests (evidence in the delivery; this is operations, not suite code)

- `security find-identity -v -p codesigning` lists exactly one valid
  "Developer ID Application: Yudame, Inc." identity.
- `codesign -dv --verbose=4`:
  - Authority chain: Developer ID Application, then Developer ID
    Certification Authority, then Apple Root CA;
  - TeamIdentifier equals the certificate's team;
  - the runtime flag is set;
  - a secure timestamp is present.
- `codesign --verify --deep --strict` passes.
- `notarytool` returns status Accepted. `xcrun stapler validate` passes.
- Gatekeeper, as Tom's Mac will see it: unzip the delivered zip into a new
  directory and add a `com.apple.quarantine` xattr. `spctl --assess --type
  execute -vv` says "accepted, source=Notarized Developer ID". The app
  launches (`open`, then `pgrep`), and is quit afterwards.
- Headless edge: lock the dedicated keychain, then unlock it the way the
  agent doc says, and re-sign a scratch copy without any prompt. This shows
  a build after a reboot works.
- No secret escaped:
  - `git status` in both repos shows no .p12 or .p8;
  - `$TMPDIR` has no .p12 or .p8 left;
  - a grep of the build output for the .p12 password, the Key ID and the
    keychain password finds nothing.
- The Valor suite passes, since the agent doc is in the repo.

## Delivery

The notarized `YudameTools.zip` with its sha256, sent for Tom to run:
unzip it and open it. It should open without the right-click step, then
ask for Accessibility once. Not done until Tom has run it.
