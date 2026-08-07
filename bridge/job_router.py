"""Bind-or-mint Job routing on the local granite model (Task 13, #2494).

The single routing authority for inbound messages — it replaces the retired
``bridge/session_router.py`` semantic session router. Structure is modeled
on that router's proven shape:

* **Zero-candidate short-circuit** — no Jobs in the Room means no model
  call; mint NEW immediately.
* **Top-5 recency cap** — candidates come from
  :meth:`models.job.Job.recent_for_room` (the ``last_active_at``
  SortedField partition).
* **Post-hoc ``valid_ids`` membership check** — a model verdict naming a
  job id outside the candidate set is treated as NEW, never trusted.
* **Total fail-open to NEW** — any model/transport/schema failure mints a
  new Job. The worst case is an extra Job (spike-3 measured ~35% over-mint
  at the tuned threshold, the safe direction); never a lost or wrong-bound
  message. The durable Room-inbox append precedes routing, so router
  latency and failures are a UX cost, not a durability cost.

The granite call goes through :func:`agent.llm.run_typed_local`
(PydanticAI + OllamaProvider) with the strict :class:`JobRouteDecision`
output model — per the two-transport rule, harness for session work,
PydanticAI for all non-harness LLM calls.

**Permanent reply index** (schema-gate ruling 1): ``reply:{message_key}``
is a standalone non-Popoto string key — no TTL, bound via ``SET NX``
(Race 4: binding is idempotent on the message identity; a crash-after-bind
re-route finds the existing binding and no-ops). Precedent for the raw
string-key ``SET NX`` shape: ``bridge/context.py`` root-id mapping and
``bridge/dedup.py::claim_message``. A plain string KV has no hash, no
class set, and no secondary index, so the #2207 flood mechanism
structurally cannot occur for it; it is deliberately outside
``index_drift``/``rebuild_indexes``. Telegram message ids are per-chat, so
the message key carries the chat id (:func:`telegram_message_key`).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

from pydantic import BaseModel

from agent.llm import run_typed_local
from models.job import Job

logger = logging.getLogger(__name__)

# Confidence threshold for binding to an existing Job. Below it the verdict
# falls open to NEW. GRAIN OF SALT: provisional/tunable via env — spike-3
# tuned 0.70 on replayed real traffic (wrong-bind 0/35 at every threshold;
# over-mint ~35% is the one production number to watch, and the reduction
# lever is prompt tuning, not this threshold).
JOB_ROUTER_CONFIDENCE_THRESHOLD = float(os.environ.get("JOB_ROUTER_CONFIDENCE_THRESHOLD", "0.70"))

# Top-N recency cap on candidate Jobs offered to the classifier.
_CANDIDATE_CAP = 5

_REPLY_INDEX_PREFIX = "reply:"


class JobRouteDecision(BaseModel):
    """Strict structured output for the bind-or-mint granite call."""

    decision: Literal["bind", "new"]
    job_id: str | None
    confidence: float


# ── Permanent message → job index ────────────────────────────────────


def telegram_message_key(chat_id, message_id) -> str:
    """Stable message identity for the reply index.

    Telegram message ids are only unique per chat, so the key is
    ``{chat_id}:{message_id}``. Email callers should pass the globally
    unique RFC 5322 Message-ID directly instead.
    """
    return f"{chat_id}:{message_id}"


def bind_message_to_job(message_key: str, job_id: str, *, room_id: str) -> bool:
    """Bind a message to a Job via ``SET NX`` — first writer wins, no TTL.

    The stored value carries ``room_id`` alongside ``job_id`` so a hit
    reconstructs the full composite Job key for the fetch (schema-gate
    ruling: no id-alone Job lookup).
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    payload = json.dumps({"job_id": job_id, "room_id": room_id})
    return bool(POPOTO_REDIS_DB.set(f"{_REPLY_INDEX_PREFIX}{message_key}", payload, nx=True))


def lookup_job_for_message(message_key: str) -> tuple[str, str] | None:
    """Resolve a message to its bound ``(job_id, room_id)``, or ``None``."""
    from popoto.redis_db import POPOTO_REDIS_DB

    raw = POPOTO_REDIS_DB.get(f"{_REPLY_INDEX_PREFIX}{message_key}")
    if not raw:
        return None
    try:
        data = json.loads(raw if isinstance(raw, str) else raw.decode())
        return (str(data["job_id"]), str(data["room_id"]))
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        logger.warning("[job-router] invalid reply-index payload for %s", message_key)
        return None


def _fetch_job(job_id: str, room_id: str) -> Job | None:
    try:
        jobs = list(Job.query.filter(room_id=room_id, id=job_id))
        return jobs[0] if jobs else None
    except Exception as e:  # noqa: BLE001 — lookup must fail open
        logger.warning("[job-router] job fetch failed for %s/%s: %s", room_id, job_id, e)
        return None


# ── Bind-or-mint routing ─────────────────────────────────────────────


