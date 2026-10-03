---
tracking: none
slug: m3-browser
type: build
status: built
critique_rounds: 1
review_rounds: 1
---

# 3c: a headless browser in the workspace

Task 3c of milestone 3 of [valor-rebuild.md](valor-rebuild.md). It gives a
workspace turn a way to open the app it built in a headless browser and
keep what it saw: a screenshot and the page's rendered HTML, written under
`.valor/screens/` and named in `done.md`, with the kernel recording each
file's digest when it collects the turn. It serves Mission item 1
("testing actual use") and is a capability for the turn, never a gate: no
stage requires a screenshot and no check reads one.

It is independent of 3a and 3b and works on either harness, since the
browser is a command the turn runs, not a harness feature. It merges after
1.5.

## Stakes

`critique_rounds: 1`, `review_rounds: 1`. The task adds a read-only
command to the workspace's tool directory, one field to `turn.collected`,
a sentence to the build and patch stage text, and a write denial on the
Playwright cache. It adds no effect, no
spend, and no credential. The browser runs inside the turn's own sandbox,
with the turn's reach and no more.

## The Done items it closes

From milestone 3, "a headless browser in the workspace: a turn opens the
app it built and records what it saw".

- **A turn opens its app and records what it saw.** Evidence: one live
  build turn (`VALOR_LIVE=1`) on a provisioned Django workspace that
  starts the dev server on a dev port, runs `look`, and names the
  screenshot in `done.md`; `turn.collected` holds the screenshot's name,
  size, and SHA-256; the build report includes the image, saved at `<scratchpad>/screens/<name>.png` and named in the report.
- **It runs under the turn's sandbox.** Evidence: the offline test below,
  under the real turn profile, and the build report stating whether
  Chromium's own sandbox runs nested inside `sandbox-exec` or needed
  `--no-sandbox`, with the error seen if it did not run.
- **Its memory is measured.** Evidence: the peak resident memory of the
  browser's whole process tree rendering one page of a workspace app,
  measured five times, written into `docs/machine.md` in place of the
  600 MB estimate.

## Threat model

- The browser runs as part of the turn, under the turn's profile, so it
  can do what the turn can: read its workspace, reach the dev ports and
  the open internet, write its own state. It gains nothing the turn lacks.
- A page the turn loads is the turn's own app or anything on the web the
  profile allows. A hostile page can exploit the browser and reach the
  turn's sandbox, which is the reach the turn already has.
- The browser's binary sits in `~/Library/Caches/ms-playwright/`, which
  every Playwright the user runs outside a sandbox also reads. The turn
  profile denies writes there (`HOME_WRITE_DENIED`), and `settings.browser`
  names one fixed build, so a turn can neither replace the binary nor plant
  a newer build for the kernel to pick. The kernel never runs the browser
  itself.
- A turn controls everything under its `.valor/screens/`: names, links,
  hard links, FIFOs. The kernel never follows or blocks on any of it: it
  opens each entry relative to a directory descriptor, refuses anything
  that is not a plain single-link regular file, and records the refusal.
- The browser's own sandbox is off (`--no-sandbox`). A hostile page that
  exploits its renderer then has the browser process's reach, which is the
  turn's reach and no more: the profile is `(allow default)` for the
  internet and mach services, and loopback is limited to the dev ports, the
  gateway, and service ports. The turn already loads any page it likes with
  its own tools. No profile line changes for the browser.
- A screenshot is the turn's own account of what it saw, as editable as
  `done.md`. The digest on `turn.collected` makes a later edit visible; it
  does not make the image true. A verifier who needs to see the page
  opens it again.

## Design

### The browser

Playwright's `chrome-headless-shell` build, already in the machine user's
cache (`~/Library/Caches/ms-playwright/chromium_headless_shell-<build>/`).
`settings.browser` holds the binary's path, default the fixed build
`~/Library/Caches/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-mac-arm64/chrome-headless-shell`
(never "the newest"), overridden by `VALOR_BROWSER`. The cache directory is
added to `HOME_WRITE_DENIED`, so turns read it and cannot write it. A turn
that installs Playwright into its own project points
`PLAYWRIGHT_BROWSERS_PATH` at its `lay.cache`. A missing browser leaves `look`
answering with that reason and a non-zero exit; nothing else in the turn
changes.

### `look` (`tools/look`, written into the workspace's `bin/`)

A POSIX shell script, so it needs no Python or Node of the project's.
`bin/` is the one directory shared by every task under the work root, first
on the turn's `PATH`, read and never written by a turn. Provisioning writes
`look` there once per provisioning, to a temporary name and then renamed
over, so a running turn never runs a half-written script.

```
look URL [NAME] [--size WxH] [--wait MS]
```

- Writes `.valor/screens/NAME.png` (a screenshot at `--size`, default
  1280x800) and `.valor/screens/NAME.html` (the page's serialized DOM after
  scripts ran) in the current clone, NAME defaulting to a timestamp. A NAME
  with a slash, a leading dot, or `..` is refused. `.valor/` is already in
  the clone's `.git/info/exclude`, so screens never enter a commit.
