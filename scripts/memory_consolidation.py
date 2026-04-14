"""Memory consolidation reflection — LLM-based semantic dedup.

Runs as a nightly reflection via the reflection scheduler. Groups active Memory
records by category and tag overlap, calls Haiku to identify near-duplicates and
contradictions, and either logs proposed actions (dry-run mode, the default) or
applies them (apply mode, enabled after a 14-day dry-run review period).

Algorithm:
    1. Load all active Memory records for the resolved project_key
       (filter: superseded_by == "", i.e. not already archived).
    2. Group records by metadata.category and metadata.tags overlap.
       Groups > 50 records are split into sub-batches of 50.
    3. For each group, call Haiku with a structured prompt and parse the JSON response.
    4. Validate each proposed action (reject merges with importance >= 7.0, etc.).
    5. In dry-run mode (default): log proposed actions, no Redis writes.
    6. In apply mode: write merged record via Memory.safe_save(), mark originals
       superseded by setting superseded_by / superseded_by_rationale and calling save().
       Guard the save() return value — WriteFilter may silently drop writes.
    7. Contradiction flagging: send Telegram notification; fall back to
       logs/memory-contradictions.log if bridge is down.

Safety rails:
    - Records with importance >= 7.0 are NEVER merged (exempt).
    - Maximum 10 merges applied per run (MAX_MERGES_PER_RUN).
    - Dry-run is the default; apply is opt-in via apply=True argument or
      running with --apply CLI flag.
    - Contradictions are always flag-only; never auto-resolved.
    - Original records are never deleted — superseded records remain in Redis for audit.

Entry point (zero-argument callable for reflection scheduler):
    run_consolidation()  # project_key resolved via PROJECT_KEY env-var or 'valor'

Manual invocation:
    python scripts/memory_consolidation.py --dry-run
    python scripts/memory_consolidation.py --apply
"""

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from typing import Any

from config.models import HAIKU

logger = logging.getLogger(__name__)

MAX_MERGES_PER_RUN = 10
MAX_BATCH_SIZE = 50
IMPORTANCE_EXEMPT_THRESHOLD = 7.0

_HAIKU_PROMPT_RULES = (
    "You are a memory consolidation assistant. Your job is to identify"
    " near-duplicate memories and contradictions in the set below."
    " You must NOT merge memories with different factual claims or that"
    " cover different topics, even if they use similar language.\n\n"
    "Rules:\n"
    "1. Only propose merging if the memories express the same"
    " instruction/observation with negligible semantic difference.\n"
    "2. A 'contradiction' is two memories that give directly opposing"
    " guidance on the same topic.\n"
    "3. Never merge memories with importance >= 7.0 (these are exempt).\n"
    "4. Return ONLY valid JSON. No prose outside the JSON object.\n"
    '5. If no duplicates or contradictions are found, return {"actions": []}.'
)

_HAIKU_PROMPT_SCHEMA = (
    "Return a JSON object with this exact schema:\n"
    "{\n"
    '  "actions": [\n'
    "    {\n"
    '      "action": "merge",\n'
    '      "ids": ["<id1>", "<id2>"],\n'
    '      "merged_content": "<combined content, max 300 chars>",\n'
    '      "merged_importance": <highest importance of the input records, float>,\n'
    '      "merged_category": "<category from input records, prefer correction if mixed>",\n'
    '      "merged_tags": [<union of input tags, max 5>],\n'
    '      "rationale": "<one sentence explaining why these are duplicates>"\n'
    "    },\n"
    "    {\n"
    '      "action": "flag_contradiction",\n'
    '      "ids": ["<id1>", "<id2>"],\n'
    '      "rationale": "<one sentence explaining the contradiction>"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Do not include any memory with importance >= 7.0 in any action."
)


def _build_haiku_prompt(memories_json: str) -> str:
    """Build the full Haiku prompt with the given memories JSON."""
    return f"{_HAIKU_PROMPT_RULES}\n\nMemories:\n{memories_json}\n\n{_HAIKU_PROMPT_SCHEMA}"


def _resolve_project_key(project_key: str | None) -> str:
    """Resolve project_key: use provided value, env-var, or default to 'valor'."""
    if project_key:
        return project_key
    return os.environ.get("PROJECT_KEY", "valor")


