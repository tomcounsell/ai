---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-04-15
revised: 2026-07-27
tracking: https://github.com/tomcounsell/ai/issues/728
last_comment_id: none
revision_applied: true
---

# Agent-Maintained Knowledge Wiki (LLM Wiki Pattern)

## Premise Correction (2026-07-27 re-validation)

**The original premise of this plan was false and has been inverted. Do not
re-derive this archaeology — it is recorded here so a later reader does not
repeat it.**

The plan was written 2026-04-15 (baseline `0e4d41e13`) on the assumption that
the work vault is sparse and stale — "the `AI Valor Engels System/` folder has
only 3 content files, confirming human-maintained wikis go stale" — and that
the agent should therefore **own** the vault. Re-validation against current
main (`aae8f8c60`, 2026-07-27) shows the opposite:

- **The vault is populated and actively human-maintained.** `_notes_/` holds
  31 human-authored business notes (pitch decks, investment theses, leadership
  principles, MOC index files); there are `daily-logs/`, per-project
  `README.md` files with wikilinks already in use, and markitdown `.md`
  sidecars alongside source PDFs/HTML/images. This is a live, curated corpus,
  not a dead one.
- **The vault is a git repository with a remote and auto-sync commits**
  (`tomcounsell/work-vault`, commits like `vault auto-sync: 2026-07-15`). The
  original plan explicitly stated "the vault is not a git repo" and dismissed
  git versioning as an unnecessary rabbit hole. Both were wrong. Auto-sync
  *raises* the idempotency stakes: every non-idempotent agent write becomes a
  commit, so reruns must be true no-ops or they produce commit storms and
  cross-machine merge conflicts.
- **`valor-ingest` / `tools/knowledge/converter.py` now exist** (the plan
  predates them). This is the established agent-writes-into-vault seam and it
  already solves the human/agent boundary with a **provenance-in-frontmatter**
  convention (`generated_by: markitdown`). We follow that convention rather
  than inventing a parallel one.

**Owner decision (2026-07-27): AUGMENT, never own.** The agent adds
clearly-marked pages *alongside* live human content and never claims ownership
of any folder a human writes to. Every safety property below flows from that
decision.

## Problem

**Current behavior:** Agents accumulate knowledge as flat, atomic memory
observations stored in Redis — one-liners like "Redis is used for operational
state only." These observations have no structure, no cross-references, and no
synthesis. When an agent processes a meaningful source (post-merge learning,
architectural decision, article), the insight either becomes a one-liner memory
or disappears into conversation history. Meanwhile the work vault is a real,
human-curated knowledge base — but nothing lets an agent contribute structured,
interlinked pages to it safely.

**Desired outcome:** Agents *augment* the work vault with structured,
interlinked wiki pages following Karpathy's LLM Wiki pattern — entity pages,
concept pages, decision/synthesis documents — written into a **dedicated,
clearly-marked agent-owned subfolder** so they never intermix with or overwrite
human work. Obsidian is the human viewing layer; agents do the bookkeeping. A
periodic lint health-checks the agent-written pages for orphans, staleness, and
contradictions. The subconscious memory system is unchanged and remains the
operational-recall path.

## Non-Negotiable Safety Properties (from the augment decision)

1. **Dedicated subfolder, never intermixed.** Agent pages live only under
   `~/work-vault/{project_folder}/_agent-wiki_/`. The underscore-wrapped name
   matches the vault's own reference-material convention (`_notes_`,
   `_archive_`).
2. **Provenance marker on every page.** Every agent page carries
   `generated_by: agent-wiki` in YAML frontmatter — visible in Obsidian's
   Properties panel, so a human tells agent pages from human pages at a glance.
   This reuses `converter.py`'s `generated_by:` convention.
3. **Marker guard is load-bearing (hard guard + test, not convention).** Before
   writing to any path that already exists, WikiWriter reads the file's
   frontmatter; if it lacks `generated_by: agent-wiki`, the write is REFUSED
   (`WikiOverwriteRefused`, caught and logged by the caller — no file touched).
   A clobbered human file is unrecoverable outside git history, so this is
   enforced in code and asserted by a test.
4. **Superseded, never deleted.** Pruning marks `superseded_by` /
   `deprecated: true` in frontmatter (mirroring the memory system's
   `superseded_by`). WikiWriter never calls `os.remove` on a vault file.
5. **Idempotent, no-op on rerun.** Writes are keyed by slug-based file
   existence and gated by a content hash: identical content is a true no-op (no
   write, no git churn). This is required because the vault auto-syncs to git.

## Recall Posture (the reversal from the original plan)

The original plan had every written page auto-indexed into the subconscious
memory system with a companion `Memory` at `importance=3.0, category="pattern"`
(the indexer stamps `category:"pattern"` at `tools/knowledge/indexer.py:373`).

**That is now the wrong move and this plan reverses it.** A parallel
investigation on #1249 measured the live memory corpus at a **93.4% dismissal
rate across 454 records**, with 237 doc-summary memories sitting at
genuine-learned-pattern weight. Recall is already surfacing mostly unhelpful
material; adding more pattern-weight content would worsen exactly that.

**v1 does NOT feed agent-wiki pages into recall.** Because pages live under
`_agent-wiki_/` (underscore-wrapped), the existing `KnowledgeWatcher` skips them
automatically — `bridge/knowledge_watcher.py:67` (`if part.startswith("_") and
part.endswith("_"): return False`) is the same mechanism that already excludes
`_notes_` and `_archive_`. No watcher change is needed, and there is provably
zero impact on the recall corpus.

Recall-feeding is deferred behind a default-off flag `WIKI_FEED_RECALL`
(default `false`) as a future, measured rollout. If a later change flips it on,
the rollout must weight pages as **reference material** (not learned patterns)
and prove via `python -m tools.memory_eval.snapshot` +
`curl -s localhost:8500/memories/metrics.json` that junk/dismissal rate does not
climb — otherwise it ships behind the flag or not at all. For v1 the before/
after snapshot is expected to show **zero delta**, and that null result is the
honest, intended finding.

## Composition With Existing Seams

- **`valor-ingest` / `converter.py`:** compose, do not duplicate. Agent-wiki
  pages are plain `.md`. We reuse the `generated_by:` frontmatter provenance
  convention converter.py established (ours is `generated_by: agent-wiki`).
- **Subconscious memory (`project_key` partitioning):** unchanged. The wiki is
  complementary — structured, human-viewable knowledge — not a competitor to
  operational recall.
- **#1249 (docs→memory ingestion, IN FLIGHT with another agent):** verified
  OPEN. It ingests `docs/*`; this ingests `~/work-vault/`. Different source
  dirs, so no file conflict. They share the retrieval system, but v1 does not
  feed recall, so there is no collision in v1. Reference each other rather than
  diverging; coordinate before either flips recall-feeding on.

## Freshness Check

**Original baseline commit:** `0e4d41e13`
**Re-validation baseline commit:** `aae8f8c60` (2026-07-27)
**Disposition:** Premise inverted (see Premise Correction above)

**File:line references re-verified on current main:**
- `agent/memory_extraction.py:971` — `extract_post_merge_learning()` exists; still called via `asyncio.run(...)` in `.claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()` (line 1006). **The synchronous-only WikiWriter constraint still holds.**
- `reflections/` package + `config/reflections.yaml` scheduler exist. The file list differs from the original spike-2 (now includes `docs_auditor.py`, `sdlc_progress.py`, `sentry_triage.py`, `stall_advisory.py`, `crash_recovery.py`, `utilities.py`; no `daily_report.py`), but `wiki_lint.py` remains a clean additive slot registered in the YAML.
- `tools/knowledge/{indexer,scope_resolver,converter,chunking}.py` all present. `scope_resolver.resolve_scope()` still resolves `(project_key, scope)` from paths via `projects.json`. `converter.py` is new (markitdown sidecars).
- `bridge/knowledge_watcher.py:67` — underscore-wrapped dir skip confirmed live; this is the v1 recall-exclusion mechanism.
- `~/work-vault/` — populated, git-backed, human-maintained (see Premise Correction).

## Prior Art

- **Issue #528 / PR #605** — read pipeline (vault → memory via `KnowledgeWatcher` + `indexer`). Foundation this augments. Succeeded.
- **`valor-ingest` / `converter.py`** — binary → `.md` sidecar with `generated_by: markitdown` provenance. The convention this plan follows. Live.
- **Issue #748 / PR #967** — extracted `scripts/reflections.py` into the `reflections/` package of async callables. Defines where wiki lint lives.
- **Issue #1249 (open)** — docs→memory ingestion; shares the recall layer, coordinated above.

## Data Flow

### Write Path (augment)

