from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, JSON
from sqlalchemy.sql import func
from app.db.base import Base
import enum

class MessageStatus(enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"

class MessageQueue(Base):
    __tablename__ = "message_queue"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_number = Column(String, nullable=False)
    recipient = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    media_url = Column(Text, nullable=True)
    variables = Column(JSON, nullable=True)  # For template variables
    status = Column(String, default="PENDING", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    error = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