def _build_prompt(message_text: str, candidates: list[Job]) -> str:
    """Very simple candidate-list-plus-message prompt (spike-3 shape).

    Deliberately small so it stays viable on the local granite model — no
    reasoning chains, no examples, a strict JSON tool result.
    """
    lines = []
    for i, job in enumerate(candidates, 1):
        lines.append(f"Job {i} (ID: {job.job_id}):\n  Goal: {job.current_goal()}")
    candidates_text = "\n\n".join(lines)

    return f"""/no_think
You are a message routing classifier. A new message arrived in a chat.
Decide whether it continues one of the ongoing jobs below or starts new work.

Ongoing jobs:

{candidates_text}

New message: "{message_text[:500]}"

Return decision ("bind" to continue an existing job, "new" otherwise),
job_id (the ID string of the matched job, or null for "new"), and
confidence (0.0-1.0).

Rules:
- "bind" only on clear topical relevance to a job's goal
- If the message is a new topic, return "new" with job_id null
- NEVER match on vague similarity"""


async def route_message(
    room_id: str,
    message_text: str,
    message_key: str,
    *,
    reply_to_message_key: str | None = None,
) -> Job:
    """Bind an inbound message to a Job, or mint a NEW one. Never raises.

    Resolution order:

    1. **Idempotency** (Race 4): the message is already bound → return that
       Job. A recovery re-route after a crash-after-bind no-ops here.
    2. **Reply-to**: the parent message's binding in the permanent reply
       index — a dict lookup, no model call.
    3. **Zero candidates** → mint NEW, no model call.
    4. **Granite bind-or-mint** via ``run_typed_local`` with the strict
       :class:`JobRouteDecision` output; post-hoc ``valid_ids`` membership
       check; confidence threshold; every failure direction is NEW.

    The winning Job is revived/touched, and the message is bound via
    ``SET NX`` — if a concurrent router won the bind, the winner's Job is
    returned instead of the local pick.
    """
    # 1. Idempotency: existing binding wins outright.
    existing = lookup_job_for_message(message_key)
    if existing:
        job = _fetch_job(existing[0], existing[1])
        if job is not None:
            return job
        logger.warning(
            "[job-router] binding for %s names missing job %s; re-routing", message_key, existing
        )

    # 2. Reply-to: permanent index, no model call.
    bound_job: Job | None = None
    if reply_to_message_key:
        parent = lookup_job_for_message(reply_to_message_key)
        if parent:
            bound_job = _fetch_job(parent[0], parent[1])
            if bound_job is not None:
                logger.info(
                    "[job-router] reply-to bound %s to job %s via reply index",
                    message_key,
                    bound_job.job_id,
                )

    # 3-4. Candidates + granite verdict.
    if bound_job is None:
        candidates = Job.recent_for_room(room_id, limit=_CANDIDATE_CAP)
        if not candidates:
            logger.info("[job-router] no candidate jobs in %s; minting NEW", room_id)
        else:
            bound_job = await _classify(room_id, message_text, candidates)

    if bound_job is None:
        bound_job = Job.mint(room_id, message_text)
        logger.info(
            "[job-router] minted NEW job %s in %s (goal: %s)",
            bound_job.job_id,
            room_id,
            bound_job.current_goal(),
        )
    else:
        bound_job.revive()

    # Bind via SET NX; on a lost race, adopt the winner's binding.
    if not bind_message_to_job(message_key, bound_job.job_id, room_id=bound_job.room_id):
        winner = lookup_job_for_message(message_key)
        if winner and winner[0] != bound_job.job_id:
            winner_job = _fetch_job(winner[0], winner[1])
            if winner_job is not None:
                logger.info(
                    "[job-router] lost bind race for %s; adopting job %s", message_key, winner[0]
                )
                return winner_job
    return bound_job


async def _classify(room_id: str, message_text: str, candidates: list[Job]) -> Job | None:
    """Granite verdict over the candidate set. ``None`` means NEW (fail-open)."""
    try:
        decision = await run_typed_local(_build_prompt(message_text, candidates), JobRouteDecision)
    except Exception as e:  # noqa: BLE001 — total fail-open to NEW
        logger.warning("[job-router] classifier failed; failing open to NEW: %s", e)
        return None

    if decision.decision != "bind" or not decision.job_id:
        logger.info("[job-router] verdict NEW (confidence %.2f)", decision.confidence)
        return None

    valid_ids = {job.job_id: job for job in candidates}
    if decision.job_id not in valid_ids:
        logger.warning(
            "[job-router] verdict named id %r outside the candidate set; treating as NEW",
            decision.job_id,
        )
        return None

    if decision.confidence < JOB_ROUTER_CONFIDENCE_THRESHOLD:
        logger.info(
            "[job-router] bind confidence %.2f below threshold %.2f; treating as NEW",
            decision.confidence,
            JOB_ROUTER_CONFIDENCE_THRESHOLD,
        )
        return None

    logger.info(
        "[job-router] bound to job %s (confidence %.2f)", decision.job_id, decision.confidence
    )
    return valid_ids[decision.job_id]
