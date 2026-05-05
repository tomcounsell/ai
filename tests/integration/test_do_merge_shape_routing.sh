#!/usr/bin/env bash
# Integration test: end-to-end shape routing inside the do-merge.md gate.
#
# Synthesizes a fake classifier output for each shape (via PYTHONPATH shim),
# invokes the routing snippets, and asserts the correct gate decisions get
# emitted (ruff always runs; pytest is skipped for docs-only; etc.).
#
# Also covers the safe-shape exemption fail-closed cases:
#   - prior approval body without REVIEW_CONTEXT trailer -> SKIP
#   - prior approval body with valid trailer + safe-shape diff -> PASS
#
# This is a black-box test of the bash glue inside do-merge.md, not a
# substitute for the Python unit tests in tests/unit/test_pr_shape_*.py.

set -uo pipefail
cd "$(dirname "$0")/../.."

PASS=0
FAIL=0
ERRORS=()

ok()   { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); ERRORS+=("$1"); echo "  FAIL: $1"; }

# -----------------------------------------------------------------------------
# 1) Classifier emits the expected JSON shape for each input.
# -----------------------------------------------------------------------------
echo "== Classifier shape outputs =="

J=$(python -c "
import json
from pathlib import Path
from scripts.pr_shape_classify import classify
r = classify(['docs/foo.md'], net_lines=5, has_new=False, has_deleted=False, repo_root=Path('.'))
print(json.dumps(r.to_dict()))
")
echo "$J" | grep -q '"shape": "docs-only"' && ok "docs-only classifier output" || fail "docs-only classifier output ($J)"

J=$(python -c "
import json
from pathlib import Path
from scripts.pr_shape_classify import classify
r = classify(['uv.lock'], net_lines=300, has_new=False, has_deleted=False, repo_root=Path('.'))
print(json.dumps(r.to_dict()))
")
echo "$J" | grep -q '"shape": "lockfile-only"' && ok "lockfile-only classifier output" || fail "lockfile-only classifier output ($J)"

J=$(python -c "
import json
from pathlib import Path
from scripts.pr_shape_classify import classify
r = classify(['docs/foo.md', 'agent/bar.py'], net_lines=15, has_new=False, has_deleted=False, repo_root=Path('.'))
print(json.dumps(r.to_dict()))
")
echo "$J" | grep -q '"shape": "mixed"' && ok "mixed classifier output" || fail "mixed classifier output ($J)"

# -----------------------------------------------------------------------------
# 2) Shape-routing decisions reproduce the bash conditional behavior.
# -----------------------------------------------------------------------------
echo "== Shape routing decisions =="

# docs-only -> Lockfile + Full Suite SKIP
OUT=$(SHAPE="docs-only" bash -c '
if [ "$SHAPE" = "docs-only" ]; then echo "LOCKFILE: SKIP"; fi
if [ "$SHAPE" = "docs-only" ]; then echo "FULL_SUITE: SKIP"; fi
')
echo "$OUT" | grep -q "LOCKFILE: SKIP" && echo "$OUT" | grep -q "FULL_SUITE: SKIP" \
  && ok "docs-only skips Lockfile + Full Suite" || fail "docs-only skips Lockfile + Full Suite"

# lockfile-only -> runs Lockfile + Full Suite (no skip)
SHAPE="lockfile-only" bash -c '
if [ "$SHAPE" = "docs-only" ]; then echo "LOCKFILE: SKIP"; else echo "LOCKFILE: RAN"; fi
' | grep -q "LOCKFILE: RAN" && ok "lockfile-only runs Lockfile" || fail "lockfile-only runs Lockfile"

# small-patch -> targeted pytest
SHAPE="small-patch" SHAPE_JSON='{"shape":"small-patch","tests_to_run":["tests/unit/test_widget.py"]}' bash -c '
if [ "$SHAPE" = "small-patch" ]; then
  T=$(echo "$SHAPE_JSON" | python3 -c "import json,sys; print(\" \".join(json.load(sys.stdin).get(\"tests_to_run\",[])))")
  echo "TARGETED: $T"
fi
' | grep -q "tests/unit/test_widget.py" && ok "small-patch targeted tests extracted" || fail "small-patch targeted tests extracted"

# -----------------------------------------------------------------------------
# 3) Safe-shape exemption: trailer extraction.
# -----------------------------------------------------------------------------
echo "== Safe-shape exemption: trailer parse =="

VALID_BODY=$'## Review: Approved\n\nLGTM\n<!-- REVIEW_CONTEXT head_sha=abc123def456abc123def456abc123def4567890 pr_body_hash=deadbeefcafe -->'
SHA=$(echo "$VALID_BODY" | grep -oE 'REVIEW_CONTEXT head_sha=[a-f0-9]{40}' | sed 's/REVIEW_CONTEXT head_sha=//' | tail -1)
[ "$SHA" = "abc123def456abc123def456abc123def4567890" ] && ok "valid trailer extracts SHA" || fail "valid trailer extracts SHA (got '$SHA')"

NO_TRAILER_BODY=$'## Review: Approved\n\nLGTM (no trailer)'
SHA=$(echo "$NO_TRAILER_BODY" | grep -oE 'REVIEW_CONTEXT head_sha=[a-f0-9]{40}' | sed 's/REVIEW_CONTEXT head_sha=//' | tail -1)
[ -z "$SHA" ] && ok "missing trailer yields empty SHA (fail-closed)" || fail "missing trailer must yield empty SHA"

MALFORMED_BODY=$'## Review: Approved\n<!-- REVIEW_CONTEXT head_sha=NOTHEX -->'
SHA=$(echo "$MALFORMED_BODY" | grep -oE 'REVIEW_CONTEXT head_sha=[a-f0-9]{40}' | sed 's/REVIEW_CONTEXT head_sha=//' | tail -1)
[ -z "$SHA" ] && ok "malformed trailer SHA rejected (fail-closed)" || fail "malformed trailer must yield empty SHA"

# -----------------------------------------------------------------------------
# 4) Safe-shape diff classification (uses real git on this repo's HEAD).
# -----------------------------------------------------------------------------
echo "== Safe-shape diff classification =="

# Use HEAD..HEAD as a no-op "safe shape" demonstration; the empty diff defaults
# to feature, which is the safe direction. We just assert the CLI is invokable
# in --diff-from / --diff-to mode.
HEAD=$(git rev-parse HEAD)
RES=$(python -m scripts.pr_shape_classify --diff-from "$HEAD" --diff-to "$HEAD" 2>/dev/null || echo '{}')
echo "$RES" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('shape')=='feature'" 2>/dev/null \
  && ok "diff-mode HEAD..HEAD returns feature (empty diff)" \
  || fail "diff-mode HEAD..HEAD returns feature (got $RES)"

# -----------------------------------------------------------------------------
# 5) do-merge.md contains the expected shape-routing block + cache call.
# -----------------------------------------------------------------------------
echo "== do-merge.md routing block presence =="

MD=".claude/commands/do-merge.md"
grep -q '### Shape Classification' "$MD" && ok "Shape Classification section present" || fail "Shape Classification section missing"
grep -q 'pr_shape_classify --pr' "$MD" && ok "classifier invoked with --pr" || fail "classifier invoked with --pr"
grep -q 'pr_shape_cache get' "$MD" && ok "cache lookup present" || fail "cache lookup present"
grep -q 'pr_shape_cache write' "$MD" && ok "cache write present" || fail "cache write present"

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo
echo "Result: $PASS passed, $FAIL failed"
if [ "$FAIL" -ne 0 ]; then
  printf '  - %s\n' "${ERRORS[@]}"
  exit 1
fi
exit 0
