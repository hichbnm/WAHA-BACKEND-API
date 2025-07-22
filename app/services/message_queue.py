import asyncio
from dotenv import load_dotenv
load_dotenv()
from collections import deque
from datetime import datetime, timedelta
import logging
from typing import Dict, Optional, Set
import os
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Campaign, Message
from app.services.messaging import MessagingService
from app.models.models import UserDelay
from app.db.database import async_session
from sqlalchemy.future import select

# Utility for phone number normalization (import from delays or define here)
def normalize_number(number: str) -> str:
    return number.lstrip('+').strip() if number else number

class MessageQueue:
    async def process_campaign_by_id(self, db_pool, campaign_id):
        """Process a single campaign by its ID (for Celery task)."""
        from app.models.models import Campaign, Message
        from app.services.messaging import MessagingService
        from sqlalchemy.ext.asyncio import AsyncSession
        from sqlalchemy import select, update
        import logging
        from datetime import datetime
        try:
            async with AsyncSession(db_pool, expire_on_commit=False) as db:
                # Fetch the campaign
                query = select(Campaign).where(Campaign.id == campaign_id)
                result = await db.execute(query)
                campaign = result.scalar_one_or_none()
                if not campaign:
                    logging.error(f"[CELERY] Campaign {campaign_id} not found.")
                    return
                # Check if any other campaign for this sender is IN_PROGRESS
                in_progress_query = select(Campaign).where(
                    Campaign.sender_number == campaign.sender_number,
                    Campaign.status == "IN_PROGRESS",
                    Campaign.id != campaign_id
                )
                in_progress_result = await db.execute(in_progress_query)
                in_progress_campaign = in_progress_result.scalar_one_or_none()
                if in_progress_campaign:
                    logging.warning(f"[CELERY] Skipping campaign {campaign_id}: another campaign for sender {campaign.sender_number} is already IN_PROGRESS.")
                    return
                # ATOMIC: Try to claim the campaign by setting status to IN_PROGRESS only if it is PENDING
                update_stmt = update(Campaign).where(
                    Campaign.id == campaign_id,
                    Campaign.status == "PENDING"
                ).values(status="IN_PROGRESS")
                result = await db.execute(update_stmt)
                await db.commit()
                if result.rowcount == 0:
                    # Already claimed or processed by another worker
                    logging.warning(f"[CELERY] Skipping campaign {campaign_id}: already claimed or not pending.")
                    return
                # Now fetch the campaign (should be IN_PROGRESS)
                query = select(Campaign).where(Campaign.id == campaign_id)
                result = await db.execute(query)
                campaign = result.scalar_one_or_none()
                if not campaign:
                    logging.error(f"[CELERY] Campaign {campaign_id} not found after claim.")
                    return
                # Get pending messages
                msg_query = select(Message.id).where(
                    Message.campaign_id == campaign_id,
                    Message.status == "PENDING"
                )
                msg_result = await db.execute(msg_query)
                message_ids = [row[0] for row in msg_result.fetchall()]
                messaging_service = MessagingService(db)
                for message_id in message_ids:
                    try:
                        query = select(Message).where(Message.id == message_id)
                        result = await db.execute(query)
                        message = result.scalar_one()
                        await self._apply_rate_limiting(campaign.sender_number, db)
                        final_message = campaign.template
                        if campaign.variables:
                            pass  # TODO: Implement variable replacement logic
                        result = await messaging_service.send_message(
                            campaign.sender_number,
                            message.recipient,
                            final_message,
                            campaign.media_url
                        )
                        message.status = "SENT"
                        message.sent_at = datetime.utcnow()
                        message.delivered_at = message.sent_at
                        waha_id = result["details"].get("waha_message_id")
                        if isinstance(waha_id, dict):
                            message.waha_message_id = waha_id.get("_serialized") or waha_id.get("id")
                        else:
                            message.waha_message_id = waha_id
                        campaign.sent_messages += 1
                        await db.commit()
                    except Exception as e:
                        logging.error(f"[CELERY] Error sending message {message_id}: {str(e)}")
                        query = select(Message).where(Message.id == message_id)
                        result = await db.execute(query)
                        message = result.scalar_one()
                        message.status = "FAILED"
                        message.error = str(e)
                        campaign.failed_messages += 1
                        await db.commit()
                # After all messages, update campaign status and completed_at
                campaign.status = "COMPLETED"
                campaign.completed_at = datetime.utcnow()
                if campaign.failed_messages > 0:
                    campaign.status = "COMPLETED_WITH_ERRORS"
                await db.commit()
                logging.info(f"[CELERY] Campaign {campaign.sender_number} {campaign.id} completed.")
        except Exception as e:
            logging.error(f"[CELERY] Error processing campaign {campaign_id}: {str(e)}")
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MessageQueue, cls).__new__(cls)
            # Store (campaign_id, sender_number) tuples for fast per-user queue size
            cls._instance.queue = deque()  # Each item: (campaign_id, sender_number)
            cls._instance.active_campaigns: Set[int] = set()  # Currently processing campaigns
            cls._instance.last_send_time: Dict[str, datetime] = {}  # Last send time per sender
            cls._instance.message_delay = int(os.getenv('MESSAGE_DELAY', '2'))  # Delay between messages
            cls._instance.sender_switch_delay = int(os.getenv('SENDER_SWITCH_DELAY', '5'))  # Delay when switching senders
            cls._instance.processing = False
            cls._instance.processing_task = None
            cls._instance.worker_tasks = []  # Store worker tasks
            cls._instance.num_workers = int(os.getenv('MESSAGE_QUEUE_WORKERS', '4'))  # Configurable via env
            # Remove static campaign_delay, always read from env
            cls._instance.last_campaign_time: Dict[str, datetime] = {}  # Last campaign completion time per sender
            cls._instance.active_senders: Set[str] = set()  # Senders currently being processed
            # Remove global sender switch lock; use per-worker last_sender
        return cls._instance

    async def start_processing(self, db_pool, num_workers: int = None):
        """Start multiple workers to process the message queue in parallel (DB-driven)."""
        if not self.processing:
            self.processing = True
            self.db_pool = db_pool
            if num_workers is not None:
                self.num_workers = num_workers
            self.worker_tasks = [asyncio.create_task(self._process_queue(db_pool)) for _ in range(self.num_workers)]
            logging.info(f"Message queue processor started with {self.num_workers} workers (DB-driven)")

    async def stop_processing(self):
        """Stop all worker tasks."""
        if self.processing:
            self.processing = False
            for task in self.worker_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self.worker_tasks = []
            logging.info("Message queue processor stopped")

    # No longer needed: campaigns are selected directly from DB

    async def get_size(self) -> int:
        """Get number of campaigns with pending messages in DB."""
        async with AsyncSession(self.db_pool) as db:
            query = select(Message.campaign_id).where(Message.status == "PENDING")
            result = await db.execute(query)
            campaigns = set(row[0] for row in result.fetchall())
            return len(campaigns)

    async def get_size_by_sender(self, sender_number: str) -> int:
        sender_number = normalize_number(sender_number)
        async with AsyncSession(self.db_pool) as db:
            from app.models.models import Campaign, Message
            query = select(Message.campaign_id).join(Campaign).where(
                Message.status == "PENDING",
                Campaign.sender_number == sender_number
            )
            result = await db.execute(query)
            campaigns = set(row[0] for row in result.fetchall())
            return len(campaigns)

    async def _process_queue(self, db_pool):
        import threading
        import asyncio
        from sqlalchemy import text
        logging.info(f"WORKER_CONTEXT: thread={threading.current_thread().name}, event_loop={asyncio.get_event_loop()}")
        self.db_pool = db_pool
        from app.models.models import Campaign, Message
        # Each worker tracks its own last_sender
        last_sender = None
        while self.processing:
            try:
                # --- DB-driven campaign selection ---
                async with AsyncSession(db_pool, expire_on_commit=False) as db:
                    # Find next campaign with pending messages, not locked, and respecting campaign delay
                    # Use FOR UPDATE SKIP LOCKED for distributed locking
                    # Get per-sender last completed campaign time from campaign table
                    # Only select campaigns with status 'PENDING' or 'IN_PROGRESS'
                    # Enforce campaign delay by checking last completed campaign for sender
                    # This query assumes campaigns table has sender_number and status fields
                    # You may need to adjust field names if different
                    # Get all eligible campaigns
                    eligible_campaign = None
                    # Raw SQL for SKIP LOCKED (SQLAlchemy 1.4+ supports with_execution_options)
                    query = (
                        select(Campaign)
                        .where(Campaign.status == "PENDING")
                        .order_by(Campaign.created_at)
                        .with_for_update(skip_locked=True)
                    )
                    result = await db.execute(query)
                    campaigns = result.scalars().all()
                    eligible_campaign = None
                    for campaign in campaigns:
                        now = datetime.utcnow()
                        # Sender switch delay: only if switching sender at campaign start
                        if last_sender is not None and last_sender != campaign.sender_number:
                            from app.services.delay_config import get_delay_config
                            config = await get_delay_config(db)
                            sender_switch_delay = config.sender_switch_delay
                            await asyncio.sleep(sender_switch_delay)
                        # Check if campaign has pending messages
                        msg_query = select(Message).where(
                            Message.campaign_id == campaign.id,
                            Message.status == "PENDING"
                        )
                        msg_result = await db.execute(msg_query)
                        pending_msgs = msg_result.scalars().all()
                        if not pending_msgs:
                            continue
                        # ATOMIC: Lock all campaigns for this sender (FOR UPDATE)
                        sender_lock_query = (
                            select(Campaign)
                            .where(Campaign.sender_number == campaign.sender_number)
                            .with_for_update()
                        )
                        await db.execute(sender_lock_query)
                        # Check if this sender has any campaign IN_PROGRESS
                        in_progress_query = select(Campaign).where(
                            Campaign.sender_number == campaign.sender_number,
                            Campaign.status == "IN_PROGRESS"
                        )
                        in_progress_result = await db.execute(in_progress_query)
                        in_progress_campaign = in_progress_result.scalar_one_or_none()
                        if in_progress_campaign:
                            continue  # Wait for previous campaign to complete
                        # Enforce campaign delay (fetch from DB)
                        from app.services.delay_config import get_delay_config
                        config = await get_delay_config(db)
                        campaign_delay = config.campaign_delay
                        # Fetch per-user campaign delay from DB
                        result = await db.execute(select(UserDelay).where(UserDelay.sender_number == campaign.sender_number))
                        user_delay = result.scalar_one_or_none()
                        if user_delay and user_delay.campaign_delay is not None:
                            campaign_delay = int(user_delay.campaign_delay)
                        # Find last completed campaign for this sender (from DB)
                        last_completed_query = select(Campaign.completed_at).where(
                            Campaign.sender_number == campaign.sender_number,
                            Campaign.status.in_(["COMPLETED", "COMPLETED_WITH_ERRORS"]),
                            Campaign.completed_at.isnot(None)
                        ).order_by(Campaign.completed_at.desc())
                        last_completed_result = await db.execute(last_completed_query)
                        last_completed_at = last_completed_result.scalar()
                        logging.info(f"[DELAY CHECK] Sender: {campaign.sender_number}, Campaign: {campaign.id}, Now: {now}, Last completed: {last_completed_at}, Delay: {campaign_delay}")
                        if last_completed_at:
                            elapsed = (now - last_completed_at).total_seconds()
                            logging.info(f"[DELAY CHECK] Elapsed since last completed: {elapsed} seconds")
                            if elapsed < campaign_delay:
                                logging.info(f"[DELAY ENFORCED] Sender: {campaign.sender_number}, Campaign: {campaign.id}, Waiting for {campaign_delay - elapsed} seconds")
                                continue
                        # Immediately set campaign status to IN_PROGRESS and commit
                        campaign.status = "IN_PROGRESS"
                        await db.commit()
                        logging.info(f"[CAMPAIGN START] Sender: {campaign.sender_number}, Campaign: {campaign.id}, Started at: {datetime.utcnow()}")
                        eligible_campaign = campaign
                        last_sender = campaign.sender_number
                        break
                if eligible_campaign:
                    campaign_id = eligible_campaign.id
                    sender_number = eligible_campaign.sender_number
                    try:
                        # Mark campaign as IN_PROGRESS
                        eligible_campaign.status = "IN_PROGRESS"
                        await db.commit()
                        # Get pending message IDs
                        msg_query = select(Message.id).where(
                            Message.campaign_id == campaign_id,
                            Message.status == "PENDING"
                        )
                        msg_result = await db.execute(msg_query)
                        message_ids = [row[0] for row in msg_result.fetchall()]
                        messaging_service = MessagingService()
                        for message_id in message_ids:
                            try:
                                async with AsyncSession(db_pool, expire_on_commit=False) as db_msg:
                                    query = select(Message).where(Message.id == message_id)
                                    result = await db_msg.execute(query)
                                    message = result.scalar_one()
                                    query = select(Campaign).where(Campaign.id == campaign_id)
                                    result = await db_msg.execute(query)
                                    campaign = result.scalar_one()
                                    await self._apply_rate_limiting(campaign.sender_number, db_msg)
                                    final_message = campaign.template
                                    if campaign.variables:
                                        pass  # TODO: Implement variable replacement logic
                                    messaging_service = MessagingService(db_msg)
                                    result = await messaging_service.send_message(
                                        campaign.sender_number,
                                        message.recipient,
                                        final_message,
                                        campaign.media_url
                                    )
                                    message.status = "SENT"
                                    message.sent_at = datetime.utcnow()
                                    message.delivered_at = message.sent_at
                                    waha_id = result["details"].get("waha_message_id")
                                    if isinstance(waha_id, dict):
                                        message.waha_message_id = waha_id.get("_serialized") or waha_id.get("id")
                                    else:
                                        message.waha_message_id = waha_id
                                    campaign.sent_messages += 1
                                    await db_msg.commit()
                                    from app.models.models import Session
                                    query = select(Session).where(Session.phone_number == campaign.sender_number)
                                    result = await db_msg.execute(query)
                                    session = result.scalar_one_or_none()
                                    if session:
                                        session.last_active = datetime.utcnow()
                                        await db_msg.commit()
                                    # Update last_send_time for per-message delay
                                    self.last_send_time[campaign.sender_number] = datetime.utcnow()
                                    # Update last_sender for sender switch delay
                                    self.last_sender = campaign.sender_number
                            except Exception as e:
                                logging.error(f"Error sending message {message_id}: {str(e)}")
                                async with AsyncSession(db_pool, expire_on_commit=False) as db_err:
                                    query = select(Message).where(Message.id == message_id)
                                    result = await db_err.execute(query)
                                    message = result.scalar_one()
                                    query = select(Campaign).where(Campaign.id == campaign_id)
                                    result = await db_err.execute(query)
                                    campaign = result.scalar_one()
                                    message.status = "FAILED"
                                    message.error = str(e)
                                    campaign.failed_messages += 1
                                    await db_err.commit()
                        # After all messages, update campaign status and completed_at
                        async with AsyncSession(db_pool, expire_on_commit=False) as db_final:
                            query = select(Campaign).where(Campaign.id == campaign_id)
                            result = await db_final.execute(query)
                            campaign = result.scalar_one()
                            campaign.status = "COMPLETED"
                            campaign.completed_at = datetime.utcnow()
                            logging.info(f"[CAMPAIGN COMPLETE] Sender: {campaign.sender_number}, Campaign: {campaign.id}, Completed at: {campaign.completed_at}")
                            if campaign.failed_messages > 0:
                                campaign.status = "COMPLETED_WITH_ERRORS"
                            await db_final.commit()
                        # Ensure DB commit is visible before next campaign selection
                        import asyncio
                        await asyncio.sleep(0.2)
                        # Log all campaigns for this sender to verify DB state
                        async with AsyncSession(db_pool, expire_on_commit=False) as db_debug:
                            from app.models.models import Campaign
                            debug_query = select(Campaign.id, Campaign.status, Campaign.completed_at).where(Campaign.sender_number == campaign.sender_number)
                            debug_result = await db_debug.execute(debug_query)
                            debug_campaigns = debug_result.fetchall()
                            logging.info(f"[DEBUG] Campaigns for sender {campaign.sender_number}: " + ", ".join([f'id={row[0]}, status={row[1]}, completed_at={row[2]}' for row in debug_campaigns]))
                    except Exception as e:
                        logging.error(f"Error processing campaign {campaign_id}: {str(e)}")
                # Small delay to prevent CPU overuse when no eligible campaign
                await asyncio.sleep(0.1)
            except Exception as e:
                logging.error(f"Error in message queue processor: {str(e)}")
                await asyncio.sleep(1)

    async def _apply_rate_limiting(self, sender_number: str, db_session: AsyncSession):
        sender_number = normalize_number(sender_number)
        """Apply rate limiting rules"""
        from app.services.delay_config import get_delay_config
        import asyncio
        now = datetime.utcnow()
        # Fetch global delays from DB using provided session
        config = await get_delay_config(db_session)
        # Fetch per-user message delay from DB
        result = await db_session.execute(select(UserDelay).where(UserDelay.sender_number == sender_number))
        user_delay = result.scalar_one_or_none()
        if user_delay and user_delay.message_delay is not None:
            message_delay = int(user_delay.message_delay)
        else:
            message_delay = config.message_delay

        # Check if we need to apply message delay
        if sender_number in self.last_send_time:
            last_time = self.last_send_time[sender_number]
            elapsed = (now - last_time).total_seconds()
            if elapsed < message_delay:
                await asyncio.sleep(message_delay - elapsed)

# Create a singleton instance
message_queue = MessageQueue()
