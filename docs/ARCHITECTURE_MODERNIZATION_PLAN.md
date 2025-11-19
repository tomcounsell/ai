# Architecture Modernization Plan

**Created**: 2025-11-19
**Status**: Planning Phase
**Completion Target**: 2-3 week effort

---

## Executive Summary

Comprehensive plan to modernize and align all architectural documentation with recent decisions:
- MCP Library & Session Management
- Multi-model agent router (Gemini CLI + Claude Code)
- Claude Code native subagents approach
- Current system completion status (62.5%)

### Key Goals
1. ✅ Eliminate outdated information
2. ✅ Align all docs with current architecture decisions
3. ✅ Create consistent documentation standards
4. ✅ Fill documentation gaps
5. ✅ Establish maintenance processes

---

## Documentation Inventory

### 📊 Current State Analysis

**Total Documents**: 33 markdown files
**Last Major Update**: August 2025 (most docs)
**Recent Additions**:
- MCP Library Requirements (Oct 27)
- Gemini CLI Integration (Nov 19)
- Skills vs Subagents Analysis (Nov 19)

### Documentation Categories

#### 1. Root Level (5 files)
```
✅ CLAUDE.md          - Up to date, comprehensive
⚠️  README.md          - References non-existent docs-rebuild/
✅ GEMINI.md          - Up to date
❓ essays/            - Not reviewed yet
```

#### 2. Architecture Docs (8 files)
```
✅ gemini-cli-integration-analysis.md     - NEW (Nov 19)
✅ skills-vs-subagents-analysis.md        - NEW (Nov 19)
✅ subagent-improvements.md               - NEW (Nov 19)
✅ subagent-mcp-system.md                 - Recent (Nov 18)
⚠️  unified-agent-design.md                - OLD (Aug 28) - Needs review
⚠️  system-overview.md                     - OLD (Aug 28) - Needs major update
⚠️  mcp-integration.md                     - OLD (Aug 28) - Pre-subagent decisions
```

#### 3. Project Management (7 files)
```
⚠️  PRD-AI-System-Rebuild.md              - OLD (Aug 28) - Missing recent features
✅ Feature-Prioritization-Matrix.md       - CURRENT (Oct 27)
✅ MCP-Library-Requirements.md            - NEW (Oct 27)
⚠️  SYSTEM_STATUS.md                      - STALE (Sep 5) - 62.5% completion outdated
⚠️  PROGRESS_SUMMARY.md                   - STALE (Sep 5)
⚠️  TODO.md                                - STALE (Sep 5) - Phase plans outdated
⚠️  User-Journey-Maps.md                  - OLD (Aug 28)
```

#### 4. Component Docs (3 files)
```
⚠️  message-processing.md                 - OLD (Aug 28)
⚠️  resource-monitoring.md                - OLD (Aug 28)
⚠️  telegram-integration.md               - OLD (Aug 28)
```

#### 5. Operations (2 files)
```
⚠️  daydream-system.md                    - OLD (Aug 28) - Needs integration with Gemini CLI
⚠️  monitoring.md                         - OLD (Aug 28)
```

#### 6. Subagents (7 files)
```
✅ All 6 subagent PRDs                     - Recent (Nov 18)
✅ subagents/README.md                     - Recent
```

#### 7. Testing & Tools (3 files)
```
⚠️  testing-strategy.md                   - OLD (Aug 28)
⚠️  quality-standards.md                  - OLD (Aug 28)
⚠️  tool-architecture.md                  - OLD (Aug 28)
```

### Priority Classification

**🔴 CRITICAL - Update Immediately**
1. README.md - Wrong documentation path reference
2. SYSTEM_STATUS.md - Stale completion metrics
3. system-overview.md - Core architecture doc outdated
4. PRD-AI-System-Rebuild.md - Missing major features

**🟡 HIGH - Update Soon (Week 1-2)**
5. mcp-integration.md - Pre-subagent/MCP-Library decisions
6. TODO.md - Phase plans outdated
7. unified-agent-design.md - May conflict with current impl
8. PROGRESS_SUMMARY.md - Stale progress tracking
9. daydream-system.md - Missing Gemini CLI integration

**🟢 MEDIUM - Update Later (Week 2-3)**
10. Component docs (3 files) - Implementation may have changed
11. Operations docs (monitoring.md)
12. Testing docs - May need AI judge updates
13. User-Journey-Maps.md - May need new flows

**⚪ LOW - Review Only**
14. Security-Compliance-Requirements.md
15. Tool architecture docs
16. Essays directory

---

