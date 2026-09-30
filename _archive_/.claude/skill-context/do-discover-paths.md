# do-discover-paths context — this repo (ai)

Traces produced by this skill feed the **happy-path testing pipeline** (see
`docs/features/happy-path-testing-pipeline.md`): discovery (this skill) → generation →
execution, yielding deterministic Rodney shell scripts that replay without LLM tokens.

## Environment

- `BYOB_ALLOW_EVAL=1` is enabled by default in this repo — the registrar at
  `scripts/update/mcp_byob.py` keeps it set so `browser_eval` works out of the box. Gate
  documentation: `docs/features/byob-browser-control.md`.
- BYOB health check: `cd ~/.byob && bun run doctor` (all green before starting).

## Directories

| Artifact | Location |
|---|---|
| Trace JSON | `tests/happy-paths/traces/<path-name>.json` |
| Generated Rodney scripts | `tests/happy-paths/scripts/` |
| Screenshots / evidence | `tests/happy-paths/evidence/` |

## Validator

`tools/happy_path_schema.py` is the schema ground truth (`VALID_ACTIONS`: navigate, input,
click, wait, assert, screenshot, exists; selector required for input/click/wait/exists;
`VALID_ASSERT_TYPES`: url_contains, text_visible, element_exists, title_equals). Validate
every trace before generation:

```bash
python -c "
import json
from tools.happy_path_schema import validate_trace_file
data = json.load(open('tests/happy-paths/traces/<path-name>.json'))
valid, errors = validate_trace_file(data)
print('Valid:', valid)
if errors: print('Errors:', errors)
"
```

## Generator

```bash
python tools/happy_path_generator.py tests/happy-paths/traces/<path-name>.json
# or generate all traces:
python tools/happy_path_generator.py tests/happy-paths/traces/
```

Emits standalone Rodney shell scripts (command mapping validated against Rodney v0.4.0).

## Runner

```bash
python tools/happy_path_runner.py
```

Executes the generated scripts in batch and collects a pass/fail report.
