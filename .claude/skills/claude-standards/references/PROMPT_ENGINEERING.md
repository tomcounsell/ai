# Prompt Engineering Reference

Guidance for iterating on a prompt until it reliably produces the output you want. Load this when refining a prompt that ships inside a skill, subagent, hook, or agent loop — not for one-shot prompts written and discarded in a single conversation. For measuring whether a prompt change actually helped, see [`EVALS.md`](EVALS.md).

---

## What prompt engineering is

Prompt engineering is the discipline of improving a prompt through iteration, not writing it well on the first try. The underlying loop is simple:

1. State the goal the prompt has to hit.
2. Draft a baseline prompt — deliberately rough, not polished.
3. Evaluate it against a dataset and grader (see `EVALS.md`).
4. Apply one specific technique to the prompt.
5. Re-evaluate on the same dataset and grader.
6. Keep the change if the score moved up, discard if it didn't. Repeat.

Steps 4 and 5 are the loop, and you stay in it until improvements flatten. Everything else in this doc is detail on how to make that loop cheap, honest, and fast.

Writing a prompt once and shipping it is not prompt engineering — it's guessing. What makes this a discipline is the measurement feedback after every change.

---

## Start from a deliberately naive baseline

The instinct to "make the first version good" is wrong. A polished first attempt destroys your measurement: if the baseline scores 7/10 there is little headroom to observe technique impact, and you cannot tell which techniques moved the number or by how much.

Write the stupid version first. For a meal-plan prompt that should account for height, weight, goal, and restrictions, the baseline can be as bare as:

```
What should this person eat?
- Height: {height}
- Weight: {weight}
- Goal: {goal}
- Restrictions: {restrictions}
```

No role framing, no structure, no examples, no output contract. Run it through the eval. The first score will look bad — 2 or 3 out of 10 is normal for a naive baseline — and that is the point. A low baseline produces clear signal when the next technique lands. A flattering baseline hides whether any technique did anything.

Baseline score is not a verdict on quality; it is the starting line from which you measure movement.

---

## Keep the iteration cycle cheap

Evaluation isn't free. Each run costs tokens, wallclock, and attention. If the loop is slow, you will run it less often, change more variables per iteration, and lose the ability to attribute improvements to specific techniques.

Two levers keep the loop cheap:

- **Small dataset during iteration.** Two to five cases is right for the tight loop — enough to feel the direction of change, small enough to rerun in seconds. Save larger datasets (tens to hundreds) for final validation before shipping.
- **Conservative concurrency.** Model APIs rate-limit. Start at 3–5 concurrent requests to avoid the retry-loop penalty; raise only if your quota supports it. A run that fires 20 parallel calls and half fail to rate limits is slower than a run that fires 3 and all succeed.

The cheaper the loop, the more iterations you will actually run, and the more disciplined each iteration can be.

---

## Tell the grader what matters

A generic grader scores output against whatever it thinks quality means. You will get middling scores that don't track your actual requirements, and you won't be able to tell whether your prompt improved the thing you care about or a tangential feature the grader fixated on.

Before running the eval, write down the criteria that matter for the use case and pass them to the grader explicitly. For a meal plan this might be:

- Total daily calories specified
- Macronutrient breakdown included
- Meals named with portions and timing

The grader now scores against the criteria you care about, not the ones it invents. Low scores become diagnostic (you know what is missing), and high scores become credible (you know what the grader was checking). See `EVALS.md` for more on grader/prompt co-design.

---

## Read the per-case feedback, not just the mean

A score of 4.2 is not actionable. The per-case breakdown — which inputs scored low, what the grader said was missing, which strengths it called out — is the artifact that points at the next technique. If three out of five cases all fail for lack of quantified portions, the next iteration is about output structure, not role framing.

The mean answers "is this better?" The per-case detail answers "what do I change next?" You need both, and most of the work happens in the second.

---

## Discipline around iteration

- **Change one technique at a time.** If you add a role frame and restructure the output format in the same iteration, you cannot attribute the delta to either.
- **Hold the dataset and grader fixed across compared runs.** Scores from different datasets are not comparable. Swapping the grader rubric mid-iteration to explain a disappointing result means you have stopped measuring and started rationalizing.
- **Record every iteration** — prompt text, dataset hash, grader rubric, resulting score, and which technique you applied. Without a log, "I think that one was worse" is not evidence, and you cannot reliably roll back.
- **Stop when improvements flatten.** Prompts have diminishing returns. Three iterations that each barely move the score is usually a signal to ship what you have, not to keep tweaking.

---

## Common techniques

Step 4 of the loop is "apply one technique." The sections below catalog the high-impact ones, each with the tradeoff that tells you when to reach for it. Apply one per iteration so the score delta is attributable.

