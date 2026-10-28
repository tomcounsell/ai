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

1. **Agent doc first.** In `mac-app.md:42`, name the identity prefix
   "Developer ID Application: Yudame, Inc.", the keychain
   `~/Library/Keychains/yudame-signing.keychain-db`, and how to unlock it
   after a reboot (`op read "op://m-valor/Yudame signing keychain/password" |
   security unlock-keychain <path>`, which reads the password on stdin; this
   was tested). Also name the `yudame-notary` profile. Only `op://`
   references appear, never a value. Commit this, so the paragraph lands
   even if the items never come.
2. **Wait inside the turn, without re-stepping.**
   - Poll `op item list --vault m-valor` for both titles, sleeping 9 minutes
     per Bash call, for up to 12 hours.
   - The wait holds one of the Cowboy's two turn slots (`max_turns` 2), so
     other tasks run at half throughput while it lasts. Twelve hours caps
     that cost; the delivery names it.
   - If either item is still missing after 12 hours, end with a delivery
     that says the setup is unfinished and why, with the doc paragraph
     merged. That is a status, not a new question.
   - An idle turn is re-stepped with no delay and no bound, so ending a
     turn to wait would loop.
3. **Dedicated keychain, decided up front.**
   - It is not the login keychain Tom named ("import the .p12 into Valor's
     login keychain"). Headless codesign needs `set-key-partition-list`
     with that keychain's password, and I don't hold the Cowboy's login
     password. Without it, codesign raises a GUI prompt that would hang the
     turn.
   - Nothing is imported into the login keychain, not even as a trial.
     This deviation is listed under Decisions in the delivery, quoting
     Tom.
   - Steps:
     1. Generate a password and store it in m-valor as "Yudame signing
        keychain".
     2. Create the keychain with the password fed twice on stdin
        (`printf '%s\n%s\n'`). `create-keychain` prompts for it and its
        retype, and a single line leaves it with an empty password and
        exit 0 (probed). Then run `set-keychain-settings` with no timeout
        and no lock on sleep.
     3. Set the user search list explicitly to both keychains: `security
        list-keychains -d user -s ~/Library/Keychains/login.keychain-db
        ~/Library/Keychains/yudame-signing.keychain-db`. `-s` replaces the
        list, so login stays first. Read the list back afterwards.
     4. Import the .p12 with `-T /usr/bin/codesign`.
     5. Set the partition list to `apple-tool:,apple:`, with the password
        on stdin. A wrong password prints "passphrase you entered is not
        correct" without a reliable exit code, so the output is checked
        for that text. The headless re-sign test is the real evidence.
   - The .p12 is written only to `$TMPDIR`, mode 600, and deleted right
     after import.
4. **Notary profile `yudame-notary`**, stored in the dedicated keychain with
   `notarytool store-credentials --keychain <path>`, after step 3 has put
   that keychain on the search list. That is how `package.sh`'s
   `--keychain-profile` finds it without a change. The .p8 temp file is
   deleted afterwards.
5. **Signed test build.** In `~/src/yudame-tools`, run
   `SIGN_IDENTITY="Developer ID Application: Yudame, Inc. (<TEAMID>)"
   NOTARY_PROFILE=yudame-notary tools/package.sh`. Copy the zip into this
   workspace (`out/`, which is gitignored).

**Secrets on argv.**
- Wherever a tool reads a secret on stdin, it gets it there:
  `unlock-keychain`, and the passwords for `create-keychain` and
  `set-key-partition-list` when given no `-p` or `-k`.
- `security import -P` and `notarytool --key-id/--issuer` take values on
  argv. They are visible in `ps` for the moment the command runs, on a
  single-user Mac.
  - The .p12 password protects only a file that is deleted seconds later.
  - The Key ID and Issuer ID are identifiers, not secrets. The .p8 is
    passed as a path.

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
  directory and add a `com.apple.quarantine` xattr. Then all of these pass
  on that copy:
  - `spctl --assess --type execute -vv` reports "accepted,
    source=Notarized Developer ID";
  - `xcrun stapler validate`;
  - `syspolicy_check distribution`.
  A quarantined launch waits for a click on screen, so the launch test
  uses a copy without quarantine: `open`, `pgrep`, then quit.
- Keychain password is real: lock the keychain. An empty password and a
  wrong one both fail to unlock it, and the m-valor password succeeds.
- Headless edge: lock the dedicated keychain, then unlock it the way the
  agent doc says (stdin), and re-sign a scratch copy without any prompt. This shows
  a build after a reboot works.
- No secret escaped:
  - `git status` in both repos shows no .p12 or .p8;
  - `$TMPDIR` has no .p12 or .p8 left;
  - `security list-keychains -d user` still lists login first, and
    Claude Code still authenticates;
  - a grep of the build output for the .p12 password, the Key ID and the
    keychain password finds nothing.
- The Valor suite passes, since the agent doc is in the repo.

## Delivery

The notarized `YudameTools.zip` with its sha256, sent for Tom to run:
unzip it and open it. It should open without the right-click step, then
ask for Accessibility once. Not done until Tom has run it.
