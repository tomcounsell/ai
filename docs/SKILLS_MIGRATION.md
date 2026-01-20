# MCP Server to Clawdbot Skills Migration Guide

**Status**: ✅ Migration Complete (2026-01-19)

All 6 MCP server skills have been migrated to Clawdbot:
- Sentry (8 tools), GitHub (10 tools), Linear (9 tools)
- Notion (8 tools), Stripe (9 tools), Render (9 tools)

See [CLAWDBOT_MIGRATION_PLAN.md](CLAWDBOT_MIGRATION_PLAN.md) for current architecture.

---

This document details how to convert our existing MCP servers (currently defined as Claude Code subagents in `.claude/agents/`) to Clawdbot skills for the unified conversational development environment.

## Overview

Our current architecture uses Claude Code subagents defined in markdown files under `.claude/agents/`. These need to be migrated to the Clawdbot skills format which provides a more structured, portable, and maintainable approach.

## Current State vs Target State

| Current | Target |
|---------|--------|
| `.claude/agents/{name}.md` | `~/clawd/skills/{skill-name}/` |
| Markdown-based agent definitions | JSON manifest + JS/Python entry points |
| Claude Code native subagents | Clawdbot portable skills |
| Tool patterns via wildcards (`stripe_*`) | Explicit tool implementations |

## Skill Directory Structure

```
~/clawd/skills/
└── {skill-name}/
    ├── manifest.json      # Skill metadata and configuration
    ├── index.js           # Entry point (or index.py for Python)
    ├── tools/             # Individual tool implementations
    │   ├── tool1.js
    │   └── tool2.js
    ├── prompts/           # System prompts and templates
    │   └── system.md
    └── README.md          # Documentation
```

## Manifest Format

```json
{
  "name": "skill-name",
  "version": "1.0.0",
  "description": "What this skill does",
  "model": "sonnet",
  "tools": ["tool1", "tool2", "tool3"],
  "requires": {
    "env": ["API_KEY", "ANOTHER_KEY"],
    "dependencies": ["axios", "lodash"]
  },
  "permissions": {
    "accept": ["list_*", "get_*", "retrieve_*"],
    "prompt": ["create_*", "update_*"],
    "reject": ["delete_*"]
  }
}
```

## Tool Implementation Pattern

Each tool should follow this pattern:

```javascript
// tools/list_items.js
module.exports = {
  name: "list_items",
  description: "List all items with optional filters",

  parameters: {
    type: "object",
    properties: {
      filter: {
        type: "string",
        description: "Optional filter query"
      },
      limit: {
        type: "number",
        description: "Max items to return",
        default: 10
      }
    },
    required: []
  },

  async execute({ filter, limit = 10 }, context) {
    try {
      // Pre-validation (cheap operations first)
      if (limit > 100) {
        return { error: "Limit must be <= 100" };
      }

      // API configuration check
      const apiKey = process.env.API_KEY;
      if (!apiKey) {
        return { error: "API_KEY not configured" };
      }

      // Execute operation
      const result = await apiClient.listItems({ filter, limit });

      // Return structured output
      return {
        success: true,
        data: result.items,
        count: result.items.length,
        hasMore: result.hasMore
      };

    } catch (error) {
      // Categorized error handling
      if (error.code === 'RATE_LIMITED') {
        return { error: "Rate limited. Please try again later." };
      }
      if (error.code === 'UNAUTHORIZED') {
        return { error: "API key invalid or expired." };
      }
      return { error: `Operation failed: ${error.message}` };
    }
  }
};
```

---

## Migration Priority

### Priority 1: Sentry (Most immediately useful for Daydream Step 3)

**Current**: `.claude/agents/sentry.md`
**Purpose**: Error monitoring, performance analysis, alert triage, stack traces

**Why First**: The Daydream system needs Sentry integration to automatically check error logs, categorize issues, and suggest fixes as part of its autonomous maintenance cycle.

### Priority 2: GitHub (Core development workflow)

**Current**: `.claude/agents/github.md`
**Purpose**: PR management, code review, issue tracking, CI/CD workflows

**Why Second**: Core to daily development workflow. Every code change flows through GitHub.

### Priority 3: Linear (Task management for Daydream Step 4)

**Current**: `.claude/agents/linear.md`
**Purpose**: Issue creation, sprint planning, roadmap management

