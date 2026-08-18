# Docs Auditor

Unified documentation hygiene substrate that consolidates five disjointed pieces — `feature-docs-audit`, `documentation-audit`, `knowledge-reindex`, `/do-xref-audit`, `/do-docs-audit` — into one module (`reflections/docs_auditor.py`) consumed by two callers: a daily rotation reflection and the `/do-docs` SDLC stage.

## What It Replaces

| Old piece | Type | Status |
|-----------|------|--------|
| `feature-docs-audit` | reflection (daily) | deleted |
| `documentation-audit` | reflection (daily) | deleted |
| `knowledge-reindex` | reflection (daily) | deleted (was broken) |
| `/do-xref-audit` | manual skill | deleted (no replacement) |
| `/do-docs-audit` | manual skill | deleted |
| `scripts/docs_auditor.py` | standalone script | deleted |

## Two Callers

```
                          reflections/docs_auditor.py
                          ┌────────────────────────────┐
docs-auditor (daily) ────►│ run_docs_auditor()         │──► docs-audit/{slug}-{ts} branch + PR
                          │   ↓                        │
                          │ audit(scope_mode='rotation')│
                          └────────────────────────────┘

                          ┌────────────────────────────┐
/do-docs (SDLC stage) ───►│ audit(scope_mode=          │──► commit on current branch
                          │   'pr-changed-files')      │
                          └────────────────────────────┘
```

### Caller A — `docs-auditor` daily rotation reflection

Picks the least-recently-audited primary doc from a Redis hash, expands its
neighborhood (≤20 files via outbound links + inbound refs), runs auto-fix
detectors, applies them, opens a `docs-audit/{slug}-{ts}` branch, posts a
non-draft PR, and notifies the `Dev: Valor` Telegram chat. Errors are
swallowed; the auditor never crashes the worker.

### Caller B — `/do-docs` SDLC stage

The `/do-docs` skill (`.claude/skills/do-docs/SKILL.md`) calls the substrate
with `scope_mode="pr-changed-files"` after sub-agents A–D finish discovery.
The substrate applies auto-fixes to the working tree, commits to the **current
branch** (no new branch), and fires the memory-refresh hook. The skill then
writes the SDLC stage marker and updates the plan frontmatter.

## Detectors

### Auto-fix (safe transforms)

| Detector | What it catches | Mechanism |
|----------|-----------------|-----------|
| Stale-term dictionary | `SessionLog`, `RedisJob`, `session_log`, `redis_job` | `STALE_TERMS` dict at module top, matched on word boundaries |

The dictionary's current entries are model and field renames: `SessionLog` and
`RedisJob` were renamed to AgentSession; the `session_log` and `redis_job`
fields were renamed to agent_session. Stating the mapping in that form is also
what keeps this page immune to its own detector — see the migration-context
hatch (gate 1) below.

### Four gates guard every rewrite

The auditor is an unreviewed autonomous committer: the rotation caller opens a
PR the branch sweeper can auto-merge with nobody in between. That makes its two
failure modes asymmetric. A stale term that survives is a cosmetic miss. A
sentence the auditor falsifies is a corruption a human later trusts. Every gate
below therefore errs toward **not** rewriting.

Four fail-closed gates sit between a proposed rewrite and the disk, in order:

| # | Gate | Where it runs |
|---|------|---------------|
| 1 | Migration-context hatch | `_detect_stale_term_fixes` (detection) |
| 2 | Fence / heading / deletion-prose suppression | `_apply_fixes_to_file` (apply) |
| 3 | Path-token suppression | `_apply_fixes_to_file` (apply) |
| 4 | Existence invariant | `_apply_fixes_to_file` (apply) |

Gates 1 to 3 govern stale-term rewrites. Gate 4 governs every auto-fix from
every detector.

