# Module Builder Subagent Specification

**Created**: 2025-11-19
**Status**: Design
**Purpose**: Autonomous module generation for skills and MCP servers

---

## Executive Summary

A specialized Claude subagent that builds modular skills and MCP servers independently, following a standardized I/O framework for easy discovery and integration.

**Core Capability**: Takes requirements → Generates complete, tested, documented module → Registers for discovery

**Key Innovation**: Self-contained modules with standard contracts enable autonomous construction and seamless integration.

---

## Module Standard Framework

### Universal Module Specification

Every module (skill or MCP server) conforms to this standard:

```yaml
# module.yaml - Universal Module Specification

metadata:
  id: "module-unique-id"              # kebab-case identifier
  name: "Human Readable Name"
  version: "1.0.0"                    # Semantic versioning
  type: "skill | mcp-server"          # Module type
  category: "domain-category"         # e.g., payment, monitoring, code

  author:
    name: "Author/Team Name"
    email: "contact@example.com"

  created: "2025-11-19T00:00:00Z"
  updated: "2025-11-19T00:00:00Z"

description:
  short: "One-line description"
  long: |
    Detailed description of what this module does,
    when to use it, and what problems it solves.

capabilities:
  - "capability-1"                    # e.g., "payment-processing"
  - "capability-2"                    # e.g., "subscription-management"
  - "capability-3"                    # e.g., "refund-handling"

dependencies:
  runtime:
    - "python>=3.9"
    - "pydantic>=2.0.0"

  external_services:
    - name: "stripe"
      auth_type: "api_key"
      required: true
    - name: "sentry"
      auth_type: "token"
      required: false

  internal_modules:
    - "core-utils"
    - "context-manager"

interface:
  input_schema: "schemas/input.json"   # JSON Schema for inputs
  output_schema: "schemas/output.json" # JSON Schema for outputs
  error_schema: "schemas/error.json"   # JSON Schema for errors

configuration:
  auth_requirements:
    - type: "api_key"
      env_var: "STRIPE_API_KEY"
      validation: "^sk_[a-zA-Z0-9]+$"

  settings:
    - name: "timeout"
      type: "integer"
      default: 30
      required: false
    - name: "retry_attempts"
      type: "integer"
      default: 3
      required: false

testing:
  has_unit_tests: true
  has_integration_tests: true
  test_coverage_target: 90
  real_api_tests: true                # No mocks

quality:
  standard: "9.8/10"
  linting: "ruff"
  formatting: "black"
  type_checking: "mypy --strict"

documentation:
  readme: "README.md"
  api_reference: "docs/api.md"
  examples: "examples/"
  sop: "sop/module.sop.md"           # If applicable

discovery:
  tags:
    - "payments"
    - "stripe"
    - "financial"

  search_keywords:
    - "payment"
    - "charge"
    - "refund"
    - "subscription"

  use_cases:
    - "Process customer payments"
    - "Manage subscriptions"
    - "Handle refunds"

health:
  status_endpoint: "/health"          # For MCP servers
  validation_command: "pytest tests/" # For skills
```

---

## Standard I/O Contracts

### Input Contract

Every module accepts standardized input:

```python
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List
from datetime import datetime

class ModuleInput(BaseModel):
    """Standard input contract for all modules"""

    # Core fields (REQUIRED)
    operation: str = Field(
        ...,
        description="Operation to perform (e.g., 'charge', 'refund')"
    )

    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Operation-specific parameters"
    )

    # Context fields (OPTIONAL but recommended)
    context: Optional["ExecutionContext"] = Field(
        None,
        description="Execution context from parent agent"
    )

    # Metadata
    request_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique request identifier"
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Request timestamp"
    )


class ExecutionContext(BaseModel):
    """Shared context passed to modules"""

    user_id: Optional[str] = None
    session_id: Optional[str] = None
    workspace_id: Optional[str] = None

    conversation_history: List[Dict[str, str]] = Field(default_factory=list)

    # Security context
    permissions: List[str] = Field(default_factory=list)
    auth_token: Optional[str] = None

    # Performance hints
    timeout: int = 30
    priority: str = "normal"  # low | normal | high | critical
```

