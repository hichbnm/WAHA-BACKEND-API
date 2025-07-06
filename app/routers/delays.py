from fastapi import APIRouter, Depends, HTTPException
from app.utils.auth import verify_admin_token
import os

router = APIRouter()

@router.get("/delays", tags=["admin"])
def get_delays(admin_token: str = Depends(verify_admin_token)):
    """Get current message and sender switch delays (admin only)"""
    return {
        "MESSAGE_DELAY": int(os.getenv("MESSAGE_DELAY", 2)),
        "SENDER_SWITCH_DELAY": int(os.getenv("SENDER_SWITCH_DELAY", 5))
    }

@router.post("/delays", tags=["admin"])
def set_delays(
    message_delay: int = None,
    sender_switch_delay: int = None,
    admin_token: str = Depends(verify_admin_token)
):
    """Set message and sender switch delays (admin only)"""
    if message_delay is not None:
        os.environ["MESSAGE_DELAY"] = str(message_delay)
    if sender_switch_delay is not None:
        os.environ["SENDER_SWITCH_DELAY"] = str(sender_switch_delay)
    return {
        "MESSAGE_DELAY": int(os.getenv("MESSAGE_DELAY", 2)),
        "SENDER_SWITCH_DELAY": int(os.getenv("SENDER_SWITCH_DELAY", 5))
    }
