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

    Returns comprehensive instructions for analyzing commit history, team velocity,
    and generating executive summaries. Designed to help CTOs quickly understand
    what their team accomplished and identify areas needing attention.

    Returns:
        str: Step-by-step instructions for conducting a weekly review
    """
    instructions = """
# Weekly Engineering Team Review Framework

## Purpose
Systematically review your development team's work over the past week to:
- Understand what was accomplished
- Identify blockers and challenges
- Recognize team contributions
- Plan next week's priorities

## Step 1: Gather Commit History
```bash
# Get commits from the last week across all branches
git log --all --since='1 week ago' --pretty=format:'%h - %an, %ar: %s' --abbrev-commit

# Get detailed stats by author
git shortlog --since='1 week ago' --numbered --summary

# Get changed files with stats
git log --all --since='1 week ago' --stat --oneline
```

## Step 2: Analyze the Data

### A. Categorize Work
Group commits into categories:
- **Features**: New functionality delivered
- **Bugs**: Issues fixed
- **Refactoring**: Code improvements without feature changes
- **Infrastructure**: DevOps, CI/CD, tooling
- **Documentation**: Docs, comments, README updates
- **Tests**: Test coverage improvements

### B. Identify Patterns
Look for:
- **High activity areas**: Which parts of codebase are changing most?
- **Team distribution**: Is work balanced or concentrated?
- **Commit patterns**: Are there many small commits or few large ones?
- **Branch activity**: Feature branches merged vs still in progress

### C. Extract Key Metrics
- **Commits per day**: Overall team velocity
- **Pull requests merged**: Delivery throughput
- **Files changed**: Scope of changes
- **Lines added/removed**: Code churn indicators
- **Authors active**: Team engagement level

## Step 3: Generate Executive Summary

Create a concise summary with:

### Highlights (3-5 bullet points)
- Major features shipped
- Critical bugs resolved
- Significant technical improvements

### Team Velocity
- X commits across Y pull requests
- Z features delivered
- N bugs fixed

### Areas of Focus
- Which systems/modules got most attention
- Why (planned features vs reactive fixes)

### Blockers & Risks
- Identified impediments
- Technical debt accumulating
- Resource constraints

### Next Week Priorities
- Carry-over work
- Upcoming milestones
- Critical path items

## Step 4: Team Recognition

Highlight individual contributions:
- Who shipped major features
- Who resolved critical bugs
- Who improved code quality
- Who helped others (pair programming, reviews)

## Step 5: Action Items

Define clear next steps:
- [ ] Schedule 1:1s for blocked team members
- [ ] Allocate time for technical debt
- [ ] Adjust priorities based on findings
- [ ] Share summary with stakeholders

## Pro Tips

**For Fast Reviews (15 min)**:
Focus on: Highlights, Metrics, Blockers only

**For Deep Reviews (60 min)**:
Include: Full categorization, pattern analysis, individual deep-dives

**For Board/Executive Updates**:
Lead with: Business value delivered, risks, resource needs

**Regular Cadence**:
- Run this every Friday at 4pm
- Compare week-over-week trends
- Track metrics over time for insights

## Sample Questions to Answer

1. What's the biggest technical achievement this week?
2. Are we making progress on our quarterly goals?
3. Is the team working on the right things?
4. Where are we accumulating technical debt?
5. Who needs support or recognition?
6. What should we double down on next week?
7. What should we stop doing?

---

**Remember**: The goal isn't just to count commits—it's to understand the story
of your team's week and make better decisions for the week ahead.
"""

    return instructions


def main():
    """Run the CTO Tools MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
