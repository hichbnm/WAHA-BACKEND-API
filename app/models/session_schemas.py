from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class QRCodeResponse(BaseModel):
    status: str
    qr_code: Optional[str]  # Base64 encoded QR code image
    expires_at: datetime
    session_id: Optional[str]

class SessionStatusResponse(BaseModel):
    status: str
    message: Optional[str]
    last_active: Optional[datetime]

    class Config:
        from_attributes = True
