from fastapi import APIRouter, BackgroundTasks

router = APIRouter()


@router.get("/")
async def get_users():
    return [
        {"user1": "User One"},
    ]
