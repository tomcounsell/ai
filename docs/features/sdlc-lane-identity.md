# SDLC Lane Identity: One Recorded Slug, Minted Once

An SDLC lane has exactly one identity: a slug. It names the lane's git branch
(`session/{slug}`), its worktree (`.worktrees/{slug}/`), and its task list. It
*usually* also names the lane's plan document — but not always, and the system
does not require it to. The slug is recorded once, on `PipelineLedger.slug`
(`agent/pipeline_ledger.py`), by `tools/lane_identity.py`. Every other
component reads it; nothing else invents it.

## The problem this closes

Before this feature, five independent call sites each guessed the lane's
identity, and guessed differently: three minted `sdlc-{N}` from the issue
number, two derived a slug from a plan document's filename. When a lane's
branch and its plan-derived guess disagreed — a human-named plan document
tracking an issue-derived lane, or vice versa — a branch probe built from the
wrong guess found nothing, and the router treated a healthy lane as an
unverified one. Issues #2735 and #2718 are the two ends of that defect.

## Who mints it

`tools/sdlc_session_ensure.py::ensure_session` is the **single minter**. It is
the one component that runs on every lane-start path — the upvote reflection
and local `/do-sdlc` alike — before any plan or any pipeline stage exists for
the issue. The call sits on the function's only entry path, above every
branch, so a single `resolve_lane_slug(issue_number, allow_heal=True)` call
covers all six of the function's return paths by construction: however
`ensure_session` decides to resolve or create the session, the lane's identity
is already recorded by the time it returns. The call is wrapped to swallow
exceptions — a Redis or git failure inside identity resolution must not turn a
successful `ensure_session` into an empty result.

## The adoption ladder

`resolve_lane_slug(issue_number, *, allow_heal=False, target_repo=None)`
(`tools/lane_identity.py`) walks four rungs, but only when the caller opts
into healing (see below) and the ledger's `slug` field is still empty:

1. **The recorded value.** If `PipelineLedger.slug` is already set, return it.
   Never re-derive over a recorded value — this rung is what makes the
   function safe to call from anywhere.
