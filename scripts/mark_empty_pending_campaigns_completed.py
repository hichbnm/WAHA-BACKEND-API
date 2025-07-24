import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select, update
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://whatsapp_user:mypassword@localhost:5432/whatsapp_campaigns")

async def mark_empty_pending_campaigns_completed():
    from app.models.models import Campaign, Message
    engine = create_async_engine(DATABASE_URL, echo=True)
    async with AsyncSession(engine) as session:
        # Find all PENDING campaigns with no PENDING messages
        result = await session.execute(select(Campaign.id).where(Campaign.status == "PENDING"))
        pending_campaigns = [row[0] for row in result.fetchall()]
        to_complete = []
        for campaign_id in pending_campaigns:
            msg_result = await session.execute(select(Message.id).where(Message.campaign_id == campaign_id, Message.status == "PENDING"))
            if not msg_result.first():
                to_complete.append(campaign_id)
        if to_complete:
            print(f"Marking campaigns as COMPLETED (no pending messages): {to_complete}")
            await session.execute(update(Campaign).where(Campaign.id.in_(to_complete)).values(status="COMPLETED", completed_at=None))
            await session.commit()
        else:
            print("No empty pending campaigns found.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(mark_empty_pending_campaigns_completed())
