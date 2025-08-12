from sqlalchemy import Column, Integer
from app.db.base import Base

import os

def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or str(val).strip() == "":
        return default
    try:
        # Strip inline comments (everything after a #)
        cleaned = str(val).split('#', 1)[0].strip()
        return int(cleaned)
    except Exception:
        # Fallback to default if invalid
        return default

class DelayConfig(Base):
    __tablename__ = "delays"
    id = Column(Integer, primary_key=True, index=True)
    message_delay = Column(Integer, default=_env_int("MESSAGE_DELAY", 2))
    sender_switch_delay = Column(Integer, default=_env_int("SENDER_SWITCH_DELAY", 5))
    campaign_delay = Column(Integer, default=_env_int("CAMPAIGN_DELAY", 10))
