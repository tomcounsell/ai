from fastapi import APIRouter

from apps.api.v1.endpoints import users

router = APIRouter()
router.include_router(users.router, prefix="/users", tags=["Users"])
