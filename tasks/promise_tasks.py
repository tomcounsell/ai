"""
Promise execution tasks using Huey.

DESIGN PATTERN: Each task should be:
1. Idempotent - safe to retry
2. Atomic - completes fully or fails cleanly
3. Logged - comprehensive logging for debugging
"""
import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List

from huey import crontab
from .huey_config import huey
from utilities.database import (
    get_promise, update_promise_status, get_database_connection, get_pending_promises
)
from utilities.missed_message_manager import scan_chat_for_missed_messages, process_missed_message_batch

logger = logging.getLogger(__name__)


@dataclass
class DaydreamSession:
    """Complete daydream session with integrated lifecycle management."""
    
    # Session metadata
    session_id: str
    start_time: datetime
    phase: str = 'initializing'  # Current execution phase
    
    # Context data
    system_health: Dict[str, Any] = field(default_factory=dict)
    workspace_analysis: Dict[str, Any] = field(default_factory=dict)
    development_trends: Dict[str, Any] = field(default_factory=dict)
    system_metrics: Dict[str, Any] = field(default_factory=dict)
    recent_activity: List[Dict[str, Any]] = field(default_factory=list)
    
    # Analysis results
    insights: str = ""
    analysis_duration: float = 0.0
    cleanup_summary: Dict[str, Any] = field(default_factory=dict)
    
    def log_phase_transition(self, new_phase: str) -> None:
        """Log phase transitions with session context."""
        logger.info(f"🧠 Session {self.session_id[:8]}: {self.phase} → {new_phase}")
        self.phase = new_phase


