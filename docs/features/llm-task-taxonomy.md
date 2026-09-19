# LLM Task Taxonomy

**Status:** In progress (issue #3410)

Every non-harness LLM call site in `agent/ bridge/ worker/ tools/ reflections/ scripts/` declares one `LLMTask` (`agent/llm/tasks.py`) as a module constant beside its output type and passes it as `task=` to `run_typed`. The declaration names the site, its kind (`classification` or `thinking`), the backend it lands on, its error-cost tier, and whether charter §7 pins it to the subscription backend (`client_only`). The router (`agent/llm/router.py::resolve`) reads the declaration with the call's project key and picks the leg.

## Site Table

One row per declared site, read by `tests/unit/test_llm_task_taxonomy.py` (doc/code parity): the site id, kind, landed backend, error-cost tier, and §7 class must match the declaration `agent/llm/tasks.py::declared_sites()` finds. The comparison record is the `classifier_comparison` evidence row on case `1ec40086ca1d422e90ef747775ff7f64` that argues for the landed backend; thinking sites and the `client_only` triage site carry none.

| Site | Kind | Backend | Error cost | §7 class | Comparison record |
|------|------|---------|------------|----------|-------------------|
| `agent_catchup.judge` | classification | anthropic | low | eligible | `20894afc13db4b18864b974dcdd8970b` |
| `classifier.intake_intent` | classification | ollama | medium | eligible | `63997aebc17e405884349d947784da44` |
| `classifier.work_type` | classification | anthropic | medium | eligible | `c2c3564f472b4efbb38c77798ab8c799` |
| `compat.network_probe` | thinking | anthropic | medium | eligible | n/a |
| `context_recall.advised` | classification | anthropic | medium | eligible | `322be273a83741ec80d985568671086e` |
| `cross_vendor_judge.review` | thinking | anthropic | medium | eligible | n/a |
| `doc_summary.summarize` | thinking | anthropic | medium | eligible | n/a |
| `documentation.generate` | thinking | anthropic | medium | eligible | n/a |
| `email_cs.action` | thinking | anthropic | medium | client_only | n/a |
| `email_cs.triage` | classification | anthropic | medium | client_only | n/a |
| `evaluate_build.verdicts` | thinking | anthropic | medium | eligible | n/a |
| `health_check.judge` | classification | anthropic | medium | eligible | `1684a0cc15f74d6c8b860fdd42c6b62e` |
| `image_analysis.analyze` | thinking | anthropic | medium | eligible | n/a |
| `image_tagging.tag` | thinking | anthropic | medium | eligible | n/a |
| `impact_finder.rerank` | thinking | anthropic | medium | eligible | n/a |
| `improvement_collect.promise_judge` | classification | anthropic | low | eligible | `5e3f8272c4f8428b8f1fbff92274b749` |
| `improvement_eval.openrouter_arm` | thinking | anthropic | medium | eligible | n/a |
| `improvement_eval.serves_charter` | thinking | anthropic | medium | eligible | n/a |
| `injection_inspection.risk` | classification | anthropic | medium | eligible | `49fc1cffb128440e948a16c27fae7e90` |
| `intent_classifier.intent` | classification | anthropic | high | eligible | `0c00bc4f0fe445a59c4b2466bc0162ad` |
| `job_router.route` | classification | ollama | high | eligible | `80163016557d4be4a3f8e99c85090a79` |
| `knowledge.converter_probe` | thinking | anthropic | medium | eligible | n/a |
| `knowledge.summarize` | thinking | anthropic | medium | eligible | n/a |
| `media.image_description` | thinking | anthropic | medium | eligible | n/a |
| `memory_audit.classify` | classification | ollama | medium | eligible | `03a39666e88249688db815c2502f9e57` |
| `memory_consolidation.merge_plan` | thinking | anthropic | medium | eligible | n/a |
| `memory_eval.query_generation` | thinking | anthropic | medium | eligible | n/a |
| `memory_eval.relevance_grading` | thinking | anthropic | medium | eligible | n/a |
| `memory_extraction.extract` | thinking | anthropic | medium | eligible | n/a |
| `pm_briefings.draft` | thinking | anthropic | medium | eligible | n/a |
| `promise_gate.verdict` | classification | anthropic | low | eligible | `77b5182f677c4a798f07874736ffe6d2` |
| `read_the_room.verdict` | thinking | anthropic | medium | eligible | n/a |
| `reflections.log_analysis` | thinking | anthropic | medium | eligible | n/a |
| `routing.needs_response` | classification | anthropic | high | eligible | `06b20b102be04cbfb5d16676b859de72` |
| `routing.terminus` | classification | anthropic | high | eligible | `4004915da988443089165ac47ce26e56` |
| `routing.work_request` | classification | anthropic | high | eligible | `fa8ededd07e84acd9d9b293533b69c57` |
| `session_completion.novelty` | classification | anthropic | medium | eligible | `2a4a367a023642828b7e35a6a3f961d1` |
| `test_judge.judge` | thinking | anthropic | medium | eligible | n/a |
| `valor_calendar.feature_name` | thinking | anthropic | medium | eligible | n/a |

The rest of this page (the router rules in order, eligibility with the `valor` pin and the fail-closed rule, the acceptance bar tiers and criteria as the reviewer applies them, and where comparison records live) is written by the documentation task.
