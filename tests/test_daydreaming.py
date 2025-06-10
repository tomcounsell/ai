#!/usr/bin/env python3
"""
Comprehensive test suite for the daydreaming functionality.

Tests the AI-powered reflection system that runs periodically to analyze 
codebase patterns, development trends, and generate creative insights.
"""
import unittest
import tempfile
import os
import json
import sqlite3
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

# Add project root to path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.promise_tasks import (
    gather_daydream_context,
    analyze_workspace_for_daydream,
    load_workspace_config,
    daydream_and_reflect,
    ollama_daydream_analysis,
    build_daydream_prompt,
    log_daydream_insights
)
from utilities.database import get_database_connection, init_database


class TestDaydreamingSystem(unittest.TestCase):
    """Test suite for the daydreaming functionality."""
    
    def setUp(self):
        """Set up test environment."""
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.test_dir)
        
        # Create a test workspace config
        os.makedirs('config', exist_ok=True)
        self.test_config = {
            "workspaces": {
                "TestWorkspace": {
                    "database_id": "test-id",
                    "working_directory": self.test_dir,
                    "description": "Test workspace"
                }
            }
        }
        
        with open('config/workspace_config.json', 'w') as f:
            json.dump(self.test_config, f)
            
        # Initialize test database
        init_database()
        
        # Add some test promise data
        with get_database_connection() as conn:
            conn.execute("""
                INSERT INTO promises (chat_id, message_id, task_description, task_type, status, created_at)
                VALUES (123, 456, 'Test task', 'code', 'completed', datetime('now', '-1 day'))
            """)
            conn.commit()
    
    def tearDown(self):
        """Clean up test environment."""
        os.chdir(self.original_cwd)
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_load_workspace_config(self):
        """Test loading workspace configuration."""
        config = load_workspace_config()
        self.assertIn('workspaces', config)
        self.assertIn('TestWorkspace', config['workspaces'])
        self.assertEqual(config['workspaces']['TestWorkspace']['database_id'], 'test-id')
    
    def test_load_workspace_config_missing_file(self):
        """Test handling missing workspace config file."""
        os.remove('config/workspace_config.json')
        config = load_workspace_config()
        self.assertEqual(config, {})
    
    def test_analyze_workspace_for_daydream(self):
        """Test workspace analysis for daydreaming context."""
        # Create some test files
        with open('test.py', 'w') as f:
            f.write('print("hello")')
        with open('test.js', 'w') as f:
            f.write('console.log("hello")')
            
        result = analyze_workspace_for_daydream('TestWorkspace', self.test_dir)
        
        self.assertEqual(result['name'], 'TestWorkspace')
        self.assertEqual(result['directory'], self.test_dir)
        self.assertTrue(result['exists'])
        self.assertIn('.py', result['file_stats'])
        self.assertIn('.js', result['file_stats'])
        self.assertEqual(result['file_stats']['.py'], 1)
        self.assertEqual(result['file_stats']['.js'], 1)
    
    def test_analyze_nonexistent_workspace(self):
        """Test analysis of non-existent workspace."""
        result = analyze_workspace_for_daydream('NonExistent', '/nonexistent/path')
        
        self.assertEqual(result['name'], 'NonExistent')
        self.assertEqual(result['directory'], '/nonexistent/path')
        self.assertFalse(result['exists'])
        self.assertIsNone(result['git_status'])
        self.assertEqual(result['recent_commits'], [])
    
    def test_gather_daydream_context(self):
        """Test gathering comprehensive daydreaming context."""
        context = gather_daydream_context()
        
        # Check basic structure
        self.assertIn('timestamp', context)
        self.assertIn('workspace_analysis', context)
        self.assertIn('recent_activity', context)
        self.assertIn('codebase_insights', context)
        
        # Check workspace analysis
        self.assertIn('TestWorkspace', context['workspace_analysis'])
        workspace_data = context['workspace_analysis']['TestWorkspace']
        self.assertEqual(workspace_data['name'], 'TestWorkspace')
        self.assertTrue(workspace_data['exists'])
        
        # Check recent activity
        self.assertIsInstance(context['recent_activity'], list)
        if context['recent_activity']:
            self.assertIn('task_description', context['recent_activity'][0])
    
    def test_build_daydream_prompt(self):
        """Test building the daydream prompt."""
        context = {
            'timestamp': '2024-01-01T00:00:00',
            'workspace_analysis': {
                'TestWorkspace': {
                    'name': 'TestWorkspace',
                    'file_stats': {'.py': 5, '.js': 3},
                    'tech_stack': ['Python', 'JavaScript']
                }
            },
            'recent_activity': [
                {'task_description': 'Fix bug', 'status': 'completed'},
                {'task_description': 'Add feature', 'status': 'pending'}
            ]
        }
        
        prompt = build_daydream_prompt(context)
        
        self.assertIn('TestWorkspace', prompt)
        self.assertIn('Python', prompt)
        self.assertIn('JavaScript', prompt)
        self.assertIn('Fix bug', prompt)
        self.assertIn('Add feature', prompt)
        self.assertIn('creative insights', prompt.lower())
        self.assertIn('reflection', prompt.lower())
    
    @patch('tasks.promise_tasks.logger')
    def test_log_daydream_insights(self, mock_logger):
        """Test logging daydream insights."""
        insights = "This is a test insight about the codebase architecture."
        
        log_daydream_insights(insights)
        
        mock_logger.info.assert_called()
        args = mock_logger.info.call_args[0]
        self.assertIn('🧠', args[0])
        self.assertIn('insights', args[0])
    
    @patch('tasks.promise_tasks.requests.post')
    def test_ollama_daydream_analysis(self, mock_post):
        """Test Ollama integration for daydream analysis."""
        # Mock Ollama response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'response': 'This codebase shows strong Python patterns with good test coverage.'
        }
        mock_post.return_value = mock_response
        
        context = {
            'workspace_analysis': {'TestWorkspace': {'file_stats': {'.py': 10}}},
            'recent_activity': []
        }
        
        result = ollama_daydream_analysis(context)
        
        self.assertIn('Python patterns', result)
        mock_post.assert_called_once()
        
        # Check the request payload
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        self.assertEqual(payload['model'], 'llama3.2')
        self.assertIn('creative', payload['prompt'].lower())
        self.assertEqual(payload['options']['temperature'], 0.8)
    
    @patch('tasks.promise_tasks.requests.post')
    def test_ollama_daydream_analysis_failure(self, mock_post):
        """Test handling Ollama API failures."""
        # Mock failed response
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response
        
        context = {'workspace_analysis': {}, 'recent_activity': []}
        
        result = ollama_daydream_analysis(context)
        
        self.assertIn('reflection', result.lower())
        self.assertIn('could not', result.lower())
    
    @patch('tasks.promise_tasks.gather_system_health_data')
    @patch('tasks.promise_tasks.ollama_daydream_analysis')
    @patch('tasks.promise_tasks.log_daydream_insights')
    def test_daydream_and_reflect_success(self, mock_log, mock_ollama, mock_health):
        """Test successful daydream and reflect cycle."""
        # Mock system as idle
        mock_health.return_value = {'pending_count': 2}
        mock_ollama.return_value = "Great insights about the codebase!"
        
        # This should complete without error
        daydream_and_reflect()
        
        mock_health.assert_called_once()
        mock_ollama.assert_called_once()
        mock_log.assert_called_once_with("Great insights about the codebase!")
    
    @patch('tasks.promise_tasks.gather_system_health_data')
    @patch('tasks.promise_tasks.logger')
    def test_daydream_and_reflect_busy_system(self, mock_logger, mock_health):
        """Test skipping daydream when system is busy."""
        # Mock system as busy
        mock_health.return_value = {'pending_count': 10}
        
        daydream_and_reflect()
        
        # Should log that system is too busy
        mock_logger.info.assert_called()
        log_message = mock_logger.info.call_args[0][0]
        self.assertIn('too busy', log_message)
        self.assertIn('skipping', log_message)
    
    def test_git_integration(self):
        """Test git repository analysis in workspace."""
        # Initialize git repo
        os.system('git init')
        os.system('git config user.email "test@example.com"')
        os.system('git config user.name "Test User"')
        
        with open('test_file.py', 'w') as f:
            f.write('# Test file\nprint("Hello, world!")\n')
        
        os.system('git add .')
        os.system('git commit -m "Initial commit"')
        
        result = analyze_workspace_for_daydream('TestWorkspace', self.test_dir)
        
        # Should detect git repo
        self.assertIsNotNone(result['git_status'])
        self.assertIsInstance(result['recent_commits'], list)
        if result['recent_commits']:
            self.assertIn('Initial commit', result['recent_commits'][0])
    
    def test_tech_stack_detection(self):
        """Test detection of tech stack from file extensions."""
        # Create files for different tech stacks
        test_files = [
            ('app.py', 'Python'),
            ('script.js', 'JavaScript'),
            ('style.css', 'CSS'),
            ('index.html', 'HTML'),
            ('main.go', 'Go'),
            ('App.java', 'Java'),
            ('component.tsx', 'TypeScript/React'),
            ('Dockerfile', 'Docker'),
            ('package.json', 'Node.js'),
            ('requirements.txt', 'Python'),
            ('Cargo.toml', 'Rust'),
            ('main.cpp', 'C++')
        ]
        
        for filename, _ in test_files:
            with open(filename, 'w') as f:
                f.write(f'// {filename} content')
        
        result = analyze_workspace_for_daydream('TestWorkspace', self.test_dir)
        
        # Check that tech stack detection works
        self.assertIsInstance(result['tech_stack'], list)
        # Should detect at least some of the technologies
        tech_stack_str = ' '.join(result['tech_stack'])
        self.assertTrue(any(tech in tech_stack_str for tech in ['Python', 'JavaScript', 'Docker']))