Over the three docs the auditor previously corrupted, false rewrites went from
**18 occurrences to 1**. Across all of `docs/**/*.md` outside `docs/plans/`,
exactly one stale-term proposal survives — the accepted quoted-transcript
residual at `docs/guides/summarizer-output-audit.md:68`, which the plan predicted
and accepted; the four docs that still contain a `STALE_TERMS` key are all
genuine migration or audit prose where any rewrite would produce a false
sentence. The detector is quiet, not dead: a synthetic
sentence carrying a stale term and no migration context still queues a fix, and
a test pins that.

#### Stale terms are word-anchored

A `STALE_TERMS` key is matched with `\b` anchors, so it never matches inside a
longer run of word characters: `session_log` does not match inside
`agent/session_logs.py` or `session_log_writer`, and `SessionLog` does not match
inside `SessionLogs`.

Word-anchoring on its own does not deliver "never rewrites a path". `/`, `.`,
and `-` are all word boundaries, so a key equal to a whole path segment still
matches. Gate 3 is what closes that.

Detection and application share one matching semantics: `_detect_stale_term_fixes`
returns compiled `(pattern, replacement)` pairs on the `regex_fixes` channel,
applied by `_apply_fixes_to_file` through `pattern.subn()`. It is the **only**
fix channel `_apply_fixes_to_file` takes — there is no literal `fixes`
parameter, and no `new == ""` whole-line-delete sentinel.

#### Gate 1: the migration-context hatch is document-scoped

`_has_migration_context` asks whether the **whole document** records the
`old_term` → `new_term` migration, over text run through `_normalize_prose`
(backticks stripped, whitespace collapsed, lowercased). That normalization is
used for cue matching only and never to produce output; it is lossy by design.
It is also the point: the corpus writes every identifier backticked, so a bare
substring test for `formerly RedisJob` never sees the prose humans actually
wrote.

Two tiers of cue:

1. **Directed cues**, generated per `(old_term, new_term)` pair by
   `_migration_cues`: `renamed to X`, `replaced by X`, `now X`, `formerly Y`,
   `replaces Y`, `replacing Y`, `earlier Y`, `old Y`, `alias`, `Y = X`,
   `Y -> X`, `Y → X`. Each names both (or the specific) terms, so it is
   conclusive alone. Generating them from the pair means adding a `STALE_TERMS`
   entry costs no edit here.
2. **Generic cue words** (`_MIGRATION_CUE_WORDS`: `renamed`, `replaced`,
   `replacing`, `formerly`, `earlier`, `old`, `alias`, `superseded`, …), matched
   **word-anchored** through the precompiled `_MIGRATION_CUE_WORD_RE`
   alternation, that fire **only when the new term also appears somewhere in the
   document**. Tier 2 is what catches real corpus phrasing whose cue and term sit
   in different clauses, such as *"replacing both the earlier `SessionLog` and
   `RedisJob` models"* for `RedisJob`, which no directed cue names.

   **Both halves of tier 2 are the anti-over-exemption guard, and both are
   load-bearing.** Requiring the new term means prose that mentions a stale name
   without ever naming its successor is not a migration record and still queues a
   fix. Word-anchoring the cue words means a cue cannot fire from inside an
   unrelated word: `old` is a substring of *threshold*, *holds*, *placeholder*,
   *bold* and *household*, all common in this corpus, and with a bare substring
   test tier 2 collapses into "the document mentions the new term somewhere".
   That was not hypothetical — `docs/guides/summarizer-output-audit.md` was
   exempted for `RedisJob` solely because it contains `summarize_threshold`.
   `TestStaleTermDictionary::test_cue_word_inside_a_larger_word_does_not_exempt`
   pins it.

**The scope is the document, deliberately.** Line-scoping (one exemption
decision per occurrence) was measured and is strictly worse: it re-exposes 8
occurrences the document scope correctly exempts, because in real prose the
migration context sits in a *different* sentence from the term it explains. Do
not "improve" this into a per-occurrence rule.

#### Gates 2 and 3 are computed at apply time, never at detection time

