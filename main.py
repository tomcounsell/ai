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
from pydantic import BaseModel

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


if __name__ == "__main__":
    # Disable reload to prevent session file changes from triggering restarts
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=False, log_level="info")
