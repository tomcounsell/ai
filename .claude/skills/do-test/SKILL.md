---
name: do-test
description: "Run the test suite. Use when the user says 'run tests', 'test this', or anything about testing. Stub — full skill tracked in #121."
---

# Do Test

Stub skill for running the test suite. Full implementation tracked in issue #121.

## Usage

```
/do-test
```

## Current Behavior

This skill is a placeholder. When invoked, run the standard test commands:

```bash
pytest tests/ -v
ruff check .
black --check .
```

## Future

The full skill (issue #121) will add:
- Targeted test selection based on changed files
- Coverage reporting
- Integration test orchestration
