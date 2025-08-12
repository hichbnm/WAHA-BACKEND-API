from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.routers import messaging, admin, sessions, webhook, delays, worker
from app.services.session_monitor import SessionMonitor
from app.services.message_queue import message_queue
from app.db.database import engine, Base
from app.models.message_queue import MessageQueue
from app.models.delays import DelayConfig
from fastapi.staticfiles import StaticFiles
import logging
import os
from app.services.session_restore import restore_sessions_on_startup
import asyncio
import time
from datetime import datetime
from app.services.waha_session import WAHASessionService
from app.db.database import async_session
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services.dispatcher import RoundRobinDispatcher
from dotenv import load_dotenv

# Load environment variables from .env if present
# Use override=True so .env wins over any pre-exported shell vars for this app
load_dotenv(override=True)

# Configure logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "DEBUG"),
    filename=os.getenv("LOG_FILE", "./logs/whatsapp_backend.log"),
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
    # Reset all IN_PROGRESS campaigns and messages to PENDING on startup
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import update
    from sqlalchemy import func
    async with AsyncSession(engine) as db:
        from app.models.models import Campaign, Message
        # Normalize any legacy lowercase statuses to uppercase expected by dispatcher
        await db.execute(update(Message).where(func.lower(Message.status) == 'pending').values(status='PENDING'))
        await db.execute(update(Message).where(func.lower(Message.status) == 'in_progress').values(status='IN_PROGRESS'))
        await db.execute(update(Message).where(func.lower(Message.status) == 'sent').values(status='SENT'))
        await db.execute(update(Message).where(func.lower(Message.status) == 'failed').values(status='FAILED'))
        await db.execute(update(Message).where(func.lower(Message.status) == 'delivered').values(status='DELIVERED'))
        await db.execute(update(Message).where(func.lower(Message.status) == 'cancelled').values(status='CANCELLED'))
        await db.execute(update(Campaign).where(func.lower(Campaign.status) == 'pending').values(status='PENDING'))
        await db.execute(update(Campaign).where(func.lower(Campaign.status) == 'in_progress').values(status='IN_PROGRESS'))
        await db.execute(update(Campaign).where(func.lower(Campaign.status) == 'completed').values(status='COMPLETED'))
        await db.execute(update(Campaign).where(func.lower(Campaign.status) == 'completed_with_errors').values(status='COMPLETED_WITH_ERRORS'))
        await db.execute(update(Campaign).where(func.lower(Campaign.status) == 'cancelled').values(status='CANCELLED'))
        await db.execute(update(Campaign).where(Campaign.status == "IN_PROGRESS").values(status="PENDING"))
        await db.execute(update(Message).where(Message.status == "IN_PROGRESS").values(status="PENDING"))
        await db.commit()

        # Reconcile campaign counters and close campaigns that have no pending/in-progress messages
        from sqlalchemy import select, func, and_, exists
        # Update counters for each campaign from messages
        # total_messages
        total_counts = await db.execute(
            select(Message.campaign_id, func.count())
            .group_by(Message.campaign_id)
        )
        totals_map = {cid: cnt for cid, cnt in total_counts.all()}
        # sent_messages
        sent_counts = await db.execute(
            select(Message.campaign_id, func.count())
            .where(Message.status == 'SENT')
            .group_by(Message.campaign_id)
        )
        sent_map = {cid: cnt for cid, cnt in sent_counts.all()}
        # failed_messages
        failed_counts = await db.execute(
            select(Message.campaign_id, func.count())
            .where(Message.status == 'FAILED')
            .group_by(Message.campaign_id)
        )
        failed_map = {cid: cnt for cid, cnt in failed_counts.all()}

        # Load campaigns that are PENDING/IN_PROGRESS to potentially update
        c_rows = await db.execute(select(Campaign.id, Campaign.status, Campaign.total_messages, Campaign.sent_messages, Campaign.failed_messages))
        now = datetime.utcnow()
        for cid, c_status, c_total, c_sent, c_failed in c_rows.all():
            total = totals_map.get(cid, 0)
            sent = sent_map.get(cid, 0)
            failed = failed_map.get(cid, 0)
            # Sync counters if they differ
            updates = {}
            if c_total != total:
                updates['total_messages'] = total
            if (c_sent or 0) != sent:
                updates['sent_messages'] = sent
            if (c_failed or 0) != failed:
                updates['failed_messages'] = failed
            if updates:
                await db.execute(update(Campaign).where(Campaign.id == cid).values(**updates))

            # Check if campaign has any non-terminal messages
            has_active = await db.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.campaign_id == cid,
                    Message.status.in_(['PENDING', 'IN_PROGRESS'])
                )
            )
            if has_active.scalar() == 0 and total > 0:
                # If no active messages remain, mark completed accordingly
                new_status = 'COMPLETED_WITH_ERRORS' if failed > 0 else 'COMPLETED'
                await db.execute(
                    update(Campaign)
                    .where(Campaign.id == cid)
                    .values(status=new_status, completed_at=now)
                )
        await db.commit()
    # Optional: log pending items so we know what will resume
    try:
        from sqlalchemy import select, func
        async with async_session() as db:
            from app.models.models import Campaign, Message
            pending_campaigns = await db.execute(
                select(func.count()).select_from(Campaign).where(Campaign.status == "PENDING")
            )
            pending_messages = await db.execute(
                select(func.count()).select_from(Message).where(Message.status == "PENDING")
            )
            logging.info(f"[RESUME] Pending campaigns: {pending_campaigns.scalar() or 0}, pending messages: {pending_messages.scalar() or 0}")
    except Exception as e:
        logging.warning(f"[RESUME] Failed to count pending items: {e}")
    
    # Start session monitor
    session_monitor = SessionMonitor(engine)
    await session_monitor.start()
    
    # Do NOT start message queue processor here; only Celery should process campaigns
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
    # Start round-robin dispatcher (singleton via Redis lock)
    try:
        dispatcher = RoundRobinDispatcher()
        # Keep a reference on app state for observability/tests
        app.state.dispatcher = dispatcher
        # Prime the sender queue once on startup so pending campaigns resume quickly
        await dispatcher.refresh_senders()
        asyncio.create_task(dispatcher.dispatch())
        logging.info("RoundRobinDispatcher started")
    except Exception as e:
        logging.warning(f"Dispatcher not started: {e}")

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
