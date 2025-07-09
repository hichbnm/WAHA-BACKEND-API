from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import models
from typing import Optional

class WorkerService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_available_worker(self) -> Optional[models.Worker]:
        # Find the worker with the most available capacity
        result = await self.db.execute(select(models.Worker))
        workers = result.scalars().all()
        best_worker = None
        max_available = -1
        for worker in workers:
            # Count active sessions for this worker
            session_count = await self.db.execute(
                select(func.count()).select_from(models.WAHASession).where(
                    models.WAHASession.worker_id == worker.id,
                    models.WAHASession.status.in_(["CONNECTED", "WORKING"])
                )
            )
            active_sessions = session_count.scalar() or 0
            available = worker.capacity - active_sessions
            if available > max_available:
                max_available = available
                best_worker = worker
        return best_worker
