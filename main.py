from fastapi import FastAPI
import uvicorn

app = FastAPI(title="AI Project API", version="1.0.0")

@app.get("/")
async def root():
    return {"message": "AI Project API is running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )