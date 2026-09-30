# Rulesets

`main.json` is the ruleset for `main`: no branch deletion, no force push
(history rewrite). Direct pushes are allowed; no pull request and no review
are required. Re-applied on 2026-09-20, replacing the earlier "trust
boundary" ruleset (23693268, 2026-09-19), which required a pull request
with one CODEOWNER approval and the `check` status. That review requirement
deadlocked in practice: GitHub refuses self-approval, and there is one
human, so every pull request needed an admin bypass to merge (prereqs.md
finding 7). Removed by request rather than worked around.

The `check` CI job still runs on every push to `main` (`.github/workflows/ci.yml`)
and on every pull request; it is informational only now, not a merge gate.

Re-apply after editing the file:

    gh api -X POST repos/tomcounsell/cori/rulesets --input .github/rulesets/main.json