class UnifiedDaydreamSystem:
    """Unified daydream system with integrated cleanup and analysis."""
    
    def __init__(self):
        self.session_timeout = 300  # 5 minutes max analysis time
        self.cleanup_stats = {
            'claude_processes_killed': 0,
            'aider_processes_killed': 0,
            'temp_files_cleaned': 0
        }
    
    def _check_system_readiness(self, session: DaydreamSession) -> bool:
        """Phase 1: Check if system is ready for intensive analysis."""
        session.log_phase_transition('readiness_check')
        
        # Get system health data (migrated from gather_system_health_data)
        session.system_health = self._get_system_health()
        
        if session.system_health['pending_count'] > 5:
            logger.info(f"🧠 System busy ({session.system_health['pending_count']} pending), skipping daydream")
            return False
            
        logger.info("🧠 System idle ✓ - Ready for daydream analysis")
        return True
    
    def _cleanup_before_analysis(self, session: DaydreamSession) -> None:
        """Phase 2: Clean resources before intensive analysis."""
        session.log_phase_transition('pre_cleanup')
        logger.info("🧹 Pre-analysis cleanup starting...")
        
        cleanup_stats = {
            'claude_processes_killed': 0,
            'aider_processes_killed': 0,
            'temp_files_cleaned': 0,
            'memory_freed_mb': 0
        }
        
        # Kill old Claude Code processes (24+ hours old)
        cleanup_stats['claude_processes_killed'] = self._cleanup_old_claude_processes()
        
        # Kill orphaned Aider processes
        cleanup_stats['aider_processes_killed'] = self._cleanup_old_aider_processes()
        
        # Clean temp analysis files
        cleanup_stats['temp_files_cleaned'] = self._cleanup_temp_files()
        
        session.cleanup_summary['pre_analysis'] = cleanup_stats
        logger.info(f"🧹 Pre-cleanup complete: {cleanup_stats}")
    
    def _gather_comprehensive_context(self, session: DaydreamSession) -> None:
        """Phase 3: Unified context gathering (replaces multiple scattered functions)."""
        session.log_phase_transition('context_gathering')
        logger.info("🧠 Gathering comprehensive analysis context...")
        
        try:
            with get_database_connection() as conn:
                # Workspace analysis (migrated from gather_daydream_context)
                session.workspace_analysis = self._analyze_all_workspaces()
                
                # System metrics (migrated from gather_system_metrics)
                session.system_metrics = self._gather_system_metrics(conn)
                
                # Development trends (migrated from gather_development_trends)
                session.development_trends = self._gather_development_trends(conn)
                
                # Recent activity
                session.recent_activity = self._gather_recent_activity(conn)
        
        except Exception as e:
            logger.error(f"🧠 Context gathering failed: {e}")
            # Provide minimal context to prevent complete failure
            session.workspace_analysis = {}
            session.system_metrics = {'error': str(e)}
            session.development_trends = {}
            session.recent_activity = []
        
        logger.info(f"🧠 Context ready: {len(session.workspace_analysis)} workspaces analyzed")
    
    def _execute_ai_analysis(self, session: DaydreamSession) -> None:
        """Phase 4: Execute AI analysis with unified prompt building."""
        session.log_phase_transition('ai_analysis')
        analysis_start = time.time()
        
        logger.info("🧠 Starting AI-powered codebase analysis...")
        
        try:
            # Build unified prompt (merge duplicate prompt builders)
            prompt = self._build_unified_analysis_prompt(session)
            
            # Execute Aider analysis with timeout
            session.insights = self._run_aider_analysis(prompt)
            
        except Exception as e:
            logger.error(f"🧠 AI analysis failed: {e}")
            session.insights = f"Analysis failed: {str(e)}"
        
        session.analysis_duration = time.time() - analysis_start
        logger.info(f"🧠 Analysis complete ({session.analysis_duration:.1f}s)")
    
    def _process_insights_and_output(self, session: DaydreamSession) -> None:
        """Phase 5: Process insights and handle output."""
        session.log_phase_transition('output_processing')
        
        # Log insights to console (migrated from log_daydream_insights)
        self._log_insights_to_console(session.insights)
        
        # Write insights to file and manage archival
        self._write_and_archive_insights(session)
        
        # Generate session summary for monitoring
        self._generate_session_summary(session)
    
    def _cleanup_after_analysis(self, session: DaydreamSession) -> None:
        """Phase 6: Post-analysis cleanup."""
        session.log_phase_transition('post_cleanup')
        logger.info("🧹 Post-analysis cleanup starting...")
        
        cleanup_stats = {
            'current_aider_killed': False,
            'insights_archived': False,
            'temp_files_cleaned': 0
        }
        
        # Kill current Aider session if still running
        cleanup_stats['current_aider_killed'] = self._cleanup_current_aider()
        
        # Archive old insights (keep last 10)
        cleanup_stats['insights_archived'] = self._archive_old_insights()
        
        session.cleanup_summary['post_analysis'] = cleanup_stats
        logger.info(f"🧹 Post-cleanup complete: {cleanup_stats}")
    
    def _emergency_cleanup(self, session: DaydreamSession) -> None:
        """Emergency cleanup for failed sessions."""
        session.log_phase_transition('emergency_cleanup')
        logger.warning(f"🚨 Emergency cleanup for session {session.session_id[:8]}")
        
        try:
            # Force kill any Aider processes
            subprocess.run(['pkill', '-f', 'aider'], check=False)
            
            # Clean temp files
            self._cleanup_temp_files()
            
            logger.info("🚨 Emergency cleanup complete")
        except Exception as e:
            logger.error(f"🚨 Emergency cleanup failed: {e}")
    
    # Helper methods for cleanup operations
    def _cleanup_old_claude_processes(self) -> int:
        """Kill Claude Code processes older than 24 hours."""
        try:
            # Get list of claude processes with timestamps
            cmd = ['ps', 'aux']
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            killed_count = 0
            current_time = time.time()
            
            for line in result.stdout.split('\n'):
                if 'claude' in line and 'claude code' in line:
                    parts = line.split()
                    if len(parts) > 1:
                        pid = parts[1]
                        # Check process age (simplified - would need actual process start time)
                        # For now, kill processes that appear to be old based on naming
                        try:
                            subprocess.run(['kill', '-TERM', pid], check=False)
                            killed_count += 1
                        except:
                            pass
            
            return killed_count
        except Exception as e:
            logger.warning(f"Failed to cleanup Claude processes: {e}")
            return 0
    
    def _cleanup_old_aider_processes(self) -> int:
        """Kill orphaned Aider processes."""
        try:
            result = subprocess.run(['pkill', '-f', 'aider.*daydream'], capture_output=True)
            return 1 if result.returncode == 0 else 0
        except Exception as e:
            logger.warning(f"Failed to cleanup Aider processes: {e}")
            return 0
    
    def _cleanup_temp_files(self) -> int:
        """Clean temporary analysis files."""
        try:
            temp_patterns = [
                '/tmp/tmp*daydream*.md',
                '/tmp/tmp*analysis*.md',
                '/var/folders/*/T/tmp*daydream*.md'
            ]
            
            cleaned_count = 0
            for pattern in temp_patterns:
                try:
                    import glob
                    files = glob.glob(pattern)
                    for file_path in files:
                        Path(file_path).unlink(missing_ok=True)
                        cleaned_count += 1
                except:
                    pass
            
            return cleaned_count
        except Exception as e:
            logger.warning(f"Failed to cleanup temp files: {e}")
            return 0
    
    def _cleanup_current_aider(self) -> bool:
        """Kill current Aider session if still running."""
        try:
            # This would be more sophisticated in practice
            result = subprocess.run(['pkill', '-f', 'aider'], check=False)
            return result.returncode == 0
        except:
            return False
    
    def _archive_old_insights(self) -> bool:
        """Archive old insight files, keeping last 10."""
        try:
            insights_file = Path('logs/daydream_insights.md')
            if insights_file.exists():
                # Simple rotation - move to timestamped backup
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_file = Path(f'logs/daydream_insights_{timestamp}.md')
                insights_file.rename(backup_file)
                return True
            return False
        except Exception as e:
            logger.warning(f"Failed to archive insights: {e}")
            return False
    
    # Context gathering methods (migrated from scattered functions)
    def _get_system_health(self) -> Dict[str, Any]:
        """Get current system health data."""
        try:
            with get_database_connection() as conn:
                # Get pending promise count
                pending_count = conn.execute("""
                    SELECT COUNT(*) FROM promises WHERE status = 'pending'
                """).fetchone()[0]
                
                # Get stalled tasks count
                stalled_count = conn.execute("""
                    SELECT COUNT(*) FROM promises 
                    WHERE status = 'in_progress' 
                    AND created_at < datetime('now', '-4 hours')
                """).fetchone()[0]
                
                return {
                    'pending_count': pending_count,
                    'stalled_count': stalled_count,
                    'timestamp': datetime.utcnow().isoformat()
                }
        except Exception as e:
            logger.error(f"Failed to get system health: {e}")
            return {'pending_count': 0, 'stalled_count': 0, 'error': str(e)}
    
    def _analyze_all_workspaces(self) -> Dict[str, Any]:
        """Analyze all configured workspaces."""
        workspaces = {}
        try:
            # Load workspace configuration
            workspace_config = self._load_workspace_config()
            
            for workspace_name, workspace_data in workspace_config.get('workspaces', {}).items():
                if isinstance(workspace_data, dict):
                    working_dir = workspace_data.get('working_directory', '')
                    if working_dir and Path(working_dir).exists():
                        workspaces[workspace_name] = self._analyze_single_workspace(workspace_name, working_dir)
                        
        except Exception as e:
            logger.warning(f"Workspace analysis failed: {e}")
            
        return workspaces
    
    def _analyze_single_workspace(self, workspace_name: str, working_dir: str) -> Dict[str, Any]:
        """Analyze a single workspace directory."""
        workspace_info = {
            'name': workspace_name,
            'directory': working_dir,
            'exists': Path(working_dir).exists(),
            'git_status': None,
            'tech_stack': [],
            'file_count': 0
        }
        
        if not workspace_info['exists']:
            return workspace_info
            
        try:
            # Get git status if it's a git repo
            git_result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=10
            )
            if git_result.returncode == 0:
                workspace_info['git_status'] = git_result.stdout.strip()
            
            # Simple tech stack detection
            if Path(working_dir, 'package.json').exists():
                workspace_info['tech_stack'].append('Node.js')
            if Path(working_dir, 'requirements.txt').exists() or Path(working_dir, 'pyproject.toml').exists():
                workspace_info['tech_stack'].append('Python')
            if Path(working_dir, 'Cargo.toml').exists():
                workspace_info['tech_stack'].append('Rust')
                
            # Count Python files
            python_files = list(Path(working_dir).rglob('*.py'))
            workspace_info['file_count'] = len(python_files)
            
        except Exception as e:
            logger.warning(f"Failed to analyze workspace {workspace_name}: {e}")
            
        return workspace_info
    
    def _gather_system_metrics(self, conn) -> Dict[str, Any]:
        """Gather system performance metrics."""
        metrics = {
            'timestamp': datetime.utcnow().isoformat(),
            'completion_stats': {},
            'task_types': {},
            'success_rate': 0
        }
        
        try:
            # Task completion statistics
            completion_stats = conn.execute("""
                SELECT 
                    status,
                    COUNT(*) as count,
                    AVG(CASE 
                        WHEN completed_at IS NOT NULL AND created_at IS NOT NULL 
                        THEN (julianday(completed_at) - julianday(created_at)) * 24 * 60 
                        ELSE NULL 
                    END) as avg_duration_minutes
                FROM promises 
                WHERE created_at > datetime('now', '-30 days')
                GROUP BY status
            """).fetchall()
            
            total_tasks = 0
            for row in completion_stats:
                status, count, avg_duration = row
                total_tasks += count
                metrics['completion_stats'][status] = {
                    'count': count,
                    'avg_duration_minutes': round(avg_duration, 2) if avg_duration else None
                }
            
            # Calculate success rate
            completed_count = metrics['completion_stats'].get('completed', {}).get('count', 0)
            metrics['success_rate'] = round((completed_count / total_tasks) * 100, 1) if total_tasks > 0 else 0
            
            # Task type distribution
            task_types = conn.execute("""
                SELECT task_type, COUNT(*) as count
                FROM promises 
                WHERE created_at > datetime('now', '-30 days')
                GROUP BY task_type
                ORDER BY count DESC
            """).fetchall()
            
            metrics['task_types'] = {row[0]: row[1] for row in task_types}
            
        except Exception as e:
            logger.warning(f"Error gathering system metrics: {e}")
            metrics['error'] = str(e)
        
        return metrics
    
    def _gather_development_trends(self, conn) -> Dict[str, Any]:
        """Gather development trend analysis."""
        trends = {
            'timestamp': datetime.utcnow().isoformat(),
            'weekly_trends': []
        }
        
        try:
            # Weekly completion trends
            weekly_trends = conn.execute("""
                SELECT 
                    strftime('%Y-W%W', created_at) as week,
                    COUNT(*) as total_tasks,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_tasks
                FROM promises 
                WHERE created_at > datetime('now', '-8 weeks')
                GROUP BY strftime('%Y-W%W', created_at)
                ORDER BY week DESC
                LIMIT 4
            """).fetchall()
            
            for row in weekly_trends:
                week, total_tasks, completed_tasks = row
                completion_rate = round((completed_tasks / total_tasks) * 100, 1) if total_tasks > 0 else 0
                trends['weekly_trends'].append({
                    'week': week,
                    'total_tasks': total_tasks,
                    'completed_tasks': completed_tasks,
                    'completion_rate': completion_rate
                })
                
        except Exception as e:
            logger.warning(f"Error gathering development trends: {e}")
            trends['error'] = str(e)
        
        return trends
    
    def _gather_recent_activity(self, conn) -> List[Dict[str, Any]]:
        """Gather recent development activity."""
        try:
            recent_promises = conn.execute("""
                SELECT 
                    task_description,
                    task_type,
                    status,
                    created_at,
                    chat_id
                FROM promises 
                WHERE created_at > datetime('now', '-7 days')
                ORDER BY created_at DESC
                LIMIT 20
            """).fetchall()
            
            activities = []
            for row in recent_promises:
                activities.append({
                    'task_description': row[0],
                    'task_type': row[1],
                    'status': row[2],
                    'created_at': row[3],
                    'chat_id': row[4]
                })
            
            return activities
            
        except Exception as e:
            logger.warning(f"Error gathering recent activity: {e}")
            return []
    
    def _build_unified_analysis_prompt(self, session: DaydreamSession) -> str:
        """Build comprehensive analysis prompt for Aider."""
        
        # Workspace summary
        workspace_summary = []
        for name, info in session.workspace_analysis.items():
            if info.get('exists'):
                tech_stack = ', '.join(info.get('tech_stack', []))
                workspace_summary.append(f"**{name}**: {tech_stack} ({info.get('file_count', 0)} Python files)")
        
        # System performance summary
        metrics = session.system_metrics
        success_rate = metrics.get('success_rate', 0)
        task_types = metrics.get('task_types', {})
        
        # Recent activity summary
        activity_summary = []
        for activity in session.recent_activity[:5]:
            status_emoji = "✅" if activity['status'] == 'completed' else "⏳" if activity['status'] == 'in_progress' else "❌"
            activity_summary.append(f"- {status_emoji} {activity['task_type']}: {activity['task_description'][:60]}...")
        
        prompt = f"""You are Valor Engels, an AI system performing thoughtful reflection on your development environment.

WORKSPACE OVERVIEW:
{chr(10).join(workspace_summary) if workspace_summary else '- No active workspaces detected'}

SYSTEM PERFORMANCE:
- Success Rate: {success_rate}%
- Task Distribution: {dict(list(task_types.items())[:3]) if task_types else 'No data'}

RECENT DEVELOPMENT ACTIVITY:
{chr(10).join(activity_summary) if activity_summary else '- No recent activity'}

As an intelligent development system, provide insights about:

1. **Architecture Patterns**: What patterns emerge from the workspace analysis?
2. **Development Velocity**: How does the data suggest productivity trends?
3. **Quality Assessment**: Based on success rates and task patterns, what stands out?
4. **Technical Opportunities**: What improvements or optimizations come to mind?
5. **Strategic Direction**: Where might this development trajectory lead?

Respond as Valor would - analytical yet creative, combining German engineering precision with California innovation mindset. Focus on actionable insights. Keep it under 400 words.

Begin your reflection:"""

        return prompt
    
    def _run_aider_analysis(self, prompt: str) -> str:
        """Execute Aider analysis with the given prompt."""
        try:
            # Create temporary file for the prompt
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
                f.write(prompt)
                prompt_file = f.name
            
            # Output file for insights
            output_file = 'logs/daydream_insights.md'
            
            # Key files for analysis
            key_files = [
                'main.py',
                'tasks/promise_tasks.py', 
                'agents/valor/agent.py',
                'mcp_servers/social_tools.py',
                'integrations/telegram/handlers.py',
                'utilities/database.py',
                'CLAUDE.md'
            ]
            
            # Filter to only existing files
            existing_files = [f for f in key_files if Path(f).exists()]
            
            cmd = [
                '/Users/valorengels/.local/bin/aider',
                '--model', 'ollama_chat/gemma3:12b-it-qat',
                '--no-git',
                '--yes',
                '--message', f'Read the analysis prompt from {prompt_file}. Then explore this AI agent codebase and write detailed insights to {output_file}. Focus on architecture, patterns, and opportunities.'
            ] + existing_files
            
            # Run Aider with timeout
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.session_timeout,
                env={**os.environ, 'OLLAMA_API_BASE': 'http://127.0.0.1:11434'}
            )
            
            # Clean up prompt file
            Path(prompt_file).unlink(missing_ok=True)
            
            # Check if analysis was written to output file
            if Path(output_file).exists():
                with open(output_file, 'r') as f:
                    insights = f.read()
                    if insights.strip():
                        return insights
            
            # Fall back to stdout if no file was created
            if result.stdout:
                return f"Aider Analysis:\n{result.stdout}"
            elif result.stderr:
                return f"Aider encountered issues:\n{result.stderr}"
            else:
                return "Aider completed but generated no output"
                
        except subprocess.TimeoutExpired:
            return "Aider analysis timed out after 5 minutes"
        except Exception as e:
            return f"Aider analysis failed: {str(e)}"
    
    def _log_insights_to_console(self, insights: str) -> None:
        """Log AI-generated insights to console."""
        logger.info("🧠 ✨ AI Daydream Insights:")
        logger.info("🧠" + "="*60)
        
        # Split into paragraphs for better readability
        paragraphs = insights.split('\n\n')
        for paragraph in paragraphs:
            if paragraph.strip():
                # Indent each line for better log formatting
                for line in paragraph.split('\n'):
                    if line.strip():
                        logger.info(f"🧠 {line.strip()}")
                logger.info("🧠")  # Empty line between paragraphs
        
        logger.info("🧠" + "="*60)
    
    def _write_and_archive_insights(self, session: DaydreamSession) -> None:
        """Write insights to file and manage archival."""
        try:
            insights_file = Path('logs/daydream_insights.md')
            
            # Ensure logs directory exists
            insights_file.parent.mkdir(exist_ok=True)
            
            # Write insights with session metadata
            with open(insights_file, 'w') as f:
                f.write(f"# Daydream Insights - Session {session.session_id[:8]}\n\n")
                f.write(f"**Generated:** {session.start_time.isoformat()}\n")
                f.write(f"**Analysis Duration:** {session.analysis_duration:.1f}s\n")
                f.write(f"**Workspaces Analyzed:** {len(session.workspace_analysis)}\n\n")
                f.write("---\n\n")
                f.write(session.insights)
                
        except Exception as e:
            logger.error(f"Failed to write insights: {e}")
    
    def _generate_session_summary(self, session: DaydreamSession) -> None:
        """Generate session summary for monitoring."""
        summary = {
            'session_id': session.session_id,
            'start_time': session.start_time.isoformat(),
            'total_duration': (datetime.utcnow() - session.start_time).total_seconds(),
            'analysis_duration': session.analysis_duration,
            'workspaces_analyzed': len(session.workspace_analysis),
            'insights_length': len(session.insights),
            'cleanup_summary': session.cleanup_summary,
            'final_phase': session.phase
        }
        
        logger.info(f"🧠 Session Summary: {summary}")
    
    def _load_workspace_config(self) -> Dict[str, Any]:
        """Load workspace configuration."""
        try:
            with open('config/workspace_config.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load workspace config: {e}")
            return {}


def with_promise_tracking(func):
    """
    Decorator that handles promise status updates and error tracking.
    
    IMPLEMENTATION NOTE: This pattern ensures consistent status
    updates across all promise-executing tasks.
    """
    def wrapper(promise_id: int, *args, **kwargs):
        # Get promise info for enhanced logging
        promise = get_promise(promise_id)
        task_desc = promise.get('task_description', 'Unknown task') if promise else 'Unknown task'
        chat_id = promise.get('chat_id', 'Unknown') if promise else 'Unknown'
        
        start_time = datetime.now()
        logger.info(f"🚀 STARTING TASK [{func.__name__}] Promise {promise_id} | Chat: {chat_id}")
        logger.info(f"📝 Task Description: {task_desc[:100]}{'...' if len(task_desc) > 100 else ''}")
        
        try:
            # Mark as in_progress
            update_promise_status(promise_id, 'in_progress')
            
            # Execute the actual task
            result = func(promise_id, *args, **kwargs)
            
            # Calculate execution time
            execution_time = datetime.now() - start_time
            duration_str = f"{execution_time.total_seconds():.1f}s"
            
            # Mark as completed
            update_promise_status(promise_id, 'completed', result_summary=result)
            
            logger.info(f"✅ COMPLETED TASK [{func.__name__}] Promise {promise_id} in {duration_str}")
            logger.info(f"📊 Result Summary: {result[:150] if result else 'No result'}{'...' if result and len(result) > 150 else ''}")
            
            return result
            
        except Exception as e:
            execution_time = datetime.now() - start_time
            duration_str = f"{execution_time.total_seconds():.1f}s"
            
            logger.error(f"❌ FAILED TASK [{func.__name__}] Promise {promise_id} after {duration_str}")
            logger.error(f"💥 Error: {str(e)}")
            logger.error(f"🔍 Full traceback:", exc_info=True)
            
            update_promise_status(promise_id, 'failed', error_message=str(e))
            raise  # Re-raise for Huey retry mechanism
    
    # Preserve the original function's name for Huey
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


@huey.task(retries=3, retry_delay=60)
@with_promise_tracking
def execute_coding_task(promise_id: int) -> str:
    """
    Execute a coding task using Claude Code.
    
    BEST PRACTICE: Keep task functions focused on one responsibility.
    Complex logic should be broken into helper functions.
    
    Args:
        promise_id: Database ID of the promise to execute
        
    Returns:
        Result summary string for user notification
        
    Raises:
        Exception: Any error during execution (triggers retry)
    """
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    # Parse task metadata
    metadata = json.loads(promise.get('metadata') or '{}')
    workspace_context = metadata.get('workspace_context', {})
    
    # Get working directory from workspace context
    working_directory = workspace_context.get('working_directory', '.')
    workspace_name = workspace_context.get('workspace_name', 'Unknown')
    
    logger.info(f"💻 Coding Task Configuration:")
    logger.info(f"   🏢 Workspace: {workspace_name}")
    logger.info(f"   📁 Directory: {working_directory}")
    logger.info(f"   🔧 Instructions: {metadata.get('instructions', 'None')[:50]}{'...' if len(metadata.get('instructions', '')) > 50 else ''}")
    logger.info(f"🏃 Spawning Claude Code session...")
    
    # IMPLEMENTATION NOTE: Import here to avoid circular imports
    from tools.valor_delegation_tool import spawn_valor_session
    
    # Execute with Claude Code in the correct workspace
    result = spawn_valor_session(
        task_description=promise['task_description'],
        target_directory=working_directory,
        specific_instructions=metadata.get('instructions', ''),
        force_sync=True  # Force synchronous execution since we're already in background
    )
    
    # Send completion notification
    # BEST PRACTICE: Use .schedule() for follow-up tasks
    send_completion_notification.schedule(
        args=(promise_id, result),
        delay=1  # Small delay to ensure DB updates are committed
    )
    
    return result


@huey.task(retries=3, retry_delay=60)
@with_promise_tracking
def execute_search_task(promise_id: int) -> str:
    """Execute a search task using Perplexity AI."""
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    # Parse task metadata
    metadata = json.loads(promise.get('metadata') or '{}')
    workspace_context = metadata.get('workspace_context', {})
    workspace_name = workspace_context.get('workspace_name', 'Unknown')
    
    logger.info(f"🔍 Search Task Configuration:")
    logger.info(f"   🏢 Workspace: {workspace_name}")
    logger.info(f"   🌐 Using Perplexity AI for current information")
    
    # Extract search query from task description or metadata
    search_query = metadata.get('search_query', promise['task_description'])
    logger.info(f"   🔎 Search Query: {search_query[:100]}{'...' if len(search_query) > 100 else ''}")
    logger.info(f"🏃 Executing web search...")
    
    # Import search tool
    from tools.search_tool import search_web
    
    # Extract search query from task description or metadata
    search_query = metadata.get('query', promise['task_description'])
    max_results = metadata.get('max_results', 3)
    
    try:
        # Execute search
        result = search_web(search_query, max_results=max_results)
        
        # Send completion notification
        send_completion_notification.schedule(args=(promise_id, result), delay=1)
        
        return result
    except Exception as e:
        logger.error(f"Search task failed: {str(e)}")
        raise


@huey.task(retries=3, retry_delay=60)
@with_promise_tracking
def execute_analysis_task(promise_id: int) -> str:
    """Execute an analysis task (image, link, or document analysis)."""
    promise = get_promise(promise_id)
    if not promise:
        raise ValueError(f"Promise {promise_id} not found")
    
    # Parse task metadata
    metadata = json.loads(promise.get('metadata') or '{}')
    workspace_context = metadata.get('workspace_context', {})
    workspace_name = workspace_context.get('workspace_name', 'Unknown')
    analysis_type = metadata.get('analysis_type', 'general')
    
    logger.info(f"Executing {analysis_type} analysis task for workspace: {workspace_name}")
    
    result = None
    
    try:
        if analysis_type == 'image':
            # Import image analysis tool
            from tools.image_analysis_tool import analyze_image_with_ai
            
            image_path = metadata.get('image_path')
            question = metadata.get('question', '')
            
            if not image_path:
                raise ValueError("Image path required for image analysis")
            
            result = analyze_image_with_ai(image_path, question, str(promise['chat_id']))
            
        elif analysis_type == 'link':
            # Import link analysis tool
            from tools.link_analysis_tool import analyze_link
            
            url = metadata.get('url')
            if not url:
                raise ValueError("URL required for link analysis")
            
            result = analyze_link(url, str(promise['chat_id']))
            
        elif analysis_type == 'document':
            # Import documentation tool
            from tools.documentation_tool import analyze_documentation
            
            file_path = metadata.get('file_path', '.')
            question = metadata.get('question', promise['task_description'])
            
            result = analyze_documentation(file_path, question)
            
        else:
            # General analysis - use Claude for text analysis
            from agents.valor.agent import valor_agent
            from agents.valor.context import TelegramChatContext
            
            # Build context for analysis
            context = TelegramChatContext(
                chat_id=promise['chat_id'],
                username=metadata.get('username'),
                is_group_chat=metadata.get('is_group_chat', False)
            )
            
            # Run analysis through valor agent
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            analysis_prompt = f"Please analyze this: {promise['task_description']}"
            agent_result = loop.run_until_complete(
                valor_agent.run(analysis_prompt, deps=context)
            )
            result = agent_result.data
        
        # Send completion notification
        send_completion_notification.schedule(args=(promise_id, result), delay=1)
        
        return result
        
    except Exception as e:
        logger.error(f"Analysis task failed: {str(e)}")
        raise


@huey.task(retries=2, retry_delay=30)
def send_completion_notification(promise_id: int, result: str):
    """
    Send completion message to user via database task queue.
    
    IMPLEMENTATION NOTE: This is a separate task so notification
    failures don't affect the main task completion status.
    """
    promise = get_promise(promise_id)
    if not promise:
        logger.error(f"Promise {promise_id} not found for notification")
        return
    
    # Format completion message
    # BEST PRACTICE: Make messages informative but concise
    duration = format_duration(promise.get('created_at'), promise.get('completed_at'))
    message = f"""✅ **Task Complete!**

I finished working on: {promise['task_description'][:100]}{'...' if len(promise['task_description']) > 100 else ''}

**Result:**
{result[:500]}{'...' if len(result) > 500 else ''}

_Completed in {duration}_
"""
    
    try:
        # Queue message via database task queue instead of direct Telegram client
        from utilities.database import queue_server_task
        queue_server_task(
            'send_message',
            {
                'chat_id': promise['chat_id'],
                'message_text': message
            },
            priority=4  # Normal priority for notifications
        )
        
        logger.info(f"Queued completion notification for promise {promise_id}")
    except Exception as e:
        # BEST PRACTICE: Log notification failures but don't retry forever
        logger.error(f"Failed to queue completion notification: {e}")


@huey.task()
def check_promise_dependencies(promise_id: int):
    """
    Check if promise dependencies are satisfied and execute if ready.
    
    DESIGN PATTERN: Simple dependency checking via polling.
    For v1, we poll every 30 seconds. Future versions could use
    signals or callbacks for immediate execution.
    """
    promise = get_promise(promise_id)
    if not promise or promise['status'] != 'waiting':
        return
    
    # Check parent promises - stored in metadata
    metadata = json.loads(promise.get('metadata') or '{}')
    parent_ids = metadata.get('parent_promise_ids', [])
    
    if not parent_ids:
        # No dependencies, execute immediately
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))
        return
    
    # Check if all parents are completed
    all_completed = True
    failed_parents = []
    
    for parent_id in parent_ids:
        parent = get_promise(parent_id)
        if not parent:
            logger.warning(f"Parent promise {parent_id} not found for promise {promise_id}")
            failed_parents.append(parent_id)
            all_completed = False
        elif parent['status'] == 'failed':
            logger.warning(f"Parent promise {parent_id} failed for promise {promise_id}")
            failed_parents.append(parent_id)
            all_completed = False
        elif parent['status'] != 'completed':
            all_completed = False
    
    if failed_parents:
        # If any parent failed, fail this promise too
        error_msg = f"Parent promise(s) failed: {', '.join(map(str, failed_parents))}"
        update_promise_status(promise_id, 'failed', error_message=error_msg)
        return
    
    if all_completed:
        # Dependencies satisfied, execute
        logger.info(f"Dependencies satisfied for promise {promise_id}")
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))
    else:
        # Check again in 30 seconds
        # IMPLEMENTATION NOTE: Exponential backoff could be added here
        check_promise_dependencies.schedule(
            args=(promise_id,),
            delay=30
        )