**Why Third**: Daydream Step 4 cleans up task management - needs Linear to update/close stale items.

### Priority 4: Notion (Documentation for Daydream Step 5)

**Current**: `.claude/agents/notion.md`
**Purpose**: Documentation, knowledge search, database management

**Why Fourth**: Daydream Step 5 updates documentation - needs Notion for documentation access.

### Priority 5: Stripe (Business operations)

**Current**: `.claude/agents/stripe.md`
**Purpose**: Payment processing, subscriptions, billing, refunds, revenue reporting

**Why Fifth**: Important for business operations but not part of core development workflow.

### Priority 6: Render (Deployment operations)

**Current**: `.claude/agents/render.md`
**Purpose**: Service deployment, infrastructure monitoring, log analysis, scaling

**Why Last**: Deployment operations are typically less frequent than other activities.

---

## Detailed Migration Plans

### 1. Sentry Skill

```
skills/
└── sentry/
    ├── manifest.json
    ├── index.js
    ├── tools/
    │   ├── list_issues.js
    │   ├── get_issue.js
    │   ├── list_events.js
    │   ├── get_event.js
    │   ├── list_projects.js
    │   ├── get_performance_data.js
    │   ├── update_issue_status.js
    │   └── resolve_issue.js
    ├── prompts/
    │   └── system.md
    └── README.md
```

**manifest.json**:
```json
{
  "name": "sentry",
  "version": "1.0.0",
  "description": "Error monitoring, performance analysis, and application observability via Sentry",
  "model": "sonnet",
  "tools": [
    "list_issues",
    "get_issue",
    "list_events",
    "get_event",
    "list_projects",
    "get_performance_data",
    "update_issue_status",
    "resolve_issue"
  ],
  "requires": {
    "env": ["SENTRY_API_KEY", "SENTRY_ORG_SLUG"],
    "dependencies": ["@sentry/node", "axios"]
  },
  "permissions": {
    "accept": ["list_*", "get_*"],
    "prompt": ["update_*", "resolve_*"],
    "reject": ["delete_*"]
  }
}
```

**Key Tools**:

| Tool | Description | Use Case |
|------|-------------|----------|
| `list_issues` | Get issues with filters (status, priority, date) | Daydream error review |
| `get_issue` | Get detailed issue with stack trace | Deep investigation |
| `list_events` | Get error events for an issue | Pattern analysis |
| `get_performance_data` | Query performance metrics | Bottleneck identification |
| `update_issue_status` | Mark issue as acknowledged/ignored | Triage workflow |
| `resolve_issue` | Mark issue as resolved | Close fixed issues |

**Daydream Integration**:
```javascript
// In Daydream Step 3
async function checkErrorLogs(state) {
  const sentry = await loadSkill('sentry');

  // Get new/recurring errors from last 24h
  const issues = await sentry.execute('list_issues', {
    status: 'unresolved',
    since: '24h',
    sort: 'frequency'
  });

  // Categorize by severity
  const categorized = categorizeIssues(issues);

  // For critical issues, get detailed stack traces
  for (const issue of categorized.critical) {
    const details = await sentry.execute('get_issue', { id: issue.id });
    state.report.errors.push({
      issue,
      stackTrace: details.stackTrace,
      suggestedFix: await analyzeFix(details)
    });
  }

  return state;
}
```

---

### 2. GitHub Skill

```
skills/
└── github/
    ├── manifest.json
    ├── index.js
    ├── tools/
    │   ├── list_prs.js
    │   ├── get_pr.js
    │   ├── create_pr.js
    │   ├── merge_pr.js
    │   ├── list_issues.js
    │   ├── create_issue.js
    │   ├── get_commits.js
    │   ├── get_checks.js
    │   ├── search_code.js
    │   └── get_file.js
    ├── prompts/
    │   └── system.md
    └── README.md
```

**manifest.json**:
```json
{
  "name": "github",
  "version": "1.0.0",
  "description": "Code repositories, PRs, issues, and development workflows via GitHub",
  "model": "sonnet",
  "tools": [
    "list_prs",
    "get_pr",
    "create_pr",
    "merge_pr",
    "list_issues",
    "create_issue",
    "get_commits",
    "get_checks",
    "search_code",
    "get_file"
  ],
  "requires": {
    "env": ["GITHUB_TOKEN"],
    "dependencies": ["@octokit/rest"]
  },
  "permissions": {
    "accept": ["list_*", "get_*", "search_*"],
    "prompt": ["create_*", "update_*", "merge_*", "close_*"],
    "reject": ["delete_repo", "delete_branch_main", "delete_branch_master"]
  }
}
```

