# Instagram from the Mac

**Ask.** Tom, Telegram DM: 2026-10-24 14:57 UTC (11722-11723) "Go like and
repost and follow" https://www.instagram.com/reel/De4PqnkDUzN/ ; 2026-10-25
11:06 (11725) "You already have an Instagram but you've only ever used on
your iPhone"; 11:34 (11727) "use the Common Login in m-vault. And sms
verification can be pulled from iMessage". Done when the reel shows as
liked, shared to Valor's story, amrtawfikai is followed, and Tom sees it.

**Premise, checked on the Cowboy (2026-10-25).** `op item get "Common
Login" --vault m-valor` answers, with fields `username`, `password`,
`notesPlain` and no URL. `~/Library/Messages/chat.db` is readable from a
turn (719 messages, newest 2026-10-24 18:05 UTC). BYOB answers
`browser_list_tabs`; no Instagram tab is open, so the 2026-10-25
`cdp_attach_failed` can't be reproduced yet.

## What is done

1. **Sign in, password never in a transcript.** Open
   instagram.com/accounts/login in BYOB Chrome (explicit `tabId`). Type
   the username with `browser_type`. It's an account name, not a secret.
   The password goes in through the clipboard: `op read
   "op://m-valor/Common Login/password" | pbcopy` (nothing printed), click
   the password field, paste it (BYOB `press_key` Meta+V; if BYOB's key
   events don't paste, `osascript` Cmd+V to Chrome), then `pbcopy
   </dev/null`. If neither pastes, use `osascript` keystroke with the
   value piped from `op` on stdin. Don't use eval/fetch to localhost:
   Instagram's CSP blocks it.
2. **SMS code.** Note the UTC time just before submitting. Poll `chat.db`
   (read-only `sqlite3`) for the first message newer than that whose text
   carries a 6-digit code and Instagram's wording. Type the code. The code
   itself may appear in the transcript, since it's single-use. Take
   "Save login info" and Chrome's offer to save the password.
3. **Right account.** After sign-in, read the profile handle. Go on only
   if it's Valor's (expected `valorengels`). If it lands on another
   account, or Instagram shows a suspicious-login or lock screen, stop and
   report it to Tom with a screenshot.
4. **cdp_attach_failed.** Diagnose only if it comes back. Likely causes: a
   DevTools window or another debugger already attached to the tab, or
   the tab's renderer swapping on a cross-site redirect. Fix the cause
   where it lives (close the debugger, retry on the new tab id). A defect
   in BYOB itself is named in the delivery, not patched here.
5. **On the reel.** Like it. Follow amrtawfikai. Use Instagram's
   **Repost** button if the web shows it. For **share to story**, try
   desktop web first. If desktop has no story option (web lacks reshare
   to story today), try BYOB `browser_emulate_device` as an iPhone. If
   neither has it, the delivery says so plainly, with what was done
   instead. The bar isn't rewritten.
6. **Leave it signed in.** Leave the tab open on the reel. Check that the
   session survives closing the tab and reopening instagram.com.
7. **Docs.** Add a paragraph to `docs/browser.md` under "Valor's
   logged-in browser": Instagram is reached through BYOB Chrome, signed in
   as Valor's account. The login is `Common Login` in m-valor, filled
   through the clipboard. SMS codes come from Messages' `chat.db`. Say
   what the web can't do (story reshare, if confirmed). Add one memory
   with the same pointer.
8. **Tom sees it.** Take screenshots (`.valor/screens`, or BYOB
   `browser_screenshot` saved in the workspace) of the reel liked, the
   creator Following, and the story (or the Reposts tab). Attach them to
   the delivery.

**Out of scope.** Creating any account. Posting anything beyond the one
reel. Changing BYOB's code or the kernel. Any new check or gate (none is
added).

**Verification (live; there's no code to unit-test).** The reel's like
control reads "Unlike". amrtawfikai's button reads "Following". Valor's
profile shows the story, or the repost in its Reposts tab. The handle
read in step 3 is Valor's. A reopened tab is still signed in. A script
that reads the password from `op` and counts its occurrences in this
task's transcript files prints `0`, and prints nothing else.
