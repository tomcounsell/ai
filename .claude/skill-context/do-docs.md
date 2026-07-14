# do-docs context — this repo (ai)

This repo's nuances for the `/do-docs` cascade. The global skill body runs a generic,
`git`-only baseline; this file layers the ai-repo automation back in. Read top to bottom and
honor every declaration — each maps to a numbered step in the global `SKILL.md`.

For the higher-level SDLC-pipeline guidance on docs (docs/features as primary index,
commit-on-branch rules, plan commit-on-main), see `docs/sdlc/do-docs.md`. This file carries
the **operational** specifics the skill body executes; do not duplicate `docs/sdlc/do-docs.md`.

## Stage marker (wraps the whole skill)

At the very start of the skill, write an `in_progress` marker:

```bash
sdlc-tool stage-marker --stage DOCS --status in_progress --issue-number {issue_number} 2>/dev/null || true
```

After all documentation updates are complete and committed (Step 4), write the completion marker:

```bash
sdlc-tool stage-marker --stage DOCS --status completed --issue-number {issue_number} 2>/dev/null || true
```

## Goal alignment — finding plan context (Step 1 input)

When invoked by `do-build`, this skill should receive the **plan context** (high-level goal,
tracking issue, acceptance criteria) so doc updates align with the feature's intent — not
just the raw diff. Resolve plan context in priority order:

1. If the caller passed plan context inline (e.g. `do-build` includes it in the prompt), use it directly.
2. Check the PR body for `Closes #N` — fetch the issue, then look for `docs/plans/{slug}.md`.
3. Check the current git branch — if `session/{slug}`, look for `docs/plans/{slug}.md`.
4. If no plan found, proceed without it — the diff alone is sufficient for doc cascading.

Use plan context to understand the *purpose* of the change, identify conceptually-related
docs without keyword overlap, and write updates that explain the "why" alongside the "what".

## Cross-repo resolution (Step 1 `gh` commands)

For cross-project work, the `GH_REPO` environment variable is set automatically by
`sdk_client.py`. The `gh` CLI natively respects it, so all `gh` commands target the correct
repository — no `--repo` flags or manual parsing needed.

## Doc inventory locations (Step 1, Agent B)

This repo's documentation lives in these locations. Scan them in this priority order:

| Location | What lives there |
|----------|-----------------|
| `CLAUDE.md` | Primary project guidance, architecture, rules, Quick Commands table |
| `docs/features/*.md` | Feature documentation |
| `docs/features/README.md` | Feature index table (canonical feature list — keep entries current) |
| `site/*.html` | Published docs site pages at valorengels.com (living docs — edit alongside markdown; `site/assets/` is out of scope) |
| `docs/plans/*.md` | Plans that may reference this work as a prerequisite |
| `docs/sdlc/*.md` | Per-stage SDLC addenda (this repo only) |
| `.claude/skill-context/*.md` | Per-skill repo-context files (this repo only) |
| `.claude/skills-global/*/SKILL.md` | Workflow skill definitions (cross-repo) |
| `.claude/skills/*/SKILL.md` | Workflow skill definitions (project-only) |
| `.claude/commands/*.md` | Slash commands |
| `config/identity.json` | Structured identity data |
| `config/personas/segments/*.md` | Composable persona segments |
| `docs/*.md` (top-level) | Deployment, tools-reference, etc. |

Sort by importance: `CLAUDE.md` first, then features, then commands, then plans, then the rest.

## Semantic doc-impact finder (Step 1, Agent C)

In addition to lexical matching, run the embedding-based finder to catch conceptually-related
docs with no shared keywords:

```bash
# 1. Ensure the doc index is current
python3 -c "import sys; sys.path.insert(0, '${AI_REPO_ROOT:-$HOME/src/ai}'); from tools.doc_impact_finder import index_docs; index_docs()"

# 2. Find affected docs from the change summary
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

Replace `<CHANGE_SUMMARY>` with a 2-3 sentence natural-language summary of the change.
`find_affected_docs` returns `(results, meta)`; a `DEGRADED:` line (e.g.
`no_embedding_provider` when no embedding API key is configured) means the finder could not
run cleanly — that is expected in keyless environments; the cascade degrades gracefully to
lexical-only matching. Zero results WITHOUT a DEGRADED line means no docs are affected.
Merge per the global body's Step 2 rules.

## Stale-reference sweep paths (Step 2b)

For each retired term from the change summary, grep across all doc locations:

```bash
rg "<retired-term>" docs/ CLAUDE.md config/identity.json config/personas/segments/ .claude/commands/ .claude/skills/ .claude/skills-global/ .claude/skill-context/
```

Sweep the published site pages separately, scoped to HTML only — never bare `site/`,
which would grep the 38k-line generated `site/assets/graph.js` on every cascade:

```bash
rg "<retired-term>" --glob 'site/*.html'
```

## Auto-fix substrate (Step 2d — run BEFORE manual edits)

Run the unified docs-auditor substrate against the PR-changed files. It auto-handles four
classes of mechanical fix — renamed markdown links, renamed paths/symbols, README index
entries pointing at deleted files, and stale-term renames — so manual editing in Step 3 only
handles cases the substrate can't auto-detect.

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

The substrate applies fixes to the working tree, commits them to the **current branch** (not
a new branch), fires the memory-refresh hook after the commit, and files deduped GitHub
issues for cases needing human judgment (deleted targets, stub docs, orphan plans).

Parse the JSON output:
- `status: "ok"` — proceed to Step 3 for any remaining manual edits.
- `status: "error"` — abort and report; do NOT write the completion stage marker.
- `status: "disabled"` — auth probe failed; proceed in skill-only mode (no auto-fixes).

Do not re-commit the substrate's changes — it commits them itself.

## Index-table maintenance (Step 3)

When a new feature doc is created, add an entry to the `docs/features/README.md` index table.
Missing entries cause discoverability gaps. When a new CLI command, script, or tool is added,
add it to the appropriate table in `CLAUDE.md` (the Quick Commands table is the first place
devs look).

When you **add or remove** a `site/*.html` page during the cascade, update `site/sitemap.xml`
so the published sitemap matches the page set. Edit affected site pages surgically, exactly
like any markdown doc.

## Site deploy (Step 4)

Site changes deploy at merge: `docs/sdlc/do-merge.md` declares a post-merge step that runs
`scripts/deploy-site.sh` (wrangler deploy + liveness curl) when the merged diff touched
`site/`, `wrangler.jsonc`, or `src/index.js`. The cascade itself does not deploy. The one
exception: if the cascade committed `site/` changes **directly on `main`** (not a feature
branch), run `scripts/deploy-site.sh` immediately and report its output in the cascade
summary — state the deploy outcome explicitly (deployed / failed / skipped-report), never
swallow it. On a machine without `wrangler` or the vault token the script exits 0 with a
"redeploy needed" notice, which is the correct report on non-deploy machines.

## Mark the plan docs-complete (Step 4, after commit)

Locate the plan via the current branch slug or PR context, then set `status: docs_complete`
in its YAML frontmatter so it is ready for deletion at merge time:

```bash
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"
```

```python
import re
from pathlib import Path

plan_path = Path('docs/plans/{slug}.md')
if plan_path.exists():
    text = plan_path.read_text()
    if text.startswith('---\n'):
        end = text.index('\n---', 4)
        frontmatter = text[4:end]
        if 'status:' not in frontmatter:
            new_fm = frontmatter + '\nstatus: docs_complete'
        else:
            new_fm = re.sub(r'status:\s*\S+', 'status: docs_complete', frontmatter)
        plan_path.write_text('---\n' + new_fm + '\n---' + text[end + 4:])
    else:
        plan_path.write_text('---\nstatus: docs_complete\n---\n\n' + text)
    print(f'Marked {plan_path} as docs_complete')
else:
    print(f'No plan found at {plan_path} — skipping plan marker')
```