### Clear and direct

The first line of a prompt carries disproportionate weight — it sets what the rest is interpreted against. A vague opening ("I need to know about...", "I was reading about X and wondering...") leaves the model guessing at the task. A clear, direct opening commits to a specific action.

Two sub-principles:

- **Lead with a direct action verb.** "Write", "Generate", "Identify", "Summarize", "Extract" — not "Can you tell me about..." or "I'd like to know...". The verb names the task; everything after it specifies it.
- **Use instructions, not questions.** "Identify three countries that use geothermal energy with generation stats for each" produces a better response than "What countries use geothermal energy?" even though both are answerable. The instruction commits to a format and scope; the question doesn't.

Applied to the meal-plan baseline ("What should this person eat?" → "Generate a one-day meal plan for an athlete that meets their dietary restrictions"), the score moved from 2.3 to 3.9. The technique is cheap and almost always helps. It doesn't replace later techniques — it clears the floor so they can land.

### Specific guidelines and process steps

Clarity about *what* to do is the floor. Specificity about *what the output should contain* and *how the model should reason before answering* is where most of the score comes from. Two flavors, often used together:

**Output guidelines** — qualities the response must have. Length, structure, format, required fields, tone, constraints. For a meal plan: total daily calories, protein/fat/carbs broken down, meal timing, portions in grams, foods that respect restrictions. Listed as an enumerated block, these turn a vague request into a contract the model can satisfy. Use these on almost every prompt — they are the cheap safety net that produces consistent shape.

**Process steps** — an explicit sequence the model should think through before answering. Right for problems that reward considering multiple angles: a sales-performance analysis should examine market, industry, individual, organizational, and customer-feedback factors *before* concluding, not latch onto a single cause. Process steps make chain-of-thought explicit without requiring the model to improvise it.

Guidelines say what the output must contain; process steps say how the model arrives at it. Add guidelines to almost every prompt. Add process steps when the task is analytical, diagnostic, or decision-heavy — anywhere a one-pass answer would miss something.

In the meal-plan example, adding output guidelines (calories, macros, timing, portions in grams, restriction-respecting foods) moved the score from 3.9 to 7.9. This is typically the single biggest technique lift in an iteration cycle.

### XML structure for content-heavy prompts

Once a prompt interpolates non-trivial content — uploaded data, prior context, multiple variables — the model can lose track of where one section ends and the next begins. XML tags fix this by giving each block a clear boundary and a descriptive name.

```
<athlete_information>
- Height: 188 cm
- Weight: 82 kg
- Goal: build muscle
- Dietary restrictions: vegetarian
</athlete_information>

Generate a one-day meal plan based on the athlete information above.
```

Tag names should describe the content, not generic `<data>` or `<input>`. `<sales_records>`, `<my_code>`, `<docs>`, `<athlete_information>` — the more specific the name, the better the model can reason about what each block is for.

Reach for tags when:

- The prompt includes a long block of context (documentation, data dump, transcript).
- The prompt mixes content types (code *and* its docs, query *and* results).
- Multiple variables are interpolated and you want the model to treat them as distinct.

For short, single-variable prompts the benefit is small — the failure mode XML prevents is ambiguity, and short prompts rarely suffer from it. Tags start paying off once the prompt crosses roughly a screen of mixed content.

### Examples (one-shot and multi-shot)

Examples show the model what good output looks like in a way instructions can't. A rule like "respond in a specific JSON shape" works up to a point; a concrete sample input paired with the exact output you wanted leaves no room for interpretation. This is what makes examples one of the highest-leverage techniques — they show rather than tell.

Examples earn their keep for:

- **Edge cases the model handles poorly by default.** Sarcasm in sentiment classification is the canonical case — "Yeah, sure, that was the best movie I've seen since 'Plan 9 from Outer Space'" reads positive on the surface and is actually negative. One example mapping a sarcastic input to "Negative" teaches the pattern more reliably than any instruction about sarcasm.
- **Complex output formats.** A JSON schema with nested fields and optional subtypes is nearly impossible to specify in prose without the model dropping a field. One worked example and the shape locks in.
- **Tone and style.** "Formal but friendly" means different things to different readers. Show one response you'd ship and the register is pinned.
- **Ambiguous inputs.** When the same input could plausibly produce several valid outputs, examples declare which valid output you actually want.

Structure each example with XML tags so the model can tell input from output:

```
<sample_input>Oh yeah, I really needed a flight delay tonight! Excellent!</sample_input>
<ideal_output>Negative</ideal_output>
```

**One-shot vs multi-shot.** One example is often enough to lock in format. Multi-shot (two to five examples) is for covering *varied* edge cases — a positive, a sarcastic negative, a genuine negative, a neutral — so the model doesn't overfit to a single example. Returns diminish past five; past that you are mostly paying tokens for noise.

