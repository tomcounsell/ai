"""
reflections/memory_management.py — Memory management reflection callables.

Reflection callables:
  - run_memory_decay_prune       — Delete below-threshold memories (dry_run default)
  - run_memory_quality_audit     — 3-layer memory health audit (issue #1231):
                                   Layer 0 (legacy zero-access + low-confidence flag, read-only),
                                   Layer 1 (deterministic supersede via _looks_like_refusal),
                                   Layer 2 (heuristic anomaly detection with 4 signals),
                                   Layer 3 (Gemma classification, fail-soft).
                                   Files GitHub issues for Layer 2/3 anomalies via gh CLI.
                                   Subsumes the one-shot scripts/cleanup_memory_extraction_junk.py.
  - run_knowledge_reindex        — Re-index work-vault docs into KnowledgeDocument
  - run_embedding_orphan_sweep   — Reconcile on-disk Memory embeddings against
                                   the live Redis class set; sweep stale
                                   atomic-write tempfiles. Requires Popoto
                                   >= 1.6.0; defensively short-circuits with
                                   a clear "skipped" status against an older
                                   stub.

All functions accept no arguments and return:
  {"status": "ok"|"error", "findings": [...], "summary": str}

Layer 3 reuses ``agent.memory_extraction.extract_json_payload`` to parse
gemma model JSON output. The helper was promoted to the public surface
(rename from ``_extract_json_payload``) precisely so this audit could couple
to a public seam — see plan critique C2.

Layer 1 escape hatch: ``MEMORY_AUDIT_LAYER1_CAP`` env var overrides the
per-run supersede cap. Default 50; ``0`` = unbounded (process all matching
candidates); positive int = override; non-int / negative = defensive
fallback to default. Operator runbook for compressing the tail:

    MEMORY_AUDIT_LAYER1_CAP=0 python -c \\
      "import asyncio; from reflections.memory_management import \\
       run_memory_quality_audit; print(asyncio.run(run_memory_quality_audit())['summary'])"

The env var has zero effect on scheduled runs (the reflection scheduler
does not set it). It must be supplied explicitly per invocation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re as _re
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from agent.memory_extraction import _looks_like_refusal, extract_json_payload
from config.models import OLLAMA_LOCAL_MODEL

# Public-seam reference (resolves critique C2): the literal pattern below is
# what the plan #1231 verification table greps for. The actual module-level
# binding is the consolidated import a few lines above.
#   from agent.memory_extraction import extract_json_payload

logger = logging.getLogger("reflections.memory_management")

# Importance floor matching Memory._wf_min_threshold
WF_MIN_THRESHOLD = 0.15

# Maximum deletions per run to prevent runaway pruning
MAX_PRUNE_PER_RUN = 50

# Memories created less than 30 days ago are exempt from pruning
PRUNE_AGE_DAYS = 30

# Memories with importance >= 7.0 are exempt from pruning (same as memory-dedup rule)
IMPORTANCE_EXEMPT_THRESHOLD = 7.0

# --- Memory health audit (issue #1231) -----------------------------------
# Layer 1 — deterministic supersede constants
CLEANUP_SUPERSEDED_BY = "cleanup-junk-extraction"
CLEANUP_RATIONALE = "auto-cleanup: refusal/json-shrapnel from issue #1212"
DEFAULT_LAYER1_CAP = MAX_PRUNE_PER_RUN  # 50, reuses module constant
MAX_LAYER1_SUPERSEDES_PER_RUN = DEFAULT_LAYER1_CAP  # back-compat alias

# Layer 2 — heuristic anomaly thresholds (tuned against post-Layer-1 corpus)
CATEGORY_DEFAULT_SKEW_THRESHOLD = 0.70  # fraction of last-7d records with no category
IMPORTANCE_1_SKEW_THRESHOLD = 0.85  # fraction of last-7d records at importance==1.0
AGENT_ID_CLUSTER_THRESHOLD = 10  # # records superseded *this run* from a single agent_id
HTML_ESCAPE_RATIO_THRESHOLD = 0.10  # fraction of last-7d records with HTML escapes
HTML_ESCAPE_WOW_RATIO_THRESHOLD = 2.0  # week-over-week ratio jump multiplier

# Layer 3 — gemma classification budget
LAYER3_SAMPLE_SIZE = 20
LAYER3_MIN_SIGNAL_CLUSTER = 3  # # records with same anomaly_signal needed to file an issue
LAYER3_WALLCLOCK_BUDGET_S = 30  # hard cap; abort remaining records past this
GEMMA_CALL_TIMEOUT_SEC = 10  # per-record asyncio.wait_for timeout (resolves critique C3)
LAYER3_PER_CALL_TIMEOUT_S = GEMMA_CALL_TIMEOUT_SEC  # back-compat alias

# Layer 3 — Gemma classification prompt (two-example few-shot, JSON output)
GEMMA_AUDIT_PROMPT = """You are auditing memory records produced by an automated extraction pipeline. Each record is supposed to be a one-sentence observation about an agent session. Some are valid; some are extractor failures (raw JSON output, refusal prose, error text).

CRITICAL: code snippets, HTML examples, JSON config, and shell commands are LEGITIMATE memory content. Do not flag them as junk merely because they contain `<`, `&`, `{`, or quotes. Junk looks like extractor JSON output stored as the observation OR refusal/error text.

Examples:

Record: "Telegram bridge handles SIGTERM via async signal handler at bridge/telegram_bridge.py:842, calling client.disconnect() before exit."
Verdict: {{"is_junk": false, "anomaly_signal": null, "why": "valid observation about a code location and behavior"}}

