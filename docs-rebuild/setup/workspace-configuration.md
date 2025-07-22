# Workspace Configuration System

## Overview

The workspace configuration system provides multi-tenant workspace isolation with strict security boundaries, ensuring complete separation between different projects and teams. This system enforces that each Telegram chat can only access its designated workspace resources, preventing cross-workspace data leaks and maintaining project confidentiality.

## Workspace Architecture

### Multi-Tenant Design

The system implements a strict multi-tenant architecture where:
- Each workspace represents a separate project or team
- Workspaces have isolated file systems, databases, and permissions
- Chat-to-workspace mapping enforces access boundaries
- Cross-workspace access is explicitly prohibited

### Core Components

```
WorkspaceValidator
├── Configuration Loading    # JSON-based workspace definitions
├── Chat Mapping            # Telegram chat ID to workspace resolution
├── Access Validation       # Notion database and file system checks
└── Security Enforcement    # Cross-workspace access prevention

WorkspaceResolver
├── Directory Resolution    # Chat context to working directory
├── Context Information     # Comprehensive workspace metadata
└── Permission Assessment   # Read/write capability determination
```

## Configuration Structure

### Workspace Definition Format

The system uses a consolidated JSON configuration file at `config/workspace_config.json`:

```json
{
  "workspaces": {
    "Workspace Name": {
      "notion_db_url": "https://www.notion.so/workspace/database-id",
      "description": "Human-readable workspace description",
      "working_directory": "/absolute/path/to/project",
      "telegram_chat_id": "-1234567890",
      "aliases": ["short-name", "abbreviation"],
      "is_dev_group": false,
      "is_test_group": false,
      "daydream_priority": 5
    }
  },
  "telegram_groups": {
    "-1234567890": "Workspace Name"
  },
  "dm_whitelist": {
    "description": "Users allowed to send direct messages",
    "default_working_directory": "/Users/valorengels/src/ai",
    "allowed_users": {
      "username": {
        "username": "username",
        "description": "User description",
        "working_directory": "/custom/path"
      }
    },
    "allowed_user_ids": {
      "123456789": {
        "description": "User without public username",
        "working_directory": "/custom/path"
      }
    }
  }
}
```

### Workspace Properties

| Property | Type | Description | Required |
|----------|------|-------------|----------|
| `notion_db_url` | string | Full Notion database URL | Yes |
| `description` | string | Human-readable workspace purpose | Yes |
| `working_directory` | string | Absolute path to project root | Yes |
| `telegram_chat_id` | string/null | Telegram group chat ID | Yes |
| `aliases` | array | Alternative names for workspace | No |
| `is_dev_group` | boolean | Development/staging environment flag | No |
| `is_test_group` | boolean | Testing environment flag | No |
| `daydream_priority` | integer | Priority for autonomous analysis (0-10) | No |

### Telegram Chat Mapping

The `telegram_groups` section provides reverse mapping for quick lookups:

```json
"telegram_groups": {
  "-1002600253717": "PsyOPTIMAL",
  "-4897329503": "PsyOPTIMAL Dev",
  "-1002553869320": "Fuse Dev",
  "-4891178445": "Yudame Dev"
}
```

### DM Whitelist Management

Direct messages are controlled through explicit whitelist:

```json
"dm_whitelist": {
  "allowed_users": {
    "tomcounsell": {
      "username": "tomcounsell",
      "description": "Tom Counsell - Owner and Boss",
      "working_directory": "/Users/valorengels/src/ai"
    }
  },
  "allowed_user_ids": {
    "179144806": {
      "description": "Tom Counsell - User ID fallback",
      "working_directory": "/Users/valorengels/src/ai"
    }
  }
}
```

## Security Model

### Workspace Boundary Enforcement

The security model implements multiple layers of protection:

#### 1. Chat-to-Workspace Isolation
```python
def validate_notion_access(self, chat_id: str, workspace_name: str) -> None:
    """Strict workspace isolation for Notion access"""
    allowed_workspace = self.get_workspace_for_chat(chat_id)
    
    if requested_workspace != allowed_workspace:
        raise WorkspaceAccessError(
            f"STRICT ISOLATION VIOLATION: Chat {chat_id} attempted to access "
            f"workspace '{workspace_name}' but is only authorized for '{allowed_workspace}'"
        )
```

