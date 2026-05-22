import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.core.config import settings
from app.core.database import AsyncSessionFactory, create_tables
import app.models  # ensures models are registered
from app.api.auth import router as auth_router
from app.api.sync import router as sync_router
from app.tasks.scheduler import start_scheduler, stop_scheduler
 
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Fitness Sync API...")
    if settings.debug:
        await create_tables()
    start_scheduler()
    logger.info("API ready")
    yield
    logger.info("Shutting down...")
    stop_scheduler()
 
app = FastAPI(
    title="Fitness Sync API",
    version="1.0.0",
    lifespan=lifespan,
)
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(sync_router)

@app.get("/health")
async def health_check():
    db_ok = False
    try:
        async with AsyncSessionFactory() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error("DB health check failed: %s", e)
    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ok" if db_ok else "degraded",
            "database": "connected" if db_ok else "unavailable",
        },
    )
 
@app.get("/")
async def root():
    return {"message": "Fitness Sync API — visit /docs"}
