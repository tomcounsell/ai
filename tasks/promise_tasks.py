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
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from huey import crontab
from .huey_config import huey
from utilities.database import (
    get_promise, update_promise_status, get_database_connection, get_pending_promises
)
from utilities.missed_message_manager import scan_chat_for_missed_messages, process_missed_message_batch

logger = logging.getLogger(__name__)


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
    True AI-powered daydreaming using local Ollama.
    
    IMPLEMENTATION NOTE: This uses Ollama to perform creative analysis
    of the workspace, codebase patterns, and development insights.
    
    Features:
    - Review recent codebase changes and patterns
    - Analyze development velocity and trends  
    - Generate creative insights about architecture
    - Suggest proactive improvements and optimizations
    - Perform philosophical reflection on code quality
    - Think about future features and technical debt
    """
    logger.info("🧠 Starting AI-powered daydream and reflection...")
    
    try:
        # Only daydream when system is relatively idle
        health_data = gather_system_health_data()
        
        if health_data['pending_count'] > 5:
            logger.info("🧠 System too busy for daydreaming, skipping this cycle")
            return
        
        # Gather daydream context
        daydream_context = gather_daydream_context()
        
        # Use Ollama for creative reflection
        insights = ollama_daydream_analysis(daydream_context)
        
        # Log the AI insights
        log_daydream_insights(insights)
        
        logger.info("🧠 Daydream cycle complete - creative insights generated")
        
    except Exception as e:
        logger.error(f"🧠 Daydream failed: {str(e)}", exc_info=True)


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
        
        # Analyze each active workspace
        workspaces = workspace_config.get('workspaces', {})
        for workspace_name, workspace_data in workspaces.items():
            if isinstance(workspace_data, dict) and workspace_data.get('working_directory'):
                try:
                    workspace_context = analyze_workspace_for_daydream(
                        workspace_name, 
                        workspace_data['working_directory']
                    )
                    context['workspace_analysis'][workspace_name] = workspace_context
                except Exception as e:
                    logger.warning(f"Failed to analyze workspace {workspace_name}: {e}")
        
        # Get recent promise activity for trend analysis
        with get_database_connection() as conn:
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


def ollama_daydream_analysis(context: Dict[str, Any]) -> str:
    """Use Ollama to perform creative analysis and generate insights."""
    try:
        import requests
        import json
        
        # Prepare the daydream prompt
        prompt = build_daydream_prompt(context)
        
        # Call local Ollama
        response = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model': 'llama3.2',  # Use lightweight model for daydreaming
                'prompt': prompt,
                'stream': False,
                'options': {
                    'temperature': 0.8,  # More creative
                    'top_p': 0.9,
                    'max_tokens': 500
                }
            },
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get('response', 'No insights generated')
        else:
            return f"Ollama request failed: {response.status_code}"
            
    except Exception as e:
        return f"Daydream analysis failed: {str(e)}"


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
        
        total_lines = 0
        for py_file in python_files[:20]:  # Limit to first 20 files for performance
            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = len(f.readlines())
                    total_lines += lines
                    if lines > 500:
                        rel_path = os.path.relpath(py_file, working_dir)
                        workspace_info['complexity_metrics']['large_files'].append({
                            'file': rel_path,
                            'lines': lines
                        })
            except Exception:
                continue
        
        if python_files:
            workspace_info['complexity_metrics']['avg_lines_per_file'] = round(total_lines / min(len(python_files), 20), 1)
        
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