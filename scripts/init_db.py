# Script to create all tables from SQLAlchemy models (for dev/empty DB use only)
import asyncio
from app.db.database import init_db

if __name__ == "__main__":
    asyncio.run(init_db())
