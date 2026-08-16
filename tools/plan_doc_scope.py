"""Which `docs/plans/` documents are expected to name a tracking issue.

A plan document declares the issue it owns in `tracking:` frontmatter. That
declaration is the sole way a lane resolves its plan: ownership is *proven* by
the frontmatter rather than guessed from a filename or from a bare `#N` mention
somewhere in the prose (a mention that, in a "Not building" No-Gos line, means
the opposite of ownership).

A few documents under `docs/plans/` are not lane plans and legitimately have no
tracking issue. They live in one frozenset here, imported by both the test that
enforces the invariant and the anti-criterion that verifies it, so the exclusion
list cannot drift between the two -- the replicated-value defect this module's
own feature exists to remove.
"""

from __future__ import annotations

NON_LANE_PLANS: frozenset[str] = frozenset(
    {
        # A standing audit of session-recovery observations, not a unit of work.
        # Its disposition is a pending human decision, which must not gate any
        # lane, so it is excluded by name rather than moved.
        "session-recovery-observation-audit.md",
        # A three-tier simplification direction whose tracking issue is not yet
        # filed; the placeholder `tracking: none yet` is deliberately not a
        # resolvable reference.
        "resilience-simplification-three-tier.md",
    }
)
