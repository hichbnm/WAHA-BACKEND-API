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

class MessageQueue:
    _instance = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MessageQueue, cls).__new__(cls)
            cls._instance.queue = deque()  # Campaign IDs queue
            cls._instance.active_campaigns: Set[int] = set()  # Currently processing campaigns
            cls._instance.last_send_time: Dict[str, datetime] = {}  # Last send time per sender
            cls._instance.message_delay = int(os.getenv('MESSAGE_DELAY', '2'))  # Delay between messages
            cls._instance.sender_switch_delay = int(os.getenv('SENDER_SWITCH_DELAY', '5'))  # Delay when switching senders
            cls._instance.processing = False
            cls._instance.processing_task = None
        return cls._instance

    async def start_processing(self, db_pool):
        """Start processing the message queue"""
        if not self.processing:
            self.processing = True
            self.processing_task = asyncio.create_task(self._process_queue(db_pool))
            logging.info("Message queue processor started")

    async def stop_processing(self):
        """Stop processing the message queue"""
        if self.processing:
            self.processing = False
            if self.processing_task:
                self.processing_task.cancel()
                try:
                    await self.processing_task
                except asyncio.CancelledError:
                    pass
            logging.info("Message queue processor stopped")

    async def add_campaign(self, campaign_id: int):
        """Add a campaign to the queue"""
        async with self._lock:
            if campaign_id not in self.active_campaigns:
                self.queue.append(campaign_id)
                logging.info(f"Campaign {campaign_id} added to queue")

    async def get_size(self) -> int:
        """Get current queue size"""
        async with self._lock:
            return len(self.queue)

    async def _process_queue(self, db_pool):
        """Process messages in the queue"""
        while self.processing:
            try:
                # Get next campaign if queue not empty
                campaign_id = None
                async with self._lock:
                    if self.queue:
                        campaign_id = self.queue.popleft()
                        self.active_campaigns.add(campaign_id)

                if campaign_id:
                    try:
                        async with AsyncSession(db_pool) as db:
                            # Get campaign details
                            query = select(Campaign).where(Campaign.id == campaign_id)
                            result = await db.execute(query)
                            campaign = result.scalar_one_or_none()

                            if not campaign:
                                logging.error(f"Campaign {campaign_id} not found")
                                continue

                            # Get pending messages
                            query = select(Message).where(
                                Message.campaign_id == campaign_id,
                                Message.status == "PENDING"
                            )
                            result = await db.execute(query)
                            messages = result.scalars().all()

                            # Process each message
                            messaging_service = MessagingService()
                            for message in messages:
                                try:
                                    # Check and apply rate limiting
                                    await self._apply_rate_limiting(campaign.sender_number)

                                    # Replace variables in template if any
                                    final_message = campaign.template
                                    if campaign.variables:
                                        # TODO: Implement variable replacement logic
                                        pass

                                    # Send message
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
                                    await db.commit()

                                    # Update last send time
                                    self.last_send_time[campaign.sender_number] = datetime.utcnow()

                                except Exception as e:
                                    logging.error(f"Error sending message {message.id}: {str(e)}")
                                    message.status = "FAILED"
                                    message.error = str(e)
                                    campaign.failed_messages += 1
                                    await db.commit()

                            # Update campaign status
                            campaign.status = "COMPLETED"
                            if campaign.failed_messages > 0:
                                campaign.status = "COMPLETED_WITH_ERRORS"
                            await db.commit()

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
        """Apply rate limiting rules"""
        import os
        now = datetime.utcnow()
        # Always fetch latest delay values from environment
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
