# Record the plan to use phone-harness for mobile work

**Request.** Tom, valor team chat, 2026-11-21 14:52 UTC (thread of
1795175659.482299): "OK, well put this in documentation that we plan to use
it so we don't forget when the time comes". "This" is
https://github.com/phone-harness/phone-harness, which he posted 2026-11-20
("The pirate machine has an iPhone attached and the Cowboy an Android";
"We expect to do more mobile dev in a couple months"). Task eef246d0c4f2
read the tool's repository and checked the Cowboy on 2026-11-21.

## Where it goes

`docs/rollout.md`, a new short section "Phones, for mobile work (planned)"
placed before "Moving a project to another Mac". Rollout is where a Mac's
setup lives and where someone setting up for mobile work would look;
`machine.md` is about one Mac's RAM, launchd and secrets, not attached
devices. No new file.

## What the section says (about 25 lines)

1. **The plan, dated.** Use phone-harness for mobile dev, expected around
   late January 2027 (Tom, 2026-11-20: "a couple months"). Set it up about
   two weeks before that work starts, both Macs in one sitting, and try it
   on a real app flow. Not set up earlier: as of 2026-11-21 the tool is
   three months old, has no releases, and changes weekly.
2. **What it is.** A Python tool (installed with `uv`, Python 3.12) that
   lets an agent open apps, tap, type, scroll and read a real phone's
   screen, used from Claude Code as a CLI and skill.
3. **How it reaches each phone.** iPhone on the Pirate through the Mac's
   iPhone Mirroring window: macOS 15+, iOS 18+, the phone paired for
   Mirroring, and Screen Recording and Accessibility permission, which
   someone at the Mac must click. Android on the Cowboy through adb, USB
   (or Wi-Fi) with USB debugging on.
4. **State found 2026-11-21.** Cowboy: no Android reachable (nothing
   phone-like on USB, only a USB-C hub; adb not installed; no wireless
   debugging advertised). Pirate: not checked.
5. **Setup steps.** Install with uv; `phone-harness skill`; follow its
   `onboarding.md`; connect the phone (someone at the Mac); `brew install
   android-platform-tools` on the Cowboy for adb; Mirroring pairing and the
   two permission clicks on the Pirate; `phone-harness doctor` to confirm;
   `phone-harness config set platform ios|android`.
6. **Known limits.** Reads text by OCR, not icons; no multi-touch, camera
   or Face ID; unlocking the iPhone pauses Mirroring; a PIN-locked Android
   needs a person; DRM video renders black.
7. **Sources.** The tool's README and GitHub API, read 2026-11-21 (task
   eef246d0c4f2); Tom's two messages above.

Before writing step 5 and the limits, the builder re-reads the tool's
README (through the valor:web agent) because eef246d0c4f2's findings were a
summary, not the raw text; where the README differs, the README wins and
the date stays 2026-11-21.

## Out of scope

Installing phone-harness or adb, checking the Pirate, changing any Mac.
No code, no checks or gates (no governance added).

**Stakes.** Low: a docs paragraph, reversible by one commit.

## Tests

None: docs only. The builder confirms `docs/rollout.md` renders the new
section in place, every item in the request's list appears in it, and the
existing suite's doc tests (if any read rollout.md) still pass.
