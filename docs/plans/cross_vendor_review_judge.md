---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-06-22
tracking: https://github.com/tomcounsell/ai/issues/1626
last_comment_id:
revision_applied: true
---

# Cross-Vendor Verification: Independent Non-Claude Reviewer

## Problem

Our PR review is **all-Claude**. The author (Claude, in BUILD) writes the diff; every reviewer (Claude, in REVIEW) checks it. Even the multi-judge consensus infrastructure shipped for #1309 runs K parallel judges (`code-quality`, `risk`) that are *all Claude* with different system prompts. Because authoring and reviewing share a training distribution, we have a structural blind spot: a class of defect that Claude systematically fails to *author* and systematically fails to *catch* passes straight through every gate.

**Current behavior:** Author and 100% of reviewers are Claude. No cross-vendor verification on any diff. The consensus layer in `agent/sdlc_review_consensus.py` aggregates only Claude judges.

**Desired outcome:** On high-stakes diffs, an independent non-Claude reviewer (default GPT-5.5) runs alongside the Claude judges, returns a per-judge dict in the same shape the consensus layer already consumes, and its verdict feeds the existing `any-blocker-wins` consensus rule. A cross-vendor blocker is preserved (never averaged away) and recorded in the SDLC verdict record.

## Freshness Check

**Baseline commit:** `6b407cde` (main at plan time)
**Issue filed at:** 2026-06-11T06:15:47Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/sdlc_review_consensus.py` — `compute_consensus(judges, rule="any-blocker-wins")` is vendor-agnostic; consumes `{judge_id, verdict, blockers, ...}` dicts only. Still holds.
- `.claude/skills-global/do-pr-review/SKILL.md:659-719` — multi-judge orchestration block present and matches the issue's recon. Note: the issue said the skill lives at `.claude/agents/code-reviewer.md` + `/do-pr-review`; the actual skill is `.claude/skills-global/do-pr-review/SKILL.md` (global skill, hardlinked to `~/.claude/skills/`). Corrected inline.
- `tools/sdlc_verdict.py:164-260` — `record_verdict(judges=, consensus=)` REVIEW-only side-fields present. Still holds.
- `config/settings.py:32` — `openai_api_key` field present. `tools/image_gen/__init__.py:107-116` — working `OpenAI()` client pattern present. Still holds.
- `scripts/pr_shape_classify.py` — shapes `docs-only|lockfile-only|small-patch|mixed|feature`. No high-stakes tier exists. Confirmed.

**Cited sibling issues/PRs re-checked:**
- #1309 — CLOSED. Shipped via PR #1343 (merged 2026-05-08) + follow-up #1347. The consensus layer this issue builds on is live and stable.

**Commits on main since issue was filed (touching referenced files):**
- `5bc6243a` SDLC router: verdict normalization + plan-existence gate + stale-verdict supersession (#1638/#1640/#1641) — touches verdict plumbing but does NOT change the `judges`/`consensus` contract in `sdlc_verdict.py` or the consensus math. Irrelevant to this plan's integration point.

**Active plans in `docs/plans/` overlapping this area:** none touching the review/consensus path.

**Notes:** Only drift is the skill path correction and the GPT-5 → gpt-5.5 model update. Premise is intact.

## Prior Art

- **#1309 / PR #1343, #1347**: Multi-judge consensus at Review — built the entire `compute_consensus` + `record_verdict(judges=, consensus=)` infrastructure this plan reuses. Succeeded; live. This plan adds one more dict producer to that pipeline rather than building anything parallel.
- No prior cross-vendor review attempt found. OpenAI is used in-repo for image gen, embeddings, transcription, link analysis — never in the review path. Greenfield for the review use-case, but the *integration substrate* is mature.

## Research

**Queries used:**
- "OpenAI GPT-5 model code review API 2026 chat completions model name"
- "cross-model LLM ensemble code review same training distribution blind spot diversity"

**Key findings:**
- **gpt-5.5 is the current strongest OpenAI coding model** (Chat Completions, 1M context, $5/1M input, $30/1M output; SWE-Bench Pro 58.6%). `gpt-5.5-pro` is higher-accuracy at $30/$180. Source: https://openai.com/index/introducing-gpt-5-5/ . **Informs:** default cross-vendor model = `gpt-5.5`, exposed as an env-overridable constant so it can be retargeted without a code change.
- **Cross-model review works because a different vendor's training distribution yields uncorrelated error distributions** — one model's blind spot is structurally likely caught by another. Source: https://arxiv.org/pdf/2512.12536 , https://arxiv.org/html/2606.01490 . **CAVEAT that directly shapes design:** consensus/averaging can *amplify* shared errors and filter out minority-correct findings; diversity-based selection recovers ~95% of the ideal-ensemble gain. **Informs:** the cross-vendor judge must feed `any-blocker-wins` (a single cross-vendor blocker forces CHANGES REQUESTED — it is NOT diluted by Claude judges' approvals). This is exactly what `compute_consensus` already does with `blockers_max` — confirming no math change is correct, not just convenient.

## Data Flow

1. **Entry point**: `/do-pr-review` runs at the REVIEW stage on an open PR. It classifies the diff via `python -m scripts.pr_shape_classify --pr N` and reads the judge roster from `SDLC_REVIEW_JUDGES`.
2. **Trigger evaluation**: the skill decides whether the cross-vendor judge is enabled (see Solution — gated by `SDLC_REVIEW_CROSS_VENDOR` env var AND a high-stakes shape/size predicate). If disabled, the existing Claude-only path runs unchanged.
3. **Judge dispatch**: Claude judges (`code-quality`, `risk`) spawn as agent forks returning dicts via stdout (unchanged). The cross-vendor judge runs as a **separate code path**: a Python CLI (`tools/cross_vendor_judge.py`) invoked via Bash that calls the OpenAI Chat Completions API with the diff + a structured review rubric, and emits one **envelope** as JSON to stdout. The envelope is ALWAYS `{"status": "ok"|"skipped", ...}` (see Solution → Output envelope contract). On `status="ok"` the envelope carries a complete `judge` dict (`{judge_id: "cross-vendor", verdict, blockers, tech_debt, confidence, reasoning_summary, meta}`); on `status="skipped"` it carries `{"status":"skipped","reason":...,"meta":...}` and NO judge dict. The envelope is the only contract the parent parses — it never passes the raw envelope to `compute_consensus`.
4. **Collection**: the `/do-pr-review` parent parses the envelope. It appends the inner `judge` dict to the judge-dict list **only when `status=="ok"`**. On `status=="skipped"` it appends nothing and records the skip reason for the aggregate comment. This is the fix for the critique blocker: a skip envelope can never reach `compute_consensus` (which would `raise ValueError` on the missing `verdict`/`blockers`) or `record_verdict(judges=)` (whose `_validate_judges_payload` would return `False` and silently drop the ENTIRE verdict record, losing the Claude judges too).
5. **Consensus**: parent calls `compute_consensus(dicts, rule="any-blocker-wins")` — **unchanged**. `blockers_max` already preserves a cross-vendor blocker.
6. **Per-judge comments**: parent posts a `## Review (Judge cross-vendor):` comment (sequential, same pattern as Claude judges).
7. **Verdict record**: parent makes ONE `record_verdict --stage REVIEW --judges-json ... --consensus-json ...` call. The cross-vendor dict is persisted in `_judges` for the SDLC verdict record. **Output:** the aggregate `## Review: Approved|Changes Requested` comment + the recorded verdict the SDLC router consumes.

