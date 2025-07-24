import os
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.models.models import Message, Session
from dotenv import load_dotenv
load_dotenv()
NEW_USER_DAILY_LIMIT = int(os.getenv("NEW_USER_DAILY_LIMIT", 50))
EXISTING_USER_DAILY_LIMIT = int(os.getenv("EXISTING_USER_DAILY_LIMIT", 300))

async def get_daily_limit(db, sender_number: str) -> int:
    # Get session creation time
    result = await db.execute(select(Session).where(Session.phone_number == sender_number))
    session = result.scalar_one_or_none()
    if not session:
        # If no session, treat as new user
        return NEW_USER_DAILY_LIMIT
    # Use created_at if available, fallback to last_active if not
    created_at = getattr(session, 'created_at', None) or getattr(session, 'last_active', None)
    if not created_at:
        return NEW_USER_DAILY_LIMIT
    if (datetime.utcnow() - created_at) < timedelta(hours=24):
        return NEW_USER_DAILY_LIMIT
    return EXISTING_USER_DAILY_LIMIT

async def get_sent_today(db, sender_number: str) -> int:
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    # Count all messages scheduled or sent today: SENT, PENDING, DELIVERED, READ
    statuses = ['SENT', 'PENDING', 'DELIVERED', 'READ']
    # Get session creation time
    result = await db.execute(select(Session).where(Session.phone_number == sender_number))
    session = result.scalar_one_or_none()
    if not session:
        # If no session, use last 24h from now
        window_start = datetime.utcnow() - timedelta(hours=24)
    else:
        created_at = getattr(session, 'created_at', None) or getattr(session, 'last_active', None)
        if not created_at:
            window_start = datetime.utcnow() - timedelta(hours=24)
        else:
            # Find the most recent reset time (created_at + N*24h <= now)
            now = datetime.utcnow()
            hours_since = (now - created_at).total_seconds() / 3600
            periods = int(hours_since // 24)
            window_start = created_at + timedelta(hours=24 * periods)
    # Count all messages sent or scheduled in the current 24h window: SENT, PENDING, DELIVERED, READ
    query = select(func.count()).select_from(Message).where(
        Message.sent_at >= window_start,
        Message.status.in_(statuses),
        Message.campaign.has(sender_number=sender_number)
    )
    result = await db.execute(query)
    count = result.scalar() or 0
    logging.debug(f"[DEBUG] get_sent_today: sender={sender_number}, window_start={window_start}, now={datetime.utcnow()}, count={count}")
    return count

async def can_send_more(db, sender_number: str, num_to_send: int) -> bool:
    limit = await get_daily_limit(db, sender_number)
    sent = await get_sent_today(db, sender_number)
    return (sent + num_to_send) <= limit
