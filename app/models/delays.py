from sqlalchemy import Column, Integer
from app.db.database import Base

import os
class DelayConfig(Base):
    __tablename__ = "delays"
    id = Column(Integer, primary_key=True, index=True)
    message_delay = Column(Integer, default=int(os.getenv("MESSAGE_DELAY", 2)))
    sender_switch_delay = Column(Integer, default=int(os.getenv("SENDER_SWITCH_DELAY", 5)))
    campaign_delay = Column(Integer, default=int(os.getenv("CAMPAIGN_DELAY", 10)))
