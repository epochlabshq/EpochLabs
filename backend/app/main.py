from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from app.core.config import settings
from app.api.endpoints import router as api_router
from app.api.ideas_endpoints import router as ideas_router
from app.api.launches_endpoints import router as launches_router
from app.api.websocket import router as ws_router
from app.api.twitter_endpoints import router as twitter_router
from app.api.epochs_endpoints import router as epochs_router
from app.api.trades_endpoints import router as trades_router
from app.api.desk_endpoints import router as desk_router
from app.api.goforge_endpoints import router as goforge_router

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    try:
        from app.services.ingest_worker import start_ingest_worker_loop
        asyncio.create_task(start_ingest_worker_loop())
    except Exception as e:
        print(f"[MAIN] Warning: Failed to start ingest worker loop: {e}")
    try:
        from app.services.model_worker import start_model_worker_loop
        asyncio.create_task(start_model_worker_loop())
    except Exception as e:
        print(f"[MAIN] Warning: Failed to start model worker loop: {e}")
    try:
        from app.services.epoch_watcher import start_epoch_watcher_loop
        asyncio.create_task(start_epoch_watcher_loop())
    except Exception as e:
        print(f"[MAIN] Warning: Failed to start epoch watcher loop: {e}")
    try:
        from app.services.desk_worker import start_desk_worker_loop
        asyncio.create_task(start_desk_worker_loop())
    except Exception as e:
        print(f"[MAIN] Warning: Failed to start desk worker loop: {e}")
    try:
        from app.services.goforge_worker import start_goforge_worker_loop
        asyncio.create_task(start_goforge_worker_loop())
    except Exception as e:
        print(f"[MAIN] Warning: Failed to start goforge worker loop: {e}")
    try:
        from app.services.twitter_service import start_twitter_scheduler_loop
        asyncio.create_task(start_twitter_scheduler_loop())
    except Exception as e:
        print(f"[MAIN] Warning: Failed to start twitter scheduler loop: {e}")
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Epoch Labs — Autonomous agent observing Robinhood Chain token survival",
    lifespan=lifespan
)

# CORS Middleware setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount local thumbnails static directory
try:
    os.makedirs(settings.STORAGE_LOCAL_PATH, exist_ok=True)
    app.mount("/thumbnails", StaticFiles(directory=settings.STORAGE_LOCAL_PATH), name="thumbnails")
except Exception as e:
    print(f"[MAIN] Warning: Could not mount /thumbnails static directory: {e}")

# Include routers
app.include_router(api_router)
app.include_router(ideas_router)
app.include_router(launches_router)
app.include_router(ws_router)
app.include_router(twitter_router)
app.include_router(epochs_router)
app.include_router(trades_router)
app.include_router(desk_router)
app.include_router(goforge_router)

@app.get("/")
async def root():
    return {
        "status": "online",
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs"
    }

@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.ENVIRONMENT}

if __name__ == "__main__":
    import uvicorn
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raw_port = os.getenv("PORT", "8000")
    try:
        port = int(raw_port)
    except ValueError:
        port = 8000
    print(f"[MAIN] Starting Uvicorn server on 0.0.0.0:{port}...")
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