1. **Trigger:** `agent/memory_extraction.py::extract_post_merge_learning()` fires post-merge, OR an agent calls the wiki writer directly.
2. **WikiWriter** (`tools/wiki/writer.py`): sanitizes slug; resolves target path to `{project_folder}/_agent-wiki_/{slug}.md` via `scope_resolver`; runs startup `*.md.tmp` sweep; **marker-guards any existing file**; content-hash gate for no-op reruns; writes atomically (temp + rename); updates `_agent-wiki_/_index.md` and `_agent-wiki_/_log.md`.
3. **No recall indexing** in v1: the `_agent-wiki_/` folder is skipped by `KnowledgeWatcher` (line 67). The flat `Memory` from post-merge extraction is still saved unchanged.
4. **Output:** a structured, human-viewable Obsidian page under `_agent-wiki_/`; no change to the memory recall corpus.

### Lint Path

1. **Trigger:** `ReflectionScheduler` fires `reflections.wiki_lint.run_wiki_lint` (weekly).
2. **WikiLint** (`reflections/wiki_lint.py`): scans ONLY `_agent-wiki_/` folders (never human files); orphan detection (page not in `_index.md`), stale detection (source older than N days), Haiku pairwise contradiction sampling (max `WIKI_LINT_MAX_PAIRS`). Writes JSON to `logs/wiki_lint.log`; returns `{"status", "findings", "summary"}`; never raises.

### Query Path (Read)

1. Humans read pages in Obsidian.
2. Agents read pages on demand via file tools; the vault KB section of `CLAUDE.md` points to `~/work-vault/`.
3. v1 does not inject wiki content via recall (see Recall Posture).

## Architectural Impact

- **New dependencies:** none. Pages are plain markdown.
- **Interface changes:** new `tools/wiki/` package; `reflections/wiki_lint.py` + YAML entry; `extract_post_merge_learning()` gains optional, feature-flagged wiki writing.
- **Coupling:** additive. Existing memory-extraction behavior is unchanged when `WIKI_WRITE_ENABLED=false`.
- **Reversibility:** high. Delete `tools/wiki/`, unregister the lint callable, flip `WIKI_WRITE_ENABLED=false`. No schema migrations. Pages remain readable in Obsidian.

## Configuration (deviation from original plan)

The original plan put `WIKI_WRITE_ENABLED` in `config/settings.py` + `.env.example`.
This revision instead uses **raw-`os.environ` module constants** in `tools/wiki/`,
matching the established provisional-threshold convention (`agent/tool_budget.py`)
and the "named env-overridable constants with a provisional/tunable comment"
directive. No `.env.example` entry is required (consistent with that precedent).

| Constant | Default | Purpose |
|----------|---------|---------|
| `WIKI_WRITE_ENABLED` | `true` | Master switch for post-merge wiki writing |
| `WIKI_FEED_RECALL` | `false` | Deferred: whether to feed pages into recall (future measured rollout) |
| `WIKI_LINT_MAX_PAIRS` | `20` | Cap on Haiku contradiction-check pairs per lint pass |
| `WIKI_TMP_SWEEP_AGE_S` | `300` | Age threshold for orphaned `*.md.tmp` sweep |

Each is a module constant with a `# provisional/tunable` comment.

## Solution

### Key Elements

- **WikiWriter** (`tools/wiki/writer.py`): synchronous-only. `write_page(title,
  content, page_type, project_key, source_ref)`; slug sanitization
  (`re.sub(r'[^a-z0-9\-_]', '-', slug.lower())`); path via `scope_resolver`;
  marker guard; content-hash no-op gate; atomic temp+rename; startup tmp sweep;
  `supersede_page(slug)` for pruning (never deletes). NDA isolation via
  `scope_resolver`.
- **WikiIndex** (`tools/wiki/index.py`): idempotent `_agent-wiki_/_index.md`
  (catalog) and `_agent-wiki_/_log.md` (chronological) upserts.
- **WikiTemplates** (`tools/wiki/templates.py`): entity / concept / decision /
  synthesis frontmatter schemas, each carrying `generated_by: agent-wiki`.
- **WikiLint** (`reflections/wiki_lint.py`): async reflection callable; scans
  only `_agent-wiki_/`; orphan/stale/contradiction; never raises.
- **Post-merge integration:** `extract_post_merge_learning()` writes a page for
  `category in ("decision", "pattern")` when `WIKI_WRITE_ENABLED`, wrapped in
  the existing try/except; failure is non-fatal and the flat Memory still saves.

### Technical Approach