`_detect_stale_term_fixes` returns `(compiled pattern, replacement string)` and
nothing else; the channel contract is unchanged. The suppression callable is
built inside `_apply_fixes_to_file`'s regex loop by `_make_stale_term_replacer`,
which wraps that plain string.

That placement is load-bearing, not stylistic. `_apply_fixes_to_file`'s regex
loop applies its fixes in sequence over the same `new_text`, mutating it after
each one, so any position computed once against the pre-loop `content` goes stale
the moment an earlier fix changes what precedes it — and it fails **silently**: a
plausible-looking wrong rewrite rather than an error. The callable instead
re-derives context from `match.string` (the text currently being rewritten) and
`match.start()` (the live offset into it), so there is no stored position to go
stale.

The two gates differ in how urgently they need that placement, and the difference
is worth stating precisely.

**Gate 2's placement is not load-bearing today.** The gate itself fires on every
run — it is what suppresses stale terms inside fenced code blocks — and only its
apply-time *placement* is currently unexercised, because nothing today can stale
the position it consumes. `_is_documented_deletion` takes a *line index*, derived
as `text.count("\n", 0, match.start())`, so only a substitution that changes the
*newline* count can stale it. `_detect_stale_term_fixes` is the
sole fix producer and every `STALE_TERMS` replacement is newline-free
(`SessionLog`->`AgentSession`, `RedisJob`->`AgentSession`,
`session_log`->`agent_session`, `redis_job`->`agent_session`), so applying one
shifts byte offsets but never line indices. Gate 2's apply-time placement is
forward-looking robustness, for a `STALE_TERMS` entry an operator may add later
whose key or replacement spans a newline — the only shape that can change the
newline count ahead of a later match.

**Gate 3 has no such reprieve — hoisting it would corrupt docs today.**
`_match_inside_path_token` consumes raw byte offsets (`match.start()`,
`match.end()`) rather than a line index, and the apply loop reassigns
`new_text = candidate` after each fix. Every shipped replacement is strictly
*longer* than its key, so each applied fix shifts the offsets of every later
match. Length and newline-freeness are orthogonal properties, not cause and
effect: newline-freeness is what spares gate 2, strictly-longer is what dooms
gate 3, and an equal-length newline-free replacement would spare both.
Concretely: a doc containing the prose word `SessionLog` and the path token
`models/session_log.py` queues fixes in `STALE_TERMS` order, so the `SessionLog`
fix lengthens the text ahead of the `session_log` match; a detection-time offset
map would mis-locate that match, gate 3 would fail to suppress, and
`models/session_log.py` would be rewritten to `models/agent_session.py` — the
corruption described below that the existence invariant provably cannot catch,
because both files exist. Do not hoist the path-token decision to detection time.

Keeping the callable local also keeps `_reject` receiving the replacement
*string*, so a withheld record stays readable in the PR body, the findings
summary, and the warning log instead of rendering as `<function ... at 0x...>`.

**Gate 2** reuses `_build_line_context` / `_is_documented_deletion`, the same
machinery the deleted-target detector uses (described below): fenced code
blocks, deletion or migration headings, and deletion prose on an adjacent line.

**Gate 3** suppresses any match lying wholly inside a `_PATH_TOKEN_RE` token
(`(?:[\w.-]+/)*[\w.-]+\.(?:py|md)`), scanned within the match's own line so the
cost is independent of document size. This is the gate that stops
`models/session_log.py` from becoming `models/agent_session.py`, a corruption
the existence invariant provably cannot catch, because both files exist.

A suppressed match returns unchanged, but `subn` still counts it as a
substitution. Suppressions are therefore tallied in a closure cell and
subtracted from the reported `applied` count, and an all-suppressed fix is
skipped outright rather than booked as a no-op write.

#### Gate 4: the existence invariant

Every auto-fix, regardless of detector, is subject to one post-condition before
anything is written: **the auditor may not introduce a `.py`/`.md` reference
that is absent from the working tree.**

