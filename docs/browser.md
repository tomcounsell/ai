# The browser: `look`

Serves Mission item 1 ("testing actual use"). A capability for the turn,
never a gate.

**`look`.** The workspace's `bin/` holds `look`, a shell script written there
at each provisioning (to a temporary name, then renamed). `look URL [NAME]
[--size WxH] [--wait MS]` takes `--size` only as two positive integers joined by
`x` (anything else, a zero included, is refused) and asks `/usr/bin/curl` for the page's status first and
exits non-zero when nothing answers; on a 5xx it still renders and keeps the
page, prints the status, and exits non-zero. The browser itself exits 0 on
its own error page. It then runs Playwright's
`chrome-headless-shell` twice, once for the screenshot and once for the
serialized DOM, and writes `.valor/screens/NAME.png` and
`.valor/screens/NAME.html` at the root of the clone, wherever in the clone it
runs, and refuses to run outside a git clone. It prints the two paths, the
status, and a `shasum -a 256` line for each file. `.valor/` is excluded from commits, so
screens never enter one. Interaction (clicks, forms, logins) is not part of it.

**The browser.** `settings.browser` (`VALOR_BROWSER`) names one fixed build,
`chromium_headless_shell-1208` in `~/Library/Caches/ms-playwright/`, never the
newest in the cache. Every turn profile denies writes to that cache, since
the user's own Playwright runs outside any sandbox read it. A turn that
installs Playwright into its own project sets `PLAYWRIGHT_BROWSERS_PATH` to
its task cache.

**Sandbox.** Chromium's own sandbox cannot start inside a turn's sandbox-exec
profile: the GPU process exits with "sandbox initialization failed: Operation
not permitted" and the browser aborts. `look` runs it with `--no-sandbox`.
That is acceptable because the turn's profile already bounds the process:
outbound internet and mach services are open to the turn anyway, loopback is
limited to the dev ports, the gateway, and service ports, and the turn can
load any page with its other tools. No profile line changes for the browser.
Under a fresh session's profile, which denies `/private/tmp` and
`/private/var/folders`, `look` runs with its user data directory under the
session's own tmp.

**What the kernel records.** When it collects a turn, the kernel moves each
entry in `.valor/screens/` to `.valor/handled/<turn_id>/screens/` first, by
directory descriptor and never through a link, so a later turn does not
record it again, and then opens it there without following a link or
blocking (`core/workspace.py`'s `_file_away` and `open_plain_file`, the same
walk as every other signal). It adds `screens` to `turn.collected`:
`{name, bytes}` (the size from `fstat`; the kernel never reads a screen's
contents, so a sparse screen is sized, not refused) for a regular file with
one link, `{name, refused}` with the reason for anything else (a link, a
hard link, a FIFO, a directory). An entry that cannot be moved is removed
unread and recorded as `{name, refused}`. `done.md` names a screen by its original name. The turn
quotes the checksums `look` printed in `done.md`; the kernel records no
checksum, and a screen is as editable as `done.md`.
Screens are evidence, never a gate: nothing requires one and no check reads
one.

**Memory.** Peak resident memory of the browser's process tree over five
renders of a Django admin login page: 330 to 372 MB ([machine.md](machine.md)).

The emulator can score the UI items (#894, #872, #893) once it exists.

Part of the workspace specified in [harnesses.md](harnesses.md).