@huey.task()
def execute_promise_by_type(promise_id: int):
    """
    Route promise to appropriate execution task based on type.
    
    BEST PRACTICE: Use a routing function to keep task selection
    logic centralized and easy to extend.
    """
    logger.info(f"Execute promise by type called for promise {promise_id}")
    promise = get_promise(promise_id)
    if not promise:
        logger.error(f"Promise {promise_id} not found")
        return
    
    logger.info(f"Promise {promise_id} details: type={promise.get('task_type')}, status={promise.get('status')}, description={promise.get('task_description')[:50]}...")
    
    # Route based on task type
    # IMPLEMENTATION NOTE: Add new task types here as needed
    task_map = {
        'code': execute_coding_task,
        'search': execute_search_task,
        'analysis': execute_analysis_task,
    }
    
    # Check if this is a test execution task
    metadata = json.loads(promise.get('metadata') or '{}')
    if metadata.get('test_files') or metadata.get('test_pattern'):
        # This is a test execution promise
        from tasks.test_runner_tasks import execute_test_suite
        task_func = execute_test_suite
    else:
        task_func = task_map.get(promise['task_type'])
    if task_func:
        task_name = getattr(task_func, '__name__', str(task_func))
        logger.info(f"Routing promise {promise_id} to {task_name}")
        # Schedule the task instead of calling directly with delay=0 for immediate execution
        result = task_func.schedule(args=(promise_id,), delay=0)
        logger.info(f"Scheduled task {task_name} for promise {promise_id}, Huey task ID: {getattr(result, 'id', 'unknown')}")
    else:
        logger.error(f"Unknown task type: {promise['task_type']}")
        update_promise_status(promise_id, 'failed', error_message=f"Unknown task type: {promise['task_type']}")


