---
name: weekly-review
description: Stakeholder-friendly summary of recent commits by category with contributor stats. Use when asked for a weekly, bi-weekly, monthly, sprint, engineering, or team review, or 'review the last N days'.
allowed-tools: Bash, Read, Write
argument-hint: "[days] [categories]"
---

# Weekly Review

## What this skill does

Produces a structured engineering review of recent commits, organized by category with 2-5 bullets each plus contributor statistics. Output is plain text with Unicode emojis suitable for copy-paste into email, Slack, Google Docs, or any communication channel. Works for ANY codebase and tech stack — the framework is purely git-based.

The framework is analysis-focused (not prescriptive): it gathers commit data, helps you organize it into meaningful categories, and writes a concise summary in plain language accessible to non-technical stakeholders while still meaningful to engineers.

## Defaults

- `days`: 7 (use 14 for bi-weekly, 30 for monthly)
- `categories`: 5 (use 3 for shorter reviews, 7 for monthly)

If the user provides args, parse them as `<days> [categories]`. Otherwise use defaults.

## Phase 1: Gather data

Run these git commands in parallel from the repo root to collect commit history:

```bash
# Verify you're on the correct branch
git pull && git branch --show-current

# Get all commits
git log --since="<DAYS> days ago" --oneline --no-merges

# Count commits by author
git log --since="<DAYS> days ago" --format="%an" --no-merges | sort | uniq -c | sort -rn

# Get detailed stats (first 500 lines)
git log --since="<DAYS> days ago" --stat --no-merges | head -500
```

## Phase 2: Analyze internally (do not output this)

Think through the commits and organize them. Do NOT produce a long verbose breakdown — this is internal work.

1. **Review the commits** — read through and understand what changed
2. **Identify patterns** — group related commits together
3. **Choose N categories** — pick categories that naturally emerge from the actual work
4. **Note key stats** — total commits, files changed, contributors, percentages
5. **Identify highlights** — the most impactful changes

**Category examples** (pick what fits this period's actual work — do not force-fit):

- 🔐 Credential & Authentication Infrastructure
- 🔌 Third-party Integrations
- 💬 Frontend Performance & UX
- 📧 User Lifecycle Automation
- 🧪 Testing & Code Quality
- 🐛 Bug Fixes & Stability
- ⚙️ DevOps & Infrastructure
- 🏗️ API Development
- 💰 Billing & Payments
- 📊 Reporting & Analytics
- 💾 Database & Data Models
- 🚀 Feature Development

Use **descriptive, specific names** based on actual work — not generic labels. Prefer "Credential & Authentication Infrastructure" over just "Auth".

## Phase 3: Write the final summary

Output format (plain text, Unicode emojis, NO numbered sections):

```
# Engineering Review - <Date Range>

🔐 **Category Name**
• **Feature/improvement name** - What it does and why it matters for users or the business
• **Another improvement** - The benefit or problem it solves, in plain language
• **Third item** - Focus on impact, not implementation details
[Continue with 2-5 bullets per category]

🔌 **Category Name**
• **Feature name** - Business value and user impact
[2-5 bullets]

[... N categories total ...]

📊 **Team Statistics & Recognition**
• [X] total commits over [N] days ([Z] commits/day average)
• [Additional high-level metrics: features completed, improvements made]
• **[Name]**: [X] commits ([%]%) - [Their focus areas in plain language]
• **[Name]**: [X] commits ([%]%) - [Their focus areas in plain language]
```

**Title date range**: ALWAYS show the full requested period (e.g., "Oct 6-13, 2025" for a 7-day review), regardless of when commits actually occurred. The title shows the review period, not the activity period.

## Writing guidelines

**Each bullet**:
- Start with `**bold title**` then a dash and description
- Focus on WHAT was done and WHY it matters (business impact, user benefit, problem solved)
- Plain language — avoid jargon, code paths, method names, file references
- 1-2 sentences max if needed for clarity
- Test: "Would a product manager, designer, or executive understand this?"

**Category selection**:
- Choose the N most relevant categories to this period's work
- Order by importance/impact
- NO NUMBERS — just emoji + bold title (e.g., `🔐 **Authentication**` not `1. 🔐 Authentication`)

**Team Statistics section**:
- Calculate commit percentages for each contributor
- List in order of commit count (highest first)
- Include 1-2 bullets describing each person's focus areas in plain language
- Add relevant aggregate metrics (files changed, tests added, etc.)

## Output expectations

- ✅ Plain text with full Unicode emoji support
- ✅ N categories with emoji + bold title (NO NUMBERS)
- ✅ 2-5 bullets per category in plain language (no code paths, no method names, no file references)
- ✅ Team Statistics section with contributor breakdown
- ✅ Focus on business impact and user benefits, not technical implementation
- ❌ NOT multiple pages of verbose analysis
- ❌ NOT numbered sections
- ❌ NOT technical jargon or code references (`apps/path/file.py`, function names, etc.)

## Final step: save the document

Save to plain text in `/tmp/`:

- Weekly: `/tmp/eng_review_<mon><day>-<day>.txt` (e.g., `/tmp/eng_review_oct6-13.txt`)
- Monthly: `/tmp/eng_review_<mon><day>-<mon><day>.txt` (e.g., `/tmp/eng_review_sep7-oct7.txt`)

After saving, offer: `open -a TextEdit <path>`

## Version history

- v1.0.0 (2026-04-29): Imported from cuttlefish `apps/ai/mcp/cto_tools/server.py::weekly_review` as a self-contained skill (no MCP dependency)
