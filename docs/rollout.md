# Rolling Valor out to a Mac

How a Mac gets the kernel: a fresh one, or one still running the old
system, whose retirement is part of the same rollout. Each Mac serves one
client's projects ([machine.md](machine.md)). The Cowboy went first
(2026-10-14); its cutover left the old system's files and jobs in place,
which the 2026-10-16 sweep retired with step 8 below.

**Order.** The Pirate next, then the Captain and the Bald. Those two serve
customers and teams and stay on the old system until the new one is shown
a valid replacement. The bar, Tom's words (2026-10-16): "a successful
teamwork session in a group telegram chat, where Valor acts properly
according to his persona and accomplishes tasks with Tom and the team. The
Eng: PsyOPTIMAL group is a great example of how well Valor has been working
with the team for several months. The rebuild has to be as good if not
better than that." Building to it is [plans/team-chat.md](plans/team-chat.md). Until then their old updater is harmless: `main` was rewritten, so
its fast-forward fails and it keeps running its own code (the Pirate's
`logs/update.log`, 395 aborted fast-forwards).

Run from the Mac (or over SSH as `valorengels`), in `bash`:

```
export PATH=/opt/homebrew/bin:/opt/homebrew/opt/postgresql@18/bin:$HOME/.local/bin:$PATH
export PGPASSFILE=~/.config/valor-kernel/pgpass VALOR_MACHINE="Valor the Pirate"   # this Mac's name
D=gui/$(id -u); LA=~/Library/LaunchAgents; PY=.venv/bin/python
OLDPY=~/src/ai/.venv/bin/python   # the old system's interpreter, before step 3
CUT=~/valor-retired/cutover-$(date +%F); mkdir -p $CUT
```

## Before the day

- **The Mac's projects have specs** in `projects/`, each with `machine` set
  to this Mac's name and `chats` taken from the old system's routing
  (`~/Desktop/Valor/projects.json`; over SSH its copy
  `~/src/ai/data/projects.last_known_good.json`), merged to `main`. A chat
  the old system read and no spec names is a chat nobody reads after step 7.
- **Valor can push** to each repo: `gh api repos/OWNER/REPO --jq .permissions.push`.

## Steps

1. **Stop the old updater** first: it pulls `main` every 30 minutes, and
   `main` is now the kernel.
   `launchctl disable $D/com.valor.update; launchctl bootout $D/com.valor.update`
2. **Prerequisites.** Apple's Command Line Tools (the kernel runs the
   root-owned `/Library/Developer/CommandLineTools/usr/bin/git`); Homebrew
   `postgresql@18` as a service (on 5432, or on another port named by
   `VALOR_PGPORT` in every job and the shell profile when 5432 is taken);
   `uv`, `node`, `gh`, `redis` (tasks start their own servers), `dovecot`
   and `openssl` (the email tests only); `claude`, `codex`, and
   `npm install -g @earendil-works/pi-coding-agent@1.0.0` (the pin in
   `harnesses/pi.py`); `sentry-cli`, `officecli`, `gws`; byob for the
   browser; `ffmpeg` (the voice process decodes and encodes audio with it).
3. **The checkout.** The kernel runs from `~/src/ai`. On a Mac with the old
   system there: `git -C ~/src/ai branch retired/old-main`, then
   `cd ~/src/ai && git fetch origin && git checkout -B main origin/main && uv sync --frozen`.
   While the old system still serves, the kernel can instead be readied in
   a clone beside it, `~/src/ai-kernel-staging`, and steps 4 to 6 and 10's
   suite run from there; after step 7 it is swapped in with
   `mv ~/src/ai ~/src/ai-old && mv ~/src/ai-kernel-staging ~/src/ai && cd ~/src/ai && git pull --ff-only && uv sync --frozen`,
   and step 8 is given `~/src/ai-old`.
   Never commit in it afterwards: a rollout fast-forwards it.
4. **The kernel database.** `$PY -m core migrate` makes the roles, the
   database and the schema, and runs `secure-login` (`pgpass`, the
   `pg_hba.conf` rules). Add `export PGPASSFILE=~/.config/valor-kernel/pgpass`
   to the shell profile.
5. **Keys**, copied from the Cowboy without printing them:
   `for K in claude-token github-keys judgement-keys openai-key mail-keys telegram-keys slack-keys; do ssh valorengels@valor-the-cowboy cat .config/valor-kernel/$K > ~/.config/valor-kernel/$K; chmod 600 ~/.config/valor-kernel/$K; done`.
   Calibrate the judge from a copy of the Cowboy's whole `~/src/valor-demo/items/`
   (the judgement file names case files beside it):
   `$PY -m core calibrate <items>/judgement/intake.underspecified.json`
   (its `task_sha256` equals `JUDGE.calibrated` in `core/judgement_tasks.py`).
