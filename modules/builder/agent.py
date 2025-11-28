"""
Module Builder Agent

Autonomous agent that generates complete modules from requirements.
Takes natural language or structured requirements and outputs:
- module.yaml specification
- Python implementation
- Tests (unit + integration)
- Documentation
- JSON schemas
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from modules.builder.templates import (
    ModuleTemplate,
    to_python_list,
    to_python_set,
    to_yaml_list,
)
from modules.registry.registry import ModuleRegistry


class ModuleRequirements(BaseModel):
    """Structured module requirements."""

    # Basic info
    name: str = Field(..., description="Human-readable module name")
    module_id: str = Field("", description="Kebab-case identifier (auto-generated if empty)")
    version: str = Field("1.0.0", description="Semantic version")
    module_type: str = Field("skill", description="Module type: skill | mcp-server")
    category: str = Field("general", description="Module category")

    # Description
    description_short: str = Field(..., description="One-line description")
    description_long: str = Field("", description="Detailed description")

    # Capabilities
    capabilities: List[str] = Field(default_factory=list, description="Module capabilities")
    operations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Operations with parameters and descriptions",
    )

    # External services
    external_services: List[Dict[str, str]] = Field(
        default_factory=list,
        description="External services with auth requirements",
    )

    # Discovery
    tags: List[str] = Field(default_factory=list)
    search_keywords: List[str] = Field(default_factory=list)
    use_cases: List[str] = Field(default_factory=list)

    # Quality
    quality_standard: str = Field("9.8/10", description="Target quality standard")
    test_coverage: int = Field(90, description="Target test coverage percentage")


class GeneratedModule(BaseModel):
    """Result of module generation."""

    module_id: str
    name: str
    path: str
    files_created: List[str]
    generation_time_ms: int
    success: bool
    errors: List[str] = Field(default_factory=list)


class ModuleBuilderAgent:
    """
    Autonomous module builder agent.

    Takes requirements and generates complete module packages including:
    - module.yaml specification
    - Python implementation code
    - Unit and integration tests
    - Documentation (README, API docs)
    - JSON schemas for input/output/error

    Output directories by module type:
    - mcp-server: mcp_servers/{module_id}/
    - skill: skills/{module_id}/

    Usage:
        builder = ModuleBuilderAgent()
        result = await builder.build_module(requirements)
    """

    # Output directories by module type
    OUTPUT_DIRS = {
        "mcp-server": "mcp_servers",
        "skill": "skills",
    }

    def __init__(
        self,
        base_dir: Optional[str] = None,
        registry: Optional[ModuleRegistry] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.registry = registry or ModuleRegistry()
        self.logger = logger or logging.getLogger("module_builder")
        self.templates = ModuleTemplate()

    def _get_output_dir(self, module_type: str) -> Path:
        """Get the appropriate output directory for module type."""
        dir_name = self.OUTPUT_DIRS.get(module_type, "modules")
        output_dir = self.base_dir / dir_name
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _generate_module_id(self, name: str) -> str:
        """Generate snake_case module ID from name."""
        # Convert to lowercase, replace spaces/hyphens with underscores
        module_id = name.lower()
        module_id = re.sub(r"[^a-z0-9]+", "_", module_id)
        module_id = module_id.strip("_")
        return module_id

    def _to_class_name(self, name: str) -> str:
        """Convert name to PascalCase class name."""
        # Remove special characters, split on spaces/hyphens
        words = re.split(r"[-_\s]+", name)
        return "".join(word.capitalize() for word in words) + "Module"

    def _create_directory_structure(self, module_path: Path) -> None:
        """Create the module directory structure."""
        dirs = [
            module_path,
            module_path / "src",
            module_path / "tests" / "unit",
            module_path / "tests" / "integration",
            module_path / "docs",
            module_path / "examples",
            module_path / "schemas",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    def _generate_module_yaml(self, req: ModuleRequirements, created_at: str) -> str:
        """Generate module.yaml content."""
        # Build YAML sections
        capabilities_yaml = to_yaml_list(req.capabilities, indent=2)
        operations_yaml = to_yaml_list(
            [op.get("name", "") for op in req.operations], indent=2
        )
        tags_yaml = to_yaml_list(req.tags, indent=4)
        keywords_yaml = to_yaml_list(req.search_keywords, indent=4)
        use_cases_yaml = to_yaml_list(req.use_cases, indent=4)

        # External services
        external_services_yaml = ""
        for svc in req.external_services:
            external_services_yaml += f'    - name: "{svc.get("name", "")}"\n'
            external_services_yaml += f'      auth_type: "{svc.get("auth_type", "api_key")}"\n'
            external_services_yaml += f'      required: {str(svc.get("required", True)).lower()}\n'

        if not external_services_yaml:
            external_services_yaml = "    []"

        # Auth requirements
        auth_requirements_yaml = ""
        for svc in req.external_services:
            env_var = svc.get("env_var", f"{svc.get('name', 'API').upper()}_API_KEY")
            auth_requirements_yaml += f'    - type: "{svc.get("auth_type", "api_key")}"\n'
            auth_requirements_yaml += f'      env_var: "{env_var}"\n'

        if not auth_requirements_yaml:
            auth_requirements_yaml = "    []"

        # Runtime deps
        runtime_deps_yaml = ""
        for svc in req.external_services:
            pkg = svc.get("package")
            if pkg:
                runtime_deps_yaml += f'    - "{pkg}"\n'

        return self.templates.MODULE_YAML.format(
            module_id=req.module_id,
            name=req.name,
            version=req.version,
            module_type=req.module_type,
            category=req.category,
            description_short=req.description_short,
            description_long=req.description_long.replace("\n", "\n    "),
            created_at=created_at,
            capabilities_yaml=capabilities_yaml,
            operations_yaml=operations_yaml,
            runtime_deps_yaml=runtime_deps_yaml,
            external_services_yaml=external_services_yaml,
            auth_requirements_yaml=auth_requirements_yaml,
            tags_yaml=tags_yaml,
            keywords_yaml=keywords_yaml,
            use_cases_yaml=use_cases_yaml,
        )

    def _generate_implementation(self, req: ModuleRequirements) -> str:
        """Generate Python implementation code."""
        class_name = self._to_class_name(req.name)
        operations_list = "\n".join(
            f"- {op.get('name', '')}: {op.get('description', '')}"
            for op in req.operations
        )
        operations_set = to_python_set([op.get("name", "") for op in req.operations])

        # Generate client initialization for external services
        client_init = self._generate_client_init(req)

        # Generate parameter validation
        parameter_validation = self._generate_parameter_validation(req)

        # Generate operation handlers
        handlers = []
        methods = []
        for op in req.operations:
            op_name = op.get("name", "")
            method_name = f"_handle_{op_name.replace('-', '_')}"
            handlers.append(
                f'        if operation == "{op_name}":\n'
                f"            return await self.{method_name}(parameters, context)"
            )

            # Generate method stub with parameter extraction
            params = op.get("parameters", {})
            param_docs = "\n".join(
                f"            {p}: {params[p].get('description', '')}"
                for p in params
            )

            # Generate parameter extraction code
            param_extraction = []
            for p, details in params.items():
                p_type = details.get("type", "string")
                required = details.get("required", False)
                if required:
                    param_extraction.append(
                        f'        {p} = parameters["{p}"]'
                    )
                else:
                    default = "None"
                    if p_type == "string":
                        default = '""' if details.get("default") is None else f'"{details.get("default")}"'
                    elif p_type == "integer":
                        default = str(details.get("default", 0))
                    elif p_type == "boolean":
                        default = str(details.get("default", False))
                    param_extraction.append(
                        f'        {p} = parameters.get("{p}", {default})'
                    )

            param_extract_code = "\n".join(param_extraction) if param_extraction else "        # No parameters"

            methods.append(
                f'    async def {method_name}(\n'
                f"        self,\n"
                f"        parameters: Dict[str, Any],\n"
                f"        context: Optional[Dict[str, Any]] = None,\n"
                f"    ) -> Dict[str, Any]:\n"
                f'        """\n'
                f"        {op.get('description', f'Handle {op_name} operation.')}\n"
                f"\n"
                f"        Parameters:\n"
                f"{param_docs}\n"
                f"\n"
                f"        Returns:\n"
                f"            Operation result\n"
                f'        """\n'
                f"        # Extract parameters\n"
                f"{param_extract_code}\n"
                f"\n"
                f"        # TODO: Implement {op_name} logic\n"
                f"        # This is scaffolding - replace with actual implementation\n"
                f"        raise NotImplementedError(\n"
                f'            "{op_name} operation not yet implemented. "\n'
                f'            "See README.md for implementation guidance."\n'
                f"        )\n"
            )

        operation_handlers = "\n".join(handlers) if handlers else "        pass"
        operation_methods = "\n\n".join(methods) if methods else ""

        return self.templates.MODULE_IMPL.format(
            name=req.name,
            description_long=req.description_long,
            operations_list=operations_list,
            class_name=class_name,
            description_short=req.description_short,
            capabilities_list=", ".join(req.capabilities),
            module_id=req.module_id,
            version=req.version,
            operations_set=operations_set,
            capabilities_list_python=to_python_list(req.capabilities),
            tags_list_python=to_python_list(req.tags),
            category=req.category,
            client_init=client_init,
            parameter_validation=parameter_validation,
            operation_handlers=operation_handlers,
            operation_methods=operation_methods,
        )

    def _generate_client_init(self, req: ModuleRequirements) -> str:
        """Generate client initialization code for external services."""
        if not req.external_services:
            return "        # No external services configured"

        lines = []
        for svc in req.external_services:
            svc_name = svc.get("name", "")
            env_var = svc.get("env_var", f"{svc_name.upper()}_API_KEY")

            lines.append(f"        # Initialize {svc_name} client")
            lines.append(f'        self.{svc_name}_api_key = os.environ.get("{env_var}")')
            lines.append(f"        if not self.{svc_name}_api_key:")
            lines.append(f'            self.logger.warning("{env_var} not set - {svc_name} operations will fail")')

        return "\n".join(lines)

    def _generate_parameter_validation(self, req: ModuleRequirements) -> str:
        """Generate parameter validation code."""
        validation_blocks = []

        for op in req.operations:
            op_name = op.get("name", "")
            params = op.get("parameters", {})
            required_params = [p for p, d in params.items() if d.get("required", False)]

            if required_params:
                checks = []
                for p in required_params:
                    checks.append(f'"{p}"')

                validation_blocks.append(
                    f'        if operation == "{op_name}":\n'
                    f"            required = [{', '.join(checks)}]\n"
                    f"            missing = [p for p in required if p not in parameters]\n"
                    f"            if missing:\n"
                    f'                return f"Missing required parameters: {{missing}}"'
                )

        if not validation_blocks:
            return "        # No parameter validation defined"

        return "\n".join(validation_blocks)

    def _get_module_import_path(self, req: ModuleRequirements) -> str:
        """Get the Python import path for a module."""
        dir_name = self.OUTPUT_DIRS.get(req.module_type, "modules")
        return f"{dir_name}.{req.module_id}.src.processor"

    def _generate_unit_tests(self, req: ModuleRequirements, module_path: Path) -> str:
        """Generate unit test file."""
        class_name = self._to_class_name(req.name)
        module_import = self._get_module_import_path(req)
        operations_set = to_python_set([op.get("name", "") for op in req.operations])
        first_operation = req.operations[0].get("name", "") if req.operations else "test"

        # Generate parameter validation tests
        validation_tests = []
        for op in req.operations:
            op_name = op.get("name", "")
            params = op.get("parameters", {})
            required_params = [p for p, d in params.items() if d.get("required", False)]

            if required_params:
                test_method = f"test_{op_name.replace('-', '_')}_missing_required_params"
                validation_tests.append(
                    f"    @pytest.mark.asyncio\n"
                    f"    async def {test_method}(self, module):\n"
                    f'        """Test {op_name} fails with missing required parameters."""\n'
                    f"        input_data = ModuleInput(\n"
                    f'            operation="{op_name}",\n'
                    f"            parameters={{}},  # Missing required params\n"
                    f"        )\n"
                    f"        result = await module.execute(input_data)\n"
                    f"        assert result.status == ExecutionStatus.FAILURE\n"
                    f"        assert result.error is not None\n"
                    f'        assert "missing" in result.error.message.lower() or "required" in result.error.message.lower()\n'
                )

        return self.templates.TEST_UNIT.format(
            name=req.name,
            module_import=module_import,
            class_name=class_name,
            module_id=req.module_id,
            version=req.version,
            operations_set=operations_set,
            category=req.category,
            first_operation=first_operation,
            operation_validation_tests="\n".join(validation_tests) if validation_tests else "    pass  # No parameter validation tests",
        )

    def _generate_integration_tests(self, req: ModuleRequirements) -> str:
        """Generate integration test file."""
        class_name = self._to_class_name(req.name)
        module_import = self._get_module_import_path(req)

        # Determine API key env var
        api_key_env = "API_KEY"
        if req.external_services:
            first_svc = req.external_services[0]
            api_key_env = first_svc.get(
                "env_var",
                f"{first_svc.get('name', 'API').upper()}_API_KEY",
            )

        # Generate integration tests
        integration_tests = []
        for op in req.operations:
            op_name = op.get("name", "")
            test_method = f"test_{op_name.replace('-', '_')}_real_api"
            integration_tests.append(
                f"class Test{op_name.replace('-', '').title()}Integration:\n"
                f'    """Integration tests for {op_name}."""\n'
                f"\n"
                f"    @pytest.mark.asyncio\n"
                f"    async def {test_method}(self, module):\n"
                f'        """Test {op_name} with real API."""\n'
                f"        input_data = ModuleInput(\n"
                f'            operation="{op_name}",\n'
                f"            parameters={{\n"
                f"                # TODO: Add test parameters\n"
                f"            }},\n"
                f"        )\n"
                f"        result = await module.execute(input_data)\n"
                f"        # TODO: Add assertions based on expected results\n"
                f"        assert result.status in [ExecutionStatus.SUCCESS, ExecutionStatus.PARTIAL_SUCCESS]\n"
            )

        first_operation = req.operations[0].get("name", "") if req.operations else "test"

        return self.templates.TEST_INTEGRATION.format(
            name=req.name,
            module_import=module_import,
            class_name=class_name,
            api_key_env=api_key_env,
            first_operation=first_operation,
            integration_tests="\n\n".join(integration_tests),
        )

    def _generate_readme(self, req: ModuleRequirements) -> str:
        """Generate README.md."""
        class_name = self._to_class_name(req.name)
        module_import = self._get_module_import_path(req)
        first_operation = req.operations[0].get("name", "") if req.operations else "test"

        # Environment variables section
        env_vars = []
        api_key_env = "API_KEY"
        for svc in req.external_services:
            env_var = svc.get(
                "env_var",
                f"{svc.get('name', 'API').upper()}_API_KEY",
            )
            env_vars.append(f"- `{env_var}`: {svc.get('name', '')} API key")
            if not api_key_env or api_key_env == "API_KEY":
                api_key_env = env_var

        env_vars_section = (
            "\n".join(env_vars) if env_vars else "No environment variables required."
        )

        # Operations documentation
        operations_docs = []
        for op in req.operations:
            op_name = op.get("name", "")
            op_desc = op.get("description", "")
            params = op.get("parameters", {})

            params_table = "| Parameter | Type | Required | Description |\n"
            params_table += "|-----------|------|----------|-------------|\n"
            for param, details in params.items():
                p_type = details.get("type", "any")
                p_req = "Yes" if details.get("required", False) else "No"
                p_desc = details.get("description", "")
                params_table += f"| {param} | {p_type} | {p_req} | {p_desc} |\n"

            operations_docs.append(
                f"### {op_name}\n\n{op_desc}\n\n**Parameters:**\n\n{params_table}"
            )

        # Implementation status - all operations are TODO
        implementation_status = []
        for op in req.operations:
            op_name = op.get("name", "")
            implementation_status.append(f"- [ ] `{op_name}` - TODO: Implement handler")

        dir_name = self.OUTPUT_DIRS.get(req.module_type, "modules")
        return self.templates.README.format(
            name=req.name,
            description_short=req.description_short,
            description_long=req.description_long,
            module_import=module_import,
            class_name=class_name,
            first_operation=first_operation,
            env_vars_section=env_vars_section,
            operations_docs="\n\n".join(operations_docs),
            implementation_status="\n".join(implementation_status),
            test_path=f"{dir_name}/{req.module_id}/tests",
            api_key_env=api_key_env,
        )

    def _generate_input_schema(self, req: ModuleRequirements) -> str:
        """Generate input JSON schema."""
        # Generate operation-specific parameter schemas
        operation_schemas = []
        for op in req.operations:
            op_name = op.get("name", "")
            params = op.get("parameters", {})

            schema = {
                "type": "object",
                "title": f"{op_name} parameters",
                "properties": {},
                "required": [],
            }

            for param, details in params.items():
                schema["properties"][param] = {
                    "type": details.get("type", "string"),
                    "description": details.get("description", ""),
                }
                if details.get("required", False):
                    schema["required"].append(param)

            operation_schemas.append(schema)

        # Build full schema programmatically
        input_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": f"{req.name} Input Schema",
            "type": "object",
            "required": ["operation"],
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [op.get("name", "") for op in req.operations],
                    "description": "Operation to perform",
                },
                "parameters": {
                    "type": "object",
                    "description": "Operation-specific parameters",
                    "oneOf": operation_schemas,
                },
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                    "description": "Validate without executing",
                },
            },
        }

        return json.dumps(input_schema, indent=2)

    def _generate_output_schema(self, req: ModuleRequirements) -> str:
        """Generate output JSON schema."""
        output_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": f"{req.name} Output Schema",
            "type": "object",
            "required": ["status", "request_id", "execution_time_ms"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["success", "partial_success", "failure", "error"],
                },
                "data": {
                    "type": "object",
                    "description": "Operation result data",
                },
                "error": {"$ref": "#/definitions/ErrorDetail"},
                "request_id": {"type": "string"},
                "execution_time_ms": {"type": "integer"},
                "side_effects": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/SideEffect"},
                },
                "warnings": {"type": "array", "items": {"type": "string"}},
                "recommendations": {"type": "array", "items": {"type": "string"}},
            },
            "definitions": {
                "ErrorDetail": {
                    "type": "object",
                    "required": ["code", "message", "category"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                        "category": {"type": "string"},
                        "recoverable": {"type": "boolean"},
                        "recovery_suggestion": {"type": "string"},
                    },
                },
                "SideEffect": {
                    "type": "object",
                    "required": ["type", "description", "target"],
                    "properties": {
                        "type": {"type": "string"},
                        "description": {"type": "string"},
                        "target": {"type": "string"},
                        "reversible": {"type": "boolean"},
                    },
                },
            },
        }

        return json.dumps(output_schema, indent=2)

    def _generate_error_schema(self, req: ModuleRequirements) -> str:
        """Generate error JSON schema."""
        module_id_upper = req.module_id.upper().replace("-", "_")

        error_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": f"{req.name} Error Schema",
            "type": "object",
            "required": ["code", "message", "category"],
            "properties": {
                "code": {
                    "type": "string",
                    "description": f"Error code (e.g., '{module_id_upper}_API_ERROR')",
                },
                "message": {
                    "type": "string",
                    "description": "Human-readable error message",
                },
                "category": {
                    "type": "string",
                    "enum": ["validation", "auth", "api", "internal", "timeout"],
                    "description": "Error category for handling",
                },
                "recoverable": {
                    "type": "boolean",
                    "description": "Whether this error can be retried",
                },
                "recovery_suggestion": {
                    "type": "string",
                    "description": "Suggested action to recover",
                },
                "details": {
                    "type": "object",
                    "description": "Additional error context",
                },
            },
        }

        return json.dumps(error_schema, indent=2)

    async def build_module(
        self,
        requirements: ModuleRequirements,
        register: bool = True,
    ) -> GeneratedModule:
        """
        Build a complete module from requirements.

        Args:
            requirements: Structured module requirements
            register: Whether to register the module after generation

        Returns:
            GeneratedModule with generation results
        """
        import time

        start_time = time.time()
        files_created = []
        errors = []

        # Ensure module_id is set
        if not requirements.module_id:
            requirements.module_id = self._generate_module_id(requirements.name)

        # Get output directory based on module type
        output_dir = self._get_output_dir(requirements.module_type)
        module_path = output_dir / requirements.module_id
        created_at = datetime.now(timezone.utc).isoformat()

        try:
            self.logger.info(f"Building module: {requirements.name}")

            # Create directory structure
            self._create_directory_structure(module_path)

            # Generate and write module.yaml
            module_yaml = self._generate_module_yaml(requirements, created_at)
            yaml_path = module_path / "module.yaml"
            yaml_path.write_text(module_yaml)
            files_created.append(str(yaml_path))

            # Generate and write implementation
            impl_code = self._generate_implementation(requirements)
            impl_path = module_path / "src" / "processor.py"
            impl_path.write_text(impl_code)
            files_created.append(str(impl_path))

            # Create __init__.py files
            init_files = [
                module_path / "__init__.py",
                module_path / "src" / "__init__.py",
                module_path / "tests" / "__init__.py",
                module_path / "tests" / "unit" / "__init__.py",
                module_path / "tests" / "integration" / "__init__.py",
            ]
            for init_file in init_files:
                init_file.write_text('"""Auto-generated module."""\n')
                files_created.append(str(init_file))

            # Generate and write unit tests
            unit_tests = self._generate_unit_tests(requirements, module_path)
            unit_test_path = module_path / "tests" / "unit" / "test_processor.py"
            unit_test_path.write_text(unit_tests)
            files_created.append(str(unit_test_path))

            # Generate and write integration tests
            integration_tests = self._generate_integration_tests(requirements)
            int_test_path = module_path / "tests" / "integration" / "test_integration.py"
            int_test_path.write_text(integration_tests)
            files_created.append(str(int_test_path))

            # Generate and write README
            readme = self._generate_readme(requirements)
            readme_path = module_path / "README.md"
            readme_path.write_text(readme)
            files_created.append(str(readme_path))

            # Generate and write JSON schemas
            input_schema = self._generate_input_schema(requirements)
            input_schema_path = module_path / "schemas" / "input.json"
            input_schema_path.write_text(input_schema)
            files_created.append(str(input_schema_path))

            output_schema = self._generate_output_schema(requirements)
            output_schema_path = module_path / "schemas" / "output.json"
            output_schema_path.write_text(output_schema)
            files_created.append(str(output_schema_path))

            error_schema = self._generate_error_schema(requirements)
            error_schema_path = module_path / "schemas" / "error.json"
            error_schema_path.write_text(error_schema)
            files_created.append(str(error_schema_path))

            # Register module if requested
            if register:
                try:
                    self.registry.register(module_path)
                    self.logger.info(f"Registered module: {requirements.module_id}")
                except Exception as e:
                    errors.append(f"Registration failed: {str(e)}")
                    self.logger.warning(f"Failed to register module: {e}")

            generation_time_ms = int((time.time() - start_time) * 1000)

            self.logger.info(
                f"Module built successfully: {requirements.module_id} "
                f"({len(files_created)} files in {generation_time_ms}ms)"
            )

            return GeneratedModule(
                module_id=requirements.module_id,
                name=requirements.name,
                path=str(module_path.absolute()),
                files_created=files_created,
                generation_time_ms=generation_time_ms,
                success=True,
                errors=errors,
            )

        except Exception as e:
            generation_time_ms = int((time.time() - start_time) * 1000)
            errors.append(str(e))

            self.logger.error(f"Module build failed: {e}", exc_info=True)

            return GeneratedModule(
                module_id=requirements.module_id,
                name=requirements.name,
                path=str(module_path.absolute()),
                files_created=files_created,
                generation_time_ms=generation_time_ms,
                success=False,
                errors=errors,
            )

    @classmethod
    def from_natural_language(cls, description: str) -> ModuleRequirements:
        """
        Parse natural language description into ModuleRequirements.

        This is a simplified parser. For production, use an LLM to
        extract structured requirements from the description.

        Args:
            description: Natural language module description

        Returns:
            ModuleRequirements extracted from description
        """
        # Extract name from first line or sentence
        lines = description.strip().split("\n")
        first_line = lines[0].strip()

        # Try to extract a name
        if ":" in first_line:
            name = first_line.split(":")[0].strip()
        else:
            name = first_line[:50].strip()

        # Generate module ID
        module_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

        return ModuleRequirements(
            name=name,
            module_id=module_id,
            description_short=first_line[:100],
            description_long=description,
            capabilities=["custom"],
            operations=[
                {
                    "name": "execute",
                    "description": "Execute the module's main functionality",
                    "parameters": {},
                }
            ],
        )