def _load_active_memories(project_key: str) -> list:
    """Load all active (non-superseded) Memory records for the project."""
    from models.memory import Memory

    try:
        all_records = Memory.query.filter(project_key=project_key)
        active = [r for r in all_records if not r.superseded_by]
        logger.debug(
            f"[memory-dedup] Loaded {len(active)} active records "
            f"(total: {len(all_records)}) for project '{project_key}'"
        )
        return active
    except Exception as e:
        logger.warning(f"[memory-dedup] Failed to load memories: {e}")
        return []


def _get_tags(record) -> frozenset:
    """Extract tags from record metadata as a frozenset."""
    try:
        meta = record.metadata or {}
        tags = meta.get("tags", [])
        return frozenset(tags) if tags else frozenset()
    except Exception:
        return frozenset()


def _get_category(record) -> str:
    """Extract category from record metadata."""
    try:
        meta = record.metadata or {}
        return meta.get("category", "")
    except Exception:
        return ""


def _group_records(records: list) -> list[list]:
    """Group records by category and tag overlap into batches of MAX_BATCH_SIZE."""
    # Build groups by (category, tag_fingerprint)
    groups: dict[tuple, list] = {}
    ungrouped = []

    for record in records:
        category = _get_category(record)
        tags = _get_tags(record)

        if category:
            key = (category, frozenset(tags))
            groups.setdefault(key, []).append(record)
        else:
            ungrouped.append(record)

    # Flatten and split into batches
    all_groups = list(groups.values())
    if ungrouped:
        all_groups.append(ungrouped)

    batches = []
    for group in all_groups:
        for i in range(0, len(group), MAX_BATCH_SIZE):
            batch = group[i : i + MAX_BATCH_SIZE]
            if len(batch) >= 2:  # need at least 2 records to find duplicates
                batches.append(batch)

    logger.debug(f"[memory-dedup] Created {len(batches)} batches from {len(records)} records")
    return batches


def _serialize_batch(records: list) -> str:
    """Serialize a batch of records for the Haiku prompt."""
    serialized = []
    for r in records:
        try:
            meta = r.metadata or {}
            serialized.append(
                {
                    "id": r.memory_id,
                    "content": r.content or "",
                    "importance": r.importance or 0.0,
                    "category": meta.get("category", ""),
                    "tags": meta.get("tags", []),
                }
            )
        except Exception as e:
            logger.debug(f"[memory-dedup] Failed to serialize record: {e}")
    return json.dumps(serialized, indent=2)


