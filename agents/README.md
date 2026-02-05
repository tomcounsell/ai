# Agents Directory

This directory contains workflow state persistence for autonomous agents executing multi-phase tasks (Plan → Build → Test → Review → Ship).

## Purpose

The `agents/` directory manages the lifecycle and state of agentic workflows. Each workflow maintains persistent state across phases, enabling:
- Multi-phase task execution with state recovery
- Detailed logging of each phase (plan, build, test, review, ship)
- Plan documentation and task tracking
- Resume capability after interruptions

## Directory Structure

```
agents/
├── {workflow_id}/                # Unique workflow identifier
│   ├── state.json               # Persistent workflow state (ignored in git)
│   ├── plan.md                  # Generated plan documentation (ignored in git)
│   └── logs/                    # Phase-specific execution logs (ignored in git)
│       ├── plan.log            # Planning phase log
│       ├── build.log           # Build phase log
│       ├── test.log            # Test phase log
│       ├── review.log          # Review phase log
│       └── ship.log            # Ship/deploy phase log
└── README.md                    # This file (tracked in git)
```

## State Management

### state.json Structure
The `state.json` file maintains:
- Current phase (plan/build/test/review/ship)
- Phase status (pending/in_progress/completed)
- Task context and requirements
- Error tracking and recovery information
- Timestamp of last state change

### File Handling
- **Tracked in git**: `agents/README.md` (this file)
- **Ignored in git**: All `state.json`, `plan.md`, and `logs/` files
- **Auto-cleanup**: Stale workflows (> 7 days) may be archived

## Usage

Workflows are created by the SDK client and managed automatically. Direct interaction with this directory is typically not needed during normal operation.

To inspect a workflow's state:
```bash
cat agents/{workflow_id}/state.json
tail -f agents/{workflow_id}/logs/{phase}.log
```

## Integration Points

- **SDK Client** (`agent/sdk_client.py`): Creates and manages workflow directories
- **State Persistence** (`agent/state.py`): Reads/writes state.json files
- **Logging**: Phase-specific logs during multi-phase execution
