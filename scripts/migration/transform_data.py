#!/usr/bin/env python3
"""
Data Transformation Tool for AI Rebuild Migration
Transforms exported data to match the new system schema.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

@dataclass
class TransformConfig:
    """Configuration for data transformation"""
    export_dir: str
    transform_dir: str
    schema_version: str = "2.0.0"
    preserve_ids: bool = True
    validate_transforms: bool = True
    create_migration_log: bool = True

class DataTransformer:
    """Main data transformation handler"""
    
    def __init__(self, config: TransformConfig):
        self.config = config
        self.export_dir = Path(config.export_dir)
        self.transform_dir = Path(config.transform_dir)
        self.transform_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.transform_dir / 'transform.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Load schema mappings
        self.schema_mappings = self._load_schema_mappings()
    
    def transform_all_data(self) -> Dict[str, Any]:
        """Transform all exported data"""
        self.logger.info("Starting data transformation process")
        
        transform_manifest = {
            'transform_timestamp': datetime.utcnow().isoformat(),
            'schema_version': self.config.schema_version,
            'source_export': str(self.export_dir),
            'transformed_components': []
        }
        
        try:
            # Load export manifest
            export_manifest_path = self.export_dir / 'export_manifest.json'
            if not export_manifest_path.exists():
                raise FileNotFoundError("Export manifest not found")
            
            with open(export_manifest_path, 'r') as f:
                export_manifest = json.load(f)
            
            # Transform each component
            for component in export_manifest['exported_components']:
                component_name = component['component']
                source_file = component['file']
                
                self.logger.info(f"Transforming {component_name}")
                
                transformed_data = self._transform_component(component_name, source_file)
                
                transform_manifest['transformed_components'].append({
                    'component': component_name,
                    'source_records': component['records_count'],
                    'transformed_records': len(transformed_data) if isinstance(transformed_data, list) else 1,
                    'output_file': f"transformed_{source_file}",
                    'transformation_status': 'success'
                })
            
            # Save transformation manifest
            with open(self.transform_dir / 'transform_manifest.json', 'w') as f:
                json.dump(transform_manifest, f, indent=2)
            
            # Generate migration scripts
            self._generate_migration_scripts(transform_manifest)
            
            self.logger.info("Data transformation completed successfully")
            return transform_manifest
            
        except Exception as e:
            self.logger.error(f"Transformation failed: {str(e)}")
            raise
    
    def _transform_component(self, component_name: str, source_file: str) -> Any:
        """Transform a specific component"""
        source_path = self.export_dir / source_file
        
        if not source_path.exists():
            self.logger.warning(f"Source file not found: {source_file}")
            return []
        
        with open(source_path, 'r') as f:
            source_data = json.load(f)
        
        # Apply component-specific transformation
        if component_name == 'chat_history':
            transformed_data = self._transform_chat_history(source_data)
        elif component_name == 'preferences':
            transformed_data = self._transform_preferences(source_data)
        elif component_name == 'workspaces':
            transformed_data = self._transform_workspaces(source_data)
        elif component_name == 'tool_metrics':
            transformed_data = self._transform_tool_metrics(source_data)
        elif component_name == 'system_state':
            transformed_data = self._transform_system_state(source_data)
        else:
            self.logger.warning(f"No transformation defined for {component_name}")
            transformed_data = source_data
        
        # Save transformed data
        output_path = self.transform_dir / f"transformed_{source_file}"
        with open(output_path, 'w') as f:
            json.dump(transformed_data, f, indent=2, default=str)
        
        return transformed_data
    
    def _transform_chat_history(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Transform chat history to new schema"""
        self.logger.info("Transforming chat history data")
        
        transformed = []
        for record in data:
            # Extract original data
            original_data = record.get('data', {})
            
            # Create new schema structure
            new_record = {
                'id': self._generate_id(original_data.get('id')),
                'session_id': original_data.get('session_id', f"session_{len(transformed)}"),
                'user_id': original_data.get('user_id', 'default_user'),
                'agent_id': original_data.get('agent_id', 'valor'),
                'message_type': original_data.get('type', 'user_message'),
                'content': original_data.get('message', original_data.get('content', '')),
                'metadata': {
                    'source_table': record.get('table'),
                    'original_timestamp': original_data.get('timestamp'),
                    'migration_timestamp': datetime.utcnow().isoformat(),
                    'tools_used': original_data.get('tools_used', []),
                    'context_length': len(str(original_data.get('content', '')))
                },
                'created_at': original_data.get('timestamp', datetime.utcnow().isoformat()),
                'updated_at': datetime.utcnow().isoformat()
            }
            
            transformed.append(new_record)
        
        return transformed
    
    def _transform_preferences(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Transform preferences to new schema"""
        self.logger.info("Transforming preferences data")
        
        # Merge all preference sources
        merged_preferences = {
            'user_preferences': {},
            'agent_configurations': {},
            'system_settings': {},
            'workspace_defaults': {}
        }
        
        for pref_set in data:
            source_data = pref_set.get('data', {})
            source = pref_set.get('source', 'unknown')
            
            # Map old structure to new
            if 'user_preferences' in source_data:
                merged_preferences['user_preferences'].update(source_data['user_preferences'])
            
            if 'agent_configurations' in source_data:
                merged_preferences['agent_configurations'].update(source_data['agent_configurations'])
            
            if 'system_settings' in source_data:
                merged_preferences['system_settings'].update(source_data['system_settings'])
            
            # Add migration metadata
            merged_preferences['migration_info'] = {
                'sources': [p.get('source') for p in data],
                'migrated_at': datetime.utcnow().isoformat(),
                'schema_version': self.config.schema_version
            }
        
        return merged_preferences
    
    def _transform_workspaces(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Transform workspaces to new schema"""
        self.logger.info("Transforming workspace data")
        
        transformed = []
        for workspace in data:
            workspace_data = workspace.get('data', {})
            
            new_workspace = {
                'id': self._generate_id(workspace.get('name')),
                'name': workspace.get('name', 'Unknown Workspace'),
                'type': workspace_data.get('type', 'general'),
                'status': workspace_data.get('status', 'active'),
                'configuration': {
                    'path': workspace_data.get('path', ''),
                    'settings': workspace_data.get('settings', {}),
                    'tools_enabled': workspace_data.get('tools_enabled', []),
                    'agent_preferences': workspace_data.get('agent_preferences', {})
                },
                'metadata': {
                    'source_file': workspace.get('source'),
                    'migrated_from': workspace.get('source'),
                    'migration_timestamp': datetime.utcnow().isoformat()
                },
                'created_at': workspace_data.get('created_at', datetime.utcnow().isoformat()),
                'updated_at': datetime.utcnow().isoformat()
            }
            
            transformed.append(new_workspace)
        
        return transformed
    
    def _transform_tool_metrics(self, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Transform tool metrics to new schema"""
        self.logger.info("Transforming tool metrics data")
        
        # Aggregate metrics from different sources
        aggregated_metrics = {
            'usage_statistics': {
                'total_sessions': 0,
                'total_tool_calls': 0,
                'unique_tools_used': set(),
                'success_rate': 0.0
            },
            'performance_metrics': {
                'average_response_time': 0.0,
                'memory_usage': {},
                'error_rates': {}
            },
            'tool_specific_metrics': {},
            'migration_info': {
                'sources_processed': len(data),
                'migrated_at': datetime.utcnow().isoformat()
            }
        }
        
        for metric_record in data:
            if metric_record.get('type') == 'usage_metrics':
                usage_data = metric_record.get('data', {})
                aggregated_metrics['usage_statistics'].update({
                    'total_sessions': usage_data.get('total_sessions', 0),
                    'total_tool_calls': usage_data.get('total_tool_calls', 0),
                    'success_rate': usage_data.get('success_rate', 0.0)
                })
                
                if 'most_used_tools' in usage_data:
                    for tool in usage_data['most_used_tools']:
                        aggregated_metrics['usage_statistics']['unique_tools_used'].add(tool)
            
            elif metric_record.get('type') == 'log_file':
                # Extract basic metrics from log file info
                log_info = {
                    'file_size': metric_record.get('size', 0),
                    'last_modified': metric_record.get('modified')
                }
                source = metric_record.get('source', 'unknown')
                aggregated_metrics['performance_metrics'][f'log_{os.path.basename(source)}'] = log_info
        
        # Convert sets to lists for JSON serialization
        aggregated_metrics['usage_statistics']['unique_tools_used'] = list(
            aggregated_metrics['usage_statistics']['unique_tools_used']
        )
        
        return aggregated_metrics
    
    def _transform_system_state(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Transform system state to new schema"""
        self.logger.info("Transforming system state data")
        
        transformed_state = {
            'environment': {
                'python_version': data.get('python_version'),
                'platform': data.get('system_info', {}).get('platform'),
                'cpu_count': data.get('system_info', {}).get('cpu_count'),
                'working_directory': data.get('working_directory')
            },
            'dependencies': {
                'installed_packages': data.get('installed_packages', []),
                'requirements_status': 'migrated'
            },
            'project_structure': {
                'file_structure': data.get('file_structure', {}),
                'total_files': self._count_files(data.get('file_structure', {}))
            },
            'migration_metadata': {
                'original_export_time': data.get('export_timestamp'),
                'transformation_time': datetime.utcnow().isoformat(),
                'schema_version': self.config.schema_version
            }
        }
        
        return transformed_state
    
    def _load_schema_mappings(self) -> Dict[str, Any]:
        """Load schema mapping configurations"""
        # Define schema mappings for different components
        mappings = {
            'chat_history': {
                'id_field': 'id',
                'required_fields': ['session_id', 'user_id', 'content'],
                'timestamp_fields': ['created_at', 'updated_at']
            },
            'preferences': {
                'merge_strategy': 'deep_merge',
                'required_sections': ['user_preferences', 'agent_configurations']
            },
            'workspaces': {
                'id_field': 'id',
                'required_fields': ['name', 'type', 'status']
            },
            'tool_metrics': {
                'aggregation_strategy': 'sum_numeric_merge_categorical'
            },
            'system_state': {
                'preserve_fields': ['environment', 'dependencies']
            }
        }
        
        return mappings
    
    def _generate_id(self, source_id: Any) -> str:
        """Generate consistent ID from source"""
        if source_id and self.config.preserve_ids:
            return str(source_id)
        
        # Generate new ID based on timestamp and counter
        import uuid
        return str(uuid.uuid4())
    
    def _count_files(self, file_structure: Dict[str, Any]) -> int:
        """Count total files in structure"""
        total = 0
        for path_info in file_structure.values():
            if isinstance(path_info, dict) and 'file_count' in path_info:
                total += path_info['file_count']
        return total
    
    def _generate_migration_scripts(self, manifest: Dict[str, Any]):
        """Generate SQL/migration scripts for database import"""
        self.logger.info("Generating migration scripts")
        
        scripts_dir = self.transform_dir / 'migration_scripts'
        scripts_dir.mkdir(exist_ok=True)
        
        # Generate import scripts for each component
        for component in manifest['transformed_components']:
            component_name = component['component']
            script_content = self._generate_component_script(component_name)
            
            script_path = scripts_dir / f"import_{component_name}.sql"
            with open(script_path, 'w') as f:
                f.write(script_content)
        
        # Generate master migration script
        master_script = self._generate_master_script(manifest)
        with open(scripts_dir / 'master_migration.sql', 'w') as f:
            f.write(master_script)
    
    def _generate_component_script(self, component_name: str) -> str:
        """Generate SQL script for specific component"""
        if component_name == 'chat_history':
            return """
-- Chat History Migration Script
CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    message_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);

-- Import will be handled by Python script
-- This script creates the target schema
"""
        elif component_name == 'preferences':
            return """
-- Preferences Migration Script
CREATE TABLE IF NOT EXISTS user_preferences (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    preference_type TEXT NOT NULL,
    preference_data TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
"""
        elif component_name == 'workspaces':
            return """
-- Workspaces Migration Script
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    configuration TEXT,
    metadata TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
"""
        else:
            return f"-- Migration script for {component_name}\n-- Component-specific schema to be defined\n"
    
    def _generate_master_script(self, manifest: Dict[str, Any]) -> str:
        """Generate master migration script"""
        components = [comp['component'] for comp in manifest['transformed_components']]
        
        script = f"""
-- Master Migration Script
-- Generated: {datetime.utcnow().isoformat()}
-- Schema Version: {self.config.schema_version}

BEGIN TRANSACTION;

-- Create migration tracking table
CREATE TABLE IF NOT EXISTS migration_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    migration_date DATETIME NOT NULL,
    records_migrated INTEGER NOT NULL,
    status TEXT NOT NULL
);

-- Run component migrations
"""
        
        for component in components:
            script += f"-- .read import_{component}.sql\n"
        
        script += """
-- Record migration completion
INSERT INTO migration_history (component, migration_date, records_migrated, status)
VALUES ('full_migration', datetime('now'), 0, 'completed');

COMMIT;
"""
        
        return script

def main():
    """Main entry point"""
    # Default configuration
    config = TransformConfig(
        export_dir=str(Path.cwd() / 'migration_export'),
        transform_dir=str(Path.cwd() / 'migration_transform'),
        schema_version="2.0.0",
        preserve_ids=True,
        validate_transforms=True
    )
    
    # Create transformer and run
    transformer = DataTransformer(config)
    manifest = transformer.transform_all_data()
    
    print("\nData Transformation Summary:")
    print("=" * 50)
    for component in manifest['transformed_components']:
        print(f"• {component['component']}: {component['source_records']} → {component['transformed_records']} records")
    
    print(f"\nTransformation completed successfully!")
    print(f"Transform directory: {config.transform_dir}")

if __name__ == "__main__":
    main()