6. **Valor's accounts for turns** (rule 1): `~/.config/gh/hosts.yml` from
   `github-keys` and `gh auth setup-git`; `~/.sentryclirc` with `org=yudame`;
   `claude plugin marketplace add yudame/skills`, then
   `claude plugin install yudame@yudame` and `yudame-dev@yudame`. Not
   `install.sh`: turns take the skills from the plugins, and a second copy
   would load each one twice.
   Valor's Google sign-in for `gws` ([machine.md](machine.md)), copied
   from the Cowboy over any old sign-in on this Mac, since an old
   encrypted credential is loaded ahead of the copy (checked 2026-10-17)
   and is dead: `gws auth logout`
   (it keeps `client_secret.json`), then
   `for F in credentials.json client_secret.json; do ssh valorengels@valor-the-cowboy cat .config/gws/$F > ~/.config/gws/$F; chmod 600 ~/.config/gws/$F; done`;
   `gws gmail users getProfile --params '{"userId":"me"}'` names valor@yuda.me.
   To sign in afresh (on the Cowboy, or after a revocation), Valor needs
   no one at the screen: `gws auth logout`, then
   `gws auth login --scopes https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/calendar,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/presentations,https://www.googleapis.com/auth/tasks,openid,https://www.googleapis.com/auth/userinfo.email`,
   run in the background, and its printed URL opened and approved as
   valor@yuda.me in byob Chrome (choose the account, Continue, Allow; byob's
   auth-domain flag must be set, [browser.md](browser.md)), then
   `gws auth export --unmasked > ~/.config/gws/credentials.json && chmod 600 ~/.config/gws/credentials.json && rm ~/.config/gws/credentials.enc`.
   Never `--full` or the default scopes: they carry Cloud scopes, and
   Yudame's Cloud session control then asks for the sign-in again.
7. **The handover**, when the old system is there:
   - The old memory, exported while its Redis is up:
     `$OLDPY -I scripts/cutover_export_memory.py $CUT/memory.jsonl` (`PYTHONDONTWRITEBYTECODE=1`).
   - Its bridges and worker stopped:
     `for L in com.valor.bridge-watchdog com.valor.worker-watchdog com.valor.bridge com.valor.email-bridge com.valor.worker com.valor.reflection-worker; do launchctl disable $D/$L; launchctl bootout $D/$L; done`.
   - The newest message it handled per chat, as the new bridge's start
     (`~/.config/valor-kernel/telegram-seen.json`, mode 600, keeping the
     larger mark): `$OLDPY -I scripts/cutover_seed_seen.py CHAT_ID...`.
   - Valor's Telegram account signed into the new bridge's own session
     (a copied session breaks the account's key): **Tom** runs
     `$PY -m bridges.telegram login` and types the code and password.
8. **Retire the old system:** `scripts/retire_old.sh [OLD_CLONE...]`. It
   stops and removes the old jobs, the old Redis, the old Claude Code
   hooks, skills, agents, commands and memory MCP server, the old
   command-line tools and caches, the old data in the checkout and in any
   old clone kept aside, and the container runtime's images. Data goes to
   `~/valor-retired/<date>`, never deleted. It leaves the caffeinate,
   brew-nightly and vault-sync jobs, `~/.zshenv`'s loader for
   `~/Desktop/Valor/zshenv.sh`, and other Postgres versions, which are the
   machine's, not the old system's.
9. **The jobs.** First the voice models, once per Mac (about 1 GB:
   Parakeet into `~/.cache/huggingface`, Kokoro, which the present
   skill's `tts.py` speaks with, into `~/.cache/kokoro-onnx`; Handy's own
   models folder is not used): `$PY -m tools.voice fetch`.
   Then for each of `core serve`, `bridges.telegram`, `bridges.email`
   (with `VALOR_EMAIL_SINCE` the rollout date), `bridges.slack` if the Mac
   reads Slack, `tools.voice` (`com.valor.voice`, which transcribes every
   bridge's audio; about 1.7 GB resident once used), `core backup` (into `~/valor-backups`, made first, or `VALOR_BACKUP_DIR` on an external disk),
   and `core routine NAME` for each routine: `$PY -m ... --plist > $LA/<label>.plist`
   with `VALOR_MACHINE` set, then `launchctl enable $D/<label>; launchctl bootstrap $D $LA/<label>.plist`.
   Every job's `PATH` includes `/opt/homebrew/bin`: the `core serve` and
   `core routine` plists print it (`routines.launchd_path`: the system
   directories, then those of `claude`, `git`, Postgres and `uv`), and the
   `tools.voice` plist adds ffmpeg's; add it by hand to the others. A
   rollout restarts the bridges but not `com.valor.voice`: when
   `tools/voice.py` changed, `launchctl kickstart -k $D/com.valor.voice`. A plist edited on disk takes effect only after
   `launchctl bootout $D/<label>` and a fresh `bootstrap`. On a Mac that
   reads Slack, the app at api.slack.com carries the manifest's user scopes
   (`python -m bridges.slack manifest`); when it gained one (`search:read`
   for `valor-slack read --search`), reinstall the app to Valor's account
   and run `python -m bridges.slack keys` if the user token changed.
10. **Check.** In a separate worktree, `VALOR_TEST_DB=<name> uv run pytest -q -n auto -p no:cacheprovider`
    passes; `$PY -m core settings` names the machine and the backup folder;
    `$PY -m core routines` lists the routines; a message in each chat the
    specs name gets 👀; a voice note sent to Valor reaches its session with
    a `transcript:` line and is answered with one that plays; `launchctl list | grep com.valor` lists only the
    jobs of step 9 and caffeinate and brew-nightly.

A Mac with no Telegram session or mailbox can run with only the local
chat bridge ([bridges/local.md](bridges/local.md)).