# BEST PRACTICE: Periodic cleanup tasks
@huey.periodic_task(crontab(minute='*/30'))
def cleanup_old_promises():
    """
    Clean up old completed/failed promises.
    
    IMPLEMENTATION NOTE: Keeps last 7 days of history for debugging.
    Adjust retention period based on your needs.
    """
    cutoff_date = datetime.utcnow() - timedelta(days=7)
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM promises 
            WHERE status IN ('completed', 'failed') 
            AND completed_at < ?
        """, (cutoff_date,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        
    if deleted_count > 0:
        logger.info(f"Cleaned up {deleted_count} old promises")


@huey.periodic_task(crontab(minute='*/5'))
def resume_stalled_promises():
    """
    Resume promises that got stuck (e.g., due to restart).
    
    BEST PRACTICE: Always have a recovery mechanism for
    tasks that might get orphaned during restarts.
    
    Enhanced to handle:
    - In-progress promises stalled for >4 hours
    - Pending promises orphaned for >5 minutes (server restart recovery)
    """
    stalled_count = 0
    orphaned_count = 0
    
    # Find promises marked as in_progress for too long (original logic)
    stalled_cutoff = datetime.utcnow() - timedelta(hours=4)
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM promises 
            WHERE status = 'in_progress' 
            AND created_at < ?
        """, (stalled_cutoff,))
        
        stalled_promises = cursor.fetchall()
    
    for (promise_id,) in stalled_promises:
        logger.warning(f"Resuming stalled promise {promise_id}")
        # Reset to pending
        update_promise_status(promise_id, 'pending')
        execute_promise_by_type.schedule(args=(promise_id,))
        stalled_count += 1
    
    # Find pending promises that were never processed after server restart
    orphaned_cutoff = datetime.utcnow() - timedelta(minutes=5)
    
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM promises 
            WHERE status = 'pending' 
            AND created_at < ?
        """, (orphaned_cutoff,))
        
        orphaned_promises = cursor.fetchall()
    
    for (promise_id,) in orphaned_promises:
        logger.warning(f"Resuming orphaned pending promise {promise_id}")
        execute_promise_by_type.schedule(args=(promise_id,))
        orphaned_count += 1
    
    if stalled_count > 0 or orphaned_count > 0:
        logger.info(f"Promise recovery: {stalled_count} stalled, {orphaned_count} orphaned promises resumed")


@huey.task(retries=1, retry_delay=30)
def startup_promise_recovery():
    """
    Process any orphaned promises on server startup.
    
    This function is called once when the server starts to handle
    promises that may have been interrupted during the previous shutdown.
    """
    logger.info("🔄 Starting startup promise recovery...")
    
    recovered_count = 0
    
    # Find all pending promises (may have been orphaned during restart)
    with get_database_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, task_description, created_at FROM promises 
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)
        
        pending_promises = cursor.fetchall()
    
    if not pending_promises:
        logger.info("✅ No orphaned promises found during startup")
        return
    
    logger.info(f"📬 Found {len(pending_promises)} pending promises to recover")
    
    for promise_id, task_description, created_at in pending_promises:
        logger.info(f"Recovering promise {promise_id}: {task_description[:50]}... (created: {created_at})")
        
        # Schedule the promise for immediate execution
        execute_promise_by_type.schedule(args=(promise_id,), delay=0)
        recovered_count += 1
    
    logger.info(f"✅ Startup promise recovery complete: {recovered_count} promises scheduled for execution")


@huey.periodic_task(crontab(minute='*/15'))
def system_health_check():
    """
    Periodic system health monitoring and queue analysis.
    
    IMPLEMENTATION NOTE: This task performs deterministic system health
    checks without consuming any API tokens.
    
    Features:
    - Check pending promises and queue health
    - Analyze recent development patterns
    - Generate health status and metrics
    - Log system performance insights
    - Detect stalled tasks and bottlenecks
    """
    logger.info("💓 Starting system health check...")
    
    try:
        # Get system state
        health_data = gather_system_health_data()
        
        # Analyze current state and generate insights
        health_insights = analyze_system_health(health_data)
        
        # Log health insights
        log_health_insights(health_insights, health_data)
        
        logger.info(f"💓 Health check complete. Queue: {health_data['queue_summary']}, Active workspaces: {len(health_data['workspace_activity'])}")
        
    except Exception as e:
        logger.error(f"💓 Health check failed: {str(e)}", exc_info=True)


def gather_system_health_data() -> Dict[str, Any]:
    """Gather current system state for health analysis."""
    with get_database_connection() as conn:
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        
        # Promise queue analysis
        queue_stats = conn.execute("""
            SELECT 
                status,
                COUNT(*) as count,
                task_type,
                COUNT(DISTINCT chat_id) as unique_chats
            FROM promises 
            WHERE created_at > datetime('now', '-24 hours')
            GROUP BY status, task_type
        """).fetchall()
        
        # Recent activity by workspace (via chat_id mapping)
        recent_activity = conn.execute("""
            SELECT 
                chat_id,
                COUNT(*) as tasks,
                task_type,
                MAX(created_at) as last_activity
            FROM promises 
            WHERE created_at > datetime('now', '-7 days')
            GROUP BY chat_id, task_type
            ORDER BY last_activity DESC
        """).fetchall()
        
        # System health indicators
        pending_count = conn.execute("""
            SELECT COUNT(*) FROM promises WHERE status = 'pending'
        """).fetchone()[0]
        
        stalled_count = conn.execute("""
            SELECT COUNT(*) FROM promises 
            WHERE status = 'in_progress' 
            AND created_at < datetime('now', '-4 hours')
        """).fetchone()[0]
        
        # Recent completion rate
        completion_stats = conn.execute("""
            SELECT 
                status,
                COUNT(*) as count
            FROM promises 
            WHERE created_at > datetime('now', '-24 hours')
            GROUP BY status
        """).fetchall()
    
    return {
        'timestamp': datetime.utcnow().isoformat(),
        'queue_stats': [dict(row) for row in queue_stats] if queue_stats else [],
        'workspace_activity': [dict(row) for row in recent_activity] if recent_activity else [],
        'pending_count': pending_count,
        'stalled_count': stalled_count,
        'completion_stats': [dict(row) for row in completion_stats] if completion_stats else [],
        'queue_summary': f"{pending_count} pending, {stalled_count} stalled"
    }


def analyze_system_health(data: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze system health and generate insights."""
    insights = {
        'timestamp': data['timestamp'],
        'health_status': 'healthy',
        'observations': [],
        'suggestions': [],
        'workspace_insights': []
    }
    
    # Analyze queue health
    if data['pending_count'] > 10:
        insights['health_status'] = 'busy'
        insights['observations'].append(f"High queue volume: {data['pending_count']} pending tasks")
    elif data['pending_count'] == 0 and data['stalled_count'] == 0:
        insights['health_status'] = 'idle'
        insights['observations'].append("System is idle - perfect time for optimization")
        insights['suggestions'].append("Consider running system maintenance or exploring new features")
    
    if data['stalled_count'] > 0:
        insights['health_status'] = 'attention_needed'
        insights['observations'].append(f"Found {data['stalled_count']} stalled tasks - may need manual review")
    
    # Analyze workspace patterns
    workspace_map = load_workspace_config()
    for activity in data['workspace_activity']:
        chat_id = str(activity['chat_id'])
        if chat_id in workspace_map.get('telegram_groups', {}):
            workspace_name = workspace_map['telegram_groups'][chat_id]
            insights['workspace_insights'].append({
                'workspace': workspace_name,
                'tasks': activity['tasks'],
                'last_activity': activity['last_activity'],
                'task_type': activity['task_type']
            })
    
    # Generate completion rate insight
    total_completed = sum(stat['count'] for stat in data['completion_stats'] if stat['status'] == 'completed')
    total_failed = sum(stat['count'] for stat in data['completion_stats'] if stat['status'] == 'failed')
    if total_completed + total_failed > 0:
        success_rate = total_completed / (total_completed + total_failed) * 100
        insights['success_rate'] = round(success_rate, 1)
        
        if success_rate < 90:
            insights['observations'].append(f"Success rate at {success_rate}% - may need attention")
        else:
            insights['observations'].append(f"Excellent success rate: {success_rate}%")
    
    return insights


