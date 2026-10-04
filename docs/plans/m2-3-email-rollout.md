# 2.3 rollout and test window

The rollout and the test window for the email bridge in [m2-3-email.md](m2-3-email.md).


1. `python -m core backup`; merge; pull and `python -m core migrate`.
2. `brew install dovecot` on any machine that runs the suite.
3. `python -m bridges.email keys`; set `email_address` and `email_since`
   (the window's date) in the bridge's launchd environment, and confirm
   `operator_email` is set.
4. Install the plist from `python -m bridges.email --plist`, unloaded.

Before the window, Tom's steps (the builder never logs in to the
mailbox):

1. Tom opens "Show original" on one mail he sent to Valor's address and
   reports its topmost `Authentication-Results` line. Both addresses are
   on one Google Workspace domain, so this shows whether such mail
   carries a `dmarc=` result at all. If it carries none even after step
   2, how email proves Tom is an identity question for him, and the
   window waits.
2. Tom publishes a DMARC record for `yuda.me` (none exists today; for
   example `v=DMARC1; p=none`), and the Google DKIM key from the Admin
   console as `google._domainkey.yuda.me`, a second aligned path besides
   SPF. `dig +short TXT _dmarc.yuda.me` shows the record.

The test window, with Tom:

1. Disable the email bridge on `main` on the build Mac (`email-disable`)
   and on the Mac whose `projects.json` lists Tom's address (Valor the
   Captain, project cuttlefish today), so no other poller marks his mail
   seen. Load the new bridge.
2. Tom sends a request from his address with a `Cc` to his second
   address: one task starts, its spending shown.
3. Tom sends one mail from another address of his: it is not received
   and stays unseen.
4. The task's reply carrying a 9 MB file is held; Tom taps once; during
   the upload, terminate the `valor-email-perform` backend by its pid.
   The bridge restarts, a sweep settles the effect, and each inbox holds
   at most one copy, with `done` exactly when it holds one.
5. Record the Sent Mail lookup form that works, Gmail's filing delay
   (from the 250 to the message in Sent Mail), the 9 MB upload rate,
   and the bridge's RSS. Note for Tom that the
   `email.dmarc` guard rarely fires and its expiry tap decides whether
   email can start work.
6. Unload the new bridge and enable the email bridge on `main` on both
   Macs. Mail in the window belongs to the new system and is not
   replayed; mail between windows stays with `main` because
   `email_since` moves to each window's date.
