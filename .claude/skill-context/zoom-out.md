# zoom-out context — this repo (ai)

This repo provides project-local CLIs for two of the `/zoom-out` steps. The global skill body
runs a generic baseline (reconstruct from `git log`, print the summary in-session); this file
supplies the richer tooling.

## Step 1 — synthesize recent context via the memory CLI

This repo has a subconscious memory store. Surface observations related to the current work area
and read the top 5 results, noting any corrections or pattern observations that apply:

```bash
python -m tools.memory_search search "<recent keywords from current session>"
```

If the bridge/Redis is unavailable, fall back to the generic `git log` reconstruction.

## Step 5 — deliver the summary remotely via Telegram

If the user is remote, optionally send the strategic summary through the project Telegram CLI
(keep it under ~300 words so it fits a single message):

```bash
valor-telegram send --chat "Dev: Valor" "<summary>"
```

Otherwise just print the summary in-session as the generic baseline describes.