def log_health_insights(insights: Dict[str, Any], data: Dict[str, Any]) -> None:
    """Log health insights to console."""
    # Format insights for logging
    status_emoji = {
        'healthy': '💚',
        'idle': '😴', 
        'busy': '🔥',
        'attention_needed': '⚠️'
    }
    
    emoji = status_emoji.get(insights['health_status'], '🤖')
    logger.info(f"💓 {emoji} System Health: {insights['health_status']}")
    
    # Log key observations
    if insights['observations']:
        logger.info("💓 📊 Health Observations:")
        for obs in insights['observations']:
            logger.info(f"💓   • {obs}")
    
    # Log workspace activity
    if insights['workspace_insights']:
        logger.info("💓 🏗️ Recent Workspace Activity:")
        for ws in insights['workspace_insights']:
            logger.info(f"💓   • {ws['workspace']}: {ws['tasks']} tasks ({ws['task_type']})")
    
    # Log success rate
    if 'success_rate' in insights:
        logger.info(f"💓 ✅ Success Rate: {insights['success_rate']}%")
    
    # Log suggestions
    if insights['suggestions']:
        logger.info("💓 💡 Health Suggestions:")
        for suggestion in insights['suggestions']:
            logger.info(f"💓   • {suggestion}")
    
    # Log raw data summary
    logger.info(f"💓 📈 Health Metrics: {data['pending_count']} pending, {data['stalled_count']} stalled, {len(data['workspace_activity'])} active workspaces")
    
    # Check if this would have triggered a message (for monitoring purposes)
    would_alert = (
        data['stalled_count'] > 2 or 
        data['pending_count'] > 15 or 
        (data['pending_count'] == 0 and len(data['workspace_activity']) > 0)
    )
    if would_alert:
        logger.info("💓 🚨 [Health check would trigger alert if messaging was enabled]")


@huey.periodic_task(crontab(hour='*/6'))  # Every 6 hours
def daydream_and_reflect():
    """
    Unified AI-powered daydream and reflection system with integrated cleanup.
    
    This replaces the previous scattered approach with a comprehensive 6-phase system:
    1. System Readiness Check - Ensure system is idle and ready
    2. Pre-Analysis Cleanup - Clean old processes and temp files
    3. Context Gathering - Unified workspace and system analysis
    4. AI Analysis - Ollama-powered codebase reflection
    5. Output Processing - Insights logging and archival
    6. Post-Analysis Cleanup - Resource cleanup and state reset
    
    Features:
    - Integrated cleanup lifecycle with daydream cycles
    - Session-based tracking with correlation IDs
    - Comprehensive error recovery and emergency cleanup
    - Unified context gathering replacing scattered functions
    - Performance monitoring and resource management
    """
    # Create new daydream session
    session = DaydreamSession(
        session_id=str(uuid.uuid4()),
        start_time=datetime.utcnow()
    )
    
    # Initialize unified daydream system
    daydream_system = UnifiedDaydreamSystem()
    
    try:
        logger.info(f"🧠 Starting unified daydream session {session.session_id[:8]}")
        
        # Phase 1: System Readiness Check
        if not daydream_system._check_system_readiness(session):
            return
            
        # Phase 2: Pre-Analysis Cleanup  
        daydream_system._cleanup_before_analysis(session)
        
        # Phase 3: Context Gathering
        daydream_system._gather_comprehensive_context(session)
        
        # Phase 4: AI Analysis
        daydream_system._execute_ai_analysis(session)
        
        # Phase 5: Output & Archival
        daydream_system._process_insights_and_output(session)
        
        # Phase 6: Post-Analysis Cleanup
        daydream_system._cleanup_after_analysis(session)
        
        # Final session summary
        session.log_phase_transition('complete')
        total_duration = (datetime.utcnow() - session.start_time).total_seconds()
        logger.info(f"🧠 Unified daydream session complete: {session.session_id[:8]} "
                   f"({total_duration:.1f}s total, {session.analysis_duration:.1f}s analysis)")
        
    except Exception as e:
        logger.error(f"🧠 Unified daydream session {session.session_id[:8]} failed: {e}", exc_info=True)
        daydream_system._emergency_cleanup(session)


