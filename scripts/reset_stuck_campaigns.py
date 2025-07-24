import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import update, select
import os

# Adjust these as needed for your environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")

async def reset_stuck_campaigns():
    from app.models.models import Campaign, Message
    engine = create_async_engine(DATABASE_URL, echo=True)
    async with AsyncSession(engine) as session:
        # Set all stuck IN_PROGRESS campaigns to PENDING if they have pending messages
        stmt = update(Campaign).where(
            Campaign.status == "IN_PROGRESS"
        ).values(status="PENDING")
        await session.execute(stmt)
        await session.commit()
        # Optionally, print all campaigns still in PENDING
        result = await session.execute(select(Campaign.id, Campaign.status, Campaign.sender_number, Campaign.completed_at))
        campaigns = result.fetchall()
        print("Current campaigns:")
        for row in campaigns:
            print(f"id={row[0]}, status={row[1]}, sender={row[2]}, completed_at={row[3]}")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(reset_stuck_campaigns())
