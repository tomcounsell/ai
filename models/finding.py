"""Finding model for cross-agent knowledge relay.

Stores structured records of sub-agent discoveries, scoped to work items
(slugs). Distinct from Memory (general observations/instructions) -- Finding
stores work-item-scoped technical discoveries that can be relayed between
sequential SDLC stages.

Partitioned by slug so findings from different work items are isolated.
DecayingSortedField ensures findings from completed/stale work items fade
naturally without explicit cleanup.
"""

import logging

from config.memory_defaults import apply_defaults

# Apply tuned defaults before model definition
apply_defaults()

from popoto import (  # noqa: E402
    AccessTrackerMixin,
    AutoKeyField,
    ConfidenceField,
    CoOccurrenceField,
    DecayingSortedField,
    FloatField,
    KeyField,
    Model,
    StringField,
    WriteFilterMixin,
)
from popoto.fields.existence_filter import ExistenceFilter  # noqa: E402

logger = logging.getLogger(__name__)

# Valid finding categories
CATEGORY_FILE_EXAMINED = "file_examined"
CATEGORY_PATTERN_FOUND = "pattern_found"
CATEGORY_DECISION_MADE = "decision_made"
CATEGORY_ARTIFACT_PRODUCED = "artifact_produced"
CATEGORY_DEPENDENCY_DISCOVERED = "dependency_discovered"

VALID_CATEGORIES = frozenset(
    {
        CATEGORY_FILE_EXAMINED,
        CATEGORY_PATTERN_FOUND,
        CATEGORY_DECISION_MADE,
        CATEGORY_ARTIFACT_PRODUCED,
        CATEGORY_DEPENDENCY_DISCOVERED,
    }
)


class Finding(WriteFilterMixin, AccessTrackerMixin, Model):
    """Cross-agent finding record.

    Stores technical discoveries from sub-agent work, scoped to a slug.
    Automatically decays over time, with importance-weighted persistence.

    Fields:
        finding_id: Auto-generated unique key.
        slug: Work item scope (e.g., "cross-agent-knowledge-relay").
        project_key: Project partition key.
        session_id: Which DevSession produced this finding.
        stage: SDLC stage (BUILD, TEST, REVIEW, etc.).
        category: Finding type (file_examined, pattern_found, etc.).
        content: The finding text (max ~500 chars).
        file_paths: Comma-separated file paths for path-based queries.
        importance: Numeric importance score (1.0-10.0).
        relevance: Decay-sorted index, partitioned by slug.
        confidence: Bayesian confidence, updated on dedup reinforcement.
        bloom: ExistenceFilter for O(1) topic pre-checks.
        associations: CoOccurrenceField linking related findings.
    """

    finding_id = AutoKeyField()
    slug = KeyField()
    project_key = KeyField()
    session_id = KeyField()
    stage = StringField(default="")
    category = StringField(default="")
    content = StringField(default="")
    file_paths = StringField(default="")
    importance = FloatField(default=3.0)

    relevance = DecayingSortedField(
        base_score_field="importance",
        partition_by="slug",
    )
    confidence = ConfidenceField(initial_confidence=0.5)
    bloom = ExistenceFilter(
        error_rate=0.01,
        capacity=50_000,
        fingerprint_fn=lambda inst: inst.content or "",
    )
    associations = CoOccurrenceField(partition_by="slug")

    # WriteFilterMixin thresholds
    _wf_min_threshold = 0.15
    _wf_priority_threshold = 0.7

    def compute_filter_score(self):
        """Score used by WriteFilterMixin to gate persistence.

        Returns the importance value. Records below _wf_min_threshold
        are silently dropped on save().
        """
        return self.importance or 0.0

    @classmethod
    def safe_save(cls, **kwargs) -> "Finding | None":
        """Save a finding record with full error handling.

        Returns the saved Finding instance, or None if save failed or was
        filtered out by WriteFilterMixin.

        This is the recommended entry point for all finding creation.
        Never raises -- all exceptions are caught and logged.
        """
        try:
            f = cls(**kwargs)
            result = f.save()
            if result is False:
                logger.debug(
                    f"Finding filtered out by WriteFilter "
                    f"(importance={kwargs.get('importance', 3.0)})"
                )
                return None
            logger.debug(
                f"Finding saved: slug={kwargs.get('slug', 'unknown')}, "
                f"category={kwargs.get('category', 'unknown')}"
            )
            return f
        except Exception as e:
            logger.warning(f"Finding save failed (non-fatal): {e}")
            return None

    @classmethod
    def query_by_slug(cls, slug: str, limit: int = 20) -> list["Finding"]:
        """Query findings for a given slug.

        Returns findings sorted by relevance (DecayingSortedField).
        Never raises -- returns empty list on error.
        """
        try:
            results = list(cls.query.filter(slug=slug))
            return results[:limit]
        except Exception as e:
            logger.warning(f"Finding query failed (non-fatal): {e}")
            return []