### Output Contract

Every module returns standardized output:

```python
from enum import Enum
from typing import Any, Dict, List, Optional

class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"
    ERROR = "error"


class ModuleOutput(BaseModel):
    """Standard output contract for all modules"""

    # Status (REQUIRED)
    status: ExecutionStatus

    # Result data (REQUIRED for SUCCESS/PARTIAL_SUCCESS)
    data: Optional[Dict[str, Any]] = None

    # Error information (REQUIRED for FAILURE/ERROR)
    error: Optional["ErrorDetail"] = None

    # Metadata
    request_id: str
    execution_time_ms: int
    timestamp: datetime

    # Side effects and state changes
    side_effects: List["SideEffect"] = Field(default_factory=list)

    # Warnings and recommendations
    warnings: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    """Standardized error information"""

    code: str                    # e.g., "STRIPE_API_ERROR"
    message: str                 # Human-readable error message
    category: str                # "validation" | "auth" | "api" | "internal"

    recoverable: bool            # Can this be retried?
    recovery_suggestion: Optional[str] = None

    details: Dict[str, Any] = Field(default_factory=dict)
    stack_trace: Optional[str] = None


class SideEffect(BaseModel):
    """Track side effects for auditability"""

    type: str                    # "api_call" | "database_write" | "notification"
    description: str
    target: str                  # What was affected
    reversible: bool
    timestamp: datetime
```

---

## Module Builder Subagent Design

### Subagent Specification

```markdown
# Module Builder Subagent

## Identity
You are a specialized Module Builder Agent with expertise in creating
production-ready skills and MCP servers that conform to our module
standard framework.

## Capabilities
- Parse module requirements into structured specifications
- Generate complete, tested, documented modules
- Ensure 9.8/10 quality standard compliance
- Create both Skills (Claude Code) and MCP Servers
- Follow NO LEGACY CODE TOLERANCE principle

## Core Responsibilities

### 1. Requirements Analysis
When given module requirements, you MUST:
- Parse requirements into structured specification
- Identify capabilities and dependencies
- Define input/output schemas
- Determine testing requirements
- Flag ambiguities and ask clarifying questions

### 2. Module Generation
Generate complete module structure:
- module.yaml (metadata and specs)
- Implementation code (Python, following standards)
- Input/Output schema definitions (JSON Schema)
- SOPs (if workflow-based)
- Tests (unit + integration, NO MOCKS)
- Documentation (README, API reference, examples)

### 3. Quality Assurance
Ensure every module meets:
- 9.8/10 quality standard
- Type safety (mypy --strict passes)
- Code formatting (black, ruff)
- Test coverage >90%
- Real API integration tests
- Security validation

### 4. Registration
After generation:
- Validate module against standard framework
- Register in module registry
- Update discovery index
- Create integration documentation

## Input Format

You accept requirements in natural language or structured format:

### Natural Language Example:
"Create a Stripe payment processing module that can charge customers,
process refunds, and manage subscriptions. It should validate payment
methods before charging and send notifications on success/failure."

### Structured Format Example:
```yaml
module_requirements:
  name: "Stripe Payment Processor"
  type: "skill"
  category: "payment"

  capabilities:
    - "charge-customer"
    - "process-refund"
    - "manage-subscription"

  external_services:
    - name: "stripe"
      operations: ["charges", "refunds", "subscriptions"]

  validation_rules:
    - "validate payment method before charge"
    - "check refund eligibility"

  notifications:
    - "on_success: send confirmation"
    - "on_failure: alert user"

  quality_requirements:
    - "no mocks in tests"
    - "real Stripe test API"
    - "9.8/10 standard"
