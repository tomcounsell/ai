# The browser build `look` runs, on every Mac

**Problem.** `settings.browser` (`VALOR_BROWSER`, `core/settings.py:419`)
names `chromium_headless_shell-1208` in `~/Library/Caches/ms-playwright`.
Observed 2026-11-21 over ssh from the Cowboy:

| Mac | 1208 present | Other headless shells |
|---|---|---|
| Cowboy | yes | 1169 |
| Pirate | yes | none |
| Captain | **no** | 1248, 1254, 1255 |
| Bald | **no** | 1255 |

No Mac's launchd overrides `VALOR_BROWSER`. Project venvs on the Captain and
the Bald run Playwright 1.58.0, which also expects 1208.

**Why it was missing (from the record).** Build 1208 was pinned on
2026-10-03 (2f7e42679), the build already in the Cowboy's cache. No setup
step ever installed it: `docs/rollout.md` step 2 lists no browser, and
Valor's own Playwright (1.64.1 in `uv.lock`) installs build 1248, so even a
`playwright install` from `~/src/ai` would not supply it. The Captain's
listing before the 2026-11-21 cleanup (task 0b976535bda8) already lacked
1208, so the cleanup did not remove it.

## What will be done

1. **Install** on the Captain and the Bald, over ssh, with the same command
   the doc will carry (so the install also tests the doc):
   `PLAYWRIGHT_SKIP_BROWSER_GC=1 /opt/homebrew/bin/uv tool run --from playwright==1.58.0 playwright install chromium-headless-shell`
   (Playwright 1.58.0 pins headless shell 1208; the variable stops the
   install deleting builds it does not name). Nothing is removed.
2. **Confirm on all four Macs**: the resolved path exists and is
   executable, `chrome-headless-shell --headless --no-sandbox --dump-dom
   'data:text/html,<p>ok</p>'` prints the page, and `~/src/ai/tools/look`
   with the kernel's `VALOR_BROWSER` shoots a page from a scratch directory.
3. **Doc fix**: `docs/rollout.md` step 2 adds the install command above, and
   says why it is not Valor's own Playwright (that installs a newer build).
   `docs/browser.md`'s browser paragraph names the same command.

## Out of scope

- Moving the pin to 1248 or deriving it from Valor's Playwright: a code
  change for another day; noted in the delivery.
- Any new check that the build exists (none is added), deleting other builds.

**Stakes.** Low: a cached browser and two doc lines, all reversible.

## Tests

No code changes, so no new test. Evidence is the live checks in step 2 on
each Mac: the path, a headless launch, and a `look` screenshot, including
the Pirate and the Cowboy where it was already present. The doc
command is the one run on the Captain and the Bald.
