"""Clean FastAPI server with separated integrations."""

import asyncio
import logging
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import re
from pathlib import Path

# Revolutionary living project context - replaced query_engine
# from integrations.notion.query_engine import get_notion_engine
from integrations.telegram.client import TelegramClient
from utilities.database import init_database, get_pending_server_tasks, update_server_task_status
from utilities.monitoring.resource_monitor import resource_monitor, ResourceLimits
from utilities.auto_restart_manager import initialize_auto_restart
import json

load_dotenv()

# Configure consolidated logging to server.log
import logging.handlers
os.makedirs('logs', exist_ok=True)

# Create rotating file handler for all server logs (startup, shutdown, telegram handlers, health checks)
file_handler = logging.handlers.RotatingFileHandler(
    'logs/system.log', 
    maxBytes=10*1024*1024,  # 10MB
    backupCount=3
)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
))

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        file_handler,
        logging.StreamHandler()  # Still log to console
    ]
)
logger = logging.getLogger(__name__)

telegram_client = None
# Revolutionary living project context - replaced notion_engine
# notion_engine = None
_shutdown_requested = False
auto_restart_manager = None


class AuthCode(BaseModel):
    code: str


class AuthPassword(BaseModel):
    password: str


async def start_telegram_client():
    """Initialize the Telegram client."""
    global telegram_client, auto_restart_manager

    # Initialize database tables including promises
    logger.info("🗄️  Initializing database...")
    init_database()
    logger.info("✅ Database initialized successfully")
    
    # Initialize resource monitoring with production limits
    logger.info("🔧 Starting resource monitoring with emergency protection...")
    limits = ResourceLimits(
        max_memory_mb=400.0,  # Conservative limit for development
        emergency_memory_mb=600.0,  # Emergency cleanup trigger
        critical_memory_mb=800.0,  # Critical situation threshold
        restart_memory_threshold_mb=1000.0,  # Auto-restart threshold
        max_cpu_percent=85.0,
        emergency_cpu_percent=95.0,
        restart_after_hours=24.0  # Restart after 24 hours uptime
    )
    resource_monitor.limits = limits
    resource_monitor.start_monitoring(monitoring_interval=30.0)  # Check every 30 seconds
    
    # Initialize auto-restart manager
    auto_restart_manager = initialize_auto_restart(resource_monitor)
    logger.info("✅ Resource monitoring and auto-restart protection enabled")

    # Revolutionary living project context - no need for separate notion_engine
    logger.info("🚀 Living project context system available via MCP tools")

    # Initialize Telegram client
    telegram_client = TelegramClient()
    success = await telegram_client.initialize(None)  # No longer needs notion_engine

    if success:
        logger.info("🤖 Telegram integration initialized successfully")
        
        # Schedule startup promise recovery after successful Telegram initialization
        try:
            from tasks.promise_tasks import startup_promise_recovery
            logger.info("🔄 Scheduling startup promise recovery...")
            startup_promise_recovery.schedule(delay=5)  # Give Huey a moment to fully start
            logger.info("✅ Startup promise recovery scheduled")
        except Exception as recovery_error:
            logger.warning(f"⚠️  Could not schedule startup promise recovery: {recovery_error}")
    else:
        logger.error("❌ Failed to initialize Telegram integration")
        logger.error("🛑 SERVER CANNOT FUNCTION WITHOUT TELEGRAM - SHUTTING DOWN")
        raise RuntimeError("Telegram integration failed - server cannot operate without it")


async def process_pending_server_tasks():
    """Process pending server tasks from the database queue."""
    try:
        tasks = get_pending_server_tasks(limit=5)  # Process up to 5 tasks per cycle
        
        for task in tasks:
            task_id = task['id']
            task_type = task['task_type']
            task_data = json.loads(task['task_data']) if task['task_data'] else {}
            
            logger.debug(f"Processing server task {task_id}: {task_type}")
            
            # Mark task as processing
            update_server_task_status(task_id, 'processing')
            
            try:
                # Handle different task types
                if task_type == 'scan_missed_messages':
                    await handle_scan_missed_messages_task(task_data)
                elif task_type == 'send_message':
                    await handle_send_message_task(task_data)
                elif task_type == 'cleanup':
                    await handle_cleanup_task(task_data)
                else:
                    logger.warning(f"Unknown task type: {task_type}")
                    update_server_task_status(task_id, 'failed', f"Unknown task type: {task_type}")
                    continue
                
                # Mark task as completed
                update_server_task_status(task_id, 'completed')
                logger.debug(f"Completed server task {task_id}")
                
            except Exception as task_error:
                error_msg = str(task_error)
                logger.error(f"Failed to process task {task_id}: {error_msg}")
                update_server_task_status(task_id, 'failed', error_msg)
                
    except Exception as e:
        logger.error(f"Error processing server tasks: {e}")


