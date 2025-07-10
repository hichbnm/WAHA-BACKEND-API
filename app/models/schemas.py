from typing import Optional, List, Dict, Any
from pydantic import BaseModel, model_validator, Field
from datetime import datetime
import re

# Phone number validation pattern
PHONE_PATTERN = r'^\+?[1-9]\d{1,14}$'

def validate_phone(phone: str) -> str:
    if not re.match(PHONE_PATTERN, phone):
        raise ValueError('Invalid phone number format')
    return phone

class MessageBase(BaseModel):
    recipient: str
    message: str
    media_url: Optional[str] = None

class MessageCreate(MessageBase):
    pass

class Message(MessageBase):
    id: int
    campaign_id: int
    status: str
    error: Optional[str] = None
    sent_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class CampaignBase(BaseModel):
    sender_number: str
    template: str
    recipients: List[str]
    variables: Optional[Dict[str, Any]] = None
    media_url: Optional[str] = None

class CampaignCreate(CampaignBase):
    pass

class CampaignResponse(BaseModel):
    id: int
    status: str
    total_messages: int
    created_at: datetime

    class Config:
        from_attributes = True

class CampaignStatus(BaseModel):
    id: int
    status: str
    total_messages: int
    sent_messages: int
    failed_messages: int
    created_at: datetime
    messages: Optional[List[Message]] = None

    class Config:
        from_attributes = True

class CampaignList(BaseModel):
    id: int
    sender_number: str
    status: str
    total_messages: int
    sent_messages: int
    failed_messages: int
    created_at: datetime

    class Config:
        from_attributes = True

class SystemMetrics(BaseModel):
    active_sessions: int
    messages_sent_today: int
    current_queue_size: int
    server_uptime: float
    total_campaigns: int
    total_users: int

class UserStats(BaseModel):
    """User statistics for admin view"""
    phone_number: str
    total_campaigns: int
    total_messages: int
    sent_messages: int
    failed_messages: int
    active_session: bool
    last_active: Optional[datetime] = None
    last_campaign: Optional[datetime] = None

    class Config:
        from_attributes = True

class SessionStatus(BaseModel):
    status: str
    last_active: datetime
    requires_auth: bool
    qr_code: Optional[str] = None
    qr_expiry: Optional[datetime] = None

class WebhookEvent(BaseModel):
    event: str
    data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class CampaignStats(BaseModel):
    """Detailed campaign statistics for admin view"""
    campaign_status: Dict[str, int]  # Count of campaigns by status
    message_status: Dict[str, int]  # Count of messages by status
    recent_campaigns: List[CampaignList]
    last_refresh: datetime

    class Config:
        from_attributes = True

class HealthCheckResponse(BaseModel):
    status: str = Field(..., description="Current health status of the WAHA API (HEALTHY/UNHEALTHY)")
    message: str = Field(..., description="Detailed status message")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional health check details")

class SessionInfo(BaseModel):
    phone_number: str = Field(..., description="The phone number associated with the session")
    status: str = Field(..., description="Current session status")
    message: Optional[str] = Field(None, description="Additional status message")
    last_active: Optional[datetime] = None
    data: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True

class SessionResponse(BaseModel):
    """Response model for session operations"""
    status: str = Field(..., description="Current session status. One of: WAITING_FOR_SCAN, SCANNED, CONNECTED, EXPIRED, FAILED, REQUIRES_AUTH, UNKNOWN")
    message: str = Field(..., description="Status message or error details")
    phone_number: str = Field(..., description="The phone number associated with the session")
    last_active: Optional[datetime] = Field(None, description="Last time the session was active (UTC)")
    expires_at: Optional[datetime] = Field(None, description="Session expiry time (UTC), if known")
    requires_auth: Optional[bool] = Field(None, description="True if session requires re-authentication")
    data: Optional[Dict[str, Any]] = Field(None, description="Raw session data from WAHA or DB")

    class Config:
        from_attributes = True

class SessionCreateRequest(BaseModel):
    """Request model for creating a session"""
    phone_number: str = Field(..., description="Phone number to create session for")

    @model_validator(mode='before')
    def validate_phone(cls, values):
        if isinstance(values, dict) and 'phone_number' in values:
            phone = values['phone_number']
            if not re.match(PHONE_PATTERN, phone):
                raise ValueError('Invalid phone number format')
        return values

class QRCodeResponse(BaseModel):
    """Response model for QR code requests"""
    status: str = Field(..., description="Current session status")
    message: Optional[str] = Field(None, description="Status message")
    qr_code: Optional[str] = Field(None, description="Base64 encoded QR code image")
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class MeInfo(BaseModel):
    id: str = Field(..., description="WhatsApp ID")
    pushname: Optional[str] = Field(None, description="WhatsApp display name")
    number: str = Field(..., description="WhatsApp phone number")
    platform: Optional[str] = Field(None, description="Platform information")
    connected: bool = Field(..., description="Connection status")
    me: Optional[Dict[str, Any]] = Field(None, description="Additional account information")

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

class WorkerCreate(BaseModel):
    url: str
    api_key: str
    capacity: int = 10
    name: str | None = None

class WorkerResponse(BaseModel):
    id: int
    url: str
    api_key: str
    capacity: int
    name: str | None = None

    class Config:
        from_attributes = True

class WorkerUpdate(BaseModel):
    url: str | None = None
    api_key: str | None = None
    capacity: int | None = None
    name: str | None = None

class SessionNumbersResponse(BaseModel):
    count: int
    numbers: List[str]

class WorkerHealthUpdate(BaseModel):
    is_healthy: bool