class TestDaydreamingIntegration(unittest.TestCase):
    """Integration tests for the daydreaming system."""
    
    def setUp(self):
        """Set up integration test environment."""
        self.test_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        
    def tearDown(self):
        """Clean up integration test environment."""
        os.chdir(self.original_cwd)
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    @patch('tasks.promise_tasks.requests.post')
    def test_end_to_end_daydream_cycle(self, mock_post):
        """Test complete daydream cycle end-to-end."""
        os.chdir(self.test_dir)
        
        # Set up realistic test environment
        os.makedirs('config', exist_ok=True)
        
        workspace_config = {
            "workspaces": {
                "TestProject": {
                    "working_directory": self.test_dir,
                    "database_id": "test-db-id",
                    "description": "Test project for daydreaming"
                }
            }
        }
        
        with open('config/workspace_config.json', 'w') as f:
            json.dump(workspace_config, f)
        
        # Create realistic project structure
        with open('main.py', 'w') as f:
            f.write('def main():\n    print("Hello, world!")\n')
        
        with open('requirements.txt', 'w') as f:
            f.write('requests==2.25.1\n')
        
        # Mock Ollama response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'response': 'This appears to be a Python project with a simple structure. The main.py suggests a basic application. Consider adding error handling and logging for production readiness.'
        }
        mock_post.return_value = mock_response
        
        # Initialize database in test directory
        init_database()
        
        # Run the daydream cycle
        with patch('tasks.promise_tasks.gather_system_health_data') as mock_health:
            mock_health.return_value = {'pending_count': 1}
            
            with patch('tasks.promise_tasks.logger') as mock_logger:
                daydream_and_reflect()
                
                # Verify the cycle completed
                mock_logger.info.assert_called()
                log_calls = [call[0][0] for call in mock_logger.info.call_args_list]
                
                # Should log start and completion
                self.assertTrue(any('Starting AI-powered daydream' in call for call in log_calls))
                self.assertTrue(any('Daydream cycle complete' in call for call in log_calls))


if __name__ == '__main__':
    # Run the tests
    unittest.main(verbosity=2)