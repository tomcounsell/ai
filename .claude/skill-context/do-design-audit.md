# do-design-audit context — this repo (ai)

The calling session must have `requires_real_chrome=True`:

- **Bridge-originated sessions:** the bridge auto-infers it from message text matching "design audit" patterns.
- **CLI-originated sessions:** `valor-session create --needs-real-chrome ...`
