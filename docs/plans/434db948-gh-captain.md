# GitHub CLI on the Captain, managed by Homebrew again

Operations task 434db948662c (Tom, 2026-11-21 14:59 UTC: "The captain
machine says it needs brew install gh"). One Mac, one doc line, no code.

## Known (observed 2026-11-21 ~15:50 UTC, over ssh, read only)

- **gh works today, but not Homebrew's.** `/opt/homebrew/bin/gh` is a
  symlink to `~/.local/bin/gh`, a hand-placed official release (2.105.0),
  made 2026-11-19 11:07 UTC; a client session's history says "gh CLI
  reinstalled by Valor 2026-11-19 (official release at ~/.local/bin/gh)".
  Homebrew's own `gh` 2.105.0 is in the Cellar but unlinked.
- **Why it went missing.** The nightly Homebrew job ran twice at once on
  2026-11-19 04:00 Bangkok (11-18 21:00 UTC); duplicated log lines and
  "Another brew update process is already running". One run upgraded gh
  2.104.0 to 2.105.0 and cleaned 2.104.0; the other hit "Bottle
  destination already exists" and left no `bin/gh` link. Turns then had
  no gh from 11-18 ~21:00 to 11-19 11:07 UTC (client tasks said so, and
  asked Tom for `brew install gh`, last on 11-19 11:06).
- **Since then, every nightly fails on it.** The Brewfile lists `gh`; the
  11-21 run says "Could not symlink bin/gh ... already exists" and
  "Installing gh has failed", because the foreign symlink is in the way.
- **Not the 13:50 cleanup.** `brew cleanup --prune=all` removes old
  versions and caches; the Cellar's current 2.105.0 and the
  `~/.local/bin` copy are both intact.
- **Turns' PATH** (workspace.provisioned, 11-21): `~/.local/bin` comes
  before `/opt/homebrew/bin`, so the copy shadows Homebrew's. The turn
  sandbox profile has no rule on gh or `~/.config/gh`; turns sign in with
  Valor's `~/.config/gh/hosts.yml` (docs/machine.md "Valor's own gh login").
- **docs/rollout.md step 2 already installs `gh`** (Homebrew prerequisites).

## Build (through Valor.app's `valor-run`, as 0b976535bda8's `vrun.sh`)

1. `brew link --overwrite gh` (that is what `brew install gh` asks for
   here; the keg is already installed). Move `~/.local/bin/gh` to
   `~/.local/share/valor-attic/gh-2.105.0-manual` so Homebrew's is the one
   on every PATH and is upgraded nightly. Nothing deleted; undo is one `mv`.
2. **Verify in a turn's own environment:** a one-off job with the kernel's
   environment, PATH set to the latest Captain workspace's harness PATH,
   run under `sandbox-exec -f <that workspace>/home/profiles/turn.sb`:
   `command -v gh` resolves to `/opt/homebrew/bin/gh`, `gh --version`,
   `gh auth status` (account `valorengels`), and `gh repo view` of the
   client's monorepo and its backend repo, both readable. If auth fails,
   fix with `github-keys` (docs/rollout.md step 6); if that needs Tom,
   name exactly what.
3. **`brew list gh` and `brew doctor`** show it linked; tonight's run
   should no longer report gh (named as unverified until then).
4. **Stalled work.** On the Captain's ledger, every task or session that
   ended or waited citing missing gh between 11-18 21:00 and 11-19 11:07
   UTC: if since delivered, merged or restarted, leave it, with the
   evidence. Any still unfinished is restarted with `python -m core
   feedback TASK "<gh is back; finish where you were>" --by valor` then
   `python -m core run TASK`. An open session whose wake note still
   carries "brew install gh" as waiting on Tom gets the same hand-on
   notice 0b976535bda8 used (`notices.request`, `about_key`
   `handon:gh-434db948`), saying gh is back so it drops the ask. Nobody
   is messaged directly.
5. **docs/rollout.md step 2:** one clause, that `gh` is Homebrew's,
   linked, and a copy earlier on PATH stops `brew` linking it (2026-11-21).

## Out of scope (named in the delivery)

- The nightly job running twice at once: it lives in the private Valor
  folder and the team app, not this repo. Named with its log evidence.
- The other Macs' gh (not reported missing); 1Password and the other
  failed casks in the same run.

## Tests

No code, so no unit test. Evidence instead: the sandboxed turn-env check
in step 2 (gh path, version, auth, both repos readable), `brew list gh`,
and for each candidate in step 4 the row that shows it handled or a new
`turn.started` after the restart.
