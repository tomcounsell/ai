"""CrashSignature model — Popoto-backed aggregation of crash signatures across sessions.

Each record represents a unique crash pattern (keyed by the signature hash produced
by ``agent.crash_signature.extract_signature``).  Records are upserted on every
terminal session so the library grows automatically over time.

Outcome tallies track per-recovery-strategy success rates, enabling
``is_auto_eligible`` to gate automatic resumption behind statistical confidence.

Usage::

    from models.crash_signature import CrashSignature

    record = CrashSignature.get_or_create_by_hash(sig_key.hash)
    record.upsert_occurrence(session_id, terminal_status="failed",
                              has_uuid=True, project_key="valor")
    CrashSignature.all_for_project("valor")
"""

from __future__ import annotations

import json
import logging

from popoto import Field, IndexedField, KeyField, Model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truthy(value: object) -> bool:
    """Coerce a Popoto-stored value to a strict Python bool.

    Popoto ``Field(default=False)`` round-trips through Redis as the *string*
    ``"False"`` / ``"True"``. A naive ``bool(value)`` treats both strings as
    truthy. This helper canonicalizes the common shapes.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _int_field(value: object) -> int:
    """Safely coerce a Popoto field value to int (stored as string)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

NON_RESUMABLE_DETERMINISTIC = "NON_RESUMABLE_DETERMINISTIC"


