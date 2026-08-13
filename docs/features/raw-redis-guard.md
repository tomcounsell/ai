---
tracking: https://github.com/tomcounsell/ai/issues/2638
status: Shipped
---

# Raw-Redis Guard

## The rule it enforces

Popoto-managed keys are read and written through the ORM, never through raw
Redis. Writes (`delete`, `srem`, `sadd`, `zrem`) go through `instance.save()` /
`instance.delete()`; reads (`hgetall`, `hget`, `hmget`, `hscan`, `scan_iter`) go
through `Model.query.filter()`.

Reads are in scope for the same reason writes are: direct reads bypass Popoto's
field-aware decoding, and a client with `decode_responses=True` raises
`UnicodeDecodeError` on any hash carrying a binary field such as an
`EmbeddingField` (float32 vector bytes). See issue #1038.

`.claude/hooks/validators/validate_no_raw_redis_delete.py` enforces it as a
PreToolUse Bash guard. It is in-process predicate 4 of
`.claude/hooks/dispatch/pre_tool_use_bash.py`, so it costs no interpreter start
of its own. See [Hook Manifest → Per-Event Dispatcher](hook-manifest.md#per-event-dispatcher).

## Two gates run before any pattern matching

`find_violation(command, cwd="")` evaluates both gates first. Only a command
that clears both is tested against the block patterns and the Popoto-context
token list.

### Gate 1: the other-repo exemption

The rule is an ai-repo rule. Raw Redis on Popoto-managed keys is wrong *here*
because Popoto is this repo's ORM. It carries no meaning in `~/src/popoto`,
where Popoto is the library under development and its own tests legitimately
need raw Redis to construct states the ORM cannot produce. Blocking there is
what issue #2638 reported, after the guard blocked popoto work for #2636.

**Project scope does not mean cwd scope.** The manifest already declares this
validator `scope = "project"`, and the issue's premise that a stale global
registration was to blame turned out to be wrong. Scope governs which
`settings.json` carries the registration, so the hook fires for every Bash call
an ai-repo *session* makes. It says nothing about where that call's working
directory points. The dispatcher had always extracted `cwd` from the payload and
passed it to every validator; this one took it as `_cwd` and discarded it. That
is the whole mechanism, and it is worth knowing before writing any new
path-sensitive validator.

`_guard_applies(cwd)` walks up from `cwd`, and whichever it meets first decides:

| First thing found walking up | Verdict |
|---|---|
| This repo's own marker path | An ai checkout. Guard applies. |
| A `.git` | A different repo. Guard stands down. |
| Neither, all the way to the root | Guard applies. |

The marker is the validator's own path inside the checkout
(`.claude/hooks/validators/validate_no_raw_redis_delete.py`), which makes the
test self-identifying. It holds for the main checkout, `.worktrees/{slug}/`,
`.claude/worktrees/{agent}/`, and a worktree parked anywhere else on disk, all
of which carry a full working tree.

`git rev-parse --show-toplevel` would answer the same question, and
`validate_no_destructive_git_in_shared_checkout` resolves its own foreign-repo
policy exactly that way. The marker walk is the cheaper choice here because it
costs a few `stat` calls instead of a subprocess on every Bash call the
dispatcher sees, and this predicate needs only a yes/no on repo identity rather
than the toplevel path itself.

`.git` is probed with `exists()` rather than `is_dir()`, because a worktree's
`.git` is a file and a worktree of another repo is still another repo. The walk
is bounded at `_MAX_PARENT_WALK = 40`, far past any real checkout depth.

**The exemption is narrower than "outside this repo," deliberately.** The Redis
these keys live in is machine-global, so a raw delete run from anywhere on this
machine reaches the same production keys. `/tmp`, `$HOME`, and
`/nonexistent/path/xyz` all belong to no repo at all, and the guard stays armed
for each of them. That is also what makes the fail-closed claim true: a missing
path resolves happily on macOS, since `Path.resolve()` is non-strict, so
"walked to the root without finding either marker" is the clause that covers an
unresolvable path.

### Gate 2: executable context

Matching on command text means prose describing the rule trips the rule. Filing
issue #2638 was itself blocked, because the issue body quoted the offending
call. A command with no interpreter in it cannot execute a Redis call, so there
is nothing to block.

`_EXECUTABLE_CONTEXT` requires one of `python`, `python3`, `ipython`, `pytest`,
`redis-cli`, `uvx`, `uv run`, or a bare `.py` path (`./scripts/thing.py`
executes without the word "python" appearing anywhere). So `gh issue create
--body '...'`, `git commit -m`, `echo >> notes.md`, a heredoc into a `.md` file,
and `grep -rn` for the pattern all pass, while `python -c`, `uv run python`, a
bare `./script.py`, and `redis-cli ... DEL` still block.

**The leading character class is exactly `/`, and deliberately not `.`.** The
class exists so `python` does not match inside a longer word like `mypython`. It
has to admit a path separator because the house idiom invokes the interpreter by
path: `.venv/bin/python -c ...`, and CLAUDE.md documents the `.venv/bin/valor-*`
form. A class of only whitespace and shell metacharacters left that primary
vector unmatched. `/` alone covers every path form including `~/...` and `./...`,
since a separator always precedes the interpreter name. Adding `.` would make
prose naming a dotfile such as `.python-version` read as an invocation, which
reopens the very false positive this gate closes. Both directions are pinned by
mutation tests.

## The Popoto-context token list

Block patterns fire only when the command also carries a `_POPOTO_CONTEXT`
token. The list is a widener: an extra entry only makes the guard fire more
often, so a name that turns out not to be a Popoto model is harmless to keep and
removing one is a fail-open change. `SessionEvent` stays listed for that reason
even though it is a pydantic model.

### `$SortF:`, not `$SortedF:`

Popoto's field metaclass builds each key prefix as `f"${name.strip('Field')}F"`
(`popoto/fields/field.py`). `str.strip` takes a *character set*, not a suffix,
so `"SortedField".strip("Field")` is `"Sort"`. The emitted prefix is `$SortF`,
and `$SortedF` is a spelling Popoto never produces. Guarding the latter guarded
nothing: a live production read returns
`$SortF:FencedMemory:importance:<id>` and zero `$SortedF:` keys exist. Both
`$SortF:` and `$DecayingSortF:` are guarded now (#2641).

This is a general Popoto gotcha, not a one-off typo. Any code deriving a prefix
from a field class name hits the same `str.strip` behavior.

### Model names are pinned by a completeness test

The model-name list drifted silently twice before it had a test. `Job` was
missing while `Room` was present, and widening the probe past the issue's claim
turned up four more that had slipped the gate: `CorpusSizeBaseline`,
`CrashSignature`, `LastProcessedRecord`, and `PipelineLedger`.

`tests/unit/test_validate_no_raw_redis_delete.py::test_model_list_is_complete`
enumerates `popoto.Model` subclasses across the first-party packages and fails
naming any that is absent, so the seventh omission is visible rather than quiet.
Adding six names was not the fix; the test is.

## Fail posture

Both gates fail closed. `_guard_applies` stays armed for any cwd that does not
resolve inside a different git repository, and `cwd` defaults to `""`, meaning
"unknown," so a caller passing only the command behaves exactly as it did
before. Note that this differs from the dispatcher's own per-validator posture,
which fails *open* for this validator: an unexpected exception inside it is
logged and the dispatcher moves to the next validator.

## Known residual

Prose that names an interpreter still blocks even when the interpreter is only
quoted as an example rather than executed. A heredoc writing a doc file that
quotes a path-invoked interpreter next to a blocked call shape is the shape that
does it. Issue #2638 concedes that matching on command text will always carry
some false positives. Tracked in #2736, with the fix direction recorded there:
drop heredoc bodies whose consuming command is not itself an interpreter.

## Related

- [Hook Manifest](hook-manifest.md) — the dispatcher, first-block-wins ordering, and per-validator fail posture.
- [Hooks Best Practices](hooks-best-practices.md) — validator conventions and the `/audit-hooks` reflection.
- [uv-sync Worktree Guard](uv-sync-worktree-guard.md) — the sibling cwd-sensitive Bash validator.
- Issues #1038 (the decode failure that motivated the read patterns), #2636, #2638, #2641, #2736.
