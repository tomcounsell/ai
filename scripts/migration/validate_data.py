#!/usr/bin/env python3
"""
Data Validation Tool for AI Rebuild Migration
Validates data integrity and completeness after transformation.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass
import hashlib

@dataclass
class ValidationConfig:
    """Configuration for data validation"""
    export_dir: str
    transform_dir: str
    validation_dir: str
    strict_mode: bool = True
    generate_report: bool = True
    check_data_integrity: bool = True
    check_schema_compliance: bool = True
    check_referential_integrity: bool = True

class DataValidator:
    """Main data validation handler"""
    
    def __init__(self, config: ValidationConfig):
        self.config = config
        self.export_dir = Path(config.export_dir)
        self.transform_dir = Path(config.transform_dir)
        self.validation_dir = Path(config.validation_dir)
        self.validation_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.validation_dir / 'validation.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Validation results
        self.validation_results = {
            'timestamp': datetime.utcnow().isoformat(),
            'overall_status': 'pending',
            'components': {},
            'errors': [],
            'warnings': [],
            'summary': {}
        }
    
    def validate_all_data(self) -> Dict[str, Any]:
        """Validate all transformed data"""
        self.logger.info("Starting comprehensive data validation")
        
        try:
            # Load manifests
            export_manifest = self._load_manifest(self.export_dir / 'export_manifest.json')
            transform_manifest = self._load_manifest(self.transform_dir / 'transform_manifest.json')
            
            if not export_manifest or not transform_manifest:
                raise FileNotFoundError("Required manifests not found")
            
            # Validate each component
            for component in transform_manifest['transformed_components']:
                component_name = component['component']
                self.logger.info(f"Validating {component_name}")
                
                component_result = self._validate_component(component_name, component)
                self.validation_results['components'][component_name] = component_result
            
            # Perform cross-component validation
            self._validate_referential_integrity()
            
            # Generate overall status
            self._determine_overall_status()
            
            # Generate validation report
            if self.config.generate_report:
                self._generate_validation_report()
            
            self.logger.info("Data validation completed")
            return self.validation_results
            
        except Exception as e:
            self.logger.error(f"Validation failed: {str(e)}")
            self.validation_results['overall_status'] = 'failed'
            self.validation_results['errors'].append({
                'type': 'system_error',
                'message': str(e),
                'timestamp': datetime.utcnow().isoformat()
            })
            return self.validation_results
    
    def _load_manifest(self, manifest_path: Path) -> Optional[Dict[str, Any]]:
        """Load and validate manifest file"""
        if not manifest_path.exists():
            self.logger.error(f"Manifest not found: {manifest_path}")
            return None
        
        try:
            with open(manifest_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load manifest {manifest_path}: {str(e)}")
            return None
    
    def _validate_component(self, component_name: str, component_info: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a specific component"""
        result = {
            'status': 'pending',
            'checks_performed': [],
            'errors': [],
            'warnings': [],
            'metrics': {}
        }
        
        try:
            # Load original and transformed data
            original_data = self._load_component_data(self.export_dir, component_info.get('file', ''))
            transformed_data = self._load_component_data(self.transform_dir, component_info.get('output_file', ''))
            
            if original_data is None or transformed_data is None:
                result['status'] = 'failed'
                result['errors'].append('Failed to load component data')
                return result
            
            # Perform validation checks
            if self.config.check_data_integrity:
                integrity_result = self._check_data_integrity(component_name, original_data, transformed_data)
                result['checks_performed'].append('data_integrity')
                result['metrics']['integrity'] = integrity_result
                
                if integrity_result['status'] != 'passed':
                    result['errors'].extend(integrity_result['errors'])
                    result['warnings'].extend(integrity_result['warnings'])
            
            if self.config.check_schema_compliance:
                schema_result = self._check_schema_compliance(component_name, transformed_data)
                result['checks_performed'].append('schema_compliance')
                result['metrics']['schema'] = schema_result
                
                if schema_result['status'] != 'passed':
                    result['errors'].extend(schema_result['errors'])
                    result['warnings'].extend(schema_result['warnings'])
            
            # Component-specific validations
            component_result = self._validate_component_specific(component_name, original_data, transformed_data)
            result['checks_performed'].append('component_specific')
            result['metrics']['component_specific'] = component_result
            
            if component_result['status'] != 'passed':
                result['errors'].extend(component_result['errors'])
                result['warnings'].extend(component_result['warnings'])
            
            # Determine component status
            if result['errors']:
                result['status'] = 'failed' if self.config.strict_mode else 'warning'
            elif result['warnings']:
                result['status'] = 'warning'
            else:
                result['status'] = 'passed'
            
        except Exception as e:
            result['status'] = 'failed'
            result['errors'].append(f"Validation error: {str(e)}")
        
        return result
    
    def _load_component_data(self, directory: Path, filename: str) -> Optional[Any]:
        """Load component data from file"""
        if not filename:
            return None
        
        file_path = directory / filename
        if not file_path.exists():
            return None
        
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load {file_path}: {str(e)}")
            return None
    
    def _check_data_integrity(self, component_name: str, original: Any, transformed: Any) -> Dict[str, Any]:
        """Check data integrity between original and transformed data"""
        result = {
            'status': 'passed',
            'errors': [],
            'warnings': [],
            'metrics': {}
        }
        
        try:
            # Count records
            orig_count = len(original) if isinstance(original, list) else 1
            trans_count = len(transformed) if isinstance(transformed, list) else 1
            
            result['metrics']['original_count'] = orig_count
            result['metrics']['transformed_count'] = trans_count
            
            # Check record count consistency
            if component_name in ['chat_history', 'workspaces'] and orig_count != trans_count:
                result['warnings'].append(f"Record count mismatch: {orig_count} → {trans_count}")
            
            # Check for data completeness
            if isinstance(original, list) and isinstance(transformed, list):
                completeness_score = self._calculate_completeness(original, transformed)
                result['metrics']['completeness_score'] = completeness_score
                
                if completeness_score < 0.95:
                    result['errors'].append(f"Low data completeness: {completeness_score:.2%}")
                    result['status'] = 'failed'
                elif completeness_score < 0.98:
                    result['warnings'].append(f"Moderate data completeness: {completeness_score:.2%}")
            
            # Check for data consistency
            consistency_issues = self._check_data_consistency(component_name, transformed)
            if consistency_issues:
                result['warnings'].extend(consistency_issues)
                if len(consistency_issues) > 5:  # Too many issues
                    result['status'] = 'failed'
            
            # Generate data checksums for integrity verification
            result['metrics']['original_checksum'] = self._calculate_checksum(original)
            result['metrics']['transformed_checksum'] = self._calculate_checksum(transformed)
            
        except Exception as e:
            result['status'] = 'failed'
            result['errors'].append(f"Integrity check failed: {str(e)}")
        
        return result
    
    def _check_schema_compliance(self, component_name: str, data: Any) -> Dict[str, Any]:
        """Check schema compliance of transformed data"""
        result = {
            'status': 'passed',
            'errors': [],
            'warnings': [],
            'metrics': {}
        }
        
        try:
            schema = self._get_component_schema(component_name)
            if not schema:
                result['warnings'].append(f"No schema defined for {component_name}")
                return result
            
            compliance_issues = self._validate_against_schema(data, schema)
            result['metrics']['schema_violations'] = len(compliance_issues)
            
            for issue in compliance_issues:
                if issue['severity'] == 'error':
                    result['errors'].append(issue['message'])
                else:
                    result['warnings'].append(issue['message'])
            
            if result['errors']:
                result['status'] = 'failed'
            elif result['warnings']:
                result['status'] = 'warning'
            
        except Exception as e:
            result['status'] = 'failed'
            result['errors'].append(f"Schema validation failed: {str(e)}")
        
        return result
    
    def _validate_component_specific(self, component_name: str, original: Any, transformed: Any) -> Dict[str, Any]:
        """Perform component-specific validations"""
        result = {
            'status': 'passed',
            'errors': [],
            'warnings': [],
            'metrics': {}
        }
        
        try:
            if component_name == 'chat_history':
                result = self._validate_chat_history(original, transformed)
            elif component_name == 'preferences':
                result = self._validate_preferences(original, transformed)
            elif component_name == 'workspaces':
                result = self._validate_workspaces(original, transformed)
            elif component_name == 'tool_metrics':
                result = self._validate_tool_metrics(original, transformed)
            elif component_name == 'system_state':
                result = self._validate_system_state(original, transformed)
            else:
                result['warnings'].append(f"No specific validation for {component_name}")
                
        except Exception as e:
            result['status'] = 'failed'
            result['errors'].append(f"Component validation failed: {str(e)}")
        
        return result
    
    def _validate_chat_history(self, original: List, transformed: List) -> Dict[str, Any]:
        """Validate chat history transformation"""
        result = {'status': 'passed', 'errors': [], 'warnings': [], 'metrics': {}}
        
        # Check required fields
        required_fields = ['id', 'session_id', 'user_id', 'content', 'created_at']
        for i, record in enumerate(transformed):
            for field in required_fields:
                if field not in record or not record[field]:
                    result['errors'].append(f"Record {i}: Missing required field '{field}'")
        
        # Check content preservation
        content_preserved = 0
        for orig_record in original:
            orig_content = orig_record.get('data', {}).get('content', '') or orig_record.get('data', {}).get('message', '')
            if any(orig_content in trans['content'] for trans in transformed):
                content_preserved += 1
        
        preservation_rate = content_preserved / len(original) if original else 0
        result['metrics']['content_preservation_rate'] = preservation_rate
        
        if preservation_rate < 0.9:
            result['errors'].append(f"Low content preservation rate: {preservation_rate:.2%}")
            result['status'] = 'failed'
        
        return result
    
    def _validate_preferences(self, original: List, transformed: Dict) -> Dict[str, Any]:
        """Validate preferences transformation"""
        result = {'status': 'passed', 'errors': [], 'warnings': [], 'metrics': {}}
        
        # Check required structure
        required_sections = ['user_preferences', 'agent_configurations', 'system_settings']
        for section in required_sections:
            if section not in transformed:
                result['errors'].append(f"Missing required section: {section}")
        
        # Check migration info
        if 'migration_info' not in transformed:
            result['warnings'].append("Missing migration metadata")
        
        return result
    
    def _validate_workspaces(self, original: List, transformed: List) -> Dict[str, Any]:
        """Validate workspaces transformation"""
        result = {'status': 'passed', 'errors': [], 'warnings': [], 'metrics': {}}
        
        # Check required fields for each workspace
        required_fields = ['id', 'name', 'type', 'status']
        for i, workspace in enumerate(transformed):
            for field in required_fields:
                if field not in workspace:
                    result['errors'].append(f"Workspace {i}: Missing required field '{field}'")
        
        # Check unique names
        names = [w.get('name') for w in transformed if w.get('name')]
        if len(names) != len(set(names)):
            result['errors'].append("Duplicate workspace names found")
        
        return result
    
    def _validate_tool_metrics(self, original: List, transformed: Dict) -> Dict[str, Any]:
        """Validate tool metrics transformation"""
        result = {'status': 'passed', 'errors': [], 'warnings': [], 'metrics': {}}
        
        # Check required structure
        required_sections = ['usage_statistics', 'performance_metrics']
        for section in required_sections:
            if section not in transformed:
                result['errors'].append(f"Missing required section: {section}")
        
        # Validate numeric metrics
        usage_stats = transformed.get('usage_statistics', {})
        for metric in ['total_sessions', 'total_tool_calls']:
            if metric in usage_stats and not isinstance(usage_stats[metric], (int, float)):
                result['errors'].append(f"Invalid type for {metric}: expected number")
        
        return result
    
    def _validate_system_state(self, original: Dict, transformed: Dict) -> Dict[str, Any]:
        """Validate system state transformation"""
        result = {'status': 'passed', 'errors': [], 'warnings': [], 'metrics': {}}
        
        # Check required sections
        required_sections = ['environment', 'dependencies', 'project_structure']
        for section in required_sections:
            if section not in transformed:
                result['errors'].append(f"Missing required section: {section}")
        
        # Check environment info preservation
        env_info = transformed.get('environment', {})
        if 'python_version' not in env_info:
            result['warnings'].append("Missing Python version information")
        
        return result
    
    def _validate_referential_integrity(self):
        """Validate referential integrity across components"""
        self.logger.info("Checking referential integrity")
        
        try:
            # Load all transformed data
            chat_data = self._load_component_data(self.transform_dir, 'transformed_chat_history.json') or []
            workspace_data = self._load_component_data(self.transform_dir, 'transformed_workspaces.json') or []
            
            # Check for orphaned references
            if isinstance(chat_data, list) and isinstance(workspace_data, list):
                workspace_ids = {w.get('id') for w in workspace_data if w.get('id')}
                
                orphaned_refs = 0
                for chat in chat_data:
                    workspace_ref = chat.get('metadata', {}).get('workspace_id')
                    if workspace_ref and workspace_ref not in workspace_ids:
                        orphaned_refs += 1
                
                if orphaned_refs > 0:
                    self.validation_results['warnings'].append({
                        'type': 'referential_integrity',
                        'message': f"Found {orphaned_refs} orphaned workspace references in chat history",
                        'component': 'cross_component'
                    })
        
        except Exception as e:
            self.validation_results['errors'].append({
                'type': 'referential_integrity_error',
                'message': f"Failed to check referential integrity: {str(e)}",
                'component': 'cross_component'
            })
    
    def _calculate_completeness(self, original: List, transformed: List) -> float:
        """Calculate data completeness score"""
        if not original:
            return 1.0
        
        # Simple completeness check based on record count and content presence
        count_ratio = min(len(transformed) / len(original), 1.0)
        
        # Check content preservation (simplified)
        content_matches = 0
        for orig in original[:10]:  # Sample first 10 records
            orig_content = str(orig).lower()
            if any(orig_content[:50] in str(trans).lower() for trans in transformed):
                content_matches += 1
        
        content_ratio = content_matches / min(10, len(original))
        
        return (count_ratio + content_ratio) / 2
    
    def _check_data_consistency(self, component_name: str, data: Any) -> List[str]:
        """Check for data consistency issues"""
        issues = []
        
        if isinstance(data, list):
            # Check for duplicate IDs
            ids = [item.get('id') for item in data if item.get('id')]
            if len(ids) != len(set(ids)):
                issues.append(f"Duplicate IDs found in {component_name}")
            
            # Check for missing timestamps
            for i, item in enumerate(data):
                if isinstance(item, dict):
                    if 'created_at' in item and not item['created_at']:
                        issues.append(f"Record {i}: Missing created_at timestamp")
        
        return issues
    
    def _calculate_checksum(self, data: Any) -> str:
        """Calculate checksum for data integrity verification"""
        data_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(data_str.encode()).hexdigest()
    
    def _get_component_schema(self, component_name: str) -> Optional[Dict[str, Any]]:
        """Get schema definition for component"""
        schemas = {
            'chat_history': {
                'type': 'array',
                'items': {
                    'required': ['id', 'session_id', 'user_id', 'content', 'created_at'],
                    'properties': {
                        'id': {'type': 'string'},
                        'session_id': {'type': 'string'},
                        'user_id': {'type': 'string'},
                        'content': {'type': 'string', 'minLength': 1},
                        'created_at': {'type': 'string', 'format': 'datetime'}
                    }
                }
            },
            'preferences': {
                'type': 'object',
                'required': ['user_preferences', 'agent_configurations'],
                'properties': {
                    'user_preferences': {'type': 'object'},
                    'agent_configurations': {'type': 'object'}
                }
            },
            'workspaces': {
                'type': 'array',
                'items': {
                    'required': ['id', 'name', 'type', 'status'],
                    'properties': {
                        'id': {'type': 'string'},
                        'name': {'type': 'string', 'minLength': 1},
                        'type': {'type': 'string'},
                        'status': {'type': 'string'}
                    }
                }
            }
        }
        
        return schemas.get(component_name)
    
    def _validate_against_schema(self, data: Any, schema: Dict[str, Any]) -> List[Dict[str, str]]:
        """Validate data against schema (simplified validation)"""
        issues = []
        
        try:
            if schema.get('type') == 'array' and not isinstance(data, list):
                issues.append({'severity': 'error', 'message': 'Expected array type'})
                return issues
            
            if schema.get('type') == 'object' and not isinstance(data, dict):
                issues.append({'severity': 'error', 'message': 'Expected object type'})
                return issues
            
            # Check required fields
            if schema.get('type') == 'object' and 'required' in schema:
                for field in schema['required']:
                    if field not in data:
                        issues.append({'severity': 'error', 'message': f'Missing required field: {field}'})
            
            # Check array items
            if schema.get('type') == 'array' and 'items' in schema and isinstance(data, list):
                item_schema = schema['items']
                for i, item in enumerate(data):
                    item_issues = self._validate_against_schema(item, item_schema)
                    for issue in item_issues:
                        issues.append({
                            'severity': issue['severity'],
                            'message': f'Item {i}: {issue["message"]}'
                        })
        
        except Exception as e:
            issues.append({'severity': 'error', 'message': f'Schema validation error: {str(e)}'})
        
        return issues
    
    def _determine_overall_status(self):
        """Determine overall validation status"""
        component_statuses = [comp['status'] for comp in self.validation_results['components'].values()]
        
        if 'failed' in component_statuses or self.validation_results['errors']:
            self.validation_results['overall_status'] = 'failed'
        elif 'warning' in component_statuses or self.validation_results['warnings']:
            self.validation_results['overall_status'] = 'warning'
        else:
            self.validation_results['overall_status'] = 'passed'
        
        # Generate summary
        self.validation_results['summary'] = {
            'total_components': len(self.validation_results['components']),
            'passed_components': sum(1 for s in component_statuses if s == 'passed'),
            'warning_components': sum(1 for s in component_statuses if s == 'warning'),
            'failed_components': sum(1 for s in component_statuses if s == 'failed'),
            'total_errors': len(self.validation_results['errors']),
            'total_warnings': len(self.validation_results['warnings'])
        }
    
    def _generate_validation_report(self):
        """Generate comprehensive validation report"""
        self.logger.info("Generating validation report")
        
        # Save detailed results
        with open(self.validation_dir / 'validation_results.json', 'w') as f:
            json.dump(self.validation_results, f, indent=2, default=str)
        
        # Generate human-readable report
        report = f"""
AI REBUILD MIGRATION - DATA VALIDATION REPORT
{'=' * 60}

Generated: {self.validation_results['timestamp']}
Overall Status: {self.validation_results['overall_status'].upper()}

SUMMARY
{'=' * 60}
Total Components Validated: {self.validation_results['summary']['total_components']}
Passed: {self.validation_results['summary']['passed_components']}
Warnings: {self.validation_results['summary']['warning_components']}
Failed: {self.validation_results['summary']['failed_components']}

Total Errors: {self.validation_results['summary']['total_errors']}
Total Warnings: {self.validation_results['summary']['total_warnings']}

COMPONENT DETAILS
{'=' * 60}
"""
        
        for component_name, result in self.validation_results['components'].items():
            report += f"\n{component_name.upper()}: {result['status'].upper()}\n"
            report += f"  Checks Performed: {', '.join(result['checks_performed'])}\n"
            
            if result['errors']:
                report += f"  Errors ({len(result['errors'])}):\n"
                for error in result['errors']:
                    report += f"    • {error}\n"
            
            if result['warnings']:
                report += f"  Warnings ({len(result['warnings'])}):\n"
                for warning in result['warnings']:
                    report += f"    • {warning}\n"
            
            # Add metrics
            if result['metrics']:
                report += f"  Metrics:\n"
                for metric, value in result['metrics'].items():
                    if isinstance(value, dict):
                        report += f"    • {metric}: {json.dumps(value, default=str)}\n"
                    else:
                        report += f"    • {metric}: {value}\n"
        
        if self.validation_results['errors']:
            report += f"\nGLOBAL ERRORS\n{'=' * 60}\n"
            for error in self.validation_results['errors']:
                report += f"• {error.get('message', error)}\n"
        
        if self.validation_results['warnings']:
            report += f"\nGLOBAL WARNINGS\n{'=' * 60}\n"
            for warning in self.validation_results['warnings']:
                report += f"• {warning.get('message', warning)}\n"
        
        report += f"\nRECOMMENDATIONS\n{'=' * 60}\n"
        
        if self.validation_results['overall_status'] == 'passed':
            report += "✓ All validations passed. Data is ready for migration.\n"
        elif self.validation_results['overall_status'] == 'warning':
            report += "⚠ Validation completed with warnings. Review warnings before proceeding.\n"
        else:
            report += "✗ Validation failed. Address all errors before proceeding with migration.\n"
        
        # Save report
        with open(self.validation_dir / 'validation_report.txt', 'w') as f:
            f.write(report)
        
        print("\nValidation Report Generated:")
        print("=" * 40)
        print(report)

def main():
    """Main entry point"""
    # Default configuration
    config = ValidationConfig(
        export_dir=str(Path.cwd() / 'migration_export'),
        transform_dir=str(Path.cwd() / 'migration_transform'),
        validation_dir=str(Path.cwd() / 'migration_validation'),
        strict_mode=True,
        generate_report=True,
        check_data_integrity=True,
        check_schema_compliance=True,
        check_referential_integrity=True
    )
    
    # Create validator and run
    validator = DataValidator(config)
    results = validator.validate_all_data()
    
    print(f"\nValidation Status: {results['overall_status'].upper()}")
    print(f"Results saved to: {config.validation_dir}")

if __name__ == "__main__":
    main()