class CrashSignature(Model):
    """Aggregation record for a single crash pattern across all sessions.

    The primary key is the ``signature_hash`` (sha256[:16] of ``human_form``)
    produced by ``agent.crash_signature.extract_signature``.

    Fields:
        signature_hash: Primary key — sha256[:16] of the human_form string.
        human_form: Human-readable description of the crash pattern.
        signature_class: Broad category; ``NON_RESUMABLE_DETERMINISTIC`` for
            never-started patterns.
        resumable: False if ``NON_RESUMABLE_DETERMINISTIC``.
        escalated: True after an escalation alert has been sent.
        occurrence_count: Total number of times this pattern has been observed.
        project_key: Project partition key (IndexedField for filtering).
        outcome_tallies_json: JSON string mapping strategy name to
            ``{"attempts": N, "recovered": N, "failed": N}``.
    """

    # Primary key — sha256[:16] hash of human_form
    signature_hash = KeyField()

    # Human-readable description
    human_form = Field(null=True, default=None)

    # Classification
    signature_class = Field(null=True, default=None)
    resumable = Field(default=True)  # stored as "True"/"False" string by Popoto
    escalated = Field(default=False)  # True after escalation alert sent

    # Aggregation
    occurrence_count = Field(default=0)  # int stored as string
    project_key = IndexedField(null=True, default=None)

    # Per-strategy outcome tallies as JSON string.
    # Format: {"strategy_name": {"attempts": N, "recovered": N, "failed": N}}
    outcome_tallies_json = Field(null=True, default=None)

    class Meta:
        pass

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_non_resumable_deterministic(self) -> bool:
        """True if this signature is classified as NON_RESUMABLE_DETERMINISTIC."""
        return self.signature_class == NON_RESUMABLE_DETERMINISTIC

    @property
    def occurrence_count_int(self) -> int:
        """Return occurrence_count as a Python int."""
        return _int_field(self.occurrence_count)

    @property
    def is_resumable(self) -> bool:
        """Return resumable field as a strict Python bool."""
        return _truthy(self.resumable)

    @property
    def is_escalated(self) -> bool:
        """Return escalated field as a strict Python bool."""
        return _truthy(self.escalated)

    # ------------------------------------------------------------------
    # Outcome tallies
    # ------------------------------------------------------------------

    def _load_tallies(self) -> dict:
        """Load outcome_tallies_json as a Python dict."""
        raw = self.outcome_tallies_json
        if not raw:
            return {}
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _save_tallies(self, tallies: dict) -> None:
        """Serialize and persist outcome_tallies_json."""
        self.outcome_tallies_json = json.dumps(tallies)
        self.save()

    def record_outcome(self, strategy: str, recovered: bool) -> None:
        """Record the outcome of a recovery attempt for *strategy*.

        Increments ``attempts`` and either ``recovered`` or ``failed``
        in the strategy's tally bucket.

        Args:
            strategy: Name of the recovery strategy (e.g. ``"auto_resume"``).
            recovered: True if the session recovered successfully.
        """
        tallies = self._load_tallies()
        bucket = tallies.setdefault(strategy, {"attempts": 0, "recovered": 0, "failed": 0})
        bucket["attempts"] += 1
        if recovered:
            bucket["recovered"] += 1
        else:
            bucket["failed"] += 1
        self._save_tallies(tallies)

    def policy_confidence(self, strategy: str) -> float:
        """Return the success ratio for *strategy* (recovered / attempts).

        Returns 0.0 if no attempts have been recorded for the strategy.

        Args:
            strategy: Name of the recovery strategy.

        Returns:
            Float in [0.0, 1.0].
        """
        tallies = self._load_tallies()
        bucket = tallies.get(strategy)
        if not bucket:
            return 0.0
        attempts = int(bucket.get("attempts") or 0)
        if attempts == 0:
            return 0.0
        recovered = int(bucket.get("recovered") or 0)
        return recovered / attempts

    def is_auto_eligible(
        self,
        strategy: str = "auto_resume",
        *,
        min_occurrences: int = 3,
        min_success_ratio: float = 0.7,
    ) -> bool:
        """Return True if this signature is eligible for automatic recovery.

        Eligibility follows a **demotion-gate** model, not a promotion gate.
        The success ratio only *demotes* a signature once it has earned real
        attempt data; a signature with zero recorded attempts is "not yet
        demoted" and remains eligible (provided the structural gates pass).
        This is the bootstrap path that makes zero-human-action auto-resume
        reachable: a promotion gate would deadlock (0 attempts -> ratio 0.0 ->
        never eligible -> never resumed -> never accrues attempts).

        Eligibility logic, in order:
        - ``NON_RESUMABLE_DETERMINISTIC`` -> never eligible (determinism
          guardrail wins unconditionally).
        - Not ``is_resumable`` -> never eligible.
        - ``occurrence_count < min_occurrences`` -> not eligible (occurrence
          gate ensures the pattern is recurring before we act).
        - Zero attempts for ``strategy`` -> ELIGIBLE (bootstrap: not yet
          demoted).
        - Attempts > 0 -> eligible iff
          ``policy_confidence(strategy) >= min_success_ratio`` (a signature
          that starts failing auto-demotes itself out of eligibility).

        Args:
            strategy: Recovery strategy name to check.
            min_occurrences: Minimum observations before auto-eligibility.
            min_success_ratio: Minimum success ratio for the strategy once
                attempt data exists (demotion threshold).

        Returns:
            True if eligible under the demotion-gate model, False otherwise.
        """
        if self.is_non_resumable_deterministic:
            return False
        if not self.is_resumable:
            return False
        if self.occurrence_count_int < min_occurrences:
            return False

        attempts = int(self._load_tallies().get(strategy, {}).get("attempts") or 0)
        if attempts == 0:
            # Not yet demoted — bootstrap case. Eligible.
            return True
        return self.policy_confidence(strategy) >= min_success_ratio

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def upsert_occurrence(
        self,
        session_id: str,
        terminal_status: str,
        *,
        has_uuid: bool = True,
        project_key: str | None = None,
    ) -> None:
        """Atomically increment occurrence_count and update project_key.

        Does NOT write outcome tallies — use ``record_outcome`` separately
        after the recovery attempt resolves.

        Args:
            session_id: The session that produced this crash pattern.
            terminal_status: The terminal status of the session (e.g. "failed").
            has_uuid: Whether the session has a UUID (informational, not stored).
            project_key: Project partition key; updates the stored value if given.
        """
        current = _int_field(self.occurrence_count)
        self.occurrence_count = current + 1
        if project_key is not None:
            self.project_key = project_key
        self.save()
        logger.debug(
            "CrashSignature %s: occurrence_count=%d session=%s status=%s uuid=%s",
            self.signature_hash,
            self.occurrence_count_int,
            session_id,
            terminal_status,
            has_uuid,
        )

    # ------------------------------------------------------------------
    # Class-level lookups
    # ------------------------------------------------------------------

    @classmethod
    def get_by_hash(cls, hash_value: str) -> CrashSignature | None:
        """Look up a CrashSignature by its signature hash.

        Args:
            hash_value: The sha256[:16] hash string.

        Returns:
            The matching record, or None if not found.
        """
        results = list(cls.query.filter(signature_hash=hash_value))
        return results[0] if results else None

    @classmethod
    def get_or_create_by_hash(
        cls,
        hash_value: str,
        *,
        human_form: str | None = None,
        signature_class: str | None = None,
        resumable: bool = True,
    ) -> CrashSignature:
        """Get or create a CrashSignature by hash.

        If creating a new record, sets ``human_form``, ``signature_class``,
        and ``resumable`` from the provided arguments.

        Args:
            hash_value: The sha256[:16] hash string (primary key).
            human_form: Human-readable crash description (set on creation only).
            signature_class: Broad category (set on creation only).
            resumable: Whether recovery should be attempted (set on creation only).

        Returns:
            Existing or newly created CrashSignature record.
        """
        existing = cls.get_by_hash(hash_value)
        if existing is not None:
            return existing

        record = cls(signature_hash=hash_value)
        if human_form is not None:
            record.human_form = human_form
        if signature_class is not None:
            record.signature_class = signature_class
        record.resumable = str(resumable)
        record.occurrence_count = 0
        record.save()
        return record

    @classmethod
    def all_for_project(cls, project_key: str) -> list[CrashSignature]:
        """Return all CrashSignature records for *project_key*.

        Args:
            project_key: Project partition key.

        Returns:
            List of matching records (may be empty).
        """
        return list(cls.query.filter(project_key=project_key))
