"""Throwaway spike prototype for docs/plans/hybrid-retrieval-eval.md, task spike-all.

Runs spike-1 (query_cues shape for ContextAssembler.assemble()), spike-2
(read-only / access_count guarantee), and spike-3 (embedding coverage on the
`valor` corpus). Never mutates the `valor` partition — spike-2 clones a
handful of records into a throwaway `dbg-hybrideval-spike` project_key.

Run with:
    .venv/bin/python scripts/spike/hybrid_retrieval_spikes.py

KNOWN GOTCHA (see plan / task brief): config/memory_defaults.py::apply_defaults()
silently swallows a circular-import ImportError when `models.memory` is the
first thing imported in a fresh process, which makes get_default_provider()
return None even when Ollama is healthy. Work around it by importing `agent`
fully before `models.memory`.
"""

import agent  # noqa: F401  (import agent FIRST — see module docstring gotcha)
import models.memory  # noqa: F401  (now safe — agent already in sys.modules)
from agent.embedding_provider import configure_embedding_provider

# Re-run configure_embedding_provider() now that models.memory has fully
# loaded — idempotent, and this time the circular import inside
# apply_defaults() won't trigger because `agent` is already imported.
configure_embedding_provider()

from popoto.fields.embedding_field import get_default_provider  # noqa: E402

provider = get_default_provider()
print(f"[setup] get_default_provider() -> {provider!r}")
assert provider is not None, "Embedding provider not configured — see module docstring gotcha"
assert provider.is_available(), "Embedding provider configured but not available"
print("[setup] provider is available\n")

from popoto.recipes.context_assembler import ContextAssembler  # noqa: E402

from agent.memory_retrieval import retrieve_memories  # noqa: E402
from models.memory import Memory  # noqa: E402

# ---------------------------------------------------------------------------
# spike-3: embedding coverage on the `valor` corpus (measure first — cheap,
# and spike-1 wants an embedded record to build a query around)
# ---------------------------------------------------------------------------
print("=" * 70)
print("SPIKE 3: embedding coverage on valor corpus")
print("=" * 70)

all_valor = list(Memory.query.filter(project_key="valor"))
total_count = len(all_valor)

embedded = [m for m in all_valor if getattr(m, "embedding", None)]
embedded_count = len(embedded)

print(f"[spike-3] total valor records: {total_count}")
print(f"[spike-3] embedded valor records: {embedded_count}")
print(f"[spike-3] coverage: {embedded_count / total_count * 100:.1f}%" if total_count else "n/a")

# ---------------------------------------------------------------------------
# spike-1: confirm ContextAssembler(Memory, ...).assemble(query_cues=<dict>)
# returns AssemblyResult.records for a text-cue query, via the real hybrid
# pull path (not .assess()).
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("SPIKE 1: ContextAssembler.assemble() query_cues shape")
print("=" * 70)

# Pick an embedded record's content as the seed for a realistic query —
# guarantees at least one BM25/vector-overlapping hit exists.
seed_record = embedded[0] if embedded else all_valor[0]
seed_words = (seed_record.content or "").split()[:6]
query_text = " ".join(seed_words) if seed_words else "memory retrieval test"
print(f"[spike-1] seed memory_id={seed_record.memory_id!r}")
print(f"[spike-1] query_text={query_text!r}")

assembler = ContextAssembler(Memory, {}, retrieval_mode="hybrid", max_items=10)
result = assembler.assemble(
    query_cues={"query": query_text},
    partition_filters={"project_key": "valor"},
)

print(f"[spike-1] type(result)={type(result).__name__}")
print(f"[spike-1] len(result.records)={len(result.records)}")
print(f"[spike-1] result.metadata={result.metadata}")

if len(result.records) > 0:
    print("[spike-1] RESOLVED: assemble() returns AssemblyResult.records for a dict query_cues")
    print("[spike-1] confirmed query_cues shape: {'query': <str>} (dict; key name arbitrary — ")
    print(
        "           popoto joins query_cues.values() with a space, see context_assembler.py:1265)"
    )
else:
    print("[spike-1] WARNING: assemble() returned zero records for this query")

# ---------------------------------------------------------------------------
# spike-2: read-only guarantee — neither retrieve_memories() nor
# ContextAssembler.assemble() mutates access_count. Clone a few real `valor`
# records into a throwaway dbg-hybrideval-spike project_key partition first.
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("SPIKE 2: read-only guarantee (access_count) on dbg-hybrideval-spike clone")
print("=" * 70)

DBG_PROJECT = "dbg-hybrideval-spike"

# Clean up any stale clones from a prior run first.
stale = list(Memory.query.filter(project_key=DBG_PROJECT))
for m in stale:
    m.delete()
print(f"[spike-2] cleaned up {len(stale)} stale dbg-hybrideval-spike record(s)")

# Clone up to 5 real valor records' content into the throwaway partition.
source_records = all_valor[:5]
clones = []
for src in source_records:
    clone = Memory(
        agent_id=src.agent_id,
        project_key=DBG_PROJECT,
        content=src.content,
        title=src.title,
        importance=max(src.importance, 5.0),  # ensure it clears WriteFilterMixin threshold
        source=src.source,
    )
    clone.save()
    clones.append(clone)
print(f"[spike-2] cloned {len(clones)} record(s) into {DBG_PROJECT}")

# Capture access_count BEFORE any retrieval call.
before = {c.memory_id: c.access_count for c in clones}
print(f"[spike-2] access_count before: {before}")

# Exercise current path.
_ = retrieve_memories("test query about the cloned content", DBG_PROJECT, limit=10)

# Exercise hybrid path.
clone_query = " ".join((clones[0].content or "").split()[:6]) or "test"
dbg_assembler = ContextAssembler(Memory, {}, retrieval_mode="hybrid", max_items=10)
_ = dbg_assembler.assemble(
    query_cues={"query": clone_query},
    partition_filters={"project_key": DBG_PROJECT},
)

# Re-fetch fresh instances and capture access_count AFTER.
after = {}
for c in clones:
    refreshed = Memory.query.filter(project_key=DBG_PROJECT, memory_id=c.memory_id)
    if refreshed:
        after[c.memory_id] = refreshed[0].access_count
    else:
        after[c.memory_id] = None
print(f"[spike-2] access_count after:  {after}")

unchanged = all(before[k] == after.get(k) for k in before)
print(f"[spike-2] access_count unchanged for all clones: {unchanged}")

# Clean up throwaway clones.
for c in clones:
    c.delete()
print(f"[spike-2] cleaned up {len(clones)} dbg-hybrideval-spike record(s) after test")

print()
print("=" * 70)
print("SPIKE RESULTS SUMMARY")
print("=" * 70)
spike1_status = "RESOLVED" if len(result.records) > 0 else "NOT RESOLVED"
print(f"spike-1 (assemble() returns records): {spike1_status}")
print(f"spike-2 (access_count unchanged): {unchanged}")
print(f"spike-3 (embedded/total): {embedded_count}/{total_count}")