`_PATH_REF_RE` is `(?:[\w.-]+/)*[\w.-]+\.(?:py|md)`. The directory segment is
optional, so bare filenames (`agent_session.py`, `README.md`) are visible to the
invariant alongside `dir/file.md` forms. A bare name existing nowhere is the
#2711 corruption shape minus its directory prefix. Two forms are still
deliberately **not** covered: other extensions (`.sh`, `.ts`, `.json`), and the
dotted module form (`bridge.session_log`). A fix that introduces only those
passes the invariant with `withheld=0`.

`_apply_fixes_to_file` simulates each fix, collects the references the candidate
text would newly introduce, and hands them to `_absent_new_path_refs` together
with the doc's own path. If any is absent, that fix is rejected.

**Resolution order.** A ref containing `/` resolves against `repo_root` and
nothing about it changed. A **bare** ref resolves first against the doc's own
directory (`repo_root / doc_path.parent / ref`), because a bare name in prose is
most often a sibling, and then against a basename index built from
`git ls-files --cached --others --exclude-standard`. That is one subprocess, it
respects `.gitignore` for free, and it counts files added but not yet committed,
which matters because a doc may reference a file added in the same change. The
index is memoized per resolved repo root and cleared at the top of every
`audit()` run, so a long-lived process never answers from a pre-commit snapshot.
On subprocess failure it logs a warning and degrades to doc-relative resolution
alone.

**Ambiguity is not a withhold.** `>=1` owner means the name denotes something
real and the fix passes; `>1` owners is DEBUG-logged and allowed through; only
`0` owners is absent. The invariant's question is "does this name denote
something real", not "is it unambiguous". Ambiguity never produced the #2711
corruption. A name existing *nowhere* did. The corpus carries 218 ambiguous
refs across 108 multi-owner basenames, so withholding on ambiguity would fire
constantly for no safety gain.

Making the directory segment optional costs nothing on already-written prose.
`_absent_new_path_refs` validates only `findall(candidate) - original_refs`, and
`original_refs` is computed with the same pattern, so both sides widen
symmetrically and pre-existing references are never re-validated. Measured
self-baselining over `docs/features/*.md` (narrow `+` arm versus shipped `*` arm
in one run), the widening produces **zero** new withholds.

#### Why the detector pattern stays narrower

`_detect_deleted_target_issues` carries its own `` `((?:[\w.-]+/)+[\w.-]+\.py)` ``
pattern that still *requires* a directory segment. The asymmetry with
`_PATH_REF_RE` is deliberate, and a comment at the site records why.

`_detect_deleted_target_issues` is a **detector** pattern, governing what the
auditor *reports*. `_PATH_REF_RE` is the only one of the two on the write path.
Widening the detector enlarges the candidate set and spends the per-run issue
cap on bare names that are not resolvable to a single path anyway. That is a
scope and volume change with its own failure modes, not a safety fix.

- **Attributed per term, not per occurrence.** Only the offending fix is
  dropped; valid sibling fixes in the same file still apply, and the file is
  never abandoned wholesale. But a fix is one `(old, new)` pair applied to the
  whole document — `str.replace` and `pattern.subn()` both rewrite *every*
  occurrence — so a single absent-target hit withholds that term's legitimate
  prose hits in the same file too. Path-shaped occurrences no longer reach this
  gate at all: gate 3 suppresses them first.
- **References already present in the document are never re-validated.** The
  invariant constrains what the auditor *adds*, so it cannot reject a fix over
  pre-existing prose that names a long-gone module. The residual hole: if a
  doc already mentions an absent path somewhere, that path is in the original
  reference set, so a fix that rewrites a *valid* path into that same absent
  one passes the invariant. No live instance is known, and there is no known
  reachable route to it: the sole detector left on the write path
  (`_detect_stale_term_fixes`) proposes replacements from a fixed, curated
  dictionary, not from a query that could ever select an absent path as a
  rewrite target.
- **Rejections are reported, not silently discarded.** Each one logs a warning
  naming the offending path and lands in the result contract.
- **An all-rejected run writes nothing** — no file write, therefore no empty
  commit.