def _call_haiku(memories_json: str) -> dict | None:
    """Call Haiku API with the consolidation prompt. Returns parsed JSON or None on error."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        prompt = _build_haiku_prompt(memories_json)
        message = client.messages.create(
            model=HAIKU,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"[memory-dedup] Haiku returned invalid JSON: {e}. Raw: {raw[:200]!r}")
        return None
    except Exception as e:
        logger.warning(f"[memory-dedup] Haiku API call failed: {e}")
        return None


def _validate_actions(actions: list, record_map: dict) -> list:
    """Validate parsed actions from Haiku response.

    Rejects:
    - merge actions with fewer than 2 IDs
    - merge actions where any record has importance >= IMPORTANCE_EXEMPT_THRESHOLD
    - actions referencing unknown memory IDs
    """
    valid = []
    for action in actions:
        action_type = action.get("action")
        ids = action.get("ids", [])

        if not ids or len(ids) < 2:
            logger.debug(f"[memory-dedup] Rejected action: fewer than 2 ids: {action}")
            continue

        # Check all IDs exist in the batch
        unknown = [id_ for id_ in ids if id_ not in record_map]
        if unknown:
            logger.debug(f"[memory-dedup] Rejected action: unknown ids {unknown}")
            continue

        if action_type == "merge":
            # Check importance exemption
            exempt = [
                id_
                for id_ in ids
                if (record_map[id_].importance or 0.0) >= IMPORTANCE_EXEMPT_THRESHOLD
            ]
            if exempt:
                logger.debug(
                    f"[memory-dedup] Rejected merge: records {exempt} have importance >= "
                    f"{IMPORTANCE_EXEMPT_THRESHOLD} (exempt)"
                )
                continue

        valid.append(action)
    return valid


def _apply_merge(action: dict, record_map: dict, project_key: str) -> bool:
    """Apply a single merge action. Returns True if applied successfully."""
    from models.memory import SOURCE_SYSTEM, Memory

    ids = action["ids"]
    merged_content = action.get("merged_content", "")
    merged_importance = action.get("merged_importance", 1.0)
    merged_category = action.get("merged_category", "")
    merged_tags = action.get("merged_tags", [])
    rationale = action.get("rationale", "")

    if not merged_content:
        logger.warning(f"[memory-dedup] Merge action has empty merged_content, skipping: {ids}")
        return False

    # Build metadata for merged record
    metadata = {}
    if merged_category:
        metadata["category"] = merged_category
    if merged_tags:
        metadata["tags"] = merged_tags[:5]

    # Create the merged record
    merged = Memory.safe_save(
        agent_id="consolidation",
        project_key=project_key,
        content=merged_content[:500],
        importance=merged_importance,
        source=SOURCE_SYSTEM,
        metadata=metadata,
    )
    if merged is None:
        logger.warning(f"[memory-dedup] Failed to save merged record for ids={ids}")
        return False

    new_id = merged.memory_id

    # Mark originals as superseded
    for id_ in ids:
        record = record_map.get(id_)
        if record is None:
            continue
        record.superseded_by = new_id
        record.superseded_by_rationale = rationale
        result = record.save()
        if result is False:
            logger.warning(
                f"[memory-dedup] WriteFilter blocked superseded_by write for {record.memory_id}"
            )
        else:
            logger.debug(f"[memory-dedup] Marked {record.memory_id} as superseded by {new_id}")

    logger.info(f"[memory-dedup] Merged {ids} → {new_id}: {rationale}")
    return True


def _flag_contradiction(action: dict, record_map: dict) -> None:
    """Flag a contradiction by sending a Telegram notification (with log fallback)."""
    ids = action["ids"]
    rationale = action.get("rationale", "")
    contents = [record_map[id_].content[:100] for id_ in ids if id_ in record_map]
    summary = (
        f"[memory-dedup] Contradiction flagged:\n"
        f"IDs: {ids}\n"
        f"Rationale: {rationale}\n"
        f"Contents: {contents}"
    )
    logger.info(summary)

    # Attempt Telegram notification
    try:
        telegram_msg = (
            f"Memory contradiction detected:\nIDs: {ids}\nReason: {rationale}\nMemories: {contents}"
        )
        subprocess.run(
            ["valor-telegram", "send", "--chat", "Dev: Valor", telegram_msg],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except subprocess.CalledProcessError as e:
        # Bridge is down — write to fallback log
        _write_contradiction_log(ids, rationale, contents)
        logger.warning(
            f"[memory-dedup] Telegram send failed (bridge down): {e}. "
            f"Contradiction written to logs/memory-contradictions.log"
        )
    except Exception as e:
        _write_contradiction_log(ids, rationale, contents)
        logger.warning(
            f"[memory-dedup] Telegram send error: {e}. "
            f"Contradiction written to logs/memory-contradictions.log"
        )


def _write_contradiction_log(ids: list, rationale: str, contents: list) -> None:
    """Write contradiction to fallback log when Telegram bridge is unavailable."""
    try:
        log_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "logs", "memory-contradictions.log"
        )
        timestamp = datetime.now(UTC).isoformat()
        entry = (
            f"[{timestamp}] CONTRADICTION\n"
            f"  IDs: {ids}\n"
            f"  Rationale: {rationale}\n"
            f"  Contents: {contents}\n\n"
        )
        with open(log_path, "a") as f:
            f.write(entry)
    except Exception as e:
        logger.error(f"[memory-dedup] Failed to write contradiction log: {e}")


def _process_batch(
    batch: list,
    project_key: str,
    dry_run: bool,
    applied_merges: int,
    max_merges: int,
) -> dict[str, Any]:
    """Process a single batch of records through Haiku and apply/log actions.

    Returns a dict with proposed_merges, applied_merges, flagged_contradictions, skipped_exempt.
    """
    _empty = {
        "proposed_merges": 0,
        "applied_merges": 0,
        "flagged_contradictions": 0,
        "skipped_exempt": 0,
    }
    if len(batch) < 2:
        return _empty

    record_map = {r.memory_id: r for r in batch}
    memories_json = _serialize_batch(batch)

    parsed = _call_haiku(memories_json)
    if parsed is None:
        return _empty

    raw_actions = parsed.get("actions", [])
    valid_actions = _validate_actions(raw_actions, record_map)
    skipped_exempt = len(raw_actions) - len(valid_actions)

    proposed_merges = sum(1 for a in valid_actions if a.get("action") == "merge")
    flagged_contradictions = sum(
        1 for a in valid_actions if a.get("action") == "flag_contradiction"
    )
    batch_applied = 0

    for action in valid_actions:
        action_type = action.get("action")
        if action_type == "merge":
            if dry_run:
                ids = action.get("ids", [])
                rationale = action.get("rationale", "")
                logger.info(f"[DRY-RUN] Would merge {ids}: {rationale}")
            else:
                if applied_merges + batch_applied >= max_merges:
                    logger.info(
                        f"[memory-dedup] Rate limit reached ({max_merges} merges/run). "
                        f"Skipping remaining merges."
                    )
                    break
                if _apply_merge(action, record_map, project_key):
                    batch_applied += 1
        elif action_type == "flag_contradiction":
            _flag_contradiction(action, record_map)

    return {
        "proposed_merges": proposed_merges,
        "applied_merges": batch_applied,
        "flagged_contradictions": flagged_contradictions,
        "skipped_exempt": skipped_exempt,
    }


def run_consolidation(
    project_key: str | None = None,
    dry_run: bool = True,
    max_merges: int = MAX_MERGES_PER_RUN,
) -> dict[str, Any]:
    """Run memory consolidation for the given project.

    This is the zero-argument callable entry point for the reflection scheduler.
    The scheduler calls func() with no arguments; all params have defaults.

    Args:
        project_key: Project to consolidate. Defaults to None, resolved via
            PROJECT_KEY env-var or 'valor' fallback.
        dry_run: If True (default), log proposed actions without writing to Redis.
            Set to False only after 14-day dry-run review period.
        max_merges: Maximum merges to apply per run. Default: 10.

    Returns:
        Summary dict: {proposed_merges, applied_merges, flagged_contradictions, skipped_exempt}
    """
    resolved_key = _resolve_project_key(project_key)
    mode = "DRY-RUN" if dry_run else "APPLY"
    logger.info(f"[memory-dedup] Starting consolidation for project='{resolved_key}' mode={mode}")

    try:
        records = _load_active_memories(resolved_key)
        if not records:
            logger.info(f"[memory-dedup] No active memories found for project '{resolved_key}'")
            return {
                "proposed_merges": 0,
                "applied_merges": 0,
                "flagged_contradictions": 0,
                "skipped_exempt": 0,
            }

        batches = _group_records(records)
        if not batches:
            logger.info("[memory-dedup] No batches with >= 2 records to process")
            return {
                "proposed_merges": 0,
                "applied_merges": 0,
                "flagged_contradictions": 0,
                "skipped_exempt": 0,
            }

        total_proposed = 0
        total_applied = 0
        total_contradictions = 0
        total_skipped = 0

        for batch in batches:
            result = _process_batch(
                batch=batch,
                project_key=resolved_key,
                dry_run=dry_run,
                applied_merges=total_applied,
                max_merges=max_merges,
            )
            total_proposed += result["proposed_merges"]
            total_applied += result["applied_merges"]
            total_contradictions += result["flagged_contradictions"]
            total_skipped += result["skipped_exempt"]

            # Stop if rate limit reached
            if not dry_run and total_applied >= max_merges:
                break

        summary = {
            "proposed_merges": total_proposed,
            "applied_merges": total_applied,
            "flagged_contradictions": total_contradictions,
            "skipped_exempt": total_skipped,
        }
        logger.info(
            f"[memory-dedup] Complete ({mode}): proposed={total_proposed}, "
            f"applied={total_applied}, contradictions={total_contradictions}, "
            f"skipped_exempt={total_skipped}"
        )
        return summary

    except Exception as e:
        logger.warning(f"[memory-dedup] Consolidation failed: {e}")
        return {
            "proposed_merges": 0,
            "applied_merges": 0,
            "flagged_contradictions": 0,
            "skipped_exempt": 0,
            "error": str(e),
        }


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Memory consolidation reflection")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Log proposed actions without writing to Redis (default)",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply merges and mark superseded records (use after 14-day dry-run review)",
    )
    parser.add_argument(
        "--project-key",
        default=None,
        help="Project key to consolidate (default: PROJECT_KEY env var or 'valor')",
    )
    parser.add_argument(
        "--max-merges",
        type=int,
        default=MAX_MERGES_PER_RUN,
        help=f"Maximum merges to apply per run (default: {MAX_MERGES_PER_RUN})",
    )
    args = parser.parse_args()

    apply_mode = args.apply
    result = run_consolidation(
        project_key=args.project_key,
        dry_run=not apply_mode,
        max_merges=args.max_merges,
    )
    print(json.dumps(result, indent=2))
