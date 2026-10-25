# Instagram from the Mac

**Ask.** Tom, Telegram DM: 2026-10-24 14:57 UTC (11722-11723) "Go like and
repost and follow" https://www.instagram.com/reel/De4PqnkDUzN/ ; 2026-10-25
11:06 (11725) "You already have an Instagram but you've only ever used on
your iPhone"; 11:34 (11727) "use the Common Login in m-vault. And sms
verification can be pulled from iMessage". Done when the reel shows as
liked, reposted, shared to Valor's story, amrtawfikai is followed, and Tom
sees it. Repost and share to story are two separate bars.

**Premise, checked on the Cowboy (2026-10-25).** `op item get "Common
Login" --vault m-valor` answers, with fields `username`, `password` and
`notesPlain`. `~/Library/Messages/chat.db` can be read from a turn. It
holds 583 SMS rows, the newest at 2026-10-24 18:05 UTC, so SMS has
reached this Mac before. Whether it still does today can't be shown
without a new text. BYOB answers `browser_list_tabs`. No Instagram tab is
open, so the 2026-10-25 `cdp_attach_failed` can't be reproduced yet.
macOS 26 shares the clipboard to other devices (Universal Clipboard) and
keeps a clipboard history, so the password never goes on the clipboard.

## What is done

1. **Sign in, password never in a transcript or on the clipboard.** Open
   instagram.com/accounts/login in BYOB Chrome (explicit `tabId`). Type
   `username` with `browser_type`; it may be an email or a phone number,
   which still signs in. Click the password field, then run `osascript`
   with Chrome frontmost: `keystroke (do shell script "op read
   'op://m-valor/Common Login/password'")`. The value is fetched and typed
   inside that one process. If macOS refuses the keystroke (no
   Accessibility permission for the turn's process), stop and report it.
   That is a blocker only Tom can clear at the Mac. Save the login in
   Chrome's password manager when Chrome offers, so later sign-ins
   autofill.
2. **Verification code, with a cap on tries.** Note the UTC time, then
   submit once. If Instagram asks for approval from the iPhone ("Check
   your notifications"), choose "Try another way", then text message.
   Poll `chat.db` (`sqlite3`, read-only) for a message newer than that
   time that carries a 6-digit code in Instagram's wording. If only email
   is offered, read the code from the account's mailbox with `gws gmail`,
   when that mailbox is valor@yuda.me. Wait 3 minutes, request at most one
   resend, wait 3 more, then stop and report. Never submit the password
   twice. Repeated failures are what lock an account.
3. **Right account.** After sign-in, read the handle from the profile
   link or the session, not from `username`. Go on only if it's Valor's
   (expected `valorengels`). If it's another account, or a lock or
   suspicious-login screen appears, stop and report to Tom with a
   screenshot.
4. **cdp_attach_failed.** Diagnose only if it comes back. Likely causes: a
   debugger or DevTools window already attached, or the tab's renderer
   swapping on a cross-site redirect. Fix it where it lives (close the
   debugger, retry with the new tab id). A defect in BYOB itself is named
   in the delivery, not patched here.
5. **On the reel.** Like it, follow amrtawfikai, and use Instagram's
   **Repost** button. For **share to story**, try desktop web, then BYOB
   `browser_emulate_device` as an iPhone. The web has offered no story
   reshare of others' reels, so this is expected to fail. If it does, the
   delivery names share to story as not done, never counts the Repost as
   meeting it, and offers the one route that works: Tom sharing it from
   the iPhone app, or a later task that drives the app on the phone.
6. **Leave it signed in.** Leave the tab open on the reel. Check that the
   session survives closing the tab and reopening instagram.com.
7. **Docs.** Add a paragraph to `docs/browser.md` under "Valor's
   logged-in browser". It says: Instagram is reached through BYOB Chrome,
   signed in as Valor's account. The login is the 1Password item `Common
   Login`. Chrome autofills it, and if it has to be typed, it goes in
   through `osascript` keystroke from `op`, never the clipboard. Codes
   arrive by SMS in Messages' `chat.db` ("Try another way" if the iPhone
   is asked). The web can't share to a story. The paragraph doesn't spell
   out an `op read` command. No memory is written, since the docs
   paragraph is the record.
8. **Tom sees it.** Take BYOB screenshots, saved in the workspace, of the
   reel liked, the creator showing Following, and the repost on Valor's
   profile, plus the story if it was made. Attach them to the delivery.

**Out of scope.** Creating any account. Posting anything beyond the one
reel. Changing BYOB's code or the kernel. Any new check or gate (none is
added).

**Verification (live; there's no code to unit-test).** The reel's like
control reads "Unlike". amrtawfikai's button reads "Following". Valor's
Reposts tab shows the reel. The story shows on Valor's profile, or is
named as not done. The handle read in step 3 is Valor's. A reopened tab
is still signed in. A script that reads the password from `op` and counts
it in this task's transcript files and the workspace prints only `0`.
