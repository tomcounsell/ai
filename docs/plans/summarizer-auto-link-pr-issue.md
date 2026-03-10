# Plan: Summarizer Auto-Link PR/Issue References

**Issue**: #353
**Status**: Complete
**Branch**: `session/summarizer-auto-link-pr-issue`

## Problem

When the observer delivers a Telegram message containing `PR #N` or `Issue #N`, the text is often plain (non-clickable) because `_render_link_footer` depends on `session.get_links()` which requires session progress tracking to have stored the URLs first. If the worker didn't call `session_progress --pr-url` before the observer fires, links are lost.

## Solution

Add a regex-based text replacement in `_compose_structured_summary()` that converts any `PR #N` and `Issue #N` patterns into markdown links using the session's `project_key` to look up the GitHub org/repo from `config/projects.json`.

This is an additive change. The existing `_render_link_footer` continues to work as before. The new regex replacement acts as a safety net that ensures links always appear regardless of session tracking state.

## Implementation

### File: `bridge/summarizer.py`

#### 1. Add helper function: `_linkify_references(text, session)`

Location: After `_render_link_footer()` (~line 866), add a new function.

```python
def _linkify_references(text: str, session) -> str:
    """Convert plain PR #N and Issue #N references to markdown links.

    Uses the session's project_key to look up the GitHub org/repo
    from the registered project config. If no project config is found
    or the text already contains markdown links for a reference, it
    is left unchanged.

    Args:
        text: The summary text potentially containing PR #N or Issue #N
        session: AgentSession with project_key field

    Returns:
        Text with plain references converted to markdown links.
    """
    if not session or not text:
        return text

    # Get project_key from session
    project_key = getattr(session, "project_key", None)
    if not project_key or not str(project_key).strip():
        return text

    # Look up GitHub org/repo from registered project config
    from agent.job_queue import get_project_config

    config = get_project_config(str(project_key))
    github_config = config.get("github", {})
    org = github_config.get("org")
    repo = github_config.get("repo")

    if not org or not repo:
        return text

    base_url = f"https://github.com/{org}/{repo}"

    # Replace PR #N → [PR #N](url/pull/N)
    # Negative lookbehind for [ ensures we don't double-link already-linked refs
    text = re.sub(
        r"(?<!\[)PR #(\d+)(?!\])",
        lambda m: f"[PR #{m.group(1)}]({base_url}/pull/{m.group(1)})",
        text,
    )

    # Replace Issue #N → [Issue #N](url/issues/N)
    text = re.sub(
        r"(?<!\[)Issue #(\d+)(?!\])",
        lambda m: f"[Issue #{m.group(1)}]({base_url}/issues/{m.group(1)})",
        text,
    )

    return text
```

Key design decisions:
- **Negative lookbehind `(?<!\[)`**: Prevents double-linking text that's already inside a markdown link (e.g., from `_render_link_footer`)
- **Negative lookahead `(?!\])`**: Additional guard against `[PR #N]` patterns
- **Uses `get_project_config()`**: This is the existing registered config mechanism — the bridge calls `register_project_config()` at startup with the full project dict from `projects.json`
- **Graceful fallback**: If project_key is unset, config is missing, or GitHub info is absent, text is returned unchanged

#### 2. Call `_linkify_references` in `_compose_structured_summary()`

Location: Line ~1284, just before the final `return "\n".join(parts)`.

Apply linkification to the full composed text so it catches references in both the bullet summary and any link footer:

```python
    # Linkify PR #N and Issue #N references
    result = "\n".join(parts)
    result = _linkify_references(result, session)
    return result
```

This replaces the current `return "\n".join(parts)` at line 1284.

### File: `tests/test_summarizer.py`

Add a new test class `TestLinkifyReferences` with these test cases:

1. **`test_pr_reference_linkified`**: `"PR #323"` with psyoptimal session → `"[PR #323](https://github.com/yudame/psyoptimal/pull/323)"`
2. **`test_issue_reference_linkified`**: `"Issue #309"` → markdown link
3. **`test_multiple_references`**: `"PR #322 and PR #323"` → both linkified
4. **`test_already_linked_not_doubled`**: `"[PR #323](url)"` → unchanged
5. **`test_no_session_returns_unchanged`**: `session=None` → text unchanged
6. **`test_no_project_key_returns_unchanged`**: session without project_key → text unchanged
7. **`test_no_github_config_returns_unchanged`**: project_key exists but no GitHub config registered → text unchanged
8. **`test_mixed_pr_and_issue`**: Both `PR #N` and `Issue #N` in same text → both linkified
9. **`test_integration_compose_structured_summary`**: Full integration test calling `_compose_structured_summary` with a mock session that has project_key set, verifying the output contains linked references

## Success Criteria

- [x] Any `PR #N` or `Issue #N` in a delivered Telegram message becomes a clickable markdown link
- [x] Links are derived from the project's repo config via `get_project_config()`, not dependent on session progress tracking
- [x] Existing `_render_link_footer` still works as before (additive change, not breaking)
- [x] Already-linked references (inside markdown `[...]` syntax) are not double-linked
- [x] Graceful fallback: missing project_key, missing GitHub config, or no session → text unchanged
- [x] All existing summarizer tests continue to pass (151/151)
- [x] New tests cover: basic linkification, multiple refs, already-linked refs, missing config fallbacks, empty text/project_key edge cases (10 tests)

## No-Gos

- Do NOT remove or modify `_render_link_footer` — it stays as the canonical link source for SDLC jobs
- Do NOT modify `config/projects.json` — it already has all needed data
- Do NOT add a new config file or config loading mechanism — use existing `get_project_config()`
- Do NOT make this async — it's pure string manipulation

## Update System

No update system changes required. This is a bridge-internal change to `bridge/summarizer.py`. No new dependencies, configs, or migration steps needed.

## Agent Integration

No agent integration required. This change is internal to the summarizer module. No new MCP tools, no changes to `.mcp.json`, and no bridge import changes needed. The function is called automatically during message composition.

## Documentation

- [x] Update `docs/features/summarizer-format.md` to document the auto-linkification behavior
- [x] Add inline docstring to the new function

## Testing Strategy

Tests will use the existing summarizer test patterns. The `_linkify_references` function is pure (text in, text out) and easy to unit test. For the integration test with `_compose_structured_summary`, we need a session object with `project_key` set and the project config registered via `register_project_config()`.

## Estimated Scope

- **Lines changed**: ~40 in summarizer.py, ~80 in test_summarizer.py
- **Files touched**: 2
- **Risk**: Low — additive change, pure function, existing tests unaffected