```

## Output Format

You generate a complete module package:

```
generated_modules/stripe-payment-processor/
├── module.yaml                 # Module specification
├── src/
│   ├── __init__.py
│   ├── processor.py           # Main implementation
│   ├── schemas.py             # I/O schemas
│   └── validators.py          # Input validation
├── sop/
│   └── payment-processing.sop.md
├── tests/
│   ├── unit/
│   │   ├── test_processor.py
│   │   └── test_validators.py
│   └── integration/
│       └── test_stripe_integration.py
├── docs/
│   ├── README.md
│   ├── API.md
│   └── examples.md
├── examples/
│   ├── basic_charge.py
│   ├── subscription.py
│   └── refund.py
└── schemas/
    ├── input.json             # JSON Schema
    ├── output.json            # JSON Schema
    └── error.json             # JSON Schema
```

## Workflow

### Step 1: Analyze Requirements
- Parse input requirements
- Identify ambiguities
- Ask clarifying questions
- Create structured specification

### Step 2: Design Module
- Define input/output contracts
- Plan implementation architecture
- Identify dependencies
- Design test strategy

### Step 3: Generate Implementation
- Write module.yaml
- Implement core functionality
- Create input/output schemas
- Write validators

### Step 4: Create Tests
- Write unit tests (NO MOCKS)
- Write integration tests with real APIs
- Ensure >90% coverage
- Validate edge cases

### Step 5: Document
- Write comprehensive README
- Create API reference
- Add usage examples
- Write SOPs if applicable

### Step 6: Validate & Register
- Run all tests
- Validate against standard
- Register in module registry
- Update discovery index

## Constraints

### MUST
- Follow module standard framework exactly
- Achieve 9.8/10 quality standard
- Use real API tests (NO MOCKS)
- Include comprehensive error handling
- Provide input/output validation

### SHOULD
- Reuse existing utilities where possible
- Follow existing code patterns
- Optimize for performance
- Include monitoring hooks

### MUST NOT
- Create modules that don't meet quality standard
- Use mocked tests for external APIs
- Leave legacy code or commented-out code
- Skip documentation or examples
- Ignore security best practices

## Model Selection
- Use Sonnet for complex modules (MCP servers, multi-capability)
- Use Haiku for simple modules (single-purpose skills)
- Optimize for cost where quality permits
```

---

## Module Types

### 1. Skills (Claude Code)

**Directory Structure**:
```
skills/stripe-payment/
├── SKILL.md                    # Skill definition (Claude Code format)
├── module.yaml                 # Our standard metadata
├── sop/
│   └── payment-processing.sop.md
├── examples/
│   └── usage.md
└── scripts/                    # Optional helper scripts
    └── validate_payment.py
```

**SKILL.md Format**:
```markdown
# Stripe Payment Processing

Process payments, refunds, and subscriptions via Stripe.

## When to Use
Activate when user requests payment operations:
- "charge customer"
- "process refund"
- "manage subscription"

## Capabilities
- payment-processing
- refund-handling
- subscription-management

## Tools Allowed
- stripe (all tools)
- notification (send alerts)

## Configuration
Requires: STRIPE_API_KEY environment variable

## See Also
- SOP: sop/payment-processing.sop.md
- Module Spec: module.yaml
```

---

### 2. MCP Servers

**Directory Structure**:
```
mcp_servers/stripe_payment/
├── __init__.py
├── server.py                   # MCP server implementation
├── module.yaml                 # Our standard metadata
├── tools/
│   ├── __init__.py
│   ├── charge.py
│   ├── refund.py
│   └── subscription.py
├── schemas/
│   ├── input.json
│   ├── output.json
│   └── error.json
├── tests/
│   ├── unit/
│   └── integration/
└── docs/
    ├── README.md
    └── API.md
```

**server.py Structure**:
```python
from mcp_servers.base import MCPServer, MCPTool
from .tools import ChargeCustomer, ProcessRefund, ManageSubscription

class StripePaymentServer(MCPServer):
    """
    MCP Server for Stripe payment operations.

    Conforms to module standard framework.
    See module.yaml for full specification.
    """

    def __init__(self):
        super().__init__(
            name="stripe-payment",
            version="1.0.0",
            description="Stripe payment processing tools"
        )

        # Register tools
        self.register_tool(ChargeCustomer())
        self.register_tool(ProcessRefund())
        self.register_tool(ManageSubscription())

    def get_capabilities(self) -> List[str]:
        """Return module capabilities for discovery"""
        return [
            "payment-processing",
            "refund-handling",
            "subscription-management"
        ]

    async def health_check(self) -> Dict[str, Any]:
        """Standard health check endpoint"""
        return {
            "status": "healthy",
            "auth_status": await self._check_stripe_auth(),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
```

