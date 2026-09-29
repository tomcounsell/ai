# zoom-out context — this repo (ai)

This repo provides project-local CLIs for the `/zoom-out` memory recall and remote delivery.

## Recall: memory CLI

This repo has a subconscious memory store. Surface observations related to the current work area
and read the top 5 results, noting any corrections or pattern observations that apply:

```bash
python -m tools.memory_search search "<recent keywords from current session>" --project valor
```

If the bridge/Redis is unavailable, fall back to the generic `git log` reconstruction.

## Remote delivery: Telegram

If the user is remote, optionally send the strategic summary through the project Telegram CLI
(keep it under ~300 words so it fits a single message):

```bash
valor-telegram send --chat "Dev: Valor" "<summary>"
```
