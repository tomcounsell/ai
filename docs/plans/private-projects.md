# Private project specs, and a history without them

Tom, valor team chat 2026-10-29 04:53 UTC: the public repo's `projects/` exposes
his clients and Yudame's work systems. 04:56 UTC, his choice: move the specs out
and rewrite history to drop them and the other client mentions, "slowly in
stages to ensure uptime across machines". This file names no client on purpose.

**Stakes.** The rewrite and force-push of a public `main` cannot be taken back,
and a spec folder a Mac cannot read silences every chat that Mac serves.

## Stages (each live and confirmed on the Cowboy, Pirate, Captain and Bald first)

One task, one merge per stage: stage 1 merges first; each later stage is a
round after the chat session sends back "go" (`merged` takes `feedback`).

1. **Private config.** Copy `projects/*.toml` to `~/Desktop/Valor/projects/`
   (the iCloud folder; Valor.app's Full Disk Access lets every launchd job read
   it, `docs/machine.md`). `projects_dir`'s default becomes that folder;
   `VALOR_PROJECTS` still overrides. No plist or per-Mac env change: the
   default reaches every process (kernel, bridges, ui, routines, turns) at the
   normal update. For this stage only, `Spec.load`/`Spec.all` fall back to the
   repo's `projects/` for any name the folder lacks or cannot list (an
   unsynced or unreadable folder keeps every chat up). Docs: `projects/README.md`
   (where real specs live, how a spec is edited now), `docs/machine.md`,
   `docs/rollout.md` (a new Mac needs the folder synced). Confirm per Mac after
   its update, over ssh: on the new commit, the kernel and bridges running, the
   bridge logs receiving its chats, and the specs served are the folder's (a
   read through Valor.app or the ui, since sshd cannot read `~/Desktop`). Specs
   merged to the repo meanwhile are copied to the folder at confirmation.
2. **Clean the tree.** Remove the real specs; keep `projects/example.toml`
   (made up) and the README. Drop the stage-1 fallback. Replace client names,
   repos, chat/user/channel/team ids, emails, domains and people's names in
   docs, `site/`, `skills/`, tests and comments with made-up ones; tests that
   read real specs read fixtures. The string list (from every historic version
   of the specs, old-system configs included, plus names) is kept privately in
   `~/Desktop/Valor/scrub/`, never in the repo. Merge; confirm each Mac as above.
   Redeploy `site/` if it changed (`scripts/deploy-site.sh`).
3. **Rewrite history.** Install `git-filter-repo`. Backup first: a
   `git clone --mirror` of GitHub (with `refs/pull/*`) as a verified bundle in
   `~/Desktop/Valor/backups/` and a copy on the Cowboy outside iCloud. Filter a
   fresh mirror: drop every historic path of real specs and old project
   configs, `--replace-text` and `--replace-message` from the private list;
   tags (`old-system`) and live branches included. Check: the new `main` tree
   equals the old `main` tree; `git log --all -p` holds no listed string.
   Then **stop and ask** the chat session (`.valor/question.md`) with the list
   of what changes, and force-push only on its go. Cutover with no restart:
   each Mac `git fetch` + `git reset --keep origin/main` (same tree, so the
   update's `--ff-only` works again and nothing reloads); open valor tasks'
   branches rebased by the commit map or restarted; finished workspaces'
   `origin.git`/`kernel.git` stay local. Check `rollout.judge` against
   rewritten shas (old merged shas are no longer ancestors) before the push.
   Update `CLAUDE.md`'s `old-system` sha.
4. **Report what stays exposed**: the 13 forks, `refs/pull/*` and closed PR
   diffs (1,259 PRs), unreachable commits by sha, issue and PR bodies, the
   deployed site, existing clones. Each with a recommendation; what GitHub
   Support must purge, which Tom must request as owner (Valor has push, not
   admin).

## Out of scope

A database home for specs; versioning the private folder; any hook or test
that refuses client names in commits (governance, no grant); editing
other people's forks.

## Tests

Stage 1: default is `~/Desktop/Valor/projects` with `VALOR_PROJECTS` unset and
the env var wins when set; a name in both loads the folder's; a name only in
the repo loads the repo's; a missing or unlistable folder gives every repo
spec; `Spec.all` is the union with the folder winning; intake receives a chat
listed only in the folder's copy, on the machine that copy names.
Stage 2: `Spec.all` reads only `projects_dir`; `projects/example.toml` loads by
path; tests that pinned real specs pass on fixtures. Stage 3 (scripted, not
suite): tree equality, the history scan, `git bundle verify`, `judge` with
rewritten shas (a unit test if the code changes). Full suite each stage.
