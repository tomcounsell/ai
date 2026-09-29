# do-docs context — this repo (ai)

Operational specifics the `/do-docs` cascade executes in this repo. Pipeline-level
docs policy (feature index, tools reference, plans on main, addenda) lives in
`docs/sdlc/do-docs.md`; do not duplicate it here.

## Stage markers

At the start:

```bash
sdlc-tool stage-marker --stage DOCS --status in_progress --issue-number {issue_number} --run-id {run_id} || true
```

At the end, whether the cascade committed doc changes or verified the docs
consistent with nothing to commit (a clean no-op is a successful DOCS completion):

```bash
sdlc-tool stage-marker --stage DOCS --status completed --issue-number {issue_number} --run-id {run_id} || true
```

Never gate this marker on a commit having happened: withholding it makes the
router re-dispatch DOCS after REVIEW in a loop (#2377 Mode 2). The one exception:
if the auto-fix substrate returned `status: error`, abort and do not write the
completion marker, because the docs are in an unknown state.

## Plan context

To align edits with the feature's intent, use plan context in this order: what
the caller passed inline; the plan behind the PR body's `Closes #N`; the plan
matching a `session/{slug}` branch (`docs/plans/{slug}.md`); otherwise the diff
alone.

## Cross-repo `gh`

`GH_REPO` is set for cross-project work and `gh` honors it; no `--repo` flags.

## Doc locations (inventory priority order)

`CLAUDE.md`; `docs/features/*.md` and its `README.md` index; `docs/tools-reference.md`;
`.claude/commands/*.md`; `docs/plans/*.md`; `docs/sdlc/*.md`;
`.claude/skill-context/*.md`; `.claude/skills-global/*/SKILL.md`;
`.claude/skills/*/SKILL.md`; `config/identity.json`;
`config/personas/segments/*.md`; `site/*.html` (published pages, edited like
markdown; `site/assets/` is out of scope); other `docs/*.md`.

## Semantic doc-impact finder

```bash
python3 -c "import sys; sys.path.insert(0, '${AI_REPO_ROOT:-$HOME/src/ai}'); from tools.doc_impact_finder import index_docs; index_docs()"
python3 -c "
import sys
sys.path.insert(0, '${AI_REPO_ROOT:-$HOME/src/ai}')
from tools.doc_impact_finder import find_affected_docs
results, meta = find_affected_docs('''<CHANGE_SUMMARY>''')
if meta.degraded:
    print(f'DEGRADED: {meta.reason} (rerank_failures={meta.rerank_failures}/{meta.candidates})')
for r in results:
    print(f'{r.relevance:.2f} | {r.path} | {r.sections} | {r.reason}')
"
```

A `DEGRADED:` line (e.g. `no_embedding_provider` without an embedding key) means
fall back to lexical matching. Zero results without it means no docs affected.

## Stale-reference sweep paths

```bash
rg "<retired-term>" docs/ CLAUDE.md config/identity.json config/personas/segments/ .claude/commands/ .claude/skills/ .claude/skills-global/ .claude/skill-context/
rg "<retired-term>" --glob 'site/*.html'
```

Never grep bare `site/`: it includes the 38k-line generated `site/assets/graph.js`.

## Auto-fix substrate (run before manual edits)

```bash
python -c "
from reflections.docs_auditor import audit
import json, sys, os
result = audit(
    primary_path=None,
    scope_mode='pr-changed-files',
    apply_mode='apply',
    project_key=os.environ.get('VALOR_PROJECT_KEY', 'valor'),
)
print(json.dumps(result))
sys.exit(0 if result['status'] != 'error' else 1)
"
```

It applies stale-term renames to the working tree, runs no git, and files deduped
issues for cases needing judgment. Its `files_touched` joins the expected set.
Route on the JSON:

- `status: "ok"`, `fixes_withheld == 0`: continue.
- `fixes_withheld > 0` (any status): the existence invariant rejected fixes whose
  target path does not exist. Echo each `withheld` entry (`doc`, `old`, `new`,
  `reason`) into your output, then fix or explicitly leave each named doc by
  hand. `status: "ok"` is not a pass on those docs.
- `status: "error"`: abort and report; no completion marker.
- `status: "disabled"`: auth probe failed; continue without auto-fixes.

## Indexes

A new feature doc gets a row in `docs/features/README.md`; a new CLI command,
script, or tool gets an entry in `docs/tools-reference.md`. Adding or removing a
`site/*.html` page updates `site/sitemap.xml`.

## Site deploy

Site changes deploy at merge (`docs/sdlc/do-merge.md` runs `scripts/deploy-site.sh`).
If the cascade committed `site/` changes directly on `main`, run
`scripts/deploy-site.sh` now and state the outcome (deployed / failed /
skipped-report); on a machine without `wrangler` or the vault token it exits 0
with a "redeploy needed" notice.

## Push guard (before any push to `main`)

```bash
sdlc-push-guard --assume-head || { echo "Push refused: HEAD carries open-PR ancestry — checkout main / merge through gh pr merge"; exit 1; }
git push
```

It refuses a push from a HEAD at or descended from an open PR head, which would
register that PR's ancestry as merged (#2026). `--assume-head` is required: with
no pre-push stdin the guard otherwise treats the call as "nothing to push" and
passes (#2800).

## Plan bookkeeping

After the commit, set `status: docs_complete` in the frontmatter of the lane's
plan (`docs/plans/{slug}.md`, slug from the `session/{slug}` branch), adding the
key if absent. No plan file: skip.