**Source examples from your own eval runs.** Once you have an eval pipeline (see `EVALS.md`), the highest-scoring outputs are the best source for examples. By construction, they are outputs the grader rated as good for your actual use case — more calibrated than anything a stakeholder imagines in the abstract. The loop: run eval, pick the 10/10 records, paste the (input, output) pair into the next prompt iteration, rerun. Often the shortest path to closing the last stretch of the iteration curve.

**Explain *why* each example is good.** Don't just drop the (input, output) pair — add a one-sentence justification.

```
<ideal_output>...</ideal_output>
This output is well-structured, breaks down macros explicitly, and ties every meal back to the stated goal.
```

The justification teaches the model which dimensions mattered, not just that this particular output was accepted. It turns the example from a template into a reasoning pattern.

### System prompts (role and behavior framing)

Pass a system prompt to set *how* the model responds, not *what* it responds. A math-tutor system prompt ("You are a patient math tutor. Guide students toward answers with hints rather than solving the problem outright.") produces different responses to the same user question than a blank system prompt or a contrarian one.

The system prompt is the right place for:

- Persona, voice, or register that should apply to every response.
- Behavioral rules ("Never solve problems directly — ask questions that guide the user to the answer.").
- Long, stable context the user shouldn't have to re-send every turn (tool inventory, policy, domain background).

Keep the user message for the specific request. Splitting the two keeps the system prompt cacheable (see the caching section below) and keeps user intent on the user side where the model expects it.

Lead the system prompt with the role itself ("You are a ..."), then the behavioral rules. The order primes the rest of the prompt — the model reads behavior through the lens of the role.

### Extended thinking (letting the model reason first)

Claude's extended-thinking mode gives the model a thinking budget before it produces the final response. The thinking text is visible; the final response follows.

When it's worth it:

- The prompt is a hard reasoning task (math, multi-step planning, constraint satisfaction) and you've already squeezed the easier techniques.
- Your eval baseline plateaued below your target and the remaining errors look like missed reasoning steps rather than formatting issues.

When it's not:

- Simple extraction, formatting, or classification tasks. Thinking tokens are charged like output tokens and added latency hits every request.
- Prompts that already score well. Thinking rarely rescues a prompt that's failing on clarity, structure, or examples.

Treat extended thinking as the last technique you add, not the first. If you can lift the score with a clearer instruction, better examples, or output guidelines, do that first — it's cheaper and more durable.

---

## Steering the output

The techniques above shape *what* the model produces. Two mechanical levers control *how* its response begins and ends, independently of what the prompt says.

### Prefilling the assistant's turn

You can start the assistant's message for it. The model continues from exactly where your prefill ends, not from a fresh line or a complete sentence.

Use cases:

