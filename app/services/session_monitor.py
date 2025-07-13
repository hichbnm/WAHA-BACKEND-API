from datetime import datetime, timedelta
import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.models import Session
from app.services.waha_session import WAHASessionService
import os 
from dotenv import load_dotenv
load_dotenv()

class SessionMonitor:
    def __init__(self, db_pool):
        # db_pool must be a SQLAlchemy engine or pool, NOT an AsyncSession instance
        self.db_pool = db_pool
        self.running = False
        self._task = None
        
    async def start(self):
        """Start the session monitoring background task"""
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._monitor_sessions())
            logging.info("Session monitor started")
    
    async def stop(self):
        """Stop the session monitoring background task"""
        if self.running:
            self.running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logging.info("Session monitor stopped")
    
    async def _monitor_sessions(self):
        """Monitor sessions and keep them alive, including auto-stop for expired sessions
        Always use a fresh AsyncSession for every DB/service operation to avoid greenlet_spawn errors.
        Only call keep_session_alive for sessions inactive for 12+ days.
        """
        from app.services.waha_session import WAHASessionService
        while self.running:
            try:
                from sqlalchemy import select
                import os
                session_lifetime = int(os.getenv("SESSION_LIFETIME_SECONDS", 0))
                now = datetime.utcnow()
                threshold = now - timedelta(days=12)
                # Get sessions to check
                async with AsyncSession(self.db_pool) as db:
                    query = select(Session).where(Session.status.in_(["CONNECTED", "WORKING"]))
                    result = await db.execute(query)
                    sessions = result.scalars().all()
                # For each session, use a new session/service for stop/keep-alive
                for session in sessions:
                    # Auto-stop if lifetime exceeded
                    if session_lifetime > 0 and (now - session.last_active).total_seconds() > session_lifetime:
                        logging.info(f"Auto-stopping session {session.phone_number} (lifetime exceeded)")
                        async with AsyncSession(self.db_pool) as stop_db:
                            stop_service = WAHASessionService(stop_db)
                            try:
                                await stop_service.stop_session(session.phone_number)
                            except Exception as e:
                                if "greenlet_spawn has not been called" in str(e):
                                    logging.info(f"Session {session.phone_number} already stopped or context closed, suppressing greenlet_spawn error.")
                                else:
                                    logging.error(f"Failed to auto-stop session {session.phone_number}: {e}")
                    # Only ping/keep-alive if session has not been active for 12+ days
                    elif session.last_active <= threshold:
                        async with AsyncSession(self.db_pool) as keep_db:
                            keep_service = WAHASessionService(keep_db)
                            try:
                                await keep_service.keep_session_alive(session.phone_number)
                            except Exception as e:
                                logging.error(f"Failed to keep session alive {session.phone_number}: {e}")
                # Find sessions that haven't been active in 12 days for status check
                async with AsyncSession(self.db_pool) as db:
                    query = select(Session).where(Session.last_active <= threshold)
                    result = await db.execute(query)
                    sessions = result.scalars().all()
                    for session in sessions:
                        try:
                            async with AsyncSession(self.db_pool) as session_db:
                                waha_service = WAHASessionService(session_db)
                                status = await waha_service.check_session_status(session.phone_number)
                                logging.info(f"Pinged session for {session.phone_number}: {status.get('status')}")
                                if status.get('status') != 'CONNECTED':
                                    session.status = 'REQUIRES_AUTH'
                                    session.data = {'message': 'Session expired, requires re-authentication'}
                                    await db.commit()
                                    logging.warning(f"Session {session.phone_number} requires re-authentication")
                        except Exception as e:
                            logging.error(f"Error monitoring session {session.phone_number}: {str(e)}")
                            continue
            except Exception as e:
                logging.error(f"Error in session monitor: {str(e)}")
            session_lifetime_seconds_check_interval = int(os.getenv("SESSION_LIFETIME_SECONDS_CHECK_INTERVAL", 30))
            await asyncio.sleep(session_lifetime_seconds_check_interval)