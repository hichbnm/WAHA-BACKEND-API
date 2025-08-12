from celery_app import celery_app
from app.services.messaging import MessagingService
from app.db.database import async_session
from app.services.redis_lock import RedisLock
import asyncio
import os

@celery_app.task
def process_message_task(message_id: int):
    """Celery task to process a single message by its ID."""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(process_message_async(message_id))

async def process_message_async(message_id: int):
    from app.models.models import Message, Campaign
    from sqlalchemy import select, update
    from datetime import datetime
    # Per-message idempotency lock to avoid double processing
    redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    async with async_session() as session:
        # Early exit if message is already terminal or already being processed
        result = await session.execute(select(Message).where(Message.id == message_id))
        message = result.scalar_one_or_none()
        if not message:
            return
        if message.status in ('SENT', 'FAILED', 'CANCELLED'):
            return
        # Fetch related campaign to get sender_number and current status
        campaign = await session.get(Campaign, message.campaign_id)
        # If campaign got cancelled or completed, do not send; mark message CANCELLED if still pending
        if campaign and campaign.status not in ('PENDING', 'IN_PROGRESS'):
            if message.status in ('PENDING', 'IN_PROGRESS'):
                await session.execute(
                    update(Message)
                    .where(Message.id == message_id)
                    .values(status='CANCELLED', error='Campaign was cancelled or completed.')
                )
                await session.commit()
            return
        sender_number = campaign.sender_number if campaign else None
        # Acquire per-message lock; if not acquired, another worker is handling it
        try:
            async with RedisLock(redis_url, f"msg_lock:{message_id}", ttl=300):
                messaging_service = MessagingService(session)
                try:
                    send_result = await messaging_service.send_message(
                        sender_number=sender_number,
                        recipient=message.recipient,
                        message=campaign.template if campaign else None,
                        media_url=getattr(campaign, 'media_url', None)
                    )
                    # Re-check if this message got cancelled during processing; if so, don't overwrite
                    refreshed = await session.execute(select(Message).where(Message.id == message_id))
                    refreshed_msg = refreshed.scalar_one_or_none()
                    if refreshed_msg and refreshed_msg.status == 'CANCELLED':
                        return
                    # Success: mark message SENT and store WAHA id
                    waha_id = None
                    if isinstance(send_result, dict):
                        details = send_result.get('details') or {}
                        waha_id = details.get('waha_message_id')
                        if isinstance(waha_id, dict):
                            waha_id = waha_id.get('_serialized') or waha_id.get('id')
                    await session.execute(
                        update(Message)
                        .where(Message.id == message_id)
                        .values(
                            status='SENT',
                            sent_at=datetime.utcnow(),
                            delivered_at=datetime.utcnow(),
                            waha_message_id=waha_id
                        )
                    )
                    # Increment campaign.sent_messages and mark completed when done
                    if campaign:
                        campaign.sent_messages = (campaign.sent_messages or 0) + 1
                        # If all messages are processed, set campaign status
                        total_processed = (campaign.sent_messages or 0) + (campaign.failed_messages or 0)
                        if campaign.total_messages and total_processed >= campaign.total_messages:
                            campaign.status = 'COMPLETED' if (campaign.failed_messages or 0) == 0 else 'COMPLETED_WITH_ERRORS'
                            campaign.completed_at = datetime.utcnow()
                            # Delete local media file once campaign finishes
                            if getattr(campaign, 'media_url', None):
                                try:
                                    if os.path.isfile(campaign.media_url):
                                        os.remove(campaign.media_url)
                                except Exception:
                                    pass
                    if campaign:
                        session.add(campaign)
                    await session.commit()
                except Exception as e:
                    # Failure inside the lock: mark message FAILED so dispatcher can move on
                    refreshed = await session.execute(select(Message).where(Message.id == message_id))
                    refreshed_msg = refreshed.scalar_one_or_none()
                    if refreshed_msg and refreshed_msg.status == 'CANCELLED':
                        return
                    await session.execute(
                        update(Message)
                        .where(Message.id == message_id)
                        .values(
                            status='FAILED',
                            error=str(e)
                        )
                    )
                    if campaign:
                        campaign.failed_messages = (campaign.failed_messages or 0) + 1
                        total_processed = (campaign.sent_messages or 0) + (campaign.failed_messages or 0)
                        if campaign.total_messages and total_processed >= campaign.total_messages:
                            campaign.status = 'COMPLETED_WITH_ERRORS' if (campaign.failed_messages or 0) > 0 else 'COMPLETED'
                            campaign.completed_at = datetime.utcnow()
                            if getattr(campaign, 'media_url', None):
                                try:
                                    if os.path.isfile(campaign.media_url):
                                        os.remove(campaign.media_url)
                                except Exception:
                                    pass
                        session.add(campaign)
                    await session.commit()
        except RuntimeError:
            # Lock not acquired: another worker is processing this message
            return