async def handle_scan_missed_messages_task(task_data: dict):
    """Handle scan_missed_messages task type."""
    chat_id = task_data.get('chat_id')
    
    if not telegram_client or not telegram_client.is_connected:
        raise RuntimeError("Telegram client not available for missed message scan")
    
    # Use the Telegram client's missed message integration
    if hasattr(telegram_client, 'missed_message_integration') and telegram_client.missed_message_integration:
        await telegram_client.missed_message_integration.process_missed_for_chat(chat_id)
    else:
        logger.warning("Missed message integration not available")


async def handle_send_message_task(task_data: dict):
    """Handle send_message task type."""
    chat_id = task_data.get('chat_id')
    message_text = task_data.get('message_text')
    
    if not chat_id or not message_text:
        raise ValueError("send_message task requires chat_id and message_text")
    
    if not telegram_client or not telegram_client.is_connected:
        raise RuntimeError("Telegram client not available for sending message")
    
    # Send message through Telegram client
    await telegram_client.client.send_message(chat_id, message_text)


async def handle_cleanup_task(task_data: dict):
    """Handle cleanup task type."""
    cleanup_type = task_data.get('type', 'general')
    
    if cleanup_type == 'old_tasks':
        # Clean up old completed/failed tasks
        from utilities.database import get_database_connection
        
        with get_database_connection() as conn:
            # Delete tasks older than 7 days
            conn.execute("""
                DELETE FROM server_tasks 
                WHERE status IN ('completed', 'failed') 
                AND processed_at < datetime('now', '-7 days')
            """)
            conn.commit()
    
    logger.info(f"Completed cleanup task: {cleanup_type}")


async def periodic_health_check():
    """Periodic health check to log server status and process database tasks"""
    global _shutdown_requested
    
    while True:
        try:
            await asyncio.sleep(30)  # Check every 30 seconds for tasks, health every 5 minutes
            telegram_status = "connected" if telegram_client and telegram_client.is_connected else "disconnected"
            
            # Check for deferred shutdown requests
            if _shutdown_requested:
                if telegram_client and telegram_client._active_handlers:
                    active_count = len(telegram_client._active_handlers)
                    logger.warning(f"⏳ Shutdown deferred - {active_count} message handlers still active")
                else:
                    logger.info("✅ All message handlers completed - proceeding with deferred shutdown")
                    # Exit gracefully - this will trigger the lifespan shutdown
                    break
            
            # Process pending server tasks
            await process_pending_server_tasks()
            
            # Health check every 5 minutes (10 cycles of 30 seconds)
            if hasattr(periodic_health_check, 'cycle_count'):
                periodic_health_check.cycle_count += 1
            else:
                periodic_health_check.cycle_count = 1
            
            if periodic_health_check.cycle_count % 10 == 0:  # Every 10 cycles = 5 minutes
                logger.info(f"💓 Server health check - Telegram: {telegram_status} | Time: {datetime.now().strftime('%H:%M:%S')}")
                
                # Log basic stats
                if telegram_client and telegram_client.is_connected:
                    try:
                        # Get basic connection info
                        active_handlers = len(telegram_client._active_handlers) if telegram_client._active_handlers else 0
                        logger.info(f"🤖 Telegram client active and receiving messages (active handlers: {active_handlers})")
                    except Exception as e:
                        logger.warning(f"⚠️  Telegram client status check failed: {e}")
                    
        except asyncio.CancelledError:
            logger.info("🛑 Periodic health check stopped")
            break
        except Exception as e:
            logger.error(f"❌ Health check error: {e}")


