from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Enum
from sqlalchemy.orm import relationship
from app.db.database import Base
import enum
from datetime import datetime

class MessageStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DELIVERED = "delivered"

class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    sender_number = Column(String, index=True)
    template = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String)
    total_messages = Column(Integer)
    sent_messages = Column(Integer, default=0)
    failed_messages = Column(Integer, default=0)
    variables = Column(JSON)
    media_url = Column(String, nullable=True)

    messages = relationship("Message", back_populates="campaign")

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    recipient = Column(String)
    status = Column(String, default=MessageStatus.PENDING)
    error = Column(String, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    
    campaign = relationship("Campaign", back_populates="messages")

class Session(Base):
    __tablename__ = "sessions"

    phone_number = Column(String, primary_key=True, index=True)
    status = Column(String)
    last_active = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON)
