import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select, update
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://whatsapp_user:mypassword@localhost:5432/whatsapp_campaigns")

async def cleanup_duplicate_campaigns():
    from app.models.models import Campaign
    engine = create_async_engine(DATABASE_URL, echo=True)
    async with AsyncSession(engine) as session:
        # Find all senders with more than one PENDING or IN_PROGRESS campaign
        result = await session.execute(select(Campaign.sender_number).where(Campaign.status.in_(["PENDING", "IN_PROGRESS"])))
        senders = [row[0] for row in result.fetchall()]
        from collections import Counter
        sender_counts = Counter(senders)
        duplicate_senders = [s for s, count in sender_counts.items() if count > 1]
        print(f"Senders with duplicate campaigns: {duplicate_senders}")
        to_update = []
        for sender in duplicate_senders:
            # Get all campaigns for this sender, oldest first
            result = await session.execute(
                select(Campaign.id, Campaign.created_at).where(
                    Campaign.sender_number == sender,
                    Campaign.status.in_(["PENDING", "IN_PROGRESS"])
                ).order_by(Campaign.created_at)
            )
            campaigns = result.fetchall()
            # Keep the oldest, mark the rest as COMPLETED_WITH_ERRORS
            for c in campaigns[1:]:
                to_update.append(c[0])
        if to_update:
            print(f"Marking duplicate campaigns as COMPLETED_WITH_ERRORS: {to_update}")
            await session.execute(update(Campaign).where(Campaign.id.in_(to_update)).values(status="COMPLETED_WITH_ERRORS"))
            await session.commit()
        else:
            print("No duplicate campaigns found.")

        # Mark all remaining PENDING campaigns as COMPLETED_WITH_ERRORS
        result = await session.execute(select(Campaign.id).where(Campaign.status == "PENDING"))
        pending_campaigns = [row[0] for row in result.fetchall()]
        if pending_campaigns:
            print(f"Marking all remaining PENDING campaigns as COMPLETED_WITH_ERRORS: {pending_campaigns}")
            await session.execute(update(Campaign).where(Campaign.id.in_(pending_campaigns)).values(status="COMPLETED_WITH_ERRORS"))
            await session.commit()
        else:
            print("No remaining PENDING campaigns found.")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(cleanup_duplicate_campaigns())
