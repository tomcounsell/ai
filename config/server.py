from fastapi import FastAPI
from mangum import Mangum

from apps.api import router as api_router
from config.database import shutdown_database, startup_database

# FastAPI Server
app = FastAPI()


@app.on_event("startup")
async def startup_event():
    print("app is starting up")
    await startup_database(app)


@app.on_event("shutdown")
async def shutdown_event():
    print("app is shutting down")
    shutdown_database(app)


@app.get("/")
async def root():
    return {"message": "Hello World"}


app.include_router(api_router, prefix="/api/v1")
handler = Mangum(app, lifespan="off")

# Run local server command
# uvicorn config.server:app --reload
