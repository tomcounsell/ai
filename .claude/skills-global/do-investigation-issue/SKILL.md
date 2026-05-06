---
name: do-investigation-issue
description: Use when posting a GitHub investigation issue for an unverified finding, potential gap, or anomaly that needs root-cause analysis before any action is taken. Also use when an audit skill surfaces something suspicious, research reveals a possible gap in the current implementation, or an observed behavior is unexpected but unconfirmed. Issues are labeled 'investigation' only — never 'bug' unless the defect is already confirmed.
allowed-tools: Bash, Read
argument-hint: "<component> — <brief finding>"
---

# Post Investigation Issue

Creates a GitHub issue that captures an unverified finding and hands it off to a future investigator with enough context to start immediately. The issue does NOT require a confirmed defect — it exists to trigger investigation.

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

### Step 2: Write the issue body to a temp file

```bash
cat > /tmp/investigation_body.md << 'BODY'
## Investigation Finding

**Component:** {component}
**Detected:** {date} ({source — e.g., "amux.io research", "daily-integration-audit", "observed in production"})
**Status:** {Unverified / Observed-once / Persistent}

## Symptoms
{What was observed or what the research describes. Be specific — include error strings, log lines, or quotes verbatim.}

## Diagnostic Output
```
{Raw evidence: log output, error messages, code excerpts, or external source quotes}
```

## Steps Attempted
{What remediation was tried, if any. "None — proactive investigation." is a valid answer.}

## Impact
{What functionality breaks or degrades if this is a real defect. Who is affected.}

## Next Steps
- [ ] {Specific file or function to inspect}
- [ ] {Specific experiment or test to run}
- [ ] {Specific behavior to confirm or rule out}
BODY
```

### Step 3: Create the issue

```bash
gh issue create \
  --title "Reliability risk: {component} — {brief description}" \
  --body "$(cat /tmp/investigation_body.md)" \
  --label "investigation"
```

### Step 4: Report

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