#### 2. Directory Access Control
```python
def validate_directory_access(self, chat_id: str, file_path: str) -> None:
    """Enforce directory boundaries"""
    workspace = self.get_workspace_for_chat(chat_id)
    normalized_path = os.path.abspath(file_path)
    
    # Must be within allowed directories
    for allowed_dir in workspace.allowed_directories:
        if normalized_path.startswith(os.path.abspath(allowed_dir)):
            return  # Access granted
    
    raise WorkspaceAccessError(
        f"STRICT DIRECTORY ISOLATION VIOLATION: Unauthorized path access"
    )
```

#### 3. Cross-Workspace Prevention
```python
def _validate_no_cross_workspace_access(self, chat_id: str, path: str, workspace: WorkspaceConfig):
    """Prevent access to other workspace directories"""
    forbidden_paths = [
        "/Users/valorengels/src/fuse",
        "/Users/valorengels/src/psyoptimal",
        "/Users/valorengels/src/flextrip"
    ]
    
    # Remove current workspace from forbidden list
    for allowed in workspace.allowed_directories:
        if allowed in forbidden_paths:
            forbidden_paths.remove(allowed)
    
    # Check for violations
    for forbidden in forbidden_paths:
        if path.startswith(forbidden):
            raise WorkspaceAccessError("CROSS-WORKSPACE ACCESS VIOLATION")
```

### Access Control Matrix

| Chat Type | Workspace Access | Directory Access | Notion Access | Cross-Workspace |
|-----------|------------------|------------------|---------------|-----------------|
| Group Chat | Single mapped workspace | Workspace directories only | Workspace database only | Strictly forbidden |
| Dev Group | Dev workspace variant | Dev directories | Shared with main | Forbidden |
| DM (Whitelisted) | User-specific | User directory | Limited/None | Forbidden |
| DM (Non-whitelisted) | Denied | Denied | Denied | N/A |

### Security Validation Procedures

#### Pre-Operation Validation
```python
# Every operation must validate access first
def execute_operation(chat_id: str, workspace: str, file_path: str):
    # Step 1: Validate workspace access
    validator.validate_notion_access(chat_id, workspace)
    
    # Step 2: Validate directory access
    validator.validate_directory_access(chat_id, file_path)
    
    # Step 3: Proceed with operation
    perform_operation()
```

#### Audit Trail
All access attempts are logged for security monitoring:
```python
logger.info(f"Workspace access granted: Chat {chat_id} -> {workspace}")
logger.error(f"Security violation: {error_msg}")
```

## Management Procedures

### Adding New Workspaces

1. **Edit Configuration File**
```json
"NewProject": {
  "notion_db_url": "https://www.notion.so/team/database-uuid",
  "description": "New project workspace",
  "working_directory": "/Users/valorengels/src/newproject",
  "telegram_chat_id": "-1234567890",
  "aliases": ["np", "new"],
  "daydream_priority": 5
}
```

2. **Add Telegram Mapping**
```json
"telegram_groups": {
  "-1234567890": "NewProject"
}
```

3. **Create Directory Structure**
```bash
mkdir -p /Users/valorengels/src/newproject
mkdir -p /Users/valorengels/src/newproject/tmp/ai_screenshots
```

4. **Validate Configuration**
```python
python -c "
from utilities.workspace_validator import get_workspace_validator
validator = get_workspace_validator()
print(validator.list_workspaces())
"
```

### Modifying Workspace Configuration

#### Change Notion Database
```json
"notion_db_url": "https://www.notion.so/team/new-database-id"
```

#### Update Directory Permissions
```json
"allowed_directories": [
  "/Users/valorengels/src/project",
  "/Users/valorengels/src/project-assets"
]
```

#### Add Workspace Aliases
```json
"aliases": ["project", "proj", "P"]
```

### User Access Management

#### Add DM User
```json
"allowed_users": {
  "newuser": {
    "username": "newuser",
    "description": "New team member",
    "working_directory": "/Users/valorengels/src/ai"
  }
}
```

#### Add User by ID (No Username)
```json
"allowed_user_ids": {
  "987654321": {
    "description": "User without public username",
    "working_directory": "/Users/valorengels/src/project"
  }
}
```

