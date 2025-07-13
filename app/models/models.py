from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Enum, Boolean
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
    waha_message_id = Column(String, nullable=True, index=True)  # Add this line
    
    campaign = relationship("Campaign", back_populates="messages")

class Session(Base):
    __tablename__ = "sessions"

    phone_number = Column(String, primary_key=True, index=True)
    status = Column(String)
    last_active = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON)

class Worker(Base):
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, unique=True, nullable=False)
    api_key = Column(String, nullable=False)
    capacity = Column(Integer, default=10)  # Max sessions this worker can handle
    name = Column(String, nullable=True)
    is_healthy = Column(Boolean, nullable=False, default=True)  # Indicates if the worker is healthy and available

    sessions = relationship("WAHASession", back_populates="worker")

class WAHASession(Base):
    __tablename__ = "waha_sessions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=True)
    user_id = Column(String, nullable=False)  # Reference to User (string for flexibility)
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False)
    phone_number = Column(String, nullable=False, index=True)
    status = Column(String, nullable=True)
    last_active = Column(DateTime, default=datetime.utcnow)
    data = Column(JSON)

    worker = relationship("Worker", back_populates="sessions")
