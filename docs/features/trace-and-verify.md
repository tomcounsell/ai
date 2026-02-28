# Trace & Verify: Root Cause Analysis Protocol

Replaces narrative-only "5 Whys" root cause analysis with a data-driven verification protocol. The key insight: **5 Whys is a thinking tool, not a verification tool.** It generates hypotheses but does not validate them. Trace & Verify adds the missing forward-verification step.

## Background

The 5 Whys methodology failed across two PRs (#205 and #208) to catch a session ID mismatch between Claude Code hooks and AgentSession in Redis. The proposed fixes were logically sound but operationally broken -- hooks fired with one ID while sessions were keyed by another. A forward data trace would have caught this immediately. See issue #212 for the full post-mortem.

## Why 5 Whys Fails for Integration Bugs

1. **It traces backward, never forward.** It walks the causal chain from symptom to root but never verifies the proposed fix would actually work through the real system.
2. **It stops at the first plausible root cause.** A convincing-sounding proximate cause masks deeper integration gaps.
3. **It operates within component boundaries.** Each "why" stays within one component. Bugs that live between components -- at handoff points -- are invisible.
4. **No reproduction step required.** It is a narrative exercise. At no point does the analysis require running code and showing actual output.

## The Protocol

### Phase 1: Trace the Data Flow

Instead of "why did X fail?", ask: **"What is the actual data at each step?"**

At every boundary between components, capture the actual values being passed. Where does the data diverge from expectations?

Example:
```
Step 1: What session_id does the bridge create?       -> tg_valor_-5051653062_XXXX
Step 2: What session_id does the hook receive?        -> [run hook, capture input]
Step 3: What does _find_session() return for that ID? -> [call it, see result]
Step 4: What data exists on the AgentSession?         -> [query Redis, print fields]
```

This is a concrete trace, not a narrative. Each step produces actual output. Gaps become visible immediately.

### Phase 2: Write a Failing Test Before Fixing

Before implementing any fix, write a test that demonstrates the current broken behavior:

```python
def test_hook_can_find_session():
    """This test should FAIL before the fix and PASS after."""
    session = AgentSession.create(session_id="tg_valor_123", task_list_id=None)
    result = _find_session("claude-code-internal-uuid")
    assert result is not None  # FAILS: can't resolve UUID to our session
```

If you cannot write a failing test, you do not understand the bug well enough to fix it.

### Phase 3: Identify the Fix

Based on where the trace diverged in Phase 1, identify the minimal change that reconnects the broken data flow. The fix should address the divergence point, not a symptom downstream of it.

### Phase 4: Verify Forward

After applying the fix, re-run the same data trace from Phase 1. Show that every step now produces correct values. Do not rely solely on unit tests -- trace the actual values through the system.

### Phase 5: Check for Mocks Hiding Reality

If existing tests pass but the bug exists in production, identify which mocks are hiding the real behavior. Every `@patch` or `Mock()` in a test suite is a potential blind spot where the test says "this works" but the real system does not.

Add integration tests that exercise the actual code paths without mocks at the boundary where the bug was found.

## Prompt Template

Use this when investigating a bug:

> **Root Cause Analysis: Trace & Verify**
>
> 1. **Trace the data flow** from input to expected output. At each boundary between components, capture the actual values being passed. Where does the data diverge from expectations?
>
> 2. **Write a failing test** that reproduces the exact broken behavior. The test must fail for the right reason (the bug), not a setup issue.
>
> 3. **Identify the fix** based on where the trace diverged.
>
> 4. **Verify forward**: After applying the fix, re-run the trace. Show that every step now produces correct values and the test passes.
>
> 5. **Check for mocks hiding reality**: If existing tests pass but the bug exists in production, identify which mocks are hiding the real behavior and add integration tests that exercise the actual code paths.

## When to Use

- Bug investigation during `/do-patch`
- Sentry error triage and root cause analysis
- Any debugging where the fix involves multiple components
- Post-incident analysis as a replacement for narrative-only 5 Whys

## When Not to Use

- Single-component bugs with obvious fixes (typo, missing import, off-by-one)
- Performance optimization (use profiling instead)
- Feature development (use plan docs instead)

## Related

- [do-patch Skill](do-patch-skill.md) -- Uses Trace & Verify in Step 1
- Issue #212 -- Origin of this protocol
- Issue #209 -- The session ID mismatch that 5 Whys missed