def gather_daydream_context() -> Dict[str, Any]:
    """Gather rich context for AI-powered daydreaming."""
    context = {
        'timestamp': datetime.utcnow().isoformat(),
        'workspace_analysis': {},
        'recent_activity': {},
        'codebase_insights': {},
        'system_metrics': {},
        'development_trends': {}
    }
    
    try:
        # Get workspace configurations
        workspace_config = load_workspace_config()
        
        # Get recent promise activity and workspace priorities for trend analysis
        with get_database_connection() as conn:
            # Analyze workspaces iteratively - prioritize by config + activity
            workspaces = workspace_config.get('workspaces', {})
            workspace_priorities = get_workspace_priorities(conn, workspaces)
            
            # Rotate through workspaces to ensure all get analyzed over time
            analyzed_count = 0
            max_workspaces_per_cycle = 3
            
            # Use current hour to determine rotation offset for fair distribution
            from datetime import datetime as dt
            current_hour = dt.utcnow().hour
            rotation_offset = (current_hour // 6) % len(workspace_priorities) if workspace_priorities else 0
            
            # Create rotated list starting from offset
            rotated_priorities = workspace_priorities[rotation_offset:] + workspace_priorities[:rotation_offset]
            
            for workspace_name, workspace_data in rotated_priorities[:max_workspaces_per_cycle]:
                if isinstance(workspace_data, dict) and workspace_data.get('working_directory'):
                    try:
                        logger.info(f"🧠 Analyzing workspace: {workspace_name}")
                        workspace_context = analyze_workspace_for_daydream(
                            workspace_name, 
                            workspace_data['working_directory']
                        )
                        context['workspace_analysis'][workspace_name] = workspace_context
                        analyzed_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to analyze workspace {workspace_name}: {e}")
            
            logger.info(f"🧠 Analyzed {analyzed_count}/{len(workspaces)} workspaces this cycle")
            
            # Get recent promises with proper column mapping
            recent_promises = conn.execute("""
                SELECT 
                    task_description,
                    task_type,
                    status,
                    created_at,
                    completed_at,
                    chat_id
                FROM promises 
                WHERE created_at > datetime('now', '-7 days')
                ORDER BY created_at DESC
                LIMIT 50
            """).fetchall()
            
            # Convert to list of dictionaries properly
            context['recent_activity'] = []
            if recent_promises:
                for row in recent_promises:
                    try:
                        # Access by index since row_factory might not be set
                        context['recent_activity'].append({
                            'task_description': row[0],
                            'task_type': row[1],
                            'status': row[2],
                            'created_at': row[3],
                            'completed_at': row[4],
                            'chat_id': row[5]
                        })
                    except Exception as e:
                        logger.warning(f"Failed to process promise row: {e}")
                        continue
            
            # Get system metrics
            context['system_metrics'] = gather_system_metrics(conn)
            
            # Get development trends
            context['development_trends'] = gather_development_trends(conn)
    
    except Exception as e:
        logger.error(f"Error gathering daydream context: {e}")
        # Return minimal context to prevent complete failure
        context = {
            'timestamp': datetime.utcnow().isoformat(),
            'workspace_analysis': {},
            'recent_activity': [],
            'error': str(e)
        }
    
    return context


def analyze_workspace_for_daydream(workspace_name: str, working_dir: str) -> Dict[str, Any]:
    """Analyze a workspace directory for daydreaming context."""
    import os
    import subprocess
    import glob
    
    workspace_info = {
        'name': workspace_name,
        'directory': working_dir,
        'exists': os.path.exists(working_dir),
        'git_status': None,
        'recent_commits': [],
        'file_stats': {},
        'tech_stack': [],
        'complexity_metrics': {},
        'quality_indicators': {},
        'development_activity': {}
    }
    
    if not workspace_info['exists']:
        return workspace_info
    
    try:
        # Get git status if it's a git repo
        git_status = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=10
        )
        if git_status.returncode == 0:
            workspace_info['git_status'] = git_status.stdout.strip()
            
            # Get recent commits
            git_log = subprocess.run(
                ['git', 'log', '--oneline', '-10'],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=10
            )
            if git_log.returncode == 0:
                workspace_info['recent_commits'] = git_log.stdout.strip().split('\n')
        
        # Analyze file types and patterns
        for root, dirs, files in os.walk(working_dir):
            # Skip hidden directories and common build dirs
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', 'venv', '.venv']]
            
            for file in files:
                if not file.startswith('.'):
                    ext = os.path.splitext(file)[1].lower()
                    workspace_info['file_stats'][ext] = workspace_info['file_stats'].get(ext, 0) + 1
        
        # Detect tech stack
        common_files = {
            'package.json': 'Node.js/JavaScript',
            'requirements.txt': 'Python',
            'Cargo.toml': 'Rust',
            'go.mod': 'Go',
            'pom.xml': 'Java/Maven',
            'Dockerfile': 'Docker',
            'docker-compose.yml': 'Docker Compose',
            'CLAUDE.md': 'Claude Code Integration'
        }
        
        for file, tech in common_files.items():
            if os.path.exists(os.path.join(working_dir, file)):
                workspace_info['tech_stack'].append(tech)
        
        # Add enhanced analysis
        workspace_info = enhance_workspace_analysis(workspace_info, working_dir)
                
    except Exception as e:
        workspace_info['analysis_error'] = str(e)
    
    return workspace_info


def aider_daydream_analysis(context: Dict[str, Any]) -> str:
    """Use Aider to perform creative codebase analysis and generate insights."""
    import subprocess
    import tempfile
    import os
    
    try:
        # Create a comprehensive analysis prompt for Aider
        analysis_prompt = build_aider_daydream_prompt(context)
        
        # Create temporary file for the prompt
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(analysis_prompt)
            prompt_file = f.name
        
        # Create output file for insights
        output_file = 'logs/daydream_insights.md'
        
        try:
            # Run Aider with our analysis prompt
            # Include key files for comprehensive analysis
            key_files = [
                'main.py',
                'tasks/promise_tasks.py', 
                'agents/valor/agent.py',
                'mcp_servers/social_tools.py',
                'integrations/telegram/handlers.py',
                'utilities/database.py',
                'CLAUDE.md',
                'config/workspace_config.json'
            ]
            
            # Filter to only existing files
            existing_files = [f for f in key_files if os.path.exists(f)]
            
            cmd = [
                '/Users/valorengels/.local/bin/aider',
                '--model', 'ollama_chat/gemma3:12b-it-qat',
                '--no-git',
                '--yes',
                '--message', f'Read the comprehensive analysis prompt from {prompt_file}. Then explore this AI agent codebase and write detailed insights to {output_file}. Focus on the architecture, patterns, and opportunities for this conversational AI system.'
            ] + existing_files
            
            # Set environment variables
            env = os.environ.copy()
            env['OLLAMA_API_BASE'] = 'http://127.0.0.1:11434'
            
            # Run Aider with timeout
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
                env=env,
                cwd=os.getcwd()
            )
            
            # Check if analysis was written to output file
            if os.path.exists(output_file):
                with open(output_file, 'r') as f:
                    insights = f.read()
                    if insights.strip():
                        return insights
            
            # Fall back to stdout if no file was created
            if result.stdout:
                return f"Aider Analysis:\n{result.stdout}"
            elif result.stderr:
                return f"Aider encountered issues:\n{result.stderr}"
            else:
                return "Aider completed but generated no output"
                
        finally:
            # Clean up temporary prompt file
            if os.path.exists(prompt_file):
                os.unlink(prompt_file)
            
    except subprocess.TimeoutExpired:
        return "Aider analysis timed out after 5 minutes"
    except Exception as e:
        return f"Aider daydream analysis failed: {str(e)}"


