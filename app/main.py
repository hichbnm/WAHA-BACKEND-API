from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.routers import messaging, admin, sessions, webhook, delays, worker
from app.services.session_monitor import SessionMonitor
from app.services.message_queue import message_queue
from app.db.database import engine, Base
from app.config import settings
from fastapi.staticfiles import StaticFiles
import logging
import os
from app.services.session_restore import restore_sessions_on_startup
import asyncio
import time
from app.services.waha_session import WAHASessionService
from app.db.database import async_session
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    filename=settings.log_file,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create FastAPI app
app = FastAPI(
    title="WhatsApp Bulk Messaging API",
    description="API for sending bulk WhatsApp messages using WAHA",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5000"],  # Add more origins as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Include routers with proper prefixes
app.include_router(
    messaging.router,
    prefix="/api",
    tags=["messaging"]
)
app.include_router(
    admin.router,
    prefix="/api/admin",
    tags=["admin"]
)
app.include_router(
    sessions.router,
    prefix="/api/sessions",
    tags=["sessions"]
)
app.include_router(
    webhook.router,
    prefix="/api/webhook",  # Now webhook endpoints will be at /api/webhook
    tags=["webhook"]
)
app.include_router(
    delays.router,
    prefix="/api/admin",
    tags=["admin"]
)
app.include_router(
    worker.router,
    prefix="/api/admin",
    tags=["worker"]
)

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Create database tables and start background services
async def init_services():
    """Initialize database and start background services"""
    # Initialize database
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Start session monitor
    session_monitor = SessionMonitor(engine)
    await session_monitor.start()
    
    # Start message queue processor with 4 workers
    await message_queue.start_processing(engine, num_workers=4)
    
    logging.info("Database initialized and background services started")

scheduler = AsyncIOScheduler()

async def periodic_monitor_sessions_once():
    logging.info("[BG] periodic_monitor_sessions_once is running")
    async with async_session() as db:
        service = WAHASessionService(db)
        try:
            await service.monitor_sessions()
        except Exception as e:
            logging.error(f"Error in periodic monitor_sessions: {e}")

@app.on_event("startup")
async def startup_event():
    await init_services()
    await restore_sessions_on_startup()
    scheduler.add_job(
        periodic_monitor_sessions_once,
        'interval',
        seconds=15,
        id='monitor_sessions_job',
        replace_existing=True
    )
    scheduler.start()
    logging.info("Application started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    # Stop message queue processor
    await message_queue.stop_processing()
    
    # Stop session monitor
    session_monitor = SessionMonitor(engine)
    await session_monitor.stop()
    
    logging.info("Application shutting down, services stopped")

@app.get("/")
async def root():
    """Redirect root to the WhatsApp session manager"""
    return {"url": "/static/index.html"}
