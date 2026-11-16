# Gemini's Decisions API as the judgement fallback, side by side with Jev (P-035)

**Why.** When Jev abstains or refuses, the fallback (Qwen3-235B on Parasail through OpenRouter)
answers in about 8 s. On the test-coverage check that is three questions in four (observed,
week 2026-11-09 to 11-16, 374 rows). On 188 recorded inputs `gemini-decision-1` matched Jev on
155 of 156 intake requests and answered test-coverage in 0.55 s (assessment, task cc21a8902276).
Tom, 2026-11-16: move non-local, non-Claude work to Google models. Answers to the pre-plan
questions (2026-11-16): ask Gemini on **every** judgement, in parallel with Jev, so that the
14-day count covers all five sites; and rebuild the seven seed cases.

**Stakes.** The port decides whether a request gets a question first. A wrong fallback, or a
slower one, changes what Valor asks people, on every task. A revert undoes it, and no stored
data changes shape.

## What is built

1. **`tools/gemini_decisions.py`**, leg `gemini`, modelled on `tools/jev.py`.
   - **Request.** `POST https://generativelanguage.googleapis.com/v1beta/decisions` with
     `{model: "gemini-decision-1", input: [{type: "text", text: json(inputs)}], questions}`.
     A boolean question is `{type: "noul", instructions, criteria: {true_description,
     false_description}}`. A choice or pick question is `{type: "choice", instructions, criteria:
     labels}`. Instructions are the question text plus `DATA_ONLY`, as in Jev.
   - **Decode.** `noul` becomes {true, false}. A choice keeps its `probabilities`. `usage`
     is read as `total_input_tokens`/`total_output_tokens`. Anything else in the body is
     `malformed`.
   - **Unknown parts of the shape.** Whether the body names `model`, and what an oversize
     input returns, are not known yet. The builder makes one live probe (under a cent) and
     saves the real 200 body as `tests/fixtures/judgement_gemini.json`. If that body names a
     model, any model but the pinned one is `malformed`, as in Jev. No error body is mapped
     to `input_too_large` until one has been seen: every non-200 is `http_status`.
2. **`core/judgement.post`** takes the auth header as a parameter. Google's is
   `x-goog-api-key`; Jev keeps `Authorization: Bearer`.
3. **Settings.**
   - Add `GEMINI_DECISION_MODEL = "gemini-decision-1"`, `GEMINI_DECISIONS_URL`, and a
     `JUDGEMENT_PRICES` entry ($0.10/M input, $0 output, checked 2026-11-16, aistudio pricing
     page).
   - Add `gemini_url` (`VALOR_GEMINI_DECISIONS_URL`) and `gemini_timeout_s = 10`.
   - The key is the Meet key, read from `settings.google_keyfile` (`GOOGLE_API_KEY`) through
     `endpoint_key`.
   - Delete every `OPEN_WEIGHT_*` constant, `open_weight_url`/`open_weight_timeout_s`,
     `OPENROUTER_API_KEY` from `judgement-keys`, and `tools/open_weight.py`. `route()`
     returns `("jev", "gemini")`.
4. **Side by side in `JudgementPort.judge`.**
   - Both calls are opened as today, then both are asked at once.
   - If Jev answers every question outside its abstain band, the `judgement.answered` row is
     written at once and `judge` returns. A Gemini call still in flight is never awaited: it
     finishes in a task the port holds, charges its call, and appends a `judgement.shadowed`
     row (judgement_id, site, model, latency, usd, per-question answers or the failure).
   - If Jev fails or abstains on any question, `judge` awaits Gemini, which has already
     started, and merges per question as today. Gemini's attempt is then in the row itself.
   - A Gemini error or timeout never fails or delays a judgement Jev answered.
   - Each attempt on a row keeps its per-question `answers` (label, p_proceed, decision), so
     Jev's lean on an abstained question is recorded beside Gemini's answer.
   - A process that exits with a shadow call in flight leaves its `gateway.opened` row open,
     and the existing holder recovery charges it at its estimate. No new mechanism is added.
   - `ask_leg` (calibration) is unchanged.
