import asyncio
from sqlalchemy import select
from app.db.database import async_session
from app.models.models import Session
from app.services.waha_session import WAHASessionService
import logging

async def restore_sessions_on_startup():
    async with async_session() as db:
        # ORM-based query for sessions
        result = await db.execute(
            select(Session.phone_number, Session.status, Session.data)
            .where(Session.status.in_(["CONNECTED", "WORKING"]))
        )
        sessions = result.fetchall()
        for row in sessions:
            phone_number = row[0]
            try:
                waha_service = WAHASessionService(db)
                await waha_service.check_session_status(phone_number)
                logging.info(f"Restored session: {phone_number}")
            except Exception as e:
                logging.error(f"Failed to restore session {phone_number}: {str(e)}")

# To be called from FastAPI startup event
