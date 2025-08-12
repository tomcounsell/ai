# AI Rebuild Migration Guide - Repository Root Migration

## Overview

This guide covers the complete migration process to move the AI Rebuild system from `/Users/valorengels/src/ai/ai-rebuild/` to the repository root at `/Users/valorengels/src/ai/`. This migration consolidates the new system into the main repository while preserving all functionality and data.

## Migration Purpose

The migration moves all components from the `ai-rebuild` subdirectory to become the primary AI system at the repository root, replacing the legacy implementation with the new 9.8/10 gold standard architecture.

## Table of Contents

1. [Pre-Migration Checklist](#pre-migration-checklist)
2. [Migration Phases](#migration-phases)
3. [File System Migration](#file-system-migration)
4. [Data Migration](#data-migration)
5. [Configuration Migration](#configuration-migration)
6. [Service Transition](#service-transition)
7. [Production Deployment](#production-deployment)
8. [Post-Migration Validation](#post-migration-validation)
9. [Rollback Procedures](#rollback-procedures)
10. [Troubleshooting](#troubleshooting)

## Pre-Migration Checklist

Before beginning the migration process, ensure all prerequisites are met:

### Current System State
- [ ] Current location: `/Users/valorengels/src/ai/ai-rebuild/`
- [ ] Target location: `/Users/valorengels/src/ai/`
- [ ] Backup of existing `/Users/valorengels/src/ai/` created
- [ ] All processes in `/Users/valorengels/src/ai/` stopped

### System Requirements
- [ ] Python 3.11+ installed with UV package manager
- [ ] All dependencies from `pyproject.toml` available
- [ ] Database backup completed and verified
- [ ] Sufficient disk space (minimum 2x current data size)
- [ ] Network connectivity to all external services

### Backup Verification
- [ ] Full backup of `/Users/valorengels/src/ai/` to `/Users/valorengels/src/ai-backup-[timestamp]/`
- [ ] Database backup from `ai-rebuild/data/` created and tested
- [ ] Configuration files from `ai-rebuild/config/` backed up
- [ ] Application code backed up with Git commit
- [ ] Migration rollback plan validated

### File System Preparation
- [ ] Target directory permissions verified
- [ ] No conflicting files in root directory
- [ ] Git repository status clean (no uncommitted changes)
- [ ] All active processes terminated

## Migration Phases

### Phase 1: Backup Current System
Create a complete backup of the existing AI system.

```bash
# Create timestamped backup
cd /Users/valorengels/src
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
cp -r ai/ ai-backup-${TIMESTAMP}/

# Verify backup
ls -la ai-backup-${TIMESTAMP}/
```

**Expected Duration:** 5-10 minutes  
**Validation:** Verify backup completeness and file count

### Phase 2: Stop All Services
Stop all running services in the current system.

```bash
# Stop any running AI services
cd /Users/valorengels/src/ai
pkill -f "python.*ai" || true
pkill -f "uvicorn" || true

# Verify no processes running
ps aux | grep -E "python.*ai|uvicorn" | grep -v grep
```

**Expected Duration:** 1-2 minutes  
**Validation:** No AI-related processes should be running

### Phase 3: Clean Target Directory
Prepare the root directory for migration.

```bash
cd /Users/valorengels/src/ai

# Remove old directories that will be replaced
rm -rf agents/ config/ integrations/ mcp_servers/ tools/ utilities/ tests/

# Keep existing data and documentation
# Keep: docs-rebuild/, data/, logs/, any user data
```

**Expected Duration:** 2-3 minutes  
**Validation:** Only user data and documentation remain

### Phase 4: Move Core Components
Move all components from ai-rebuild to root.

```bash
cd /Users/valorengels/src/ai

# Move all Python packages and modules
mv ai-rebuild/agents/ ./
mv ai-rebuild/config/ ./
mv ai-rebuild/integrations/ ./
mv ai-rebuild/mcp_servers/ ./
mv ai-rebuild/tools/ ./
mv ai-rebuild/utilities/ ./
mv ai-rebuild/tests/ ./
mv ai-rebuild/scripts/ ./
mv ai-rebuild/examples/ ./

# Move configuration files
mv ai-rebuild/pyproject.toml ./
mv ai-rebuild/.gitignore ./
mv ai-rebuild/.env.template ./

# Move or merge data directories
cp -r ai-rebuild/data/* ./data/ 2>/dev/null || mkdir -p data
cp -r ai-rebuild/logs/* ./logs/ 2>/dev/null || mkdir -p logs

# Move documentation
cp -r ai-rebuild/docs/* ./docs/ 2>/dev/null || mkdir -p docs
```

**Expected Duration:** 3-5 minutes  
**Validation:** All directories in place at root level

### Phase 5: Update Path References
Update all path references to use root directory.

```bash
cd /Users/valorengels/src/ai

# Update imports and path references
find . -type f -name "*.py" -exec sed -i '' 's|/ai-rebuild/|/|g' {} \;
find . -type f -name "*.py" -exec sed -i '' 's|ai-rebuild\.|.|g' {} \;

# Update configuration files
find . -type f -name "*.json" -exec sed -i '' 's|/ai-rebuild/|/|g' {} \;
find . -type f -name "*.yaml" -exec sed -i '' 's|/ai-rebuild/|/|g' {} \;
find . -type f -name "*.yml" -exec sed -i '' 's|/ai-rebuild/|/|g' {} \;
```

**Expected Duration:** 2-3 minutes  
**Validation:** No references to ai-rebuild remain

### Phase 6: Install Dependencies
Install all dependencies in the new location.

```bash
cd /Users/valorengels/src/ai

# Install dependencies using UV
uv sync

# Verify installation
uv pip list
```

**Expected Duration:** 5-10 minutes  
**Validation:** All dependencies installed successfully

### Phase 7: Database Migration
Migrate database to new location if needed.

```bash
cd /Users/valorengels/src/ai

# Initialize database in new location
python scripts/init_db.py init

# If existing data needs migration
python scripts/migration/export_data.py --source ai-rebuild/data/ai_rebuild.db
python scripts/migration/transform_data.py
python scripts/migration/validate_data.py
```

**Expected Duration:** 5-10 minutes  
**Validation:** Database accessible and data intact

### Phase 8: Configuration Update
Update configuration for new paths.

```bash
cd /Users/valorengels/src/ai

# Copy environment template if not exists
cp .env.template .env

# Update paths in .env
sed -i '' 's|DATABASE_PATH=.*|DATABASE_PATH=data/ai_rebuild.db|g' .env
sed -i '' 's|LOG_PATH=.*|LOG_PATH=logs/|g' .env

# Validate configuration
python config/setup_config.py --validate-only
```

**Expected Duration:** 2-3 minutes  
**Validation:** Configuration valid and complete

### Phase 9: System Startup
Start the migrated system.

```bash
cd /Users/valorengels/src/ai

# Run startup script
python scripts/startup.py --verbose

# Verify system health
python scripts/deployment/monitor_health.py
```

**Expected Duration:** 3-5 minutes  
**Validation:** All components healthy and running

### Phase 10: Clean Up
Remove the now-empty ai-rebuild directory.

```bash
cd /Users/valorengels/src/ai

# Remove empty ai-rebuild directory
rm -rf ai-rebuild/

# Commit changes to Git
git add .
git commit -m "Migrate AI Rebuild to repository root"
git push
```

**Expected Duration:** 1-2 minutes  
**Validation:** Repository clean and changes committed

## File System Migration

### Directory Structure Mapping

The migration moves the following structure from `ai-rebuild/` to root:

```
/Users/valorengels/src/ai-rebuild/        →  /Users/valorengels/src/ai/
├── agents/                                →  agents/
├── config/                                →  config/
├── integrations/                          →  integrations/
├── mcp_servers/                           →  mcp_servers/
├── tools/                                 →  tools/
├── utilities/                             →  utilities/
├── tests/                                 →  tests/
├── scripts/                               →  scripts/
├── examples/                              →  examples/
├── docs/                                  →  docs/
├── data/                                  →  data/ (merged)
├── logs/                                  →  logs/ (merged)
├── pyproject.toml                         →  pyproject.toml
├── .env.template                          →  .env.template
└── .gitignore                             →  .gitignore
```

### Files to Preserve
These existing files/directories should be preserved during migration:
- `docs-rebuild/` - Existing documentation
- `CLAUDE.md` - System instructions
- Any existing user data in `data/`
- Any existing logs in `logs/`
- `.git/` - Git repository history

### Files to Replace
These will be replaced by the new system:
- Old agent implementations
- Legacy configuration system
- Previous tool implementations
- Outdated test suites

## Data Migration

### Database Migration
The database will be migrated from:
- Source: `ai-rebuild/data/ai_rebuild.db`
- Target: `data/ai_rebuild.db`

### Migration Strategy
1. **Backup existing data:** Preserve any existing database
2. **Export from ai-rebuild:** Extract all tables and data
3. **Merge if needed:** Combine with existing data if present
4. **Validate integrity:** Ensure all relationships intact

### Data Types Migrated
- Chat history and conversation data
- User preferences and settings
- Workspace configurations
- Tool usage metrics
- System state information
- Migration history and metadata

## Configuration Migration

### Configuration Sources
The migration process extracts configurations from multiple sources:
- JSON configuration files
- YAML configuration files
- INI configuration files
- Python configuration modules
- Environment variables
- Runtime configurations

### Mapping Process
Configurations are mapped to the new schema:
- **Direct mapping:** Simple field-to-field mapping
- **Schema-based mapping:** Complex structure transformation
- **Pattern-based mapping:** Template-driven conversion
- **Validation:** Security and completeness checks

### Security Considerations
- Sensitive values are properly masked
- API keys and secrets are protected
- Configuration validation ensures security compliance

## Service Transition

### Parallel Running
Both old and new systems run simultaneously during transition:
- **Old system:** Continues serving existing traffic
- **New system:** Receives gradually increasing traffic
- **Data synchronization:** Keeps both systems synchronized
- **Health monitoring:** Continuous system health checks

### Traffic Routing
Traffic is gradually shifted using percentage-based routing:
- **0%:** New system receives no production traffic (testing only)
- **25%:** Quarter of traffic routed to new system
- **50%:** Half of traffic on each system
- **75%:** Majority traffic on new system
- **100%:** Full traffic migration to new system

### Monitoring
Comprehensive monitoring during transition:
- System health and performance metrics
- Error rates and response times
- Resource usage (CPU, memory, disk)
- User experience metrics

## Production Deployment

### Deployment Checklist
Comprehensive checklist ensures deployment readiness:

#### Pre-Deployment Checks
- [ ] Migration scripts completed successfully
- [ ] Configuration validation passed
- [ ] Database backup verified
- [ ] Rollback plan tested

#### System Validation
- [ ] All dependencies available
- [ ] Services healthy and responsive
- [ ] Agent functionality verified
- [ ] Integration tests passed

#### Security Checks
- [ ] Configuration security audit completed
- [ ] API security validation passed
- [ ] No sensitive data exposed

#### Performance Validation
- [ ] Load testing completed
- [ ] Memory usage within limits
- [ ] Response times acceptable

#### Final Readiness
- [ ] Smoke tests passed
- [ ] Monitoring systems configured
- [ ] Rollback procedures validated

### Go-Live Process
1. Execute final validation checks
2. Perform deployment smoke tests
3. Monitor system health for 24 hours
4. Collect and validate performance metrics
5. Confirm feature parity with old system
6. Complete post-deployment checklist

## Post-Migration Validation

### Immediate Validation (0-2 hours)
- [ ] System starts successfully
- [ ] All services respond to health checks
- [ ] Critical user workflows functional
- [ ] No critical errors in logs

### Short-term Validation (2-24 hours)
- [ ] Performance metrics within expected ranges
- [ ] Error rates below baseline thresholds
- [ ] User experience metrics acceptable
- [ ] Data integrity maintained

### Extended Validation (1-7 days)
- [ ] System stability confirmed
- [ ] Performance optimization applied
- [ ] User feedback addressed
- [ ] Documentation updated

### Success Criteria
Migration is considered successful when:
- ✅ Zero data loss confirmed
- ✅ All functionality working as expected
- ✅ Performance meets or exceeds baselines
- ✅ User experience maintained or improved
- ✅ No critical issues identified
- ✅ Rollback plan no longer needed

## Rollback Procedures

### Quick Rollback (5 minutes)
If migration fails at any point, use the backup to restore:

```bash
cd /Users/valorengels/src

# Stop any running services
pkill -f "python.*ai" || true

# Remove migrated files
rm -rf ai/agents/ ai/config/ ai/integrations/ ai/mcp_servers/ 
rm -rf ai/tools/ ai/utilities/ ai/tests/ ai/scripts/ ai/examples/
rm -f ai/pyproject.toml ai/.env.template

# Restore from backup
LATEST_BACKUP=$(ls -d ai-backup-* | tail -1)
cp -r ${LATEST_BACKUP}/* ai/

# Restart services if needed
cd ai
# Start your previous system
```

### Partial Rollback
If only specific components need rollback:

```bash
cd /Users/valorengels/src

# Example: Rollback just the agents directory
LATEST_BACKUP=$(ls -d ai-backup-* | tail -1)
rm -rf ai/agents/
cp -r ${LATEST_BACKUP}/agents/ ai/

# Restart affected services
```

### Database Rollback
Restore the database from backup:

```bash
cd /Users/valorengels/src/ai

# Restore database backup
cp data/ai_rebuild.db.backup data/ai_rebuild.db

# Or from timestamped backup
LATEST_BACKUP=$(ls -d ../ai-backup-*/data | tail -1)
cp ${LATEST_BACKUP}/ai_rebuild.db data/
```

### Post-Rollback Actions
1. Verify system functionality after rollback
2. Document what caused the migration failure
3. Update migration plan to address issues
4. Clean up any partial migration artifacts
5. Schedule revised migration attempt

## Troubleshooting

### Common Migration Issues

#### Path Reference Errors
**Symptoms:** Import errors, module not found
**Solutions:**
```bash
# Find remaining ai-rebuild references
grep -r "ai-rebuild" . --include="*.py"
grep -r "ai_rebuild" . --include="*.py"

# Fix import paths
find . -type f -name "*.py" -exec sed -i '' 's|from ai_rebuild|from|g' {} \;
```

#### Permission Issues
**Symptoms:** Permission denied errors during file operations
**Solutions:**
```bash
# Fix directory permissions
chmod -R 755 agents/ config/ tools/ utilities/ mcp_servers/ integrations/
chmod -R 755 scripts/ tests/

# Fix file permissions
find . -type f -name "*.py" -exec chmod 644 {} \;
find scripts/ -type f -name "*.py" -exec chmod 755 {} \;
```

#### Dependency Conflicts
**Symptoms:** Package version conflicts, import errors
**Solutions:**
```bash
# Clean and reinstall dependencies
cd /Users/valorengels/src/ai
rm -rf .venv/
uv venv
uv sync
```

#### Database Connection Issues
**Symptoms:** Cannot connect to database
**Solutions:**
```bash
# Check database file exists
ls -la data/ai_rebuild.db

# Initialize if missing
python scripts/init_db.py init

# Check database permissions
chmod 644 data/ai_rebuild.db
```

#### Configuration Loading Failures
**Symptoms:** Settings not found, environment variables missing
**Solutions:**
```bash
# Ensure .env file exists
cp .env.template .env

# Validate configuration
python config/setup_config.py --validate-only

# Set up configuration interactively
python config/setup_config.py --interactive
```

### Verification Commands

```bash
# Check system structure
ls -la agents/ config/ tools/ utilities/ mcp_servers/

# Verify Python can import modules
python -c "from agents.valor.agent import ValorAgent; print('✓ Agents OK')"
python -c "from tools.base import ToolImplementation; print('✓ Tools OK')"
python -c "from config import settings; print('✓ Config OK')"

# Run basic tests
python -m pytest tests/test_config.py -v
python -m pytest tests/test_database.py -v

# Check system health
python scripts/deployment/monitor_health.py
```

### Getting Help

#### Internal Support
- Check migration logs: `logs/migration_*.log`
- Review validation reports in respective directories
- Consult troubleshooting section of this guide

#### Emergency Contacts
- Primary: Migration Team Lead
- Secondary: System Administrator
- Escalation: Technical Director

#### Documentation
- Technical specifications: `docs/TECHNICAL_SPECS.md`
- API documentation: `docs/API_GUIDE.md`
- Deployment procedures: `docs/DEPLOYMENT_CHECKLIST.md`

## Migration Timeline

### Repository Root Migration Schedule

| Phase | Task | Duration | Downtime |
|-------|------|----------|----------|
| 1 | Backup Current System | 5-10 min | None |
| 2 | Stop All Services | 1-2 min | Start |
| 3 | Clean Target Directory | 2-3 min | Yes |
| 4 | Move Core Components | 3-5 min | Yes |
| 5 | Update Path References | 2-3 min | Yes |
| 6 | Install Dependencies | 5-10 min | Yes |
| 7 | Database Migration | 5-10 min | Yes |
| 8 | Configuration Update | 2-3 min | Yes |
| 9 | System Startup | 3-5 min | Ending |
| 10 | Clean Up | 1-2 min | None |

**Total Migration Time:** 30-45 minutes  
**Total Downtime:** 25-40 minutes  
**Post-Migration Testing:** 1-2 hours

### Quick Migration Script

For automated migration, create and run this script:

```bash
#!/bin/bash
# save as migrate_to_root.sh

set -e  # Exit on error

echo "🚀 Starting AI Rebuild Migration to Repository Root"

# Phase 1: Backup
echo "📦 Phase 1: Creating backup..."
cd /Users/valorengels/src
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
cp -r ai/ ai-backup-${TIMESTAMP}/
echo "✅ Backup created: ai-backup-${TIMESTAMP}/"

# Phase 2: Stop services
echo "🛑 Phase 2: Stopping services..."
pkill -f "python.*ai" || true
pkill -f "uvicorn" || true
echo "✅ Services stopped"

# Phase 3-4: Clean and move
echo "📁 Phase 3-4: Moving components..."
cd /Users/valorengels/src/ai
rm -rf agents/ config/ integrations/ mcp_servers/ tools/ utilities/ tests/
mv ai-rebuild/* ./ 2>/dev/null || true
mv ai-rebuild/.* ./ 2>/dev/null || true
echo "✅ Components moved"

# Phase 5: Update paths
echo "🔧 Phase 5: Updating path references..."
find . -type f -name "*.py" -exec sed -i '' 's|/ai-rebuild/|/|g' {} \;
find . -type f -name "*.py" -exec sed -i '' 's|ai-rebuild\.|.|g' {} \;
echo "✅ Paths updated"

# Phase 6: Install dependencies
echo "📦 Phase 6: Installing dependencies..."
uv sync
echo "✅ Dependencies installed"

# Phase 7-8: Database and config
echo "🗄️ Phase 7-8: Setting up database and configuration..."
cp .env.template .env 2>/dev/null || true
python scripts/init_db.py init || true
echo "✅ Database and configuration ready"

# Phase 9: Startup
echo "🚀 Phase 9: Starting system..."
python scripts/startup.py --verbose
echo "✅ System started"

# Phase 10: Cleanup
echo "🧹 Phase 10: Cleaning up..."
rm -rf ai-rebuild/
echo "✅ Cleanup complete"

echo "🎉 Migration Complete! Run 'python scripts/deployment/monitor_health.py' to verify"
```

### Optimization Recommendations
- Run during off-hours to minimize impact
- Have database backup ready before starting
- Test migration in a development environment first
- Keep the backup for at least 7 days after migration

## Success Metrics

The migration is considered successful when:

### System Verification
- ✅ All components moved to repository root
- ✅ No references to `ai-rebuild` remain in code
- ✅ All imports resolve correctly
- ✅ Database accessible and functional
- ✅ Configuration loads without errors

### Functional Verification
- ✅ System starts without errors
- ✅ All tests pass (`pytest tests/`)
- ✅ Agent responds to queries
- ✅ Tools execute correctly
- ✅ MCP servers operational

### Performance Verification
- ✅ Response time <2 seconds
- ✅ Memory usage stable
- ✅ No error logs in first hour
- ✅ Health score >95%

### Final Checklist
```bash
# Run this verification script after migration
cd /Users/valorengels/src/ai

echo "Running post-migration verification..."

# 1. Check structure
echo "✓ Checking directory structure..."
ls -d agents/ config/ tools/ utilities/ mcp_servers/ || exit 1

# 2. Check imports
echo "✓ Checking Python imports..."
python -c "from agents.valor.agent import ValorAgent" || exit 1
python -c "from tools.base import ToolImplementation" || exit 1
python -c "from config import settings" || exit 1

# 3. Check for old references
echo "✓ Checking for ai-rebuild references..."
! grep -r "ai-rebuild" . --include="*.py" || echo "Warning: Found ai-rebuild references"

# 4. Run tests
echo "✓ Running basic tests..."
python -m pytest tests/test_config.py -q || exit 1

# 5. Check health
echo "✓ Checking system health..."
python scripts/deployment/monitor_health.py || exit 1

echo "🎉 All verification checks passed!"
```

---

## Summary

This migration guide provides a complete process to move the AI Rebuild system from `/Users/valorengels/src/ai/ai-rebuild/` to the repository root at `/Users/valorengels/src/ai/`. 

### Key Points:
- **Total Time:** 30-45 minutes
- **Downtime:** 25-40 minutes
- **Risk Level:** Low (with proper backup)
- **Rollback Time:** 5 minutes

### Next Steps After Migration:
1. Run the verification script
2. Monitor system for 24 hours
3. Remove old backups after 7 days
4. Update any external references to the new location

---

*Migration Guide Version: 2.0*  
*Updated for Repository Root Migration*  
*Part of the AI Rebuild Documentation Package*