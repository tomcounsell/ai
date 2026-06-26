# do-design-audit context — this repo (ai)

How this repo opts a session into real-Chrome mode for the `/do-design-audit` skill. The global
skill body runs a generic baseline (ensure your harness's real-browser mode is active); this file
supplies the project-specific mechanism.

## Requiring real Chrome

The calling session **must** have `requires_real_chrome=True`. In this repo there are two paths:

- **Bridge-originated sessions:** the bridge auto-infers `requires_real_chrome` from message text
  matching "design audit" patterns — no manual flag needed.
- **CLI-originated sessions:** pass the flag explicitly when creating the session:

  ```bash
  valor-session create --needs-real-chrome ...
  ```

There is no anonymous-headless fallback (that surface was retired in #1256). Two concurrent
real-Chrome sessions race on the active tab and corrupt each other's DOM, so only one design
audit runs at a time.
