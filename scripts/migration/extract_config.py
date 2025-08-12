#!/usr/bin/env python3
"""
Configuration Extraction Tool for AI Rebuild Migration
Extracts current configuration from various sources and formats.
"""

import json
import logging
import os
import yaml
import configparser
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass

@dataclass
class ExtractionConfig:
    """Configuration for config extraction"""
    source_dirs: List[str]
    output_dir: str
    include_env_vars: bool = True
    include_json_configs: bool = True
    include_yaml_configs: bool = True
    include_ini_configs: bool = True
    include_python_configs: bool = True
    backup_existing: bool = True
    validate_configs: bool = True

class ConfigExtractor:
    """Main configuration extraction handler"""
    
    def __init__(self, config: ExtractionConfig):
        self.config = config
        self.output_dir = Path(config.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.output_dir / 'config_extraction.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Extraction results
        self.extraction_results = {
            'extraction_timestamp': datetime.utcnow().isoformat(),
            'source_directories': config.source_dirs,
            'extracted_configs': {},
            'environment_variables': {},
            'config_inventory': [],
            'extraction_summary': {},
            'validation_results': {}
        }
    
    def extract_all_configs(self) -> Dict[str, Any]:
        """Extract all configuration data"""
        self.logger.info("Starting configuration extraction")
        
        try:
            # Extract environment variables
            if self.config.include_env_vars:
                self._extract_environment_variables()
            
            # Scan and extract configurations from all source directories
            for source_dir in self.config.source_dirs:
                self._extract_from_directory(source_dir)
            
            # Extract runtime configurations
            self._extract_runtime_configs()
            
            # Validate extracted configurations
            if self.config.validate_configs:
                self._validate_extracted_configs()
            
            # Generate extraction summary
            self._generate_extraction_summary()
            
            # Save extraction results
            self._save_extraction_results()
            
            self.logger.info("Configuration extraction completed successfully")
            return self.extraction_results
            
        except Exception as e:
            self.logger.error(f"Configuration extraction failed: {str(e)}")
            raise
    
    def _extract_environment_variables(self):
        """Extract relevant environment variables"""
        self.logger.info("Extracting environment variables")
        
        # Filter environment variables for relevant ones
        relevant_prefixes = [
            'AI_REBUILD_', 'PYTHONPATH', 'PATH', 'HOME', 'USER',
            'OPENAI_', 'ANTHROPIC_', 'TELEGRAM_', 'DATABASE_'
        ]
        
        env_vars = {}
        for key, value in os.environ.items():
            if any(key.startswith(prefix) for prefix in relevant_prefixes):
                # Mask sensitive values
                if any(sensitive in key.lower() for sensitive in ['key', 'token', 'secret', 'password']):
                    env_vars[key] = '***MASKED***'
                else:
                    env_vars[key] = value
        
        self.extraction_results['environment_variables'] = {
            'extracted_at': datetime.utcnow().isoformat(),
            'variables': env_vars,
            'total_relevant_vars': len(env_vars)
        }
    
    def _extract_from_directory(self, source_dir: str):
        """Extract configurations from a specific directory"""
        source_path = Path(source_dir)
        if not source_path.exists():
            self.logger.warning(f"Source directory not found: {source_dir}")
            return
        
        self.logger.info(f"Extracting configurations from: {source_dir}")
        
        dir_configs = {
            'source_directory': source_dir,
            'extraction_timestamp': datetime.utcnow().isoformat(),
            'configs': {}
        }
        
        # Extract JSON configurations
        if self.config.include_json_configs:
            json_configs = self._extract_json_configs(source_path)
            if json_configs:
                dir_configs['configs']['json'] = json_configs
        
        # Extract YAML configurations
        if self.config.include_yaml_configs:
            yaml_configs = self._extract_yaml_configs(source_path)
            if yaml_configs:
                dir_configs['configs']['yaml'] = yaml_configs
        
        # Extract INI configurations
        if self.config.include_ini_configs:
            ini_configs = self._extract_ini_configs(source_path)
            if ini_configs:
                dir_configs['configs']['ini'] = ini_configs
        
        # Extract Python configurations
        if self.config.include_python_configs:
            python_configs = self._extract_python_configs(source_path)
            if python_configs:
                dir_configs['configs']['python'] = python_configs
        
        # Store directory configurations
        dir_key = source_path.name or 'root'
        self.extraction_results['extracted_configs'][dir_key] = dir_configs
    
    def _extract_json_configs(self, source_path: Path) -> Dict[str, Any]:
        """Extract JSON configuration files"""
        json_configs = {}
        
        # Common JSON config files
        json_patterns = [
            '*.json', '*config*.json', '*settings*.json', 
            'package.json', 'tsconfig.json', 'pyproject.toml'
        ]
        
        for pattern in json_patterns:
            for json_file in source_path.rglob(pattern):
                if json_file.is_file():
                    try:
                        config_name = str(json_file.relative_to(source_path))
                        
                        # Handle different file types
                        if json_file.suffix == '.toml':
                            import tomli
                            with open(json_file, 'rb') as f:
                                config_data = tomli.load(f)
                        else:
                            with open(json_file, 'r', encoding='utf-8') as f:
                                config_data = json.load(f)
                        
                        json_configs[config_name] = {
                            'file_path': str(json_file),
                            'file_size': json_file.stat().st_size,
                            'modified_time': datetime.fromtimestamp(json_file.stat().st_mtime).isoformat(),
                            'data': config_data,
                            'extraction_notes': []
                        }
                        
                    except Exception as e:
                        self.logger.warning(f"Could not extract JSON config from {json_file}: {str(e)}")
                        json_configs[config_name] = {
                            'file_path': str(json_file),
                            'extraction_error': str(e)
                        }
        
        return json_configs
    
    def _extract_yaml_configs(self, source_path: Path) -> Dict[str, Any]:
        """Extract YAML configuration files"""
        yaml_configs = {}
        
        # Common YAML config files
        yaml_patterns = ['*.yaml', '*.yml', '*config*.yaml', '*config*.yml']
        
        for pattern in yaml_patterns:
            for yaml_file in source_path.rglob(pattern):
                if yaml_file.is_file():
                    try:
                        config_name = str(yaml_file.relative_to(source_path))
                        
                        with open(yaml_file, 'r', encoding='utf-8') as f:
                            config_data = yaml.safe_load(f)
                        
                        yaml_configs[config_name] = {
                            'file_path': str(yaml_file),
                            'file_size': yaml_file.stat().st_size,
                            'modified_time': datetime.fromtimestamp(yaml_file.stat().st_mtime).isoformat(),
                            'data': config_data,
                            'extraction_notes': []
                        }
                        
                    except Exception as e:
                        self.logger.warning(f"Could not extract YAML config from {yaml_file}: {str(e)}")
                        yaml_configs[config_name] = {
                            'file_path': str(yaml_file),
                            'extraction_error': str(e)
                        }
        
        return yaml_configs
    
    def _extract_ini_configs(self, source_path: Path) -> Dict[str, Any]:
        """Extract INI configuration files"""
        ini_configs = {}
        
        # Common INI config files
        ini_patterns = ['*.ini', '*.cfg', '*.conf', 'setup.cfg', 'tox.ini']
        
        for pattern in ini_patterns:
            for ini_file in source_path.rglob(pattern):
                if ini_file.is_file():
                    try:
                        config_name = str(ini_file.relative_to(source_path))
                        
                        config_parser = configparser.ConfigParser()
                        config_parser.read(ini_file, encoding='utf-8')
                        
                        # Convert to dictionary
                        config_data = {}
                        for section_name in config_parser.sections():
                            config_data[section_name] = dict(config_parser[section_name])
                        
                        ini_configs[config_name] = {
                            'file_path': str(ini_file),
                            'file_size': ini_file.stat().st_size,
                            'modified_time': datetime.fromtimestamp(ini_file.stat().st_mtime).isoformat(),
                            'data': config_data,
                            'extraction_notes': []
                        }
                        
                    except Exception as e:
                        self.logger.warning(f"Could not extract INI config from {ini_file}: {str(e)}")
                        ini_configs[config_name] = {
                            'file_path': str(ini_file),
                            'extraction_error': str(e)
                        }
        
        return ini_configs
    
    def _extract_python_configs(self, source_path: Path) -> Dict[str, Any]:
        """Extract Python configuration modules"""
        python_configs = {}
        
        # Look for common Python config files
        config_patterns = ['*config*.py', '*settings*.py', 'setup.py']
        
        for pattern in config_patterns:
            for py_file in source_path.rglob(pattern):
                if py_file.is_file():
                    try:
                        config_name = str(py_file.relative_to(source_path))
                        
                        # Read Python file content (don't execute for security)
                        with open(py_file, 'r', encoding='utf-8') as f:
                            content = f.read()
                        
                        # Extract configuration-like assignments
                        config_vars = self._parse_python_config_vars(content)
                        
                        python_configs[config_name] = {
                            'file_path': str(py_file),
                            'file_size': py_file.stat().st_size,
                            'modified_time': datetime.fromtimestamp(py_file.stat().st_mtime).isoformat(),
                            'config_variables': config_vars,
                            'extraction_notes': ['Extracted variables only, not executed']
                        }
                        
                    except Exception as e:
                        self.logger.warning(f"Could not extract Python config from {py_file}: {str(e)}")
                        python_configs[config_name] = {
                            'file_path': str(py_file),
                            'extraction_error': str(e)
                        }
        
        return python_configs
    
    def _parse_python_config_vars(self, content: str) -> Dict[str, Any]:
        """Parse Python configuration variables from source code"""
        import re
        import ast
        
        config_vars = {}
        
        # Find simple variable assignments
        assignment_pattern = r'^(\w+)\s*=\s*(.+)$'
        
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            match = re.match(assignment_pattern, line)
            if match:
                var_name, var_value = match.groups()
                
                # Skip private variables and imports
                if var_name.startswith('_') or 'import' in line:
                    continue
                
                try:
                    # Try to safely evaluate the value
                    parsed_value = ast.literal_eval(var_value)
                    config_vars[var_name] = {
                        'value': parsed_value,
                        'raw_value': var_value,
                        'type': type(parsed_value).__name__
                    }
                except:
                    # If evaluation fails, store as string
                    config_vars[var_name] = {
                        'value': var_value,
                        'raw_value': var_value,
                        'type': 'unparsed'
                    }
        
        return config_vars
    
    def _extract_runtime_configs(self):
        """Extract runtime configuration information"""
        self.logger.info("Extracting runtime configurations")
        
        runtime_configs = {
            'python_version': os.sys.version,
            'python_path': os.sys.path,
            'platform': os.name,
            'working_directory': str(Path.cwd()),
            'user_home': str(Path.home()),
            'temp_directory': str(Path('/tmp') if Path('/tmp').exists() else Path.cwd() / 'temp')
        }
        
        # Try to get additional system information
        try:
            import platform
            runtime_configs.update({
                'system': platform.system(),
                'machine': platform.machine(),
                'processor': platform.processor(),
                'python_implementation': platform.python_implementation()
            })
        except ImportError:
            pass
        
        # Try to get pip information
        try:
            import subprocess
            result = subprocess.run(['pip', 'list', '--format=json'], 
                                  capture_output=True, text=True, check=True)
            runtime_configs['installed_packages'] = json.loads(result.stdout)
        except:
            runtime_configs['installed_packages'] = []
        
        self.extraction_results['runtime_config'] = {
            'extracted_at': datetime.utcnow().isoformat(),
            'data': runtime_configs
        }
    
    def _validate_extracted_configs(self):
        """Validate extracted configurations"""
        self.logger.info("Validating extracted configurations")
        
        validation_results = {
            'validation_timestamp': datetime.utcnow().isoformat(),
            'validation_summary': {
                'total_configs': 0,
                'valid_configs': 0,
                'invalid_configs': 0,
                'warnings': 0
            },
            'config_validations': {}
        }
        
        # Validate each extracted configuration
        for dir_name, dir_configs in self.extraction_results['extracted_configs'].items():
            dir_validation = {
                'directory': dir_name,
                'validations': {}
            }
            
            for config_type, configs in dir_configs.get('configs', {}).items():
                for config_name, config_data in configs.items():
                    validation_results['validation_summary']['total_configs'] += 1
                    
                    config_validation = self._validate_single_config(
                        config_name, config_data, config_type
                    )
                    
                    if config_validation['status'] == 'valid':
                        validation_results['validation_summary']['valid_configs'] += 1
                    elif config_validation['status'] == 'invalid':
                        validation_results['validation_summary']['invalid_configs'] += 1
                    else:
                        validation_results['validation_summary']['warnings'] += 1
                    
                    dir_validation['validations'][config_name] = config_validation
            
            validation_results['config_validations'][dir_name] = dir_validation
        
        self.extraction_results['validation_results'] = validation_results
    
    def _validate_single_config(self, config_name: str, config_data: Dict[str, Any], config_type: str) -> Dict[str, Any]:
        """Validate a single configuration"""
        validation = {
            'config_name': config_name,
            'config_type': config_type,
            'status': 'valid',
            'issues': [],
            'recommendations': []
        }
        
        try:
            # Check if extraction was successful
            if 'extraction_error' in config_data:
                validation['status'] = 'invalid'
                validation['issues'].append(f"Extraction failed: {config_data['extraction_error']}")
                return validation
            
            # Check if configuration data exists
            if 'data' not in config_data and 'config_variables' not in config_data:
                validation['status'] = 'invalid'
                validation['issues'].append("No configuration data found")
                return validation
            
            # Type-specific validations
            if config_type == 'json':
                validation.update(self._validate_json_config(config_data))
            elif config_type == 'yaml':
                validation.update(self._validate_yaml_config(config_data))
            elif config_type == 'ini':
                validation.update(self._validate_ini_config(config_data))
            elif config_type == 'python':
                validation.update(self._validate_python_config(config_data))
            
            # Common validations
            if 'file_path' in config_data:
                file_path = Path(config_data['file_path'])
                if not file_path.exists():
                    validation['issues'].append("Configuration file no longer exists")
                    validation['status'] = 'invalid'
                
                # Check file size (warn if very large)
                if config_data.get('file_size', 0) > 1024 * 1024:  # 1MB
                    validation['recommendations'].append("Large configuration file - consider splitting")
        
        except Exception as e:
            validation['status'] = 'invalid'
            validation['issues'].append(f"Validation error: {str(e)}")
        
        return validation
    
    def _validate_json_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate JSON configuration"""
        validation_updates = {
            'json_specific': {
                'is_valid_json': True,
                'has_nested_objects': False,
                'max_nesting_depth': 0
            }
        }
        
        data = config_data.get('data', {})
        
        if isinstance(data, dict):
            validation_updates['json_specific']['max_nesting_depth'] = self._calculate_nesting_depth(data)
            validation_updates['json_specific']['has_nested_objects'] = validation_updates['json_specific']['max_nesting_depth'] > 1
        
        return validation_updates
    
    def _validate_yaml_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate YAML configuration"""
        validation_updates = {
            'yaml_specific': {
                'is_valid_yaml': True,
                'has_anchors': False,  # YAML anchors and aliases
                'complexity_score': 0
            }
        }
        
        # YAML-specific validation would go here
        # For now, basic structure validation
        data = config_data.get('data', {})
        
        if isinstance(data, dict):
            validation_updates['yaml_specific']['complexity_score'] = len(str(data))
        
        return validation_updates
    
    def _validate_ini_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate INI configuration"""
        validation_updates = {
            'ini_specific': {
                'section_count': 0,
                'total_options': 0,
                'has_default_section': False
            }
        }
        
        data = config_data.get('data', {})
        
        if isinstance(data, dict):
            validation_updates['ini_specific']['section_count'] = len(data)
            validation_updates['ini_specific']['total_options'] = sum(
                len(section) for section in data.values() if isinstance(section, dict)
            )
            validation_updates['ini_specific']['has_default_section'] = 'DEFAULT' in data
        
        return validation_updates
    
    def _validate_python_config(self, config_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate Python configuration"""
        validation_updates = {
            'python_specific': {
                'variable_count': 0,
                'has_complex_types': False,
                'has_imports': False
            }
        }
        
        config_vars = config_data.get('config_variables', {})
        
        if isinstance(config_vars, dict):
            validation_updates['python_specific']['variable_count'] = len(config_vars)
            
            # Check for complex types
            for var_data in config_vars.values():
                if isinstance(var_data, dict) and var_data.get('type') in ['list', 'dict', 'tuple']:
                    validation_updates['python_specific']['has_complex_types'] = True
                    break
        
        return validation_updates
    
    def _calculate_nesting_depth(self, obj: Any, current_depth: int = 0) -> int:
        """Calculate maximum nesting depth of a data structure"""
        if not isinstance(obj, (dict, list)):
            return current_depth
        
        if isinstance(obj, dict):
            if not obj:
                return current_depth
            return max(
                self._calculate_nesting_depth(value, current_depth + 1)
                for value in obj.values()
            )
        
        if isinstance(obj, list):
            if not obj:
                return current_depth
            return max(
                self._calculate_nesting_depth(item, current_depth + 1)
                for item in obj
            )
        
        return current_depth
    
    def _generate_extraction_summary(self):
        """Generate extraction summary"""
        summary = {
            'extraction_timestamp': datetime.utcnow().isoformat(),
            'total_directories_scanned': len(self.config.source_dirs),
            'total_configs_extracted': 0,
            'configs_by_type': {
                'json': 0,
                'yaml': 0,
                'ini': 0,
                'python': 0
            },
            'extraction_issues': [],
            'recommendations': []
        }
        
        # Count configurations by type
        for dir_configs in self.extraction_results['extracted_configs'].values():
            for config_type, configs in dir_configs.get('configs', {}).items():
                summary['configs_by_type'][config_type] += len(configs)
                summary['total_configs_extracted'] += len(configs)
        
        # Add environment variables count
        env_vars = self.extraction_results.get('environment_variables', {})
        if env_vars:
            summary['environment_variables_extracted'] = env_vars.get('total_relevant_vars', 0)
        
        # Generate recommendations
        if summary['total_configs_extracted'] == 0:
            summary['extraction_issues'].append("No configurations found in specified directories")
            summary['recommendations'].append("Verify source directories contain configuration files")
        
        if summary['configs_by_type']['python'] > 0:
            summary['recommendations'].append("Python configurations require manual review for security")
        
        # Add validation summary if available
        if 'validation_results' in self.extraction_results:
            val_summary = self.extraction_results['validation_results']['validation_summary']
            summary['validation_summary'] = val_summary
            
            if val_summary['invalid_configs'] > 0:
                summary['extraction_issues'].append(f"{val_summary['invalid_configs']} invalid configurations found")
        
        self.extraction_results['extraction_summary'] = summary
    
    def _save_extraction_results(self):
        """Save extraction results to files"""
        # Save complete extraction results
        results_file = self.output_dir / 'config_extraction_results.json'
        with open(results_file, 'w') as f:
            json.dump(self.extraction_results, f, indent=2, default=str)
        
        # Save individual configuration files for easy access
        configs_dir = self.output_dir / 'extracted_configs'
        configs_dir.mkdir(exist_ok=True)
        
        for dir_name, dir_configs in self.extraction_results['extracted_configs'].items():
            dir_output = configs_dir / dir_name
            dir_output.mkdir(exist_ok=True)
            
            for config_type, configs in dir_configs.get('configs', {}).items():
                type_output = dir_output / config_type
                type_output.mkdir(exist_ok=True)
                
                for config_name, config_data in configs.items():
                    # Save individual configuration
                    safe_name = config_name.replace('/', '_').replace('\\', '_')
                    config_file = type_output / f"{safe_name}.json"
                    
                    with open(config_file, 'w') as f:
                        json.dump(config_data, f, indent=2, default=str)
        
        # Save environment variables separately
        if 'environment_variables' in self.extraction_results:
            env_file = self.output_dir / 'environment_variables.json'
            with open(env_file, 'w') as f:
                json.dump(self.extraction_results['environment_variables'], f, indent=2)
        
        # Save extraction report
        self._generate_extraction_report()
        
        self.logger.info(f"Extraction results saved to {self.output_dir}")
    
    def _generate_extraction_report(self):
        """Generate human-readable extraction report"""
        summary = self.extraction_results['extraction_summary']
        
        report = f"""
AI REBUILD MIGRATION - CONFIGURATION EXTRACTION REPORT
{'=' * 60}

Generated: {summary['extraction_timestamp']}

EXTRACTION SUMMARY
{'=' * 60}
Total Directories Scanned: {summary['total_directories_scanned']}
Total Configurations Extracted: {summary['total_configs_extracted']}

Configurations by Type:
  • JSON: {summary['configs_by_type']['json']}
  • YAML: {summary['configs_by_type']['yaml']}
  • INI: {summary['configs_by_type']['ini']}
  • Python: {summary['configs_by_type']['python']}

"""
        
        if 'environment_variables_extracted' in summary:
            report += f"Environment Variables Extracted: {summary['environment_variables_extracted']}\n\n"
        
        if 'validation_summary' in summary:
            val_summary = summary['validation_summary']
            report += f"""VALIDATION RESULTS
{'=' * 60}
Total Configurations Validated: {val_summary['total_configs']}
Valid: {val_summary['valid_configs']}
Invalid: {val_summary['invalid_configs']}
Warnings: {val_summary['warnings']}

"""
        
        if summary['extraction_issues']:
            report += f"EXTRACTION ISSUES\n{'=' * 60}\n"
            for issue in summary['extraction_issues']:
                report += f"• {issue}\n"
            report += "\n"
        
        if summary['recommendations']:
            report += f"RECOMMENDATIONS\n{'=' * 60}\n"
            for rec in summary['recommendations']:
                report += f"• {rec}\n"
            report += "\n"
        
        report += f"""EXTRACTED CONFIGURATIONS
{'=' * 60}
"""
        
        for dir_name, dir_configs in self.extraction_results['extracted_configs'].items():
            report += f"\n{dir_name.upper()}\n{'-' * len(dir_name)}\n"
            
            for config_type, configs in dir_configs.get('configs', {}).items():
                report += f"{config_type.upper()} Configurations ({len(configs)}):\n"
                for config_name in configs.keys():
                    report += f"  • {config_name}\n"
                report += "\n"
        
        # Save report
        report_file = self.output_dir / 'config_extraction_report.txt'
        with open(report_file, 'w') as f:
            f.write(report)
        
        print("\nConfiguration Extraction Report:")
        print("=" * 40)
        print(report)

def main():
    """Main entry point"""
    # Default configuration
    config = ExtractionConfig(
        source_dirs=[
            str(Path.cwd()),
            str(Path.cwd() / 'config'),
            str(Path.home() / '.ai-rebuild')
        ],
        output_dir=str(Path.cwd() / 'config_extraction'),
        include_env_vars=True,
        include_json_configs=True,
        include_yaml_configs=True,
        include_ini_configs=True,
        include_python_configs=True,
        backup_existing=True,
        validate_configs=True
    )
    
    # Create extractor and run
    extractor = ConfigExtractor(config)
    results = extractor.extract_all_configs()
    
    summary = results['extraction_summary']
    print(f"\nConfiguration Extraction Completed!")
    print(f"Total configurations extracted: {summary['total_configs_extracted']}")
    print(f"Results saved to: {config.output_dir}")

if __name__ == "__main__":
    main()