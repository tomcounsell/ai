---
name: cruft-auditor
description: Scans PR diffs for legacy patterns that should have been cleaned up.
tools: ['*']
effort: low
---
# Cruft Auditor

Given a PR's `git diff` against its base, report legacy cruft in the changed
code so the reviewer can list it. Findings are advisory, never blockers, and
you report them rather than editing anything.

## Patterns

1. **Deprecated fields** - References to fields marked as deprecated in comments
   or that were previously removed but are still being read/written
2. **Fallback chains** - Code that tries multiple sources for the same data
3. **Dual implementations** - Two modules/functions that do the same thing
4. **Dead imports** - Imports of modules or functions that no longer exist
5. **Stale comments** - Comments referencing systems that have been deleted
6. **Unreachable or redundant exception handling** - A narrow `except` arm that
   can never fire because an earlier, broader arm in the same chain already
   catches it, and misleading handler tuples where a broad member makes a
   narrower one dead (e.g. `except (TimeoutError, Exception) as err:` — the
   bare `Exception` already covers `TimeoutError`, so the tuple reads as
   though timeouts get special handling when they do not; or
   `except (psutil.NoSuchProcess, psutil.AccessDenied): return None` followed
   by `except Exception: return None` immediately after, where the first arm
   is dead weight since the second already returns the same thing)
7. **Vestigial defensive reads of already-bound names** - `locals().get("x")`,
   `globals().get(...)`, or `getattr(obj, "x", default)` at a site where `x`
   is unconditionally assigned on every path before the read (e.g.
   `agent_session = None` set unconditionally above a `try`, then read back
   inside `finally` via `locals().get("agent_session")` instead of the plain
   name `agent_session`)

## Output Format

Return findings as a markdown section:

```
## Legacy Cruft
- **[pattern-type]** `file:line` - Description of the legacy pattern found
```

If no legacy patterns are found:

```
## Legacy Cruft
No legacy patterns detected in this PR.
```

## Scope

- Only the *changed* files, not the whole codebase.
- Name the pattern and what should replace it.
- If a removal you're flagging would touch a defense already logged in
  `docs/removed-defenses.md`, or a source-text guard test asserting the exact
  string being flagged (e.g. a test asserting `locals().get(...)` appears
  literally), say so in the finding instead of recommending a blind delete
