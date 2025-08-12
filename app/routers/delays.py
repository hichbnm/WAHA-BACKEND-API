from fastapi import APIRouter, Depends, HTTPException
from fastapi import Body
from app.utils.auth import verify_admin_token
import os
from fastapi import Query
from app.models.models import UserDelay
from app.db.database import async_session
from sqlalchemy.future import select
from sqlalchemy import update
from pydantic import BaseModel, Field
from typing import Optional

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

class DelayUpdate(BaseModel):
    # Accept either MESSAGE_DELAY or message_delay in JSON body
    message_delay: Optional[int] = Field(default=None, alias="MESSAGE_DELAY")

    class Config:
        allow_population_by_field_name = True

@router.post("/delays", tags=["admin"])
async def set_delays(
    body: Optional[DelayUpdate] = Body(None),
    message_delay: Optional[int] = Query(None),
    admin_token: str = Depends(verify_admin_token)
):
    """Set message delay for global system (admin only).

    Accepts either:
    - JSON body: { "MESSAGE_DELAY": <int> } or { "message_delay": <int> }
    - Or query parameter: ?message_delay=<int>
    """
    # Prefer explicit query parameter if provided; otherwise use JSON body
    message_delay = message_delay if message_delay is not None else (body.message_delay if body else None)
    if message_delay is not None and not (1 <= message_delay <= 30):
        return {"error": "message_delay must be between 1 and 30 seconds."}
    from app.services.delay_config import set_delay_config, get_delay_config
    async with async_session() as db:
        await set_delay_config(db, message_delay, None, None)
        # If a new global message_delay was provided, reset all user-specific delays to this value
        if message_delay is not None:
            await db.execute(update(UserDelay).values(message_delay=message_delay))
            await db.commit()
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
        from app.services.delay_config import get_delay_config
        config = await get_delay_config(db)
        return {
            "MESSAGE_DELAY": config.message_delay
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