def handle_shutdown_signal(signum, frame):
    """Handle shutdown signals gracefully during message processing"""
    global _shutdown_requested
    
    if telegram_client and telegram_client._active_handlers:
        active_count = len(telegram_client._active_handlers)
        logger.warning(f"🛑 Shutdown signal received ({signal.Signals(signum).name}) but {active_count} message handlers are active")
        logger.warning("⏳ Deferring shutdown to prevent message processing corruption...")
        _shutdown_requested = True
        return
    
    logger.info(f"🛑 Shutdown signal received ({signal.Signals(signum).name}) - no active handlers, proceeding...")
    _shutdown_requested = True


async def periodic_database_maintenance_task():
    """Periodically perform database maintenance to prevent locks"""
    await asyncio.sleep(60)  # Wait 1 minute before first maintenance
    
    while True:
        try:
            from utilities.database import periodic_database_maintenance
            periodic_database_maintenance()
            logger.debug("🗄️ Database maintenance completed")
        except Exception as e:
            logger.warning(f"Database maintenance failed: {e}")
        
        await asyncio.sleep(600)  # Every 10 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events with graceful message processing protection"""
    # Startup
    logger.info("🚀 Starting FastAPI server with Telegram integration...")
    
    # Install signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    logger.info("🛡️  Signal handlers installed for graceful shutdown protection")
    
    await start_telegram_client()
    
    # Start periodic health check
    health_task = asyncio.create_task(periodic_health_check())
    logger.info("💓 Periodic health monitoring started (every 5 minutes)")
    
    # Start periodic database maintenance
    database_maintenance_task = asyncio.create_task(periodic_database_maintenance_task())
    logger.info("🗄️ Periodic database maintenance started (every 10 minutes)")

    yield

    # Shutdown with message processing protection
    logger.info("🛑 Shutting down server...")
    global telegram_client
    
    # Wait for any active message processing to complete before shutdown
    if telegram_client and hasattr(telegram_client, '_active_handlers'):
        active_count = len(telegram_client._active_handlers) if telegram_client._active_handlers else 0
        if active_count > 0:
            logger.info(f"⏳ Waiting for {active_count} active message handlers to complete...")
            max_wait = 10  # Maximum 10 seconds wait
            wait_count = 0
            while len(telegram_client._active_handlers) > 0 and wait_count < max_wait:
                await asyncio.sleep(1)
                wait_count += 1
                remaining = len(telegram_client._active_handlers)
                logger.info(f"⏳ Still waiting... {remaining} handlers remaining ({max_wait - wait_count}s)")
            
            if len(telegram_client._active_handlers) > 0:
                logger.warning(f"⚠️  Proceeding with shutdown despite {len(telegram_client._active_handlers)} active handlers")
            else:
                logger.info("✅ All message handlers completed gracefully")
    
    # Cancel health check
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    logger.info("🛑 Periodic health check stopped")

    if telegram_client:
        logger.info("🤖 Stopping Telegram client...")
        await telegram_client.stop()
        logger.info("✅ Telegram client stopped")


app = FastAPI(title="AI Project API", version="1.0.0", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "AI Project API is running"}


@app.get("/health")
async def health_check():
    telegram_status = (
        "connected" if telegram_client and telegram_client.is_connected else "disconnected"
    )
    return {"status": "healthy", "telegram": telegram_status}


@app.get("/telegram/status")
async def telegram_status():
    """Get Telegram client status"""
    if telegram_client:
        return {
            "telegram": "connected" if telegram_client.is_connected else "disconnected",
            "client_id": telegram_client.session_name,
        }
    return {"telegram": "disconnected"}