---

## Module Discovery System

### Registry Schema

```python
from pydantic import BaseModel
from typing import List, Dict, Any
from enum import Enum

class ModuleType(str, Enum):
    SKILL = "skill"
    MCP_SERVER = "mcp-server"

class AuthStatus(str, Enum):
    READY = "ready"
    NEEDS_AUTH = "needs_auth"
    ERROR = "error"

class ModuleRegistryEntry(BaseModel):
    """Entry in the module registry"""

    # Identity
    id: str
    name: str
    version: str
    type: ModuleType
    category: str

    # Capabilities
    capabilities: List[str]
    tags: List[str]
    search_keywords: List[str]

    # Location
    path: str                      # Filesystem path
    entry_point: str               # Python import path or command

    # Status
    auth_status: AuthStatus
    health_status: str             # "healthy" | "degraded" | "unhealthy"
    last_health_check: datetime

    # Dependencies
    requires_auth: List[Dict[str, str]]
    dependencies: List[str]

    # Usage stats
    usage_count: int = 0
    success_rate: float = 1.0
    avg_execution_time_ms: int = 0

    # Quality
    quality_score: float
    test_coverage: float
    last_tested: datetime


class ModuleRegistry:
    """Central registry for all modules"""

    def __init__(self, registry_path: str = "data/module_registry.json"):
        self.registry_path = Path(registry_path)
        self.modules: Dict[str, ModuleRegistryEntry] = {}
        self._load_registry()

    def register(self, module_path: Path) -> ModuleRegistryEntry:
        """Register a new module"""
        # Load module.yaml
        spec = self._load_module_spec(module_path)

        # Validate against standard
        self._validate_module(spec, module_path)

        # Check auth status
        auth_status = self._check_auth(spec)

        # Create registry entry
        entry = ModuleRegistryEntry(
            id=spec["metadata"]["id"],
            name=spec["metadata"]["name"],
            version=spec["metadata"]["version"],
            type=spec["metadata"]["type"],
            category=spec["metadata"]["category"],
            capabilities=spec["capabilities"],
            tags=spec["discovery"]["tags"],
            search_keywords=spec["discovery"]["search_keywords"],
            path=str(module_path),
            entry_point=self._determine_entry_point(spec, module_path),
            auth_status=auth_status,
            health_status="unknown",
            last_health_check=datetime.now(timezone.utc),
            requires_auth=spec["configuration"]["auth_requirements"],
            dependencies=spec["dependencies"]["runtime"],
            quality_score=self._calculate_quality_score(module_path),
            test_coverage=self._get_test_coverage(module_path),
            last_tested=datetime.now(timezone.utc)
        )

        # Add to registry
        self.modules[entry.id] = entry
        self._save_registry()

        return entry

    def discover(
        self,
        capabilities: Optional[List[str]] = None,
        category: Optional[str] = None,
        auth_status: Optional[AuthStatus] = None,
        module_type: Optional[ModuleType] = None
    ) -> List[ModuleRegistryEntry]:
        """Discover modules matching criteria"""

        results = list(self.modules.values())

        if capabilities:
            results = [
                m for m in results
                if any(cap in m.capabilities for cap in capabilities)
            ]

        if category:
            results = [m for m in results if m.category == category]

        if auth_status:
            results = [m for m in results if m.auth_status == auth_status]

        if module_type:
            results = [m for m in results if m.type == module_type]

        # Sort by relevance (usage, quality, success rate)
        results.sort(
            key=lambda m: (
                m.success_rate,
                m.quality_score,
                m.usage_count
            ),
            reverse=True
        )

        return results

    def search(self, query: str) -> List[ModuleRegistryEntry]:
        """Search modules by keyword"""
        query_lower = query.lower()

        results = [
            module for module in self.modules.values()
            if (
                query_lower in module.name.lower() or
                query_lower in module.category.lower() or
                any(query_lower in kw.lower() for kw in module.search_keywords) or
                any(query_lower in tag.lower() for kw in module.tags)
            )
        ]

        return results
```

