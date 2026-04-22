# Prompt Evaluation Reference

Guidance for evaluating prompts systematically. Load this when iterating on a prompt that ships to users, powers a skill, or drives an agent — not for one-off prompts written and discarded inside a single conversation.

---

## Why evals exist

**Prompt engineering** is how you write a prompt: multishot examples, XML structure, role framing, explicit output contracts. **Prompt evaluation** is how you measure whether the prompt actually works once real inputs hit it. The two are different disciplines. A well-engineered prompt with no eval is still an unmeasured claim.

The three common paths after drafting a prompt:

1. Ship it after one manual test. Fast, and wrong as soon as a user sends something you didn't imagine.
2. Tweak it against a handful of hand-picked cases. Better, but still anchored to the cases you thought to try.
3. Run it through an automated eval pipeline and score it against a dataset. Slower and more expensive up front; the only path that produces a defensible answer to "is this prompt better than the last one?"

Paths 1 and 2 are the default failure mode. Path 3 is the asset.

---

## When to invest in evals

Reach for evals when at least one of:

- The prompt is inside a skill, subagent, hook, or agent loop that runs many times against inputs you don't fully control.
- You are about to change a prompt that already ships, and "did this help" needs an honest answer.
- Users will see the output and "sometimes wrong" has a real cost.
- You are comparing two prompt variants and deciding which to keep.

Skip evals for genuinely one-shot prompts inside a single conversation — the pipeline overhead outweighs the signal.

---

## The five-step workflow

### 1. Draft the prompt

Write the prompt as a template with an interpolated input variable. Keep the template stable across the eval run — it is the thing being measured.

### 2. Build an eval dataset

A list of sample inputs that represent the real distribution the prompt will face. Each record is one input that will be substituted into the template.

- **Size**: tens for a rough read, hundreds for a confident one, thousands for production-grade. Start small and grow as signal demands.
- **Coverage**: include easy cases, edge cases, adversarial cases, and cases you've already seen fail in production. Do not over-index on cases the prompt already handles.
- **Sourcing**: hand-write, pull from real logs, or generate with Claude. Generated datasets are fine but review them — synthetic inputs tend to cluster in the middle of the distribution and miss the tails.
- **Stability**: treat the dataset as an artifact. Changing it mid-iteration invalidates comparisons to earlier runs.

### 3. Feed each input through the prompt and collect responses

Substitute each dataset input into the template, send to Claude, store the response alongside the input. Keep the model, temperature, and any tool config constant across the run — those are independent variables, not part of the prompt.

### 4. Grade the responses

Each (input, response) pair gets a score — typically an integer 1–10 where 10 is high quality and 1 is poor.

Before picking a grader, **define the evaluation criteria first**. For a code generation prompt the criteria might be: _format_ (only code, no prose), _valid syntax_ (parses), _task following_ (actually solves the stated problem). The criteria decide which grader type fits.

Three grader types, each suited to different criteria:

- **Code graders** — programmatic checks. Fast, cheap, deterministic. Right for anything you can express as a function: exact-match against a ground truth, output length bounds, presence/absence of specific tokens, JSON/regex/Python syntax validation, readability scores, schema conformance. Anything that returns a number or boolean without judgment.
- **Model graders** — another LLM call scores the response against a rubric. Right for judgment-heavy criteria: response quality, instruction-following, completeness, helpfulness, safety, tone. Flexible but less deterministic than code graders.
- **Human graders** — manual review. Slow and tedious. Right for criteria that resist both programmatic and LLM scoring (nuanced relevance, depth, subjective fit) and for calibrating automated graders against ground truth on a small sample.

Match the grader to the criterion. Format and syntax checks are wasted on a model grader; task-following judgment is wasted on a code grader. A single eval run often uses several graders in combination, one per criterion, then aggregates.

**Model grader rubric design.** The most common failure mode is middling scores clustered around 6. Ask the model for structured output that forces it to defend the score:

- a short list of strengths
- a short list of weaknesses
- one-sentence reasoning
- the numeric score

Requesting the qualitative fields before the score pushes the grader off the neutral default and produces more discriminating numbers. Return the reasoning alongside the score in your run record — it is the artifact that lets you debug low scores later.

**Aggregation.** Mean across the dataset is the default headline metric. Also keep the full per-record distribution — a regression that lowers a handful of hard cases while raising the easy ones often moves the mean in the wrong direction.

**Combining multiple graders.** A single record often needs more than one grader — e.g., a code-generation prompt evaluated on _format_, _valid syntax_, and _task following_ uses code graders for the first two and a model grader for the third. Combine the per-grader scores into a record-level score before aggregating across the dataset. A simple unweighted mean is the default; weight the components if one criterion genuinely matters more than the others for the use case. Keep the per-grader scores in the run record so regressions can be traced to a specific criterion.

**Binary validators.** When a criterion is pass/fail (parses or doesn't, matches ground truth or doesn't), it is fine to return `10` on pass and `0` on fail. A binary validator is still a code grader; the "scale" just happens to have two points. Compose these with richer graders in the combination step — do not smear a binary result into a subjective 1–10 rating.

**Grader and prompt co-design.** When the grader validates a specific output format, the prompt must ask for that format explicitly. A syntax validator that rejects anything other than raw code only helps if the prompt also says "respond with only the code, no commentary" (and ideally pre-fills an assistant turn like ```` ```code ```` to bias toward raw output). Prompt and grader evolve together; changing one without the other produces noise.

**Dataset carries per-record metadata the grader needs.** If different records use different validators (one expects JSON, another Python, another regex), each record carries a `format` field (or equivalent) that the grader dispatches on. Generated datasets should produce this field automatically so records route to the right validator without hand-sorting.

**Baseline is not a verdict.** A first run score is a starting point, not a quality judgment. What matters is whether the next iteration moves it in the right direction. A mediocre-looking baseline that improves 6.4 → 7.8 after prompt changes is a better outcome than a flattering-looking baseline that does not budge.

### 5. Iterate

Change the prompt, run the same dataset through the same grader, compare the new score to the old. Keep the winner. Repeat until improvements stall.

---

## Discipline around iteration

- **Change one variable at a time.** If you rewrite the prompt and swap the model in the same run, you cannot attribute the delta to either.
- **Hold the dataset and grader constant across compared runs.** Scores from different datasets are not comparable, period.
- **Record the full setup** (prompt, dataset hash, model, temperature, grader rubric, score) so a later comparison is actually apples-to-apples.
- **Watch per-record scores, not only the mean.** A prompt that lifts the mean by moving easy cases from 8 to 9 while dragging hard cases from 4 to 2 is a regression, not a win.
- **Beware grader drift.** LLM graders are also prompts. If you tune the grader rubric mid-iteration to explain a disappointing result, you have stopped measuring and started rationalizing.

---

## What a minimal eval run looks like

```
prompt_template = """..."""
dataset = [ {input: "...", expected: "..."}, ... ]

responses = [ claude(prompt_template.format(input=d.input)) for d in dataset ]
scores = [ grader(d.input, d.expected, r) for d, r in zip(dataset, responses) ]

baseline_score = mean(scores)

# edit prompt_template, re-run the same dataset and grader
new_score = mean(scores_after)

if new_score > baseline_score: keep the new prompt
```

This is enough to escape path 1 and path 2. Everything else — dashboards, regression tracking, CI integration — is scale, not substance.

---

## What this reference is not

This is guidance for **how to do evals**, not a set of audit rules for the `/claude-standards` skill to enforce. The `/claude-standards` audit does not grade prompt quality; it checks structural conformance of assets. If a future rule does require a prompt to have an eval (e.g., "prompts that ship inside agent loops must have a recorded baseline score"), that rule would land in `STANDARDS_SKILLS.md` or similar with its own severity — and this file would be the reference it points to.