- **Slug sanitization (MANDATORY):** `re.sub(r'[^a-z0-9\-_]', '-', slug.lower())`
  before any path construction — `pathlib` does NOT strip `..`. Test:
  `slugify("fix: config/../secrets")` yields no `/` or `..`.
- **Marker guard (MANDATORY):** overwrite of any existing file requires
  `generated_by: agent-wiki` in its frontmatter, else `WikiOverwriteRefused`.
  Test asserts a human file (no marker) is never written.
- **Synchronous I/O only:** no `aiofiles`, no `async def`, because
  `extract_post_merge_learning()` runs inside `asyncio.run()` in the hook.
- **Atomic write + startup sweep:** write `.{slug}.md.tmp`, `os.rename`; sweep
  `*.md.tmp` older than `WIKI_TMP_SWEEP_AGE_S`.
- **Idempotency:** slug-based existence + content hash → identical content is a
  no-op; changed content merges a dated section and dedupes wikilinks.
- **Wikilinks:** only inject `[[Title]]` for titles present in `_index.md` AND
  with a confirmed on-disk file (no dead links).
- **Concurrency:** `fcntl.flock` on the target for the read-merge-write cycle.

## Failure Path Test Strategy

- `tools/wiki/writer.py` catches all I/O exceptions and logs warnings — never
  crashes the agent or post-merge hook.
- `reflections/wiki_lint.py` catches all exceptions, returns
  `{"status": "error", ...}`, never raises.
- `extract_post_merge_learning()` wiki extension lives inside the existing
  try/except — wiki failure never breaks memory extraction.
- Empty observation → skip page (debug log). Missing project_key → company-wide
  namespace. Empty vault → lint returns ok with empty findings.

## Test Impact

New:
- `tests/unit/test_wiki_writer.py` — creation; **marker guard (human file never
  overwritten)**; slug sanitization; idempotent no-op on identical content;
  atomic write + tmp sweep; supersede (no delete); NDA isolation; recall
  exclusion (page path is under an underscore-wrapped dir the watcher skips).
- `tests/unit/test_wiki_index.py` — index upsert, log append, idempotency, empty.
- `tests/unit/test_wiki_lint.py` — orphan/stale detection, empty vault, never-raises contract, log output.

Update:
- `tests/unit/test_memory_extraction.py` — wiki write path enabled/disabled via
  `WIKI_WRITE_ENABLED`; wiki failure → memory still saved.

## Success Criteria

### Technical
- [ ] WikiWriter creates well-formed pages under `{project}/_agent-wiki_/` with `generated_by: agent-wiki` frontmatter and correct `project_key` isolation.
- [ ] Marker guard: writing over a file lacking the marker raises `WikiOverwriteRefused`, no file changed (test-enforced).
- [ ] Slug sanitization: `slugify("fix: config/../secrets")` has no `/` or `..`.
- [ ] Synchronous-only: `grep -n "async def\|aiofiles" tools/wiki/writer.py` → no output.
- [ ] Idempotent: writing identical content twice produces no second write (content-hash no-op).
- [ ] Supersede marks frontmatter; no `os.remove` on vault files.
- [ ] Recall unaffected: pages under `_agent-wiki_/` are not indexed (watcher line 67); before/after `tools.memory_eval.snapshot` shows zero corpus delta.
- [ ] `reflections/wiki_lint.py` reports orphan/stale/contradiction to `logs/wiki_lint.log`; never raises.
- [ ] Post-merge extraction writes a page for decision/pattern when `WIKI_WRITE_ENABLED=true`.
- [ ] Lint clean (`python -m ruff check .`), format clean.

### User-Facing
- [ ] After a PR merges with a decision/pattern extraction, a page appears under `_agent-wiki_/` in Obsidian, visually distinguished by `generated_by: agent-wiki` in the Properties panel.
- [ ] Human notes in the same project folder are never modified by the agent.
- [ ] Weekly lint produces a `logs/wiki_lint.log` entry with actionable findings.

## Update System

The wiki write feature is local to the bridge machine where `~/work-vault/`
lives. No multi-machine deployment concern for v1 — the vault is a single
Obsidian vault synced by git auto-sync (and iCloud).

- **Update script:** no changes needed. `tools/wiki/` is a new package; no
  migration of existing data.
- **New env vars:** none in `config/settings.py` / `.env.example`. All knobs are
  raw-`os.environ` module constants in `tools/wiki/` (see Configuration above),
  matching the `agent/tool_budget.py` provisional-threshold precedent — so the
  `.env.example` completeness check is unaffected.
