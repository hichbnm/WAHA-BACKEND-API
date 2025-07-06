from datetime import datetime, timedelta
import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.models import Session
from app.services.waha_session import WAHASessionService

class SessionMonitor:
    def __init__(self, db_pool):
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
        """Monitor sessions and keep them alive"""
        while self.running:
            try:
                async with AsyncSession(self.db_pool) as db:
                    # Find sessions that haven't been active in 12 days
                    threshold = datetime.utcnow() - timedelta(days=12)
                    query = select(Session).where(Session.last_active <= threshold)
                    result = await db.execute(query)
                    sessions = result.scalars().all()
                    
                    for session in sessions:
                        try:
                            waha_service = WAHASessionService(db)
                            # Ping the session to keep it alive
                            status = await waha_service.check_session_status(session.phone_number)
                            logging.info(f"Pinged session for {session.phone_number}: {status.get('status')}")
                            
                            # If session is not connected, mark it for re-authentication
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
            
            # Sleep for 1 hour before next check
            await asyncio.sleep(3600)  # 1 hour