**Key Tools**:

| Tool | Description | Use Case |
|------|-------------|----------|
| `list_prs` | Get PRs with filters | PR queue review |
| `get_pr` | Get PR details with diff | Code review |
| `create_pr` | Create new PR from branch | Ship changes |
| `merge_pr` | Merge approved PR | Complete workflow |
| `list_issues` | Get issues with labels | Bug tracking |
| `create_issue` | Create issue with template | Bug reporting |
| `get_checks` | Get CI/CD status | Deployment readiness |
| `search_code` | Search across repo | Code discovery |

---

### 3. Linear Skill

```
skills/
└── linear/
    ├── manifest.json
    ├── index.js
    ├── tools/
    │   ├── list_issues.js
    │   ├── get_issue.js
    │   ├── create_issue.js
    │   ├── update_issue.js
    │   ├── close_issue.js
    │   ├── list_cycles.js
    │   ├── get_team_velocity.js
    │   ├── search_issues.js
    │   └── get_roadmap.js
    ├── prompts/
    │   └── system.md
    └── README.md
```

**manifest.json**:
```json
{
  "name": "linear",
  "version": "1.0.0",
  "description": "Project management, issue tracking, sprint planning via Linear",
  "model": "haiku",
  "tools": [
    "list_issues",
    "get_issue",
    "create_issue",
    "update_issue",
    "close_issue",
    "list_cycles",
    "get_team_velocity",
    "search_issues",
    "get_roadmap"
  ],
  "requires": {
    "env": ["LINEAR_API_KEY"],
    "dependencies": ["@linear/sdk"]
  },
  "permissions": {
    "accept": ["list_*", "get_*", "search_*"],
    "prompt": ["create_*", "update_*", "assign_*", "close_*"],
    "reject": ["delete_*"]
  }
}
```

**Daydream Integration**:
```javascript
// In Daydream Step 4
async function cleanupTaskManagement(state) {
  const linear = await loadSkill('linear');

  // Find stale issues (no activity > 30 days)
  const staleIssues = await linear.execute('search_issues', {
    filter: { updatedBefore: '30d', status: ['backlog', 'todo'] }
  });

  for (const issue of staleIssues) {
    // Close if clearly outdated
    if (isObsolete(issue)) {
      await linear.execute('close_issue', {
        id: issue.id,
        reason: 'Closed by Daydream - stale/obsolete'
      });
      state.report.tasksCleanedUp.push(issue);
    }
  }

  return state;
}
```

---

### 4. Notion Skill

```
skills/
└── notion/
    ├── manifest.json
    ├── index.js
    ├── tools/
    │   ├── search.js
    │   ├── get_page.js
    │   ├── create_page.js
    │   ├── update_page.js
    │   ├── append_blocks.js
    │   ├── list_databases.js
    │   ├── query_database.js
    │   └── create_database_entry.js
    ├── prompts/
    │   └── system.md
    └── README.md
```

**manifest.json**:
```json
{
  "name": "notion",
  "version": "1.0.0",
  "description": "Documentation, knowledge bases, and structured information via Notion",
  "model": "haiku",
  "tools": [
    "search",
    "get_page",
    "create_page",
    "update_page",
    "append_blocks",
    "list_databases",
    "query_database",
    "create_database_entry"
  ],
  "requires": {
    "env": ["NOTION_API_KEY"],
    "dependencies": ["@notionhq/client"]
  },
  "permissions": {
    "accept": ["search", "get_*", "list_*", "query_*"],
    "prompt": ["create_*", "update_*", "append_*"],
    "reject": ["delete_*"]
  }
}
```

**Daydream Integration**:
```javascript
// In Daydream Step 5
async function updateDocumentation(state) {
  const notion = await loadSkill('notion');

  // Find docs that reference changed code
  const changedFiles = state.changesFromYesterday;
  const relatedDocs = await notion.execute('search', {
    query: changedFiles.map(f => f.name).join(' OR ')
  });

  for (const doc of relatedDocs) {
    // Flag for review
    await notion.execute('append_blocks', {
      pageId: doc.id,
      blocks: [{
        type: 'callout',
        content: `Review needed: Related code changed on ${state.date}`
      }]
    });
    state.report.docsReviewed.push(doc);
  }

  return state;
}
```

