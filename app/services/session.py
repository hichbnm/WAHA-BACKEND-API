from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models.models import Session
from datetime import datetime
import os
import logging
from dotenv import load_dotenv
from app.services.waha_session import WAHASessionService

load_dotenv()

class SessionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_session_status(self, phone_number: str):
        try:
            # Check local database first
            result = await self.db.execute(
                select(Session).where(Session.phone_number == phone_number)
            )
            session = result.scalar_one_or_none()
            
            if not session:
                # Get correct worker for this phone_number
                waha_service = WAHASessionService(self.db)
                waha_url, api_key = await waha_service._get_worker_for_session(phone_number)
                # Check WAHA API
                import aiohttp
                headers = {"X-Api-Key": api_key}
                async with aiohttp.ClientSession() as client:
                    async with client.get(f"{waha_url}/api/sessions/{phone_number}", headers=headers) as response:
                        if response.status == 200:
                            data = await response.json()
                            # Create new session record
                            session = Session(
                                phone_number=phone_number,
                                status=data.get("status", "unknown"),
                                last_active=datetime.utcnow(),
                                data=data
                            )
                            self.db.add(session)
                            await self.db.commit()
                        else:
                            return None

            return session
        except Exception as e:
            logging.error(f"Error getting session status: {str(e)}")
            raise

    async def delete_session(self, phone_number: str):
        try:
            # Get correct worker for this phone_number
            waha_service = WAHASessionService(self.db)
            waha_url, api_key = await waha_service._get_worker_for_session(phone_number)
            # Delete from WAHA first
            import aiohttp
            headers = {"X-Api-Key": api_key}
            async with aiohttp.ClientSession() as client:
                async with client.delete(f"{waha_url}/api/sessions/{phone_number}", headers=headers) as response:
                    if response.status not in (200, 404):
                        return False

            # Delete from local database
            result = await self.db.execute(
                select(Session).where(Session.phone_number == phone_number)
            )
            session = result.scalar_one_or_none()
            if session:
                await self.db.delete(session)
                await self.db.commit()
                return True

            return False
        except Exception as e:
            logging.error(f"Error deleting session: {str(e)}")
            raise
