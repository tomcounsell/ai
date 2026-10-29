# Private project specs, and a history without them

Tom, valor team chat 2026-10-29 04:53 UTC: the public repo's `projects/` exposes
his clients and Yudame's work systems. 04:56 UTC, his choice: move the specs out
and rewrite history to drop them and the other client mentions, "slowly in
stages to ensure uptime across machines". This file names no client on purpose.

**Stakes.** The rewrite and force-push of a public repo cannot be taken back,
and a spec folder a Mac cannot read silences every chat that Mac serves.

## Stages (each live and confirmed on the Cowboy, Pirate, Captain and Bald first)

Stages 1 and 2 are merges of this task; stage 2 is a round after the chat
session says "go" (`merged` takes `feedback`). Stage 3 is no merge (below).

1. **Private config.** Copy `projects/*.toml` to `~/Desktop/Valor/projects/`
   (iCloud; Valor.app's Full Disk Access lets every launchd job and turn read
   it). `projects_dir`'s default becomes that folder; `VALOR_PROJECTS` still
   overrides. No plist change: the default reaches kernel, bridges, ui,
   routines and turns at the normal update. For this stage only, `Spec.load`/
   `Spec.all` fall back to the repo's `projects/` for a name the folder lacks
   or cannot list, only when `projects_dir` is the default (`VALOR_PROJECTS`
   unset); a configured folder sees no repo spec. `tests/conftest.py` sets
   `VALOR_PROJECTS` to a per-run temp dir, so the suite never reads or writes
   the private folder. **Editing a spec from now on:** a turn edits
   `~/Desktop/Valor/projects/NAME.toml` directly and puts the diff in its
   delivery; there is no merge or review of a spec any more (Tom sees this in
   the stage-1 report). **Over ssh:** sshd cannot read `~/Desktop`, so a
   spec-reading command (`core start --project`, status) runs on a Mac through
   a chat or a turn, not ssh; `projects/README.md`, `docs/machine.md` and
   `docs/rollout.md` say so, and a new Mac needs the folder synced and Full
   Disk Access before its bridges serve. Confirm per Mac after its update: on
   the new commit, kernel and bridges up, bridge logs receiving its chats, and
   a turn on that Mac lists the folder's specs. Specs merged meanwhile are
   copied to the folder at confirmation.
2. **Clean the tree.** Remove the real specs; keep `projects/example.toml`
   (made up) and the README. Drop the fallback; an unreadable or missing
   folder raises an error naming the folder, not "no such project". Replace
   private client identifiers in docs, `site/`, `skills/`, tests and comments
   with made-up ones; tests that read real specs read fixtures. **The list**
   (kept in `~/Desktop/Valor/scrub/`, never the repo) holds only: client
   company and people's names, private repo slugs, chat/user/channel/team
   ids, client emails and domains, and Tom's personal Telegram and Slack user
   ids and the Yudame Slack team id (tests and docs use made-up ones), each
   as a literal or anchored pattern. Rule: everything on the list is gone
   from the tree before the filter runs, so the filter cannot change `main`'s
   tree. It excludes Valor's and Yudame's public identity (`valor@yuda.me`, Yudame,
   `yuda.me`), Tom's public repos (`tomcounsell/ai`, `tomcounsell/popoto`,
   `yudame/skills`, `yudame/yuda.me`), Tom's and Valor's names, and bare
   common words (a spec named after one is matched only in its client
   context). Merge; confirm each Mac. Redeploy `site/` if it changed.
3. **Rewrite history.** Install `git-filter-repo`. **Backup:** `git clone
   --mirror` of GitHub with `refs/pull/*`, recording each kept ref's
   pre-filter sha, as a verified bundle in
   `~/Desktop/Valor/backups/` and a copy on the Cowboy outside iCloud.
   **Refs:** GitHub has 681 branches, the tag `old-system`, and 3 open PRs
   (#3670, #3656, #3593, all same-repo). Kept and rewritten: `main`, the 3
   PR heads, `old-system`, any branch of a running task. Deleted: every other
   branch (merged or stale). **Filter** a fresh mirror: drop every historic
   path of real specs and old project configs; `--replace-text` and
   `--replace-message` from the list. Check filtered `main`'s tree equals
   today's; then commit `CLAUDE.md`'s new `old-system` sha onto filtered
   `main` in the mirror. **Stop and ask** the chat session
   (`.valor/question.md`): what is rewritten, the deleted branch list, each
   kept ref's pre-filter sha, the backup's place. On go, at a quiet moment: no Valor task merging and no
   merge outcome without `rollout.ended` on any Mac (`serve.MERGE_OUTCOMES`);
   push each kept ref with `--force-with-lease=<ref>:<pre-filter sha>`, and
   delete the rest the same way; a lease failure (something landed since the
   clone) means re-clone and re-filter, never a retry. Then at once, each
   Mac: `git fetch` + `git reset --keep origin/main` (only `CLAUDE.md`
   differs, nothing reloads), then one `/update` per Mac. The running
   kernel's `started` is still the old sha, so that update ends `done`,
   "rolled out with no restart" (the diff is a quiet root `.md`), and so does
   each later one until the kernel next restarts; no Mac runs `git gc
   --prune` on its kernel checkout before then, since judge and prepare read
   the old `started` sha's objects. **After:** `git ls-remote` shows only the kept refs, each
   reaching only rewritten commits; a fresh clone of everything GitHub
   serves (`--mirror`) is scanned with the list and finds nothing.
   **This task afterwards:** its own origin and mirror hold old history and
   refuse a non-fast-forward (`core/workspace.py:769`, `core/git.py:564`), so
   it merges nothing after the push; stage 4 is its delivery
   (`.valor/stop.md`). Any other open task on old history gets its merge
   refused in the same way, rather than re-pushing it (branch pushes go only
   to its local bare origin, `core/workspace.py:824`); such tasks are listed
   and restarted on the new `main`.
4. **Report what stays exposed**: the 13 forks, `refs/pull/*` and closed PR
   diffs (1,259 PRs), commits reachable by old sha, issue and PR bodies, the
   deployed site, existing clones. Each with a recommendation, and what GitHub
   Support must purge, which Tom must ask for as owner (Valor has push only).

## Out of scope

A database home for specs; versioning the private folder; any hook or test
that refuses client names in commits (governance, no grant); others' forks.

## Tests

Stage 1: default is `~/Desktop/Valor/projects` unset, `VALOR_PROJECTS` wins
when set; a name in both loads the folder's; a name only in the repo loads the
repo's; an unlistable folder gives every repo spec; `Spec.all` is the union,
folder winning; intake receives a chat listed only in the folder's copy, on the
machine that copy names; a configured `projects_dir` sees no repo spec; the
suite with `VALOR_PROJECTS` unset outside it never touches `~/Desktop`. Stage 2: `Spec.all` reads only `projects_dir`; a
missing or unreadable folder raises naming it; `projects/example.toml` loads by
path; tests that pinned real specs pass on fixtures; the persona and site still
name Valor and Yudame. Stage 3 (scripted, not suite): tree equality, `git
bundle verify`, the ref list, the post-push mirror scan, each Mac's `/update`
ending `done` with no restart. Full suite each stage.
