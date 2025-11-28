"""
Module Registry Implementation

Central registry for all modules with discovery and health tracking.
"""

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


class ModuleType(str, Enum):
    """Type of module."""

    SKILL = "skill"
    MCP_SERVER = "mcp-server"


class AuthStatus(str, Enum):
    """Authentication status for module dependencies."""

    READY = "ready"
    NEEDS_AUTH = "needs_auth"
    ERROR = "error"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    """Module health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ModuleRegistryEntry(BaseModel):
    """Entry in the module registry."""

    # Identity
    id: str = Field(..., description="Unique module identifier")
    name: str = Field(..., description="Human-readable name")
    version: str = Field(..., description="Semantic version")
    type: ModuleType = Field(..., description="Module type")
    category: str = Field(..., description="Module category")

    # Description
    description_short: str = Field("", description="One-line description")
    description_long: str = Field("", description="Detailed description")

    # Capabilities
    capabilities: List[str] = Field(default_factory=list)
    operations: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    search_keywords: List[str] = Field(default_factory=list)

    # Location
    path: str = Field(..., description="Filesystem path to module")
    entry_point: str = Field("", description="Python import path")

    # Status
    auth_status: AuthStatus = Field(default=AuthStatus.UNKNOWN)
    health_status: HealthStatus = Field(default=HealthStatus.UNKNOWN)
    last_health_check: Optional[datetime] = None

    # Dependencies
    requires_auth: List[Dict[str, str]] = Field(default_factory=list)
    runtime_dependencies: List[str] = Field(default_factory=list)
    external_services: List[str] = Field(default_factory=list)

    # Usage stats
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_execution_time_ms: float = 0.0

    # Quality
    quality_score: float = Field(default=0.0, ge=0.0, le=10.0)
    test_coverage: float = Field(default=0.0, ge=0.0, le=100.0)
    last_tested: Optional[datetime] = None

    # Timestamps
    registered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat() if v else None}

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.usage_count
        if total == 0:
            return 1.0
        return self.success_count / total


class ModuleRegistry:
    """
    Central registry for all modules.

    Provides:
    - Module registration from filesystem
    - Discovery by capabilities, category, or search
    - Health tracking and statistics
    - Persistence to JSON file
    """

    def __init__(
        self,
        registry_path: str = "data/module_registry.json",
        logger: Optional[logging.Logger] = None,
    ):
        self.registry_path = Path(registry_path)
        self.logger = logger or logging.getLogger("module_registry")
        self.modules: Dict[str, ModuleRegistryEntry] = {}

        # Create parent directory if needed
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing registry
        self._load_registry()

    def _load_registry(self) -> None:
        """Load registry from disk."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path, "r") as f:
                    data = json.load(f)
                    for entry_data in data.get("modules", []):
                        entry = ModuleRegistryEntry(**entry_data)
                        self.modules[entry.id] = entry
                self.logger.info(
                    f"Loaded {len(self.modules)} modules from registry"
                )
            except Exception as e:
                self.logger.error(f"Failed to load registry: {e}")

    def _save_registry(self) -> None:
        """Save registry to disk."""
        try:
            data = {
                "version": "1.0.0",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "modules": [
                    entry.dict() for entry in self.modules.values()
                ],
            }
            with open(self.registry_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
            self.logger.debug("Registry saved to disk")
        except Exception as e:
            self.logger.error(f"Failed to save registry: {e}")

    def _load_module_spec(self, module_path: Path) -> Dict[str, Any]:
        """Load module.yaml specification."""
        spec_path = module_path / "module.yaml"
        if not spec_path.exists():
            raise FileNotFoundError(f"module.yaml not found at {spec_path}")

        with open(spec_path, "r") as f:
            return yaml.safe_load(f)

    def _check_auth_status(self, spec: Dict[str, Any]) -> AuthStatus:
        """Check authentication status for module dependencies."""
        auth_requirements = spec.get("configuration", {}).get(
            "auth_requirements", []
        )

        if not auth_requirements:
            return AuthStatus.READY

        for req in auth_requirements:
            env_var = req.get("env_var")
            if env_var and not os.environ.get(env_var):
                return AuthStatus.NEEDS_AUTH

        return AuthStatus.READY

    def _to_relative_path(self, module_path: Path) -> str:
        """Convert module path to relative path for portability."""
        try:
            return str(module_path.relative_to(Path.cwd()))
        except ValueError:
            # Path is not relative to cwd, try to make it relative anyway
            return str(module_path)

    def _determine_entry_point(
        self, spec: Dict[str, Any], module_path: Path
    ) -> str:
        """Determine Python entry point for module."""
        module_type = spec.get("metadata", {}).get("type", "skill")

        if module_type == "mcp-server":
            # Look for server.py
            server_path = module_path / "server.py"
            if server_path.exists():
                # Convert path to Python import
                rel_path = module_path.relative_to(Path.cwd())
                return str(rel_path).replace("/", ".") + ".server"
        else:
            # Look for __init__.py or processor.py
            src_path = module_path / "src"
            if src_path.exists():
                processor = src_path / "processor.py"
                if processor.exists():
                    rel_path = module_path.relative_to(Path.cwd())
                    return str(rel_path).replace("/", ".") + ".src.processor"

        return ""

    def register(self, module_path: Path) -> ModuleRegistryEntry:
        """
        Register a module from its filesystem path.

        Args:
            module_path: Path to module directory containing module.yaml

        Returns:
            ModuleRegistryEntry for the registered module
        """
        module_path = Path(module_path)
        if not module_path.exists():
            raise FileNotFoundError(f"Module path not found: {module_path}")

        # Load module specification
        spec = self._load_module_spec(module_path)

        metadata = spec.get("metadata", {})
        description = spec.get("description", {})
        discovery = spec.get("discovery", {})
        configuration = spec.get("configuration", {})
        dependencies = spec.get("dependencies", {})
        quality = spec.get("quality", {})
        testing = spec.get("testing", {})

        # Check auth status
        auth_status = self._check_auth_status(spec)

        # Create registry entry
        entry = ModuleRegistryEntry(
            id=metadata.get("id", module_path.name),
            name=metadata.get("name", module_path.name),
            version=metadata.get("version", "1.0.0"),
            type=ModuleType(metadata.get("type", "skill")),
            category=metadata.get("category", "general"),
            description_short=description.get("short", ""),
            description_long=description.get("long", ""),
            capabilities=spec.get("capabilities", []),
            operations=spec.get("operations", []),
            tags=discovery.get("tags", []),
            search_keywords=discovery.get("search_keywords", []),
            path=self._to_relative_path(module_path),
            entry_point=self._determine_entry_point(spec, module_path),
            auth_status=auth_status,
            health_status=HealthStatus.UNKNOWN,
            requires_auth=configuration.get("auth_requirements", []),
            runtime_dependencies=dependencies.get("runtime", []),
            external_services=[
                s.get("name", "")
                for s in dependencies.get("external_services", [])
            ],
            quality_score=float(
                quality.get("standard", "0").replace("/10", "")
            ) if quality.get("standard") else 0.0,
            test_coverage=float(testing.get("test_coverage_target", 0)),
        )

        # Check for existing entry
        if entry.id in self.modules:
            self.logger.info(f"Updating existing module: {entry.id}")
            entry.registered_at = self.modules[entry.id].registered_at
            entry.usage_count = self.modules[entry.id].usage_count
            entry.success_count = self.modules[entry.id].success_count
            entry.failure_count = self.modules[entry.id].failure_count

        entry.updated_at = datetime.now(timezone.utc)
        self.modules[entry.id] = entry
        self._save_registry()

        self.logger.info(f"Registered module: {entry.id} ({entry.type.value})")
        return entry

    def unregister(self, module_id: str) -> bool:
        """Remove a module from the registry."""
        if module_id not in self.modules:
            return False

        del self.modules[module_id]
        self._save_registry()

        self.logger.info(f"Unregistered module: {module_id}")
        return True

    def get(self, module_id: str) -> Optional[ModuleRegistryEntry]:
        """Get a specific module by ID."""
        return self.modules.get(module_id)

    def list_all(self) -> List[ModuleRegistryEntry]:
        """List all registered modules."""
        return list(self.modules.values())

    def discover(
        self,
        capabilities: Optional[List[str]] = None,
        category: Optional[str] = None,
        auth_status: Optional[AuthStatus] = None,
        module_type: Optional[ModuleType] = None,
        tags: Optional[List[str]] = None,
        min_quality_score: float = 0.0,
    ) -> List[ModuleRegistryEntry]:
        """
        Discover modules matching criteria.

        Args:
            capabilities: Required capabilities (any match)
            category: Required category
            auth_status: Required auth status
            module_type: Required module type
            tags: Required tags (any match)
            min_quality_score: Minimum quality score

        Returns:
            List of matching modules, sorted by relevance
        """
        results = list(self.modules.values())

        # Filter by capabilities (any match)
        if capabilities:
            results = [
                m
                for m in results
                if any(cap in m.capabilities for cap in capabilities)
            ]

        # Filter by category
        if category:
            results = [m for m in results if m.category == category]

        # Filter by auth status
        if auth_status:
            results = [m for m in results if m.auth_status == auth_status]

        # Filter by module type
        if module_type:
            results = [m for m in results if m.type == module_type]

        # Filter by tags (any match)
        if tags:
            results = [
                m for m in results if any(tag in m.tags for tag in tags)
            ]

        # Filter by quality score
        if min_quality_score > 0:
            results = [
                m for m in results if m.quality_score >= min_quality_score
            ]

        # Sort by relevance (success rate, quality, usage)
        results.sort(
            key=lambda m: (
                m.success_rate,
                m.quality_score,
                m.usage_count,
            ),
            reverse=True,
        )

        return results

    def search(self, query: str) -> List[ModuleRegistryEntry]:
        """
        Search modules by keyword.

        Searches in:
        - Name
        - Category
        - Tags
        - Search keywords
        - Description
        """
        query_lower = query.lower()

        results = []
        for module in self.modules.values():
            # Calculate match score
            score = 0

            if query_lower in module.name.lower():
                score += 10
            if query_lower in module.category.lower():
                score += 5
            if any(query_lower in tag.lower() for tag in module.tags):
                score += 3
            if any(query_lower in kw.lower() for kw in module.search_keywords):
                score += 3
            if query_lower in module.description_short.lower():
                score += 2
            if query_lower in module.description_long.lower():
                score += 1
            if any(query_lower in cap.lower() for cap in module.capabilities):
                score += 4

            if score > 0:
                results.append((score, module))

        # Sort by score
        results.sort(key=lambda x: x[0], reverse=True)
        return [module for _, module in results]

    def record_usage(
        self, module_id: str, success: bool, execution_time_ms: float
    ) -> None:
        """Record module usage for statistics."""
        if module_id not in self.modules:
            return

        entry = self.modules[module_id]
        entry.usage_count += 1

        if success:
            entry.success_count += 1
        else:
            entry.failure_count += 1

        # Update average execution time
        total_time = entry.avg_execution_time_ms * (entry.usage_count - 1)
        entry.avg_execution_time_ms = (
            total_time + execution_time_ms
        ) / entry.usage_count

        entry.updated_at = datetime.now(timezone.utc)
        self._save_registry()

    def update_health(
        self, module_id: str, health_status: HealthStatus
    ) -> None:
        """Update module health status."""
        if module_id not in self.modules:
            return

        entry = self.modules[module_id]
        entry.health_status = health_status
        entry.last_health_check = datetime.now(timezone.utc)
        entry.updated_at = datetime.now(timezone.utc)
        self._save_registry()

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        modules = list(self.modules.values())

        return {
            "total_modules": len(modules),
            "by_type": {
                t.value: len([m for m in modules if m.type == t])
                for t in ModuleType
            },
            "by_auth_status": {
                s.value: len([m for m in modules if m.auth_status == s])
                for s in AuthStatus
            },
            "by_health_status": {
                s.value: len([m for m in modules if m.health_status == s])
                for s in HealthStatus
            },
            "total_usage": sum(m.usage_count for m in modules),
            "avg_quality_score": (
                sum(m.quality_score for m in modules) / len(modules)
                if modules
                else 0
            ),
        }
