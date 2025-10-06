"""
CTO Tools MCP Server

Provides tools and frameworks for common CTO activities including:
- Weekly team reviews and commit analysis
- Engineering metrics and productivity insights
- Technical decision-making frameworks
- Team health and performance monitoring

This MCP server runs as a standalone FastMCP application.
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

    Returns comprehensive step-by-step instructions that guide you through:
    - Gathering commit data efficiently
    - Analyzing and categorizing work
    - Creating multi-level summaries
    - Identifying issues and patterns
    - Recognizing team contributions

    This framework works for ANY codebase and tech stack.

    Returns:
        str: Detailed step-by-step instructions for conducting a comprehensive weekly review
    """
    instructions = """
# Weekly Engineering Team Review - Step-by-Step Framework

## OVERVIEW
You are conducting a weekly review to understand what the team accomplished, identify issues,
and recognize contributions. Follow these steps in order.

---

## ⚠️ BEFORE YOU START

**Important**: Make sure you have the latest changes and are on the correct branch:

```bash
# Pull latest changes from remote
git pull

# Verify you're on the default branch (usually main or master)
git branch --show-current

# If not on default branch, switch to it
git checkout main  # or master, depending on your repo
```

---

## PHASE 1: GATHER DATA (Run these commands in parallel)

### 1.1 Get All Commits from Last 7 Days
```bash
git log --since="7 days ago" --oneline --no-merges
```

### 1.2 Count Commits by Author
```bash
git log --since="7 days ago" --format="%an" --no-merges | sort | uniq -c | sort -rn
```

### 1.3 Get Detailed Commit Stats (first 500 lines)
```bash
git log --since="7 days ago" --stat --no-merges | head -500
```

**After gathering data, proceed to analysis.**

---

## PHASE 2: ANALYZE & CATEGORIZE

### 2.1 Draft All Updates
**First, organize the raw data before deciding on categories**

Go through all commits and create a working draft:
- List each significant commit with a 1-line summary
- Note any obvious groupings or patterns you see
- Identify related commits (same feature, same bug fix, same refactoring)
- Look for repeated themes or focus areas

**This is your working notes - not the final output yet.**

### 2.2 Identify Natural Groupings
Based on your draft, identify what naturally clusters together:
- Which commits are related to the same initiative?
- What are the common themes across multiple commits?
- Are there clear technical domains that received focus?
- What types of work dominated this week?

### 2.3 Choose Category Names
**Now that you've seen and organized the work, choose 5 main categories that best describe it.**

**Category suggestions** (choose what fits, or create your own):
- 🤖 AI & ML Features
- 🔐 Authentication & Security
- 💬 User Experience / Frontend
- ⚡ Performance & Infrastructure
- 📝 Code Quality & DevEx
- 🐛 Bug Fixes & Hotfixes
- 📊 Data & Analytics
- 🔧 DevOps & CI/CD
- 🔌 API Development
- 💰 Billing & Payments
- 📈 Reporting & Dashboards
- 🗄️ Database & Data Models
- 🧪 Testing & QA

**Always include** (for team/repo stats):
- 👥 **Team Activity** (contributor stats, collaboration patterns)
- 📈 **Repository Metrics** (files changed, code churn, velocity)

Your categories should emerge from the work, not force the work into predetermined boxes.

### 2.4 Categorize & Calculate Metrics
- Total commits over 7 days (and average per day)
- Number of files changed
- Number of contributors
- Peak activity periods

---

## PHASE 3: CREATE SUMMARIES (Multiple Levels)

### 3.1 Technical Summary (For Engineering Team)
Create a detailed summary covering:

**Per Category:**
- What was accomplished (bullet points with technical details)
- Key technical decisions made
- Technologies/tools involved

**Include:**
- Code examples or file references when relevant
- Links to important commits or PRs
- Technical debt identified

### 3.2 Executive Summary (For Leadership/Stakeholders)
Create a high-level summary (200-300 words) with:

**Format:**
```
## Development Summary ([Date Range])

### 🚀 Overview
[Total commits] | [Files changed] | [Contributors]

### [Category Icons & Names]
- [3-4 word description of accomplishments]

### 👥 Team Recognition
For each contributor:
- **[Name] ([X] commits)**: [Their focus areas and notable achievements]
- Call out collaborative work (co-authored commits)
- Highlight high-impact contributions

### Impact
[1-2 sentences on business impact or user value delivered]
```

### 3.3 Quick Summary (For Slack/Chat)
Create a condensed version (50-100 words) suitable for quick updates:
- Total activity metrics
- Top 3 achievements
- Key contributors
- Most active areas

---

## EXECUTION TIPS

### ⚡ For Speed (15-20 min review):
1. Run all git commands in parallel
2. Use sequential thinking tool to analyze categories
3. Create executive summary only
4. List top 3 achievements and any red flags

### 🔍 For Depth (45-60 min review):
1. Gather all data thoroughly
2. Deep-dive into each category
3. Create all three summary levels with detailed team recognition

### 🎯 Pro Tips:
- **Run in parallel**: Use multiple tool calls simultaneously for git commands
- **Use thinking tool**: Leverage sequential thinking for complex analysis
- **Be specific**: Reference exact file paths and line numbers (file.ts:123)
- **Look for stories**: Commits tell a story - find the narrative
- **Trust patterns**: Repeated issues indicate systemic problems
- **Recognize effort**: Call out both big wins and quality improvements

### 🌟 Extra Credit (When Asked to be Verbose):
If specifically requested, identify development patterns:
- **Peak activity days**: Which days had most commits?
- **High-change areas**: Which files/modules changed most?
- **Work distribution**: Is one person doing most work? Is work balanced?
- **Commit patterns**: Many small commits or few large ones?
- **Cross-functional work**: Are people working across different areas?

---

## FINAL OUTPUT FORMAT

Your final deliverable should be:

1. **Comprehensive Summary** (markdown formatted)
   - Overview section with metrics
   - Categorized accomplishments
   - Team contributions and recognition

2. **Supporting Analysis** (if deep review)
   - Detailed breakdowns per category
   - Individual contributor highlights
   - Technical details and code references

---

**Remember**: This isn't just a commit count—you're telling the story of what the team
accomplished and how they contributed. Focus on clear analysis and team recognition.

Start by running the PHASE 1 git commands in parallel, then proceed through each phase systematically.
"""

    return instructions


def main():
    """Run the CTO Tools MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
