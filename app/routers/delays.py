from fastapi import APIRouter, Depends, HTTPException
from app.utils.auth import verify_admin_token
import os
from fastapi import Query
import threading

router = APIRouter()

# In-memory, thread-safe per-user delay storage
_user_delays = {}
_user_delays_lock = threading.Lock()

def normalize_number(number: str) -> str:
    """Remove leading + and whitespace from phone numbers."""
    return number.lstrip('+').strip() if number else number

@router.get("/delays", tags=["admin"])
def get_delays(admin_token: str = Depends(verify_admin_token)):
    """Get current message and sender switch delays globally (admin only)"""
    return {
        "MESSAGE_DELAY": int(os.getenv("MESSAGE_DELAY", 2)),
        "SENDER_SWITCH_DELAY": int(os.getenv("SENDER_SWITCH_DELAY", 5)),
        "CAMPAIGN_DELAY": int(os.getenv("CAMPAIGN_DELAY", 10))
    }

@router.post("/delays", tags=["admin"])
def set_delays(
    message_delay: int = None,
    sender_switch_delay: int = None,
    campaign_delay: int = None,
    admin_token: str = Depends(verify_admin_token)
):
    """Set message and sender switch delays for global system (admin only)"""
    if message_delay is not None:
        os.environ["MESSAGE_DELAY"] = str(message_delay)
    if sender_switch_delay is not None:
        os.environ["SENDER_SWITCH_DELAY"] = str(sender_switch_delay)
    if campaign_delay is not None:
        os.environ["CAMPAIGN_DELAY"] = str(campaign_delay)
    return {
        "MESSAGE_DELAY": int(os.getenv("MESSAGE_DELAY", 2)),
        "SENDER_SWITCH_DELAY": int(os.getenv("SENDER_SWITCH_DELAY", 5)),
        "CAMPAIGN_DELAY": int(os.getenv("CAMPAIGN_DELAY", 10))
    }

@router.get("/user-delays", include_in_schema=True)
def get_user_delays(sender_number: str = Query(...)):
    """Get message and sender switch delays per user """
    sender_number = normalize_number(sender_number)
    with _user_delays_lock:
        user_delay = _user_delays.get(sender_number)
    if user_delay:
        # Ensure campaign delay is present (fallback to global if missing)
        if "CAMPAIGN_DELAY" not in user_delay:
            user_delay["CAMPAIGN_DELAY"] = int(os.getenv("CAMPAIGN_DELAY", 10))
        # Remove sender switch delay from user response
        user_delay.pop("SENDER_SWITCH_DELAY", None)
        return user_delay
    # Fallback to global
    return {
        "MESSAGE_DELAY": int(os.getenv("MESSAGE_DELAY", 2)),
        "CAMPAIGN_DELAY": int(os.getenv("CAMPAIGN_DELAY", 10))
    }

@router.post("/user-delays", include_in_schema=True)
def set_user_delays(
    sender_number: str = Query(...),
    message_delay: int = None,
    campaign_delay: int = None
):
    """Set message and sender switch delays for a specific user"""
    sender_number = normalize_number(sender_number)
    with _user_delays_lock:
        user_delay = _user_delays.get(sender_number) or {
            "MESSAGE_DELAY": int(os.getenv("MESSAGE_DELAY", 2)),
            "CAMPAIGN_DELAY": int(os.getenv("CAMPAIGN_DELAY", 10))
        }
        if message_delay is not None:
            user_delay["MESSAGE_DELAY"] = message_delay
        if campaign_delay is not None:
            user_delay["CAMPAIGN_DELAY"] = campaign_delay
        _user_delays[sender_number] = user_delay
    return user_delay