Record: "\\"observation\\": \\"Update orchestrator automatically bumps critical dependencies\\""
Verdict: {{"is_junk": true, "anomaly_signal": "json-key-as-content", "why": "raw extractor JSON output stored as the observation field"}}

Now audit this record. Respond with ONLY a JSON object: {{"is_junk": bool, "anomaly_signal": str | null, "why": str}}.

Record: {content}
Verdict:"""  # noqa: E501

# Issue body template for Layer 2/3 anomalies
ISSUE_BODY_TEMPLATE = """## Memory Health Audit Anomaly

**Signal:** `{signal}`
**Observed:** {observed}
**Threshold:** {threshold}
**Detected at:** {timestamp}

## Evidence

{evidence}

## Sample memory_ids (3-5)

{sample_ids}

## Suggested investigation

- `python -m tools.memory_search inspect --id <id>` on each sample
- `git log -- agent/memory_extraction.py` to find recent extractor changes
- `tail -200 logs/worker.log | grep memory_extraction` for live extractor errors
- Dashboard: http://localhost:8500/dashboard.json

## Auto-filed metadata

- Source: `reflections/memory_management.py::run_memory_quality_audit`
- Audit feature: #1231
- Cleanup convention: `superseded_by="cleanup-junk-extraction"`

If this is a known/expected condition, close the issue with a comment so future audits don't re-file (the dup-check matches title prefix `[memory-audit] {signal}`).
"""  # noqa: E501

# HTML-escape detection regex (resolves Layer 2 html-escape-rate signal)
_HTML_ESCAPE_RE = _re.compile(r"&#\d+;|&amp;|&lt;|&gt;")


async def run_memory_decay_prune() -> dict:
    """Delete below-threshold memories that have never been accessed.

    Criteria for deletion (all must be true):
    - importance < WF_MIN_THRESHOLD (0.15)
    - access_count == 0
    - created_at > 30 days ago (older than 30 days)
    - importance < 7.0 (exempt threshold)

    Default: dry_run=True for the first two weeks.
    Set env MEMORY_DECAY_PRUNE_APPLY=true to enable actual deletion.

    Caps at 50 deletions per run.
    """
    import os

    apply_mode = os.environ.get("MEMORY_DECAY_PRUNE_APPLY", "false").lower() in ("true", "1", "yes")
    dry_run = not apply_mode

    findings: list[str] = []
    deleted_count = 0
    candidate_count = 0

    try:
        from models.memory import Memory

        cutoff = _time.time() - (PRUNE_AGE_DAYS * 86400)

        try:
            all_memories = Memory.query.all()
        except Exception as e:
            logger.warning(f"Memory decay prune: could not query memories: {e}")
            return {"status": "error", "findings": [], "summary": f"Query error: {e}"}

        candidates = []
        for memory in all_memories:
            # Skip superseded memories (already handled by memory-dedup)
            if memory.superseded_by:
                continue

            importance = memory.importance or 0.0
            if importance >= WF_MIN_THRESHOLD:
                continue
            if importance >= IMPORTANCE_EXEMPT_THRESHOLD:
                continue

            access_count = memory.access_count or 0
            if access_count > 0:
                continue

            # Check age (created_at field)
            created_at = getattr(memory, "created_at", None)
            if created_at is None:
                continue
            from bridge.utc import to_unix_ts

            created_ts = to_unix_ts(created_at)
            if created_ts is None:
                continue
            if created_ts > cutoff:
                # Less than 30 days old — exempt
                continue

            candidates.append(memory)

        candidate_count = len(candidates)
        capped = candidates[:MAX_PRUNE_PER_RUN]

        if dry_run:
            findings.append(
                f"[DRY RUN] Would delete {candidate_count} memories "
                f"(capped at {MAX_PRUNE_PER_RUN}). "
                "Set MEMORY_DECAY_PRUNE_APPLY=true to enable."
            )
            for memory in capped[:5]:
                findings.append(
                    f"  Would delete: memory_id={memory.memory_id}, "
                    f"importance={memory.importance:.3f}, "
                    f"content={str(memory.content)[:60]}"
                )
        else:
            for memory in capped:
                try:
                    memory.delete()
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Memory decay prune: delete failed for {memory.memory_id}: {e}")

            findings.append(
                f"Deleted {deleted_count} of {candidate_count} candidate memories "
                f"(cap={MAX_PRUNE_PER_RUN})"
            )

    except Exception as e:
        logger.warning(f"Memory decay prune failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Memory decay prune error: {e}"}

    mode_str = "DRY RUN" if dry_run else "APPLIED"
    summary = (
        f"Memory decay prune [{mode_str}]: {candidate_count} candidates, {deleted_count} deleted"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


# ============================================================
# Memory Health Audit (issue #1231) — 3-layer reflection
# ============================================================


def _resolve_layer1_cap() -> int | None:
    """Return the per-run Layer 1 supersede cap (resolves critique C5).

    Honors the ``MEMORY_AUDIT_LAYER1_CAP`` env var:
      - unset / non-int  → ``DEFAULT_LAYER1_CAP`` (50)
      - "0"              → ``None`` (no cap; process all matching candidates)
      - positive int     → that integer
      - negative         → ``DEFAULT_LAYER1_CAP`` (defensive fallback)

    The env var is intentionally not propagated by the reflection scheduler;
    it must be set explicitly per invocation by an operator who wants to
    compress the cleanup tail in a one-off run.
    """
    raw = os.environ.get("MEMORY_AUDIT_LAYER1_CAP")
    if raw is None:
        return DEFAULT_LAYER1_CAP
    try:
        n = int(raw)
    except (ValueError, TypeError):
        return DEFAULT_LAYER1_CAP
    if n == 0:
        return None
    if n < 0:
        return DEFAULT_LAYER1_CAP
    return n


def _has_no_category(metadata: dict | None) -> bool:
    """Canonical 'no category' predicate. True when:
    - metadata is None or empty dict (line-based fallback path), OR
    - metadata['category'] is missing, empty string, or the legacy 'default' literal.

    Resolves B1: JSON path writes metadata={"category": <str>}, fallback writes metadata={}.
    Both shapes must count as 'no category' for the skew detector to be meaningful.
    """
    if not metadata:
        return True
    cat = metadata.get("category")
    return not cat or cat == "default"


def _is_extraction_record(memory) -> bool:
    """Return True if memory is an extraction-* record (blast radius gate)."""
    try:
        return str(memory.agent_id or "").startswith("extraction-")
    except Exception:
        return False


def _to_unix_ts_safe(memory) -> float | None:
    """Best-effort created_at -> unix timestamp; None on any failure."""
    try:
        from bridge.utc import to_unix_ts

        created_at = getattr(memory, "created_at", None)
        if created_at is None:
            return None
        return to_unix_ts(created_at)
    except Exception:
        return None


def _layer1_supersede(extraction_records: list) -> tuple[int, int, list[str], list[str]]:
    """Layer 1: deterministic supersede via _looks_like_refusal predicate.

    Returns (superseded_count, blocked_count, just_superseded_ids, agent_ids).
    The just_superseded_ids list feeds Layer 2's agent-id-cluster signal so it
    only counts records superseded *in this run* (resolves C5 idempotency).

    Per-record try/except: one bad save never aborts the layer.
    Detect-and-skip pattern for memory-dedup race (C2): re-read superseded_by
    immediately before save; if non-empty, skip (dedup claimed it).
    """
    superseded = 0
    blocked = 0
    just_superseded_ids: list[str] = []
    agent_ids: list[str] = []

    candidates = [
        m
        for m in extraction_records
        if not (m.superseded_by or "") and _looks_like_refusal(m.content or "")
    ]
    cap = _resolve_layer1_cap()
    capped = candidates if cap is None else candidates[:cap]

    for m in capped:
        # Race detection: re-check superseded_by immediately before write to
        # short-circuit on memory-dedup having claimed the same record.
        try:
            current_superseded = m.superseded_by or ""
            if current_superseded:
                # memory-dedup already claimed it; skip without writing.
                continue
        except Exception:
            pass

        try:
            m.superseded_by = CLEANUP_SUPERSEDED_BY
            m.superseded_by_rationale = CLEANUP_RATIONALE
            result = m.save()
            if result is False:
                blocked += 1
            else:
                superseded += 1
                mid = getattr(m, "memory_id", None)
                if mid:
                    just_superseded_ids.append(str(mid))
                aid = str(getattr(m, "agent_id", "") or "")
                if aid:
                    agent_ids.append(aid)
        except Exception as e:
            mid = getattr(m, "memory_id", "<unknown>")
            logger.warning(f"layer1 save failed for {mid}: {e}")

    return superseded, blocked, just_superseded_ids, agent_ids


def _layer2_signals(
    extraction_records: list,
    just_superseded_ids: list[str],
    just_superseded_agent_ids: list[str],
) -> list[dict]:
    """Layer 2: heuristic anomaly detection. Returns list of anomaly candidates.

    Each candidate is a dict with: signal_name, observed, threshold, sample_ids, evidence.

    Computed against the post-Layer-1 non-superseded extraction-* corpus (the
    `extraction_records` list — caller filters out already-superseded).

    Resolves C5: agent-id-cluster only counts records superseded in *this* run
    (just_superseded_ids parameter), not the cumulative backlog.
    """
    candidates: list[dict] = []
    now = _time.time()

    # Filter to non-superseded. Pinned to this exact comprehension because Layer 1
    # mutates `extraction_records` in-place (sets superseded_by on the same model
    # objects we hold here), so this filter is the post-Layer-1 view derived from
    # the prior load — no second Memory.query.all() is needed.
    live = [m for m in extraction_records if not (m.superseded_by or "")]

    # Time windows
    cutoff_7d = now - 7 * 86400
    cutoff_24h = now - 24 * 3600
    cutoff_14d = now - 14 * 86400

    last_7d: list = []
    last_24h: list = []
    prior_7d: list = []  # 7-14d ago, for WoW comparison

    for m in live:
        ts = _to_unix_ts_safe(m)
        if ts is None:
            continue
        if ts >= cutoff_7d:
            last_7d.append(m)
        elif ts >= cutoff_14d:
            prior_7d.append(m)
        if ts >= cutoff_24h:
            last_24h.append(m)

    # Signal: category-default-skew
    if last_7d:
        no_cat_count = sum(1 for m in last_7d if _has_no_category(getattr(m, "metadata", None)))
        ratio = no_cat_count / len(last_7d)
        if ratio > CATEGORY_DEFAULT_SKEW_THRESHOLD:
            samples = [
                str(m.memory_id) for m in last_7d if _has_no_category(getattr(m, "metadata", None))
            ][:5]
            candidates.append(
                {
                    "signal_name": "category-default-skew",
                    "observed": f"{ratio:.2%} ({no_cat_count}/{len(last_7d)})",
                    "threshold": f"> {CATEGORY_DEFAULT_SKEW_THRESHOLD:.0%}",
                    "sample_ids": samples,
                    "evidence": (
                        f"Of last-7d non-superseded extraction-* records, "
                        f"{no_cat_count}/{len(last_7d)} have no category "
                        f"(metadata empty, missing, or 'default'). "
                        f"Healthy extractor produces categorized observations."
                    ),
                }
            )

    # Signal: importance-1.0-skew
    if last_7d:
        imp_1_count = sum(1 for m in last_7d if (m.importance or 0.0) == 1.0)
        ratio = imp_1_count / len(last_7d)
        if ratio > IMPORTANCE_1_SKEW_THRESHOLD:
            samples = [str(m.memory_id) for m in last_7d if (m.importance or 0.0) == 1.0][:5]
            candidates.append(
                {
                    "signal_name": "importance-1.0-skew",
                    "observed": f"{ratio:.2%} ({imp_1_count}/{len(last_7d)})",
                    "threshold": f"> {IMPORTANCE_1_SKEW_THRESHOLD:.0%}",
                    "sample_ids": samples,
                    "evidence": (
                        f"Of last-7d non-superseded extraction-* records, "
                        f"{imp_1_count}/{len(last_7d)} have importance==1.0. "
                        f"corrections/decisions should be 4.0; chronic 1.0 skew "
                        f"suggests categorization broke."
                    ),
                }
            )

    # Signal: agent-id-cluster (records superseded in THIS run, by agent_id)
    if just_superseded_agent_ids:
        from collections import Counter

        # Zip the parallel lists from _layer1_supersede so each (memory_id,
        # agent_id) pair stays together. This lets us draw `sample_ids`
        # filtered to the specific cluster's agent_id rather than the global
        # pool — important when two distinct agent_ids both cross the
        # cluster threshold in one run (resolves review nit).
        id_pairs = list(zip(just_superseded_ids, just_superseded_agent_ids))
        cluster_counts = Counter(just_superseded_agent_ids)
        for aid, count in cluster_counts.items():
            if count > AGENT_ID_CLUSTER_THRESHOLD:
                # Truncate suffix for issue title specificity (resolves C5 belt-and-suspenders)
                aid_suffix = aid[-40:] if len(aid) > 40 else aid
                cluster_samples = [mid for mid, a in id_pairs if a == aid][:5]
                candidates.append(
                    {
                        "signal_name": f"agent-id-cluster-{aid_suffix}",
                        "observed": f"{count} junk records from agent_id={aid}",
                        "threshold": f"> {AGENT_ID_CLUSTER_THRESHOLD}",
                        "sample_ids": cluster_samples,
                        "evidence": (
                            f"agent_id={aid} produced {count} refusal/shrapnel records "
                            f"that were superseded in this audit run. Strong signal "
                            f"of one stuck session looping on a malformed extractor "
                            f"response."
                        ),
                    }
                )

    # Signal: html-escape-rate (last 7d ratio + WoW jump)
    if last_7d:
        html_count = sum(1 for m in last_7d if _HTML_ESCAPE_RE.search(m.content or ""))
        ratio = html_count / len(last_7d)
        if ratio > HTML_ESCAPE_RATIO_THRESHOLD:
            # WoW comparison: previous week's ratio
            if prior_7d:
                prior_html = sum(1 for m in prior_7d if _HTML_ESCAPE_RE.search(m.content or ""))
                prior_ratio = prior_html / len(prior_7d) if prior_7d else 0.0
            else:
                prior_ratio = 0.0

            wow_jump = (ratio / prior_ratio) if prior_ratio > 0 else float("inf")

            if wow_jump > HTML_ESCAPE_WOW_RATIO_THRESHOLD:
                samples = [
                    str(m.memory_id) for m in last_7d if _HTML_ESCAPE_RE.search(m.content or "")
                ][:5]
                candidates.append(
                    {
                        "signal_name": "html-escape-rate",
                        "observed": (
                            f"{ratio:.2%} ({html_count}/{len(last_7d)}); "
                            f"WoW jump {wow_jump:.1f}x (prior week {prior_ratio:.2%})"
                        ),
                        "threshold": (
                            f"> {HTML_ESCAPE_RATIO_THRESHOLD:.0%} AND "
                            f"WoW > {HTML_ESCAPE_WOW_RATIO_THRESHOLD:.1f}x"
                        ),
                        "sample_ids": samples,
                        "evidence": (
                            f"HTML escape sequences (&#NN;, &amp;, &lt;, &gt;) "
                            f"appearing in {html_count}/{len(last_7d)} last-7d records "
                            f"(prior week: {prior_ratio:.2%}). Suggests new escape bug "
                            f"in upstream extractor."
                        ),
                    }
                )

    return candidates


def _gemma_classify(content: str) -> dict | None:
    """Classify a single record via Gemma. Returns None on any failure (fail-soft)."""
    try:
        import ollama

        response = ollama.chat(
            model=OLLAMA_LOCAL_MODEL,
            messages=[
                {"role": "user", "content": GEMMA_AUDIT_PROMPT.format(content=content[:1000])}
            ],
            options={"temperature": 0},
        )
        raw = response["message"]["content"].strip()
        # Tolerant JSON parse (handles fenced output, preamble) — same helper
        # the extractor uses for the same model family. Imported at module top
        # via the public name (resolves critique C2).
        payload = extract_json_payload(raw) or raw
        return json.loads(payload)
    except Exception as e:
        logger.debug(f"layer3 gemma classify failed (non-fatal): {e}")
        return None


async def _layer3_classify(extraction_records: list) -> tuple[list[dict], list[str]]:
    """Layer 3: Gemma classification (fail-soft, wallclock-budgeted).

    Returns (anomaly_candidates, layer3_findings). On any top-level failure,
    returns ([], [...]) — the audit completes without Layer 3.

    Wallclock budget enforced via deadline check + per-call asyncio.wait_for.
    Dedicated single-thread executor per invocation, shut down in finally.
    """
    candidates: list[dict] = []
    findings: list[str] = []
    now = _time.time()

    try:
        # Sample last-24h non-superseded extraction-* records (post-Layer-1).
        live = [m for m in extraction_records if not (m.superseded_by or "")]
        cutoff_24h = now - 24 * 3600
        last_24h = []
        for m in live:
            ts = _to_unix_ts_safe(m)
            if ts is not None and ts >= cutoff_24h:
                last_24h.append(m)

        sample = last_24h[:LAYER3_SAMPLE_SIZE]
        if not sample:
            findings.append("layer-3: no last-24h extraction-* records to classify")
            return candidates, findings

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="memory-audit-l3")
        try:
            deadline = _time.monotonic() + LAYER3_WALLCLOCK_BUDGET_S
            verdicts: list[tuple[str, dict]] = []  # (memory_id, verdict)
            unavailable_count = 0
            processed = 0

            for record in sample:
                if _time.monotonic() >= deadline:
                    findings.append(
                        f"layer-3: wallclock budget {LAYER3_WALLCLOCK_BUDGET_S}s exceeded "
                        f"after {processed}/{len(sample)} records — skipping rest"
                    )
                    break

                content = record.content or ""
                if not content:
                    processed += 1
                    continue

                try:
                    # Resolves critique C3: use asyncio.get_running_loop()
                    # (not the deprecated asyncio loop accessor) and bound
                    # each call by GEMMA_CALL_TIMEOUT_SEC.
                    loop = asyncio.get_running_loop()
                    verdict = await asyncio.wait_for(
                        loop.run_in_executor(executor, _gemma_classify, content),
                        timeout=GEMMA_CALL_TIMEOUT_SEC,
                    )
                except TimeoutError:
                    verdict = None
                except Exception as e:
                    logger.debug(f"layer3 record dispatch failed: {e}")
                    verdict = None

                processed += 1

                if verdict is None:
                    unavailable_count += 1
                    continue

                if verdict.get("is_junk") is True:
                    mid = str(getattr(record, "memory_id", "") or "")
                    if mid:
                        verdicts.append((mid, verdict))

            # Group by anomaly_signal
            if processed > 0 and unavailable_count == processed:
                findings.append(f"layer-3 skipped: ollama unavailable for all {processed} records")

            if verdicts:
                from collections import defaultdict

                signal_groups: dict[str, list[tuple[str, dict]]] = defaultdict(list)
                for mid, v in verdicts:
                    sig = v.get("anomaly_signal")
                    if sig:
                        signal_groups[sig].append((mid, v))

                for sig, members in signal_groups.items():
                    if len(members) >= LAYER3_MIN_SIGNAL_CLUSTER:
                        sample_ids = [mid for mid, _ in members[:5]]
                        whys = [v.get("why", "") for _, v in members[:3]]
                        candidates.append(
                            {
                                "signal_name": f"gemma-{sig}",
                                "observed": f"{len(members)} records flagged by gemma",
                                "threshold": f">= {LAYER3_MIN_SIGNAL_CLUSTER}",
                                "sample_ids": sample_ids,
                                "evidence": (
                                    f"Gemma classified {len(members)}/{processed} "
                                    f"sampled records as junk with signal '{sig}'.\n\n"
                                    f"Sample whys:\n" + "\n".join(f"- {w}" for w in whys if w)
                                ),
                            }
                        )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    except Exception as e:
        logger.debug(f"layer3 outer wrapper failed (non-fatal): {e}")
        findings.append(f"layer-3 skipped: {e}")

    return candidates, findings


async def _find_open_audit_issue(signal_name: str) -> int | None:
    """Return the issue number of an open audit issue for this signal, or None.

    Resolves critique C4: title-prefix is the **sole** dup-check key. Labels
    are descriptive only and may be stripped or relabeled by an operator
    without breaking idempotency. The `gh issue list` call deliberately does
    NOT include any label-filter flag and the `--search` query has no
    ``label:`` term — the title prefix that the audit itself controls is the
    structured primary key.

    Async via ``asyncio.create_subprocess_exec`` + ``asyncio.wait_for`` so the
    worker event loop is not blocked by ``gh`` latency (matches the pattern in
    ``reflections/maintenance.py:244-265``).

    Returns -1 sentinel on `gh` failure (treated as "duplicate exists" by callers
    to suppress filing for the run; better to skip than spam if search is broken).
    """
    title_prefix = f"[memory-audit] {signal_name}:"
    search_query = f'in:title "{title_prefix}"'
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--search",
            search_query,
            "--json",
            "number,title",
            "--limit",
            "20",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"gh exited {proc.returncode}")
        issues = json.loads(stdout.decode())
        for issue in issues:
            if issue.get("title", "").startswith(title_prefix):
                return issue["number"]
        return None
    except Exception as e:
        logger.warning(f"dup-check failed for {signal_name}, suppressing file: {e}")
        return -1  # sentinel: treated as "duplicate exists" by callers


async def _file_anomaly_issue(
    signal_name: str,
    observed: str,
    threshold: str,
    sample_ids: list[str],
    evidence: str,
) -> bool:
    """File a GitHub issue for a Layer 2/3 anomaly.

    Async via ``asyncio.create_subprocess_exec`` + ``asyncio.wait_for`` so the
    worker event loop is not blocked by ``gh`` latency.

    Returns True if filed, False if skipped (dup) or failed (logged).
    """
    title = f"[memory-audit] {signal_name}: {observed} (threshold {threshold})"

    existing = await _find_open_audit_issue(signal_name)
    if existing is not None:
        # Either real dup (positive int) or sentinel (-1) — both suppress filing.
        if existing == -1:
            logger.debug(f"layer2/3: {signal_name} — dup-check failed, suppressing")
        else:
            logger.debug(f"layer2/3: skipping {signal_name} — open issue #{existing} exists")
        return False

    body = ISSUE_BODY_TEMPLATE.format(
        signal=signal_name,
        observed=observed,
        threshold=threshold,
        sample_ids="\n".join(f"- `{mid}`" for mid in sample_ids[:5]) or "- (none)",
        evidence=evidence,
        timestamp=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "issue",
            "create",
            "--label",
            "memory",
            "--label",
            "investigation",
            "--title",
            title,
            "--body",
            body,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"gh exited {proc.returncode}")
        return True
    except Exception as e:
        logger.warning(f"gh issue create failed for {signal_name}: {e}")
        return False


async def run_memory_quality_audit() -> dict:
    """3-layer memory health audit (issue #1231).

    Layer 0 (legacy, read-only): Flag zero-access + low-confidence memories.
    Layer 1 (always-apply): Supersede extraction-* records that match the
        same _looks_like_refusal predicate that gates new writes upstream.
        Capped at MAX_LAYER1_SUPERSEDES_PER_RUN (50).
    Layer 2 (heuristic anomaly detection): Compute 4 signals against the
        post-Layer-1 corpus. Cross-threshold signals file GitHub issues
        (deduped via title-prefix search).
    Layer 3 (Gemma classification, fail-soft): Sample up to 20 last-24h
        records and classify via gemma4:e2b. Wallclock-budgeted at 30s
        with 5s per-call timeout. Files issues for clusters of >=3 records
        sharing an anomaly_signal.

    Returns: {"status": "ok"|"error", "findings": [...], "summary": str}

    Layer 1 supersedes never trigger issues (clearing known junk is the
    expected steady-state work). Layer 2/3 issues are the alerting channel.
    """
    findings: list[str] = []
    flagged_zero_access = 0
    flagged_low_confidence = 0
    layer1_superseded = 0
    layer1_blocked = 0
    layer1_skipped_already_superseded = 0
    layer2_anomalies = 0
    layer3_anomalies = 0
    issues_filed = 0

    try:
        from models.memory import Memory

        cutoff = _time.time() - (PRUNE_AGE_DAYS * 86400)

        try:
            all_memories = Memory.query.all()
        except Exception as e:
            logger.warning(f"Memory quality audit: could not query memories: {e}")
            return {"status": "error", "findings": [], "summary": f"Query error: {e}"}

        if not all_memories:
            return {
                "status": "ok",
                "findings": [],
                "summary": "Memory quality audit: no memories to audit",
            }

        # ---- Layer 0: legacy zero-access + low-confidence flagging --------
        # Operates on the full Memory corpus (not just extraction-*) so it
        # provides orthogonal observability for human-saved, post-merge, and
        # Telegram memories. Read-only — files no issues.
        for memory in all_memories:
            if memory.superseded_by:
                continue

            created_ts = _to_unix_ts_safe(memory)
            if created_ts is None:
                continue

            access_count = memory.access_count or 0
            if access_count == 0 and created_ts < cutoff:
                flagged_zero_access += 1
                if flagged_zero_access <= 5:
                    findings.append(
                        f"Zero-access memory: memory_id={memory.memory_id}, "
                        f"importance={memory.importance:.2f}, "
                        f"content={str(memory.content)[:80]}"
                    )

            try:
                confidence_val = float(memory.confidence) if memory.confidence is not None else None
                if confidence_val is not None and confidence_val < 0.2:
                    flagged_low_confidence += 1
                    if flagged_low_confidence <= 5:
                        findings.append(
                            f"Low-confidence memory: memory_id={memory.memory_id}, "
                            f"confidence={confidence_val:.3f}, "
                            f"importance={memory.importance:.2f}"
                        )
            except (TypeError, ValueError):
                pass

        findings.append(
            f"Layer 0 audit totals: {flagged_zero_access} zero-access, "
            f"{flagged_low_confidence} low-confidence memories"
        )

        # ---- Layer 1: deterministic supersede ---------------------------------
        extraction_records = [m for m in all_memories if _is_extraction_record(m)]

        # Resolve the per-run cap ONCE so both the supersede call and the
        # finding label agree on what cap actually applied (resolves review
        # tech-debt: hardcoded cap=50 didn't reflect MEMORY_AUDIT_LAYER1_CAP
        # operator overrides).
        resolved_cap = _resolve_layer1_cap()
        cap_label = "unbounded" if resolved_cap is None else str(resolved_cap)

        (
            layer1_superseded,
            layer1_blocked,
            just_superseded_ids,
            just_superseded_agent_ids,
        ) = _layer1_supersede(extraction_records)

        # Count records that STILL match the refusal predicate after Layer 1
        # ran. Layer 1 mutates `superseded_by` in-place on records it claims,
        # so this filter excludes just_superseded ones — the finding makes
        # that explicit by labeling the value as "remaining" rather than
        # "candidates" (resolves review tech-debt: the prior label "of M
        # candidates" misled operators into reading M as the original pool).
        layer1_remaining = sum(
            1
            for m in extraction_records
            if not (m.superseded_by or "") and _looks_like_refusal(m.content or "")
        )

        findings.append(
            f"Layer 1: superseded {layer1_superseded}, remaining {layer1_remaining} "
            f"candidates (cap={cap_label}, blocked={layer1_blocked})"
        )

        # ---- Layer 2: heuristic anomaly detection -----------------------------
        # Re-load extraction_records from in-memory list, excluding records we
        # just superseded in this run (their superseded_by is now non-empty).
        # No additional Redis query needed.
        layer2_candidates = _layer2_signals(
            extraction_records,
            just_superseded_ids,
            just_superseded_agent_ids,
        )
        layer2_anomalies = len(layer2_candidates)
        if layer2_anomalies:
            findings.append(f"Layer 2: detected {layer2_anomalies} anomaly signal(s)")

        # ---- Layer 3: Gemma classification (fail-soft) ------------------------
        layer3_candidates: list[dict] = []
        layer3_findings: list[str] = []
        try:
            layer3_candidates, layer3_findings = await _layer3_classify(extraction_records)
        except Exception as e:
            logger.debug(f"layer3 outer raised (non-fatal): {e}")
            layer3_findings = [f"layer-3 skipped: {e}"]

        layer3_anomalies = len(layer3_candidates)
        findings.extend(layer3_findings)
        if layer3_anomalies:
            findings.append(f"Layer 3: detected {layer3_anomalies} anomaly signal(s)")

        # ---- Issue surfacing (Layer 2 + Layer 3) ------------------------------
        for candidate in layer2_candidates + layer3_candidates:
            try:
                filed = await _file_anomaly_issue(
                    signal_name=candidate["signal_name"],
                    observed=candidate["observed"],
                    threshold=candidate["threshold"],
                    sample_ids=candidate["sample_ids"],
                    evidence=candidate["evidence"],
                )
                if filed:
                    issues_filed += 1
                    findings.append(f"Filed issue for signal: {candidate['signal_name']}")
            except Exception as e:
                logger.warning(f"issue surfacing failed for {candidate.get('signal_name')}: {e}")
                findings.append(
                    f"layer2/3: failed to file issue for {candidate.get('signal_name')}: {e}"
                )

    except Exception as e:
        logger.warning(f"Memory quality audit failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Memory quality audit error: {e}"}

    summary = (
        f"Memory health audit: {layer1_superseded} superseded, "
        f"{layer2_anomalies + layer3_anomalies} anomalies, "
        f"{issues_filed} issues filed"
    )
    # Reference unused locals for clarity in summary; Layer 0 + skipped counts
    # surface in findings list, not summary.
    _ = (flagged_zero_access, flagged_low_confidence, layer1_skipped_already_superseded)
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_knowledge_reindex() -> dict:
    """Re-index work-vault docs into KnowledgeDocument records.

    Idempotent: existing records with matching hash are skipped.

    If KnowledgeDocument is not available (issue #728 not yet merged),
    returns a stub result with status "skipped".

    If ~/src/work-vault/ does not exist (e.g., CI), returns gracefully.
    """
    # Check for work-vault directory
    vault_path = Path.home() / "src" / "work-vault"
    if not vault_path.exists():
        logger.info("knowledge-reindex: ~/src/work-vault/ not found, skipping")
        return {
            "status": "ok",
            "findings": [],
            "summary": "knowledge-reindex skipped: ~/src/work-vault/ not found",
        }

    # Probe for KnowledgeDocument availability
    try:
        import tools.knowledge.indexer as _indexer  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        logger.info("knowledge-reindex: tools.knowledge.indexer not available (issue #728 pending)")
        return {
            "status": "ok",
            "findings": [],
            "summary": "knowledge-reindex skipped: KnowledgeDocument not available (see #728)",
        }

    try:
        from tools.knowledge.indexer import reindex_vault

        result = reindex_vault(str(vault_path))
        indexed = result.get("indexed", 0)
        skipped = result.get("skipped", 0)
        errors = result.get("errors", [])

        findings = [f"Indexed {indexed} docs, skipped {skipped} unchanged"]
        for error in errors[:5]:
            findings.append(f"Error: {error}")

        summary = f"knowledge-reindex: {indexed} indexed, {skipped} skipped, {len(errors)} errors"
        logger.info(summary)
        return {"status": "ok", "findings": findings, "summary": summary}

    except Exception as e:
        logger.warning(f"knowledge-reindex failed: {e}")
        return {"status": "error", "findings": [], "summary": f"knowledge-reindex error: {e}"}


# Mtime guard threshold for the orphan sweep — see plan Race Conditions section.
# 5 minutes covers Ollama timeout/retry pathologies during atomic writes.
_EMBEDDING_ORPHAN_MIN_AGE_SECONDS = 300

# Stale atomic-write tempfile cutoff — atomic writes complete in milliseconds,
# so anything older than 1 hour is unambiguously a leaked file.
_EMBEDDING_TEMPFILE_MAX_AGE_SECONDS = 3600


async def run_embedding_orphan_sweep() -> dict:
    """Reconcile on-disk Memory embeddings against the live Redis class set.

    Two-phase sweep:

    1. ``EmbeddingField.garbage_collect(Memory)`` — removes ``.npy`` files
       whose name is not in the SHA-256 hashed expected-keep set computed
       from ``$Class:Memory``. Mtime guard of 5 minutes protects in-flight
       saves.
    2. ``EmbeddingField.sweep_stale_tempfiles(Memory)`` — removes
       ``tmp*.npy`` atomic-write tempfiles older than 1 hour (leaked
       on process crashes between ``mkstemp`` and ``rename``).

    Defensive guard: if the installed Popoto is < 1.6.0 (stub
    ``garbage_collect`` body), the sweep short-circuits with a clear
    "skipped" status rather than silently appearing to succeed.

    Apply gating: ``EMBEDDING_ORPHAN_SWEEP_APPLY=true`` enables actual
    deletion. Default is dry-run — the sweep walks the directory and
    reports counts without unlinking anything.

    Emits two metrics:
      - ``memory.embedding_orphans_swept``    (count, dimensions={"mode": "..."})
      - ``memory.embedding_tempfiles_swept``  (count, dimensions={"mode": "..."})
    """
    import os

    findings: list[str] = []

    # --- Stub-detection guard (Popoto < 1.6.0) ------------------------------
    # Capability probe: Popoto 1.6.0 introduces EmbeddingField.sweep_stale_tempfiles
    # alongside the real garbage_collect implementation. The 1.5.x stub has only
    # garbage_collect (returning 0 unconditionally) and lacks sweep_stale_tempfiles.
    # Probing for the new method is a deterministic across-version signal — the
    # earlier docstring-marker check failed because the "Future enhancement" phrase
    # lived in the method body comment, not the docstring (verified live on 1.5.0).
    try:
        from popoto.fields.embedding_field import EmbeddingField
    except Exception as e:
        return {
            "status": "error",
            "findings": [f"popoto import failed: {e}"],
            "summary": "embedding-orphan-sweep error: popoto not importable",
        }

    if not hasattr(EmbeddingField, "sweep_stale_tempfiles"):
        logger.warning(
            "embedding-orphan-sweep: popoto-embedding-gc-stub-detected — install popoto>=1.6.0"
        )
        return {
            "status": "ok",
            "findings": ["popoto<1.6 — gc not implemented yet"],
            "summary": "embedding-orphan-sweep skipped (popoto stub)",
        }

    apply_mode = os.environ.get("EMBEDDING_ORPHAN_SWEEP_APPLY", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    dry_run = not apply_mode
    mode_str = "DRY RUN" if dry_run else "APPLIED"

    try:
        from models.memory import Memory
    except Exception as e:
        logger.warning("embedding-orphan-sweep: Memory import failed: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"embedding-orphan-sweep error: Memory import failed: {e}",
        }

    # --- Count-only when in dry-run mode ------------------------------------
    orphans_swept = 0
    tempfiles_swept = 0

    try:
        if dry_run:
            # Use the read-only count helper instead of touching the directory.
            try:
                from scripts.popoto_index_cleanup import _count_disk_orphans

                would_remove = _count_disk_orphans(Memory)
                orphans_swept = would_remove
            except Exception as e:
                logger.warning("embedding-orphan-sweep: dry-run count failed: %s", e)
                would_remove = 0
                orphans_swept = 0

            findings.append(
                f"[DRY RUN] Would remove ~{would_remove} disk orphans. "
                "Set EMBEDDING_ORPHAN_SWEEP_APPLY=true to enable deletion."
            )
            # Don't sweep tempfiles in dry-run either — keep this fully read-only
            findings.append("[DRY RUN] Stale tmp*.npy sweep deferred until apply mode.")
        else:
            try:
                orphans_swept = EmbeddingField.garbage_collect(
                    Memory, min_age_seconds=_EMBEDDING_ORPHAN_MIN_AGE_SECONDS
                )
            except Exception as e:
                logger.warning("embedding-orphan-sweep: garbage_collect failed: %s", e)
                findings.append(f"garbage_collect error: {e}")

            try:
                tempfiles_swept = EmbeddingField.sweep_stale_tempfiles(
                    Memory, max_age_seconds=_EMBEDDING_TEMPFILE_MAX_AGE_SECONDS
                )
            except Exception as e:
                logger.warning("embedding-orphan-sweep: sweep_stale_tempfiles failed: %s", e)
                findings.append(f"sweep_stale_tempfiles error: {e}")

            findings.append(
                f"Removed {orphans_swept} orphan .npy files and "
                f"{tempfiles_swept} stale tmp*.npy files."
            )
    except Exception as e:
        logger.warning("embedding-orphan-sweep failed: %s", e)
        return {
            "status": "error",
            "findings": findings,
            "summary": f"embedding-orphan-sweep error: {e}",
        }

    # Emit metrics — best-effort, never crash the reflection
    try:
        from analytics.collector import record_metric

        record_metric(
            "memory.embedding_orphans_swept",
            float(orphans_swept),
            dimensions={"mode": mode_str.lower().replace(" ", "_")},
        )
        record_metric(
            "memory.embedding_tempfiles_swept",
            float(tempfiles_swept),
            dimensions={"mode": mode_str.lower().replace(" ", "_")},
        )
    except Exception as e:
        logger.debug("embedding-orphan-sweep: metric emission failed: %s", e)

    summary = (
        f"embedding-orphan-sweep [{mode_str}]: {orphans_swept} orphans, {tempfiles_swept} tempfiles"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