---

### 5. Stripe Skill

```
skills/
└── stripe/
    ├── manifest.json
    ├── index.js
    ├── tools/
    │   ├── list_customers.js
    │   ├── get_customer.js
    │   ├── list_subscriptions.js
    │   ├── get_subscription.js
    │   ├── list_invoices.js
    │   ├── create_refund.js
    │   ├── get_balance.js
    │   ├── get_mrr.js
    │   └── cancel_subscription.js
    ├── prompts/
    │   └── system.md
    └── README.md
```

**manifest.json**:
```json
{
  "name": "stripe",
  "version": "1.0.0",
  "description": "Payment processing, subscriptions, billing, and revenue analytics via Stripe",
  "model": "sonnet",
  "tools": [
    "list_customers",
    "get_customer",
    "list_subscriptions",
    "get_subscription",
    "list_invoices",
    "create_refund",
    "get_balance",
    "get_mrr",
    "cancel_subscription"
  ],
  "requires": {
    "env": ["STRIPE_API_KEY"],
    "dependencies": ["stripe"]
  },
  "permissions": {
    "accept": ["list_*", "get_*"],
    "prompt": ["create_*", "update_*", "cancel_*"],
    "reject": ["delete_*"]
  }
}
```

**Security Considerations**:
- Never expose full card numbers or tokens
- Require explicit confirmation for refunds > $100
- Require explicit confirmation for subscription cancellations
- Log all financial operations for audit trail

---

### 6. Render Skill

```
skills/
└── render/
    ├── manifest.json
    ├── index.js
    ├── tools/
    │   ├── list_services.js
    │   ├── get_service.js
    │   ├── get_service_logs.js
    │   ├── deploy_service.js
    │   ├── restart_service.js
    │   ├── scale_service.js
    │   ├── list_deploys.js
    │   ├── get_env_vars.js
    │   └── update_env_vars.js
    ├── prompts/
    │   └── system.md
    └── README.md
```

**manifest.json**:
```json
{
  "name": "render",
  "version": "1.0.0",
  "description": "Cloud infrastructure, deployments, and service management via Render",
  "model": "haiku",
  "tools": [
    "list_services",
    "get_service",
    "get_service_logs",
    "deploy_service",
    "restart_service",
    "scale_service",
    "list_deploys",
    "get_env_vars",
    "update_env_vars"
  ],
  "requires": {
    "env": ["RENDER_API_KEY"],
    "dependencies": ["axios"]
  },
  "permissions": {
    "accept": ["list_*", "get_*"],
    "prompt": ["deploy_*", "scale_*", "restart_*", "update_*"],
    "reject": ["delete_*", "suspend_*"]
  }
}
```

**Safety Considerations**:
- Always confirm destructive operations
- Extra caution with production services
- Require explicit "production" mention for prod deploys
- Provide rollback instructions for every deployment

---

## Integration with Clawdbot

### Skill Loading

Skills are loaded from `~/clawd/skills/` and automatically exposed to the agent:

```javascript
// In Clawdbot
class SkillLoader {
  constructor() {
    this.skillsPath = path.join(os.homedir(), 'clawd', 'skills');
    this.loadedSkills = new Map();
  }

  async loadSkill(name) {
    const skillPath = path.join(this.skillsPath, name);
    const manifest = require(path.join(skillPath, 'manifest.json'));

    // Validate environment requirements
    for (const envVar of manifest.requires.env) {
      if (!process.env[envVar]) {
        throw new Error(`Missing required env var: ${envVar}`);
      }
    }

    // Load tools
    const tools = {};
    for (const toolName of manifest.tools) {
      const tool = require(path.join(skillPath, 'tools', `${toolName}.js`));
      tools[toolName] = tool;
    }

    // Load system prompt
    const promptPath = path.join(skillPath, 'prompts', 'system.md');
    const systemPrompt = fs.existsSync(promptPath)
      ? fs.readFileSync(promptPath, 'utf-8')
      : null;

    this.loadedSkills.set(name, { manifest, tools, systemPrompt });
    return this.loadedSkills.get(name);
  }

  async executeSkillTool(skillName, toolName, params, context) {
    const skill = this.loadedSkills.get(skillName);
    if (!skill) {
      throw new Error(`Skill not loaded: ${skillName}`);
    }

    const tool = skill.tools[toolName];
    if (!tool) {
      throw new Error(`Tool not found: ${toolName} in ${skillName}`);
    }

    // Check permissions
    const permission = this.checkPermission(skill.manifest, toolName);
    if (permission === 'reject') {
      throw new Error(`Tool ${toolName} is not allowed`);
    }
    if (permission === 'prompt') {
      // Require user confirmation
      const confirmed = await context.confirmAction(
        `Execute ${skillName}.${toolName}?`,
        params
      );
      if (!confirmed) {
        return { cancelled: true };
      }
    }

    return await tool.execute(params, context);
  }
}
```