@app.post("/telegram/initialize")
async def initialize_telegram():
    """Manually initialize Telegram client"""
    try:
        await start_telegram_client()
        return {"status": "initialization_started"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to initialize: {e}")


@app.get("/resources/status")
async def resource_status():
    """Get current resource usage and health status"""
    try:
        health = resource_monitor.get_system_health()
        emergency = resource_monitor.get_emergency_status()
        return {
            "health": health,
            "emergency": emergency,
            "monitoring_active": resource_monitor.monitoring_active
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get resource status: {e}")


@app.get("/resources/sessions")
async def session_report():
    """Get detailed session management report"""
    try:
        return resource_monitor.get_session_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get session report: {e}")


@app.get("/restart/status")
async def restart_status():
    """Get auto-restart status and history"""
    try:
        if auto_restart_manager:
            return auto_restart_manager.get_restart_status()
        else:
            return {"error": "Auto-restart manager not initialized"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get restart status: {e}")


@app.post("/restart/force")
async def force_restart():
    """Force an immediate server restart"""
    try:
        if auto_restart_manager:
            auto_restart_manager.force_restart("api_request")
            return {"status": "restart_initiated", "message": "Server restart has been initiated"}
        else:
            raise HTTPException(status_code=503, detail="Auto-restart manager not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to force restart: {e}")


@app.post("/restart/cancel")
async def cancel_restart():
    """Cancel a scheduled restart"""
    try:
        if auto_restart_manager:
            auto_restart_manager.cancel_scheduled_restart("api_request")
            return {"status": "restart_cancelled", "message": "Scheduled restart has been cancelled"}
        else:
            raise HTTPException(status_code=503, detail="Auto-restart manager not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel restart: {e}")


class TrueE2ETestRequest(BaseModel):
    """Request model for TRUE E2E test."""
    test_message: str = "🧪 TRUE E2E API Test: Validate complete message processing pipeline"
    wait_seconds: int = 3


@app.post("/test/true-e2e")
async def run_true_e2e_test(request: TrueE2ETestRequest):
    """
    Run TRUE E2E test using existing TelegramClient.
    
    Sends a real message through the existing Telegram connection,
    waits for processing, and validates the response.
    """
    global telegram_client
    
    if not telegram_client or not telegram_client.client:
        raise HTTPException(status_code=503, detail="Telegram client not available")
    
    if not telegram_client.is_connected:
        raise HTTPException(status_code=503, detail="Telegram client not connected")
    
    try:
        # Get bot user info
        me = await telegram_client.client.get_me()
        test_start_time = datetime.now()
        
        logger.info(f"🧪 Starting TRUE E2E test as @{me.username} (ID: {me.id})")
        
        # Step 1: Send real message to self
        test_message = f"{request.test_message} (Started: {test_start_time.strftime('%H:%M:%S')})"
        sent_message = await telegram_client.client.send_message("me", test_message)
        
        logger.info(f"📤 Sent TRUE E2E test message with ID: {sent_message.id}")
        
        # Step 2: Wait for processing
        await asyncio.sleep(request.wait_seconds)
        
        # Step 3: Check chat history for processing
        chat_history = telegram_client.chat_history
        test_message_found = False
        response_found = False
        latest_response = None
        
        if me.id in chat_history.chat_histories:
            recent_messages = chat_history.chat_histories[me.id][-10:]  # Last 10 messages
            
            for msg in recent_messages:
                content = msg.get("content", "")
                
                # Check if our test message was recorded
                if test_message in content:
                    test_message_found = True
                    logger.info(f"✅ Test message found in chat history")
                
                # Check for recent agent response
                elif (msg.get("role") == "assistant" and 
                      len(content) > 20):
                    response_found = True
                    latest_response = content[:200] + ("..." if len(content) > 200 else "")
                    logger.info(f"✅ Agent response found: {latest_response}")
        
        # Step 4: Check recent Telegram messages
        telegram_response_found = False
        telegram_response = None
        
        try:
            async for message in telegram_client.client.get_chat_history("me", limit=5):
                # Look for bot responses (messages from our bot user)
                if (message.from_user and 
                    message.from_user.id == me.id and 
                    message.text and 
                    message.text != test_message):
                    telegram_response_found = True
                    telegram_response = message.text[:200] + ("..." if len(message.text) > 200 else "")
                    logger.info(f"✅ Telegram response found: {telegram_response}")
                    break
        except Exception as e:
            logger.warning(f"Could not check Telegram history: {e}")
        
        # Step 5: Build results
        test_duration = (datetime.now() - test_start_time).total_seconds()
        
        result = {
            "status": "completed",
            "test_duration_seconds": test_duration,
            "bot_user": f"@{me.username}",
            "bot_id": me.id,
            "sent_message_id": sent_message.id,
            "message_processing": {
                "test_message_found": test_message_found,
                "agent_response_found": response_found,
                "latest_agent_response": latest_response
            },
            "telegram_validation": {
                "telegram_response_found": telegram_response_found,
                "telegram_response": telegram_response
            },
            "overall_success": test_message_found and (response_found or telegram_response_found),
            "timestamp": test_start_time.isoformat()
        }
        
        if result["overall_success"]:
            logger.info("🎉 TRUE E2E test PASSED - Complete pipeline working!")
        else:
            logger.warning("⚠️ TRUE E2E test PARTIAL - Some components may need attention")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ TRUE E2E test failed: {e}")
        raise HTTPException(status_code=500, detail=f"TRUE E2E test error: {str(e)}")


def get_daydream_insights():
    """Get all daydream insight files sorted by most recent first."""
    logs_dir = Path("logs")
    
    if not logs_dir.exists():
        return []
    
    # Find all daydream insight files
    daydream_files = list(logs_dir.glob("daydream_insights_*.md"))
    
    # Sort by modification time (most recent first)
    daydream_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    
    insights = []
    for file_path in daydream_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Parse the daydream file
            session_id = ""
            generated_time = ""
            duration = ""
            workspaces = ""
            insights_content = ""
            
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if line.startswith("# Daydream Insights - Session"):
                    session_id = line.split("Session ")[-1] if "Session " in line else "Unknown"
                elif line.startswith("**Generated:**"):
                    generated_time = line.replace("**Generated:**", "").strip()
                elif line.startswith("**Analysis Duration:**"):
                    duration = line.replace("**Analysis Duration:**", "").strip()
                elif line.startswith("**Workspaces Analyzed:**"):
                    workspaces = line.replace("**Workspaces Analyzed:**", "").strip()
                elif line.startswith("---") and i > 5:  # Content starts after the metadata section
                    insights_content = '\n'.join(lines[i+2:]).strip()
                    break
            
            # Determine status based on content
            status = "completed"
            if "timed out" in insights_content.lower() or "timeout" in insights_content.lower():
                status = "timed_out"
            elif not insights_content or len(insights_content) < 50:
                status = "failed"
            
            insights.append({
                "session_id": session_id,
                "filename": file_path.name,
                "generated_time": generated_time,
                "duration": duration,
                "workspaces_analyzed": workspaces,
                "status": status,
                "content": insights_content,
                "file_size": file_path.stat().st_size,
                "last_modified": file_path.stat().st_mtime
            })
        except Exception as e:
            logger.error(f"Error reading daydream file {file_path}: {e}")
            continue
    
    return insights


@app.get("/daydreams", response_class=HTMLResponse)
async def get_daydreams():
    """Get all daydream insights in a beautiful, human-readable HTML format."""
    try:
        insights = get_daydream_insights()
        
        if not insights:
            html_content = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>🧠 Valor's Daydreams</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                           margin: 40px; background: #f8f9fa; color: #2c3e50; }
                    .container { max-width: 900px; margin: 0 auto; background: white; 
                                border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); padding: 40px; }
                    h1 { color: #e74c3c; text-align: center; margin-bottom: 20px; }
                    .empty { text-align: center; color: #7f8c8d; font-style: italic; margin: 60px 0; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🧠 Valor's Daydreams</h1>
                    <div class="empty">
                        <p>No daydream insights found yet...</p>
                        <p>My daydream system runs every 6 hours to generate architectural insights.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            return html_content
        
        # Generate HTML for insights
        insights_html = ""
        for insight in insights:
            status_color = {
                "completed": "#27ae60",
                "timed_out": "#f39c12", 
                "failed": "#e74c3c"
            }.get(insight["status"], "#7f8c8d")
            
            status_emoji = {
                "completed": "✅",
                "timed_out": "⏱️",
                "failed": "❌"
            }.get(insight["status"], "❓")
            
            # Format the content for HTML display
            content_html = insight["content"].replace('\n', '<br>') if insight["content"] else "<em>No insights generated</em>"
            
            # Format timestamp nicely
            try:
                if insight["generated_time"]:
                    # Parse ISO timestamp and format it nicely
                    from datetime import datetime
                    timestamp = datetime.fromisoformat(insight["generated_time"].replace('Z', '+00:00'))
                    formatted_time = timestamp.strftime("%B %d, %Y at %I:%M %p UTC")
                else:
                    formatted_time = "Unknown time"
            except:
                formatted_time = insight["generated_time"] or "Unknown time"
            
            insights_html += f"""
            <div class="insight-card">
                <div class="insight-header">
                    <h3>{status_emoji} Session {insight["session_id"][:8]}...</h3>
                    <div class="metadata">
                        <span class="status" style="color: {status_color}">● {insight["status"].title()}</span>
                        <span class="time">{formatted_time}</span>
                    </div>
                </div>
                <div class="insight-stats">
                    <span><strong>Duration:</strong> {insight["duration"]}</span>
                    <span><strong>Workspaces:</strong> {insight["workspaces_analyzed"]}</span>
                    <span><strong>File:</strong> {insight["filename"]}</span>
                </div>
                <div class="insight-content">
                    {content_html}
                </div>
            </div>
            """
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>🧠 Valor's Daydreams</title>
            <meta charset="UTF-8">
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    color: #2c3e50; min-height: 100vh; padding: 20px; box-sizing: border-box;
                }}
                .container {{ 
                    max-width: 1000px; margin: 0 auto; 
                }}
                .header {{
                    background: rgba(255,255,255,0.95); border-radius: 16px; 
                    box-shadow: 0 8px 32px rgba(0,0,0,0.1); padding: 30px; 
                    margin-bottom: 30px; text-align: center; backdrop-filter: blur(10px);
                }}
                h1 {{ 
                    color: #2c3e50; margin: 0; font-size: 2.5em; font-weight: 700;
                    background: linear-gradient(45deg, #667eea, #764ba2);
                    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
                }}
                .subtitle {{ 
                    color: #7f8c8d; margin-top: 10px; font-size: 1.1em; 
                }}
                .insight-card {{ 
                    background: rgba(255,255,255,0.95); border-radius: 16px; 
                    box-shadow: 0 8px 32px rgba(0,0,0,0.1); padding: 25px; 
                    margin-bottom: 25px; backdrop-filter: blur(10px);
                    transition: transform 0.2s ease, box-shadow 0.2s ease;
                }}
                .insight-card:hover {{
                    transform: translateY(-2px); 
                    box-shadow: 0 12px 40px rgba(0,0,0,0.15);
                }}
                .insight-header {{ 
                    display: flex; justify-content: space-between; align-items: center; 
                    margin-bottom: 15px; flex-wrap: wrap;
                }}
                .insight-header h3 {{ 
                    margin: 0; color: #2c3e50; font-size: 1.3em;
                }}
                .metadata {{ 
                    display: flex; gap: 20px; align-items: center; flex-wrap: wrap;
                }}
                .status {{ 
                    font-weight: 600; font-size: 0.9em;
                }}
                .time {{ 
                    color: #7f8c8d; font-size: 0.9em;
                }}
                .insight-stats {{ 
                    display: flex; gap: 20px; margin-bottom: 20px; 
                    flex-wrap: wrap; font-size: 0.9em; color: #5a6c7d;
                }}
                .insight-stats span {{
                    background: #f8f9fa; padding: 5px 12px; border-radius: 20px;
                }}
                .insight-content {{ 
                    line-height: 1.6; color: #34495e; 
                    border-left: 4px solid #3498db; padding-left: 20px;
                    background: #f8f9fa; padding: 20px; border-radius: 8px;
                }}
                .footer {{
                    text-align: center; color: rgba(255,255,255,0.8); 
                    margin-top: 40px; font-size: 0.9em;
                }}
                @media (max-width: 768px) {{
                    .insight-header {{ flex-direction: column; align-items: flex-start; }}
                    .metadata {{ margin-top: 10px; }}
                    .insight-stats {{ flex-direction: column; gap: 10px; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🧠 Valor's Daydreams</h1>
                    <p class="subtitle">AI-powered architectural insights and codebase reflections • Updates every 6 hours</p>
                </div>
                {insights_html}
                <div class="footer">
                    <p>🤖 Generated by Valor's unified daydream system • {len(insights)} sessions captured</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_content
        
    except Exception as e:
        logger.error(f"Error generating daydreams page: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate daydreams: {str(e)}")


if __name__ == "__main__":
    # Disable reload to prevent session file changes from triggering restarts
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=False, log_level="info")