Rejected fixes surface on the `audit()` result:

| Result key | Type | Meaning |
|---|---|---|
| `fixes_withheld` | `int` | count of fixes rejected by the existence invariant |
| `withheld` | `list[dict]` | one entry per rejection: `{"doc", "old", "new", "reason"}`, `reason` = `"target-absent"` |

`old` is always the **regex source** of the withheld stale-term fix
(`\bsession_log\b`, not `session_log`) — there is no other channel left to
emit a literal string. A caller that echoes `old` verbatim —
`.claude/skill-context/do-docs.md` does — will show the pattern, which is the
accurate thing to show, since that is what was withheld.

The rotation caller (`run_docs_auditor`) is the one path with no human review —
it opens a PR the branch sweeper can auto-merge — so it threads the withheld set
into its `findings`, its `summary`, and its Telegram message, and stamps
`WITHHELD_PR_MARKER` plus the rejection list into the PR body.
`_pr_is_auto_merge_eligible` refuses any PR carrying that marker, so a run that
withheld a fix always requires a human merge. It also passes the count to
`_write_liveness` as a keyword `fixes_withheld`, emitted into the Redis summary
only when non-zero. That last surface is the load-bearing one: the reflection
scheduler consumes only `projects` from a function reflection's return, so
`findings` and `summary` reach no human, and without the liveness field a
withheld run would be byte-identical to a clean one in Redis.

