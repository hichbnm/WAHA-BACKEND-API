from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os
from dotenv import load_dotenv
import logging

load_dotenv()

# Get database configuration from environment
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./whatsapp_campaigns.db")

# Configure database URL based on database type
if DATABASE_URL.startswith("sqlite"):
    DATABASE_URL = DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///")
elif DATABASE_URL.startswith("postgresql"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

# Database engine configuration
engine_config = {
    "echo": True,
    "pool_pre_ping": True,
    "pool_size": 5,
    "max_overflow": 10
}

# SQLite-specific configuration
if DATABASE_URL.startswith("sqlite"):
    engine_config.pop("pool_size", None)
    engine_config.pop("max_overflow", None)
    engine_config["connect_args"] = {"check_same_thread": False}

try:
    engine = create_async_engine(DATABASE_URL, **engine_config)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    Base = declarative_base()
except Exception as e:
    logging.error(f"Failed to create database engine: {str(e)}")
    raise

async def init_db():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logging.info("Database initialized successfully")
    except Exception as e:
        logging.error(f"Failed to initialize database: {str(e)}")
        raise

async def get_db():
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            if (
                isinstance(e, Exception)
                and (
                    "No available WAHA worker found for new session" in str(e)
                    or "WhatsApp session not connected. Please scan QR code to authenticate." in str(e)
                )
            ):
                logging.info(f"Database session info: {str(e)}")
            else:
                logging.error(f"Database session error: {str(e)}")
            raise
        finally:
            await session.close()