### Environment Variables

All skills read from the standard `.env` file:

```bash
# Sentry
SENTRY_API_KEY=your_sentry_api_key
SENTRY_ORG_SLUG=your_org_slug

# GitHub
GITHUB_TOKEN=ghp_your_token

# Linear
LINEAR_API_KEY=lin_api_your_key

# Notion
NOTION_API_KEY=secret_your_key

# Stripe
STRIPE_API_KEY=sk_live_your_key

# Render
RENDER_API_KEY=rnd_your_key
```

### Skill Discovery

Clawdbot automatically discovers available skills on startup:

```javascript
async function discoverSkills() {
  const skillsDir = path.join(os.homedir(), 'clawd', 'skills');
  const skills = [];

  for (const entry of await fs.readdir(skillsDir, { withFileTypes: true })) {
    if (entry.isDirectory()) {
      const manifestPath = path.join(skillsDir, entry.name, 'manifest.json');
      if (await fs.exists(manifestPath)) {
        const manifest = require(manifestPath);
        skills.push({
          name: manifest.name,
          description: manifest.description,
          tools: manifest.tools,
          available: checkEnvRequirements(manifest.requires.env)
        });
      }
    }
  }

  return skills;
}
```

---

## Migration Checklist

### For Each Skill:

- [ ] Create skill directory structure
- [ ] Write manifest.json with all metadata
- [ ] Port system prompt from `.claude/agents/{name}.md` to `prompts/system.md`
- [ ] Implement each tool with proper error handling
- [ ] Add input validation for all parameters
- [ ] Add categorized error responses
- [ ] Test each tool independently
- [ ] Document tool usage in README.md
- [ ] Add to skill discovery
- [ ] Test integration with Clawdbot

### Testing Protocol:

1. **Unit Tests**: Each tool function works correctly
2. **Integration Tests**: Skill loads and tools execute properly
3. **Permission Tests**: Accept/prompt/reject work as configured
4. **Error Tests**: All error paths return appropriate messages
5. **E2E Tests**: Full workflow from user request to result

---

## Rollout Plan

### Phase 1: Foundation (Week 1)
- Set up `~/clawd/skills/` directory structure
- Create skill loader infrastructure
- Migrate Sentry skill (highest priority for Daydream)

### Phase 2: Core Development (Week 2)
- Migrate GitHub skill
- Migrate Linear skill
- Test Daydream integration with Sentry + Linear

### Phase 3: Documentation & Business (Week 3)
- Migrate Notion skill
- Migrate Stripe skill
- Full Daydream cycle testing

### Phase 4: Infrastructure (Week 4)
- Migrate Render skill
- Complete integration testing
- Documentation and cleanup

---

## Notes

### Differences from Current Architecture

1. **Explicit Tool Definitions**: Current agents use wildcard patterns (`stripe_*`). Skills require explicit tool implementations.

2. **Portable Format**: Skills can be shared and version-controlled independently from the main codebase.

3. **Environment Isolation**: Each skill declares its own environment requirements.

4. **Structured Permissions**: Permission rules are machine-readable JSON rather than markdown comments.

### Backward Compatibility

During migration, both systems can coexist:
- Claude Code subagents continue working via `.claude/agents/`
- Clawdbot skills gradually replace functionality
- MCP tools remain available through existing `mcp_servers/` infrastructure

### Future Enhancements

- **Skill Marketplace**: Share skills across projects
- **Version Management**: Semantic versioning for skill updates
- **Dependency Resolution**: Skills that depend on other skills
- **Hot Reload**: Update skills without restarting Clawdbot
- **Metrics & Monitoring**: Track skill usage and performance
