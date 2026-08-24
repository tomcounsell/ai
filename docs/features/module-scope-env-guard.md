# Module-Scope Env Read Guard

Slice 0 of the module-scope environment-read migration (issue #2866, #2945). Adds the instrument — a reproducible census and a regression guard — before any of the 188 unmigrated call sites are touched. Changes no env read itself.

## The Defect Class

A *module-scope env read* is a call to `os.environ.get`, `os.getenv`, `os.environ.setdefault`, or `os.environ.pop` written at the top level of a `.py` file, so it executes the moment the module is first imported. The value such a read sees is a property of *when the process started*, not of *who is configuring it* — a value baked in at import time never picks up a later `.env` edit or per-instance override without a process restart. The established fix is a `config/settings.py` field read lazily at call time (the #1968 `TimeoutSettings` precedent); see [Config Timeout Catalog](config-timeout-catalog.md).

## The Census (`scripts/scan_module_scope_env.py`)

One AST detector, `find_module_scope_env_calls(content, filename) -> list[EnvCall]`, is the single implementation. It has exactly two consumers: this script's own CLI, and the regression guard below — so the guard and the published count can never disagree about what counts as a violation.

**AST-based, not regex.** The module-scope vs. `def`/`class`-body distinction is exactly what a regex cannot express — a regex sees `os.environ.get("X")` identically whether it sits at column 0 or four frames deep inside a method. The visitor descends into module-level `if` / `try` / `with` / `for` / `while` bodies (those run at import) and refuses to descend into `def` / `async def` / `class` bodies (those run when called).

**Corpus: git-tracked `*.py` only**, via `git ls-files`. A filesystem walk instead sweeps `.worktrees/` and `.claude/worktrees/` — untracked full checkouts of this same repo — and inflates the census from 72 modules to 4768.

**Baseline** (reproduced exactly by the committed script): 72 non-test modules / 190 call sites (2 allowlisted, 188 to migrate); 79 modules / 202 sites with `--tests`. By function: `os.environ.get` 155, `os.getenv` 35, `os.environ.setdefault` 0, `os.environ.pop` 0.

```bash
python scripts/scan_module_scope_env.py              # non-test census
python scripts/scan_module_scope_env.py --tests      # include test files
python scripts/scan_module_scope_env.py --by-file    # per-file breakdown
python scripts/scan_module_scope_env.py --json       # machine-readable
```

See `docs/tools-reference.md` for the full CLI writeup.

### Methodology limitation — syntactic only

The census cannot see an import-time env read made *indirectly* through a function call. `config/settings.py` calls `stale_granite_env_keys()` at module scope; that function reads `os.environ` internally, so the read genuinely happens at import time and the scan is blind to it. The scan also does not descend into class bodies at all — `config/settings.py`'s `model_config.env_file` site (`__import__("os").environ.get(...)` inside the `Settings` class body) is marked with the allowlist comment for documentation purposes but is **not counted** in the 190/188 figures.

**Consequence: a future "72 → 0" result proves the *syntactic* class is drained, not that every import-time env read is gone.** Do not present it as proof the defect class is eliminated. This limitation is stated in the plan (`docs/plans/module-scope-env-reads-migration.md`, "Methodology limitations") and in the script's own module docstring.

## The Regression Guard (`.claude/hooks/validators/validate_no_module_scope_env.py`)

Imports the same `find_module_scope_env_calls` detector, then wraps it with two things the census doesn't need: a git-diff-aware caller, and the hook protocol.

### Diff-scoped, not whole-file — this is load-bearing

The guard only flags lines the staged `git commit` **adds or rewrites**, not every module-scope read that happens to live in a touched file. This is deliberate, not a softening: 188 unmigrated sites live across 72 modules today, and the migration's slices 1-9 must edit exactly those files. A whole-file guard would block every one of its own migration commits the moment it touched a file with more than one pre-existing site.

- The pure `find_violations(content, filename, changed_lines=None)` core is **whole-file** — pass `None` (or nothing) for `changed_lines` and it reports every site in the file. This is what the CLI path and all 46 unit tests exercise.
- Diff scoping is applied only by the `git commit` caller, `find_violation_for_command()`: it resolves the staged content via `git show :path` and the changed line numbers via `git diff --cached -U0` hunk headers, then calls `find_violations(content, path, changed_lines)`.
- `_staged_added_lines()` returns an **empty set on any git failure**, which fails OPEN (no violations reported) — consistent with the dispatcher's fail-open posture for this validator.
- The predicate early-returns `None` unless `"git commit"` appears in the command string, so there is no added per-Bash-call cost — every non-commit Bash invocation exits the guard on its first line.

### What it flags

- A call to one of the four env-read functions at module top level, including inside a module-level `if`/`try`/`with`/`for`/`while` body — **and** on a line the staged commit actually touches.

### What it does not flag

- Reads inside `def`/`async def`/`class` bodies.
- Pre-existing module-scope reads on lines the commit does not touch (see above).
- Test files (`tests/`, `test_*.py`, `conftest.py`, `fixtures/`) — matched by path *components*, not substring, so `.../test_something0/bad.py` (a pytest `tmp_path` dir) does not false-positive.
- A call whose source span carries the `# env-scope-guard: allow` marker comment.

### Allowlist marker and the triage criterion

`# env-scope-guard: allow` on the offending line, with a one-line comment saying why. Per the triage criterion in `docs/plans/module-scope-env-reads-migration.md`, a read may stay at import time only if **all three** hold:

1. **pre-config** — it runs before `config.settings` is importable, or it determines whether/how config loads at all;
2. **launcher-owned** — it is set by launchd/systemd/a shell wrapper, not by a human tuning `.env`;
3. **cannot vary per-instance** by construction.

Three sites carry the marker today, each with a written justification clearing all three tests:

| Site | Shape |
|------|-------|
| `worker/__main__.py` | `if not os.environ.get("VALOR_LAUNCHD")` gate around `load_dotenv()` |
| `bridge/telegram_bridge.py` | same `VALOR_LAUNCHD` gate |
| `config/settings.py` | class-body equivalent, `model_config.env_file=None if __import__("os").environ.get("VALOR_LAUNCHD") else ".env"` — not counted in the census (class bodies are out of scope for the scan), marker recorded so a later refactor can't lose the verdict |

## Registration — dispatcher predicate, not a manifest entry

Per #2435, the PreToolUse/Bash validators are consolidated into one in-process dispatcher (`.claude/hooks/dispatch/pre_tool_use_bash.py`); new predicates are added directly to its `_VALIDATORS` list rather than a new `manifest.toml` `[[hook]]` entry. This guard is appended as the 10th tuple, calling `find_violation_for_command(command) -> str | None` directly — first-block-wins, fail-open like its siblings. See [Hook Manifest](hook-manifest.md) for the dispatcher contract.

The manifest's `timeout = 20` budget for the dispatcher entry was re-confirmed in a comment (following the #2645 precedent for the 9th predicate) and **not changed**: on the `git commit` path this guard does one `ast.parse` per staged non-test `.py` file plus two short `git` reads each (`git show :path`, `git diff --cached -U0 -- path`), each self-capped at 10s, bounded by the size of a single commit and still dominated by the out-of-process design-system-sync leg.

## Direct/manual invocation

The script also exits 1 with an actionable stderr message when run directly against files, deliberately whole-file (not diff-scoped) — useful for a migration slice asking "is this module clean yet?":

```bash
python .claude/hooks/validators/validate_no_module_scope_env.py <file> [<file> ...]
```

## Tests

`tests/unit/test_validate_no_module_scope_env.py` — 46 tests, mirroring the shape of `tests/unit/test_validate_no_inline_timeout.py`.

## Key Files

| File | Role |
|------|------|
| `scripts/scan_module_scope_env.py` | The single AST detector (`find_module_scope_env_calls`), the census CLI, `ScanResult` aggregation. |
| `.claude/hooks/validators/validate_no_module_scope_env.py` | Regression guard: diff-scoped `find_violation_for_command()` for the dispatcher, whole-file `find_violations()` core, standalone CLI. |
| `.claude/hooks/dispatch/pre_tool_use_bash.py` | Registers the guard as its 10th `_VALIDATORS` predicate. |
| `.claude/hooks/manifest.toml` | `timeout = 20` re-confirmation comment for the dispatcher entry. |
| `docs/plans/module-scope-env-reads-migration.md` | Full 10-slice migration plan, the two-axis triage criterion, methodology limitations. |
| `tests/unit/test_validate_no_module_scope_env.py` | 46 tests. |

## Related

- [Config Timeout Catalog](config-timeout-catalog.md) — the target pattern every migrated site lands on.
- [Hook Manifest](hook-manifest.md) — the dispatcher this guard registers against.