## Architectural Impact

- **New dependencies**: none new — `openai` is already a repo dependency; `OPENAI_API_KEY` already in `config/settings.py` and `.env`.
- **Interface changes**: none to `compute_consensus` or `record_verdict`. New CLI `tools/cross_vendor_judge.py` (entry point in `pyproject.toml`). New env vars for gating and model/cost config.
- **Coupling**: the cross-vendor judge is deliberately decoupled — it is a standalone CLI that emits the same dict contract the consensus layer already accepts. The consensus layer never learns there is a non-Claude judge. This keeps vendor knowledge isolated to one file.
- **Data ownership**: the cross-vendor judge dict is owned by `tools/cross_vendor_judge.py`; the `/do-pr-review` parent owns collection/consensus/record (unchanged ownership).
- **Reversibility**: fully reversible. Set `SDLC_REVIEW_CROSS_VENDOR=0` (or unset) and the cross-vendor judge never runs; the Claude-only path is byte-for-byte the prior behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (trigger policy + cost ceiling are the decision points)
- Review rounds: 1

The hard part is not the code (one CLI + skill wiring); it's getting the trigger predicate, failure behavior, and determinism right so the gate is trustworthy and reproducible.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `OPENAI_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENAI_API_KEY')"` | Cross-vendor judge API access |
| `openai` package | `python -c "import openai"` | OpenAI client (already a dependency) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/cross_vendor_review_judge.md`

## Solution

### Key Elements

- **`tools/cross_vendor_judge.py` CLI**: Standalone judge. Takes a PR number (or diff on stdin), fetches the diff, sends it to the OpenAI Chat Completions API with a structured review rubric, and emits exactly one **envelope** as JSON to stdout (see Output envelope contract below). On success the envelope wraps a judge dict in the same shape Claude judge forks return. Owns ALL vendor-specific knowledge (client, model, prompt, parsing). Never writes Redis, never posts comments.

- **Reserved `judge_id` constant (collision-safety fix)**: The cross-vendor judge id is a single source of truth — `CROSS_VENDOR_JUDGE_ID = "cross-vendor"` defined once in `tools/cross_vendor_judge.py`. The CLI stamps this constant onto every emitted judge dict; tests and any skill reference import it rather than re-typing the literal. The value is disjoint from the Claude judge ids (`code-quality`, `risk`), so `compute_consensus._dedup_last_wins` (which keeps last-wins per `judge_id`) can never collapse the cross-vendor entry onto a Claude judge and silently discard a blocker. This is what makes the any-blocker-wins guarantee hold when a third judge joins the roster.

- **Output envelope contract (the critique-blocker fix)**: The CLI's stdout is ALWAYS a JSON envelope with a top-level `status` discriminator — never a bare judge dict, never a partial dict:
  - `status="ok"` → `{"status":"ok","judge":{"judge_id":CROSS_VENDOR_JUDGE_ID,"verdict":<str>,"blockers":<int>,"tech_debt":<int>,"confidence":<float>,"reasoning_summary":<str>,"meta":{...}}}`. The `judge` sub-dict is guaranteed to contain all three required keys with the right types, validated AND COERCED by the CLI before emission (see Type-coercion note below).
  - **Type-coercion note (concern resolution)**: The model returns JSON whose types are not guaranteed — e.g. `blockers` may come back as `"1"` (string) or `1.0` (float), `verdict` as a non-string. Both downstream validators reject wrong types: consensus's `_validate_judge` (`agent/sdlc_review_consensus.py:24-35`) and `sdlc_verdict._validate_judges_payload` (`tools/sdlc_verdict.py:140-162`) BOTH require `judge_id` non-empty `str`, `verdict` `str`, `blockers` `int` (Python `bool` is an `int` subclass — exclude it explicitly, since `isinstance(True, int)` is `True`). Before wrapping a judge dict in a `status="ok"` envelope the CLI MUST coerce to those exact types: `judge_id` stamped from the `CROSS_VENDOR_JUDGE_ID` constant (never trusted from the model), `verdict` → `str(...)` normalized to the canonical verdict space, `blockers` → `int(...)` (rejecting `bool` and un-coercible values). If coercion fails (non-numeric `blockers`, missing key), the CLI degrades to a `status="skipped"` envelope rather than emitting a dict that one validator accepts and the other rejects. This guarantees the same judge dict passes BOTH `compute_consensus` and `record_verdict(judges=)` — never a dict that survives one path and silently breaks the other.
  - `status="skipped"` → `{"status":"skipped","reason":<str>,"meta":{...}}`. NO `judge` key, NO `verdict`/`blockers`.
  - The parent appends `envelope["judge"]` to the consensus-input list **iff `envelope["status"]=="ok"`**. A skip envelope contributes nothing to `compute_consensus` and nothing to `record_verdict(judges=)`. This is deliberate: the prior plan's flat `{"judge_id":"cross-vendor","skipped":true}` shape was runtime-fatal — it is missing `verdict`/`blockers`, so reaching `compute_consensus` raises `ValueError`, and reaching `record_verdict`'s `_validate_judges_payload` returns `False`, silently dropping the entire verdict record (Claude judges included). The status-discriminated envelope makes that impossible by construction: the skip branch has no path to either function.
- **Trigger gate**: A two-part predicate — `SDLC_REVIEW_CROSS_VENDOR` env var (operator kill switch, default off) AND a high-stakes shape predicate. The predicate reuses `scripts/pr_shape_classify.py`'s emitted `shape`: cross-vendor runs only when `shape=="feature"` (the full-gate, non-trivial shape — everything that is not a trivial safe shape). Trivial shapes (`docs-only`, `lockfile-only`, `small-patch`, `mixed`) never pay the cross-vendor cost. NOTE (concern resolution): there is intentionally NO separate line-count threshold env var. The classifier's `to_dict()` emits only `shape` (not `net_lines`), so a `LINE_THRESHOLD` knob would require the parent to re-run `gh pr view --json additions,deletions` — redundant plumbing the classifier already does internally but does not expose. The `feature` shape is itself the high-stakes signal (the classifier already filters out the <=20-net-line `small-patch` tier), so the gate is `feature`-shape + kill switch, full stop.
- **`/do-pr-review` wiring**: In the multi-judge orchestration block, after collecting Claude judge dicts, conditionally invoke `tools/cross_vendor_judge.py` and append its dict to the list before `compute_consensus`. Add a `## Review (Judge cross-vendor):` per-judge comment.
- **Failure behavior**: degrade-to-Claude-only by default (the cross-vendor judge is an *additive* safety net, not a single point of failure). If the OpenAI call fails (timeout, auth, rate limit, malformed response, **or an unsupported model id / unsupported request param**), the CLI emits a `status="skipped"` envelope and the parent simply does not append it — consensus proceeds with the Claude judges. A `SDLC_REVIEW_CROSS_VENDOR_REQUIRED=1` opt-in flips this to fail-closed (CHANGES REQUESTED if the cross-vendor judge could not run).
- **Model-id / unsupported-param resilience (the second critique-blocker fix)**: The `gpt-5.5` model id and the `seed` / `response_format={"type":"json_object"}` Chat Completions params are taken from OpenAI's published docs, NOT verified against this repo's account — the only in-repo OpenAI call today is `images.generate` (`tools/image_gen/__init__.py`), so there is no proven `chat.completions` precedent here. A wrong model id raises `openai.NotFoundError`; an unsupported param raises `openai.BadRequestError`. **Catch the broad base class `openai.OpenAIError` (concern resolution)** — it is the parent of `BadRequestError`, `NotFoundError`, `RateLimitError`, `APITimeoutError`, `AuthenticationError`, `APIConnectionError`, and every other SDK error — so the skip path covers the *entire* OpenAI failure surface, not just the two ids called out by name. The handler routes all of them through the **same skip path** (envelope `status="skipped"`, `reason` naming the exception class + the model id, `logger.warning`). A final `except Exception` catches non-OpenAI failures (e.g. JSON parse) and also routes to skip. Combined with the env-overridable model id, this means a bad default never hard-fails the review gate — the operator retargets `SDLC_REVIEW_CROSS_VENDOR_MODEL` and the judge resumes. The model id is therefore both env-overridable AND fail-safe.
- **Cost ceiling**: a configurable max-diff-token cap. If the diff exceeds the cap, the CLI truncates with a clear marker (and lowers its own `confidence`) rather than sending a 500K-line diff. Model + cap are env-overridable constants with grain-of-salt comments.
- **Determinism**: the CLI calls the API with `temperature=0` and `seed` set to a fixed value (or the PR head SHA), and records the model id + request params in the judge dict's `reasoning_summary` / a `meta` field so the SDLC verdict record captures exactly what produced the verdict. Reproducibility is "same diff + same model + same seed → same structured verdict" within API determinism limits.
- **Tri-state observability + token logging (concern resolution)**: every invocation emits exactly one `logger.info` line capturing the tri-state outcome — `ran` (status=ok with a verdict), `skipped` (status=skipped + reason), or `disabled` (gate off / not `feature`-shape, so the CLI was never invoked — logged by the parent). On `ran`, the line also records `model`, `prompt_tokens`, and `completion_tokens` taken directly from the API response `usage` object. **NO hardcoded USD rate table (concern resolution)**: the prior plan proposed an env-overridable per-1M-token `$` rate table, but those rates are unverified against the account and drift whenever OpenAI reprices — a hardcoded dollar figure would be a stale, misleading number baked into logs. Instead the CLI logs **raw token counts only**; cost is computed downstream by whoever owns the billing rates (or simply read from the OpenAI dashboard), never guessed in-process. The `model` + token counts are also stored in the judge dict's `meta` so the recorded `_judges` entry is self-describing. This gives an operator a grep-able audit trail of how often the judge runs, skips, and how many tokens it consumes — without baking in a rate table that goes stale.