2. **The lane's PR head SHA, matched against a full `git ls-remote --heads
   origin` listing.** This rung sits *above* the direct branch probe because
   it is shape-agnostic: it recovers a lane whose branch was named by a
   supervisor or a human (`session/dev-<hash>`, `session/session-liveness-
   tick-counter`), which a fixed-shape probe would miss entirely. The match
   must be unique — zero matches (the common "merged and deleted" case) or
   multiple matches both fall through rather than guessing.
3. **A direct probe for the issue-derived branch on origin**
   (`session/sdlc-{N}`). Adopts an identity that already exists in the world
   before inventing one.
4. **Mint.** `mint_lane_slug(issue_number)` returns `f"sdlc-{issue_number}"`.
   It is the sole home of that literal in `tools/`, `agent/`, and
   `reflections/` — three independent minters used to exist and they drifted
   (#1915); now there is one.

Two rungs are deliberately **absent**:

- **No `docs/plans/` filename-stem rung.** An earlier draft of this ladder
  scanned `docs/plans/` for a `tracking:` match and adopted that plan's
  filename stem. It was removed because a plan document is not an identity —
  it is a document that *mentions* an issue. Reading its filename to name a
  lane is derivation wearing adoption's clothes, which is the precise defect
  this feature closes. Recording the result would have made it *worse* than
  the prior guessing, not better, because the write is no-overwrite and a
  wrong adoption could never be corrected.
- **No machine-local `git worktree list` rung.** A rung that reads local
  filesystem state would make two hosts reach different answers for the same
  lane. A per-host identity is not an identity.

Rungs 2 and 3 share the property that makes them safe: both **adopt an
identity that already exists in the world** — a pushed branch, a PR's head
ref — something another process already created and is already using. The
excluded rungs would each have **derived** an identity from something that
merely mentions the lane.

## `allow_heal` defaults to `False`, and that default is load-bearing

`resolve_lane_slug` is called from read paths far more often than from
lane-start paths: `stage-query --issue-number N` runs for any issue number a
router, a dashboard, or an operator names, and most of those issue numbers are
not lanes at all. If healing defaulted on, a single read of a non-lane issue
would mint and permanently record `sdlc-{N}` for it — contradicting this
feature's own thesis that a slug is minted exactly once, at lane start, by the
one component that actually starts lanes.

With `allow_heal=False` (the default), the function stops after rung 1: no
git subprocess, no write, and — critically — **no ledger creation**. A read
path can never bring a `PipelineLedger` into existence for a non-lane issue.

Exactly three callers write lane identity, and only two of them heal:

| Caller | Mechanism | Why |
|---|---|---|
| `tools/sdlc_session_ensure.py::ensure_session` | `resolve_lane_slug(N, allow_heal=True)` | The minter. Runs at lane start with no identity in hand. |
| `reflections/sdlc_upvote_lanes.py` lane pickup | `resolve_lane_slug(N, allow_heal=True, target_repo=repo)` | Lane start on the reflection path, past every gate, about to create a real branch. It also has no identity in hand — it scanned an issue, not a branch. |
| `reflections/sdlc_progress.py` stalled-lane respawn | `adopt_lane_slug(N, slug, target_repo=target_repo)` | See "Adopt vs. resolve" below — this caller already knows the identity, so it does not heal. |

## Adopt vs. resolve: the three-way rule

`tools/lane_identity.py` exposes a second write path, `adopt_lane_slug
(issue_number, slug, *, target_repo=None)`, for a caller that does not need to
*ask* what the lane's identity is because it already *knows*. The rule the
module enforces has three clauses:

- A site that must **search** for the identity may guess — it walks the
  ladder via `resolve_lane_slug(..., allow_heal=True)`.
- A site that **writes** identity may not guess at all — `allow_heal=False`
  read paths never mint or adopt.
- A site that already **knows** the identity from the world must record what
  it knows rather than re-derive it — it calls `adopt_lane_slug`.

`reflections/sdlc_progress.py`'s stalled-lane respawn is the worked example
for the third clause. Its caller reads a PR's `headRefName` directly and binds
`slug = _slug_from_branch(branch)` before the respawn call — it is holding the
lane's true, pushed branch name. Calling `resolve_lane_slug(...,
allow_heal=True)` there instead would re-derive an identity the caller already
has, and worse: the resolver's rung 3 probes only the issue-derived
`session/sdlc-{N}` shape, so for a lane whose branch is human-named (e.g.
`session/session-liveness-tick-counter`) it **misses**, falls through to the
mint rung, and records `sdlc-{N}` — a name that diverges from the real branch.
The lane would then get a respawned session under an identity nothing else
would recognize. `adopt_lane_slug` avoids this because it takes no ladder; it
just records the caller-supplied value, conditional-on-empty.

One caveat on that example, so it is not read as a live failure being fixed:
the human-named branch cannot presently reach this call site. The respawn's
candidate list is filtered by `_SDLC_BRANCH_RE` and then by
`_issue_number_from_slug`, which requires `^sdlc-(\d+)$`, so a human-named lane
branch is dropped before the rung runs. The pass-through is correct regardless
and is tested by calling `_attempt_action` directly. Whether that reflection
should admit human-named lane branches at all is an open question: widening the
filter needs another source for the issue number, because a human-named branch
does not carry one.

`adopt_lane_slug` shares the same conditional-on-empty write path, the same
`None`-repo guard, and the same `PipelineLedger.get_or_create` as the healing
arm of `resolve_lane_slug`. It walks no ladder because the caller is not
asking a question.

## The write is conditional-on-empty and takes no lease

Both write paths funnel through `_record_slug_if_empty`: re-read the ledger
immediately before writing, write only if `slug` is still blank, and persist
with `save(update_fields=["slug"])` so `stage_states_json` is never touched.
The write takes **no lease**. This is deliberate: it is an identity write, not
a stage transition, and gating it on the issue lease would reintroduce the
deadlock class #2026 closed.

A lost race is not an error — it is the expected outcome for the loser. If two
processes both try to record a slug for the same lane at the same moment, the
one that loses the (best-effort, short-TTL) write lock simply re-reads and
returns whatever the winner recorded. Both callers end up agreeing on the same
identity; neither treats the race as a failure.

## Two slugs, kept distinct

The **lane slug** and the **plan-document filename stem** are different
concepts and are not required to match. A human-named plan
(`session-liveness-tick-counter.md`) can legitimately track a lane whose
recorded identity is issue-derived (`sdlc-2716`), and the reverse is equally
valid. `tracking:` frontmatter is the only bridge between the two — it names
the issue a plan document owns, and `find_plan_path(issue_number)`
(`tools/lane_identity.py`) resolves on that one rung alone. A plan that merely
*mentions* an issue number in prose, including inside a "Not building" No-Gos
line, does not own that issue.

`tools/plan_doc_scope.py::NON_LANE_PLANS` is the durable, single-sourced
exclusion set for the handful of `docs/plans/` documents that legitimately
carry no `tracking:` line (a standing audit, a plan with a not-yet-filed
issue). It is imported by both `tests/unit/test_plan_docs.py` and the
anti-criterion that verifies the sweep, so the list cannot drift between the
two — the same replicated-value defect this feature otherwise closes.

## `_meta.slug` and `_meta.slug_source`

`tools/sdlc_stage_query.py::_compute_meta` exposes the resolved identity to
every router and dashboard consumer as `_meta["slug"]` alongside
`_meta["slug_source"]`, one of three values:

- **`recorded`** — `resolve_lane_slug(issue_number)` (heal off) returned a
  value straight from `PipelineLedger.slug`.
- **`session`** — no ledger-recorded slug exists yet, but the cold-path
  `AgentSession` fallback (reached through `_resolve_issue_record`'s
  session-record rung) carries one. This is the pre-cutover compatibility
  rung for a lane that started before this field existed.
- **`unresolved`** — neither source has a value.

The distinction an operator reads off `slug_source` is not cosmetic:
`unresolved` means a branch-existence check downstream **no-opped** because
there was nothing to probe, while `recorded` or `session` means the check
actually ran against a real name and came back clean. Collapsing those into a
single "no branch found" signal is exactly what made #2663's wedge report the
wrong root cause (oscillation, not a stale probe) — this field is what lets a
future reader tell the two apart at a glance.

## `AgentSession.slug` is a mirror, not a second source of truth

`models/agent_session.py::AgentSession.slug` continues to exist as a
convenience field for the executor code that already reads
`AgentSession.slug` to route work (worktree-stage session routing, the
message drafter, and other pre-existing readers). It is written at lane start
— `create_session(..., slug=slug)` — from the value the lane's identity
resolution already produced, and it is **never read back as authority** by
the lane-identity system itself. `PipelineLedger.slug` is the single source of
truth; `AgentSession.slug` mirrors it outward to readers that were built
before the ledger carried it. A future reader should not treat the mirror as
a place to look up or correct a lane's identity — only `PipelineLedger.slug`,
via `resolve_lane_slug`, answers that question.

## No holder, no pid, no liveness field

`PipelineLedger.slug` carries pure identity: no holder, no pid, no host, no
heartbeat, no `last_seen`. Liveness for a lane's run stays entirely with the
issue lease (#2026's design). #2446/#2451 came from spreading liveness
inference across scattered extra fields on records like this one; this
feature's field exists to answer "what is this lane called," never "is this
lane's run still alive."

## Key files

- `tools/lane_identity.py` — `mint_lane_slug`, `lane_branch_name`,
  `find_plan_path`, `resolve_lane_slug`, `adopt_lane_slug`, and the shared
  conditional-on-empty write helper.
- `agent/pipeline_ledger.py` — `PipelineLedger.slug`, the durable record.
- `tools/sdlc_session_ensure.py::ensure_session` — the minter.
- `tools/sdlc_stage_query.py::_compute_meta` — the `_meta.slug` /
  `_meta.slug_source` read consumer.
- `tools/plan_doc_scope.py` — `NON_LANE_PLANS`, the `tracking:` invariant's
  exclusion set, shared by the test and the anti-criterion that enforce it.
- `tests/unit/test_plan_docs.py` — the durable owner of the `tracking:`
  invariant.
- `reflections/sdlc_upvote_lanes.py`, `reflections/sdlc_progress.py`,
  `tools/valor_session.py::cmd_create` — the three other consumers; the first
  two are lane-start paths (heal and adopt, respectively), the third reads
  with healing off and falls back to `mint_lane_slug()` without recording,
  because a casual issue mention in an eng session's message is not a lane
  start.
