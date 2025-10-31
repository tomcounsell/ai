# CTO Tools - Final 3-Phase Weekly Review Framework

## Summary

The `weekly_review()` tool in the CTO Tools MCP server provides a streamlined **3-phase framework** for conducting weekly engineering team reviews. This framework is analysis-focused, not prescriptive - it helps you understand what happened without telling you what to do next.

## Key Design Principles

1. **Analysis, Not Prescription**: Focus on understanding what the team accomplished and how they contributed, without prescribing future actions
2. **LLM-Aligned Workflow**: Draft first, identify patterns, then categorize - matching how LLMs naturally process information
3. **Adaptive Categorization**: Let the LLM choose 5 relevant categories based on actual work, not predetermined templates
4. **Team Recognition Built-In**: Team recognition is integrated into summaries, not a separate phase
5. **Multi-Level Outputs**: One review produces summaries for different audiences
6. **Optional Depth**: Patterns and deeper analysis available when requested (extra credit)

### Why This Workflow Aligns with LLMs

LLMs work best when they:
1. **Build context first** - See all the data before making decisions
2. **Identify patterns naturally** - Find groupings organically rather than forcing predetermined categories
3. **Make informed decisions** - Choose category names after understanding what's actually there

The Phase 2 workflow mirrors this: **Draft → Identify → Categorize → Calculate**. This allows the LLM to build a mental model of the week's work before committing to a categorization scheme.

## The 3-Phase Framework

### Phase 1: GATHER DATA
**Run these commands in parallel for efficiency**

```bash
# Get commits from last 7 days
git log --since="7 days ago" --oneline --no-merges

# Count commits by author
git log --since="7 days ago" --format="%an" --no-merges | sort | uniq -c | sort -rn

# Get detailed commit stats
git log --since="7 days ago" --stat --no-merges | head -500
```

### Phase 2: ANALYZE & CATEGORIZE
**LLM-aligned workflow: Draft → Identify → Categorize**

#### 2.1 Draft All Updates (Context Building)
First, organize the raw data before making decisions:
- List each significant commit with a 1-line summary
- Note obvious groupings or patterns
- Identify related commits (same feature, bug, refactoring)
- Look for repeated themes or focus areas
- **This is working notes - not final output**

#### 2.2 Identify Natural Groupings (Pattern Recognition)
Based on your draft, find what naturally clusters:
- Which commits relate to the same initiative?
- What common themes span multiple commits?
- What technical domains received focus?
- What types of work dominated this week?

#### 2.3 Choose Category Names (Decision Making)
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

**Always include** (Team/Repo Stats):
- 👥 Team Activity (contributor stats, collaboration patterns)
- 📈 Repository Metrics (files changed, code churn, velocity)

**Key principle**: Categories should emerge from the work, not force work into predetermined boxes.

#### 2.4 Categorize & Calculate Metrics
- Total commits over 7 days (average per day)
- Number of files changed
- Number of contributors
- Peak activity periods

### Phase 3: CREATE SUMMARIES
**Multi-level outputs with integrated team recognition**

#### 3.1 Technical Summary (For Engineering Team)
- Detailed breakdown per category
- Key technical decisions
- Technologies/tools involved
- Code examples and file references

#### 3.2 Executive Summary (For Leadership/Stakeholders)
**Format:**
```markdown
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

#### 3.3 Quick Summary (For Slack/Chat)
50-100 words covering:
- Total activity metrics
- Top 3 achievements
- Key contributors
- Most active areas

## Execution Modes

### ⚡ Speed Mode (15-20 min)
1. Run all git commands in parallel
2. Use sequential thinking for categorization
3. Create executive summary only
4. List top 3 achievements

### 🔍 Deep Mode (45-60 min)
1. Gather all data thoroughly
2. Deep-dive into each category
3. Create all three summary levels with detailed team recognition

## Pro Tips

- **Run in parallel**: Use multiple tool calls for git commands
- **Use thinking tool**: Leverage sequential thinking for complex analysis
- **Be specific**: Reference exact file paths and line numbers (file.ts:123)
- **Look for stories**: Commits tell a story - find the narrative
- **Trust patterns**: Repeated issues indicate systemic problems
- **Recognize effort**: Call out both big wins and quality improvements

## Extra Credit (When Asked to be Verbose)

If specifically requested, identify development patterns:
- **Peak activity days**: Which days had most commits?
- **High-change areas**: Which files/modules changed most?
- **Work distribution**: Is work balanced across the team?
- **Commit patterns**: Many small commits or few large ones?
- **Cross-functional work**: Are people working across different areas?

## Final Output Format

Your deliverable should include:

1. **Comprehensive Summary** (markdown formatted)
   - Overview section with metrics
   - Categorized accomplishments
   - Team contributions and recognition

2. **Supporting Analysis** (if deep review)
   - Detailed breakdowns per category
   - Individual contributor highlights
   - Technical details and code references

## What This Framework Does NOT Do

- ❌ Does not prescribe future actions or priorities
- ❌ Does not create action item checklists
- ❌ Does not make planning decisions
- ❌ Does not force predetermined categories
- ❌ Does not require a specific number of issues to be found

## What This Framework DOES Do

- ✅ Tells the story of what the team accomplished
- ✅ Recognizes individual and collaborative contributions
- ✅ Adapts categories to match actual work
- ✅ Produces multi-format summaries for different audiences
- ✅ Works with ANY codebase and tech stack
- ✅ Scales from 15-minute quick reviews to 60-minute deep dives

## Usage Examples

### Quick Executive Update
```
Claude, use CTO Tools for a fast 15-minute weekly review with executive summary.
```

### Comprehensive Analysis
```
Claude, use CTO Tools to run a detailed weekly review of the last 7 days with all summary formats.
```

### With Patterns (Verbose)
```
Claude, use CTO Tools for a comprehensive review including development patterns and workload analysis.
```

## Technical Details

- **Protocol**: Model Context Protocol (MCP)
- **Framework**: FastMCP
- **Language**: Python 3.11+
- **Dependencies**: None for tool logic (uses stdlib)
- **Architecture**: Stateless, single-function server
- **Works with**: Any git repository, any tech stack

## Testing & Quality

✅ All 11 tests pass
✅ Black formatting applied
✅ Deterministic output (same instructions each time)
✅ Works in both Claude Desktop and web manifest modes

## Files Involved

### Core Implementation
- `apps/ai/mcp/cto_tools_server.py` - 3-phase framework implementation
- `apps/ai/tests/test_mcp_cto_tools.py` - 11 comprehensive tests

### Documentation & Web Assets
- `apps/ai/mcp/CTO_TOOLS_README.md` - Installation and usage guide
- `apps/ai/templates/mcp/cto_tools.html` - Django template for landing page at ai.yuda.me/mcp/cto-tools
- `apps/ai/mcp/cto_tools_manifest.json` - Web manifest for browser installation

## Real-World Usage Pattern

This framework was designed based on analyzing a real weekly review session where:
1. We gathered 181 commits from 4 contributors over 7 days
2. Categorized work into 5 meaningful areas
3. Created technical, executive, and quick summaries
4. Recognized individual contributions and collaboration patterns

The framework captures this workflow and makes it repeatable for any codebase.