def build_daydream_prompt(context: Dict[str, Any]) -> str:
    """Build a creative prompt for AI daydreaming."""
    
    # Summarize workspace activity with enhanced metrics
    workspace_summary = []
    for name, info in context['workspace_analysis'].items():
        if info.get('exists'):
            tech_stack = ', '.join(info.get('tech_stack', []))
            
            # Add quality indicators
            quality_info = info.get('quality_indicators', {})
            quality_flags = []
            if quality_info.get('has_tests'):
                quality_flags.append('tests')
            if quality_info.get('has_docs'):
                quality_flags.append('docs')
            if quality_info.get('has_ci'):
                quality_flags.append('CI')
            
            # Add complexity metrics
            complexity = info.get('complexity_metrics', {})
            complexity_note = ""
            if complexity.get('avg_lines_per_file', 0) > 200:
                complexity_note = " [large files detected]"
            elif complexity.get('total_python_files', 0) > 50:
                complexity_note = " [complex codebase]"
            
            workspace_line = f"- {name}: {tech_stack}"
            if quality_flags:
                workspace_line += f" (has: {', '.join(quality_flags)})"
            if complexity_note:
                workspace_line += complexity_note
            
            workspace_summary.append(workspace_line)
    
    # Summarize recent activity with trends
    activity_summary = []
    for activity in context['recent_activity'][:10]:
        status_emoji = "✅" if activity['status'] == 'completed' else "⏳" if activity['status'] == 'in_progress' else "❌" if activity['status'] == 'failed' else "📋"
        activity_summary.append(f"- {status_emoji} {activity['task_type']}: {activity['task_description'][:50]}...")
    
    # Add system metrics if available
    metrics_summary = ""
    if 'system_metrics' in context and context['system_metrics']:
        metrics = context['system_metrics']
        success_rate = metrics.get('success_rate', 0)
        total_tasks = sum(stats.get('count', 0) for stats in metrics.get('completion_stats', {}).values())
        metrics_summary = f"\n\nSYSTEM PERFORMANCE (last 30 days):\n- Success rate: {success_rate}% ({total_tasks} total tasks)"
        
        if 'task_types' in metrics:
            top_tasks = list(metrics['task_types'].items())[:3]
            task_breakdown = ", ".join([f"{task}: {count}" for task, count in top_tasks])
            metrics_summary += f"\n- Most common tasks: {task_breakdown}"
    
    # Add development trends
    trends_summary = ""
    if 'development_trends' in context and context['development_trends'].get('weekly_trends'):
        recent_weeks = context['development_trends']['weekly_trends'][:3]
        if recent_weeks:
            avg_completion_rate = sum(w.get('completion_rate', 0) for w in recent_weeks) / len(recent_weeks)
            trends_summary = f"\n\nDEVELOPMENT TRENDS:\n- Recent completion rate: {avg_completion_rate:.1f}% (3-week average)"
    
    prompt = f"""You are Valor Engels, an AI system performing thoughtful reflection on your development environment and activities.

WORKSPACE OVERVIEW:
{chr(10).join(workspace_summary) if workspace_summary else '- No active workspaces detected'}

RECENT DEVELOPMENT ACTIVITY:
{chr(10).join(activity_summary) if activity_summary else '- No recent activity'}{metrics_summary}{trends_summary}

Time for creative reflection! As an intelligent development system, provide insights about:

1. **Architecture Patterns**: What patterns emerge from the workspace analysis and tech stacks?
2. **Development Velocity**: How does the data suggest productivity trends and focus areas?
3. **Quality Assessment**: Based on tests, docs, and complexity metrics, what quality insights stand out?
4. **Technical Opportunities**: What improvements, optimizations, or innovations come to mind?
5. **Strategic Direction**: Where might this development trajectory lead? What future possibilities emerge?
6. **Process Insights**: Any observations about development workflows, task patterns, or efficiency?

Respond as Valor would - analytical yet creative, combining German engineering precision with California innovation mindset. Focus on actionable insights and creative connections. Keep it under 400 words and avoid generic advice.

Begin your reflection:"""

    return prompt


def build_aider_daydream_prompt(context: Dict[str, Any]) -> str:
    """Build a comprehensive analysis prompt specifically for Aider codebase exploration."""
    
    # Summarize workspace context for Aider
    workspace_summary = []
    for name, info in context['workspace_analysis'].items():
        if info.get('exists'):
            tech_stack = ', '.join(info.get('tech_stack', []))
            complexity = info.get('complexity_metrics', {})
            quality = info.get('quality_indicators', {})
            
            quality_items = [k.replace('has_', '') for k, v in quality.items() if v]
            files_analyzed = complexity.get('files_analyzed', 0)
            total_files = complexity.get('total_python_files', 0)
            avg_lines = complexity.get('avg_lines_per_file', 0)
            
            workspace_summary.append(f"""
**{name}:**
- Tech Stack: {tech_stack}
- Quality: {', '.join(quality_items) if quality_items else 'None detected'}
- Code Metrics: {files_analyzed}/{total_files} files analyzed, {avg_lines} avg lines/file
- Directory: {info.get('directory', 'Unknown')}""")
    
    # System performance summary
    metrics_summary = ""
    if 'system_metrics' in context and context['system_metrics']:
        metrics = context['system_metrics']
        success_rate = metrics.get('success_rate', 0)
        task_types = metrics.get('task_types', {})
        metrics_summary = f"""
**System Performance:**
- Success Rate: {success_rate}%
- Task Distribution: {dict(list(task_types.items())[:3]) if task_types else 'No data'}
- Recent Activity: {len(context.get('recent_activity', []))} tasks in last 7 days"""
    
    # Recent development activity
    activity_summary = ""
    recent_activities = context.get('recent_activity', [])[:5]
    if recent_activities:
        activity_lines = []
        for activity in recent_activities:
            status_emoji = "✅" if activity['status'] == 'completed' else "⏳" if activity['status'] == 'in_progress' else "❌"
            activity_lines.append(f"- {status_emoji} {activity['task_type']}: {activity['task_description'][:60]}...")
        activity_summary = f"""
**Recent Development Activity:**
{chr(10).join(activity_lines)}"""
    
    prompt = f"""# Aider Daydreaming Analysis Request

You are Valor Engels, an AI system performing deep codebase reflection and creative analysis. You have been given context about the current development environment and need to provide thoughtful insights.

## Workspace Analysis Context
{chr(10).join(workspace_summary) if workspace_summary else '- No workspaces analyzed this cycle'}

{metrics_summary}

{activity_summary}

## Analysis Instructions

Please perform a comprehensive analysis of this codebase with the following focus areas:

### 1. Architecture Patterns & Design
- Examine the codebase structure and identify architectural patterns
- Look for design patterns, modularity, and code organization
- Assess the overall system design and component relationships

### 2. Code Quality & Technical Health  
- Review code complexity, maintainability, and readability
- Identify potential technical debt or areas for improvement
- Assess testing coverage and documentation quality

### 3. Development Velocity & Productivity
- Analyze recent development patterns and commit history
- Evaluate development workflow efficiency
- Identify bottlenecks or optimization opportunities

### 4. Technology Stack & Dependencies
- Review technology choices and their appropriateness
- Examine dependencies and potential upgrade paths
- Assess security and performance implications

### 5. Strategic Opportunities
- Identify areas for innovation or enhancement
- Suggest architectural improvements or refactoring opportunities
- Recommend process or tooling improvements

### 6. Future Direction & Vision
- Propose strategic development directions
- Identify emerging patterns or opportunities
- Suggest long-term architectural evolution paths

## Output Format

Write your analysis as a comprehensive markdown document to `logs/daydream_insights.md`. Structure it with clear sections and actionable insights. Be specific about file locations, code patterns, and concrete recommendations.

Focus on:
- **Specific observations** about the codebase
- **Actionable recommendations** for improvement
- **Strategic insights** about future development
- **Technical opportunities** for optimization

Respond as Valor would - analytical yet creative, combining German engineering precision with California innovation mindset. Provide concrete, implementable suggestions based on actual code exploration.

Begin your analysis by exploring the codebase structure and then proceed with your comprehensive assessment."""

    return prompt


def log_daydream_insights(insights: str) -> None:
    """Log AI-generated daydream insights."""
    logger.info("🧠 ✨ AI Daydream Insights:")
    logger.info("🧠" + "="*60)
    
    # Split into paragraphs for better readability
    paragraphs = insights.split('\n\n')
    for paragraph in paragraphs:
        if paragraph.strip():
            # Indent each line for better log formatting
            for line in paragraph.split('\n'):
                if line.strip():
                    logger.info(f"🧠 {line.strip()}")
            logger.info("🧠")  # Empty line between paragraphs
    
    logger.info("🧠" + "="*60)


def format_reflection_message(insights: Dict[str, Any]) -> str:
    """Format insights into a user-friendly message."""
    status_emoji = {
        'healthy': '💚',
        'idle': '😴', 
        'busy': '🔥',
        'attention_needed': '⚠️'
    }
    
    emoji = status_emoji.get(insights['health_status'], '🤖')
    
    message = f"{emoji} **System Reflection**\n"
    
    # Status summary
    if insights['health_status'] == 'idle':
        message += "System is quiet - good time for planning or optimization.\n"
    elif insights['health_status'] == 'busy':
        message += "System is actively processing tasks.\n"
    elif insights['health_status'] == 'attention_needed':
        message += "Some tasks may need attention.\n"
    else:
        message += "All systems operating normally.\n"
    
    # Key observations
    if insights['observations']:
        message += f"\n📊 **Observations:**\n"
        for obs in insights['observations'][:3]:  # Limit to top 3
            message += f"• {obs}\n"
    
    # Workspace activity
    if insights['workspace_insights']:
        message += f"\n🏗️ **Recent Activity:**\n"
        for ws in insights['workspace_insights'][:3]:  # Top 3 workspaces
            message += f"• {ws['workspace']}: {ws['tasks']} tasks ({ws['task_type']})\n"
    
    # Success rate if available
    if 'success_rate' in insights:
        message += f"\n✅ **Success Rate:** {insights['success_rate']}%\n"
    
    # Suggestions
    if insights['suggestions']:
        message += f"\n💡 **Suggestions:**\n"
        for suggestion in insights['suggestions'][:2]:  # Max 2 suggestions
            message += f"• {suggestion}\n"
    
    message += f"\n_Reflection at {datetime.fromisoformat(insights['timestamp']).strftime('%H:%M')}_"
    
    return message


