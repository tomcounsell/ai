"""MigrationPendingClear - sidecar set for the reflections-yaml migration.

Phase 2 of ``scripts/migrate_reflections_yaml.py`` walks every Reflection and
moves embedded ``run_history`` entries into ``ReflectionRun`` rows. If a
reflection is currently ``running`` at scan time, the migration cannot safely
clear its ``run_history`` mid-flight, so it records the name here. After the
scan, the migration walks this set and clears ``run_history`` only on rows
that have stopped running.

This is a Popoto-managed sidecar (not raw Redis) per the project's
no-raw-Redis-on-Popoto-keys invariant.
"""

import popoto


class MigrationPendingClear(popoto.Model):
    """One row per reflection name pending a deferred ``run_history`` clear."""

    reflection_name = popoto.KeyField(unique=True)
    recorded_at = popoto.Field(type=float, default=0.0)

    class Meta:
        ttl = 86400 * 14  # 14 days — bounded by reasonable migration window