## Key Issues Identified

### 1. **Architectural Inconsistencies**

**Issue**: Pre-subagent MCP integration docs don't reflect current approach
- `mcp-integration.md` (Aug 28) written before subagent decision
- Missing MCP Library & Session Management integration
- No multi-model agent router documentation

**Impact**: HIGH - Core architecture understanding

### 2. **Missing Recent Features**

**Features Not Documented**:
- ❌ MCP Library catalog system
- ❌ Auth status tracking for MCPs
- ❌ Task-based MCP selection logic
- ❌ Gemini CLI integration points
- ❌ Multi-model agent routing logic
- ✅ Claude Code subagents (documented in new files)

**Impact**: HIGH - Feature discoverability

### 3. **Stale Progress Tracking**

**Issue**: Progress docs from Sep 5 (2+ months old)
- SYSTEM_STATUS says 62.5% complete
- TODO has Phase 6-8 that may have changed
- No recent completion updates

**Impact**: MEDIUM - Project visibility

### 4. **Reference Errors**

**Issue**: README.md points to non-existent `docs-rebuild/`
```markdown
See [`docs-rebuild/`](docs-rebuild/) for complete system documentation.
```
Should be: `docs/`

**Impact**: CRITICAL - User can't find docs

### 5. **Inconsistent Standards**

**Issue**: Mixed documentation styles
- Some docs have YAML frontmatter
- Some use different heading structures
- Inconsistent status indicators
- Different date formats

**Impact**: LOW - Readability

---

## Modernization Strategy

### Phase 1: Critical Fixes (Days 1-3)

#### 1.1 Fix README.md
- Update docs path reference
- Add recent feature highlights
- Update system status
- Link to new architecture docs

#### 1.2 Update SYSTEM_STATUS.md
- Current completion percentage
- Recent accomplishments
- Architecture decisions summary
- Link to modernization plan

#### 1.3 Rewrite system-overview.md
- Integrate subagent architecture
- Add MCP Library section
- Include Gemini CLI integration
- Update with current tech stack

#### 1.4 Update PRD-AI-System-Rebuild.md
- Add MCP Library feature
- Add Gemini CLI integration
- Update completion status
- Revise feature priorities

### Phase 2: Architecture Alignment (Days 4-7)

#### 2.1 Update mcp-integration.md
- Reflect subagent-based approach
- Document MCP Library integration
- Add session management flow
- Include auth tracking system

#### 2.2 Review unified-agent-design.md
- Verify alignment with current implementation
- Add multi-model router section
- Update with PydanticAI patterns
- Link to subagent docs

#### 2.3 Update daydream-system.md
- Integrate Gemini CLI for background tasks
- Document agent routing logic
- Add cost optimization notes
- Update implementation examples

#### 2.4 Create New: Multi-Model Router Design
- Document routing logic
- Cost optimization strategies
- Failover mechanisms
- Implementation guidelines

### Phase 3: Component & Operations (Days 8-11)

#### 3.1 Review Component Docs
- message-processing.md
- resource-monitoring.md
- telegram-integration.md
- Verify implementation matches docs
- Update if implementation has evolved

#### 3.2 Update Operations Docs
- monitoring.md
- Add multi-agent monitoring
- Include cost tracking
- Update health check logic

#### 3.3 Update Testing Docs
- testing-strategy.md
- quality-standards.md
- Integrate AI judge approach
- Update with recent test patterns

### Phase 4: Progress & Planning (Days 12-14)

#### 4.1 Update TODO.md
- Archive completed phases
- Update Phase 6-8 based on new architecture
- Add MCP Library implementation tasks
- Add Gemini CLI integration tasks

#### 4.2 Update PROGRESS_SUMMARY.md
- Current accomplishments
- Recent architecture decisions
- Next phase priorities
- Updated metrics

#### 4.3 Review User-Journey-Maps.md
- Add MCP Library selection flow
- Add multi-model routing experience
- Update with current UX

### Phase 5: Standards & Maintenance (Days 15-21)

#### 5.1 Create Documentation Standards
- Heading structure
- Status indicators
- Date formats
- Code example style
- Cross-referencing conventions

#### 5.2 Create Maintenance Process
- Update schedule (monthly reviews)
- Ownership model
- Review checklist
- Version control practices

#### 5.3 Final Review
- Cross-reference verification
- Broken link checking
- Consistency audit
- Completeness check

---

## Documentation Standards (To Be Established)

### Document Header Template
```markdown
# [Document Title]

**Last Updated**: YYYY-MM-DD
**Status**: [Draft|Review|Current|Deprecated]
**Relates To**: [Related documents]
**Owners**: [Responsible parties]

## Quick Links
- Architecture: [relevant links]
- Implementation: [code references]
- Related: [related docs]
```

### Status Indicators
- ✅ Implemented & Working
- 🚧 In Progress
- 📋 Planned
- ⚠️  Needs Update
- 🔴 Blocking Issue
- ❌ Deprecated

### Cross-Reference Format
Use relative paths:
```markdown
See [MCP Integration](../architecture/mcp-integration.md)
Implemented in `mcp_servers/base.py:123`
```

### Date Format
Use ISO 8601: `YYYY-MM-DD`

### Code Examples
Always include:
- Language identifier
- Brief description
- Context (file location if real code)

---

## Success Metrics

### Completion Criteria
- [ ] Zero broken document references
- [ ] All "OLD" docs updated to "CURRENT"
- [ ] 100% architectural consistency
- [ ] All recent features documented
- [ ] Standards doc created
- [ ] Maintenance process established

### Quality Metrics
- **Accuracy**: All docs match current implementation
- **Completeness**: No missing features
- **Consistency**: Unified style and structure
- **Maintainability**: Clear update process
- **Usability**: Easy navigation and discovery

---

## Resource Requirements

### Time Estimate
- **Phase 1 (Critical)**: 3 days
- **Phase 2 (Architecture)**: 4 days
- **Phase 3 (Components)**: 4 days
- **Phase 4 (Progress)**: 3 days
- **Phase 5 (Standards)**: 7 days
- **Total**: 21 days (3 weeks)

### Effort Distribution
- Review & Analysis: 30%
- Writing & Updates: 50%
- Cross-checking: 15%
- Standards Creation: 5%

---

## Risk Mitigation

### Risk 1: Documentation Drift
**Risk**: Docs get outdated again
**Mitigation**:
- Establish monthly review cycle
- Link docs to code with tests
- Make doc updates part of feature PRs

### Risk 2: Inconsistent Updates
**Risk**: Some docs updated, others missed
**Mitigation**:
- Use this checklist as tracking
- Cross-reference audit at end
- Automated link checking

### Risk 3: Implementation Mismatch
**Risk**: Docs don't match actual code
**Mitigation**:
- Review code during doc update
- Include code references in docs
- Test examples from docs

---

## Implementation Checklist

### Critical Phase (Days 1-3)
- [ ] Fix README.md docs path
- [ ] Update SYSTEM_STATUS.md with current state
- [ ] Rewrite system-overview.md
- [ ] Update PRD with recent features
- [ ] Create this modernization plan

### Architecture Phase (Days 4-7)
- [ ] Update mcp-integration.md for subagents
- [ ] Review unified-agent-design.md
- [ ] Update daydream-system.md with Gemini CLI
- [ ] Create multi-model-router-design.md
- [ ] Update tool-architecture.md if needed

### Components Phase (Days 8-11)
- [ ] Review all component docs (3 files)
- [ ] Update operations docs (2 files)
- [ ] Update testing docs (2 files)
- [ ] Verify implementation alignment

### Progress Phase (Days 12-14)
- [ ] Update TODO.md with new phases
- [ ] Update PROGRESS_SUMMARY.md
- [ ] Review User-Journey-Maps.md
- [ ] Update Feature-Prioritization-Matrix if needed

### Standards Phase (Days 15-21)
- [ ] Create DOCUMENTATION_STANDARDS.md
- [ ] Create DOCUMENTATION_MAINTENANCE.md
- [ ] Run consistency audit
- [ ] Run link checker
- [ ] Final review pass
- [ ] Create completion report

---

## Next Steps

1. **Approve this plan** - Review and get sign-off
2. **Start Phase 1** - Begin critical fixes immediately
3. **Daily progress updates** - Track via git commits
4. **Weekly review** - Check progress against timeline
5. **Final audit** - Comprehensive review at end

---

## Appendix: Document Index

### By Category
**Architecture**: 8 docs
**Project Management**: 7 docs
**Components**: 3 docs
**Operations**: 2 docs
**Subagents**: 7 docs
**Testing & Tools**: 3 docs
**Root**: 5 docs

### By Status
**Current** ✅: 11 docs
**Needs Update** ⚠️: 19 docs
**Not Reviewed** ❓: 3 docs

### By Priority
**Critical** 🔴: 4 docs
**High** 🟡: 5 docs
**Medium** 🟢: 7 docs
**Low** ⚪: 3 docs
**Good** ✅: 11 docs