- **Lock in format.** Prefill ` ```json ` and the model's next tokens are the JSON, not a markdown preamble. Prefill `{"` and it's a JSON object opening with a string key.
- **Steer framing.** In a debate-style prompt, prefilling "Coffee is better because" forces the model to argue for coffee rather than hedge.
- **Skip preamble.** Prefilling `<answer>` (or whatever tag you expect) cuts the "Sure, here's the answer:" fluff the model otherwise adds.

The response starts *from* the prefill, so when you stitch output back together you need to prepend the prefill yourself.

### Stop sequences

A stop sequence is a string that, when generated, halts the response. The stop string itself is not included in the output.

Use cases:

- **Terminate on a delimiter.** Paired with a prefill like ` ```json `, a stop sequence of ` ``` ` produces clean, parseable output between the fences with no tail commentary.
- **Bound a section.** In a prompt that generates until a tag, the tag is the stop sequence.
- **Prevent runaway completion.** If the model tends to keep going past the useful part of the response, a stop token caps it.

### Prefill + stop = structured output

The combination is the fastest way to get raw structured output without prompting gymnastics. Prefill the opening delimiter, use the closing delimiter as the stop sequence, and the model produces exactly the content between them:

```
user:     "Return the athlete's daily macros as JSON."
assistant (prefill): ```json
stop sequence: ```
```

The response is the JSON body. No "here's the output:" preamble, no trailing commentary, no post-processing. For higher reliability or more complex schemas than prefill+stop can enforce, see the "Structured output via tools" section in [`TOOL_USE.md`](TOOL_USE.md).

---

## Tuning the sampler: temperature

Temperature (0.0–1.0) controls how aggressively the model picks lower-probability tokens. It is a sampler parameter, not a prompt rewrite, but it interacts with the prompt enough to count as a prompting decision.

- **Temperature 0** — nearly deterministic; the model picks the highest-probability token at each step. Use for extraction, classification, code generation, anything where "the right answer" is well-defined and repeatability matters.
- **Temperature 1** — maximum spread; more creative, more varied output per run. Use for brainstorming, marketing copy, fiction, anything where variety is the point.
- **Middling (0.3–0.7)** — a compromise. Often the default when you're not sure.

Hold temperature constant across eval comparisons. A score that moves from 6 to 7 because you changed temperature is not a win for the prompt — it's a sampler change you could have made without touching the prompt at all.

Higher temperature does not guarantee different output on any given call; it only raises the probability of variation. Two runs at temperature 1 can produce similar output.

---

## Multimodal prompts (images and PDFs)

When the prompt includes images or PDFs, the techniques above still apply, but their importance shifts. The model has to extract facts from the visual content *and* act on them — twice as many places where ambiguity can hide.

- **Step-by-step structure matters more.** A prompt that asks "describe the fire risk in this property image" often fails. A prompt that lists what to inspect — tree density, property access, roof overhang — and asks for a score per dimension lands consistently.
- **Examples compound.** One-shot and multi-shot examples are especially powerful for visual tasks because the model is otherwise guessing at your taxonomy. Alternating image/text example pairs teaches the model how to read the next image.
- **Ask for verification.** Adding "before scoring, list what you can and cannot see clearly in the image" catches hallucination early. The model admits missing detail rather than inventing it.

PDFs follow the same discipline applied to a larger mixed-content input. When a PDF is long or contains many charts and tables, favor retrieval (see [`RAG.md`](RAG.md)) over dropping the whole document into one prompt.

---

## Optimizing for prompt caching

When a prompt has large stable content (system prompt, tool schemas, multi-shot examples, a long document), prompt caching reuses the model's preprocessing work across requests. The prompting implications:

- **Order stable content first.** Cache order is tools → system prompt → messages. Put the content most likely to stay identical at the top: tool schemas, then system prompt, then user messages. Anything after the cache breakpoint has to be reprocessed.
- **Put volatile content last.** User messages change every turn. Keep them at the end, after the cached block.
- **One breakpoint usually; up to four available.** A single breakpoint at the end of the stable content is enough for most prompts. Multiple breakpoints (one after tools, one after system prompt) let partial cache hits absorb some content changes — worth it only when you have distinct stable tiers that churn at different rates.
- **Minimum size matters.** Content under ~1024 tokens won't cache. If your stable prefix is short, either add more stable content to cross the threshold or don't bother with breakpoints.
- **Cache invalidates on any upstream change.** Adding a single token to the system prompt invalidates the cache for everything after it. Once you've decided the system prompt, stop tweaking it between production runs, or the cache never warms.

Cache hits don't change the model's output — they change cost and latency. If you're still iterating on the prompt (via `EVALS.md`), the cache is irrelevant; you want it off or cleared between runs so scores reflect the prompt, not cache state.

---

## What a minimal iteration cycle looks like

```
goal = "..."
prompt_template_v1 = "... {input} ..."
dataset = [ ... 3 cases ... ]
grader_criteria = "..."

# Baseline
responses_v1 = [ claude(prompt_template_v1.format(input=d)) for d in dataset ]
score_v1 = eval(responses_v1, dataset, grader_criteria)  # e.g., 2.3

# Iterate: apply ONE technique (say, add output structure)
prompt_template_v2 = "..."
responses_v2 = [ claude(prompt_template_v2.format(input=d)) for d in dataset ]
score_v2 = eval(responses_v2, dataset, grader_criteria)  # e.g., 4.1

# Decision
keep = prompt_template_v2 if score_v2 > score_v1 else prompt_template_v1

# Repeat with the next technique against `keep` as the new baseline.
```

This loop is enough to iterate honestly. Everything more — dashboards, regression tracking, CI-enforced thresholds — is scale, not substance.

---

## What this reference is not

This doc covers the iteration workflow and the single-prompt techniques that feed it. Adjacent topics have their own references:

- **[`EVALS.md`](EVALS.md)** — how to measure whether a prompt change actually helped.
- **[`TOOL_USE.md`](TOOL_USE.md)** — when the prompt needs Claude to call functions, including structured-output extraction via forced tool calls.
- **[`RAG.md`](RAG.md)** — when the prompt needs retrieved context from a large corpus instead of everything inline.
- **[`AGENTS_AND_WORKFLOWS.md`](AGENTS_AND_WORKFLOWS.md)** — when the task needs multiple prompts orchestrated together (chaining, routing, parallelization, evaluator-optimizer).
- **[`MCP.md`](MCP.md)** — when the tools your prompt calls are maintained by someone else via the Model Context Protocol.

If the prompt you're writing lives inside one of those systems, the techniques in this doc still apply — they just run inside a larger design whose reference lives elsewhere.
