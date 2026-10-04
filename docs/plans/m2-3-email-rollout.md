# 2.3 rollout and test window

The rollout and the test window for the email bridge in [m2-3-email.md](m2-3-email.md).


1. `python -m core backup`; merge; pull and `python -m core migrate`.
2. `brew install dovecot` on any machine that runs the suite.
3. `python -m bridges.email keys`; set `VALOR_EMAIL_ADDRESS` and
   `VALOR_EMAIL_SINCE` (the window's date) in the bridge's launchd environment,
   and `VALOR_EMAIL_ADDRESS` in the kernel's too (it fills reply-all and
   measures the message's size); confirm `operator_email` is set.
4. Install the plist from `python -m bridges.email --plist`, unloaded.

Tom has no step before the window: the window tests sends only, and mail
from Tom is recorded and starts nothing.

The test window, with Tom:

1. Disable the email bridge on `main` on the build Mac (`email-disable`)
   and on the Mac whose `projects.json` lists Tom's address (Valor the
   Captain, project cuttlefish today), so no other poller marks his mail
   seen. Load the new bridge.
2. Tom sends a mail from his address with a `Cc` to his second address:
   it is received and recorded, and no task starts. A reply-all a task
   sends in answer to a received mail (by `reply_to`) is held for his tap.
3. Tom sends one mail from another address of his: it is not received
   and stays unseen.
4. A task Tom started on Telegram asks to reply to his mail with a 9 MB
   file; the send is held; Tom taps once; during
   the upload, terminate the `valor-email-perform` backend by its pid.
   The bridge restarts, a sweep settles the effect, and each inbox holds
   at most one copy, with `done` exactly when it holds one.
5. Record the Sent Mail lookup form that works, Gmail's filing delay
   (from the 250 to the message in Sent Mail), the 9 MB upload rate,
   and the bridge's RSS.
6. Unload the new bridge and enable the email bridge on `main` on both
   Macs. Mail in the window belongs to the new system and is not
   replayed; mail between windows stays with `main` because
   `email_since` moves to each window's date.
