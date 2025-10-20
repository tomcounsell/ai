#!/usr/bin/env -S uv run --quiet --script
# /// script
# dependencies = ["mcp"]
# ///
"""
CTO Tools MCP Server

Provides tools and frameworks for common CTO activities including:
- Weekly team reviews and commit analysis
- Engineering metrics and productivity insights
- Technical decision-making frameworks
- Team health and performance monitoring

This MCP server runs as a standalone FastMCP application.

Usage:
    # Run directly with uv
    uv run apps/ai/mcp/cto_tools_server.py

    # Or make executable and run
    chmod +x apps/ai/mcp/cto_tools_server.py
    ./apps/ai/mcp/cto_tools_server.py

    # In Claude Desktop config
    {
      "mcpServers": {
        "cto-tools": {
          "command": "uv",
          "args": ["run", "/absolute/path/to/cto_tools_server.py"]
        }
      }
    }
"""

import logging

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("CTO Tools")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@mcp.tool()
def weekly_review() -> str:
    """
    Provides a structured framework for conducting weekly engineering team reviews.

    Returns step-by-step instructions that guide you through:
    - Gathering commit data efficiently
    - Analyzing and categorizing work internally
    - Creating a concise summary suitable for any communication channel

    This framework works for ANY codebase and tech stack.

    Returns:
        str: Step-by-step instructions for conducting a weekly review with concise output
    """
    instructions = """
# Weekly Engineering Team Review Framework

## GOAL
Produce a structured weekly engineering review organized by 5 categories with 2-5 bullets each,
plus team statistics. Format uses numbered list, bold titles, and emojis. Written in plain
language accessible to non-technical stakeholders while still meaningful to engineers.

---

## PHASE 1: GATHER DATA

Run these git commands in parallel to collect commit history from the past 7 days:

```bash
# Verify you're on the correct branch
git pull && git branch --show-current

# Get all commits
git log --since="7 days ago" --oneline --no-merges

# Count commits by author
git log --since="7 days ago" --format="%an" --no-merges | sort | uniq -c | sort -rn

# Get detailed stats (first 500 lines)
git log --since="7 days ago" --stat --no-merges | head -500
```

---

## PHASE 2: ANALYZE INTERNALLY (Don't output this, just think)

Use the sequential thinking tool to organize your analysis:

1. **Review the commits** - Read through and understand what changed
2. **Identify patterns** - Group related commits together
3. **Choose 5 categories** - Pick categories that naturally emerge from the work

**Category examples** (choose what fits this week's actual work):
- 🔐 Credential & Authentication Infrastructure
- 🔌 Google Integration Rewrite (Enterprise Scale)
- 💬 Frontend Performance & User Experience
- 📧 User Lifecycle Automation
- 🧪 Testing & Code Quality
- 🐛 Bug Fixes & Stability
- ⚙️ DevOps & Infrastructure
- 🏗️ API Development
- 💰 Billing & Payments
- 📊 Reporting & Analytics
- 💾 Database & Data Models
- 🚀 Feature Development

**Important**: Choose descriptive, specific names based on actual work - not generic labels.
Examples: "Credential & Authentication Infrastructure" not just "Auth"

4. **Note key stats** - Total commits, files changed, contributors
5. **Identify highlights** - What were the most impactful changes?

**Important**: This analysis is internal work. Don't output long detailed breakdowns.

---

## PHASE 3: WRITE THE FINAL SUMMARY

**Output ONLY this format**:

```markdown
# Weekly Engineering Review - [Date Range]

**IMPORTANT**: The title date range should ALWAYS show the full period requested (e.g., "Oct 6-13, 2025"
for a 7-day review), regardless of when commits actually occurred. Even if there are only 2 days of
commits, the title reflects the full search period.

1. 🔐 Category Name
- **Feature/improvement name** - What it does and why it matters for users or the business
- **Another improvement** - The benefit or problem it solves, in plain language
- **Third item** - Focus on impact, not implementation details
- [Continue with 2-5 bullets per category]

2. 🔌 Category Name
- **Feature name** - Business value and user impact
- **Another feature** - What changed and why
- [2-5 bullets]

3. 💬 Category Name
- **Feature name** - Clear description of what users will experience
- [2-5 bullets]

4. 📧 Category Name
- **Feature name** - Benefits and outcomes
- [2-5 bullets]

5. 🧪 Category Name
- **Feature name** - What was improved and why it matters
- [2-5 bullets]

📊 Team Statistics & Recognition
- [X] total commits over [N] days ([Z] commits/day average)
- [Additional high-level metrics: features completed, improvements made]
- **[Name]**: [X] commits ([%]%) - [Their focus areas in plain language]
- **[Name]**: [X] commits ([%]%) - [Their focus areas in plain language]
- **[Name]**: [X] commits ([%]%) - [Their focus areas in plain language]
```

---

## WRITING GUIDELINES

**Title date range**:
- ALWAYS use the full period searched (e.g., "Oct 6-13" for 7 days, "Sept 7 - Oct 7" for 30 days)
- Don't adjust dates based on when commits actually happened
- The title shows the review period, not the activity period

**Emoji format**:
- Use real Unicode emojis (🔐 🔌 💬 📧 🧪 etc.)
- Choose appropriate emojis that visually represent the category

**Each bullet format**:
- Start with **bold title** - then description
- Focus on WHAT was done and WHY it matters (business impact, user benefit, problem solved)
- Write in plain language - avoid jargon, code paths, method names, technical implementation details
- Can be 1-2 sentences if needed for clarity
- Think: "Would a product manager, designer, or executive understand this?"

**Category selection**:
- Choose the 5 most relevant to this week's work
- Use descriptive names like "Credential & Authentication Infrastructure" not just "Auth"
- Order by importance/impact
- Use professional slack emoji codes

**Team Statistics section**:
- Calculate commit percentages for each contributor
- List in order of commit count (highest first)
- Include 1-2 bullet points describing each person's focus areas
- Add relevant metrics (files changed, tests added, etc.)

---

## IMPORTANT: OUTPUT EXPECTATIONS

Your final response should be:
- ✅ One structured summary with 5 numbered categories
- ✅ 2-5 bullets per category in plain language (no code paths, no method names, no file references)
- ✅ Team Statistics section with contributor breakdown
- ✅ Focus on business impact and user benefits, not technical implementation
- ❌ NOT multiple pages of verbose analysis
- ❌ NOT separate technical/executive/quick summaries
- ❌ NOT technical jargon or code references (apps/path/file.py, function names, etc.)

The analysis happens internally using sequential thinking. The output is a well-structured
summary accessible to ALL stakeholders - technical and non-technical alike.

---

## FINAL STEP: SAVE TO FILE

After completing the summary, save it to a markdown file:

**Filename format**: `/tmp/eng_review_<dates>.md`
- Example: `/tmp/eng_review_oct6-13.md` (for weekly review)
- Example: `/tmp/eng_review_sep7-oct7.md` (for monthly review)

**Only save if file write access is available** - if not, just output the summary.

Use the Write tool to save the complete markdown summary to this file.

---

**Start by running the PHASE 1 git commands in parallel, then proceed.**
"""

    return instructions


def main():
    """Run the CTO Tools MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
