---
tracking: none
slug: 4a54eb8b-rollout-restarts-bridges
type: plan
status: planned
critique_rounds: 2
review_rounds: 1
governance_grant: none
---

# A rollout restarts every bridge whose code the merge reaches

Task 4a54eb8bdf09 (backlog `rollout-restarts-bridges`). Mission item 1,
"Tom never coordinates the gaps between those steps", and the architect,
2026-10-15: "a merge to main should hot reload the running server
essentially". Ports frozen task fad0dd74b313's reviewed build (its branch
`valor/fad0dd74`, commits `662639fd2` and `ac9da7024`) onto the stripped
main. The patch applies with conflicts only in `core/README.md` (the
`serve` entry, rewritten by the strip), `core/binaries.py`
(`LAUNCHCTL = "/bin/launchctl"`), and `tests/test_rollout.py`.

**Today.** `Kernel._apply` (`core/serve.py:559`) migrates, writes
`rollout.restarting`, and raises `Restart`. launchd restarts only
`com.valor.kernel`, and on the next start `recover`'s `_rolled`
(`core/serve.py:324`) writes `rollout.ended`. The bridges
(`com.valor.kernel.telegram`, `com.valor.kernel.slack`, `com.valor.email`,
`com.valor.kernel.local`, each its `__main__.LABEL`) keep their old code,
because nothing restarts them (`docs/machine.md:205-209`). Each bridge
holds its lock on a connection named `valor-<channel>`
(`core/bridge.py:607`), under the kernel's own role.

## Build

1. **Stamp.** `core.bridge.serve` names its lock connection
   `valor-<channel>@<git head of ROOT>`, read once at start. If the head
   cannot be read for any reason, the stamp is `@none` and the bridge
   serves as before. The name is 55 bytes at most, under Postgres's 63.
2. **`core/rollout.py`.** It adds the following:
   - `BRIDGES`: channel to label.
   - `closure(checkout, name)`: an AST walk of every import in
     `bridges/<name>/**.py`, at any depth, relative imports and package
     `__init__.py`s included, that resolves to a `core/` or `bridges/`
     file. Telegram and Slack import `core` inside functions, so a
     `sys.modules` check would miss those. The standard library's
     `modulefinder` crashes on this tree (tried 2026-10-16), so it can't
     be used.
   - `reaches(paths, files, name)`: a path is in the closure, under
     `bridges/<name>/`, or is `uv.lock` or `pyproject.toml`.
   - `stale(checkout, running, live)`: a bridge is stale when it has no
     stamp, its sha is unresolvable, its diff fails, or the diff from its
     sha to `running` reaches it. `@none` is not stale, so a kernel crash
     loop never kicks a bridge every 10 s.
   - `kick(label)`: `launchctl kickstart -k gui/<uid>/<label>`, with a
     10 s timeout. It returns an error string and never raises.
3. **`core/serve.py`.** `recover` runs `restart_bridges` just before
   `_rolled`:
   - It reads the live `valor-<channel>[@stamp]` connections from
     `pg_stat_activity`.
   - It computes `stale` off the loop, then kicks each stale bridge one at
     a time, also off the loop.
   - `_rolled` writes `restarted` (label to the old stamp) and
     `restart_failed` (label to the error) on each `rollout.ended` row.
   - Any error is logged, and the kernel still starts.

   Running the kicks at the new kernel's start, after migrate, puts
   kernel and bridges on the same code and schema. The outbox stays safe
   because each bridge reconciles its sends on start. The same pass also
   repairs a failed kick, or a bridge started by hand on old code, at the
   next start. A merge of only `docs/`, `tests/`, `persona/`, `skills/` or
   top-level `*.md` restarts neither the kernel nor any bridge.
4. **Docs:**
   - the `core/rollout.py` and `core/serve.py` docstrings;
   - `docs/machine.md`: the launchd section and the rollout paragraph;
   - `docs/data.md`: the two new fields on `rollout.ended`;
   - `docs/architecture.md`;
   - `core/README.md`'s `serve` entry;
   - `bridges/README.md`.

**Out of scope:**
- skipping the kernel's restart for a merge that touches only bridges;
- restarting routines, the backup job or the cluster;
- any retry of a failed kick other than the next kernel start.

## Tests

All new tests go in the existing `tests/test_rollout.py`, at most 250 new
lines, since the suite is already at its 15-file limit and has 612 lines
of its 5,000-line budget left. `test_budget.py` must still pass.

- **`closure`:** function-level, relative and transitive imports are
  followed, and package `__init__.py`s are added. `core/serve.py`,
  `core/rollout.py` and `core/router.py` are in no real bridge's closure.
  `core/bridge.py`, `core/intake.py` and `core/channels.py` are in every
  real bridge's closure.
- **`reaches`:**
  - `bridges/slack/x.py` does not reach telegram;
  - `bridges/local/chat.js` reaches local;
  - `uv.lock` reaches every bridge;
  - a diff of only docs, tests, persona, skills and `README.md` reaches
    none.
- **`stale`:**
  - equal to running: not stale;
  - behind running with a reaching diff: stale;
  - behind running with a quiet diff: not stale;
  - ahead of running and reached: stale;
  - no stamp: stale;
  - unresolvable sha: stale;
  - `@none`: not stale.
- **`kick`:** the argv is exact. A non-zero exit and a timeout come back
  as errors and are not raised.
- **`BRIDGES`:** each entry equals the bridge's `LABEL`.
- **`restart_bridges` and `recover`** (test database, fake bridge
  connections, injected kick):
  - only the stale bridge is kicked;
  - `-perform` and `-listen` connections and unknown channels are
    ignored;
  - a failing kick lands in `restart_failed`;
  - a raising query is logged and nothing is kicked;
  - with no checkout, nothing is read;
  - the kick runs before the `rollout.ended` row, and that row carries
    `restarted`.
- **Stamp:** the lock connection shows `valor-<channel>@<head>`, and
  `@none` when `git.head` raises.

Run `test_rollout`, `test_serve`, the telegram suites and the full suite.
After the merge, check live on the Cowboy:
- `pg_stat_activity` shows `valor-telegram@<new head>`;
- the `rollout.ended` row lists every bridge once, because none was
  stamped before this change.

## Stakes

This changes the kernel's start path and kills live channel processes. A
bug could stop the kernel from starting, kick a bridge on every start, or
leave a bridge on old code without anyone noticing. Hence 2 critique
rounds and 1 review round.
