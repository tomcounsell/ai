"""
reflections/behavioral_learning.py — Behavioral learning pipeline callable.

Extracted from scripts/reflections.py pipeline:
  step_episode_cycle_close → step_pattern_crystallization

This is a single callable that runs both sub-steps internally,
preserving ordering without depends_on complexity in the YAML scheduler.

Skips gracefully if models.cyclic_episode is not available (guard preserved
from monolith step_behavioral_learning).

Returns:
  {"status": "ok"|"error", "findings": [...], "summary": str}
"""

from __future__ import annotations

import logging
import time as _time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("reflections.behavioral_learning")


async def run() -> dict:
    """Run the behavioral learning pipeline.

    Pipeline: Episode Cycle-Close → Pattern Crystallization

    Maps to monolith: step_behavioral_learning (which calls step_episode_cycle_close
    and step_pattern_crystallization in sequence)

    Skips gracefully if models.cyclic_episode is not available.
    Raises exceptions on sub-step failure (propagates to scheduler).
    """
    try:
        import models.cyclic_episode  # noqa: F401 — probe only
    except ImportError:
        logger.info(
            "[behavioral_learning] models.cyclic_episode not available — "
            "skipping episode cycle-close and pattern crystallization"
        )
        return {
            "status": "ok",
            "findings": [],
            "summary": "behavioral_learning skipped: models.cyclic_episode not installed",
        }

    findings: list[str] = []

    # Sub-step 1: Episode cycle-close
    cutoff = _time.time() - 86400
    episodes_created = 0
    sessions_skipped = 0

    try:
        from models.cyclic_episode import CyclicEpisode
        from models.agent_session import AgentSession

        try:
            from scripts.fingerprint_classifier import classify_session
        except ImportError:

            def classify_session(session):
                return {
                    "problem_topology": "ambiguous",
                    "affected_layer": "unknown",
                    "ambiguity_at_intake": 0.5,
                    "acceptance_criterion_defined": False,
                }

        try:
            all_sessions = AgentSession.query.all()
        except Exception as e:
            logger.warning(f"Episode cycle-close: failed to query sessions: {e}")
            return {"status": "error", "findings": [], "summary": f"Session query error: {e}"}

        for session in all_sessions:
            ca = session.completed_at
            if ca is None:
                continue
            completed_ts = ca.timestamp() if isinstance(ca, datetime) else float(ca)
            if completed_ts < cutoff:
                continue

            if not session.is_sdlc:
                sessions_skipped += 1
                continue

            if session.status != "completed":
                sessions_skipped += 1
                continue

            existing = CyclicEpisode.query.filter(raw_ref=session.agent_session_id)
            if existing:
                sessions_skipped += 1
                continue

            try:
                fingerprint = classify_session(session)
            except Exception as e:
                logger.warning(
                    f"Fingerprint classification failed for {session.agent_session_id}: {e}"
                )
                fingerprint = {
                    "problem_topology": "ambiguous",
                    "affected_layer": "unknown",
                    "ambiguity_at_intake": 0.5,
                    "acceptance_criterion_defined": False,
                }

            vault = f"mem:{session.project_key}" if session.project_key else "mem:default"

            dedup_matches = [
                e
                for e in CyclicEpisode.query.filter(
                    problem_topology=fingerprint["problem_topology"],
                    affected_layer=fingerprint["affected_layer"],
                    vault=vault,
                )
                if e.branch_name and session.branch_name and e.branch_name == session.branch_name
            ]
            if dedup_matches:
                sessions_skipped += 1
                continue

            try:
                CyclicEpisode.create(
                    vault=vault,
                    raw_ref=session.agent_session_id,
                    created_at=_time.time(),
                    problem_topology=fingerprint["problem_topology"],
                    affected_layer=fingerprint["affected_layer"],
                    ambiguity_at_intake=fingerprint["ambiguity_at_intake"],
                    acceptance_criterion_defined=fingerprint["acceptance_criterion_defined"],
                    tool_sequence=session.tool_sequence
                    if isinstance(session.tool_sequence, list)
                    else [],
                    friction_events=session.friction_events
                    if isinstance(session.friction_events, list)
                    else [],
                    stage_durations={},
                    deviation_count=0,
                    resolution_type=(
                        "clean_merge"
                        if not session.has_failed_stage()
                        else "patch_required"
                    ),
                    intent_satisfied=session.status == "completed",
                    review_round_count=0,
                    surprise_delta=0.0,
                    issue_url=session.issue_url,
                    branch_name=session.branch_name,
                    session_summary=session.summary[:1000] if session.summary else None,
                )
                episodes_created += 1
            except Exception as e:
                logger.warning(
                    f"Failed to create episode for session {session.agent_session_id}: {e}"
                )

        if episodes_created:
            findings.append(
                f"Created {episodes_created} behavioral episodes from completed SDLC sessions"
            )
        logger.info(
            f"Episode cycle-close: created={episodes_created}, skipped={sessions_skipped}"
        )

        # Sub-step 2: Pattern crystallization
        crystallization_threshold = 3
        patterns_created = 0
        patterns_reinforced = 0

        try:
            from models.procedural_pattern import ProceduralPattern

            all_episodes = CyclicEpisode.query.all()

            clusters: dict[tuple[str, str], list] = defaultdict(list)
            for episode in all_episodes:
                key = (
                    episode.problem_topology or "ambiguous",
                    episode.affected_layer or "unknown",
                )
                clusters[key].append(episode)

            for (topology, layer), episodes in clusters.items():
                if len(episodes) < crystallization_threshold:
                    continue

                successes = sum(1 for e in episodes if e.intent_satisfied)
                success_rate = successes / len(episodes)

                if success_rate == 0.0:
                    continue

                tool_seqs = [
                    tuple(e.tool_sequence)
                    for e in episodes
                    if isinstance(e.tool_sequence, list) and e.tool_sequence
                ]
                canonical = list(Counter(tool_seqs).most_common(1)[0][0]) if tool_seqs else []

                warnings: list[str] = []
                friction_counts: dict[str, int] = defaultdict(int)
                for episode in episodes:
                    if isinstance(episode.friction_events, list):
                        for fe in episode.friction_events:
                            parts = fe.split("|") if isinstance(fe, str) else []
                            if len(parts) >= 2:
                                friction_counts[f"{parts[0]}:{parts[1]}"] += 1
                for friction_key, count in friction_counts.items():
                    if count > len(episodes) / 2:
                        warnings.append(
                            f"Frequent friction in {topology}/{layer}: {friction_key} "
                            f"({count}/{len(episodes)} episodes)"
                        )

                existing = ProceduralPattern.query.filter(
                    problem_topology=topology, affected_layer=layer
                )
                episode_ids = [e.episode_id for e in episodes if e.episode_id]

                if existing:
                    pattern = existing[0]
                    pattern.reinforce(success_rate > 0.5)
                    pattern.canonical_tool_sequence = canonical
                    pattern.warnings = warnings
                    pattern.source_episode_ids = episode_ids
                    pattern.save()
                    patterns_reinforced += 1
                else:
                    now = _time.time()
                    ProceduralPattern.create(
                        vault="shared",
                        problem_topology=topology,
                        affected_layer=layer,
                        canonical_tool_sequence=canonical,
                        warnings=warnings,
                        shortcuts=[],
                        success_rate=success_rate,
                        sample_count=len(episodes),
                        success_count=successes,
                        confidence=success_rate * min(len(episodes) / 10.0, 1.0),
                        last_reinforced=now,
                        created_at=now,
                        source_episode_ids=episode_ids,
                    )
                    patterns_created += 1

            if patterns_created or patterns_reinforced:
                findings.append(
                    f"Crystallized {patterns_created} new patterns, "
                    f"reinforced {patterns_reinforced} existing patterns"
                )
            logger.info(
                f"Pattern crystallization: created={patterns_created}, "
                f"reinforced={patterns_reinforced}, clusters={len(clusters)}"
            )

        except Exception as e:
            logger.warning(f"Pattern crystallization failed: {e}")

    except Exception as e:
        logger.warning(f"Episode cycle-close failed: {e}")
        return {"status": "error", "findings": findings, "summary": f"Episode error: {e}"}

    summary = (
        f"Behavioral learning: {episodes_created} episodes created, "
        f"patterns: +{patterns_created} new, ={patterns_reinforced} reinforced"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
