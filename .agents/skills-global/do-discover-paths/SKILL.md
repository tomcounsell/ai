---
name: do-discover-paths
description: "Explore an authorized browser flow and record a replayable JSON trace with stable selectors and final-state assertions."
---

# Do Discover Paths

Resolve the starting URL and path name. Use the available Codex browser surface and its documented inspection APIs. Read current state before each interaction and refresh it afterward. Capture stable observed selectors: unique IDs or test IDs, then semantic attributes, with structural paths as a last resort. Do not invent DOM evaluation capabilities or store ephemeral browser handles as replay selectors.
Record `name`, `url`, `steps`, `expected_final_url`, and `expected_text`. Step actions: `navigate` with url; `input` with selector/value; `click`, `wait`, or `exists` with selector; `assert` with type/value; `screenshot` with optional path. Assertion types are `url_contains`, `text_visible`, `element_exists`, and `title_equals`.
Use credential placeholders such as `{{credentials.username}}` and `{{credentials.password}}`, never real secrets. Do not exercise production purchases, sends, or destructive flows unless those actions are authorized. Assert the observed outcome rather than assuming a successful click completed the flow.
Save to `tests/happy-paths/traces/<path-name>.json` unless another location is declared. In Valor validate against `tools/happy_path_schema.py` and use `tools/happy_path_generator.py` for the documented Rodney script output. Use its runner only within the authorized environment. Missing steps or inaccessible selectors mean an incomplete trace; report them rather than silently skipping steps or claiming a replayable success.
