"""Finding extraction from dev-session output via Haiku LLM.

Called by SubagentStop hook when a DevSession completes. Sends the
subagent's output to Haiku for structured extraction, then persists
each finding via Finding.safe_save().

Includes deduplication: bloom pre-check + content similarity to
consolidate duplicate findings via confidence reinforcement.

All operations are wrapped in try/except -- extraction failures must
never crash or slow down the agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Extraction prompt for Haiku
EXTRACTION_PROMPT = """\
You are analyzing the output of a development agent session. Extract structured \
findings -- technical discoveries that would be useful for future agent sessions \
working on the same task.

Session context:
- Work item slug: {slug}
- SDLC stage: {stage}
- Project: {project_key}

Agent output:
{output}

Extract findings in these categories:
- file_examined: Files examined and what was learned about them
- pattern_found: Patterns found in the codebase
- decision_made: Decisions made and their rationale
- artifact_produced: Artifacts produced (PRs, commits, test files)
- dependency_discovered: Dependencies discovered

Return a JSON array of findings. Each finding has:
- "category": one of the categories above
- "content": the finding text (max 300 chars, be specific and actionable)
- "file_paths": comma-separated file paths if applicable, or empty string
- "importance": float 1.0-10.0 (higher = more useful for future sessions)

Return ONLY a JSON array, no other text. If no meaningful findings, return [].
Example:
[{{"category": "pattern_found", "content": "Auth uses JWT RS256", \
"file_paths": "auth/jwt.py", "importance": 5.0}}]\
"""

# Maximum findings per extraction to avoid noise
MAX_FINDINGS_PER_EXTRACTION = 10

# Maximum output length to send to Haiku (chars)
MAX_OUTPUT_LENGTH = 8000


def extract_findings_from_output(
    output: str,
    slug: str,
    stage: str,
    session_id: str,
    project_key: str,
) -> list[dict[str, Any]]:
    """Extract and persist findings from a dev-session output.

    Calls Haiku to extract structured findings, deduplicates against
    existing findings for the same slug, and persists new findings.

    Args:
        output: The dev-session's output/result text.
        slug: Work item slug for scoping.
        stage: SDLC stage (BUILD, TEST, etc.).
        session_id: The DevSession's session_id.
        project_key: Project partition key.

    Returns:
        List of dicts representing saved findings (for logging/testing).
        Empty list on any failure.
    """
    if not output or not slug:
        logger.debug("[finding_extraction] No output or slug, skipping extraction")
        return []

    try:
        # Truncate output for Haiku
        truncated_output = output[:MAX_OUTPUT_LENGTH]

        # Call Haiku for extraction
        raw_findings = _call_haiku_extraction(truncated_output, slug, stage, project_key)

        if not raw_findings:
            return []

        # Deduplicate and persist
        saved = []
        for finding_data in raw_findings[:MAX_FINDINGS_PER_EXTRACTION]:
            result = _deduplicate_and_save(
                finding_data=finding_data,
                slug=slug,
                stage=stage,
                session_id=session_id,
                project_key=project_key,
            )
            if result:
                saved.append(result)

        # Update co-occurrences for findings from the same batch
        _update_co_occurrences(saved, slug)

        logger.info(
            f"[finding_extraction] Extracted {len(saved)} findings for slug={slug}, stage={stage}"
        )
        return saved

    except Exception as e:
        logger.warning(f"[finding_extraction] Extraction failed (non-fatal): {e}")
        return []


def _call_haiku_extraction(
    output: str, slug: str, stage: str, project_key: str
) -> list[dict[str, Any]]:
    """Call Haiku to extract structured findings from output.

    Returns a list of finding dicts, or empty list on failure.
    """
    try:
        import anthropic

        from config.models import MODEL_FAST
        from utils.api_keys import get_anthropic_api_key

        api_key = get_anthropic_api_key()
        if not api_key:
            logger.warning("[finding_extraction] No API key available, skipping")
            return []

        prompt = EXTRACTION_PROMPT.format(
            slug=slug,
            stage=stage,
            project_key=project_key,
            output=output,
        )

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=MODEL_FAST,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text if response.content else "[]"

        # Strip markdown code fences
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("\n", 1)[0] if "\n" in text else text[:-3]
        text = text.strip()

        findings = json.loads(text)
        if not isinstance(findings, list):
            logger.warning("[finding_extraction] Haiku returned non-list, skipping")
            return []

        return findings

    except json.JSONDecodeError as e:
        logger.warning(f"[finding_extraction] Failed to parse Haiku response: {e}")
        return []
    except Exception as e:
        logger.warning(f"[finding_extraction] Haiku call failed (non-fatal): {e}")
        return []


def _deduplicate_and_save(
    finding_data: dict[str, Any],
    slug: str,
    stage: str,
    session_id: str,
    project_key: str,
) -> dict[str, Any] | None:
    """Check for duplicates and either reinforce or save new finding.

    Deduplication flow:
    1. Check bloom filter for content
    2. If bloom hit, do full content similarity check within same slug
    3. If duplicate found, reinforce confidence and return None
    4. If no duplicate, save as new finding

    Returns the saved finding data dict, or None if deduplicated/failed.
    """
    try:
        from models.finding import Finding

        content = finding_data.get("content", "")
        if not content or len(content) < 10:
            return None

        category = finding_data.get("category", "pattern_found")
        file_paths = finding_data.get("file_paths", "")
        importance = float(finding_data.get("importance", 3.0))

        # Clamp importance to valid range
        importance = max(0.5, min(10.0, importance))

        # Bloom pre-check for deduplication
        duplicate = _find_duplicate(slug, content)
        if duplicate:
            # Reinforce existing finding's confidence
            try:
                duplicate.confidence.update(positive=True)
                duplicate.confirm_access()
                duplicate.save()
                logger.debug(f"[finding_extraction] Reinforced existing finding: {content[:50]}...")
            except Exception:
                pass
            return None

        # Save new finding
        saved = Finding.safe_save(
            slug=slug,
            project_key=project_key,
            session_id=session_id,
            stage=stage,
            category=category,
            content=content[:500],
            file_paths=str(file_paths)[:500],
            importance=importance,
        )

        if saved:
            return {
                "finding_id": saved.finding_id,
                "category": category,
                "content": content[:500],
                "file_paths": file_paths,
                "importance": importance,
            }
        return None

    except Exception as e:
        logger.warning(f"[finding_extraction] Save failed (non-fatal): {e}")
        return None


def _find_duplicate(slug: str, content: str) -> Any | None:
    """Check if a finding with similar content already exists for this slug.

    Uses bloom filter for fast pre-check, then exact substring match
    for confirmation (bloom has false positives).

    Returns the duplicate Finding instance, or None.
    """
    try:
        from models.finding import Finding

        # Bloom pre-check
        bloom_field = Finding._meta.fields.get("bloom")
        if bloom_field:
            try:
                if not bloom_field.might_exist(Finding, content):
                    return None  # Bloom says definitely not present
            except Exception:
                pass  # Bloom corrupted, fall through to full check

        # Full check: query findings for this slug and check content similarity
        existing = Finding.query_by_slug(slug, limit=50)
        content_lower = content.lower().strip()

        for finding in existing:
            existing_content = (finding.content or "").lower().strip()
            if not existing_content:
                continue

            # Exact match or substantial substring overlap
            if existing_content == content_lower:
                return finding
            if len(content_lower) > 20 and content_lower[:50] in existing_content:
                return finding
            if len(existing_content) > 20 and existing_content[:50] in content_lower:
                return finding

        return None

    except Exception as e:
        logger.debug(f"[finding_extraction] Dedup check failed (non-fatal): {e}")
        return None


def _update_co_occurrences(saved_findings: list[dict[str, Any]], slug: str) -> None:
    """Update CoOccurrenceField for findings from the same extraction batch.

    Findings extracted together are related -- link them via associations.
    """
    if len(saved_findings) < 2:
        return

    try:
        from models.finding import Finding

        finding_ids = [f["finding_id"] for f in saved_findings if f.get("finding_id")]
        if len(finding_ids) < 2:
            return

        # Load findings and update co-occurrences pairwise
        for i, fid in enumerate(finding_ids):
            try:
                findings = list(Finding.query.filter(finding_id=fid))
                if not findings:
                    continue
                finding = findings[0]
                # Associate with other findings in the batch
                for other_id in finding_ids:
                    if other_id != fid:
                        try:
                            finding.associations.record(other_id)
                        except Exception:
                            pass
                finding.save()
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"[finding_extraction] Co-occurrence update failed (non-fatal): {e}")
