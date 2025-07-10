import asyncio
from collections import deque
from datetime import datetime, timedelta
import logging
from typing import Dict, Optional, Set
import os
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.models import Campaign, Message
from app.services.messaging import MessagingService
from app.routers.delays import _user_delays, _user_delays_lock

# Utility for phone number normalization (import from delays or define here)
def normalize_number(number: str) -> str:
    return number.lstrip('+').strip() if number else number

class MessageQueue:
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
            cls._instance.num_workers = 4  # Default to 4 workers
        return cls._instance

    async def start_processing(self, db_pool, num_workers: int = None):
        """Start multiple workers to process the message queue in parallel."""
        if not self.processing:
            self.processing = True
            self.db_pool = db_pool
            if num_workers is not None:
                self.num_workers = num_workers
            self.worker_tasks = [asyncio.create_task(self._process_queue(db_pool)) for _ in range(self.num_workers)]
            logging.info(f"Message queue processor started with {self.num_workers} workers")

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

    async def add_campaign(self, campaign_id: int, sender_number: str = None):
        if sender_number:
            sender_number = normalize_number(sender_number)
        """Add a campaign to the queue, storing sender_number for fast per-user stats."""
        async with self._lock:
            if campaign_id not in self.active_campaigns:
                if sender_number is None:
                    # Fallback: fetch sender_number from DB (should not happen in optimized flow)
                    from app.models.models import Campaign
                    import asyncio
                    async with AsyncSession(self.db_pool) as db:
                        query = select(Campaign).where(Campaign.id == campaign_id)
                        result = await db.execute(query)
                        campaign = result.scalar_one_or_none()
                        sender_number = normalize_number(campaign.sender_number) if campaign else None
                self.queue.append((campaign_id, sender_number))
                logging.info(f"Campaign {campaign_id} (sender {sender_number}) added to queue")

    async def get_size(self) -> int:
        """Get current queue size"""
        async with self._lock:
            return len(self.queue)

    async def get_size_by_sender(self, sender_number: str) -> int:
        sender_number = normalize_number(sender_number)
        """Get the number of campaigns in the queue for a specific sender_number."""
        async with self._lock:
            return sum(1 for _, s in self.queue if s == sender_number)

    async def _process_queue(self, db_pool):
        import threading
        import asyncio
        logging.info(f"WORKER_CONTEXT: thread={threading.current_thread().name}, event_loop={asyncio.get_event_loop()}")
        self.db_pool = db_pool  # Store for add_campaign fallback
        """Process messages in the queue"""
        while self.processing:
            try:
                campaign_id = None
                sender_number = None
                async with self._lock:
                    if self.queue:
                        campaign_id, sender_number = self.queue.popleft()
                        self.active_campaigns.add(campaign_id)

                if campaign_id:
                    try:
                        # Fetch campaign details once
                        async with AsyncSession(db_pool, expire_on_commit=False) as db:
                            query = select(Campaign).where(Campaign.id == campaign_id)
                            result = await db.execute(query)
                            campaign = result.scalar_one_or_none()
                            if not campaign:
                                logging.error(f"Campaign {campaign_id} not found")
                                continue
                            # Get pending message IDs
                            query = select(Message.id).where(
                                Message.campaign_id == campaign_id,
                                Message.status == "PENDING"
                            )
                            result = await db.execute(query)
                            message_ids = [row[0] for row in result.fetchall()]

                        messaging_service = MessagingService()
                        for message_id in message_ids:
                            try:
                                # Use a new session for each message
                                async with AsyncSession(db_pool, expire_on_commit=False) as db_msg:
                                    # Fetch message and campaign fresh in this session
                                    query = select(Message).where(Message.id == message_id)
                                    result = await db_msg.execute(query)
                                    message = result.scalar_one()
                                    query = select(Campaign).where(Campaign.id == campaign_id)
                                    result = await db_msg.execute(query)
                                    campaign = result.scalar_one()

                                    # Check and apply rate limiting
                                    await self._apply_rate_limiting(campaign.sender_number)

                                    # Replace variables in template if any
                                    final_message = campaign.template
                                    if campaign.variables:
                                        # TODO: Implement variable replacement logic
                                        pass

                                    # Send message
                                    messaging_service = MessagingService(db_msg)
                                    result = await messaging_service.send_message(
                                        campaign.sender_number,
                                        message.recipient,
                                        final_message,
                                        campaign.media_url
                                    )

                                    # Update message status
                                    message.status = "SENT"
                                    message.sent_at = datetime.utcnow()
                                    campaign.sent_messages += 1
                                    await db_msg.commit()

                                    # Update last send time
                                    self.last_send_time[campaign.sender_number] = datetime.utcnow()

                                    # Update session last_active to keep session alive during broadcast
                                    from app.models.models import Session
                                    query = select(Session).where(Session.phone_number == campaign.sender_number)
                                    result = await db_msg.execute(query)
                                    session = result.scalar_one_or_none()
                                    if session:
                                        session.last_active = datetime.utcnow()
                                        await db_msg.commit()

                            except Exception as e:
                                logging.error(f"Error sending message {message_id}: {str(e)}")
                                # Use a new session for error update
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

                        # After all messages, update campaign status
                        async with AsyncSession(db_pool, expire_on_commit=False) as db_final:
                            query = select(Campaign).where(Campaign.id == campaign_id)
                            result = await db_final.execute(query)
                            campaign = result.scalar_one()
                            campaign.status = "COMPLETED"
                            if campaign.failed_messages > 0:
                                campaign.status = "COMPLETED_WITH_ERRORS"
                            await db_final.commit()

                    except Exception as e:
                        logging.error(f"Error processing campaign {campaign_id}: {str(e)}")
                    finally:
                        async with self._lock:
                            self.active_campaigns.remove(campaign_id)
                
                # Small delay to prevent CPU overuse when queue is empty
                await asyncio.sleep(0.1)

            except Exception as e:
                logging.error(f"Error in message queue processor: {str(e)}")
                await asyncio.sleep(1)  # Longer delay on error

    async def _apply_rate_limiting(self, sender_number: str):
        sender_number = normalize_number(sender_number)
        """Apply rate limiting rules"""
        import os
        now = datetime.utcnow()
        # --- PATCH: Use per-user delay if set, else fallback to global ---
        with _user_delays_lock:
            user_delay = _user_delays.get(sender_number)
        if user_delay:
            message_delay = int(user_delay.get('MESSAGE_DELAY', os.environ.get('MESSAGE_DELAY', '2')))
            sender_switch_delay = int(user_delay.get('SENDER_SWITCH_DELAY', os.environ.get('SENDER_SWITCH_DELAY', '5')))
        else:
            message_delay = int(os.environ.get('MESSAGE_DELAY', '2'))
            sender_switch_delay = int(os.environ.get('SENDER_SWITCH_DELAY', '5'))
        # Check if we need to apply sender switch delay
        last_sender = None
        if self.last_send_time:
            last_sender = max(self.last_send_time.items(), key=lambda x: x[1])[0]
        if last_sender and last_sender != sender_number:
            await asyncio.sleep(sender_switch_delay)
        # Check if we need to apply message delay
        if sender_number in self.last_send_time:
            last_time = self.last_send_time[sender_number]
            elapsed = (now - last_time).total_seconds()
            if elapsed < message_delay:
                await asyncio.sleep(message_delay - elapsed)

# Create a singleton instance
message_queue = MessageQueue()
