from datetime import datetime
from sqlalchemy import update
from app.models.models import Session, WAHASession

async def update_last_active(db, phone_number: str):
    """Update last_active for both Session and WAHASession tables."""
    await db.execute(
        update(Session)
        .where(Session.phone_number == phone_number)
        .values(last_active=datetime.utcnow())
    )
    await db.execute(
        update(WAHASession)
        .where(WAHASession.phone_number == phone_number)
        .values(last_active=datetime.utcnow())
    )
    await db.commit()
