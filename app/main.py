import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from app.core.config import settings
from app.core.database import AsyncSessionFactory, create_tables, get_db
import uuid
import app.models  # ensures models are registered
from app.api.auth import router as auth_router
from app.api.sync import router as sync_router
from app.api.analytics import router as analytics_router
from app.api.activities import router as activities_router
from app.api.assistant import router as assistant_router
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
app.include_router(analytics_router)
app.include_router(activities_router)
app.include_router(assistant_router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/report", include_in_schema=False)
async def report():
    return FileResponse("app/static/report.html")


@app.get("/chat", include_in_schema=False)
async def chat():
    return FileResponse("app/static/chat.html")


@app.post("/report/whatsapp")
async def send_whatsapp_now(
    user_id: uuid.UUID,
    week_offset: int = 0,
    db=Depends(get_db),
):
    from app.api.analytics import weekly_report as get_report
    from app.services.whatsapp import send_message, format_weekly_report
    data = await get_report(user_id=user_id, week_offset=week_offset, db=db)
    ok = await send_message(format_weekly_report(data))
    if not ok:
        return JSONResponse(status_code=500, content={"status": "failed", "detail": "Check Twilio WhatsApp config in .env"})
    return {"status": "sent"}


@app.get("/report/pdf")
async def report_pdf(
    user_id: uuid.UUID,
    week_offset: int = 0,
    db=Depends(get_db),
):
    from app.api.analytics import weekly_report as get_report
    from app.services.pdf_report import generate_weekly_pdf
    data = await get_report(user_id=user_id, week_offset=week_offset, db=db)
    pdf_bytes = generate_weekly_pdf(data)
    filename = f"fitness-week-{data['week_start']}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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
