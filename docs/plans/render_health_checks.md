---
status: In Progress
type: chore
appetite: Small
owner: Valor
created: 2026-02-11
tracking: https://github.com/yudame/cuttlefish/issues/26
---

# Configure Render Health Checks for Production Monitoring

## Problem

Production uptime monitoring currently relies on a GitHub Actions workflow that runs every 12 hours, which is both too infrequent to catch outages quickly and costs money in GitHub Actions minutes. Meanwhile, Render has built-in health check capabilities that are free, faster, and can automatically restart unhealthy services.

**Current behavior:**
- GitHub Actions runs `tools/testing/production_health_check.py` every 12 hours
- No Render-native health checks are configured (the live service at `srv-d3ho96p5pdvs73feafhg` has `healthCheckPath: ""` despite `render.yaml` specifying `/health/`)
- Outages could go undetected for up to 12 hours
- No automatic restart on failure

**Desired outcome:**
- Render monitors `/health/` and automatically restarts the service on consecutive failures
- GitHub Actions workflow is demoted to manual-only (for deeper debugging)
- Zero ongoing cost for basic uptime monitoring

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

This is pure infrastructure configuration with no code changes needed. The health check endpoints already exist and work.

## Prerequisites

No prerequisites — the health check endpoints (`/health/` and `/health/deep/`) are already deployed and functional at `https://ai.yuda.me`.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Health endpoint responds | `curl -s -o /dev/null -w '%{http_code}' https://ai.yuda.me/health/` | Verify endpoint is live |
| Render MCP access | Render workspace "Yudame" selected | Service configuration |

## Solution

### Key Elements

- **Render health check configuration**: Set `healthCheckPath` on the live service via Render API/MCP
- **GitHub Actions demotion**: Remove the cron schedule trigger, keep only `workflow_dispatch` for manual use
- **render.yaml sync**: Verify `render.yaml` matches the live configuration

### Flow

**Deploy** → Render checks `/health/` every few minutes → **Healthy** (200) → continue
**Deploy** → Render checks `/health/` → **Unhealthy** (non-200 or timeout) × 3 → **Auto-restart**

### Technical Approach

1. Use Render MCP `update_web_service` to set `healthCheckPath: /health/` on service `srv-d3ho96p5pdvs73feafhg`
   - Render's default health check behavior: checks the path during and after deploys, restarts on consecutive failures
2. Update `.github/workflows/production-health-check.yml` to remove the `schedule` trigger
3. Verify `render.yaml` already has `healthCheckPath: /health/` (it does — line 16)

## Rabbit Holes

- **Don't use `/health/deep/` for Render health checks** — The deep check hits the database and cache on every request. For high-frequency automated checks, the lightweight `/health/` endpoint is sufficient. Deep checks should remain available for manual debugging.
- **Don't build custom alerting** — Render's built-in notifications are sufficient for now. Custom Slack/PagerDuty integration is a separate project if ever needed.
- **Don't try to configure check interval/thresholds via API** — Render manages these internally for web services. The `healthCheckPath` is the only configurable parameter.

## Risks

### Risk 1: Health check endpoint causes restart loops
**Impact:** Service repeatedly restarts if `/health/` returns errors unexpectedly
**Mitigation:** The `/health/` endpoint is extremely lightweight (no DB/cache calls), making false failures unlikely. If issues arise, the health check path can be cleared via Render dashboard immediately.

## No-Gos (Out of Scope)

- Custom alerting integrations (Slack, PagerDuty, email)
- Modifying the health check endpoint logic
- Adding new health check endpoints
- Monitoring other Yudame services (psyoptimal, royop, etc.)

## Update System

No update system changes required — this is a Render configuration change and a GitHub Actions workflow edit. No new dependencies or config files.

## Agent Integration

No agent integration required — this is infrastructure configuration only.

## Documentation

### Inline Documentation
- [ ] Add comment in GitHub Actions workflow explaining it's manual-only now

No other documentation changes needed — the existing health check code is already well-documented.

## Success Criteria

- [ ] Render service `srv-d3ho96p5pdvs73feafhg` has `healthCheckPath` set to `/health/`
- [ ] Render dashboard shows health checks passing
- [ ] GitHub Actions workflow no longer runs on a schedule (manual trigger only)
- [ ] `render.yaml` matches live configuration

## Team Orchestration

### Team Members

- **Builder (infra)**
  - Name: infra-builder
  - Role: Configure Render health checks and update GitHub Actions workflow
  - Agent Type: builder
  - Resume: true

- **Validator (infra)**
  - Name: infra-validator
  - Role: Verify health checks are active and workflow is updated
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Configure Render health check
- **Task ID**: build-render-healthcheck
- **Depends On**: none
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: true
- Use Render MCP `update_web_service` on service `srv-d3ho96p5pdvs73feafhg` to set health check path to `/health/`
- Verify the update took effect by re-fetching service details

### 2. Demote GitHub Actions to manual-only
- **Task ID**: build-demote-gha
- **Depends On**: none
- **Assigned To**: infra-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.github/workflows/production-health-check.yml` to remove the `schedule` trigger
- Keep `workflow_dispatch` for manual use
- Add a comment explaining Render now handles automated monitoring

### 3. Validate configuration
- **Task ID**: validate-all
- **Depends On**: build-render-healthcheck, build-demote-gha
- **Assigned To**: infra-validator
- **Agent Type**: validator
- **Parallel**: false
- Fetch Render service details and confirm `healthCheckPath` is `/health/`
- Verify GitHub Actions workflow file no longer has `schedule` trigger
- Confirm `render.yaml` has `healthCheckPath: /health/` (already present)
- Run `curl -s https://ai.yuda.me/health/` to confirm endpoint responds 200

## Validation Commands

- `curl -s -o /dev/null -w '%{http_code}' https://ai.yuda.me/health/` - Health endpoint returns 200
- `grep -c 'schedule' .github/workflows/production-health-check.yml` - Should return 0 (no schedule trigger)
- `grep 'healthCheckPath' render.yaml` - Should show `/health/`
