#!/usr/bin/env bash
# Guard against NEW uninterlocked popoto rebuild_indexes() call sites (issue #2536).
#
# The seam interlock in config/popoto_floor.py wraps popoto's
# Model.rebuild_indexes, so every caller is covered automatically. This check
# exists so that growth in the caller set is a deliberate, reviewed act rather
# than something that drifts in unnoticed -- a new caller reached by a path that
# never imports `models` would bypass the seam.
#
# Contract: the baseline below is an explicit FILE LIST, never a bare zero-match
# grep. A file DISAPPEARING from the caller set is fine and must not fail (issue
# #2524 removes two of these). Only ADDITIONS fail.
#
# Note the quoted --include="*.py". An unquoted glob makes zsh abort the command
# and return exit 1, which reads as a pass -- that exact mistake produced this
# plan's original mis-count, so it must not be repeated in the check that guards
# against it.

set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

# Baseline: files invoking rebuild_indexes() as of issue #2536's investigation.
#
# models/session_enumeration.py is a docstring-only mention (":187", prose
# recommending rebuild_indexes()), not a call. It matches the superset pattern
# and is baselined for that reason.
BASELINE="models/agent_session.py
models/session_enumeration.py
scripts/merge_dev_chat_into_eng.py
scripts/migrate_agent_session_keyfield_rename.py
scripts/migrate_parent_session_field.py
scripts/migrate_schema_diet_fields.py
scripts/migrate_session_type_chat_to_pm.py
scripts/migrate_session_type_pm_to_eng.py
scripts/migrate_strip_pty_fields.py
scripts/migrate_unify_parent_session_field.py
scripts/popoto_index_cleanup.py"

# Real call statements only -- `name.rebuild_indexes()`. Prose mentions inside
# docstrings match too, which is acceptable: this check is a superset guard.
CURRENT=$(grep -rlE "[A-Za-z_][A-Za-z0-9_]*\.rebuild_indexes\(\)" \
  --include="*.py" \
  models/ scripts/ tools/ agent/ bridge/ worker/ 2>/dev/null | sort -u)

NEW=$(comm -23 <(printf '%s\n' "$CURRENT") <(printf '%s\n' "$BASELINE" | sort -u))

if [ -n "$NEW" ]; then
  echo "FAIL: new uninterlocked rebuild_indexes() caller(s) beyond the #2536 baseline:"
  printf '  %s\n' $NEW
  echo
  echo "Every caller must reach popoto through the seam interlock installed in"
  echo "models/__init__.py. If this caller is legitimate and does import models,"
  echo "add it to BASELINE in $0 with a note explaining why."
  exit 1
fi

echo "OK: no new rebuild_indexes() callers beyond the #2536 baseline."
exit 0
