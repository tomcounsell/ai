"""Clean FastAPI server with separated integrations."""

import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from integrations.notion.query_engine import get_notion_engine
from integrations.telegram.client import TelegramClient

load_dotenv()

telegram_client = None
notion_engine = None


class AuthCode(BaseModel):
    code: str


class AuthPassword(BaseModel):
    password: str


async def start_telegram_client():
    """Initialize the Telegram client."""
    global telegram_client, notion_engine

    # Initialize Notion query engine
    notion_engine = get_notion_engine()
    if notion_engine:
        print("Notion query engine initialized successfully")
    else:
        print("Notion query engine not initialized - missing API keys")

    # Initialize Telegram client
    telegram_client = TelegramClient()
    success = await telegram_client.initialize(notion_engine)

    if success:
        print("Telegram integration initialized successfully")
    else:
        print("Failed to initialize Telegram integration")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    # Startup
    await start_telegram_client()

    yield

    # Shutdown
    global telegram_client

    if telegram_client:
        await telegram_client.stop()


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
