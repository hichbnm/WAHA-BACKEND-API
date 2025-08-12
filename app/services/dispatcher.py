
import asyncio
import collections
import logging
from typing import Deque, Dict, Optional
from datetime import datetime, timedelta
import math


from app.models.models import Message
from app.db.database import async_session
from sqlalchemy import select, update
from app.services.redis_lock import RedisLock
import os

class RoundRobinDispatcher:
    def __init__(self):
        # FIFO rotation of sender numbers that currently have pending messages
        self.sender_queue: Deque[str] = collections.deque()
        # Fast membership check of active senders in the queue
        self.active_senders: set = set()
        # Next time a sender is allowed to enqueue a message (per-user delay)
        self._next_allowed_time: Dict[str, datetime] = {}

    async def refresh_senders(self):
        """Refresh the queue of senders with pending messages (case-insensitive)."""
        from app.models.models import Campaign
        async with async_session() as session:
            result = await session.execute(
                select(Campaign.sender_number)
                .join(Message, Message.campaign_id == Campaign.id)
                .where(
                    Message.status == 'PENDING',
                    Campaign.status.in_(['PENDING', 'IN_PROGRESS'])  # skip cancelled/completed campaigns
                )
                .distinct()
            )
            senders = [row[0] for row in result.fetchall()]
        # Only add new senders to the queue
        for sender in senders:
            if sender not in self.active_senders:
                self.sender_queue.append(sender)
                self.active_senders.add(sender)
        # Remove senders with no pending messages
        for sender in list(self.active_senders):
            if sender not in senders:
                self.active_senders.remove(sender)
                try:
                    self.sender_queue.remove(sender)
                except ValueError:
                    pass

    async def get_next_pending_message(self, sender_number: str) -> Optional[Message]:
        from app.models.models import Campaign
        async with async_session() as session:
            result = await session.execute(
                select(Message)
                .join(Campaign, Message.campaign_id == Campaign.id)
                .where(
                    Campaign.sender_number == sender_number,
                    Message.status == 'PENDING',
                    Campaign.status.in_(['PENDING', 'IN_PROGRESS'])
                )
                .order_by(Message.id.asc())
                .limit(1)
            )
            message = result.scalar_one_or_none()
            return message

    async def mark_message_in_progress(self, message_id: int, sender_number: str) -> bool:
        """Atomically mark a message IN_PROGRESS only if no other message is IN_PROGRESS for this sender.
        Returns True if the row was updated, False otherwise.
        """
        from app.models.models import Campaign
        from sqlalchemy import exists, and_, not_
        async with async_session() as session:
            # Ensure campaign is still active
            camp_id_res = await session.execute(select(Message.campaign_id).where(Message.id == message_id))
            camp_id = camp_id_res.scalar_one_or_none()
            if camp_id is None:
                return False
            camp_res = await session.execute(select(Campaign.status).where(Campaign.id == camp_id))
            camp_status = camp_res.scalar_one_or_none()
            if camp_status not in ('PENDING', 'IN_PROGRESS'):
                return False

            # Build NOT EXISTS for any other IN_PROGRESS message for this sender
            m2 = Message.__table__.alias('m2')
            c2 = Campaign.__table__.alias('c2')
            exists_inflight = exists(
                select(1)
                .select_from(m2.join(c2, m2.c.campaign_id == c2.c.id))
                .where(and_(
                    c2.c.sender_number == sender_number,
                    m2.c.status == 'IN_PROGRESS'
                ))
            )
            # Perform conditional update
            result = await session.execute(
                update(Message)
                .where(
                    and_(
                        Message.id == message_id,
                        not_(exists_inflight)
                    )
                )
                .values(status='IN_PROGRESS')
            )
            updated = result.rowcount and result.rowcount > 0
            if not updated:
                await session.rollback()
                return False
            # Ensure parent campaign is marked IN_PROGRESS
            await session.execute(
                update(Campaign)
                .where(Campaign.id == camp_id)
                .values(status='IN_PROGRESS')
            )
            await session.commit()
            return True

    async def has_in_progress(self, sender_number: str) -> bool:
        from app.models.models import Campaign
        async with async_session() as session:
            result = await session.execute(
                select(Message.id)
                .join(Campaign, Message.campaign_id == Campaign.id)
                .where(
                    Campaign.sender_number == sender_number,
                    Message.status == 'IN_PROGRESS'
                )
                .limit(1)
            )
            return result.first() is not None

    async def dispatch(self):
        from tasks.message_tasks import process_message_task
        from app.models.models import UserDelay
        from sqlalchemy import select
        from app.services.delay_config import get_delay_config
        redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
        lock_key = 'dispatcher_lock'
        backoff = 2
        while True:
            try:
                async with RedisLock(redis_url, lock_key, ttl=60):
                    # Reset backoff once we get the lock
                    backoff = 2
                    while True:
                        await self.refresh_senders()
                        if not self.sender_queue:
                            await asyncio.sleep(1)
                            continue
                        sender = self.sender_queue.popleft()
                        logging.info(f"Dispatcher: Picked sender {sender}")
                        message = await self.get_next_pending_message(sender)
                        if message:
                            # Enforce per-user delay without blocking other senders
                            now = datetime.utcnow()
                            next_time = self._next_allowed_time.get(sender)
                            if next_time and now < next_time:
                                # Not yet time to send for this sender, rotate and retry later
                                remaining = (next_time - now).total_seconds()
                                logging.debug(f"[RATE LIMIT] Sender: {sender}, not yet allowed. Retry in ~{remaining:.2f}s")
                                self.sender_queue.append(sender)
                                await asyncio.sleep(0.05)
                                continue
                            # Compute a new delay window and set next allowed time now
                            # Fetch delay config and user-specific delay
                            async with async_session() as session:
                                config = await get_delay_config(session)
                                result = await session.execute(select(UserDelay).where(UserDelay.sender_number == sender))
                                user_delay = result.scalar_one_or_none()
                                base_delay = int(user_delay.message_delay) if (user_delay and user_delay.message_delay is not None) else config.message_delay
                                # Extra guard: ensure at least base_delay seconds from last SENT message for this sender
                                from sqlalchemy import func
                                from app.models.models import Campaign
                                last_sent_res = await session.execute(
                                    select(func.max(Message.sent_at))
                                    .join(Campaign, Message.campaign_id == Campaign.id)
                                    .where(
                                        Campaign.sender_number == sender,
                                        Message.sent_at.isnot(None)
                                    )
                                )
                                last_sent_at = last_sent_res.scalar_one_or_none()
                            if last_sent_at is not None:
                                gap = (now - last_sent_at).total_seconds()
                                if gap < (base_delay or 1):
                                    remaining = (base_delay or 1) - gap
                                    logging.debug(f"[RATE LIMIT/DB] Sender: {sender}, last_sent gap={gap:.2f}s, need {remaining:.2f}s more")
                                    # Push the sender to the back and wait a bit to avoid tight loop
                                    self.sender_queue.append(sender)
                                    await asyncio.sleep(min(max(remaining, 0.1), 1.0))
                                    continue
                            # Strictly enforce at least the configured delay; remove jitter
                            chosen_delay = max(1, int(math.ceil(base_delay or 1)))
                            # Mark as IN_PROGRESS atomically; skip enqueue if campaign is no longer active or another inflight exists
                            if await self.mark_message_in_progress(message.id, sender):
                                # set next allowed time only when we actually enqueue
                                self._next_allowed_time[sender] = now + timedelta(seconds=chosen_delay)
                                logging.info(f"[RATE LIMIT] Sender: {sender}, Base delay: {base_delay}, Chosen delay: {chosen_delay}")
                                logging.info(f"Dispatcher: Enqueuing Celery task for message {message.id} (sender={sender})")
                                process_message_task.delay(message.id)
                            else:
                                logging.info(f"Dispatcher: Skip enqueue for message {message.id} (sender={sender}); campaign inactive or another inflight exists")
                        else:
                            logging.info(f"Dispatcher: No pending message found for sender {sender}")
                        # Rotate sender to end of queue if still has pending messages
                        await self.refresh_senders()
                        if sender in self.active_senders:
                            self.sender_queue.append(sender)
                        await asyncio.sleep(0.1)  # Tune as needed
            except RuntimeError as e:
                # Likely could not acquire lock; another instance is running the dispatcher
                if 'dispatcher lock' in str(e).lower():
                    logging.debug("Dispatcher lock not acquired; another instance holds it. Retrying...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10)
                    continue
                logging.exception("Dispatcher runtime error; retrying soon")
                await asyncio.sleep(3)
            except Exception:
                logging.exception("Dispatcher crashed; retrying")
                await asyncio.sleep(5)

# Usage (in a background task or Celery beat):
# dispatcher = RoundRobinDispatcher()
# asyncio.run(dispatcher.dispatch())
