"""Clean FastAPI server with separated integrations."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from integrations.notion.query_engine import get_notion_engine
from integrations.telegram.client import TelegramClient
from utilities.database import init_database

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

telegram_client = None
notion_engine = None


class AuthCode(BaseModel):
    code: str


class AuthPassword(BaseModel):
    password: str


async def start_telegram_client():
    """Initialize the Telegram client."""
    global telegram_client, notion_engine

    # Initialize database tables including promises
    logger.info("🗄️  Initializing database...")
    init_database()
    logger.info("✅ Database initialized successfully")

    # Initialize Notion query engine
    notion_engine = get_notion_engine()
    if notion_engine:
        logger.info("📚 Notion query engine initialized successfully")
    else:
        logger.warning("⚠️  Notion query engine not initialized - missing API keys")

    # Initialize Telegram client
    telegram_client = TelegramClient()
    success = await telegram_client.initialize(notion_engine)

    if success:
        logger.info("🤖 Telegram integration initialized successfully")
    else:
        logger.error("❌ Failed to initialize Telegram integration")
        logger.error("🛑 SERVER CANNOT FUNCTION WITHOUT TELEGRAM - SHUTTING DOWN")
        raise RuntimeError("Telegram integration failed - server cannot operate without it")


async def periodic_health_check():
    """Periodic health check to log server status"""
    while True:
        try:
            await asyncio.sleep(300)  # Every 5 minutes
            telegram_status = "connected" if telegram_client and telegram_client.is_connected else "disconnected"
            
            logger.info(f"💓 Server health check - Telegram: {telegram_status} | Time: {datetime.now().strftime('%H:%M:%S')}")
            
            # Log basic stats
            if telegram_client and telegram_client.is_connected:
                try:
                    # Get basic connection info
                    logger.info("🤖 Telegram client active and receiving messages")
                except Exception as e:
                    logger.warning(f"⚠️  Telegram client status check failed: {e}")
                    
        except asyncio.CancelledError:
            logger.info("🛑 Periodic health check stopped")
            break
        except Exception as e:
            logger.error(f"❌ Health check error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    # Startup
    logger.info("🚀 Starting FastAPI server with Telegram integration...")
    await start_telegram_client()
    
    # Start periodic health check
    health_task = asyncio.create_task(periodic_health_check())
    logger.info("💓 Periodic health monitoring started (every 5 minutes)")

    yield

    # Shutdown
    logger.info("🛑 Shutting down server...")
    global telegram_client
    
    # Cancel health check
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass

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


if __name__ == "__main__":
    # Disable reload to prevent session file changes from triggering restarts
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=False, log_level="info")
