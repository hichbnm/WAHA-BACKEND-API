from fastapi import APIRouter, Depends, HTTPException
from app.utils.auth import verify_admin_token
import os
from fastapi import Query
from app.models.models import UserDelay
from app.db.database import async_session
from sqlalchemy.future import select

router = APIRouter()

@router.get("/delays", tags=["admin"])
async def get_delays(admin_token: str = Depends(verify_admin_token)):
    """Get current message and sender switch delays globally (admin only)"""
    from app.services.delay_config import get_delay_config
    async with async_session() as db:
        config = await get_delay_config(db)
        return {
            "MESSAGE_DELAY": config.message_delay
        }

@router.post("/delays", tags=["admin"])
async def set_delays(
    message_delay: int = None,
    admin_token: str = Depends(verify_admin_token)
):
    """Set message delay for global system (admin only)"""
    if message_delay is not None and not (1 <= message_delay <= 30):
        return {"error": "message_delay must be between 1 and 30 seconds."}
    from app.services.delay_config import set_delay_config, get_delay_config
    async with async_session() as db:
        await set_delay_config(db, message_delay, None, None)
        config = await get_delay_config(db)
        return {
            "MESSAGE_DELAY": config.message_delay
        }

def normalize_number(number: str) -> str:
    """Remove leading + and whitespace from phone numbers."""
    return number.lstrip('+').strip() if number else number

@router.get("/user-delays", include_in_schema=True)
async def get_user_delays(sender_number: str = Query(...)):
    """Get message and sender switch delays per user """
    sender_number = normalize_number(sender_number)
    async with async_session() as db:
        result = await db.execute(select(UserDelay).where(UserDelay.sender_number == sender_number))
        user_delay = result.scalar_one_or_none()
        if user_delay:
            return {
                "MESSAGE_DELAY": user_delay.message_delay
            }
        # Fallback to global
        return {
            "MESSAGE_DELAY": int(os.getenv("MESSAGE_DELAY", 2))
        }

@router.post("/user-delays", include_in_schema=True)
async def set_user_delays(
    sender_number: str = Query(...),
    message_delay: int = None
):
    """Set message delay for a specific user"""
    if message_delay is not None and not (1 <= message_delay <= 30):
        return {"error": "message_delay must be between 1 and 30 seconds."}
    sender_number = normalize_number(sender_number)
    async with async_session() as db:
        result = await db.execute(select(UserDelay).where(UserDelay.sender_number == sender_number))
        user_delay = result.scalar_one_or_none()
        if user_delay is None:
            user_delay = UserDelay(
                sender_number=sender_number,
                message_delay=message_delay
            )
            db.add(user_delay)
        else:
            if message_delay is not None:
                user_delay.message_delay = message_delay
        await db.commit()
        return {
            "MESSAGE_DELAY": user_delay.message_delay
        }
