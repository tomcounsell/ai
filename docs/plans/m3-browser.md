---
tracking: none
slug: m3-browser
type: build
status: planned
critique_rounds: 1
review_rounds: 1
---

# 3c: a headless browser in the workspace

Task 3c of milestone 3 of [valor-rebuild.md](valor-rebuild.md). It gives a
workspace turn a way to open the app it built in a headless browser and
keep what it saw: a screenshot and the page's rendered text, written under
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
and a sentence to the build and patch stage text. It adds no effect, no
spend, and no credential. The browser runs inside the turn's own sandbox,
with the turn's reach and no more.

## The Done items it closes

From milestone 3, "a headless browser in the workspace: a turn opens the
app it built and records what it saw".

- **A turn opens its app and records what it saw.** Evidence: one live
  build turn (`VALOR_LIVE=1`) on a provisioned Django workspace that
  starts the dev server on a dev port, runs `look`, and names the
  screenshot in `done.md`; `turn.collected` holds the screenshot's name,
  size, and SHA-256; the build report includes the image.
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
- The browser's binary sits in the machine user's cache, which the user
  can write and the turn profile does not deny writing. A turn that
  replaces it affects only later turns that run it, inside their own
  sandboxes; the kernel never runs it outside one.
- A screenshot is the turn's own account of what it saw, as editable as
  `done.md`. The digest on `turn.collected` makes a later edit visible; it
  does not make the image true. A verifier who needs to see the page
  opens it again.

## Design

### The browser

Playwright's `chrome-headless-shell` build, already in the machine user's
cache (`~/Library/Caches/ms-playwright/chromium_headless_shell-<build>/`).
`settings.browser` holds the binary's path, default the newest build in
that cache, overridden by `VALOR_BROWSER`. A missing browser leaves `look`
answering with that reason and a non-zero exit; nothing else in the turn
changes.

### `look` (`tools/look`, copied into the workspace's `bin/`)

A POSIX shell script, so it needs no Python or Node of the project's.
Provisioning copies it into the work root's `bin/`, which every turn and
fresh profile reads and none writes, and which is first on the turn's
`PATH`.

```
look URL [NAME] [--size WxH] [--wait MS]
```

- Writes `.valor/screens/NAME.png` (a screenshot at `--size`, default
  1280x800) and `.valor/screens/NAME.txt` (the rendered DOM's text) in the
  current clone, NAME defaulting to a timestamp. `.valor/` is already in
  the clone's `.git/info/exclude`, so screens never enter a commit.
- Runs the browser with `--headless`, `--screenshot`, `--dump-dom`,
  `--window-size`, `--virtual-time-budget` (from `--wait`, default 3000),
  a user data directory under the turn's own `TMPDIR`, and
  `--no-first-run --no-default-browser-check --disable-extensions`. If the
  build finds Chromium's sandbox cannot start nested, `--no-sandbox` is
  added, with the reason in a comment and in `docs/harnesses.md`.
- Prints the two paths and the page's HTTP status, and exits non-zero if
  the page did not load, so the turn sees a dead dev server as a failure.

Interaction (clicks, forms, a login) is left out; see below.

### What the kernel records (`core/signals.py`)

When it collects a build or patch turn, the kernel lists
`.valor/screens/` and adds `screens: [{name, bytes, sha256}]` to
`turn.collected`. Files are opened without following links, as other
signal files are. A screens directory that is absent or empty records
nothing. Screens are evidence, not signals: they change no state.

### The stage text (`skills/sdlc/build.md`, `skills/sdlc/patch.md`)

One sentence in each: when the work changes what a page shows, start the
dev server on a port from 8000 to 8009, run `look` on the page, look at
the screenshot, and name it in `done.md` with what you saw. The text says
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
  the file) and a text file holding the page's text, and exits 0.
- The same against a dev port with nothing listening exits non-zero and
  says so.
- A page that sets its text after a 1 second timer: with `--wait 2000`
  the text file holds it.
- A NAME with a slash or `..` is refused; screens land only under
  `.valor/screens/`.
- `look` under the fresh session's profile (no `/private/tmp`) still runs,
  its user data directory under the session's own tmp.
- With `VALOR_BROWSER` pointing at a missing path, `look` exits non-zero
  with the reason.
- Collection: a scripted turn (`tests/scripted.py`) that runs `look` and
  writes `done.md` produces `turn.collected` with one screens entry whose
  digest matches the file; a screen that is a link to a file outside the
  clone is not followed and is recorded as refused.
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
- `core/workspace.py` (copy `look` into the work root's `bin/` at
  provisioning; `settings.browser` passed into the turn's environment as
  `VALOR_BROWSER`).
- `core/settings.py` (`browser`).
- `core/signals.py` (the screens list on `turn.collected`).
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