- **Reflections YAML:** the `wiki-lint` callable must be registered in
  `config/reflections.yaml` (`callable: reflections.wiki_lint.run_wiki_lint`,
  weekly interval, `priority: low`, `execution_type: function`,
  `group: housekeeping`). The reflections config is machine-synced through the
  existing scheduler wiring; no bespoke propagation is required.
- **Migration:** none. Pages start empty on first ingest; existing vault files
  are never modified (marker guard).

## Agent Integration

No explicit agent invocation is required for v1. The write path is triggered
automatically by the post-merge hook
(`.claude/hooks/hook_utils/memory_bridge.py::post_merge_extract()` →
`extract_post_merge_learning()`), which calls `tools.wiki.writer.write_page()`
for decision/pattern extractions when `WIKI_WRITE_ENABLED` is set.

Agents read wiki pages on demand via ordinary file tools against
`~/work-vault/{project}/_agent-wiki_/`; the vault KB section of `CLAUDE.md`
already points agents at the vault. v1 does **not** expose MCP tools
(`wiki_write`, `wiki_query`) and does **not** inject wiki content through the
recall pipeline (see Recall Posture). Both MCP exposure and measured
recall-feeding are follow-on work, the latter gated by `WIKI_FEED_RECALL`.

## Rabbit Holes / No-Gos

- No recall feeding in v1 (deferred behind `WIKI_FEED_RECALL`).
- No hard-delete of any vault file.
- No writes outside `_agent-wiki_/` subfolders.
- No MCP tool exposure (follow-on).
- No embedding search, no Obsidian plugin.
- No custom multi-machine sync (git auto-sync already handles it; our job is idempotency).

## Step by Step Tasks

### 1. Build Wiki Core (writer + index + templates)
- Create `tools/wiki/{__init__,writer,index,templates}.py`.
- Implement slug sanitization, marker guard (`WikiOverwriteRefused`), content-hash no-op, atomic temp+rename, startup tmp sweep, `supersede_page`, NDA isolation, `_agent-wiki_/` path resolution, sync-only I/O, env module constants.
- Create `tests/unit/test_wiki_writer.py`, `tests/unit/test_wiki_index.py`.

### 2. Build Wiki Lint Reflection
- Create `reflections/wiki_lint.py` (`run_wiki_lint()`), scan only `_agent-wiki_/`.
- Register in `config/reflections.yaml` (weekly, low priority, `execution_type: function`).
- Create `tests/unit/test_wiki_lint.py`.

### 3. Extend Post-Merge Learning Extraction
- Extend `extract_post_merge_learning()` with feature-flagged (`WIKI_WRITE_ENABLED`) page writing for decision/pattern, inside the existing try/except.
- Update `tests/unit/test_memory_extraction.py`.

### 4. Telemetry Before/After
- Snapshot recall corpus before and after (`python -m tools.memory_eval.snapshot`, `curl -s localhost:8500/memories/metrics.json`); confirm zero delta (v1 does not feed recall). Report honestly.

### 5. Documentation
- Create `docs/features/agent-wiki.md`; update `docs/features/knowledge-document-integration.md` (write path), `docs/features/subconscious-memory.md` (wiki/memory boundary), `docs/features/README.md` index.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_wiki_writer.py tests/unit/test_wiki_index.py tests/unit/test_wiki_lint.py tests/unit/test_memory_extraction.py -q` | exit 0 |
| Lint clean | `python -m ruff check .` | exit 0 |
| Wiki writer importable | `python -c "from tools.wiki.writer import write_page; print('ok')"` | ok |
| Wiki lint importable | `python -c "from reflections.wiki_lint import run_wiki_lint; print('ok')"` | ok |
| Slug sanitization | `grep -n "re.sub" tools/wiki/writer.py` | contains `[^a-z0-9` |
| Sync-only writer | `grep -n "async def\|aiofiles" tools/wiki/writer.py` | no output |
| Recall exclusion | pages resolve under a `_agent-wiki_/` dir | watcher line 67 skips |

## Critique Results (original, 2026-04-15 — still applicable)

The original war-room critique's BLOCKER (slug path traversal) and CONCERNs
(sync-only I/O, orphaned tmp files, idempotent merge, user-facing criteria) are
all carried forward above. The MCP-scope and companion-Memory-importance items
are superseded by the augment reframe (no MCP in v1; no recall feeding in v1).
