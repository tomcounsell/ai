# Vault↔Site/Docs Drift Audit

A standing, repeatable audit that cross-references the human-curated work
vault (`~/work-vault/AI Valor Engels System/`) against the
[valorengels.com docs site](valorengels-site.md) and repo docs, catching
divergence between the two hand-maintained corpora before it accumulates.
Shipped as an extension to the existing [docs-auditor](docs-auditor.md)
substrate (issue #2084) rather than as a new skill — the issue's original
target, `/do-xref-audit`, was deleted with no replacement well before this
work started (see the plan's Freshness Check for the code evidence).

## Why this exists

The vault's `Valor AI System Overview.md`, its 4 strategic-analysis decks, and
its 4 persona bios cover ground the site and repo docs also describe, but the
two are edited independently and had no mechanism catching drift. Prior art
(`/do-xref-audit`) was a two-agent LLM inventory pipeline that got deleted in
#1247's consolidation; its consolidated successor, `reflections/docs_auditor.py`,
never grew vault awareness — `_select_primary_doc` still only globs
`docs/features/*.md`, and a rotation-hash schema hook for vault docs
(`DEFAULT_VAULT_WEIGHT`) existed but was never wired to a producer. This
feature is the first real vault-aware detector in the substrate.

## Design: curated mapping, not a vault walk

The detector does **not** enumerate the vault. It iterates a small,
module-level curated constant in `reflections/docs_auditor.py`:

```python
VAULT_SITE_MAPPING: dict[str, tuple[str, str | None]] = {
    "Valor AI System Overview.md": ("site/index.html", None),
    "managed-agents-x-valor-report.md": ("site/research.html", None),
    "valor-x-paperclip-report.md": ("site/research.html", None),
    "ma-x-perplexity-computer-report.md": ("site/research.html", None),
    "openhuman-vs-hermes.md": ("site/research.html", None),
    "Personas/Valor Engels.md": ("site/index.html", None),
    "Personas/HG Wells – Head of Operations.md": ("site/runtime.html", None),
    "Personas/Jules Verne – Head of Engineering.md": ("site/runtime.html", None),
    "Personas/Philip Pullman – Head of Product.md": ("site/runtime.html", None),
}
```

Each key is a vault-relative path; the value is `(site_page, repo_doc)` — the
site page is required, the repo-doc counterpart is optional (`None` when
there isn't one). This is the **entire** candidate set on every run.

An earlier revision of this plan tried a full-vault walk (enumerate every
vault `*.md`, exclude `daily-logs/` from the *comparison*, cap the walk with a
`VAULT_ENUM_CAP`). A second critique rejected it as silent-failure-prone dead
machinery: `daily-logs/` was excluded from comparison but **not** from the
walk, so an ever-growing dated-log tree could consume the enumeration cap and
silently push the ~9 canonical narratives out of the comparison entirely —
while `vault_narratives_compared` stayed nonzero, masking the failure. The
curated mapping replaces the walk outright: `daily-logs/` is never a mapping
entry, so it is **structurally** excluded and cannot affect the detector at
any volume.

## Vault-root resolution

The vault root for a `project_key` is resolved by reusing the tracked helper
`tools/knowledge/scope_resolver.py::_load_project_mappings()`, which reads
`~/Desktop/Valor/projects.json`'s `knowledge_base` field (`expanduser` +
`normpath`). This deliberately does **not** depend on the deleted, untracked
`~/.claude/skills/do-xref-audit/` orphan — that directory has no repo source
and is stale sync residue (see [Orphan sweep](#orphan-sweep) below). If no
`knowledge_base` mapping exists for the project key, `_resolve_vault_root`
logs a warning and returns `None`; the caller then skips vault comparison for
that run and the repo-doc rotation proceeds unaffected.

## Drift detection

For each mapping entry, `_detect_vault_site_drift` applies a coarse
changed-since heuristic — advisory/report-only, never blocking:

1. Guard the entry with `_is_secrets_path` (below) before touching the
   filesystem at all.
2. Confirm the vault file exists; skip (logged) if not.
3. Skip markitdown sidecars (`generated_by: markitdown` frontmatter) —
   defensive, since none of the curated entries are sidecars today.
4. Read the vault file's filesystem mtime. This is the point at which the
   narrative counts toward `vault_narratives_compared`.
5. Compare the vault mtime against the mapped `site_page`'s last git-commit
   timestamp (`git log -1 --format=%ct -- <path>`, via `_git_commit_ts`; `0`
   if the page has no commit history yet, so an unauthored page always reads
   as drifted). If the vault file is newer, emit a drift finding.
6. If a `repo_doc` counterpart is set, repeat the same comparison against it
   independently — a narrative can drift from the site, the repo doc, both,
   or neither.

Findings are `{"title", "body", "category": "vault-drift"}` dicts filed as
GitHub issues (label `documentation`) through the substrate's existing
`_file_issue_if_new` — the same two-tier dedup gate (local-Redis fast path +
authoritative live-tracker query) every other docs-auditor detector uses.
Nothing here invents a new filing or dedup path.

## `secrets/` guard

A single shared predicate, `_is_secrets_path(rel_path, vault_root)`, guards
**every** `VAULT_SITE_MAPPING` entry before it is read — unconditionally, not
just on a happy path. With the full-vault walk gone, `secrets/` can only enter
via a mis-authored mapping entry, so this is defense-in-depth over a static
list rather than a filter over a dynamic walk.

Matching semantics (exact):

```python
any(part.lower() == "secrets" for part in Path(rel_path).parts)
# OR, after resolving:
any(part.lower() == "secrets"
    for part in (vault_root / rel_path).resolve().relative_to(vault_root.resolve()).parts)
```

- **Component match, not substring** — `secrets-analysis.md` and
  `Secretsandbox/` are siblings, not matches.
- **Case-insensitive** — `Secrets/`, `SECRETS/`, `secrets/` all match.
- **Checked on both the lexical and the resolved path.** The lexical
  (declared) path is checked because `.resolve()` can rewrite a symlinked
  `secrets` directory to a real target that no longer has a `secrets`
  component — a resolved-only check could be fooled backwards. The resolved
  path is checked because a symlink can point *into* a real `secrets/` tree
  even when its own declared name says nothing about secrets.
- **Fail-closed on `ValueError`.** If `relative_to(vault_root.resolve())`
  raises (the entry resolves outside the vault root entirely), the entry is
  treated as excluded rather than raised or silently included.

Tested independently in `tests/unit/test_docs_auditor_substrate.py`
(`TestIsSecretsPath`): mixed-case component, near-miss non-match, a
symlink-into-secrets entry, an out-of-vault (`ValueError`) entry, and an
invariant that no shipped `VAULT_SITE_MAPPING` entry is itself a `secrets/`
path.

## Issue-volume cap

`VAULT_DRIFT_ISSUE_CAP = 5` is defined next to the existing `NEIGHBORHOOD_CAP`
constant and checked **before** every vault-drift `gh issue create` call. Once
the cap is reached in a run, remaining findings are logged and skipped — never
filed. The curated mapping is already tiny (9 entries, at most 18 findings per
run — one per site page plus one per optional repo-doc counterpart), so the
cap is defense-in-depth against a future mapping that grows, not a load-bearing
limiter today.

## Liveness signal

`_run_vault_drift_detection` returns `vault_narratives_compared` — the count
of narratives that were actually read and compared (secrets-guarded, missing,
or markitdown-sidecar entries don't count). This threads into
`_write_liveness` through a new **explicit optional 5th parameter**:

```python
def _write_liveness(
    slug: str,
    status: str,
    pr_url: str | None,
    files_touched: int,
    vault_narratives_compared: int | None = None,
) -> None: ...
```

`_write_liveness` had a fixed 4-arg signature with four existing call sites,
each passing exactly four positional args; a bare 5th positional arg would
have raised `TypeError` inside the function's own swallow-and-log wrapper,
silently dropping the liveness write. The explicit-optional-parameter approach
avoids that: the summary dict only gains a `vault_narratives_compared` key
when the value is not `None`, and only the rotation call site that actually
ran the vault comparison passes it — the other three call sites are unchanged
and unbroken.

This makes "detector ran, found zero drift" (`vault_narratives_compared: 0`,
key present) observably different from "the mapping is silently broken" (key
absent entirely, from a call site that never ran the vault comparison) or
from "vault unresolvable" (`vault_narratives_compared: 0` via
`_resolve_vault_root` returning `None` before any file is read — same `0`
value, but paired with the `docs_audit: vault root resolution failed` /
`no knowledge_base mapping` warning in the logs). Inspect the current value
with:

```bash
redis-cli GET docs_audit:last_completed_run_summary
```

## Advisory only

The detector files GitHub issues; it never rewrites `site/*.html`, repo docs,
or vault files. The substrate's pre-existing markdown-only apply guard is
unchanged and unaffected by this work — a human reconciles every drift
finding by hand.

## Reflection enablement

The detector runs inside `run_docs_auditor()` (the `docs-auditor` daily
rotation reflection), unconditionally on every pass, beside — not through —
the existing `docs/features/*.md` single-pick rotation. That reflection was
dormant (`config/reflections.yaml` `enabled: false`) from #1247 through this
work; #2084 flipped it to `enabled: true` (advisory/report-only, same
execution model as before) so the detector actually runs. See
[docs-auditor.md § Configuration](docs-auditor.md#configuration) for the
enable-history note. Verify with:

```bash
python -m reflections --dry-run   # exit 0, docs-auditor listed
```

## Orphan sweep

The issue's Recon Summary named `.claude/skills/do-xref-audit/SKILL.md` as the
file to edit — that skill was already deleted with no replacement (see
[docs-auditor.md § What It Replaces](docs-auditor.md#what-it-replaces)). A
stale, **untracked** copy survived only at
`~/.claude/skills/do-xref-audit/` (and `do-xref/`) on machines that had synced
it before deletion — legacy sync residue, not a source of truth. #2084 added
both to `RENAMED_REMOVALS` in `scripts/update/hardlinks.py`:

```python
RENAMED_REMOVALS: list[tuple[str, str]] = [
    ...
    ("skills", "do-xref-audit"),
    ("skills", "do-xref"),
]
```

`/update`'s inode-guarded sweep (`scripts/update/hardlinks.py`) removes these
stale user-level hardlinks on every machine — guarded so a target still
hardlinked to a live project source is preserved, and only genuine orphans
(no live source anywhere) are deleted.

## Dead code removed

With the curated mapping replacing the full-vault-walk design, three pieces
of vestigial machinery — never wired to any producer — were removed outright
(NO LEGACY CODE TOLERANCE): the `DEFAULT_VAULT_WEIGHT` constant, the
`vault_weight` parameter path, and the `_vault_field` helper (reachable only
via `_update_rotation_hash(..., is_vault=True)`, which nothing called).
`tests/unit/test_docs_auditor_substrate.py::TestVaultDeadCodeRemoved` asserts
all three are gone and that `_select_primary_doc` still globs only
`docs/features/*.md` (no rotation regression).

## Tests

`tests/unit/test_docs_auditor_substrate.py`:

- `TestIsSecretsPath` — the exclusion predicate's exact semantics.
- `TestVaultSiteDrift` — comparison correctness (drift found / not found /
  repo-doc counterpart / missing file / markitdown sidecar / secrets-guarded
  entry never read), the issue cap, and vault-unresolvable graceful
  degradation.
- `TestWriteLivenessVaultParam` — 4-arg call sites unaffected, 5-arg call
  site includes the count.
- `TestVaultDeadCodeRemoved` — the removed schema hook stays removed.

```bash
pytest tests/unit/test_docs_auditor_substrate.py -v -k "Vault or Secrets"
```

## See Also

- [docs-auditor](docs-auditor.md) — the substrate this detector lives inside
- [valorengels.com Docs Site](valorengels-site.md) — the site pages this
  detector compares against
- `reflections/docs_auditor.py` — source
  (`VAULT_SITE_MAPPING`, `_is_secrets_path`, `_detect_vault_site_drift`,
  `_run_vault_drift_detection`)
- `scripts/update/hardlinks.py` — `RENAMED_REMOVALS` orphan sweep
- `tools/knowledge/scope_resolver.py` — vault-root resolution helper
- Issue #2084 — vault↔site/docs drift detector, reflection enable, orphan sweep
- Issue #1247 — the consolidation that deleted `/do-xref-audit` with no
  vault-aware replacement, which this feature finally supplies