### Flow

PR at REVIEW → `/do-pr-review` classifies shape → cross-vendor gate (`SDLC_REVIEW_CROSS_VENDOR=1` AND high-stakes shape) passes → spawn Claude judges (forks) **and** invoke `tools/cross_vendor_judge.py --pr N` → collect all dicts → `compute_consensus(rule="any-blocker-wins")` → post per-judge comments incl. cross-vendor → ONE `record_verdict --judges-json --consensus-json` → aggregate Review comment → SDLC router reads verdict.

### Technical Approach

- **Reuse, don't rebuild.** The consensus layer (`agent/sdlc_review_consensus.py`) and verdict recorder (`tools/sdlc_verdict.py`) require **zero changes** — verified in recon. The cross-vendor judge is a new dict producer only.
- **`judge_id = CROSS_VENDOR_JUDGE_ID` — a reserved, pinned constant (collision-safety fix).** `compute_consensus._dedup_last_wins` (`agent/sdlc_review_consensus.py:38-44`) keys dedup on `judge_id` and keeps the LAST entry per id. If the cross-vendor judge ever emitted an id that collided with a Claude judge id (`code-quality` / `risk`), the colliding entry would silently overwrite a real Claude judge dict — and if that overwritten dict carried the only blocker, `blockers_max` would drop it, defeating the any-blocker-wins guarantee. To make collision impossible by construction, the cross-vendor judge id is a single named constant `CROSS_VENDOR_JUDGE_ID = "cross-vendor"` defined in **one place** (`tools/cross_vendor_judge.py`, the sole owner of vendor knowledge) and imported anywhere the test suite or skill needs to reference it — never a re-typed string literal. The constant value `"cross-vendor"` is deliberately disjoint from the Claude judge ids (`code-quality`, `risk`), so dedup never collapses a cross-vendor entry onto a Claude one. With Claude `code-quality` + `risk` + `cross-vendor`, K becomes 3 distinct ids; `SDLC_REVIEW_K` is auto-clamped to `min(K, len(enabled_judges))` per the existing skill logic. A dedup-survival test (see Test Impact) asserts the cross-vendor blocker survives `_dedup_last_wins` aggregation alongside both Claude judges.
- **OpenAI client** mirrors `tools/image_gen/__init__.py`'s `OpenAI()` construction (`from openai import OpenAI; client = OpenAI(api_key=settings.openai_api_key)`), but the call is `client.chat.completions.create(model=..., temperature=0, seed=..., response_format={"type":"json_object"})`. NOTE: image_gen uses `images.generate`, so the `chat.completions` call shape, the `gpt-5.5` model id, and the `seed`/`response_format` params have NO verified in-repo precedent — treat them as unproven. Wrap the call so the broad base class `openai.OpenAIError` — covering `BadRequestError` (unsupported param), `NotFoundError` (bad model id), `RateLimitError`, `APITimeoutError`, `AuthenticationError`, `APIConnectionError`, etc. — routes to the skip envelope (see Solution → Model-id resilience), with a trailing `except Exception` for non-OpenAI failures. On success, parse the JSON, validate it contains all three `_REQUIRED_KEYS` (`judge_id`, `verdict` str, `blockers` int) with correct types, and ONLY then wrap it as `{"status":"ok","judge":<dict>}`. A response that parses but is missing a required key is treated as a skip (degrade), never emitted as a partial judge dict.
- **Rubric**: the CLI sends a system prompt instructing the model to output ONLY a JSON object matching the judge-dict schema, reusing the 10-item review rubric concepts from `do-pr-review/sub-skills/code-review.md` (correctness, regression risk, security, error handling) — adapted to a single structured response. No prose review; just the dict.
- **Env config** (all in `config/settings.py`, env-overridable, with provisional grain-of-salt comments):
  - `SDLC_REVIEW_CROSS_VENDOR` (default `0` / off)
  - `SDLC_REVIEW_CROSS_VENDOR_MODEL` (default `gpt-5.5`) — **env-overridable and fail-safe**: an invalid id raises `NotFoundError`, which routes to skip (degrade), never a hard crash. Grain-of-salt comment notes the id is provisional/unverified against the account.
  - `SDLC_REVIEW_CROSS_VENDOR_MAX_DIFF_TOKENS` (cost cap)
  - `SDLC_REVIEW_CROSS_VENDOR_REQUIRED` (default `0` — degrade-to-Claude-only)

  (No `LINE_THRESHOLD` var — see Trigger gate: `feature`-shape is the high-stakes signal; a line threshold would be unowned redundant plumbing.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] In `tools/cross_vendor_judge.py`, the OpenAI call is wrapped to catch the broad base class `openai.OpenAIError` (covering timeout, auth, rate limit, connection, `BadRequestError` unsupported param, `NotFoundError` bad model id) plus a trailing `except Exception` for non-OpenAI failures (JSON parse, type-coercion). Every handler must emit a `{"status":"skipped","reason":...,"meta":...}` envelope (NO `judge` key) AND log a `logger.warning` — not silently `pass`. Test asserts the warning fires and `status=="skipped"` for `OpenAIError` subclasses and for a generic parse failure.