#### Revoke Access
Remove the user entry from either `allowed_users` or `allowed_user_ids`.

### Workspace Monitoring and Validation

#### Validate All Workspaces
```python
from utilities.workspace_validator import get_workspace_validator

validator = get_workspace_validator()
workspaces = validator.list_workspaces()

for name, config in workspaces.items():
    print(f"\nWorkspace: {name}")
    print(f"  Type: {config['type']}")
    print(f"  Directories: {config['allowed_directories']}")
    print(f"  Chat ID: {config['telegram_chat_id']}")
```

#### Check Chat Access
```python
chat_id = "-1234567890"
workspace = validator.get_workspace_for_chat(chat_id)
print(f"Chat {chat_id} has access to: {workspace}")
```

#### Audit Access Logs
```bash
# Search for access grants
grep "Workspace access granted" logs/system.log

# Search for violations
grep "VIOLATION" logs/system.log
```

## Integration Points

### Tool Integration

All tools respect workspace boundaries through context injection:

```python
# MCP Tool Example
@mcp.tool()
def process_file(file_path: str, chat_id: str = "") -> str:
    # Automatic validation
    validator = get_workspace_validator()
    validator.validate_directory_access(chat_id, file_path)
    
    # Process file safely
    return process_with_workspace_context(file_path)
```

### Context Injection Pattern

```python
class WorkspaceAwareTool:
    def execute(self, chat_id: str, **kwargs):
        # Resolve workspace context
        workspace = get_workspace_for_chat(chat_id)
        working_dir = get_working_directory(chat_id)
        
        # Inject context
        kwargs['workspace'] = workspace
        kwargs['working_directory'] = working_dir
        
        # Execute with context
        return self._execute_with_context(**kwargs)
```

### Database Isolation

Each workspace maintains separate databases:

```python
def get_database_path(chat_id: str) -> str:
    workspace = get_workspace_for_chat(chat_id)
    working_dir = get_working_directory(chat_id)
    return os.path.join(working_dir, "data", f"{workspace.lower()}.db")
```

### File System Isolation

Operations are restricted to workspace directories:

```python
def safe_file_operation(chat_id: str, file_path: str):
    # Validate access
    validate_directory_access(chat_id, file_path)
    
    # Normalize to workspace root
    workspace_root = get_working_directory(chat_id)
    relative_path = os.path.relpath(file_path, workspace_root)
    
    # Perform operation
    return operate_on_file(workspace_root, relative_path)
```

## Multi-Server Deployment

### Environment-Based Filtering

For multi-server deployments, use environment variables:

```bash
# Server 1: PsyOPTIMAL Only
TELEGRAM_ALLOWED_GROUPS=PsyOPTIMAL,PsyOPTIMAL Dev
TELEGRAM_ALLOW_DMS=false

# Server 2: Fuse Only  
TELEGRAM_ALLOWED_GROUPS=Fuse,Fuse Dev
TELEGRAM_ALLOW_DMS=false

# Server 3: DMs Only
TELEGRAM_ALLOWED_GROUPS=
TELEGRAM_ALLOW_DMS=true
```

### Validation Functions

```python
def validate_telegram_environment() -> Dict[str, str]:
    """Validate Telegram filtering configuration"""
    allowed_groups = os.getenv("TELEGRAM_ALLOWED_GROUPS", "")
    allow_dms = os.getenv("TELEGRAM_ALLOW_DMS", "true")
    
    # Parse and validate
    workspace_names = [name.strip() for name in allowed_groups.split(",")]
    # Map to chat IDs from config
    return validation_results
```

### Chat Whitelist Enforcement

```python
def validate_chat_whitelist_access(chat_id: int, is_private: bool) -> bool:
    """Check if chat is allowed by environment configuration"""
    if is_private:
        # Check DM settings
        if not allow_dms_enabled():
            return False
        return validate_dm_user_access(username, chat_id)
    else:
        # Check group whitelist
        return chat_id in get_allowed_group_ids()
```

## Security Best Practices

### 1. Principle of Least Privilege
- Grant minimal necessary access
- Use specific directory paths, not parent directories
- Separate dev/test environments from production

### 2. Regular Auditing
```bash
# Weekly security audit
python scripts/audit_workspace_access.py

# Check for suspicious patterns
grep -E "(VIOLATION|denied|unauthorized)" logs/system.log | tail -100
```

