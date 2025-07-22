import asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.models.message_queue import MessageQueue, MessageStatus
from app.services.messaging import MessagingService
from app.models.models import Session
import logging
import os

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def process_pending_messages():
    MAX_RETRIES = 3
    while True:
        async with AsyncSessionLocal() as db:
            # Use row-level locking to avoid double-processing
            result = await db.execute(
                MessageQueue.__table__
                .select()
                .where(MessageQueue.status == MessageStatus.PENDING)
                .order_by(MessageQueue.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            row = result.first()
            if not row:
                await asyncio.sleep(5)
                continue
            msg = row[0]
            try:
                # Render template with variables if present
                message_text = msg.message
                if msg.variables and isinstance(msg.variables, dict):
                    try:
                        message_text = message_text.format(**msg.variables)
                    except Exception as e:
                        logging.error(f"Template rendering failed for message {msg.id}: {e}")
                service = MessagingService(db)
                await service.send_message(msg.sender_number, msg.recipient, message_text, msg.media_url)
                msg.status = MessageStatus.SENT
                msg.error = None
                # Update campaign/message counters if needed
                from app.models.models import Campaign, Message
                campaign = None
                message = None
                # Try to find related campaign/message
                campaign_result = await db.execute(
                    Campaign.__table__.select().where(Campaign.sender_number == msg.sender_number)
                )
                campaign = campaign_result.first()
                if campaign:
                    campaign = campaign[0]
                    campaign.sent_messages = (campaign.sent_messages or 0) + 1
                    if campaign.sent_messages + (campaign.failed_messages or 0) >= (campaign.total_messages or 0):
                        campaign.status = "COMPLETED"
                # Optionally update related Message row if exists
                message_result = await db.execute(
                    Message.__table__.select().where(
                        Message.recipient == msg.recipient,
                        Message.status == "PENDING"
                    )
                )
                message = message_result.first()
                if message:
                    message = message[0]
                    message.status = "SENT"
            except Exception as e:
                msg.retry_count = (msg.retry_count or 0) + 1
                msg.error = str(e)
                if msg.retry_count >= MAX_RETRIES:
                    msg.status = MessageStatus.FAILED
                    # Update campaign/message counters if needed
                    from app.models.models import Campaign, Message
                    campaign_result = await db.execute(
                        Campaign.__table__.select().where(Campaign.sender_number == msg.sender_number)
                    )
                    campaign = campaign_result.first()
                    if campaign:
                        campaign = campaign[0]
                        campaign.failed_messages = (campaign.failed_messages or 0) + 1
                        if campaign.sent_messages + (campaign.failed_messages or 0) >= (campaign.total_messages or 0):
                            campaign.status = "COMPLETED"
                    message_result = await db.execute(
                        Message.__table__.select().where(
                            Message.recipient == msg.recipient,
                            Message.status == "PENDING"
                        )
                    )
                    message = message_result.first()
                    if message:
                        message = message[0]
                        message.status = "FAILED"
                logging.error(f"Failed to send queued message {msg.id}: {e}")
            await db.commit()
        await asyncio.sleep(1)  # Throttle worker

if __name__ == "__main__":
    asyncio.run(process_pending_messages())