- [ ] No `except Exception: pass` anywhere in the new file — each handler has an observable effect (skip envelope + log).

### Empty/Invalid Input Handling
- [ ] Test: empty diff (no changed files) → CLI emits a `status="ok"` envelope with a low-confidence "nothing to review" judge dict (verdict APPROVED, blockers 0), not a crash.
- [ ] Test: OpenAI returns malformed/non-JSON or a dict missing a required key → CLI emits `status="skipped"` (degrade), logs, does NOT emit a partial `status="ok"` envelope that would poison `compute_consensus`.
- [ ] Test: diff exceeds the token cap → CLI truncates with a marker and lowers confidence; does not send unbounded input.

### Error State Rendering
- [ ] Test (degrade path, default): cross-vendor judge skips → `/do-pr-review` consensus proceeds with Claude judges only; the aggregate review notes "cross-vendor skipped" so it is visible, not silent.
- [ ] Test (fail-closed path, `SDLC_REVIEW_CROSS_VENDOR_REQUIRED=1`): cross-vendor judge skips → consensus returns CHANGES REQUESTED with a blocker citing the missing cross-vendor verdict.

## Test Impact

- [ ] `tests/unit/test_review_multi_judge.py` — UPDATE: this is the REAL home of the `compute_consensus` any-blocker-wins coverage (verified at plan time; the prior plan named a nonexistent `tests/unit/test_sdlc_review_consensus.py`). It already contains `test_split_one_blocker_returns_changes_requested` (2-judge: 1 approve + 1 blocker → CHANGES REQUESTED) and `test_both_block_max_aggregates_blockers`. ADD a 3-judge dedup-survival case `test_cross_vendor_blocker_preserved_among_claude_approvals`: `[code-quality APPROVED/0, risk APPROVED/0, cross-vendor (judge_id=CROSS_VENDOR_JUDGE_ID) CHANGES REQUESTED/1]` → CHANGES REQUESTED with `blockers==1`. This is the HARD assertion proving the value premise (BLOCKER fix) AND the collision-safety/dedup-survival assertion: it confirms `_dedup_last_wins` keeps three distinct ids and that `blockers_max` preserves the lone cross-vendor blocker rather than overwriting/averaging it. ALSO add `test_cross_vendor_id_disjoint_from_claude_ids` asserting `CROSS_VENDOR_JUDGE_ID not in {"code-quality", "risk"}` so a future rename of the constant can never silently reintroduce a collision. Existing cases unchanged — `compute_consensus` is NOT modified, so this is additive coverage of an already-correct behavior.
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE (file exists, verified at plan time): add ONLY a positive round-trip case — a judge dict with `judge_id=CROSS_VENDOR_JUDGE_ID` round-trips into the `_judges` side-field via `record_verdict(judges=[...])` and survives `_validate_judges_payload`. **Do NOT** add the skip-gating ("a skip envelope is never passed to `record_verdict`") assertion here — that assertion is about the `/do-pr-review` parent's collection logic, not about `sdlc_verdict.py` (an UNCHANGED module). Asserting parent behavior from inside an unchanged module's test file misplaces the contract. The skip-gating assertion moves to the orchestration test below.
- [ ] `tests/unit/test_cross_vendor_judge.py` — CREATE (new module): the CLI's own coverage — envelope shape (`status="ok"` wraps a valid judge dict stamped with `CROSS_VENDOR_JUDGE_ID`; `status="skipped"` carries no judge), `BadRequestError`/`NotFoundError`/generic `OpenAIError` → skip, malformed/partial OpenAI response → skip, empty diff, token-cap truncation.
- [ ] `tests/unit/test_cross_vendor_orchestration.py` — CREATE (new module): the parent collection-logic coverage (the correct home for the skip-gating assertion, moved out of `test_sdlc_verdict.py`). Asserts: (a) given an `status="ok"` envelope, the parent appends `envelope["judge"]` to the consensus-input list; (b) given a `status="skipped"` envelope, the parent appends NOTHING and the skip never reaches `record_verdict(judges=)` / `compute_consensus` (so `_validate_judges_payload` never sees a malformed entry and the whole verdict record is never dropped). This isolates the parent's append-iff-ok contract in a module that owns it, leaving the unchanged `sdlc_verdict.py` test file scoped to verdict-recorder behavior only.

