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
Produce a structured weekly engineering review organized by 5 categories with 2-5 technical
bullets each, plus detailed team statistics. Format uses numbered list, bold bullet titles,
file references, and emojis. Suitable for sharing with engineering teams and leadership.

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

1. 🔐 Category Name
- **Feature/component name** - Technical description with key details (path/to/file.py)
- **Another feature** - What changed and how, include file references when relevant
- **Third feature** - More technical details with specifics
- [Continue with 2-5 bullets per category]

2. 🔌 Category Name
- **Feature name** - Description (apps/specific/path.py)
- **Another feature** - Technical details
- [2-5 bullets]

3. 💬 Category Name
- **Feature name** - Description
- [2-5 bullets]

4. 📧 Category Name
- **Feature name** - Description
- [2-5 bullets]

5. 🧪 Category Name
- **Feature name** - Description
- [2-5 bullets]

📊 Team Statistics & Recognition
- [X] total commits across [Y] files ([Z] commits/day average)
- [Additional metrics like test cases added, files changed, etc.]
- **[Name]**: [X] commits ([%]%) - [Their focus areas and achievements]
- **[Name]**: [X] commits ([%]%) - [Their focus areas and achievements]
- **[Name]**: [X] commits ([%]%) - [Their focus areas and achievements]
```

---

## WRITING GUIDELINES

**Emoji format**:
- Use real Unicode emojis (🔐 🔌 💬 📧 🧪 etc.)
- Choose appropriate emojis that visually represent the category

**Each bullet format**:
- Start with **bold title** - then description
- Include technical details (what AND how when relevant)
- Add file references in parentheses: `(apps/module/file.py)` or `(apps/module/file.py:123)`
- Can be 1-2 sentences if needed for clarity
- Be specific about technologies, patterns, and technical decisions

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
- ✅ 2-5 technical bullets per category with file references
- ✅ Team Statistics section with contributor breakdown
- ❌ NOT multiple pages of verbose analysis
- ❌ NOT separate technical/executive/quick summaries
- ❌ NOT long explanations of methodology

The analysis happens internally using sequential thinking. The output is a well-structured
technical summary suitable for sharing with engineering teams and leadership.

---

**Start by running the PHASE 1 git commands in parallel, then proceed.**
"""

    return instructions


def main():
    """Run the CTO Tools MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
