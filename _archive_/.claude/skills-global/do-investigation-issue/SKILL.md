---
name: do-investigation-issue
description: "Post a GitHub investigation issue for an unverified finding, gap, or anomaly. Use when an audit or research surfaces something suspicious, or an observed behavior is unexpected but unconfirmed."
allowed-tools: Bash, Read
argument-hint: "<component> — <brief finding>"
---

# Post Investigation Issue

Creates a GitHub issue that captures an unverified finding and hands it off to a future investigator with enough context to start immediately. The issue does NOT require a confirmed defect — it exists to trigger root-cause analysis before any action is taken. Findings that fit: an audit skill surfaced something suspicious, research revealed a possible gap in the current implementation, or an observed behavior is unexpected but unconfirmed.

## Label Policy

- **Always add:** `investigation`
- **Never add:** `bug`, `feature`, or any other label unless the finding already confirms it
- `bug` is added later by the investigator if root-cause analysis confirms a defect

## Title Format

```
Reliability risk: {component} — {brief one-line description}
```

Use `Reliability risk:` for session/agent reliability findings.
Use `Integration failure:` for observed (not hypothetical) integration outages.
Use `Gap:` for missing capabilities surfaced by research or audits.

The component is the system area (e.g., `worker`, `nudge loop`, `session executor`, `bridge`).

## Body Template

Load [TEMPLATE.md](TEMPLATE.md) as the body skeleton. Fill every section — leave no section empty or as a placeholder. If a section genuinely has no content, write one sentence explaining why (e.g., "None — proactive investigation, no remediation attempted yet.").

## Quick Start

### Step 1: Collect the finding

Identify:
- **Component** — which part of the system is affected
- **Symptoms** — what was observed or what the research describes
- **Evidence** — raw output, error strings, quotes from source material, or code references
- **Impact** — what breaks or degrades if this is a real defect
- **Next steps** — concrete checklist of files to check or experiments to run

### Step 2: Draft, Verify, and Create the Issue (single bash invocation)

**Single-shell invariant (load-bearing):** The whole sequence below — allocate scratch path, write body, verify anchor, publish, cleanup — MUST run inside ONE bash tool invocation. Each Bash tool call spawns a fresh shell with a new `$$`, so splitting these steps across calls loses `OWNER_PID`/`OWNER_TS`/`ANCHOR`/`DRAFT` and breaks anchor verification. Do not split.

```bash
# Per-invocation draft path (mktemp ensures no collision with any concurrent agent).
# Anchor header proves the draft we publish is the draft we wrote.
# DO NOT "simplify" the anchor check away — it defends against another agent
# clobbering the scratch file between write and publish.
DRAFT=$(mktemp "${TMPDIR:-/tmp}/investigation_body.XXXXXX") || { echo "ERROR: mktemp failed" >&2; exit 1; }
OWNER_PID=$$
OWNER_TS=$(date +%s)
ANCHOR="draft-owner: pid=${OWNER_PID} ts=${OWNER_TS}"

# Write the anchor as the first line via printf (expands ${ANCHOR}),
# then append the body with a QUOTED heredoc so any literal ${...} or
# backticks in the body stay unexpanded.
printf '<!-- %s -->\n' "${ANCHOR}" > "$DRAFT"
cat >> "$DRAFT" << 'BODY'
…replace this heredoc with the filled-in TEMPLATE.md body (every section completed, no placeholders left)…
BODY

# Verify the anchor BEFORE publishing. Mismatch = a foreign agent clobbered
# the file, or our own write failed — never publish unknown content.
if ! head -1 "$DRAFT" | grep -qF "<!-- ${ANCHOR} -->"; then
  echo "ERROR: draft anchor mismatch — refusing to publish unknown content" >&2
  echo "  expected first line: <!-- ${ANCHOR} -->" >&2
  echo "  actual first line:   $(head -1 "$DRAFT")" >&2
  rm -f "$DRAFT"
  exit 1
fi

gh issue create \
  --title "Reliability risk: {component} — {brief description}" \
  --body "$(cat "$DRAFT")" \
  --label "investigation"

# Best-effort cleanup; mktemp paths live under $TMPDIR and the OS reaps stragglers.
rm -f "$DRAFT"
```

### Step 3: Report

```
Investigation issue created: #{number} — {title}
URL: {url}
```

## When to Err on the Side of Filing

File an issue if you are unsure whether to file. The cost of an unnecessary investigation issue is low. The cost of a missed reliability gap is high. Let the investigator decide what's worth acting on.

**File for:**
- Any finding from external research (blog posts, post-mortems, docs) that may apply to this codebase
- Any audit result that surfaces a suspicious gap, even if unconfirmed
- Any observed anomaly that occurred once and wasn't diagnosed
- Any pattern the current watchdog or monitoring might miss

**Do not file for:**
- Things already tracked in an open issue (search first: `gh issue list --search "KEYWORDS"`)
- Things that have been explicitly ruled out by prior investigation
- Aspirational features with no evidence of a current gap

## Cross-Repo Usage

The `gh` CLI uses the current working directory's git remote by default. No `--repo` flag needed when invoked from within the target repo. If invoked from outside a repo, pass `--repo owner/name` explicitly.
