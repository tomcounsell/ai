"""Clean FastAPI server with separated integrations."""

import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from integrations.notion.scout import NotionScout
from integrations.telegram.client import TelegramClient

load_dotenv()

telegram_client = None
notion_scout = None


class AuthCode(BaseModel):
    code: str


class AuthPassword(BaseModel):
    password: str


async def start_telegram_client():
    """Initialize the Telegram client."""
    global telegram_client, notion_scout

    notion_key = os.getenv("NOTION_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")

    # Initialize Notion Scout if keys are available
    if notion_key and anthropic_key:
        notion_scout = NotionScout(notion_key, anthropic_key)
        print("Notion Scout initialized successfully")
    else:
        print("Notion Scout not initialized - missing API keys")

    # Initialize Telegram client
    telegram_client = TelegramClient()
    success = await telegram_client.initialize(notion_scout)

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
    uvicorn.run("main:app", host="0.0.0.0", port=9000, reload=True, log_level="info")