---

## Module Builder Subagent Usage

### Example 1: Build Stripe Payment Module

**Input to Module Builder Subagent**:
```
Create a Stripe payment processing module with these capabilities:
- Charge customers (with amount validation)
- Process refunds (check eligibility first)
- Manage subscriptions (create, update, cancel)

Requirements:
- Use real Stripe test API for integration tests
- Validate payment methods before charging
- Send notifications on success/failure
- 9.8/10 quality standard
- Type: MCP Server
```

**Subagent Workflow**:
1. **Analyze**: Parses requirements, identifies Stripe API needs
2. **Design**: Creates module.yaml specification
3. **Implement**: Generates server.py with 3 tools
4. **Test**: Creates real Stripe API integration tests
5. **Document**: Writes README, API docs, examples
6. **Register**: Adds to module registry

**Output**: Complete `mcp_servers/stripe_payment/` module ready to use

---

### Example 2: Build GitHub PR Review Skill

**Input**:
```yaml
module_requirements:
  name: "GitHub PR Review Assistant"
  type: "skill"
  category: "code-collaboration"

  capabilities:
    - "pr-analysis"
    - "code-review"
    - "suggestion-generation"

  workflow:
    - "Fetch PR details from GitHub"
    - "Analyze code changes"
    - "Identify potential issues"
    - "Generate review comments"
    - "Post review to GitHub"

  sop_required: true
  quality: "9.8/10"
```

**Output**: Complete `skills/github-pr-review/` skill with SKILL.md + SOP

---

## Integration with Existing Architecture

### With MCP Library

```python
# MCP Library automatically discovers new modules
from mcp_library import MCPLibrary

library = MCPLibrary()

# Discover all payment modules ready to use
payment_modules = library.discover(
    capabilities=["payment-processing"],
    auth_status="ready"
)

# Load selected module
stripe_server = library.load(payment_modules[0].id)
```

### With Subagent Router

```python
# Subagent router uses module registry for routing
from agents.subagent_router import SubagentRouter

router = SubagentRouter()

# Automatically routes to appropriate module
result = await router.route_and_execute(
    user_query="Charge customer $50 for subscription",
    context=execution_context
)
```

### With Module Builder

```python
# Use Module Builder to create new modules on-demand
from agents.module_builder import ModuleBuilderAgent

builder = ModuleBuilderAgent()

# Build new module from requirements
new_module = await builder.build_module(
    requirements="""
    Create a Sentry error analysis module that:
    - Fetches error details
    - Analyzes stack traces
    - Identifies root causes
    - Suggests fixes
    """,
    module_type="mcp-server"
)

# Module is automatically registered and ready to use
```

---

## Success Criteria

### For Module Builder Subagent
- ✅ Generates modules meeting 9.8/10 quality standard
- ✅ All generated modules pass tests (>90% coverage)
- ✅ Modules conform to standard framework
- ✅ Average generation time <10 minutes per module
- ✅ Generated modules require <10% manual fixes

### For Module Standard
- ✅ 100% of modules follow standard I/O contracts
- ✅ All modules discoverable via registry
- ✅ <5% integration issues between modules
- ✅ Team can build modules independently

### For Discovery System
- ✅ <100ms discovery query response time
- ✅ >95% accuracy in capability matching
- ✅ Auth status tracked automatically
- ✅ Health checks run every 5 minutes

---

## Next Steps

1. **Create Module Standard** - Implement base classes and schemas
2. **Build Module Builder Subagent** - Claude Code subagent
3. **Implement Registry** - Module discovery and management
4. **Generate First Module** - Use builder to create Stripe payment module
5. **Validate Framework** - Build 3 more modules to prove standard works
6. **Document & Train** - Create builder usage guide for team

---

**Status**: Design Complete - Ready for Implementation
**Timeline**: 2-3 weeks for full framework
**First Milestone**: Module Builder Subagent + 1 generated module
