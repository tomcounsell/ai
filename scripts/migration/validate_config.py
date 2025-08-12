#!/usr/bin/env python3
"""
Configuration Validation Tool for AI Rebuild Migration
Validates mapped configurations for completeness, correctness, and compatibility.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Union
from dataclasses import dataclass
import re

@dataclass
class ValidationConfig:
    """Configuration for config validation"""
    mapping_dir: str
    validation_dir: str
    strict_mode: bool = True
    check_syntax: bool = True
    check_completeness: bool = True
    check_compatibility: bool = True
    check_security: bool = True
    check_performance: bool = True
    generate_validation_report: bool = True

class ConfigValidator:
    """Main configuration validation handler"""
    
    def __init__(self, config: ValidationConfig):
        self.config = config
        self.mapping_dir = Path(config.mapping_dir)
        self.validation_dir = Path(config.validation_dir)
        self.validation_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.validation_dir / 'config_validation.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Load validation rules and schemas
        self.validation_rules = self._load_validation_rules()
        self.security_patterns = self._load_security_patterns()
        self.performance_benchmarks = self._load_performance_benchmarks()
        
        # Validation results
        self.validation_results = {
            'validation_timestamp': datetime.utcnow().isoformat(),
            'validation_config': config.__dict__,
            'overall_status': 'pending',
            'validation_categories': {
                'syntax': {'status': 'pending', 'results': []},
                'completeness': {'status': 'pending', 'results': []},
                'compatibility': {'status': 'pending', 'results': []},
                'security': {'status': 'pending', 'results': []},
                'performance': {'status': 'pending', 'results': []}
            },
            'summary': {
                'total_checks': 0,
                'passed_checks': 0,
                'failed_checks': 0,
                'warnings': 0,
                'critical_issues': 0
            },
            'recommendations': []
        }
    
    def validate_all_configs(self) -> Dict[str, Any]:
        """Validate all mapped configurations"""
        self.logger.info("Starting comprehensive configuration validation")
        
        try:
            # Load mapping results
            mapping_results = self._load_mapping_results()
            
            if not mapping_results:
                raise FileNotFoundError("Mapping results not found")
            
            # Run validation checks
            if self.config.check_syntax:
                self._validate_syntax(mapping_results)
            
            if self.config.check_completeness:
                self._validate_completeness(mapping_results)
            
            if self.config.check_compatibility:
                self._validate_compatibility(mapping_results)
            
            if self.config.check_security:
                self._validate_security(mapping_results)
            
            if self.config.check_performance:
                self._validate_performance(mapping_results)
            
            # Determine overall status
            self._determine_overall_status()
            
            # Generate recommendations
            self._generate_recommendations()
            
            # Generate validation report
            if self.config.generate_validation_report:
                self._generate_validation_report()
            
            # Save validation results
            self._save_validation_results()
            
            self.logger.info("Configuration validation completed")
            return self.validation_results
            
        except Exception as e:
            self.logger.error(f"Configuration validation failed: {str(e)}")
            self.validation_results['overall_status'] = 'failed'
            self.validation_results['error'] = str(e)
            raise
    
    def _load_mapping_results(self) -> Optional[Dict[str, Any]]:
        """Load configuration mapping results"""
        results_file = self.mapping_dir / 'config_mapping_results.json'
        merged_file = self.mapping_dir / 'merged_configuration.json'
        
        if not results_file.exists():
            self.logger.error(f"Mapping results not found: {results_file}")
            return None
        
        try:
            # Load mapping results
            with open(results_file, 'r') as f:
                mapping_results = json.load(f)
            
            # Load merged configuration if available
            if merged_file.exists():
                with open(merged_file, 'r') as f:
                    mapping_results['merged_configuration'] = json.load(f)
            
            return mapping_results
            
        except Exception as e:
            self.logger.error(f"Failed to load mapping results: {str(e)}")
            return None
    
    def _load_validation_rules(self) -> Dict[str, Any]:
        """Load configuration validation rules"""
        validation_rules = {
            'required_sections': [
                'system', 'logging', 'database', 'integrations', 'agents'
            ],
            'required_fields': {
                'system': ['debug_mode'],
                'logging': ['level'],
                'database': ['connection_string'],
                'integrations': ['openai', 'anthropic', 'telegram'],
                'agents': ['valor']
            },
            'field_types': {
                'system.debug_mode': 'boolean',
                'system.python_path': 'list',
                'logging.level': 'string',
                'database.pool_size': 'integer',
                'database.timeout': 'integer',
                'api_settings.port': 'integer',
                'workspace.definitions': 'list'
            },
            'field_constraints': {
                'logging.level': {
                    'type': 'enum',
                    'values': ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
                },
                'api_settings.port': {
                    'type': 'range',
                    'min': 1024,
                    'max': 65535
                },
                'database.pool_size': {
                    'type': 'range',
                    'min': 1,
                    'max': 100
                },
                'database.timeout': {
                    'type': 'range',
                    'min': 1,
                    'max': 300
                }
            },
            'dependency_rules': {
                'integrations.telegram': {
                    'requires': ['integrations.telegram.bot_token']
                },
                'integrations.openai': {
                    'requires': ['integrations.openai.api_key']
                }
            }
        }
        
        return validation_rules
    
    def _load_security_patterns(self) -> Dict[str, Any]:
        """Load security validation patterns"""
        security_patterns = {
            'sensitive_fields': [
                '*api_key', '*token', '*secret', '*password',
                'database.connection_string'
            ],
            'insecure_values': [
                'admin', 'password', '123456', 'test', 'demo'
            ],
            'required_masking': [
                'integrations.*.api_key',
                'integrations.*.token',
                'database.connection_string'
            ],
            'security_configurations': {
                'system.debug_mode': {
                    'production_value': False,
                    'warning': 'Debug mode should be disabled in production'
                },
                'api_settings.cors_origins': {
                    'pattern': r'^https?://',
                    'warning': 'CORS origins should use HTTPS in production'
                }
            },
            'forbidden_patterns': [
                r'(password|pwd|secret|key)\s*=\s*["\']?\w+["\']?',
                r'api[_-]?key\s*=\s*["\']?\w+["\']?',
                r'token\s*=\s*["\']?\w+["\']?'
            ]
        }
        
        return security_patterns
    
    def _load_performance_benchmarks(self) -> Dict[str, Any]:
        """Load performance validation benchmarks"""
        performance_benchmarks = {
            'database_settings': {
                'pool_size': {
                    'recommended_min': 5,
                    'recommended_max': 50,
                    'warning_threshold': 100
                },
                'timeout': {
                    'recommended_min': 30,
                    'recommended_max': 120,
                    'warning_threshold': 300
                }
            },
            'api_settings': {
                'port': {
                    'avoid_ports': [80, 443, 22, 21, 25],
                    'recommended_range': (8000, 9999)
                }
            },
            'logging_settings': {
                'level': {
                    'production_recommended': ['INFO', 'WARNING', 'ERROR'],
                    'development_ok': ['DEBUG', 'INFO', 'WARNING', 'ERROR']
                }
            },
            'memory_considerations': {
                'max_config_size': 1024 * 1024,  # 1MB
                'max_nesting_depth': 10
            }
        }
        
        return performance_benchmarks
    
    def _validate_syntax(self, mapping_results: Dict[str, Any]):
        """Validate configuration syntax and structure"""
        self.logger.info("Validating configuration syntax")
        
        syntax_results = []
        merged_config = mapping_results.get('merged_configuration', {})
        
        # Check JSON structure validity
        syntax_check = {
            'check_name': 'JSON Structure Validation',
            'status': 'passed',
            'issues': [],
            'details': {}
        }
        
        try:
            # Validate that merged config is proper JSON structure
            if not isinstance(merged_config, dict):
                syntax_check['status'] = 'failed'
                syntax_check['issues'].append("Configuration is not a valid JSON object")
            else:
                # Check for circular references
                self._check_circular_references(merged_config, syntax_check)
                
                # Check for maximum nesting depth
                max_depth = self._calculate_max_depth(merged_config)
                syntax_check['details']['max_nesting_depth'] = max_depth
                
                if max_depth > self.performance_benchmarks['memory_considerations']['max_nesting_depth']:
                    syntax_check['issues'].append(f"Configuration nesting too deep: {max_depth} levels")
                    syntax_check['status'] = 'warning'
                
        except Exception as e:
            syntax_check['status'] = 'failed'
            syntax_check['issues'].append(f"Syntax validation error: {str(e)}")
        
        syntax_results.append(syntax_check)
        
        # Validate individual mapped configurations
        for config_key, config_data in mapping_results.get('mapped_configs', {}).items():
            individual_check = self._validate_individual_config_syntax(config_key, config_data)
            syntax_results.append(individual_check)
        
        # Update validation results
        self._update_category_results('syntax', syntax_results)
    
    def _validate_completeness(self, mapping_results: Dict[str, Any]):
        """Validate configuration completeness"""
        self.logger.info("Validating configuration completeness")
        
        completeness_results = []
        merged_config = mapping_results.get('merged_configuration', {})
        
        # Check required sections
        sections_check = {
            'check_name': 'Required Sections Check',
            'status': 'passed',
            'issues': [],
            'details': {
                'required_sections': self.validation_rules['required_sections'],
                'found_sections': list(merged_config.keys()),
                'missing_sections': []
            }
        }
        
        for required_section in self.validation_rules['required_sections']:
            if required_section not in merged_config:
                sections_check['status'] = 'failed'
                sections_check['issues'].append(f"Missing required section: {required_section}")
                sections_check['details']['missing_sections'].append(required_section)
        
        completeness_results.append(sections_check)
        
        # Check required fields within sections
        fields_check = {
            'check_name': 'Required Fields Check',
            'status': 'passed',
            'issues': [],
            'details': {
                'missing_fields': [],
                'field_coverage': {}
            }
        }
        
        for section, required_fields in self.validation_rules['required_fields'].items():
            if section in merged_config:
                section_config = merged_config[section]
                if isinstance(section_config, dict):
                    missing_in_section = []
                    for field in required_fields:
                        if field not in section_config:
                            missing_in_section.append(field)
                            fields_check['issues'].append(f"Missing required field: {section}.{field}")
                            fields_check['status'] = 'failed'
                    
                    fields_check['details']['field_coverage'][section] = {
                        'required': len(required_fields),
                        'found': len(required_fields) - len(missing_in_section),
                        'missing': missing_in_section
                    }
        
        completeness_results.append(fields_check)
        
        # Check mapping coverage
        coverage_check = self._validate_mapping_coverage(mapping_results)
        completeness_results.append(coverage_check)
        
        # Update validation results
        self._update_category_results('completeness', completeness_results)
    
    def _validate_compatibility(self, mapping_results: Dict[str, Any]):
        """Validate configuration compatibility"""
        self.logger.info("Validating configuration compatibility")
        
        compatibility_results = []
        merged_config = mapping_results.get('merged_configuration', {})
        
        # Check field types
        types_check = {
            'check_name': 'Field Types Validation',
            'status': 'passed',
            'issues': [],
            'details': {'type_violations': []}
        }
        
        for field_path, expected_type in self.validation_rules['field_types'].items():
            actual_value = self._get_nested_value(merged_config, field_path)
            if actual_value is not None:
                if not self._validate_field_type(actual_value, expected_type):
                    types_check['status'] = 'failed'
                    types_check['issues'].append(f"Invalid type for {field_path}: expected {expected_type}")
                    types_check['details']['type_violations'].append({
                        'field': field_path,
                        'expected_type': expected_type,
                        'actual_type': type(actual_value).__name__,
                        'actual_value': str(actual_value)
                    })
        
        compatibility_results.append(types_check)
        
        # Check field constraints
        constraints_check = {
            'check_name': 'Field Constraints Validation',
            'status': 'passed',
            'issues': [],
            'details': {'constraint_violations': []}
        }
        
        for field_path, constraint in self.validation_rules['field_constraints'].items():
            actual_value = self._get_nested_value(merged_config, field_path)
            if actual_value is not None:
                violation = self._check_field_constraint(field_path, actual_value, constraint)
                if violation:
                    constraints_check['status'] = 'failed'
                    constraints_check['issues'].append(violation)
                    constraints_check['details']['constraint_violations'].append({
                        'field': field_path,
                        'constraint': constraint,
                        'actual_value': actual_value
                    })
        
        compatibility_results.append(constraints_check)
        
        # Check dependencies
        dependencies_check = self._validate_dependencies(merged_config)
        compatibility_results.append(dependencies_check)
        
        # Update validation results
        self._update_category_results('compatibility', compatibility_results)
    
    def _validate_security(self, mapping_results: Dict[str, Any]):
        """Validate configuration security"""
        self.logger.info("Validating configuration security")
        
        security_results = []
        merged_config = mapping_results.get('merged_configuration', {})
        
        # Check sensitive field masking
        masking_check = {
            'check_name': 'Sensitive Fields Masking',
            'status': 'passed',
            'issues': [],
            'details': {
                'sensitive_fields_found': [],
                'unmasked_fields': [],
                'properly_masked': []
            }
        }
        
        sensitive_fields = self._find_sensitive_fields(merged_config)
        for field_path, field_value in sensitive_fields:
            masking_check['details']['sensitive_fields_found'].append(field_path)
            
            if not self._is_value_masked(field_value):
                masking_check['status'] = 'failed'
                masking_check['issues'].append(f"Sensitive field not masked: {field_path}")
                masking_check['details']['unmasked_fields'].append(field_path)
            else:
                masking_check['details']['properly_masked'].append(field_path)
        
        security_results.append(masking_check)
        
        # Check for insecure values
        insecure_check = {
            'check_name': 'Insecure Values Detection',
            'status': 'passed',
            'issues': [],
            'details': {'insecure_values_found': []}
        }
        
        insecure_findings = self._find_insecure_values(merged_config)
        for field_path, insecure_value in insecure_findings:
            insecure_check['status'] = 'warning'
            insecure_check['issues'].append(f"Potentially insecure value in {field_path}: {insecure_value}")
            insecure_check['details']['insecure_values_found'].append({
                'field': field_path,
                'value': insecure_value
            })
        
        security_results.append(insecure_check)
        
        # Check security configurations
        security_config_check = self._validate_security_configurations(merged_config)
        security_results.append(security_config_check)
        
        # Check for forbidden patterns
        patterns_check = self._check_forbidden_patterns(mapping_results)
        security_results.append(patterns_check)
        
        # Update validation results
        self._update_category_results('security', security_results)
    
    def _validate_performance(self, mapping_results: Dict[str, Any]):
        """Validate configuration performance implications"""
        self.logger.info("Validating configuration performance")
        
        performance_results = []
        merged_config = mapping_results.get('merged_configuration', {})
        
        # Check database performance settings
        db_perf_check = {
            'check_name': 'Database Performance Settings',
            'status': 'passed',
            'issues': [],
            'details': {}
        }
        
        db_config = merged_config.get('database', {})
        db_benchmarks = self.performance_benchmarks['database_settings']
        
        for setting, benchmark in db_benchmarks.items():
            if setting in db_config:
                value = db_config[setting]
                if isinstance(value, int):
                    if value < benchmark.get('recommended_min', 0):
                        db_perf_check['status'] = 'warning'
                        db_perf_check['issues'].append(
                            f"Database {setting} ({value}) below recommended minimum ({benchmark['recommended_min']})"
                        )
                    elif value > benchmark.get('warning_threshold', float('inf')):
                        db_perf_check['status'] = 'warning'
                        db_perf_check['issues'].append(
                            f"Database {setting} ({value}) exceeds warning threshold ({benchmark['warning_threshold']})"
                        )
        
        performance_results.append(db_perf_check)
        
        # Check logging performance settings
        logging_perf_check = {
            'check_name': 'Logging Performance Settings',
            'status': 'passed',
            'issues': [],
            'details': {}
        }
        
        log_level = merged_config.get('logging', {}).get('level')
        if log_level:
            log_benchmarks = self.performance_benchmarks['logging_settings']['level']
            if log_level == 'DEBUG':
                logging_perf_check['status'] = 'warning'
                logging_perf_check['issues'].append(
                    "DEBUG logging level may impact performance in production"
                )
        
        performance_results.append(logging_perf_check)
        
        # Check configuration size
        size_check = self._validate_configuration_size(merged_config)
        performance_results.append(size_check)
        
        # Update validation results
        self._update_category_results('performance', performance_results)
    
    def _check_circular_references(self, config: Dict[str, Any], syntax_check: Dict[str, Any]):
        """Check for circular references in configuration"""
        try:
            json.dumps(config)  # This will fail if there are circular references
        except ValueError as e:
            if "circular reference" in str(e).lower():
                syntax_check['status'] = 'failed'
                syntax_check['issues'].append("Circular references detected in configuration")
    
    def _calculate_max_depth(self, obj: Any, current_depth: int = 0) -> int:
        """Calculate maximum nesting depth of configuration"""
        if not isinstance(obj, (dict, list)):
            return current_depth
        
        if isinstance(obj, dict):
            if not obj:
                return current_depth
            return max(
                self._calculate_max_depth(value, current_depth + 1)
                for value in obj.values()
            )
        
        if isinstance(obj, list):
            if not obj:
                return current_depth
            return max(
                self._calculate_max_depth(item, current_depth + 1)
                for item in obj
            )
        
        return current_depth
    
    def _validate_individual_config_syntax(self, config_key: str, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate syntax of individual configuration"""
        check = {
            'check_name': f'Individual Config Syntax: {config_key}',
            'status': 'passed',
            'issues': [],
            'details': {'config_key': config_key}
        }
        
        if 'mapped_data' not in config_data:
            check['status'] = 'warning'
            check['issues'].append("No mapped data found")
            return check
        
        mapped_data = config_data['mapped_data']
        
        try:
            # Check if mapped data is serializable
            json.dumps(mapped_data)
            
            # Check for empty configurations
            if not mapped_data:
                check['status'] = 'warning'
                check['issues'].append("Configuration is empty")
            
        except (TypeError, ValueError) as e:
            check['status'] = 'failed'
            check['issues'].append(f"Configuration not serializable: {str(e)}")
        
        return check
    
    def _validate_mapping_coverage(self, mapping_results: Dict[str, Any]) -> Dict[str, Any]:
        """Validate mapping coverage completeness"""
        check = {
            'check_name': 'Mapping Coverage Analysis',
            'status': 'passed',
            'issues': [],
            'details': {
                'coverage_stats': {},
                'low_coverage_configs': []
            }
        }
        
        mapping_summary = mapping_results.get('mapping_summary', {})
        
        # Check overall mapping success rate
        mapping_stats = mapping_summary.get('mapping_statistics', {})
        success_rate = mapping_stats.get('mapping_success_rate', 0)
        
        if success_rate < 0.8:
            check['status'] = 'warning'
            check['issues'].append(f"Low mapping success rate: {success_rate:.1%}")
        
        # Check schema coverage
        coverage_analysis = mapping_summary.get('coverage_analysis', {})
        coverage_percentage = coverage_analysis.get('coverage_percentage', 0)
        
        if coverage_percentage < 0.7:
            check['status'] = 'warning'
            check['issues'].append(f"Low schema coverage: {coverage_percentage:.1%}")
        
        check['details']['coverage_stats'] = {
            'mapping_success_rate': success_rate,
            'schema_coverage': coverage_percentage,
            'unmapped_configs': mapping_stats.get('unmapped_configs', 0)
        }
        
        return check
    
    def _validate_field_type(self, value: Any, expected_type: str) -> bool:
        """Validate field type matches expected type"""
        type_map = {
            'string': str,
            'integer': int,
            'boolean': bool,
            'list': list,
            'object': dict,
            'number': (int, float)
        }
        
        expected_python_type = type_map.get(expected_type)
        if expected_python_type:
            return isinstance(value, expected_python_type)
        
        return True  # Unknown type, assume valid
    
    def _check_field_constraint(self, field_path: str, value: Any, constraint: Dict[str, Any]) -> Optional[str]:
        """Check if field value violates constraint"""
        constraint_type = constraint.get('type')
        
        if constraint_type == 'enum':
            allowed_values = constraint.get('values', [])
            if value not in allowed_values:
                return f"Invalid value for {field_path}: {value} not in {allowed_values}"
        
        elif constraint_type == 'range':
            if isinstance(value, (int, float)):
                min_val = constraint.get('min')
                max_val = constraint.get('max')
                
                if min_val is not None and value < min_val:
                    return f"Value for {field_path} ({value}) below minimum ({min_val})"
                
                if max_val is not None and value > max_val:
                    return f"Value for {field_path} ({value}) above maximum ({max_val})"
        
        elif constraint_type == 'pattern':
            pattern = constraint.get('pattern')
            if pattern and isinstance(value, str):
                if not re.match(pattern, value):
                    return f"Value for {field_path} does not match required pattern"
        
        return None
    
    def _validate_dependencies(self, merged_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate configuration dependencies"""
        check = {
            'check_name': 'Configuration Dependencies',
            'status': 'passed',
            'issues': [],
            'details': {'dependency_violations': []}
        }
        
        dependency_rules = self.validation_rules.get('dependency_rules', {})
        
        for config_path, dependency in dependency_rules.items():
            config_value = self._get_nested_value(merged_config, config_path)
            
            if config_value is not None:  # Config exists
                required_fields = dependency.get('requires', [])
                
                for required_field in required_fields:
                    required_value = self._get_nested_value(merged_config, required_field)
                    
                    if required_value is None:
                        check['status'] = 'failed'
                        check['issues'].append(
                            f"Configuration {config_path} requires {required_field} but it's missing"
                        )
                        check['details']['dependency_violations'].append({
                            'config': config_path,
                            'required_field': required_field
                        })
        
        return check
    
    def _find_sensitive_fields(self, config: Dict[str, Any], path: str = '') -> List[Tuple[str, Any]]:
        """Find sensitive fields in configuration"""
        sensitive_fields = []
        sensitive_patterns = self.security_patterns['sensitive_fields']
        
        for key, value in config.items():
            current_path = f"{path}.{key}" if path else key
            
            # Check if field matches sensitive pattern
            if any(self._matches_pattern(current_path, pattern) for pattern in sensitive_patterns):
                sensitive_fields.append((current_path, value))
            
            # Recurse into nested objects
            if isinstance(value, dict):
                sensitive_fields.extend(self._find_sensitive_fields(value, current_path))
        
        return sensitive_fields
    
    def _is_value_masked(self, value: Any) -> bool:
        """Check if a value appears to be masked/redacted"""
        if not isinstance(value, str):
            return False
        
        masked_patterns = ['***', 'MASKED', 'REDACTED', 'HIDDEN', '[PROTECTED]']
        return any(pattern in value.upper() for pattern in masked_patterns)
    
    def _find_insecure_values(self, config: Dict[str, Any], path: str = '') -> List[Tuple[str, str]]:
        """Find potentially insecure values in configuration"""
        insecure_findings = []
        insecure_values = self.security_patterns['insecure_values']
        
        for key, value in config.items():
            current_path = f"{path}.{key}" if path else key
            
            if isinstance(value, str) and value.lower() in [v.lower() for v in insecure_values]:
                insecure_findings.append((current_path, value))
            
            elif isinstance(value, dict):
                insecure_findings.extend(self._find_insecure_values(value, current_path))
        
        return insecure_findings
    
    def _validate_security_configurations(self, merged_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate specific security configurations"""
        check = {
            'check_name': 'Security Configuration Validation',
            'status': 'passed',
            'issues': [],
            'details': {'security_warnings': []}
        }
        
        security_configs = self.security_patterns['security_configurations']
        
        for field_path, security_rule in security_configs.items():
            field_value = self._get_nested_value(merged_config, field_path)
            
            if field_value is not None:
                # Check production value
                if 'production_value' in security_rule:
                    if field_value != security_rule['production_value']:
                        check['status'] = 'warning'
                        warning_msg = security_rule.get('warning', f"Security concern with {field_path}")
                        check['issues'].append(warning_msg)
                        check['details']['security_warnings'].append({
                            'field': field_path,
                            'current_value': field_value,
                            'recommended_value': security_rule['production_value'],
                            'warning': warning_msg
                        })
                
                # Check pattern
                if 'pattern' in security_rule and isinstance(field_value, (str, list)):
                    pattern = security_rule['pattern']
                    values_to_check = [field_value] if isinstance(field_value, str) else field_value
                    
                    for value in values_to_check:
                        if isinstance(value, str) and not re.match(pattern, value):
                            check['status'] = 'warning'
                            warning_msg = security_rule.get('warning', f"Pattern mismatch for {field_path}")
                            check['issues'].append(f"{warning_msg}: {value}")
        
        return check
    
    def _check_forbidden_patterns(self, mapping_results: Dict[str, Any]) -> Dict[str, Any]:
        """Check for forbidden patterns in configuration content"""
        check = {
            'check_name': 'Forbidden Patterns Detection',
            'status': 'passed',
            'issues': [],
            'details': {'pattern_matches': []}
        }
        
        forbidden_patterns = self.security_patterns['forbidden_patterns']
        
        # Check in original extracted configurations
        extracted_configs = mapping_results.get('extracted_configs', {})
        
        for dir_name, dir_configs in extracted_configs.items():
            for config_type, configs in dir_configs.get('configs', {}).items():
                for config_name, config_data in configs.items():
                    # Only check raw content from files
                    if config_type == 'python' and 'config_variables' in config_data:
                        # Check Python config raw values
                        for var_name, var_data in config_data['config_variables'].items():
                            raw_value = var_data.get('raw_value', '') if isinstance(var_data, dict) else str(var_data)
                            
                            for pattern in forbidden_patterns:
                                if re.search(pattern, raw_value, re.IGNORECASE):
                                    check['status'] = 'failed'
                                    check['issues'].append(
                                        f"Forbidden pattern found in {config_name}:{var_name}"
                                    )
                                    check['details']['pattern_matches'].append({
                                        'config': config_name,
                                        'field': var_name,
                                        'pattern': pattern
                                    })
        
        return check
    
    def _validate_configuration_size(self, merged_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate configuration size for performance"""
        check = {
            'check_name': 'Configuration Size Validation',
            'status': 'passed',
            'issues': [],
            'details': {}
        }
        
        try:
            config_json = json.dumps(merged_config, indent=2)
            config_size = len(config_json.encode('utf-8'))
            
            check['details']['config_size_bytes'] = config_size
            check['details']['config_size_mb'] = config_size / (1024 * 1024)
            
            max_size = self.performance_benchmarks['memory_considerations']['max_config_size']
            
            if config_size > max_size:
                check['status'] = 'warning'
                check['issues'].append(
                    f"Configuration size ({config_size} bytes) exceeds recommended maximum ({max_size} bytes)"
                )
            
        except Exception as e:
            check['status'] = 'failed'
            check['issues'].append(f"Could not calculate configuration size: {str(e)}")
        
        return check
    
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
    
    def _update_category_results(self, category: str, results: List[Dict[str, Any]]):
        """Update validation results for a category"""
        category_data = self.validation_results['validation_categories'][category]
        category_data['results'] = results
        
        # Determine category status
        failed_checks = [r for r in results if r['status'] == 'failed']
        warning_checks = [r for r in results if r['status'] == 'warning']
        
        if failed_checks:
            category_data['status'] = 'failed'
        elif warning_checks:
            category_data['status'] = 'warning'
        else:
            category_data['status'] = 'passed'
        
        # Update summary counters
        for result in results:
            self.validation_results['summary']['total_checks'] += 1
            
            if result['status'] == 'passed':
                self.validation_results['summary']['passed_checks'] += 1
            elif result['status'] == 'failed':
                self.validation_results['summary']['failed_checks'] += 1
                if 'critical' in result.get('check_name', '').lower():
                    self.validation_results['summary']['critical_issues'] += 1
            else:  # warning
                self.validation_results['summary']['warnings'] += 1
    
    def _determine_overall_status(self):
        """Determine overall validation status"""
        categories = self.validation_results['validation_categories']
        
        failed_categories = [cat for cat, data in categories.items() if data['status'] == 'failed']
        warning_categories = [cat for cat, data in categories.items() if data['status'] == 'warning']
        
        if failed_categories:
            self.validation_results['overall_status'] = 'failed'
        elif warning_categories:
            self.validation_results['overall_status'] = 'warning'
        else:
            self.validation_results['overall_status'] = 'passed'
    
    def _generate_recommendations(self):
        """Generate recommendations based on validation results"""
        recommendations = []
        
        # Overall status recommendations
        if self.validation_results['overall_status'] == 'failed':
            recommendations.append("❌ Address all critical validation failures before proceeding")
        elif self.validation_results['overall_status'] == 'warning':
            recommendations.append("⚠️ Review validation warnings and address security concerns")
        else:
            recommendations.append("✅ Configuration validation passed - ready for deployment")
        
        # Category-specific recommendations
        for category, data in self.validation_results['validation_categories'].items():
            if data['status'] == 'failed':
                recommendations.append(f"🔴 Fix all {category} validation failures")
            elif data['status'] == 'warning':
                recommendations.append(f"🟡 Review {category} validation warnings")
        
        # Security-specific recommendations
        security_results = self.validation_results['validation_categories']['security']['results']
        for result in security_results:
            if result['status'] in ['failed', 'warning'] and result['issues']:
                recommendations.append(f"🔒 Security: {result['issues'][0]}")
        
        # Performance recommendations
        performance_results = self.validation_results['validation_categories']['performance']['results']
        for result in performance_results:
            if result['status'] == 'warning' and result['issues']:
                recommendations.append(f"⚡ Performance: {result['issues'][0]}")
        
        self.validation_results['recommendations'] = recommendations
    
    def _save_validation_results(self):
        """Save validation results to files"""
        # Save detailed validation results
        results_file = self.validation_dir / 'config_validation_results.json'
        with open(results_file, 'w') as f:
            json.dump(self.validation_results, f, indent=2, default=str)
        
        self.logger.info(f"Validation results saved to {results_file}")
    
    def _generate_validation_report(self):
        """Generate human-readable validation report"""
        summary = self.validation_results['summary']
        
        report = f"""
AI REBUILD MIGRATION - CONFIGURATION VALIDATION REPORT
{'=' * 60}

Generated: {self.validation_results['validation_timestamp']}
Overall Status: {self.validation_results['overall_status'].upper()}

VALIDATION SUMMARY
{'=' * 60}
Total Checks: {summary['total_checks']}
Passed: {summary['passed_checks']}
Failed: {summary['failed_checks']}
Warnings: {summary['warnings']}
Critical Issues: {summary['critical_issues']}

VALIDATION CATEGORIES
{'=' * 60}
"""
        
        for category, data in self.validation_results['validation_categories'].items():
            status_symbol = {
                'passed': '✅',
                'warning': '⚠️',
                'failed': '❌'
            }.get(data['status'], '❓')
            
            report += f"\n{status_symbol} {category.upper()}: {data['status'].upper()}\n"
            
            for result in data['results']:
                report += f"  • {result['check_name']}: {result['status']}\n"
                
                if result['issues']:
                    for issue in result['issues'][:3]:  # Show first 3 issues
                        report += f"    - {issue}\n"
                    
                    if len(result['issues']) > 3:
                        report += f"    ... and {len(result['issues']) - 3} more issues\n"
        
        if self.validation_results['recommendations']:
            report += f"\nRECOMMENDATIONS\n{'=' * 60}\n"
            for i, rec in enumerate(self.validation_results['recommendations'], 1):
                report += f"{i}. {rec}\n"
        
        # Save report
        report_file = self.validation_dir / 'config_validation_report.txt'
        with open(report_file, 'w') as f:
            f.write(report)
        
        print("\nConfiguration Validation Report:")
        print("=" * 40)
        print(report)

def main():
    """Main entry point"""
    # Default configuration
    config = ValidationConfig(
        mapping_dir=str(Path.cwd() / 'config_mapping'),
        validation_dir=str(Path.cwd() / 'config_validation'),
        strict_mode=True,
        check_syntax=True,
        check_completeness=True,
        check_compatibility=True,
        check_security=True,
        check_performance=True,
        generate_validation_report=True
    )
    
    # Create validator and run
    validator = ConfigValidator(config)
    results = validator.validate_all_configs()
    
    print(f"\nConfiguration Validation Completed!")
    print(f"Overall Status: {results['overall_status'].upper()}")
    print(f"Results saved to: {config.validation_dir}")

if __name__ == "__main__":
    main()