### 3. Configuration Validation
```python
# Pre-deployment validation
def validate_workspace_config():
    config = load_config()
    
    # Check for overlapping directories
    all_dirs = []
    for workspace in config['workspaces'].values():
        for dir in workspace.get('allowed_directories', []):
            if dir in all_dirs:
                raise ValueError(f"Directory {dir} assigned to multiple workspaces")
            all_dirs.append(dir)
```

### 4. Access Monitoring
```python
# Real-time access monitoring
def log_access_attempt(chat_id: str, resource: str, granted: bool):
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "chat_id": chat_id,
        "resource": resource,
        "granted": granted,
        "workspace": get_workspace_for_chat(chat_id)
    }
    security_logger.info(json.dumps(log_entry))
```

## Troubleshooting

### Common Issues

#### 1. Chat Not Mapped to Workspace
**Error**: `Chat -1234567890 is not mapped to any workspace`

**Solution**:
1. Add chat mapping in `workspace_config.json`
2. Ensure `telegram_chat_id` is set correctly
3. Restart the service

#### 2. Cross-Workspace Access Violation
**Error**: `CROSS-WORKSPACE ACCESS VIOLATION: Chat attempted to access forbidden directory`

**Solution**:
1. Verify workspace boundaries are correct
2. Check if operation truly needs cross-workspace access
3. If legitimate, update `allowed_directories`

#### 3. DM Access Denied
**Error**: `DM access denied for user @username`

**Solution**:
1. Add user to `dm_whitelist.allowed_users`
2. Ensure `TELEGRAM_ALLOW_DMS=true` in environment
3. Verify username is lowercase in config

### Validation Scripts

```python
# validate_workspace_access.py
#!/usr/bin/env python3
"""Validate workspace configuration and access"""

from utilities.workspace_validator import get_workspace_validator

def validate_all():
    validator = get_workspace_validator()
    
    # Check configuration
    print("Checking workspace configuration...")
    workspaces = validator.list_workspaces()
    print(f"Found {len(workspaces)} workspaces")
    
    # Validate each workspace
    for name, config in workspaces.items():
        print(f"\nValidating {name}:")
        print(f"  - Chat ID: {config['telegram_chat_id']}")
        print(f"  - Directories: {len(config['allowed_directories'])}")
        
        # Check directory existence
        for dir in config['allowed_directories']:
            if not os.path.exists(dir):
                print(f"  ⚠️  Directory not found: {dir}")

if __name__ == "__main__":
    validate_all()
```

## Configuration Templates

### Minimal Workspace
```json
{
  "workspaces": {
    "ProjectName": {
      "notion_db_url": "https://notion.so/team/database-id",
      "description": "Project description",
      "working_directory": "/path/to/project",
      "telegram_chat_id": "-1234567890"
    }
  },
  "telegram_groups": {
    "-1234567890": "ProjectName"
  }
}
```

### Multi-Environment Workspace
```json
{
  "workspaces": {
    "Project": {
      "notion_db_url": "https://notion.so/team/prod-db",
      "description": "Production environment",
      "working_directory": "/path/to/project",
      "telegram_chat_id": "-1111111111",
      "aliases": ["prod", "project-prod"]
    },
    "Project Dev": {
      "notion_db_url": "https://notion.so/team/dev-db",
      "description": "Development environment",
      "working_directory": "/path/to/project-dev",
      "telegram_chat_id": "-2222222222",
      "aliases": ["dev", "project-dev"],
      "is_dev_group": true
    }
  }
}
```

### DM-Only Configuration
```json
{
  "workspaces": {},
  "telegram_groups": {},
  "dm_whitelist": {
    "default_working_directory": "/Users/valorengels/src/ai",
    "allowed_users": {
      "admin": {
        "username": "admin",
        "description": "System administrator",
        "working_directory": "/Users/valorengels/src/ai"
      }
    }
  }
}
```

## Conclusion

The workspace configuration system provides robust multi-tenant isolation with comprehensive security controls. By enforcing strict boundaries between workspaces, validating all access attempts, and maintaining detailed audit trails, the system ensures data confidentiality and operational integrity across all projects and teams. Regular monitoring and validation procedures help maintain security posture and catch potential issues before they become problems.