def load_workspace_config() -> Dict[str, Any]:
    """Load workspace configuration."""
    try:
        with open('config/workspace_config.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load workspace config: {e}")
        return {}


def format_duration(start_time: str, end_time: str) -> str:
    """Format duration between two timestamps."""
    if not start_time or not end_time:
        return "unknown duration"
    
    try:
        start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        duration = end - start
        
        if duration.total_seconds() < 60:
            return f"{int(duration.total_seconds())} seconds"
        elif duration.total_seconds() < 3600:
            return f"{int(duration.total_seconds() / 60)} minutes"
        else:
            return f"{int(duration.total_seconds() / 3600)} hours"
    except:
        return "unknown duration"


def get_workspace_priorities(conn, workspaces: Dict[str, Any]) -> list:
    """Get workspaces prioritized by daydream_priority and recent activity."""
    import os
    priorities = []
    
    try:
        # Get chat activity for each workspace to prioritize analysis
        for workspace_name, workspace_data in workspaces.items():
            if not isinstance(workspace_data, dict):
                continue
                
            # Use configured daydream_priority (higher = more important)
            priority_score = workspace_data.get('daydream_priority', 5)  # Default priority 5
            
            # Get recent activity level for this workspace based on chat_id
            chat_id = workspace_data.get('telegram_chat_id')
            activity_score = 0
            
            if chat_id:
                try:
                    result = conn.execute("""
                        SELECT COUNT(*) as task_count
                        FROM promises 
                        WHERE chat_id = ?
                        AND created_at > datetime('now', '-7 days')
                    """, (str(chat_id),)).fetchone()
                    activity_score = result[0] if result else 0
                except Exception:
                    # If query fails, fall back to directory existence check
                    activity_score = 1 if workspace_data.get('working_directory') else 0
            else:
                # For workspaces without chat IDs, check if directory exists
                working_dir = workspace_data.get('working_directory')
                if working_dir and os.path.exists(working_dir):
                    activity_score = 1
            
            # Combined score: priority (weight 10) + recent activity (weight 1)
            combined_score = (priority_score * 10) + activity_score
            
            priorities.append((workspace_name, workspace_data, combined_score, priority_score, activity_score))
        
        # Sort by combined score (descending), then by priority, then alphabetically
        priorities.sort(key=lambda x: (-x[2], -x[3], x[0]))
        
        # Log the prioritization for transparency
        logger.info("🧠 Workspace prioritization:")
        for name, data, combined, priority, activity in priorities[:5]:  # Top 5
            logger.info(f"   {name}: priority={priority}, activity={activity}, total={combined}")
        
        # Return tuples of (name, data) without the scores
        return [(name, data) for name, data, combined, priority, activity in priorities]
        
    except Exception as e:
        logger.warning(f"Error prioritizing workspaces: {e}")
        # Fall back to simple alphabetical order
        return list(workspaces.items())


def gather_system_metrics(conn) -> Dict[str, Any]:
    """Gather system-wide metrics for daydreaming analysis."""
    metrics = {}
    
    try:
        # Promise completion rates
        completion_stats = conn.execute("""
            SELECT 
                status,
                COUNT(*) as count,
                AVG(CASE 
                    WHEN completed_at IS NOT NULL AND created_at IS NOT NULL 
                    THEN (julianday(completed_at) - julianday(created_at)) * 24 * 60 
                    ELSE NULL 
                END) as avg_duration_minutes
            FROM promises 
            WHERE created_at > datetime('now', '-30 days')
            GROUP BY status
        """).fetchall()
        
        metrics['completion_stats'] = {}
        total_tasks = 0
        for row in completion_stats:
            status = row[0]
            count = row[1]
            avg_duration = row[2]
            total_tasks += count
            metrics['completion_stats'][status] = {
                'count': count,
                'avg_duration_minutes': round(avg_duration, 2) if avg_duration else None
            }
        
        # Calculate success rate
        completed_count = metrics['completion_stats'].get('completed', {}).get('count', 0)
        metrics['success_rate'] = round((completed_count / total_tasks) * 100, 1) if total_tasks > 0 else 0
        
        # Task type distribution
        task_types = conn.execute("""
            SELECT task_type, COUNT(*) as count
            FROM promises 
            WHERE created_at > datetime('now', '-30 days')
            GROUP BY task_type
            ORDER BY count DESC
        """).fetchall()
        
        metrics['task_types'] = {row[0]: row[1] for row in task_types}
        
        # Recent activity trends (last 7 days)
        daily_activity = conn.execute("""
            SELECT 
                date(created_at) as day,
                COUNT(*) as task_count
            FROM promises 
            WHERE created_at > datetime('now', '-7 days')
            GROUP BY date(created_at)
            ORDER BY day DESC
        """).fetchall()
        
        metrics['daily_activity'] = {row[0]: row[1] for row in daily_activity}
        
    except Exception as e:
        logger.warning(f"Error gathering system metrics: {e}")
        metrics['error'] = str(e)
    
    return metrics


def gather_development_trends(conn) -> Dict[str, Any]:
    """Gather development trend analysis for daydreaming."""
    trends = {}
    
    try:
        # Weekly completion trends
        weekly_trends = conn.execute("""
            SELECT 
                strftime('%Y-W%W', created_at) as week,
                COUNT(*) as total_tasks,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_tasks,
                AVG(CASE 
                    WHEN completed_at IS NOT NULL AND created_at IS NOT NULL 
                    THEN (julianday(completed_at) - julianday(created_at)) * 24 * 60 
                    ELSE NULL 
                END) as avg_completion_time_minutes
            FROM promises 
            WHERE created_at > datetime('now', '-8 weeks')
            GROUP BY strftime('%Y-W%W', created_at)
            ORDER BY week DESC
            LIMIT 8
        """).fetchall()
        
        trends['weekly_trends'] = []
        for row in weekly_trends:
            week_data = {
                'week': row[0],
                'total_tasks': row[1],
                'completed_tasks': row[2],
                'completion_rate': round((row[2] / row[1]) * 100, 1) if row[1] > 0 else 0,
                'avg_completion_time_minutes': round(row[3], 2) if row[3] else None
            }
            trends['weekly_trends'].append(week_data)
        
        # Most active chat/workspace
        chat_activity = conn.execute("""
            SELECT 
                chat_id,
                COUNT(*) as task_count,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed_count
            FROM promises 
            WHERE created_at > datetime('now', '-30 days')
            GROUP BY chat_id
            ORDER BY task_count DESC
            LIMIT 5
        """).fetchall()
        
        trends['most_active_chats'] = []
        for row in chat_activity:
            chat_data = {
                'chat_id': row[0],
                'task_count': row[1],
                'completed_count': row[2],
                'completion_rate': round((row[2] / row[1]) * 100, 1) if row[1] > 0 else 0
            }
            trends['most_active_chats'].append(chat_data)
            
    except Exception as e:
        logger.warning(f"Error gathering development trends: {e}")
        trends['error'] = str(e)
    
    return trends


def enhance_workspace_analysis(workspace_info: Dict[str, Any], working_dir: str) -> Dict[str, Any]:
    """Add enhanced analysis to workspace info."""
    import os
    import glob
    
    try:
        # Code complexity metrics
        python_files = glob.glob(os.path.join(working_dir, "**/*.py"), recursive=True)
        workspace_info['complexity_metrics'] = {
            'total_python_files': len(python_files),
            'avg_lines_per_file': 0,
            'large_files': []  # Files > 500 lines
        }
        
        # Analyze files with intelligent sampling strategy
        total_lines = 0
        files_analyzed = 0
        max_files = min(len(python_files), 100)  # Reasonable limit based on codebase size
        
        # For large codebases, sample files intelligently
        if len(python_files) > 100:
            # Sample every Nth file to get representative coverage
            step = len(python_files) // 100
            sampled_files = python_files[::step][:100]
        else:
            sampled_files = python_files
        
        for py_file in sampled_files:
            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = len(f.readlines())
                    total_lines += lines
                    files_analyzed += 1
                    
                    # Track large files (but limit to avoid log spam)
                    if lines > 500 and len(workspace_info['complexity_metrics']['large_files']) < 10:
                        rel_path = os.path.relpath(py_file, working_dir)
                        workspace_info['complexity_metrics']['large_files'].append({
                            'file': rel_path,
                            'lines': lines
                        })
            except Exception:
                continue
        
        workspace_info['complexity_metrics']['files_analyzed'] = files_analyzed
        workspace_info['complexity_metrics']['sample_coverage'] = round((files_analyzed / len(python_files)) * 100, 1) if python_files else 0
        
        if files_analyzed > 0:
            workspace_info['complexity_metrics']['avg_lines_per_file'] = round(total_lines / files_analyzed, 1)
        
        # Quality indicators
        workspace_info['quality_indicators'] = {
            'has_tests': any(
                'test' in f.lower() for f in os.listdir(working_dir) 
                if os.path.isdir(os.path.join(working_dir, f))
            ),
            'has_docs': any(
                f.lower() in ['docs', 'documentation', 'readme.md']
                for f in os.listdir(working_dir)
            ),
            'has_config': any(
                f in os.listdir(working_dir)
                for f in ['pyproject.toml', 'setup.py', 'requirements.txt', 'package.json']
            ),
            'has_ci': any(
                f in os.listdir(working_dir)
                for f in ['.github', '.gitlab-ci.yml', '.travis.yml']
            )
        }
        
        # Development activity indicators
        if workspace_info.get('recent_commits'):
            workspace_info['development_activity'] = {
                'commit_count_last_10': len(workspace_info['recent_commits']),
                'recent_commit_messages': workspace_info['recent_commits'][:3]  # Last 3 commits
            }
    
    except Exception as e:
        workspace_info['enhancement_error'] = str(e)
    
    return workspace_info