Because the marker disqualification is permanent, a withheld PR nobody reviews
reaches the sweeper's stale-close at `STALE_PR_AGE_DAYS` and is closed with
`--delete-branch`, discarding the fixes that did pass the invariant; the next
rotation onto that slug re-proposes and re-opens the same PR. This is strictly
safer than auto-merging suspect output, and the escalation (exempt from
stale-close, or notify before closing) is an open gap tracked by
[#2729](https://github.com/tomcounsell/ai/issues/2729), not a shipped behavior.

`status` stays `"ok"` — a withheld fix is not an error. That is precisely why a
caller must branch on `fixes_withheld > 0` rather than treat `"ok"` as "output
is correct"; `.claude/skill-context/do-docs.md` wires that branch for the
cascade.

### File-as-issue (judgment required)

| Detector | What it catches | Action |
|----------|-----------------|--------|
| Deleted target | `` `path.py` `` references to a target absent from the working tree, after placeholder / fenced-block / deletion-heading filtering | `gh issue create` (deduped) |
| Stub doc | Docs with <5 content lines | `gh issue create` (deduped) |
| Orphan plan | `docs/plans/*.md` lacking a tracking-issue link | `gh issue create` (deduped) |

#### Deleted-target false-positive filtering

The deleted-target detector runs three suppression passes before emitting a
finding, so it never floods the tracker with illustrative or
intentionally-documented paths:

- **Placeholder / example paths** (`_is_placeholder_path`): a path is skipped if
  any component (or the final file's stem) is a well-known stand-in — `foo`,
  `bar`, `baz`, `qux`, `quux`, `example`, `your-module`, `mymodule`, `sample` —
  or a single-letter directory. This suppresses paths like `foo/bar.py` and
  `agent/docs_handler/foo.py`.
- **Fenced code blocks** (`_build_line_context`): a single line-scan over the
  doc tracks fenced ```` ``` ```` block state. Matches inside a fenced block are
  treated as illustrative and skipped. Inline single-backtick code is **not**
  suppressed — that is the normal way genuine references are written.
- **Deletion headings & prose** (`_is_documented_deletion`): a match is skipped
  if its nearest preceding heading names a deletion (`migration`, `removed`,
  `deleted`, `deprecated`) or if its line / an adjacent line carries a deletion
  cue (`deleted module`, `no longer in the codebase`, `no longer exists`,
  `previously in`, `formerly`). This suppresses paths like `intent/__init__.py`
  documented under a `## Migration ...` heading.

Every suppressed match is logged at DEBUG so an operator can audit exactly what
the filter dropped.

#### Two-tier dedup

Issue filing uses a two-tier dedup gate:

1. **Local-Redis fast-path** (`docs_audit:issues_filed:{hash}`, SHA-256 of the
   title, 30-day TTL): a per-machine cache. If the key exists, filing is skipped
   without any GitHub call.
2. **Live-tracker gate** (`_open_issue_exists`): the **authoritative**
   cross-machine check. Before filing, it runs
   `gh issue list --state open --label documentation --search "<title>"` and
   confirms a hit with an exact normalized-title comparison (the title encodes
   both the path and the doc, so it is a natural composite key). Local Redis
   alone is insufficient because each machine keeps its own Redis, so the same
   finding would otherwise be filed once per machine.

The tracker query **fails open**: on any `gh` failure, non-zero exit, or
malformed output it logs a warning and degrades to Redis-only dedup rather than
silently dropping a genuine finding. This shrinks the cross-machine duplicate
window to a brief TOCTOU race (two machines querying within a few seconds of
each other) instead of one duplicate per machine per 30 days.

## Rotation State

Rotation state lives in a **single Redis hash** `docs_audit:last_run`:

| Field | Value |
|-------|-------|
| `{path_slug}` | float Unix timestamp of last audit (repo doc) |

Reads use `HGETALL` (single round-trip); writes use `HSET`. A repo with 200
docs produces 200 hash fields under one key, not 200 top-level keys. Audit
state is inspectable via `redis-cli HGETALL docs_audit:last_run`.

`_select_primary_doc` globs `docs/features/*.md` only — it has never enumerated,
selected, or weighted vault docs. An earlier revision of this doc claimed "vault
docs are picked at half the rate of repo docs (`DEFAULT_VAULT_WEIGHT = 0.5`)" —
that was inaccurate: `DEFAULT_VAULT_WEIGHT`, the `vault_weight` parameter path,
and the `vault:{project_key}:{path_slug}` rotation-hash field it fed
(`_vault_field`) were a schema hook that was never wired to a producer. Issue
#2084 removed all three as dead code (per the repo's no-dead-code policy) and replaced
them with a real vault-aware mechanism that runs **beside**, not through, this
rotation — see [Vault↔Site/Docs Drift Detector](#vaultsitedocs-drift-detector)
below.

## Locking

```
docs_audit:running:global       — rotation reflection lock (TTL 1h)
docs_audit:sweeper:running      — branch-sweeper lock (TTL 30min)
docs_audit:issues_filed:{hash}  — per-finding dedup (TTL 30d)
docs_audit:last_run             — rotation state hash
docs_audit:last_completed_run_ts        — Phase 2 liveness signal
docs_audit:last_completed_run_summary   — Phase 2 liveness JSON summary
```

All locks use the established SETNX pattern: `r.set(key, "1", nx=True, ex=ttl)`.
The auditor releases its lock in a `try/finally` so the next scheduled run
does not have to wait out the TTL.

## Memory Refresh Hook

`reflections.docs_auditor.refresh_docs_in_memory(touched_paths: list[str]) -> None`
is a public no-op-by-default hook called after fixes are applied and committed,
before PR creation (Caller A) or stage marker write (Caller B). Issue #1249
will replace the body with a real implementation that re-ingests touched docs
into the Memory substrate.

The hook signature is **stable** — call sites in this module will not change
when #1249 lands. The hook is always non-blocking and fire-and-forget;
exceptions are caught and logged so the auditor never fails because the hook
failed.

## Branch Sweeper

`run_docs_branch_sweeper()` runs daily and:
- Deletes `docs-audit/*` remote branches with no PR ever opened, age >7 days
- Closes (`gh pr close --delete-branch`) open `docs-audit/*` PRs older than 14 days

Scope is intentionally narrow — only branches under the `docs-audit/` prefix
are touched. Branches with any review comment, non-bot authorship, or merged
PR are left alone. This sweeper does NOT touch `session/*` branches; that
remains `agent/session_revival.py`'s scope.

## Vault↔Site/Docs Drift Detector

Issue #2084 added a second, independent detector to the rotation reflection:
a curated vault↔page drift audit. It compares the human-curated work vault's
canonical narratives against the site/docs pages that are supposed to reflect
them, and flags divergence. Full design writeup:
[Vault↔Site/Docs Drift Audit](vault-drift-audit.md).

Summary of what lives in `reflections/docs_auditor.py`:

- **`VAULT_SITE_MAPPING`** — a module-level curated
  `dict[str, tuple[str, str | None]]` mapping each canonical vault-relative
  narrative path to `(site_page, repo_doc)`. Currently 9 entries: the "Valor AI
  System Overview," the 4 strategic-analysis decks, and the 4 persona bios. This
  is **not a full-vault walk** — the detector iterates only these fixed entries,
  so `daily-logs/` (never a mapping entry) is structurally excluded and cannot
  grow the comparison surface.
- **`_detect_vault_site_drift`** — for each mapping entry, a coarse
  changed-since heuristic: if the vault file's filesystem mtime is newer than
  the mapped site page's (and, when present, the repo doc's) last git-commit
  timestamp, the narrative has drifted and an advisory finding is emitted.
  Markitdown sidecars (`generated_by: markitdown` frontmatter) are skipped.
- **`_is_secrets_path(rel_path, vault_root)`** — a defensive assertion guarding
  every mapping entry before it is read. Excludes on a path-**component**
  match (not substring), case-insensitive, checked on **both** the lexical
  (declared) relative path and the resolved real path:
  `.resolve()` can rewrite a symlinked `secrets` directory to a target that no
  longer has the `secrets` component (so the lexical check catches what
  resolution would hide), while a symlink pointing *into* a real `secrets/`
  tree is caught by the resolved check. If `relative_to(vault_root)` raises
  `ValueError` (the entry resolves outside the vault), the entry is treated as
  excluded — fail-closed, not raised or silently included.
- **`VAULT_DRIFT_ISSUE_CAP = 5`** (defined next to `NEIGHBORHOOD_CAP`) — bounds
  how many vault-drift `gh issue create` calls a single run may make, checked
  before every filing. Findings past the cap are logged and skipped, not
  filed. Filing reuses the same two-tier dedup gate (`_file_issue_if_new`) as
  every other detector in this module.
- **`vault_narratives_compared`** — a per-run count of narratives actually
  compared (secrets-guarded, missing, or markitdown-sidecar entries don't
  count), threaded into `_write_liveness` via a new **explicit optional 5th
  parameter** (`vault_narratives_compared: int | None = None`). The other four
  existing call sites still pass exactly four positional args and are
  unaffected — `_write_liveness` only includes the field in the liveness
  summary when it is not `None`, so "detector ran, found zero drift" (`0`) is
  distinguishable from "the field is absent because this call site never runs
  the vault comparison."
- **Advisory only.** The detector files GitHub issues; it never rewrites
  `site/*.html` or vault files. The existing markdown-only apply guard is
  unchanged.

Runs unconditionally on every `docs-auditor` rotation pass, beside (not
through) `_select_primary_doc` — it does not touch the `docs/features/*.md`
single-pick rotation or its hash, so it cannot starve or destabilize that
rotation.

## Configuration

Both reflections are registered in `config/reflections.yaml`. The
`docs-auditor` entry was **dormant** (`enabled: false`) from #1247 through
#2084 — even the pre-existing repo-doc rotation never fired, despite this
doc's YAML example always having shown `enabled: true`. Issue #2084 flipped
the live vault-managed file to actually match the example, so the reflection
now runs for real (advisory/report-only, same as before). Verify with
`python -m reflections --dry-run` — exit 0, `docs-auditor` listed as due/ran.

```yaml
- name: docs-auditor
  description: "Unified docs auditor: rotates least-recently-audited primary doc..."
  interval: 86400
  priority: low
  execution_type: function
  callable: "reflections.docs_auditor.run_docs_auditor"
  enabled: true

- name: do-docs-branch-sweeper
  description: "Delete stale docs-audit/* branches >7d..."
  interval: 86400
  priority: low
  execution_type: function
  callable: "reflections.docs_auditor.run_docs_branch_sweeper"
  enabled: true
```

`config/reflections.yaml` is vault-managed (symlink to `~/Desktop/Valor/reflections.yaml`).

## Operational Cheatsheet

```bash
# Inspect rotation state
redis-cli HGETALL docs_audit:last_run

# Phase 2 liveness signal
redis-cli GET docs_audit:last_completed_run_ts
redis-cli GET docs_audit:last_completed_run_summary

# Force-clear the lock if a run hung
redis-cli DEL docs_audit:running:global

# Run /do-docs from a PR
python -c "from reflections.docs_auditor import audit; \
  import json; print(json.dumps(audit(primary_path=None, \
  scope_mode='pr-changed-files', apply_mode='apply', project_key='valor')))"
```

## Tests

Unit tests live in `tests/unit/test_docs_auditor_substrate.py` — covers
`audit()`, rotation reflection, branch sweeper, SETNX lock contention,
neighborhood cap, zero-diff gate, auth probe degradation, memory-refresh
hook, and the `/do-docs` thin-caller contract. `TestIsSecretsPath` and
`TestVaultSiteDrift` cover the vault↔site/docs drift detector (mixed-case,
near-miss, symlink-into-secrets, out-of-vault exclusion; compared-count
correctness; issue-cap enforcement); `TestWriteLivenessVaultParam` covers
the `_write_liveness` 4-arg/5-arg positional contract (`fixes_withheld` is a
trailing keyword param, so that contract is unchanged); `TestVaultDeadCodeRemoved`
asserts `DEFAULT_VAULT_WEIGHT`, `vault_weight`, and `_vault_field` are gone.

The four gates are covered by `TestStaleTermDictionary` (cue tiers across
backticked, cased, alias, and arrow forms; the channel stays
`(re.Pattern, str)`), `TestStaleTermWordBoundary` (path-token suppression, and
`test_suppression_survives_an_earlier_shortening_fix`, a fixture with two regex
fixes where the first shortens the text ahead of the second's match, proving
context is derived at match time rather than from a stale detection-time
index), `TestExistenceInvariant` (bare-name withhold, doc-relative resolution,
ambiguous-but-present pass, no re-validation of pre-existing refs — every case
is expressed as a prose-anchored regex fix whose *replacement*, not its match,
carries the path-shaped string, so gate 3's path-token suppression cannot eat
the case before the invariant runs), and `TestWithheldBlocksAutoMerge` (a
bare-name withhold reaching the PR body, Telegram, and liveness).
`TestWithheldRateNonRegression` self-baselines the narrow and widened
`_PATH_REF_RE` arms in one run inside a disposable detached `git worktree`,
asserting the widening adds no withholds. `TestDeletedTargetFiltering::
test_rename_destination_reference_is_reported` runs on a real git checkout
(commit, `git mv`, delete) and asserts a reference to a former rename
destination is now reported rather than suppressed.

```bash
pytest tests/unit/test_docs_auditor_substrate.py -v
```

## See Also

- [Reflections](reflections.md) — registry and scheduler design
- [Vault↔Site/Docs Drift Audit](vault-drift-audit.md) — the curated
  `VAULT_SITE_MAPPING` drift detector, in full
- `.claude/skills/do-docs/SKILL.md` — Caller B skill definition
- `reflections/docs_auditor.py` — substrate source
- Issue #1247 — design and rollout plan
- Issue #1249 — memory refresh hook (forward-compat target)
- Issue #2084 — vault↔site/docs drift detector, reflection enable, xref-orphan sweep
- Issues #2744 / #2759 — the migration-context hatch, apply-time suppression, and
  the bare-name existence invariant (`docs/plans/docs-auditor-migration-context-and-bare-paths.md`)