5. **`python -m core judgement-agreement [--since DATE]`.**
   - Per site, over live tasks (calibration tasks excluded), it counts the judgements where
     both answered: how many agree on the action (proceed/caution/abstain) and how many on the
     label, plus disagreements by kind (Jev proceed / Gemini caution, and so on).
   - It also counts the judgements where one side failed.
   - It prints JSON. The pure counting function sits in `core/judgement.py`.
6. **Recalibration.**
   - The seven seed cases in `docs/judgement-baseline.md` are rebuilt from their issues, with
     the same labels, into `~/Desktop/Valor/research/2026-11-16-gemini-decisions/` (private:
     the client names stay out of the repo).
   - `python -m core calibrate` runs on both legs. `UNDERSPECIFIED.calibrated` and its comment
     are updated to the new record: per-leg score, Brier, n, and which cases were rebuilt.
   - The wording is not changed to make a case pass. A Gemini miss is reported as it stands.
7. **Docs.** `docs/judgement-layer.md` (legs, router, side by side, the shadow row, the
   agreement command), `core/README.md`, `tools/README.md`.
   - Scope addition: the doc that names *OpenAI's* Decisions API, citing a community post, as
     the images design is corrected to name Google's leg, which takes images. Debt paid: a
     wrong vendor citation, found by the assessment. Images are still not routed.

## Out of scope

- Jev stays first-line, and deep research is not touched.
- No second fallback is kept, and no image routing is added.
- No new check, gate, or guard.
- The breadth and overlap floors stay as they are. Both sites are in shadow
  (`calibrated=None`), so Gemini's more frequent abstains hold nothing.
- The unused `judgement.Shared` is noted for a later bloat pass, not touched here.

## Tests

- **Adapter.** The body has noul criteria, choice criteria, the input as one text block, and
  the `x-goog-api-key` header. The live fixture decodes, for noul and choice. Malformed cases:
  no answers, a missing question, a probability out of range, a non-JSON body, a wrong model
  if the body names one. Usage maps, and a non-200 is `http_status` billed none.
- **Port, local upstream speaking Gemini's format** (`tests/judgement_upstream.py` loses
  OpenRouter's):
  1. Jev confident and Gemini slowed by 2 s: `judge` returns before Gemini answers, then
     exactly one `judgement.shadowed` row and one charge arrive.
  2. Jev abstains: the row's model includes `gemini-decision-1`, the leg is `fallback`, and
     the attempt carries latency_ms, usd_micros, and both legs' answers.
  3. Jev confident on one question and abstaining on another: the leg is `both`.
  4. Jev returns `max_tokens_exceeded`: Gemini answers.
  5. Gemini times out or returns 500 while Jev is confident: the judgement is answered with
     no added delay, and the shadow row records the failure.
  6. Both fail: `judgement.failed`, as before.
  7. A stopped task is refused before either provider is asked.
- **Agreement.** Rows covering agree, disagree on action, disagree only on label, a failed
  side, a calibration task excluded, and the `--since` window.
- **Composition root.** The `gemini` leg reads `GOOGLE_API_KEY` from `google-key`; a missing
  key raises `MissingKey`; loopback gets the placeholder; `calibrate` refuses a non-default
  Gemini URL.
- **Existing tests.** Tests that build `OpenWeight` (`test_pipeline`, `scripted`,
  `test_reactions`) move to the new leg.

## Done

- Acceptance as pitched: a Jev abstain is answered by `gemini-decision-1`, and its row names
  the model, cost, and latency. Qwen/OpenRouter is gone from settings (grep).
- The test-coverage fallback p50 is under 2 s over a day of real calls, read from the ledger
  after rollout.
- On 2026-11-30, `judgement-agreement` prints the count per site.
