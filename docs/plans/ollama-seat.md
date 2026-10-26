---
status: Planning
type: feature
appetite: Small
created: 2026-10-23
tracking: https://github.com/tomcounsell/ai/issues/3671
last_comment_id:
---

# An Ollama seat on Pi, for RSI work

**Ask.** Tom, 2026-10-23: Valor pays OpenRouter for open-weight models
while the Ollama subscription's credits go unused; use them, through Pi
only, and for now only for RSI work. Free credits do not count against
RSI's cash limits (`rsi.md`, Grants). Done (rule 5) when a task the `rsi`
routine started has run its turns on the seat and Tom has used the result,
dated.

**Not in scope.** The judgement fallback stays on OpenRouter: Ollama Cloud
ignored a strict JSON schema on both `format` and `response_format`
(gemma4:31b, gpt-oss:120b, glm-5.2, probed 2026-10-23) and does not host the
pinned Qwen3-235B. No Claude Code route to Ollama. No seat for other work.

## Freshness

Baseline `35066059c`. RSI Build 1 (`routines/rsi/`) is being built on
`session/rsi` and is not on `main`; this lands after it.

## Spike results (2026-10-23, this Mac, ollama 0.40.6, signed in)

- `POST /v1/responses` on the local daemon works for `kimi-k3:cloud`,
  `glm-5.3:cloud` and `kimi-k2.7-code:cloud`, with a function tool called
  correctly. That is the API Pi's provider speaks (`api: "openai-responses"`,
  `harnesses/pi.py` `models_json`). High confidence.
- Streamed, the last event is `response.completed` with `usage`
  (`input_tokens`, `output_tokens`), which `core/gateway.py` `OpenAIMeter`
  already reads. High confidence.
- Context: `kimi-k3` and `glm-5.3` 1,048,576; `kimi-k2.7-code` 262,144
  (`/api/show`). Ollama publishes no maximum output.
- The daemon holds the sign-in, so the gateway sends no key.
- Pi 1.0.0 against the daemon (2026-10-26): one bug fix (two defects, three
  tests) passed on all three candidates, in 8 to 10 s and 4 turns each. No
  separation, so the seat is `kimi-k3`: a 1M window like the frontier
  seat's, so Pi compacts as late as Claude Code does. Chosen by Valor, Tom's
  delegation, 2026-10-26.

## Solution

1. **Seat.** `core/settings.py` `SEATS["ollama"] = ("pi", "kimi-k3:cloud")`,
   and an `OLLAMA_MODELS` table: per id, the context window from `/api/show`,
   the day checked, price 0 (the subscription). `ollama_upstream` setting,
   `VALOR_OLLAMA_UPSTREAM`, default `http://127.0.0.1:11434`.
2. **Gateway route.** Tails starting `ollama/` go to `ollama_upstream` with
   the prefix removed, through the OpenAI route's metered path
   (`_metered_openai`, `OpenAIMeter`) with the upstream, credential and
   price table as parameters, not a copy. No credential is added; the turn's
   `authorization` is dropped as on the OpenAI route. Recorded
   `route: "ollama"`, charged 0 micro-dollars on every call (the route never
   reaches `openai_charge_prices`' stand-in rates), tokens as reported.
3. **Pi.** `models_json` takes the route and table from the seat's model:
   `baseUrl` `<base>/ollama/v1`, `contextWindow` the table's window less
   `DEFAULT_MAX_OUTPUT_TOKENS`, cost 0. `context_window` and `_cost` read
   `OLLAMA_MODELS` for an Ollama id.
4. **RSI.** `routines/rsi/routine.toml` `model = "ollama"`. Nothing else:
   `_routine_project` already passes the seat name and `_start_project`
   resolves the harness from it (`core/__main__.py:491`). The SDLC stages
   with their own seats (critique, docs, review, `core/fresh.py:69`) stay on
   Claude Code; a Pi task's advisor is already Claude Code's `reviewer`
   (`fresh.py:251-259`).

## Tests

- Gateway: an `ollama/v1/responses` call reaches a local fake upstream
  without any authorization header, streams through, and records
  `route: "ollama"`, the reported tokens and 0 micro-dollars.
- `resolve_seat("ollama")` is `("pi", "kimi-k3:cloud")`; Pi's `models.json`
  for it points at `/ollama/v1` with the table's window.
- A finding started by a routine with `model = "ollama"` gets
  `harness_name == "pi"`.

## Rabbit holes

- Subscription limits: when Ollama answers 429, the turn fails as any
  failed turn does. No fallback to another seat until that has happened.
- No benchmark of the candidates. RSI's comparative-evidence rule governs
  claims of improvement, not the choice of seat model.
- `reasoning: False` in Pi's model entry stays; thinking output is
  returned and metered either way.

## Rollout and docs

Each Mac that runs `rsi` is signed in (`ollama signin`; `ollama list` shows
a `:cloud` entry): a line in `docs/rollout.md`. Docs: `docs/pi.md`,
`docs/performers.md` (an Ollama row: the daemon's sign-in, route
`ollama`), `docs/tech-stack.md`, the gateway docstring, `routines.md`.
No governance is added.
