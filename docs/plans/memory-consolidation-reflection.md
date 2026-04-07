---
status: Planning
type: feature
appetite: Medium
owner: valorengels
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/795
last_comment_id:
---

# Memory Consolidation Reflection: LLM-Based Semantic Dedup

## Problem

**Current behavior:** The Memory store accumulates records from four write paths (human Telegram messages at 6.0, post-session Haiku extraction at 1.0–4.0, intentional saves at 7.0–8.0, post-merge learning at 7.0) with no process to merge semantically near-duplicate entries or reconcile contradictions. Hash-based dedup (proposed in #748) only catches byte-identical records — it misses the realistic failure mode: the same correction phrased differently across sessions.

Concrete symptoms that will accumulate: "don't mock the DB" / "use real integration tests" / "mocks burned us last quarter" are three memories that should be one. Contradicted entries where a newer human correction supersedes an older one both remain active. Low-signal observations that cluster into a pattern were saved individually.

**Desired outcome:** A nightly `memory-dedup` reflection that runs an LLM-based semantic consolidation pass over active memories, merges duplicates with explicit safety rails, and flags contradictions for human review. Implemented as a declarative reflection registered in `config/reflections.yaml` (and `~/Desktop/Valor/reflections.yaml` once that path exists per #748), plugging into the unified reflection framework from #748.

## Prior Art

- **Issue #748** — Reflections unification (OPEN). Defines the `memory-dedup` / `memory-decay-prune` / `memory-quality-audit` / `knowledge-reindex` reflection slots. This plan is the detailed design for `memory-dedup` specifically. **Hard dependency: #748 must land before this reflection can be registered in the unified YAML scheduler.**
- **Issue #620** — Claude Code feature integration roadmap. Lists "cron memory consolidation" as a Phase 1 item with brief spec. This plan fleshes out that item.
- **Issue #613** (closed) — Memory trigger training. Covers outcome tracking and routine compression; adjacent but distinct.
- **Issue #323** (closed) — MuninnDB cognitive memory layer. Foundational design of the Memory model.
- **No merged PRs** found for memory consolidation or semantic dedup — this is greenfield.

## Dependency Sequencing

**#748 is a hard dependency.** That issue delivers:
1. The unified `Reflection` model and YAML scheduler replacing the monolith
2. The `~/Desktop/Valor/reflections.yaml` deployment-specific config path
3. The declarative reflection registration pattern this plan depends on

**What can be done in parallel before #748 lands:**
- Add the `superseded_by` field to the `Memory` model (additive, no behavioral change)
- Add the recall-filter change in `agent/memory_retrieval.py` (one-line guard, safe to ship independently)
- Write `tests/unit/test_memory_consolidation.py` with canary set, idempotency, and superseded-recall tests
- Write the consolidation callable (`scripts/memory_consolidation.py`) in isolation

**What must wait for #748:**
- Registering the reflection in `config/reflections.yaml` / `~/Desktop/Valor/reflections.yaml`
- Wiring the callable into the unified scheduler

This plan sequences the work so that parallel tasks ship incrementally, and the reflection registration is the final integration step after #748 merges.

## Data Flow

1. **Trigger**: Nightly, the reflection scheduler calls `scripts.memory_consolidation.run_consolidation()`
2. **Load**: Query all active Memory records for the project (filter: `superseded_by` is null, `relevance` score > decay floor)
3. **Group**: Partition records by `metadata.category` (correction, decision, pattern, surprise) and `metadata.tags` overlap. Groups exceeding 50 records are split into sub-batches of 50.
4. **LLM pass (Haiku)**: For each group, send structured prompt (see Solution section) with serialized memory content. Haiku returns a JSON consolidation plan.
5. **Parse and validate**: Parse JSON response; validate each action (merge/flag_contradiction). Reject malformed output — log and skip.
6. **Dry-run gate**: If dry-run mode (default for first 14 days), log proposed actions to `logs/reflections.log` — no writes to Redis.
7. **Apply gate**: If `--apply` flag is set AND dry-run period has elapsed AND run count ≤ 10 merges/run:
   - Write merged record as NEW Memory via `Memory.safe_save()`
   - Set `superseded_by=<new_id>` on all originals via `m.superseded_by = new_id; m.save()`
   - Log each action to `logs/reflections.log`
8. **Contradiction path**: Flag-only. Write a Telegram notification via `valor-telegram send` with the contradiction summary. No auto-resolve.
9. **Return**: Summary dict `{proposed_merges, applied_merges, flagged_contradictions, skipped_exempt}` for scheduler run history.

## Architectural Impact

- **New field on Memory model**: `superseded_by = StringField(default="")` — additive, no migration needed for old records (empty string = not superseded).
- **Recall filter change**: `agent/memory_retrieval.py` `retrieve_memories()` adds a one-line filter: skip records where `m.superseded_by != ""`. Load-bearing but isolated.
- **New script**: `scripts/memory_consolidation.py` — standalone callable, no bridge imports.
- **New reflection entry**: `config/reflections.yaml` gets a `memory-dedup` entry. Deployment copy at `~/Desktop/Valor/reflections.yaml` added via update script.
- **Coupling**: Low. The consolidation script reads/writes the Memory model only. No bridge, agent, or scheduler code changes beyond registration.
- **Reversibility**: High. Remove the reflection from YAML, set all `superseded_by` back to `""` (or ignore the field). Original records are never deleted.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1–2 (scope alignment on canary set definition, dry-run period length)
- Review rounds: 1 (code review of Haiku prompt + consolidation logic)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Memory model storage |
| Anthropic API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('/Users/valorengels/src/ai/.env').get('ANTHROPIC_API_KEY')"` | Haiku LLM calls |
| #748 merged (for registration step only) | `gh issue view 748 --json state -q .state` → `CLOSED` | Unified scheduler |

## Solution

### Key Elements

- **`superseded_by` field**: Additive StringField on Memory model. Empty string = active. Non-empty = archived; value is the `memory_id` of the merged replacement.
- **Recall filter**: `retrieve_memories()` skips records where `superseded_by != ""`. One-line guard. Archived memories remain in Redis for audit.
- **Consolidation callable**: `scripts/memory_consolidation.py::run_consolidation()`. Groups memories, calls Haiku, applies or dry-runs the plan.
- **Haiku prompt**: Structured JSON output (see below). Processes one group (≤50 records) per call.
- **Safety rails**: Importance ≥7.0 exempt from merging. Max 10 merges per run. Contradictions flag-only. Dry-run default for 14 days.
- **Canary set**: 10 hand-curated memories that must never merge (tested automatically).

### Haiku Prompt Design

The prompt is sent per memory group. Each record is serialized as `{id, content, importance, category, tags}`.

```
You are a memory consolidation assistant. Your job is to identify near-duplicate memories and contradictions in the set below. You must NOT merge memories with different factual claims or that cover different topics, even if they use similar language.

Rules:
1. Only propose merging if the memories express the same instruction/observation with negligible semantic difference.
2. A "contradiction" is two memories that give directly opposing guidance on the same topic.
3. Never merge memories with importance >= 7.0 (these are exempt).
4. Return ONLY valid JSON. No prose outside the JSON object.
5. If no duplicates or contradictions are found, return {"actions": []}.

Memories:
{memories_json}

Return a JSON object with this exact schema:
{
  "actions": [
    {
      "action": "merge",
      "ids": ["<id1>", "<id2>"],
      "merged_content": "<combined content, max 300 chars>",
      "merged_importance": <highest importance of the input records, float>,
      "merged_category": "<category from input records, prefer 'correction' if mixed>",
      "merged_tags": [<union of input tags, max 5>],
      "rationale": "<one sentence explaining why these are duplicates>"
    },
    {
      "action": "flag_contradiction",
      "ids": ["<id1>", "<id2>"],
      "rationale": "<one sentence explaining the contradiction>"
    }
  ]
}

Do not include any memory with importance >= 7.0 in any action.
```

**Validation rules applied after parsing:**
- Reject any `merge` action where any input `memory_id` has `importance >= 7.0`
- Reject any `merge` action where `len(ids) < 2`
- Reject malformed JSON entirely (log and skip the group)
- Cap applied merges at `MAX_MERGES_PER_RUN = 10` across the entire run

### `superseded_by` Field Schema

```python
# In models/memory.py, added to the Memory class:
superseded_by = StringField(default="")
# Empty string = active record
# Non-empty = memory_id of the merged replacement record
# Populated only by the consolidation reflection; never set by ingestion paths
```

**Why StringField not Optional[str]**: Popoto's `StringField(default="")` handles None/empty consistently in Redis serialization. The empty string serves as the null sentinel. Querying active records: filter where `superseded_by == ""`.

**Recall filter change** in `agent/memory_retrieval.py`:
```python
# In retrieve_memories(), after loading records from BM25/RRF:
records = [r for r in records if not r.superseded_by]
```

This is the only recall-pipeline change. The ExistenceFilter bloom is NOT modified — superseded records remain in the bloom filter (false positives are harmless; the full retrieval step filters them out).

### Canary Set Definition

The canary set is a hardcoded list of 10 memory content strings in `tests/unit/test_memory_consolidation.py` representing distinct human corrections on different topics that should never be merged with each other:

1. "Always commit plan documents on main, never on feature branches"
2. "Never include co-author trailers in commit messages"
3. "Use real integration tests — never mock the database"
4. "All bulk Redis operations must be project-scoped; tests must never touch production data"
5. "Memory system must fail silently — never crash the bridge or agent"
6. "The PM session orchestrates; Dev session executes — never reverse this"
7. "Plans must include ## Documentation, ## Update System, ## Agent Integration, ## Test Impact sections"
8. "Popoto records must use instance.delete() or Model.rebuild_indexes() — never raw Redis DEL"
9. "Telegram output routing uses the nudge loop — bridge has no SDLC awareness"
10. "SupersededBy records must be excluded from recall but retained in Redis for audit"

The canary test creates Memory records for each pair of adjacent canary items and asserts that `run_consolidation(dry_run=True)` proposes zero merges across any canary pair.

### Rollout Sequence

```
Phase 0 (parallel, pre-#748):
  1. Add superseded_by field to Memory model
  2. Add recall filter to agent/memory_retrieval.py
  3. Write scripts/memory_consolidation.py (callable)
  4. Write tests/unit/test_memory_consolidation.py (canary + idempotency + superseded-recall)
  5. Capture baseline metrics (memory count, duplication depth for known corrections)

Phase 1 (after #748 merges):
  6. Register memory-dedup in config/reflections.yaml (dry_run=true, interval=86400, priority=normal)
  7. Deploy: update script propagates new reflections.yaml to ~/Desktop/Valor/reflections.yaml

Phase 2 (14-day dry-run period):
  8. Review logs/reflections.log daily for proposed merges
  9. Verify canary set never proposed for merge
  10. Achieve ≥95% human agreement on proposed merges before enabling apply

Phase 3 (apply mode):
  11. Set apply_enabled=true in reflections.yaml (or pass --apply flag)
  12. Monitor: post-consolidation metrics (record count reduction, recall precision)
  13. 30-day audit: verify all importance ≥7.0 records still exist (possibly superseded but retrievable)
```

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_consolidation()` wraps the Haiku API call in try/except; on failure logs WARNING and returns early with `{applied_merges: 0, error: str(e)}`
- [ ] `Memory.safe_save()` already wraps all Redis writes — consolidation creates merged records through this path, inheriting its error handling
- [ ] JSON parse failure from Haiku: log WARNING with raw response snippet, skip the group, continue with remaining groups
- [ ] Each `except Exception` path must have a corresponding test asserting `logs/reflections.log` gets a WARNING entry

### Empty/Invalid Input Handling
- [ ] Empty memory group (0 records): return `{"actions": []}` immediately, no Haiku call
- [ ] Single-record group (1 record): return `{"actions": []}` immediately (can't merge with itself)
- [ ] Haiku returns empty string or whitespace: treated as JSON parse failure (log and skip)
- [ ] Memory record with `importance=None`: treat as 0.0, apply exemption threshold check conservatively (exempt if None)

### Error State Rendering
- [ ] Dry-run log entries are human-readable: `[DRY-RUN] Would merge {ids}: {rationale}`
- [ ] Contradiction flags produce a Telegram message via `valor-telegram send`; test that the send call is invoked with correct content

## Test Impact

- [ ] `tests/unit/test_memory_model.py` — UPDATE: add assertions that `superseded_by` field defaults to `""` and accepts a memory_id string
- [ ] `tests/unit/test_memory_retrieval.py` — UPDATE: add test that `retrieve_memories()` excludes records where `superseded_by != ""`
- [ ] `tests/unit/test_memory_consolidation.py` — CREATE (new file): canary set test, idempotency test, superseded-recall test, exemption test (importance ≥7.0 never merged), rate-limit test (max 10 merges enforced)

No other existing memory tests are affected — consolidation is additive and the recall filter is the only behavioral change to existing code paths.

## Rabbit Holes

- **Vector embeddings for grouping**: The Memory model intentionally uses bigram overlap + BM25, not vectors. Don't add a vector store to improve grouping quality — Haiku semantic judgment is sufficient and keeps the architecture consistent.
- **Real-time consolidation**: Nightly is fine for our write volume. 30-minute cadence (Google's demo) adds complexity with no benefit.
- **Auto-resolving contradictions**: The temptation to have Haiku pick a winner is strong but wrong. Contradictions require human judgment; auto-resolve is the "drift and loops" failure mode we're guarding against.
- **Migrating existing records**: Don't backfill `superseded_by` on existing records or run a bulk migration. The field defaults to `""` which is correct for all existing records.
- **Changing the bloom filter**: Superseded records remaining in the bloom causes harmless false positives (the retrieval step filters them). Don't modify the bloom filter logic — it's a complex data structure and the benefit is negligible.
- **Cross-project consolidation**: Memory records are partitioned by `project_key`. Consolidation runs per-project. Don't merge memories across projects.

## Risks

### Risk 1: Haiku prompt regression producing over-merging
**Impact:** Distinct corrections collapsed into a generic platitude — precision loss, hard to detect until human notices degraded recall
**Mitigation:** Canary set test (automated), 14-day dry-run with daily human review of proposed merges, 95% agreement threshold before enabling apply, max 10 merges/run rate cap

### Risk 2: #748 slips, blocking reflection registration
**Impact:** Phase 0 work (field, filter, tests) ships but reflection never activates
**Mitigation:** Phase 0 is independently useful (superseded_by field + recall filter are correct regardless). The consolidation callable can be run manually via `python -m scripts.memory_consolidation --dry-run` while waiting for #748.

### Risk 3: Superseded records accumulate in Redis without pruning
**Impact:** Redis memory grows; superseded records are invisible to recall but still occupy space
**Mitigation:** Out of scope for this plan — the `memory-decay-prune` reflection slot from #748 handles pruning. Superseded records are low-importance by definition (they've been replaced) and will decay naturally. Document in `docs/features/subconscious-memory.md` that `memory-decay-prune` should eventually prune superseded records.

### Risk 4: Recall filter breaks if Popoto field loading returns None instead of ""
**Impact:** `not r.superseded_by` evaluates True for None (correct) but a migration edge case could produce unexpected values
**Mitigation:** Filter uses `not r.superseded_by` (truthy check) which handles both `""` and `None` correctly. Test with explicit `None` and `""` cases in `test_memory_retrieval.py`.

## Race Conditions

### Race 1: Consolidation run and concurrent Memory.safe_save() on the same record
**Location:** `scripts/memory_consolidation.py` apply phase, `models/memory.py`
**Trigger:** Consolidation reads a batch of records, a new save happens mid-run on a record in that batch
**Data prerequisite:** Consolidation must have a stable snapshot of records for the Haiku pass
**State prerequisite:** The `superseded_by` write must not overwrite a record that was just saved as new merged content
**Mitigation:** Consolidation runs nightly when write activity is low. The `superseded_by` field is only written by the consolidation script (never by ingestion paths). A concurrent `safe_save()` on an original record produces a new record with empty `superseded_by` — this is correct behavior (new saves post-consolidation are fresh records). No locking needed.

## No-Gos (Out of Scope)

- Multi-tenant memory isolation (single-user system)
- Vector embeddings or semantic search (architecture is intentionally bigram + BM25)
- Real-time or sub-hourly consolidation cadence
- Replacing the bloom filter recall mechanism
- Auto-resolving contradictions (flag-only, human decision required)
- The `memory-decay-prune`, `memory-quality-audit`, and `knowledge-reindex` reflections (separate slots in #748)
- Pruning superseded records (delegated to `memory-decay-prune` from #748)
- Cross-project consolidation

## Update System

The `config/reflections.yaml` in-repo file will gain a new `memory-dedup` entry. The update script (`scripts/remote-update.sh`) must propagate:
- The new `memory-dedup` entry to `~/Desktop/Valor/reflections.yaml` on all machines where #748's unified scheduler is running

Specifically, the update skill should merge the new reflection entry into the deployment-specific YAML. If `~/Desktop/Valor/reflections.yaml` doesn't exist yet (pre-#748), the update script skips this step gracefully.

No new Python dependencies are added. No new environment variables required. The Haiku API call uses the existing `ANTHROPIC_API_KEY` already in `.env`.

## Agent Integration

No new MCP server or `.mcp.json` changes required. The consolidation reflection runs as a scheduled Python callable, not an agent-invoked tool.

The agent can inspect consolidation results via the existing `memory_search` CLI:
```bash
python -m tools.memory_search search "query" --category correction
```

Superseded records are filtered out of recall automatically — no agent-facing API changes.

The contradiction notification reaches the agent via Telegram (the consolidation script sends a message using `valor-telegram send`). No new integration work needed.

No agent integration required beyond the existing memory recall pipeline — this is a scheduled maintenance callable.

## Documentation

- [ ] Update `docs/features/subconscious-memory.md`: add a **Memory Consolidation** section describing the `memory-dedup` reflection, `superseded_by` field, recall filter, and rollout phases
- [ ] Update `docs/features/subconscious-memory.md` Key Files table: add `scripts/memory_consolidation.py`
- [ ] Update `docs/features/subconscious-memory.md` Reversibility section: note that superseded records can be re-activated by clearing `superseded_by`
- [ ] Add entry to `docs/features/README.md` index table if a standalone consolidation doc is warranted (likely not — the subconscious-memory.md update is sufficient)
- [ ] Add inline docstring to `scripts/memory_consolidation.py` covering algorithm, safety rails, and dry-run/apply modes
- [ ] Update `config/reflections.yaml` inline comment to document the `memory-dedup` entry's purpose and the dry-run period requirement

## Success Criteria

- [ ] `Memory` model has `superseded_by = StringField(default="")` field
- [ ] `agent/memory_retrieval.py` `retrieve_memories()` filters out records where `superseded_by != ""`
- [ ] `scripts/memory_consolidation.py` callable exists with Haiku prompt, dry-run mode, apply mode, max-10-merges rate cap, importance ≥7.0 exemption, and contradiction flagging
- [ ] `memory-dedup` reflection registered in `config/reflections.yaml` with `interval: 86400`, `priority: normal`, `execution_type: function`, `enabled: true` (after #748 lands)
- [ ] `tests/unit/test_memory_consolidation.py` has canary set test, idempotency test, superseded-recall test, exemption test, and rate-limit test
- [ ] Dry-run mode is the default; apply is gated behind a config flag or `--apply` argument
- [ ] Contradictions produce a Telegram notification, never auto-resolve
- [ ] Baseline metrics captured before first apply run
- [ ] 14-day dry-run period planned with human review checkpoint documented in `logs/reflections.log`
- [ ] All tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (memory-model)**
  - Name: memory-model-builder
  - Role: Add `superseded_by` field to Memory model and recall filter to `agent/memory_retrieval.py`
  - Agent Type: builder
  - Resume: true

- **Builder (consolidation-script)**
  - Name: consolidation-builder
  - Role: Implement `scripts/memory_consolidation.py` with Haiku prompt, dry-run/apply modes, safety rails
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write `tests/unit/test_memory_consolidation.py` with all required test cases
  - Agent Type: test-engineer
  - Resume: true

- **Builder (reflection-registration)**
  - Name: reflection-builder
  - Role: Register `memory-dedup` in `config/reflections.yaml` (depends on #748)
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: consolidation-validator
  - Role: Run full test suite, verify recall filter, verify dry-run logs, verify canary set passes
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: memory-documentarian
  - Role: Update `docs/features/subconscious-memory.md` with consolidation section
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add `superseded_by` field to Memory model
- **Task ID**: build-memory-model
- **Depends On**: none
- **Validates**: `tests/unit/test_memory_model.py` (UPDATE)
- **Assigned To**: memory-model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `superseded_by = StringField(default="")` to `models/memory.py` Memory class
- Add docstring comment: "Empty string = active. Non-empty = memory_id of merged replacement."
- Update `tests/unit/test_memory_model.py`: assert field defaults to `""`, accepts a string ID

### 2. Add recall filter for superseded records
- **Task ID**: build-recall-filter
- **Depends On**: build-memory-model
- **Validates**: `tests/unit/test_memory_retrieval.py` (UPDATE)
- **Assigned To**: memory-model-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/memory_retrieval.py` `retrieve_memories()`, add: `records = [r for r in records if not r.superseded_by]` after loading records
- Update `tests/unit/test_memory_retrieval.py`: test that superseded records (non-empty `superseded_by`) are excluded from results

### 3. Implement consolidation callable
- **Task ID**: build-consolidation
- **Depends On**: build-memory-model
- **Validates**: `tests/unit/test_memory_consolidation.py` (CREATE)
- **Assigned To**: consolidation-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/memory_consolidation.py` with `run_consolidation(project_key, dry_run=True, max_merges=10)` entry point
- Implement grouping by `metadata.category` and `metadata.tags`, batching at 50 records
- Implement Haiku API call with the prompt specified in the Solution section
- Implement JSON response validation (reject malformed, reject merges with importance ≥7.0 IDs)
- Implement dry-run path: log to `logs/reflections.log`, no Redis writes
- Implement apply path: `Memory.safe_save()` for merged record, `m.superseded_by = new_id; m.save()` for originals
- Implement contradiction flagging: `valor-telegram send` with contradiction summary
- Return summary dict `{proposed_merges, applied_merges, flagged_contradictions, skipped_exempt}`

### 4. Write consolidation tests
- **Task ID**: build-tests
- **Depends On**: build-consolidation
- **Validates**: `tests/unit/test_memory_consolidation.py` (CREATE)
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_memory_consolidation.py`
- Canary set test: create Memory records for each of the 10 canary pairs; assert `run_consolidation(dry_run=True)` proposes zero merges across any canary pair
- Idempotency test: run consolidation twice in sequence; assert second run proposes no additional merges
- Superseded-recall test: create two duplicate memories, run with `--apply`; assert originals excluded from `retrieve_memories()` and merged record included
- Exemption test: create two near-duplicate memories with importance=8.0; assert neither is proposed for merge
- Rate-limit test: inject 15 merge proposals via mock; assert only 10 applied

### 5. Register reflection (post-#748 gate)
- **Task ID**: build-reflection-registration
- **Depends On**: build-consolidation, build-tests (requires #748 to be merged)
- **Validates**: `config/reflections.yaml` has `memory-dedup` entry
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `memory-dedup` entry to `config/reflections.yaml`:
  ```yaml
  - name: memory-dedup
    description: "LLM-based semantic consolidation: merge near-duplicate memories, flag contradictions"
    interval: 86400  # daily
    priority: normal
    execution_type: function
    callable: "scripts.memory_consolidation.run_consolidation"
    enabled: true
  ```
- Add same entry to `~/Desktop/Valor/reflections.yaml` (update script propagation)

### 6. Validate all
- **Task ID**: validate-all
- **Depends On**: build-recall-filter, build-tests, build-reflection-registration
- **Assigned To**: consolidation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_memory_consolidation.py tests/unit/test_memory_model.py tests/unit/test_memory_retrieval.py -v`
- Run `python -m ruff check scripts/memory_consolidation.py models/memory.py agent/memory_retrieval.py`
- Verify dry-run mode produces no Redis writes
- Verify canary set test passes (zero merges proposed)
- Report pass/fail

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: memory-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/subconscious-memory.md` with Memory Consolidation section
- Add `scripts/memory_consolidation.py` to Key Files table
- Update Reversibility section

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_memory_consolidation.py tests/unit/test_memory_model.py tests/unit/test_memory_retrieval.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/memory_consolidation.py models/memory.py agent/memory_retrieval.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/memory_consolidation.py models/memory.py agent/memory_retrieval.py` | exit code 0 |
| superseded_by field exists | `python -c "from models.memory import Memory; assert hasattr(Memory, 'superseded_by')"` | exit code 0 |
| Canary set safe | `pytest tests/unit/test_memory_consolidation.py::test_canary_set_never_merged -v` | exit code 0 |
| Idempotency | `pytest tests/unit/test_memory_consolidation.py::test_idempotency -v` | exit code 0 |
| Reflection registered | `python -c "import yaml; r=yaml.safe_load(open('config/reflections.yaml')); names=[x['name'] for x in r['reflections']]; assert 'memory-dedup' in names"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **#748 timeline**: Is there a target date for #748 to merge? This determines whether Phase 0 work ships as a standalone PR or waits. If #748 is 2+ weeks away, shipping Phase 0 independently (field + filter + tests + callable) is the right call.
2. **Dry-run period length**: The issue spec says 14 days. Is this fixed, or can it be shortened to 7 days if 95% agreement is achieved earlier in the review of `logs/reflections.log`?
3. **Contradiction notification channel**: The plan sends contradiction flags via `valor-telegram send` to the "Dev: Valor" chat. Confirm this is the right channel vs. a dedicated memory-health chat.
