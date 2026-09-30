"""ReflectionIgnore model - auto-fix suppression with TTL-based auto-expiry.

See docs/features/reflections.md for full documentation.
"""

import time

from popoto import (
    AutoKeyField,
    Field,
    KeyField,
    Model,
    SortedField,
)


class ReflectionIgnore(Model):
    """An ignored bug pattern with automatic TTL-based expiry.

    The expires_at SortedField enables efficient range queries to
    find/prune expired entries.
    """

    ignore_id = AutoKeyField()
    pattern = KeyField()  # pattern string to match against
    reason = Field(null=True)
    created_at = SortedField(type=float)
    expires_at = SortedField(type=float)  # Unix timestamp when this expires

    @classmethod
    def add_ignore(cls, pattern: str, reason: str = "", days: int = 14) -> "ReflectionIgnore":
        """Add a new ignore entry that expires after `days` days."""
        now = time.time()
        return cls.create(
            pattern=pattern,
            reason=reason,
            created_at=now,
            expires_at=now + (days * 86400),
        )

    @classmethod
    def get_active(cls) -> list["ReflectionIgnore"]:
        """Return all non-expired ignore entries."""
        now = time.time()
        all_entries = cls.query.all()
        return [e for e in all_entries if e.expires_at and e.expires_at > now]

    @classmethod
    def cleanup_expired(cls) -> int:
        """Delete expired ignore entries. Returns count deleted."""
        now = time.time()
        all_entries = cls.query.all()
        deleted = 0
        for entry in all_entries:
            if entry.expires_at and entry.expires_at <= now:
                entry.delete()
                deleted += 1
        return deleted

    @classmethod
    def is_ignored(cls, pattern: str) -> bool:
        """Check if a pattern matches any active ignore entry (case-insensitive)."""
        pattern_lower = pattern.lower()
        for entry in cls.get_active():
            entry_pattern = (entry.pattern or "").lower()
            if entry_pattern and (entry_pattern in pattern_lower or pattern_lower in entry_pattern):
                return True
        return False