- First asks for the page's status with `/usr/bin/curl -s -o /dev/null -w
  '%{http_code}'`. A `000` (nothing answered) or a `5xx` prints that and
  exits non-zero without starting the browser; the browser's flags report
  no status and exit 0 on its own error page.
- Then runs the browser twice, once with `--screenshot` and once with
  `--dump-dom`, since the two flags are not honoured together in one run.
  Both get `--headless`, `--window-size`, `--virtual-time-budget` (from
  `--wait`, default 3000), a user data directory under the turn's own
  `TMPDIR`, and `--no-first-run --no-default-browser-check
  --disable-extensions --no-sandbox`. `--no-sandbox` is on for the reason
  in the threat model, and the build report states whether Chromium's own
  sandbox runs nested inside `sandbox-exec`.
- Prints the two paths and the HTTP status.

Interaction (clicks, forms, a login) is left out; see below.

### What the kernel records (`core/signals.py`, `core/session.py`)

When it collects a build or patch turn, the kernel lists `.valor/screens/`
and adds `screens: [{name, bytes, sha256} | {name, refused}]` to the
`turn.collected` payload that `core/session.py` builds. `core/signals.py`
reads it into `Signals.screens`.

Other signal files in `signals.py` are today read with `is_file()` and
`read_text()`, which follow links; the separate task `m1-4s-signal-reads`
fixes those and builds a shared safe-read helper. The screens reader here
is a small self-contained function (`read_screens`) written the same way
`workspace.read_verdict` reads: `.valor`, then `screens`, then each entry
opened relative to the directory descriptor with `O_NOFOLLOW | O_NONBLOCK`,
required to be a regular file with `st_nlink == 1`, hashed from the open
descriptor. Anything else (a link, a FIFO, a directory, a hard-linked file)
is recorded as `{name, refused: reason}` and never read. At merge the shared
helper replaces the function's body. Each recorded or refused screen is
moved to `.valor/handled/<turn_id>/screens/`, so a later turn does not
record it again; `done.md` names it by its original name. A screens
directory that is absent or empty records nothing. Screens are evidence,
not signals: they change no state.

### The stage text (`skills/sdlc/build.md`, `skills/sdlc/patch.md`)

One sentence in each: when the work changes what a page shows, start the
dev server on a port from 8000 to 8009, run `look` on the page, look at
the screenshot, and name it in `done.md` with what you saw (the kernel files recorded screens away after the turn, so a later turn finds none). The text says
what the tool is for; no check reads whether it was used.

## Tech debt absorbed

- `docs/harnesses.md` "Testing actual use: a browser" names the browser as
  a gap and an unmeasured design. It is rewritten to describe `look`, its
  sandbox result, and its memory.
- `docs/machine.md` carries the headless browser as "not measured" with a
  600 MB estimate, and lists it among the measurements to make. The
  measured figure replaces both, and the peak total is recomputed.

## Left out

- Driving a page: clicks, typing, logins, multi-step flows. A turn that
  needs them can install Playwright into its own project cache; a kernel
  tool for it waits until a UI item shows `look` is not enough.
- Firefox and WebKit.
- Copying screens out of the workspace into the document store; that
  belongs with the session file copy `docs/harnesses.md` designs.
- The emulator's grades on the UI items (#894, #872, #893); that is 1.5's.

## Tests

`tests/test_look.py`, real binaries, no model, `pytest.mark.spend(usd=0)`:

- `look` under a real turn profile against a tiny local HTTP server bound
  to a dev port writes a PNG (its header and the requested size read from
  the file) and an `.html` file holding the page's text, and exits 0.
- The same against a dev port with nothing listening exits non-zero and
  says so.
- A page that sets its text after a 1 second timer: with `--wait 2000`
  the html file holds it.
- A dev port that answers 500 exits non-zero.
- A turn profile write to a file under `~/Library/Caches/ms-playwright` is
  denied; `settings.browser` is the fixed build path.
- A NAME with a slash or `..` is refused; screens land only under
  `.valor/screens/`.
- `look` under the fresh session's profile (no `/private/tmp`) still runs,
  its user data directory under the session's own tmp, and the browser
  touches no path under `/private/var/folders` (checked with `fs_usage` or
  the profile's denial log).
- With `VALOR_BROWSER` pointing at a missing path, `look` exits non-zero
  with the reason.
- Collection: a scripted turn (`tests/scripted.py`) that runs `look` and
  writes `done.md` produces `turn.collected` with one screens entry whose
  digest matches the file; a screen that is a symlink to a file outside the
  clone, a hard link, and a FIFO are each recorded as refused, never read
  and never blocking; recorded screens are moved to
  `handled/<turn_id>/screens/` and the next turn records none.
- Memory: `tests/test_look.py::test_memory` (marked to run only with
  `VALOR_MEASURE=1`) samples the browser's process tree with `/bin/ps`
  every 50 ms over five renders of a provisioned Django app's admin login
  page and prints the peak; the build copies it into `docs/machine.md`.

Live (`VALOR_LIVE=1`, `pytest.mark.spend(usd=1.00)`): a build turn on a
provisioned Django workspace whose plan is a one-line template change;
the test asserts a screens entry on `turn.collected` and that `done.md`
names it.

## Files it changes

- `tools/look` (new script).
- `core/workspace.py` (write `look` into the work root's `bin/` at
  provisioning, atomically; `settings.browser` passed into the turn's
  environment as `VALOR_BROWSER`; `Library/Caches/ms-playwright` in
  `HOME_WRITE_DENIED`).
- `core/settings.py` (`browser`).
- `core/signals.py` (`read_screens`, `Signals.screens`).
- `core/session.py` (the `screens` field of the `turn.collected` payload).
- `skills/sdlc/build.md`, `skills/sdlc/patch.md`.
- `tests/test_look.py`, `tests/scripted.py` (a step that runs a command).
- `docs/harnesses.md`, `docs/machine.md`, `docs/architecture.md` (the
  turn record table's `turn.collected` fields).

3b also changes `core/workspace.py`, `core/settings.py`, and
`docs/harnesses.md`; whichever merges later rebases.

## Expected spend, as information

The offline tests spend nothing. The live build turn is about a dollar.

## Rollout

1. None on the machine beyond merge: the browser is in the cache and
   provisioning copies `look` into each workspace's `bin/`.
2. The build session reports the memory figure, the sandbox finding, and
   the live turn's screenshot.

## Questions for Tom

None. The task touches no identity or credential.

## Decided by default

- **Playwright's headless shell from the existing cache.** It is already
  installed, matches what Playwright drives if a turn installs it, and
  needs no new download.
- **A shell script over a Playwright program.** Viewing a page needs only
  the browser's own flags, and a script runs in every project kind.
- **Screens in `.valor/screens/`.** It is the turn's existing channel to
  the kernel, excluded from commits, and readable by the kernel at
  collection.
- **Digests recorded, images not copied.** The digest shows whether a
  screen changed; copying waits for the document store's session copy.
- **The stage text mentions `look` without requiring it.** A screenshot is
  evidence the turn chooses to give.

## Critique round 1 (of 1): revise

The critique's verdict was revise; the round is spent, and every finding is
built in.

1. **Screens were said to be read without following links, as other signal
   files are; they are followed.** Fixed: `read_screens` opens everything
   by directory descriptor with `O_NOFOLLOW | O_NONBLOCK`, requires a
   regular file with one link, and records `{name, refused}` otherwise. The
   sentence about other signal files is corrected; their fix and a shared
   helper belong to `m1-4s-signal-reads`, and `read_screens` is written to
   be replaced by that helper at merge.
2. **The Playwright cache is shared with the user's unsandboxed runs.**
   Fixed: `Library/Caches/ms-playwright` joins `HOME_WRITE_DENIED`, and
   `settings.browser` names build 1208 rather than the newest.
3. **Browser flags give no status and exit 0 on errors.** Fixed: `curl`
   first for the status, two browser runs, output named `NAME.html`.
4. **`--no-sandbox` was undecided.** Decided: on, for the reason in the
   threat model; no profile line changes.
5. **Screens recorded again by every later turn.** Fixed: each recorded
   screen moves to `handled/<turn_id>/screens/`.
6. **Smaller errors.** `core/session.py` added to the files changed;
   `bin/` is shared and written atomically; the fresh-profile test asserts
   no `/private/var/folders` path.
7. **Done evidence.** The report names where the screenshot is saved.

## Build record

- **Built to the plan, every named test.** `tests/test_look.py` has 18
  offline tests that run, and two that need an opt-in: `test_memory`
  (`VALOR_MEASURE=1`) and the live build turn (`VALOR_LIVE=1`, about one
  dollar). The live turn is written and has not been run.
- **Offline tests use ports 6451 to 6459.** The local server binds one of
  them and the profile is told it as a service port, because the dev ports
  may be in use on a shared machine; the profile's loopback rule is the
  same for either.
- **Sandbox finding.** Chromium's own sandbox cannot start inside the turn
  profile: the GPU process exits with "sandbox initialization failed:
  Operation not permitted" and the browser aborts. `look` passes
  `--no-sandbox`; the reason is in the threat model and `docs/harnesses.md`.
  Under the turn profile and the fresh session's profile both runs
  succeed, and the fresh run prints no `/private/var/folders` path.
- **Memory.** Five renders of a Django admin login page (Django served on
  a loopback port, one `look` per render, process tree sampled by
  `/bin/ps` every 50 ms): peaks of 372, 343, 344, 330, and 343 MB. Each
  render covers both browser runs. `docs/machine.md` now holds 372 MB and a
  peak total of 10,202 MB.
- **`read_screens` is replaceable.** It is one function in
  `core/signals.py` with no other reader of `signals.py` touched; the
  shared safe-read helper from `m1-4s-signal-reads` replaces its body at
  merge.
- **Rebase note.** 3b also edits `core/workspace.py`, `core/settings.py`,
  and `docs/harnesses.md`; whichever merges later rebases.
