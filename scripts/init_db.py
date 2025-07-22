# Script to create all tables from SQLAlchemy models (for dev/empty DB use only)

import asyncio
from app.db.database import init_db
# Import all model classes to ensure all tables are registered
from app.models.models import Campaign, Message, Session, Worker, WAHASession, UserDelay
from app.models.message_queue import MessageQueue
from app.models.delays import DelayConfig

if __name__ == "__main__":
    asyncio.run(init_db())
