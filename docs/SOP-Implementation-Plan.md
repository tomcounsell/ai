# Standard Operating Procedures (SOP) Implementation Plan

**Created**: 2025-11-19
**Updated**: 2026-01-19
**Status**: Phase 1 Complete (9 SOPs Created)
**Goal**: Structure all agentic skills with formal SOPs

---

## Executive Summary

Every agentic skill in our system should be derived from a structured Standard Operating Procedure (SOP). This provides:
- ✅ Versioned, git-tracked workflows
- ✅ Clear MUST/SHOULD/MAY constraints
- ✅ Parameterized, reusable procedures
- ✅ Team-editable without code changes
- ✅ AI-assisted generation and improvement

**Framework**: [Strands Agent-SOP](https://github.com/strands-agents/agent-sop)
**Standard**: RFC 2119 constraint levels (MUST/SHOULD/MAY)
**Format**: Markdown files with YAML frontmatter

---

## SOP Hierarchy

### Current State (9 SOPs Implemented)

```
sops/
├── templates/
│   └── sop-template.md              ✅ Created
│
├── routing/                         ✅ All 3 complete
│   ├── task-analyzer.sop.md         ✅ Created
│   ├── mcp-library-selector.sop.md  ✅ Created
│   └── coding-agent-router.sop.md   ✅ Created
│
├── subagents/                       ⏳ 2 of ~18 planned
│   ├── stripe/
│   │   └── payment-processing.sop.md ✅ Created
│   └── sentry/
│       └── error-investigation.sop.md ✅ Created
│
├── tools/                           ⏳ 2 of 5 planned
│   ├── search-tool.sop.md           ✅ Created
│   └── code-execution.sop.md        ✅ Created
│
├── operations/                      ⏳ 1 of 5 planned
│   └── daydream/
│       └── daily-health-check.sop.md ✅ Created
│
└── workflows/                       ⏳ 1 of 4 planned
    └── feature-development.sop.md   ✅ Created
```

### Planned SOPs (Not Yet Created)

```
sops/
├── subagents/
│   ├── stripe/
│   │   ├── subscription-management.sop.md
│   │   └── refund-handling.sop.md
│   ├── sentry/
│   │   ├── performance-analysis.sop.md
│   │   └── alert-triage.sop.md
│   ├── github/
│   │   ├── pr-review.sop.md
│   │   ├── issue-triage.sop.md
│   │   └── code-collaboration.sop.md
│   ├── render/
│   │   ├── deployment.sop.md
│   │   ├── infrastructure-monitoring.sop.md
│   │   └── scaling-operations.sop.md
│   ├── notion/
│   │   ├── knowledge-search.sop.md
│   │   ├── documentation-creation.sop.md
│   │   └── database-management.sop.md
│   └── linear/
│       ├── issue-management.sop.md
│       ├── sprint-planning.sop.md
│       └── roadmap-coordination.sop.md
│
├── tools/
│   ├── image-generation.sop.md
│   ├── image-analysis.sop.md
│   └── knowledge-search.sop.md
│
├── operations/
│   ├── daydream/
│   │   ├── code-maintenance.sop.md
│   │   └── performance-optimization.sop.md
│   └── monitoring/
│       ├── resource-tracking.sop.md
│       └── alert-handling.sop.md
│
└── workflows/
    ├── bug-investigation.sop.md
    ├── code-review.sop.md
    └── deployment.sop.md
```

---

## SOP Template

Every SOP follows this standard structure:

```markdown
# [Workflow Name] SOP

**Version**: 1.0.0
**Last Updated**: YYYY-MM-DD
**Owner**: [Team/Person]
**Status**: [Draft|Active|Deprecated]

## Overview
Brief description of what this SOP accomplishes and when to use it.

## Prerequisites
- Any required setup or conditions
- Tools that must be available
- Permissions needed

## Parameters

### Required
- **param_name** (type): Description and constraints
  - Example: `customer_id`
  - Type: `string`
  - Format: `cus_[a-zA-Z0-9]+`

### Optional
- **param_name** (type): Description with default value
  - Default: `value`

## Steps

### 1. [Step Name]
**Purpose**: Why this step is necessary

**Actions**:
- MUST perform required action
- SHOULD perform recommended action
- MAY perform optional action

**Validation**:
- How to verify this step succeeded

**Error Handling**:
- What to do if this step fails

### 2. [Next Step]
[Continue pattern...]

## Success Criteria
How to know the workflow completed successfully.

## Error Recovery
Common errors and how to recover:
- **Error Type**: Recovery procedure
- **Error Type**: Recovery procedure

## Examples

### Example 1: [Use Case]
\`\`\`
Input:
  param1: value1
  param2: value2

Expected Output:
  result: success
  data: {...}
\`\`\`

## Related SOPs
- [Related SOP](path/to/sop.md)
- [Parent SOP](path/to/parent.md)

## Version History
- v1.0.0 (YYYY-MM-DD): Initial version
```

---

## SOP Categories

### 1. Routing SOPs (Priority: CRITICAL)

#### Task Analyzer SOP
**Purpose**: Analyze incoming requests to determine required capabilities
**File**: `sops/routing/task-analyzer.sop.md`

**Parameters**:
- `user_query` (MUST): Raw user request
- `conversation_context` (SHOULD): Recent conversation history
- `user_preferences` (MAY): User-specific settings

**Steps**:
1. Parse user intent
2. Extract required capabilities
3. Identify domain (payment, code, error, etc.)
4. Assess complexity level
5. Determine interaction mode (interactive/autonomous)

**Output**: Structured task description for routing

---

#### MCP Library Selector SOP
**Purpose**: Select MCP servers based on task requirements and auth status
**File**: `sops/routing/mcp-library-selector.sop.md`

**Parameters**:
- `required_capabilities` (MUST): List of needed capabilities
- `auth_status_check` (MUST): Whether to verify auth
- `min_servers` (MAY): Minimum servers to load (default: 1)

**Steps**:
1. Query MCP Library for matching servers
2. Filter by `auth_status == "ready"`
3. Rank by relevance score
4. Select minimal set covering capabilities
5. Alert user if critical MCPs need auth

**Output**: List of MCP server IDs to load

---

#### Coding Agent Router SOP
**Purpose**: Route coding tasks to optimal agent (Gemini CLI or Claude Code)
**File**: `sops/routing/coding-agent-router.sop.md`

**Parameters**:
- `task_type` (MUST): `interactive | autonomous | batch`
- `file_count` (SHOULD): Number of files involved
- `requires_mcp` (MUST): Boolean, needs MCP tools
- `complexity` (SHOULD): `simple | medium | complex`

**Steps**:
1. Analyze task characteristics
2. Check MCP tool requirements
3. Assess complexity and file scope
4. Apply routing rules (MUST use Gemini if autonomous + simple + no MCP)
5. Track agent selection for cost analysis

**Output**: Selected agent (gemini_cli | claude_code)

---

### 2. Subagent SOPs (Priority: HIGH)

#### Example: Stripe Payment Processing SOP
**File**: `sops/subagents/stripe/payment-processing.sop.md`

**Parameters**:
- `operation` (MUST): `charge | refund | subscription`
- `amount` (SHOULD): Numeric value in cents
- `customer_id` (MUST): Stripe customer ID
- `idempotency_key` (SHOULD): For duplicate prevention

**Steps**:
1. **Validate Input**
   - MUST verify customer_id exists in Stripe
   - SHOULD check amount is within limits
   - MUST validate payment method is attached
   - MAY check fraud detection score

2. **Execute Operation**
   - MUST use appropriate Stripe MCP tool
   - MUST include idempotency_key
   - MUST log transaction details
   - SHOULD send user notification

3. **Handle Errors**
   - MUST catch and categorize Stripe errors
   - SHOULD suggest recovery steps
   - MAY retry with exponential backoff
   - MUST NOT retry payment failures

**Success Criteria**:
- Payment processed successfully
- Transaction logged to database
- User notification sent

---

### 3. Tool SOPs (Priority: MEDIUM)

#### Search Tool SOP
**File**: `sops/tools/search-tool.sop.md`

**Parameters**:
- `query` (MUST): Search query string
- `search_depth` (SHOULD): `quick | standard | comprehensive`
- `domain_filter` (MAY): Restrict to specific domains

**Steps**:
1. Sanitize and validate query
2. Check cache for recent identical queries
3. Execute Perplexity API search
4. Parse and structure results
5. Cache results with TTL

---

### 4. Operational SOPs (Priority: HIGH)

#### Daydream Daily Health Check SOP
**File**: `sops/operations/daydream/daily-health-check.sop.md`

**Parameters**:
- `check_time` (SHOULD): Timestamp of check
- `severity_threshold` (MAY): `error | warning | info`

**Steps**:
1. **Gather Metrics**
   - MUST collect error rates from Sentry
   - MUST check resource utilization
   - SHOULD review performance trends
   - MAY analyze user activity patterns

2. **Identify Issues**
   - MUST flag errors above threshold
   - SHOULD categorize by severity
   - MAY predict future issues

3. **Generate Report**
   - MUST create summary of findings
   - SHOULD suggest remediation steps
   - MAY create Linear issues for high-priority items
   - MUST NOT create duplicate issues

4. **Execute Safe Fixes**
   - MAY auto-fix trivial issues (linting, formatting)
   - MUST NOT deploy code changes without approval
   - SHOULD queue complex fixes for human review

**Safety Constraints**:
- MUST NOT modify production code
- MUST NOT change database schema
- MUST NOT alter security configurations

---

## Implementation Roadmap

### Week 1: Foundation & Routing SOPs ✅ COMPLETE
**Goal**: Set up SOP infrastructure and critical routing

#### Tasks
1. ~~Install `strands-agents-sops` package~~ (using custom template instead)
2. ✅ Create SOP directory structure
3. ✅ Write routing SOPs:
   - task-analyzer.sop.md ✅
   - mcp-library-selector.sop.md ✅
   - coding-agent-router.sop.md ✅
4. ⏳ Integrate with existing routing code (partial - SOPs exist, routing uses intent module)
5. ⏳ Test routing accuracy improvements

**Success Criteria**:
- [x] SOP directory structure created
- [x] All 3 routing SOPs written
- [ ] All routing decisions use SOPs (intent module created, not fully integrated)
- [ ] Routing accuracy >90%
- [x] Team can edit routing rules via markdown

---

### Week 2: Stripe Subagent SOPs (POC) ⏳ PARTIAL
**Goal**: Prove value with one complete subagent

#### Tasks
1. Write Stripe SOPs:
   - payment-processing.sop.md ✅
   - subscription-management.sop.md ⏳
   - refund-handling.sop.md ⏳
2. ⏳ Refactor StripeSubagent to use SOPs
3. ⏳ Compare with keyword-based detection
4. ⏳ Measure context usage reduction

**Success Criteria**:
- [x] Stripe payment-processing SOP written
- [ ] All 3 Stripe SOPs complete
- [ ] Stripe subagent fully SOP-driven
- [ ] >15% context token reduction
- [ ] >95% routing accuracy for Stripe tasks
- [x] Team can modify Stripe workflows without code

---

### Week 3: All Subagent SOPs ⏳ PARTIAL
**Goal**: Complete SOP coverage for all 6 subagents

#### Tasks
1. Write remaining subagent SOPs (5 x 3-4 SOPs each)
   - Sentry: error-investigation.sop.md ✅ (1 of 3)
   - GitHub: 0 of 3 ⏳
   - Render: 0 of 3 ⏳
   - Notion: 0 of 3 ⏳
   - Linear: 0 of 3 ⏳
2. ⏳ Refactor all subagents to use SOPs
3. ⏳ Remove keyword detection code
4. ⏳ Set up MCP server for SOP discovery

**Success Criteria**:
- [x] Sentry error-investigation SOP written
- [ ] All subagents have SOPs (2 of ~18 complete)
- [ ] Keyword detection deprecated
- [ ] SOPs accessible via MCP
- [x] Team authoring guide complete (template exists)

---

### Week 4: Tool & Operational SOPs ⏳ PARTIAL
**Goal**: Extend SOPs beyond subagents

#### Tasks
1. Write tool SOPs (5 major tools)
   - search-tool.sop.md ✅
   - code-execution.sop.md ✅
   - image-generation.sop.md ⏳
   - image-analysis.sop.md ⏳
   - knowledge-search.sop.md ⏳
2. Write daydream operational SOPs
   - daily-health-check.sop.md ✅
   - code-maintenance.sop.md ⏳
   - performance-optimization.sop.md ⏳
3. Create workflow SOPs (feature dev, bug fix, etc.)
   - feature-development.sop.md ✅
   - bug-investigation.sop.md ⏳
   - code-review.sop.md ⏳
   - deployment.sop.md ⏳
4. ⏳ Set up SOP validation and testing

**Success Criteria**:
- [x] Tool SOPs started (2 of 5 complete)
- [x] Daydream SOPs started (1 of 3 complete)
- [x] Workflow SOPs started (1 of 4 complete)
- [ ] All tools have SOPs
- [ ] Daydream runs via SOPs
- [ ] Common workflows documented
- [ ] SOP CI/CD pipeline active

---

## SOP Authoring Guide

### Creating a New SOP

1. **Start from Template**
   ```bash
   cp sops/templates/sop-template.md sops/category/new-workflow.sop.md
   ```

2. **Fill in Metadata**
   - Version (start at 1.0.0)
   - Owner
   - Status (Draft initially)

3. **Write Overview**
   - What does this workflow accomplish?
   - When should it be used?
   - What problem does it solve?

4. **Define Parameters**
   - Required parameters (MUST have)
   - Optional parameters with defaults
   - Type constraints and validation

5. **Document Steps**
   - Use RFC 2119 keywords (MUST/SHOULD/MAY)
   - Include validation criteria
   - Add error handling

6. **Add Examples**
   - At least one complete example
   - Show edge cases if relevant

7. **Review & Test**
   - Have AI generate test cases
   - Run through workflow manually
   - Get team review

8. **Activate**
   - Change status to Active
   - Add to SOP index
   - Update related SOPs

---

### AI-Assisted SOP Generation

Use the Agent-SOP toolkit to generate SOPs:

```bash
# Generate SOP from existing workflow description
strands-agents-sops generate \
  --description "Process Stripe payment with validation" \
  --output sops/subagents/stripe/payment-processing.sop.md
```

Or use Claude/GPT to generate:
```
Prompt: "Create an RFC 2119-compliant SOP for [workflow description].
Include parameters, steps with MUST/SHOULD/MAY constraints,
error handling, and examples."
```

---

## SOP Maintenance

### Monthly Review Cycle
1. Review all Active SOPs
2. Check for outdated steps
3. Update examples if tools changed
4. Increment version if modified
5. Update related SOPs

### Version Control
- Use semantic versioning (MAJOR.MINOR.PATCH)
- MAJOR: Breaking changes to parameters or steps
- MINOR: New optional parameters or steps
- PATCH: Clarifications, examples, typo fixes

### Deprecation Process
1. Mark SOP as Deprecated in metadata
2. Link to replacement SOP
3. Keep for 3 months before removal
4. Update all references to new SOP

---

## Success Metrics

### Coverage Metrics
- **Target**: 100% of subagents have SOPs
- **Target**: 100% of tools have SOPs
- **Target**: 100% of operational workflows have SOPs

### Quality Metrics
- **SOP Compliance**: 95% of executions follow SOPs correctly
- **Error Rate**: <5% SOP execution failures
- **Team Velocity**: 60% faster workflow authoring

### Business Impact
- **Routing Accuracy**: >90% correct agent/subagent selection
- **Context Efficiency**: 20-30% reduction in token usage
- **Team Productivity**: Non-engineers can modify workflows

---

## Tools & Resources

### SOP Framework
- **Package**: `strands-agents-sops`
- **Docs**: https://github.com/strands-agents/agent-sop
- **Installation**: `pip install strands-agents-sops`

### Related Documentation
- [Agent-SOP Evaluation](architecture/agent-sop-evaluation.md)
- [Subagent PRDs](subagents/README.md)
- [MCP Library Requirements](MCP-Library-Requirements.md)
- [Skills vs Subagents Analysis](architecture/skills-vs-subagents-analysis.md)

### SOP Index
(To be generated automatically)
```bash
# Generate index of all SOPs
strands-agents-sops index --output docs/SOP-INDEX.md
```

---

## Next Actions

1. **Get Approval** - Review this plan with team
2. **Install Framework** - Set up strands-agents-sops
3. **Create Templates** - Set up SOP template directory
4. **Week 1 Kickoff** - Start with routing SOPs
5. **POC with Stripe** - Prove value with one subagent
6. **Scale to All** - Expand across all skills

---

**Status**: Planning Complete - Ready for Implementation
**Owner**: TBD
**Timeline**: 4 weeks
**Expected ROI**: 60-70% reduction in prompt engineering effort
