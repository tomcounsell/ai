# Test Quality Rubric

Reference for evaluating test suite quality. Load when running /do-test on a new module or when /do-patch is reviewing test failures.

## The 8 Quality Criteria

A test suite is high-quality when ALL of these hold:

| # | Criterion | Check |
|---|-----------|-------|
| 1 | **Behavioral** | Tests describe what the code does, not how it does it |
| 2 | **Independent** | Each test can run alone without setup from another test |
| 3 | **Deterministic** | Same test, same result, every run (no time/random dependencies without seeding) |
| 4 | **Fast** | Unit tests finish in < 1s each; integration in < 10s each |
| 5 | **Named precisely** | `test_parse_duration_returns_seconds_for_minutes_suffix` beats `test_parse_duration_1` |
| 6 | **No redundancy** | No two tests assert the same thing in different words |
| 7 | **Failure-informative** | When a test fails, the output says what went wrong without reading the source |
| 8 | **Covers the sad path** | At least one test per function covers an invalid input or error condition |

## Red Flags (Lower Quality)

These patterns reduce test value:

- `assert result is not None` — tests that a value exists, not what it is
- `assert len(result) > 0` — weaker than asserting the exact expected content
- Mocking everything — if every dependency is mocked, the test only tests the mock setup
- Setup in `__init__` of test class — use fixtures instead
- Tests named `test_1`, `test_a`, `test_works` — names must describe behavior
- Tests that import private functions (`_internal_helper`) — tests should use public interfaces
- `time.sleep()` in tests — use event signals or mock time instead

## Parameterization Pattern

Collapse repetitive cases with `@pytest.mark.parametrize`:

```python
# Instead of three separate test functions for "5m", "2h", "30s":
@pytest.mark.parametrize("value,expected", [
    ("5m", 300),
    ("2h", 7200),
    ("30s", 30),
])
def test_parse_duration_valid_inputs(value, expected):
    assert parse_duration(value) == expected
```

## Coverage Targets (from CLAUDE.md)

| Scope | Target |
|-------|--------|
| Unit tests | 100% |
| Integration tests | 95% |
| E2E tests | 90% |

Coverage alone is not quality — a test that asserts `True` covers a line but adds no value.

## When to Delete a Test

Delete a test when:
- The code it tested has been deleted
- It tests an internal implementation detail that no longer exists
- It duplicates another test exactly
- It was written to satisfy a coverage metric and asserts nothing meaningful

Leaving dead tests in the suite is worse than having no tests — they mislead future readers.