No other existing tests are affected — the consensus math (`agent/sdlc_review_consensus.py`) and verdict recorder (`tools/sdlc_verdict.py`) are unchanged.

## Rabbit Holes

- **Do NOT rewrite `compute_consensus` to be "vendor-aware" or add weighting knobs.** Recon + research both confirm `any-blocker-wins` with `blockers_max` already does the right thing (preserves the minority cross-vendor blocker). Adding weights would *reintroduce* the error-amplification the research warns against.
- **Do NOT build a generic multi-vendor abstraction layer** (pluggable Gemini/Llama/etc.). Scope is one non-Claude judge (GPT). A vendor-registry is a separate project if ever needed.
- **Do NOT make the cross-vendor judge post its own GitHub review via the API.** It returns a dict; the parent owns all posting (single-writer / single-review-comment invariant from #1309). Letting it post independently would break `do-merge`'s aggregate-comment regex.
- **Do NOT try to make the OpenAI verdict bit-for-bit deterministic.** `temperature=0` + `seed` is best-effort; the goal is a recorded, auditable verdict, not cryptographic reproducibility.

## Risks

### Risk 1: Error amplification from naive consensus
**Impact:** If the cross-vendor judge's verdict were averaged/diluted, a real minority-correct blocker could be filtered out — the exact failure mode the research flags.
**Mitigation:** Feed `any-blocker-wins` (already the Review rule). `blockers_max` preserves any single blocker. A test asserts the 2-approve-1-block → CHANGES REQUESTED invariant.

### Risk 2: Second vendor is down / rate-limited
**Impact:** Review gate could stall or hard-fail on every PR.
**Mitigation:** Default degrade-to-Claude-only (skip dict, log, proceed). Fail-closed is strictly opt-in via `SDLC_REVIEW_CROSS_VENDOR_REQUIRED=1`. The skip is surfaced in the aggregate comment so it is never silent.

### Risk 3: Cost blowout on large diffs
**Impact:** A 100K-line diff at $5/1M input tokens could be expensive, and only fires on high-stakes shapes.
**Mitigation:** `SDLC_REVIEW_CROSS_VENDOR_MAX_DIFF_TOKENS` cap with truncation + lowered confidence; trigger gated to `feature`-shape diffs only (trivial shapes excluded by the classifier); operator kill switch defaults off.

### Risk 4: Non-deterministic verdict undermines the SDLC verdict record
**Impact:** A flaky verdict makes the recorded `_judges` entry untrustworthy.
**Mitigation:** `temperature=0` + fixed `seed`; record model id + params in the judge dict so the verdict is auditable even if not perfectly reproducible.

## Race Conditions

No race conditions identified. The cross-vendor judge is a synchronous CLI invocation that emits to stdout; the `/do-pr-review` parent collects all judge dicts before calling `compute_consensus` (which is a pure function with no I/O), and makes a single `record_verdict` write (single-writer invariant already enforced by #1309). The judge runs in the same sequential collection loop as the Claude judges — no shared mutable state, no concurrent writes.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Generic multi-vendor reviewer registry (Gemini/Llama/Mistral) — scope is one GPT judge; a registry is a distinct future project, not yet filed (do not implement here).
- Cross-vendor review at the CRITIQUE (plan) stage — this issue is REVIEW-only; CRITIQUE uses a separate aggregation pattern that explicitly must not gain `_judges` (per `tools/sdlc_verdict.py:226`).
- Nothing else deferred — trigger policy, failure behavior, cost cap, and determinism are all in scope for this plan.

## Update System

- **`config/settings.py`**: add the four `SDLC_REVIEW_CROSS_VENDOR*` fields (`SDLC_REVIEW_CROSS_VENDOR`, `_MODEL`, `_MAX_DIFF_TOKENS`, `_REQUIRED`). No new secret — `OPENAI_API_KEY` already exists and is synced.
- **`.env.example`**: add commented placeholders for the new env vars (with a comment line above each `KEY=`, required by the completeness check). These are behavior toggles, not secrets, defaulting off — the feature is inert until an operator enables it.
- **`pyproject.toml`**: add the `valor-cross-vendor-judge` (or similar) entry point under `[project.scripts]`.
- No `scripts/remote-update.sh` / update-skill changes required — no new system dependency, no new launchd job, no migration. The `openai` package is already installed everywhere.

## Agent Integration

- **CLI entry point required**: yes — `tools/cross_vendor_judge.py` gets a `[project.scripts]` entry so it is invokable via Bash from the `/do-pr-review` skill (the skill runs in the agent's Bash tool, mirroring how it already calls `python -m scripts.pr_shape_classify`).
- **No bridge import**: the bridge (`bridge/telegram_bridge.py`) does not need to import this code. The cross-vendor judge is only reached through the `/do-pr-review` skill at REVIEW.
- **No MCP server**: this is not a conversational tool; it is an internal SDLC-stage component invoked by a skill, not by free-form agent chat.
- **Integration test**: a test that invokes the CLI against a small fixture diff (mock or real OpenAI call gated on `OPENAI_API_KEY`) and asserts it emits a valid judge dict that `compute_consensus` accepts without raising.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/multi-judge-consensus.md` with a "Cross-vendor judge" subsection: the trigger gate, env vars, degrade/fail-closed behavior, and the cross-vendor-blocker-preserved invariant.
- [ ] Add/confirm entry in `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on `tools/cross_vendor_judge.py` describing the judge-dict contract it emits and that it owns all vendor knowledge.
- [ ] Grain-of-salt comments on each provisional env-overridable constant (model, token cap).

## Success Criteria

- [ ] `tools/cross_vendor_judge.py` emits a valid judge dict (`judge_id="cross-vendor"`) that `compute_consensus` accepts.
- [ ] With `SDLC_REVIEW_CROSS_VENDOR=1` on a high-stakes (`feature`-shape) PR, the cross-vendor judge runs, posts a `## Review (Judge cross-vendor):` comment, and its dict appears in the recorded `_judges` side-field.
- [ ] A cross-vendor blocker forces CHANGES REQUESTED even when both Claude judges approve (test-proven under `any-blocker-wins`).
- [ ] Default failure behavior degrades to Claude-only with a visible "cross-vendor skipped" note; `SDLC_REVIEW_CROSS_VENDOR_REQUIRED=1` flips to fail-closed (test-proven).
- [ ] Trivial shapes (`docs-only`, `lockfile-only`, `small-patch`, `mixed`) never invoke the cross-vendor judge.
- [ ] **Value-premise validation — HARD deterministic assertion (BLOCKER fix):** The thesis ("a cross-vendor blocker forces CHANGES REQUESTED even when every Claude judge approves") is proven by a **deterministic, fixture-based, mock-free-of-live-network** test that ALWAYS runs (no `OPENAI_API_KEY` gate). The test constructs the three judge dicts directly — two Claude judges (`code-quality` APPROVED/0, `risk` APPROVED/0) plus a cross-vendor judge dict (`judge_id=CROSS_VENDOR_JUDGE_ID`, verdict CHANGES REQUESTED, blockers 1) representing a seeded defect — and asserts `compute_consensus([...], rule="any-blocker-wins")` returns `verdict == "CHANGES REQUESTED"` with `blockers == 1`. This is a HARD assertion: it FAILS the build if the cross-vendor blocker is averaged away or dropped by dedup. It does NOT depend on the live model's behavior — it proves the *consensus contract* that gives the feature its reason to exist, deterministically. (This is the dedup-survival test referenced in Technical Approach and Test Impact.)
- [ ] **Value-premise validation — live smoke test (OPTIONAL, gated):** A separate, `OPENAI_API_KEY`-gated smoke test feeds a fixture diff containing a defect-class Claude judges systematically miss (e.g., a subtle cross-language API misuse or a locale/encoding edge case) to the real CLI and observes whether the live model returns a blocker. This test is **explicitly optional and skips when the key is absent**; when it runs, a live miss is recorded as a known-limitation note (NOT a build failure) — it is empirical signal about model quality, not the gate that proves the contract. The HARD deterministic test above is the load-bearing assertion; this live test is corroborating evidence only.
- [ ] **Observability (concern resolution):** Each invocation emits a tri-state `logger.info` (`ran` | `skipped` | `disabled`); on `ran` the line includes model id and raw `prompt_tokens`/`completion_tokens` (NO hardcoded USD cost — rates are unverified and drift); the same fields appear in the recorded `_judges` `meta`. Test asserts the log line and `meta` fields are present and that no fabricated `$` figure is logged.
- [ ] `agent/sdlc_review_consensus.py` and `tools/sdlc_verdict.py` are unchanged (grep confirms no diff to their logic).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (cross-vendor-judge)**
  - Name: judge-builder
  - Role: Implement `tools/cross_vendor_judge.py` (OpenAI client, rubric, dict emission, failure handling, token cap), `config/settings.py` fields, `pyproject.toml` entry, `.env.example` placeholders.
  - Agent Type: builder
  - Resume: true

- **Builder (skill-wiring)**
  - Name: skill-builder
  - Role: Wire the cross-vendor judge into `.claude/skills-global/do-pr-review/SKILL.md` (trigger gate, dispatch, per-judge comment) without altering the consensus/record contract.
  - Agent Type: builder
  - Resume: true

- **Test Engineer (judge-tests)**
  - Name: judge-tester
  - Role: Unit tests for the judge dict shape, failure/degrade/fail-closed paths, token cap, empty diff; consensus invariant test (2-approve-1-block); integration test for CLI invocation.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (docs)**
  - Name: judge-documentarian
  - Role: Update `docs/features/multi-judge-consensus.md` and index.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria, confirm consensus/recorder files unchanged.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build the cross-vendor judge CLI
- **Task ID**: build-judge
- **Depends On**: none
- **Validates**: tests/unit/test_cross_vendor_judge.py (create)
- **Informed By**: research (gpt-5.5 model id, temperature=0+seed determinism, any-blocker-wins preserves minority blocker)
- **Assigned To**: judge-builder
- **Agent Type**: builder
- **Parallel**: true
- Define `CROSS_VENDOR_JUDGE_ID = "cross-vendor"` as a single named constant (the one source of truth for the judge id; disjoint from `code-quality`/`risk`). Stamp it onto every emitted judge dict — never trust an id from the model.
- Implement `tools/cross_vendor_judge.py`: fetch PR diff, call OpenAI Chat Completions (`SDLC_REVIEW_CROSS_VENDOR_MODEL` default `gpt-5.5`, `temperature=0`, `seed`, `response_format=json_object`), parse the response, then **coerce + validate** the judge dict to the exact downstream types (`judge_id` from the constant, `verdict` → canonical `str`, `blockers` → `int` excluding `bool`) so it passes BOTH `compute_consensus._validate_judge` and `sdlc_verdict._validate_judges_payload`. Emit `{"status":"ok","judge":{...}}` on success / `{"status":"skipped","reason":...}` on any failure (incl. failed coercion). Never emit a bare or partial judge dict.
- Implement failure handling: wrap the call in `except openai.OpenAIError` (broad base class — covers BadRequest/NotFound/RateLimit/Timeout/Auth/Connection) plus a trailing `except Exception` for non-OpenAI failures → `{"status":"skipped","reason":...,"meta":...}` envelope + `logger.warning`; never `except: pass`.
- Implement tri-state `logger.info` (`ran`/`skipped`/`disabled`); on `ran` log `model` + raw `prompt_tokens`/`completion_tokens` from the API `usage` (NO hardcoded USD rate table); mirror into the judge dict's `meta`.
- Implement token cap with truncation + lowered confidence; empty-diff handling.
- Add the four `SDLC_REVIEW_CROSS_VENDOR*` fields to `config/settings.py` with grain-of-salt comments; add `.env.example` placeholders; add `[project.scripts]` entry in `pyproject.toml`.

### 2. Wire the judge into /do-pr-review
- **Task ID**: build-skill
- **Depends On**: build-judge
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- In the multi-judge orchestration block of `.claude/skills-global/do-pr-review/SKILL.md`: add the trigger gate (`SDLC_REVIEW_CROSS_VENDOR=1` AND `shape=="feature"`; skip trivial shapes), invoke the CLI, parse the envelope, append `envelope["judge"]` to the consensus-input list ONLY when `envelope["status"]=="ok"` (never append a skip envelope) before `compute_consensus`, post the `## Review (Judge cross-vendor):` comment, surface skips in the aggregate.
- Do NOT modify `compute_consensus` call signature or `record_verdict` usage.

### 3. Tests
- **Task ID**: build-tests
- **Depends On**: build-judge
- **Assigned To**: judge-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit (`tests/unit/test_cross_vendor_judge.py`, new): envelope shape (`status="ok"` stamped with `CROSS_VENDOR_JUDGE_ID` vs `status="skipped"`); `OpenAIError` subclasses (`BadRequestError`/`NotFoundError`/`RateLimitError`) AND a generic parse failure → skip envelope + `logger.warning`; type-coercion (`blockers` as `"1"`/`1.0`/`bool` → coerced or skipped); token-cap truncation; empty diff; tri-state `logger.info` with token counts and NO `$` figure.
- Consensus invariant + dedup-survival (`tests/unit/test_review_multi_judge.py`, UPDATE): add `test_cross_vendor_blocker_preserved_among_claude_approvals` (the HARD deterministic value-premise assertion) — 2 Claude approve + 1 cross-vendor blocker (`judge_id=CROSS_VENDOR_JUDGE_ID`) → CHANGES REQUESTED, `blockers==1`, always-on (no API-key gate). Add `test_cross_vendor_id_disjoint_from_claude_ids`.
- Verdict round-trip (`tests/unit/test_sdlc_verdict.py`, UPDATE): ONLY the positive case — `judge_id=CROSS_VENDOR_JUDGE_ID` dict round-trips into `_judges` and survives `_validate_judges_payload`. (Skip-gating assertion does NOT live here — moved to the orchestration test below.)
- Orchestration skip-gating (`tests/unit/test_cross_vendor_orchestration.py`, CREATE): the parent appends `envelope["judge"]` iff `status=="ok"`; a `status="skipped"` envelope is appended NOWHERE and never reaches `record_verdict(judges=)` / `compute_consensus`.
- Live smoke (OPTIONAL, gated on `OPENAI_API_KEY`, skips when absent): invoke the CLI on a fixture diff with a seeded defect Claude misses; observe whether the live model blocks. A miss is recorded as a known-limitation note, NOT a build failure — this is corroborating evidence, not the load-bearing gate.
- Integration: invoke the CLI on a fixture diff (real OpenAI gated on `OPENAI_API_KEY`); assert the `status="ok"` envelope's `judge` dict is accepted by `compute_consensus` without raising.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-skill, build-tests
- **Assigned To**: judge-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/multi-judge-consensus.md` cross-vendor subsection and the README index.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-skill, build-tests, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm `agent/sdlc_review_consensus.py` and `tools/sdlc_verdict.py` logic unchanged; verify all success criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_cross_vendor_judge.py tests/unit/test_cross_vendor_orchestration.py tests/unit/test_review_multi_judge.py tests/unit/test_sdlc_verdict.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Consensus unchanged | `git diff --exit-code main -- agent/sdlc_review_consensus.py` | exit code 0 |
| CLI entry point wired | `grep -q cross-vendor-judge pyproject.toml` | exit code 0 |
| Env field present | `python -c "from config.settings import settings"` | exit code 0 |

## Critique Results

Critique verdict: **NEEDS REVISION**. Revised 2026-06-22 to clear 3 blockers + 3 concerns.

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | Skip-dict shape `{"judge_id":"cross-vendor","skipped":true}` is runtime-fatal — missing `verdict`/`blockers` (both in `_REQUIRED_KEYS`); reaching `compute_consensus` raises `ValueError`, reaching `record_verdict` fails `_validate_judges_payload` and silently drops the whole record. | Solution → Output envelope contract; Data Flow steps 3-4 | CLI now emits a status-discriminated envelope `{"status":"ok","judge":{...}}` / `{"status":"skipped","reason":...}`. Parent appends `envelope["judge"]` to consensus inputs ONLY when `status=="ok"`. A skip envelope has no path to `compute_consensus` or `record_verdict(judges=)`. |
| BLOCKER | `gpt-5.5` + `chat.completions`/`seed`/`response_format` unverified (marketing URL); only in-repo OpenAI use is `images.generate`. Wrong model id / unsupported param raises on every call. | Solution → Model-id resilience; Technical Approach OpenAI bullet; `_MODEL` env var note | CLI catches `openai.BadRequestError` + `openai.NotFoundError` and routes both to the skip path; model id is env-overridable via `SDLC_REVIEW_CROSS_VENDOR_MODEL`. A bad default degrades, never hard-fails. |
| BLOCKER | Test Impact named nonexistent `tests/unit/test_sdlc_review_consensus.py`. Real coverage is `tests/unit/test_review_multi_judge.py` (already has the any-blocker invariant). | Test Impact section; Step 3; Verification table | Retargeted to `tests/unit/test_review_multi_judge.py` (UPDATE: add 3-judge case), `tests/unit/test_sdlc_verdict.py` (UPDATE, file confirmed present), `tests/unit/test_cross_vendor_judge.py` (CREATE). |
| CONCERN | `LINE_THRESHOLD` env var unowned/redundant (classifier emits `shape` only, not a threshold tier). | Trigger gate (removed); env-var list note | Removed the var entirely. Classifier's `to_dict()` exposes only `shape`; a threshold would force a redundant `gh pr view --json additions,deletions`. Gate is `feature`-shape + kill switch. |
| CONCERN | No success criterion validates the value premise (judge catching a defect Claude misses). | Success Criteria → Value-premise validation | Added a fixture-seeded-defect criterion: cross-vendor blocks where Claude approves → CHANGES REQUESTED; live-call gated, records a miss as a known-limitation note. |
| CONCERN | No tri-state observability or cost logging. | Solution → Tri-state observability; Success Criteria → Observability | Each invocation emits a tri-state `logger.info` (`ran`/`skipped`/`disabled`); `ran` includes model + raw token counts; same fields stored in `_judges` `meta`. |

### Second critique pass (2026-06-22): NEEDS REVISION — 2 new blockers + 4 concerns

The first round's 3 blockers held up; the second pass surfaced these. The first round's USD-cost-estimate concern resolution is itself reversed below (concern 4) — raw token counts only.

| Severity | Finding | Addressed By | Implementation Note |
|----------|---------|--------------|---------------------|
| BLOCKER | `judge_id` collision risk: `compute_consensus._dedup_last_wins` keys on `judge_id` (`agent/sdlc_review_consensus.py:38-44`); a colliding cross-vendor id could overwrite a Claude judge dict and silently drop its blocker, defeating any-blocker-wins. | Solution → Reserved `judge_id` constant; Technical Approach `judge_id` bullet; Test Impact | Pinned `CROSS_VENDOR_JUDGE_ID = "cross-vendor"` as the single source of truth in `tools/cross_vendor_judge.py`, value disjoint from `code-quality`/`risk`. Added a dedup-survival test (`test_cross_vendor_blocker_preserved_among_claude_approvals`) asserting the cross-vendor blocker survives aggregation alongside both Claude judges, plus `test_cross_vendor_id_disjoint_from_claude_ids`. |
| BLOCKER | Value-premise could ship unvalidated: the success criterion allowed a live model miss to be recorded as a known-limitation note instead of failing — turning the thesis into a non-assertion. | Success Criteria → Value-premise (HARD + live); Test Impact; Step 3 | Split into (a) a deterministic, always-on, fixture-based HARD assertion (cross-vendor blocker among Claude approvals → consensus CHANGES REQUESTED, build fails if not) and (b) an OPTIONAL `OPENAI_API_KEY`-gated live smoke test that records a miss as corroborating signal only, never a gate. |
| CONCERN | OpenAI catch too narrow (only `BadRequestError`/`NotFoundError`). | Solution → Model-id resilience; Failure Path; Technical Approach OpenAI bullet; Step 1 | Broadened to the base class `openai.OpenAIError` (covers RateLimit/Timeout/Auth/Connection too) + trailing `except Exception` for non-OpenAI failures; all route to the skip envelope. |
| CONCERN | Judge-dict types not coerced; could pass one downstream validator and fail the other. | Solution → Type-coercion note; Step 1 | CLI coerces to the exact types BOTH validators require (`judge_id` from constant, `verdict` `str`, `blockers` `int` excluding `bool`) before emitting `status="ok"`; failed coercion degrades to skip. |
| CONCERN | Skip-gating assertion placed in `test_sdlc_verdict.py` (an UNCHANGED module). | Test Impact; Step 3 | Moved the skip-gating assertion to a new `tests/unit/test_cross_vendor_orchestration.py` (owns the parent's append-iff-ok contract); `test_sdlc_verdict.py` keeps only the positive round-trip case. |
| CONCERN | Speculative hardcoded USD cost rate table (unverified, drifts on repricing). | Solution → Tri-state observability; Success Criteria → Observability | Dropped the rate table entirely. CLI logs raw `prompt_tokens`/`completion_tokens` from the API `usage` only; cost computed downstream where billing rates are owned, never guessed in-process. |

---

## Open Questions

1. **Trigger policy.** Default is `SDLC_REVIEW_CROSS_VENDOR=0` (opt-in) AND high-stakes (`feature`-shape). Is opt-in-off the right default, or should it auto-fire on every `feature`-shape PR once enabled per machine? (Cost vs. coverage call.)
2. **Cost ceiling.** What's the acceptable per-PR spend? This sets `SDLC_REVIEW_CROSS_VENDOR_MAX_DIFF_TOKENS` and whether to default to `gpt-5.5` ($5/$30) vs. a cheaper tier for routine high-stakes diffs.
3. **Failure default.** Plan defaults to degrade-to-Claude-only (additive safety net). Confirm that's preferred over fail-closed as the default posture.
