#!/usr/bin/env python3
"""
Configuration Mapping Tool for AI Rebuild Migration
Maps extracted configurations to new system configuration format.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass

@dataclass
class MappingConfig:
    """Configuration for config mapping"""
    extraction_dir: str
    mapping_dir: str
    target_schema_version: str = "2.0.0"
    create_mapping_templates: bool = True
    validate_mappings: bool = True
    preserve_unknown_configs: bool = True
    generate_migration_scripts: bool = True

class ConfigMapper:
    """Main configuration mapping handler"""
    
    def __init__(self, config: MappingConfig):
        self.config = config
        self.extraction_dir = Path(config.extraction_dir)
        self.mapping_dir = Path(config.mapping_dir)
        self.mapping_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.mapping_dir / 'config_mapping.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Load mapping rules and schemas
        self.mapping_rules = self._load_mapping_rules()
        self.target_schema = self._load_target_schema()
        
        # Mapping results
        self.mapping_results = {
            'mapping_timestamp': datetime.utcnow().isoformat(),
            'target_schema_version': config.target_schema_version,
            'mapped_configs': {},
            'mapping_summary': {},
            'unmapped_configs': {},
            'validation_results': {}
        }
    
    def map_all_configs(self) -> Dict[str, Any]:
        """Map all extracted configurations to new format"""
        self.logger.info("Starting configuration mapping process")
        
        try:
            # Load extraction results
            extraction_results = self._load_extraction_results()
            
            if not extraction_results:
                raise FileNotFoundError("Extraction results not found")
            
            # Map each configuration type
            self._map_environment_variables(extraction_results)
            self._map_extracted_configs(extraction_results)
            self._map_runtime_configs(extraction_results)
            
            # Handle unmapped configurations
            self._handle_unmapped_configs()
            
            # Validate mapped configurations
            if self.config.validate_mappings:
                self._validate_mapped_configs()
            
            # Generate mapping templates
            if self.config.create_mapping_templates:
                self._generate_mapping_templates()
            
            # Generate migration scripts
            if self.config.generate_migration_scripts:
                self._generate_migration_scripts()
            
            # Generate mapping summary
            self._generate_mapping_summary()
            
            # Save mapping results
            self._save_mapping_results()
            
            self.logger.info("Configuration mapping completed successfully")
            return self.mapping_results
            
        except Exception as e:
            self.logger.error(f"Configuration mapping failed: {str(e)}")
            raise
    
    def _load_extraction_results(self) -> Optional[Dict[str, Any]]:
        """Load configuration extraction results"""
        results_file = self.extraction_dir / 'config_extraction_results.json'
        
        if not results_file.exists():
            self.logger.error(f"Extraction results not found: {results_file}")
            return None
        
        try:
            with open(results_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load extraction results: {str(e)}")
            return None
    
    def _load_mapping_rules(self) -> Dict[str, Any]:
        """Load configuration mapping rules"""
        # Define mapping rules for different configuration types
        mapping_rules = {
            'environment_variables': {
                'mapping_strategy': 'direct_mapping',
                'mappings': {
                    'AI_REBUILD_DEBUG': 'system.debug_mode',
                    'AI_REBUILD_LOG_LEVEL': 'logging.level',
                    'AI_REBUILD_CONFIG_PATH': 'system.config_path',
                    'OPENAI_API_KEY': 'integrations.openai.api_key',
                    'ANTHROPIC_API_KEY': 'integrations.anthropic.api_key',
                    'TELEGRAM_BOT_TOKEN': 'integrations.telegram.bot_token',
                    'DATABASE_URL': 'database.connection_string',
                    'PYTHONPATH': 'system.python_path'
                },
                'transformations': {
                    'boolean_conversion': ['AI_REBUILD_DEBUG'],
                    'path_normalization': ['AI_REBUILD_CONFIG_PATH', 'PYTHONPATH'],
                    'sensitive_masking': ['*API_KEY', '*TOKEN', '*SECRET']
                }
            },
            'json_configs': {
                'mapping_strategy': 'schema_based',
                'schema_mappings': {
                    'workspace_config.json': {
                        'target_section': 'workspace',
                        'field_mappings': {
                            'workspaces': 'workspace.definitions',
                            'default_workspace': 'workspace.default_id',
                            'workspace_settings': 'workspace.global_settings'
                        }
                    },
                    'package.json': {
                        'target_section': 'project',
                        'field_mappings': {
                            'name': 'project.name',
                            'version': 'project.version',
                            'description': 'project.description',
                            'dependencies': 'project.dependencies.runtime',
                            'devDependencies': 'project.dependencies.development'
                        }
                    },
                    'pyproject.toml': {
                        'target_section': 'project',
                        'field_mappings': {
                            'tool.poetry.name': 'project.name',
                            'tool.poetry.version': 'project.version',
                            'tool.poetry.description': 'project.description',
                            'tool.poetry.dependencies': 'project.dependencies.runtime'
                        }
                    }
                }
            },
            'yaml_configs': {
                'mapping_strategy': 'pattern_based',
                'pattern_mappings': {
                    'docker-compose*.yml': {
                        'target_section': 'deployment',
                        'extraction_rules': ['services', 'volumes', 'networks']
                    },
                    '*config*.yml': {
                        'target_section': 'application',
                        'merge_strategy': 'deep_merge'
                    }
                }
            },
            'ini_configs': {
                'mapping_strategy': 'section_based',
                'section_mappings': {
                    'DEFAULT': 'system.defaults',
                    'logging': 'logging',
                    'database': 'database',
                    'api': 'api_settings'
                }
            },
            'python_configs': {
                'mapping_strategy': 'variable_based',
                'variable_mappings': {
                    'DEBUG': 'system.debug_mode',
                    'LOG_LEVEL': 'logging.level',
                    'DATABASE_URL': 'database.connection_string',
                    'API_HOST': 'api_settings.host',
                    'API_PORT': 'api_settings.port'
                },
                'type_conversions': {
                    'boolean': ['DEBUG', 'ENABLE_*'],
                    'integer': ['*_PORT', '*_TIMEOUT'],
                    'list': ['*_HOSTS', '*_PATHS']
                }
            }
        }
        
        return mapping_rules
    
    def _load_target_schema(self) -> Dict[str, Any]:
        """Load target configuration schema"""
        # Define the target configuration schema for AI Rebuild v2
        target_schema = {
            'version': self.config.target_schema_version,
            'schema': {
                'system': {
                    'debug_mode': {'type': 'boolean', 'default': False},
                    'config_path': {'type': 'string', 'required': False},
                    'python_path': {'type': 'list', 'items': 'string'},
                    'defaults': {'type': 'object', 'required': False}
                },
                'logging': {
                    'level': {'type': 'string', 'enum': ['DEBUG', 'INFO', 'WARNING', 'ERROR']},
                    'format': {'type': 'string', 'required': False},
                    'handlers': {'type': 'list', 'required': False}
                },
                'database': {
                    'connection_string': {'type': 'string', 'sensitive': True},
                    'pool_size': {'type': 'integer', 'default': 10},
                    'timeout': {'type': 'integer', 'default': 30}
                },
                'integrations': {
                    'openai': {
                        'api_key': {'type': 'string', 'sensitive': True},
                        'model': {'type': 'string', 'default': 'gpt-4'},
                        'timeout': {'type': 'integer', 'default': 60}
                    },
                    'anthropic': {
                        'api_key': {'type': 'string', 'sensitive': True},
                        'model': {'type': 'string', 'default': 'claude-3-sonnet'},
                        'timeout': {'type': 'integer', 'default': 60}
                    },
                    'telegram': {
                        'bot_token': {'type': 'string', 'sensitive': True},
                        'webhook_url': {'type': 'string', 'required': False}
                    }
                },
                'workspace': {
                    'definitions': {'type': 'list', 'items': 'object'},
                    'default_id': {'type': 'string', 'required': False},
                    'global_settings': {'type': 'object', 'required': False}
                },
                'agents': {
                    'valor': {
                        'enabled': {'type': 'boolean', 'default': True},
                        'tools': {'type': 'list', 'items': 'string'},
                        'settings': {'type': 'object', 'required': False}
                    }
                },
                'api_settings': {
                    'host': {'type': 'string', 'default': '0.0.0.0'},
                    'port': {'type': 'integer', 'default': 8000},
                    'cors_origins': {'type': 'list', 'items': 'string'}
                },
                'project': {
                    'name': {'type': 'string', 'required': False},
                    'version': {'type': 'string', 'required': False},
                    'description': {'type': 'string', 'required': False},
                    'dependencies': {
                        'runtime': {'type': 'object', 'required': False},
                        'development': {'type': 'object', 'required': False}
                    }
                },
                'deployment': {
                    'services': {'type': 'object', 'required': False},
                    'volumes': {'type': 'object', 'required': False},
                    'networks': {'type': 'object', 'required': False}
                }
            }
        }
        
        return target_schema
    
    def _map_environment_variables(self, extraction_results: Dict[str, Any]):
        """Map environment variables to new configuration format"""
        self.logger.info("Mapping environment variables")
        
        env_vars = extraction_results.get('environment_variables', {}).get('variables', {})
        if not env_vars:
            self.logger.warning("No environment variables found to map")
            return
        
        mapping_rules = self.mapping_rules['environment_variables']
        mapped_env = {}
        unmapped_env = {}
        
        for env_var, value in env_vars.items():
            # Check for direct mapping
            if env_var in mapping_rules['mappings']:
                target_path = mapping_rules['mappings'][env_var]
                mapped_value = self._transform_value(env_var, value, mapping_rules.get('transformations', {}))
                self._set_nested_value(mapped_env, target_path, mapped_value)
            else:
                # Check for pattern matches
                mapped = False
                for pattern, target_path in mapping_rules['mappings'].items():
                    if pattern.endswith('*') and env_var.startswith(pattern[:-1]):
                        mapped_value = self._transform_value(env_var, value, mapping_rules.get('transformations', {}))
                        self._set_nested_value(mapped_env, target_path, mapped_value)
                        mapped = True
                        break
                
                if not mapped:
                    unmapped_env[env_var] = value
        
        self.mapping_results['mapped_configs']['environment'] = {
            'source': 'environment_variables',
            'mapped_at': datetime.utcnow().isoformat(),
            'mapped_data': mapped_env,
            'mapping_stats': {
                'total_variables': len(env_vars),
                'mapped_variables': len(env_vars) - len(unmapped_env),
                'unmapped_variables': len(unmapped_env)
            }
        }
        
        if unmapped_env:
            self.mapping_results['unmapped_configs']['environment_variables'] = unmapped_env
    
    def _map_extracted_configs(self, extraction_results: Dict[str, Any]):
        """Map extracted configuration files"""
        self.logger.info("Mapping extracted configurations")
        
        extracted_configs = extraction_results.get('extracted_configs', {})
        
        for dir_name, dir_configs in extracted_configs.items():
            self.logger.info(f"Mapping configurations from: {dir_name}")
            
            dir_mapped = {
                'source_directory': dir_name,
                'mapped_at': datetime.utcnow().isoformat(),
                'mapped_configs': {}
            }
            
            for config_type, configs in dir_configs.get('configs', {}).items():
                if config_type in self.mapping_rules:
                    mapped_type_configs = self._map_config_type(config_type, configs)
                    if mapped_type_configs:
                        dir_mapped['mapped_configs'][config_type] = mapped_type_configs
            
            if dir_mapped['mapped_configs']:
                self.mapping_results['mapped_configs'][f'directory_{dir_name}'] = dir_mapped
    
    def _map_config_type(self, config_type: str, configs: Dict[str, Any]) -> Dict[str, Any]:
        """Map specific configuration type"""
        mapping_rule = self.mapping_rules[config_type]
        mapped_configs = {}
        
        for config_name, config_data in configs.items():
            if 'extraction_error' in config_data:
                continue
            
            mapped_config = self._apply_mapping_strategy(
                config_name, config_data, mapping_rule, config_type
            )
            
            if mapped_config:
                mapped_configs[config_name] = mapped_config
        
        return mapped_configs
    
    def _apply_mapping_strategy(self, config_name: str, config_data: Dict[str, Any], 
                              mapping_rule: Dict[str, Any], config_type: str) -> Optional[Dict[str, Any]]:
        """Apply mapping strategy based on configuration type"""
        strategy = mapping_rule.get('mapping_strategy', 'direct')
        
        if strategy == 'direct_mapping':
            return self._apply_direct_mapping(config_name, config_data, mapping_rule)
        elif strategy == 'schema_based':
            return self._apply_schema_based_mapping(config_name, config_data, mapping_rule)
        elif strategy == 'pattern_based':
            return self._apply_pattern_based_mapping(config_name, config_data, mapping_rule)
        elif strategy == 'section_based':
            return self._apply_section_based_mapping(config_name, config_data, mapping_rule)
        elif strategy == 'variable_based':
            return self._apply_variable_based_mapping(config_name, config_data, mapping_rule)
        else:
            self.logger.warning(f"Unknown mapping strategy: {strategy}")
            return None
    
    def _apply_schema_based_mapping(self, config_name: str, config_data: Dict[str, Any], 
                                  mapping_rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply schema-based mapping for JSON/YAML configurations"""
        schema_mappings = mapping_rule.get('schema_mappings', {})
        
        # Find matching schema mapping
        schema_mapping = None
        for pattern, mapping in schema_mappings.items():
            if pattern == config_name or (pattern.endswith('*') and config_name.startswith(pattern[:-1])):
                schema_mapping = mapping
                break
        
        if not schema_mapping:
            return None
        
        source_data = config_data.get('data', {})
        if not isinstance(source_data, dict):
            return None
        
        mapped_data = {}
        field_mappings = schema_mapping.get('field_mappings', {})
        
        for source_field, target_path in field_mappings.items():
            source_value = self._get_nested_value(source_data, source_field)
            if source_value is not None:
                self._set_nested_value(mapped_data, target_path, source_value)
        
        return {
            'source_config': config_name,
            'target_section': schema_mapping.get('target_section', 'unknown'),
            'mapped_data': mapped_data,
            'mapping_metadata': {
                'fields_mapped': len([f for f in field_mappings.keys() 
                                    if self._get_nested_value(source_data, f) is not None]),
                'total_fields': len(field_mappings)
            }
        }
    
    def _apply_section_based_mapping(self, config_name: str, config_data: Dict[str, Any], 
                                   mapping_rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply section-based mapping for INI configurations"""
        section_mappings = mapping_rule.get('section_mappings', {})
        source_data = config_data.get('data', {})
        
        if not isinstance(source_data, dict):
            return None
        
        mapped_data = {}
        
        for section_name, section_data in source_data.items():
            if section_name in section_mappings:
                target_path = section_mappings[section_name]
                self._set_nested_value(mapped_data, target_path, section_data)
        
        return {
            'source_config': config_name,
            'mapped_data': mapped_data,
            'mapping_metadata': {
                'sections_mapped': len([s for s in source_data.keys() if s in section_mappings]),
                'total_sections': len(source_data)
            }
        }
    
    def _apply_variable_based_mapping(self, config_name: str, config_data: Dict[str, Any], 
                                    mapping_rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply variable-based mapping for Python configurations"""
        variable_mappings = mapping_rule.get('variable_mappings', {})
        type_conversions = mapping_rule.get('type_conversions', {})
        
        config_vars = config_data.get('config_variables', {})
        if not isinstance(config_vars, dict):
            return None
        
        mapped_data = {}
        
        for var_name, var_data in config_vars.items():
            if var_name in variable_mappings:
                target_path = variable_mappings[var_name]
                var_value = var_data.get('value') if isinstance(var_data, dict) else var_data
                
                # Apply type conversions
                converted_value = self._apply_type_conversion(var_name, var_value, type_conversions)
                self._set_nested_value(mapped_data, target_path, converted_value)
        
        return {
            'source_config': config_name,
            'mapped_data': mapped_data,
            'mapping_metadata': {
                'variables_mapped': len([v for v in config_vars.keys() if v in variable_mappings]),
                'total_variables': len(config_vars)
            }
        }
    
    def _apply_direct_mapping(self, config_name: str, config_data: Dict[str, Any], 
                            mapping_rule: Dict[str, Any]) -> Dict[str, Any]:
        """Apply direct mapping strategy"""
        mappings = mapping_rule.get('mappings', {})
        source_data = config_data.get('data', config_data)
        
        mapped_data = {}
        
        if isinstance(source_data, dict):
            for source_key, target_path in mappings.items():
                if source_key in source_data:
                    value = source_data[source_key]
                    self._set_nested_value(mapped_data, target_path, value)
        
        return {
            'source_config': config_name,
            'mapped_data': mapped_data
        }
    
    def _apply_pattern_based_mapping(self, config_name: str, config_data: Dict[str, Any], 
                                   mapping_rule: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Apply pattern-based mapping for YAML configurations"""
        pattern_mappings = mapping_rule.get('pattern_mappings', {})
        
        # Find matching pattern
        matching_mapping = None
        for pattern, mapping in pattern_mappings.items():
            if self._matches_pattern(config_name, pattern):
                matching_mapping = mapping
                break
        
        if not matching_mapping:
            return None
        
        source_data = config_data.get('data', {})
        mapped_data = {}
        
        # Extract specified rules
        extraction_rules = matching_mapping.get('extraction_rules', [])
        if extraction_rules:
            for rule in extraction_rules:
                if rule in source_data:
                    self._set_nested_value(mapped_data, rule, source_data[rule])
        
        # Apply merge strategy if specified
        merge_strategy = matching_mapping.get('merge_strategy')
        if merge_strategy == 'deep_merge':
            mapped_data = source_data  # For now, just copy all data
        
        return {
            'source_config': config_name,
            'target_section': matching_mapping.get('target_section', 'application'),
            'mapped_data': mapped_data
        }
    
    def _map_runtime_configs(self, extraction_results: Dict[str, Any]):
        """Map runtime configuration data"""
        runtime_config = extraction_results.get('runtime_config', {}).get('data', {})
        
        if not runtime_config:
            return
        
        mapped_runtime = {
            'system.python_version': runtime_config.get('python_version'),
            'system.platform': runtime_config.get('system'),
            'system.working_directory': runtime_config.get('working_directory'),
            'project.dependencies.runtime': self._format_installed_packages(
                runtime_config.get('installed_packages', [])
            )
        }
        
        # Remove None values
        mapped_runtime = {k: v for k, v in mapped_runtime.items() if v is not None}
        
        self.mapping_results['mapped_configs']['runtime'] = {
            'source': 'runtime_config',
            'mapped_at': datetime.utcnow().isoformat(),
            'mapped_data': mapped_runtime
        }
    
    def _transform_value(self, key: str, value: Any, transformations: Dict[str, List[str]]) -> Any:
        """Transform value based on transformation rules"""
        # Boolean conversion
        if any(self._matches_pattern(key, pattern) for pattern in transformations.get('boolean_conversion', [])):
            if isinstance(value, str):
                return value.lower() in ('true', '1', 'yes', 'on')
            return bool(value)
        
        # Path normalization
        if any(self._matches_pattern(key, pattern) for pattern in transformations.get('path_normalization', [])):
            if isinstance(value, str):
                return str(Path(value).resolve())
        
        # Sensitive masking
        if any(self._matches_pattern(key, pattern) for pattern in transformations.get('sensitive_masking', [])):
            return '***MASKED***'
        
        return value
    
    def _apply_type_conversion(self, var_name: str, value: Any, type_conversions: Dict[str, List[str]]) -> Any:
        """Apply type conversion based on variable name patterns"""
        for target_type, patterns in type_conversions.items():
            if any(self._matches_pattern(var_name, pattern) for pattern in patterns):
                try:
                    if target_type == 'boolean':
                        if isinstance(value, str):
                            return value.lower() in ('true', '1', 'yes', 'on')
                        return bool(value)
                    elif target_type == 'integer':
                        return int(value)
                    elif target_type == 'list':
                        if isinstance(value, str):
                            return [item.strip() for item in value.split(',')]
                        return list(value)
                except (ValueError, TypeError):
                    pass
        
        return value
    
    def _matches_pattern(self, string: str, pattern: str) -> bool:
        """Check if string matches pattern (simple wildcard matching)"""
        if '*' not in pattern:
            return string == pattern
        
        if pattern.startswith('*') and pattern.endswith('*'):
            return pattern[1:-1] in string
        elif pattern.startswith('*'):
            return string.endswith(pattern[1:])
        elif pattern.endswith('*'):
            return string.startswith(pattern[:-1])
        
        return string == pattern
    
    def _get_nested_value(self, data: Dict[str, Any], path: str) -> Any:
        """Get value from nested dictionary using dot notation"""
        keys = path.split('.')
        current = data
        
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        
        return current
    
    def _set_nested_value(self, data: Dict[str, Any], path: str, value: Any):
        """Set value in nested dictionary using dot notation"""
        keys = path.split('.')
        current = data
        
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        current[keys[-1]] = value
    
    def _format_installed_packages(self, packages: List[Dict[str, str]]) -> Dict[str, str]:
        """Format installed packages list into dependency dictionary"""
        if not packages:
            return {}
        
        return {pkg.get('name', 'unknown'): pkg.get('version', 'unknown') for pkg in packages[:20]}
    
    def _handle_unmapped_configs(self):
        """Handle configurations that couldn't be mapped"""
        if not self.mapping_results.get('unmapped_configs'):
            return
        
        if self.config.preserve_unknown_configs:
            # Create a special section for unmapped configs
            unmapped_section = {
                'preserved_at': datetime.utcnow().isoformat(),
                'preservation_note': 'These configurations could not be automatically mapped',
                'unmapped_data': self.mapping_results['unmapped_configs']
            }
            
            self.mapping_results['mapped_configs']['unmapped_preserved'] = unmapped_section
    
    def _validate_mapped_configs(self):
        """Validate mapped configurations against target schema"""
        self.logger.info("Validating mapped configurations")
        
        validation_results = {
            'validation_timestamp': datetime.utcnow().isoformat(),
            'target_schema_version': self.config.target_schema_version,
            'validation_summary': {
                'total_configs_validated': 0,
                'valid_configs': 0,
                'invalid_configs': 0,
                'warnings': 0
            },
            'config_validations': {}
        }
        
        # Merge all mapped configurations
        merged_config = self._merge_mapped_configs()
        
        # Validate against schema
        schema_validation = self._validate_against_schema(merged_config)
        validation_results['schema_validation'] = schema_validation
        
        # Validate individual mapped configurations
        for config_key, config_data in self.mapping_results['mapped_configs'].items():
            config_validation = self._validate_mapped_config(config_key, config_data)
            validation_results['config_validations'][config_key] = config_validation
            
            validation_results['validation_summary']['total_configs_validated'] += 1
            if config_validation['status'] == 'valid':
                validation_results['validation_summary']['valid_configs'] += 1
            elif config_validation['status'] == 'invalid':
                validation_results['validation_summary']['invalid_configs'] += 1
            else:
                validation_results['validation_summary']['warnings'] += 1
        
        self.mapping_results['validation_results'] = validation_results
    
    def _merge_mapped_configs(self) -> Dict[str, Any]:
        """Merge all mapped configurations into single configuration"""
        merged = {}
        
        for config_key, config_data in self.mapping_results['mapped_configs'].items():
            if 'mapped_data' in config_data:
                mapped_data = config_data['mapped_data']
                if isinstance(mapped_data, dict):
                    # Deep merge
                    merged = self._deep_merge(merged, mapped_data)
        
        return merged
    
    def _deep_merge(self, dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
        """Deep merge two dictionaries"""
        result = dict1.copy()
        
        for key, value in dict2.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        
        return result
    
    def _validate_against_schema(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate configuration against target schema"""
        schema = self.target_schema['schema']
        validation = {
            'schema_compliance': True,
            'missing_required_fields': [],
            'invalid_types': [],
            'unknown_fields': [],
            'sensitive_fields_check': True
        }
        
        # Check required fields
        for section, section_schema in schema.items():
            if section not in config:
                # Check if any field in this section is required
                for field, field_schema in section_schema.items():
                    if isinstance(field_schema, dict) and field_schema.get('required', False):
                        validation['missing_required_fields'].append(f"{section}.{field}")
        
        # Check for unknown fields in config
        for section in config.keys():
            if section not in schema:
                validation['unknown_fields'].append(section)
        
        # Set overall compliance
        validation['schema_compliance'] = (
            len(validation['missing_required_fields']) == 0 and
            len(validation['invalid_types']) == 0
        )
        
        return validation
    
    def _validate_mapped_config(self, config_key: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate individual mapped configuration"""
        validation = {
            'config_key': config_key,
            'status': 'valid',
            'issues': [],
            'recommendations': []
        }
        
        # Check if mapping was successful
        if 'mapped_data' not in config_data:
            validation['status'] = 'invalid'
            validation['issues'].append("No mapped data found")
            return validation
        
        mapped_data = config_data['mapped_data']
        
        # Check for empty mappings
        if not mapped_data:
            validation['status'] = 'warning'
            validation['issues'].append("Mapped data is empty")
        
        # Check mapping completeness
        if 'mapping_metadata' in config_data:
            metadata = config_data['mapping_metadata']
            if 'fields_mapped' in metadata and 'total_fields' in metadata:
                completeness = metadata['fields_mapped'] / metadata['total_fields']
                if completeness < 0.5:
                    validation['status'] = 'warning'
                    validation['recommendations'].append(f"Low mapping completeness: {completeness:.1%}")
        
        return validation
    
    def _generate_mapping_templates(self):
        """Generate mapping templates for manual configuration"""
        templates_dir = self.mapping_dir / 'templates'
        templates_dir.mkdir(exist_ok=True)
        
        # Generate template for target configuration structure
        target_template = {
            '_meta': {
                'version': self.config.target_schema_version,
                'generated_at': datetime.utcnow().isoformat(),
                'description': 'AI Rebuild v2 Configuration Template'
            }
        }
        
        # Add schema structure with defaults
        for section, section_schema in self.target_schema['schema'].items():
            target_template[section] = {}
            for field, field_schema in section_schema.items():
                if isinstance(field_schema, dict):
                    if 'default' in field_schema:
                        target_template[section][field] = field_schema['default']
                    else:
                        target_template[section][field] = f"<{field_schema.get('type', 'unknown')}>"
        
        with open(templates_dir / 'target_config_template.json', 'w') as f:
            json.dump(target_template, f, indent=2)
        
        # Generate mapping template for unmapped configurations
        if self.mapping_results.get('unmapped_configs'):
            unmapped_template = {
                '_instructions': 'Map these configurations to the target schema manually',
                'unmapped_configurations': self.mapping_results['unmapped_configs'],
                'target_schema_sections': list(self.target_schema['schema'].keys())
            }
            
            with open(templates_dir / 'unmapped_configs_template.json', 'w') as f:
                json.dump(unmapped_template, f, indent=2)
    
    def _generate_migration_scripts(self):
        """Generate configuration migration scripts"""
        scripts_dir = self.mapping_dir / 'migration_scripts'
        scripts_dir.mkdir(exist_ok=True)
        
        # Generate configuration merger script
        merger_script = f'''#!/usr/bin/env python3
"""
Generated Configuration Merger Script
Merges mapped configurations into final configuration file.
"""

import json
from pathlib import Path
from datetime import datetime

def merge_configurations():
    """Merge all mapped configurations"""
    mapping_dir = Path("{self.mapping_dir}")
    
    # Load mapping results
    with open(mapping_dir / "config_mapping_results.json", 'r') as f:
        mapping_results = json.load(f)
    
    # Merge configurations
    final_config = {{
        '_meta': {{
            'version': '{self.config.target_schema_version}',
            'generated_at': datetime.utcnow().isoformat(),
            'source': 'automated_migration'
        }}
    }}
    
    # Add merged configuration logic here
    for config_key, config_data in mapping_results.get('mapped_configs', {{}}).items():
        if 'mapped_data' in config_data:
            # Implement deep merge logic
            pass
    
    # Save final configuration
    output_file = mapping_dir / "final_configuration.json"
    with open(output_file, 'w') as f:
        json.dump(final_config, f, indent=2)
    
    print(f"Final configuration saved to: {{output_file}}")

if __name__ == "__main__":
    merge_configurations()
'''
        
        with open(scripts_dir / 'merge_configurations.py', 'w') as f:
            f.write(merger_script)
        
        # Make script executable
        (scripts_dir / 'merge_configurations.py').chmod(0o755)
    
    def _generate_mapping_summary(self):
        """Generate mapping summary"""
        summary = {
            'mapping_timestamp': datetime.utcnow().isoformat(),
            'total_mapped_configs': len(self.mapping_results['mapped_configs']),
            'mapping_statistics': {},
            'coverage_analysis': {},
            'recommendations': []
        }
        
        # Calculate mapping statistics
        total_source_configs = 0
        successful_mappings = 0
        
        for config_data in self.mapping_results['mapped_configs'].values():
            if 'mapped_data' in config_data and config_data['mapped_data']:
                successful_mappings += 1
            total_source_configs += 1
        
        summary['mapping_statistics'] = {
            'total_source_configs': total_source_configs,
            'successful_mappings': successful_mappings,
            'mapping_success_rate': successful_mappings / total_source_configs if total_source_configs > 0 else 0,
            'unmapped_configs': len(self.mapping_results.get('unmapped_configs', {}))
        }
        
        # Analyze target schema coverage
        merged_config = self._merge_mapped_configs()
        schema_sections = set(self.target_schema['schema'].keys())
        covered_sections = set(merged_config.keys())
        
        summary['coverage_analysis'] = {
            'total_schema_sections': len(schema_sections),
            'covered_sections': len(covered_sections),
            'coverage_percentage': len(covered_sections) / len(schema_sections) if schema_sections else 0,
            'uncovered_sections': list(schema_sections - covered_sections)
        }
        
        # Generate recommendations
        if summary['mapping_statistics']['mapping_success_rate'] < 0.8:
            summary['recommendations'].append("Review mapping rules to improve success rate")
        
        if summary['coverage_analysis']['coverage_percentage'] < 0.7:
            summary['recommendations'].append("Consider adding default values for uncovered sections")
        
        if self.mapping_results.get('unmapped_configs'):
            summary['recommendations'].append("Review unmapped configurations for manual mapping")
        
        self.mapping_results['mapping_summary'] = summary
    
    def _save_mapping_results(self):
        """Save mapping results to files"""
        # Save complete mapping results
        results_file = self.mapping_dir / 'config_mapping_results.json'
        with open(results_file, 'w') as f:
            json.dump(self.mapping_results, f, indent=2, default=str)
        
        # Save merged configuration
        merged_config = self._merge_mapped_configs()
        merged_file = self.mapping_dir / 'merged_configuration.json'
        with open(merged_file, 'w') as f:
            json.dump(merged_config, f, indent=2)
        
        # Generate mapping report
        self._generate_mapping_report()
        
        self.logger.info(f"Mapping results saved to {self.mapping_dir}")
    
    def _generate_mapping_report(self):
        """Generate human-readable mapping report"""
        summary = self.mapping_results['mapping_summary']
        
        report = f"""
AI REBUILD MIGRATION - CONFIGURATION MAPPING REPORT
{'=' * 60}

Generated: {summary['mapping_timestamp']}
Target Schema Version: {self.config.target_schema_version}

MAPPING SUMMARY
{'=' * 60}
Total Source Configurations: {summary['mapping_statistics']['total_source_configs']}
Successful Mappings: {summary['mapping_statistics']['successful_mappings']}
Mapping Success Rate: {summary['mapping_statistics']['mapping_success_rate']:.1%}
Unmapped Configurations: {summary['mapping_statistics']['unmapped_configs']}

SCHEMA COVERAGE
{'=' * 60}
Total Schema Sections: {summary['coverage_analysis']['total_schema_sections']}
Covered Sections: {summary['coverage_analysis']['covered_sections']}
Coverage Percentage: {summary['coverage_analysis']['coverage_percentage']:.1%}

Uncovered Sections:
"""
        
        for section in summary['coverage_analysis']['uncovered_sections']:
            report += f"  • {section}\n"
        
        report += f"\nMAPPED CONFIGURATIONS\n{'=' * 60}\n"
        
        for config_key, config_data in self.mapping_results['mapped_configs'].items():
            source = config_data.get('source', 'unknown')
            report += f"\n{config_key} (Source: {source})\n"
            
            if 'mapping_metadata' in config_data:
                metadata = config_data['mapping_metadata']
                if 'fields_mapped' in metadata:
                    report += f"  • Fields Mapped: {metadata['fields_mapped']}/{metadata.get('total_fields', '?')}\n"
        
        if 'validation_results' in self.mapping_results:
            val_summary = self.mapping_results['validation_results']['validation_summary']
            report += f"\nVALIDATION RESULTS\n{'=' * 60}\n"
            report += f"Valid Configurations: {val_summary['valid_configs']}\n"
            report += f"Invalid Configurations: {val_summary['invalid_configs']}\n"
            report += f"Warnings: {val_summary['warnings']}\n"
        
        if summary['recommendations']:
            report += f"\nRECOMMENDATIONS\n{'=' * 60}\n"
            for rec in summary['recommendations']:
                report += f"• {rec}\n"
        
        # Save report
        with open(self.mapping_dir / 'config_mapping_report.txt', 'w') as f:
            f.write(report)
        
        print("\nConfiguration Mapping Report:")
        print("=" * 40)
        print(report)

def main():
    """Main entry point"""
    # Default configuration
    config = MappingConfig(
        extraction_dir=str(Path.cwd() / 'config_extraction'),
        mapping_dir=str(Path.cwd() / 'config_mapping'),
        target_schema_version="2.0.0",
        create_mapping_templates=True,
        validate_mappings=True,
        preserve_unknown_configs=True,
        generate_migration_scripts=True
    )
    
    # Create mapper and run
    mapper = ConfigMapper(config)
    results = mapper.map_all_configs()
    
    summary = results['mapping_summary']
    print(f"\nConfiguration Mapping Completed!")
    print(f"Mapping success rate: {summary['mapping_statistics']['mapping_success_rate']:.1%}")
    print(f"Schema coverage: {summary['coverage_analysis']['coverage_percentage']:.1%}")
    print(f"Results saved to: {config.mapping_dir}")

if __name__ == "__main__":
    main()