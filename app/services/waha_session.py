from sqlalchemy.ext.asyncio import AsyncSession
import aiohttp
import json
import os
from datetime import datetime, timedelta
from app.models.models import Session
from typing import Optional, Dict, Any
import logging
import base64

class WAHASessionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.waha_url = f"{os.getenv('WAHA_HOST')}:{os.getenv('WAHA_PORT')}"
        self.api_key = os.getenv('WAHA_API_KEY')
        if not self.api_key:
            raise ValueError("WAHA_API_KEY environment variable is not set")

    async def _make_waha_request(self, endpoint: str, method: str = "GET", data: Dict = None, expect_json: bool = True) -> Any:
        """Make a request to WAHA API"""
        url = f"{self.waha_url}/api/{endpoint}"
        headers = {"X-Api-Key": self.api_key}
        
        async with aiohttp.ClientSession() as session:
            try:
                if method == "GET":
                    async with session.get(url, headers=headers) as response:
                        if response.status == 401:
                            raise ValueError("Unauthorized: Invalid WAHA API key")
                        response.raise_for_status()
                        
                        if expect_json:
                            return await response.json()
                        else:
                            # Return raw response content for non-JSON responses
                            return await response.read()
                            
                elif method == "POST":
                    async with session.post(url, json=data, headers=headers) as response:
                        if response.status == 401:
                            raise ValueError("Unauthorized: Invalid WAHA API key")
                        response.raise_for_status()
                        return await response.json()
            except aiohttp.ClientError as e:
                logging.error(f"WAHA API request failed: {str(e)}")
                raise ValueError(f"Failed to connect to WAHA API: {str(e)}")
            except json.JSONDecodeError as e:
                if expect_json:
                    logging.error(f"Failed to decode WAHA API response: {str(e)}")
                    raise ValueError("Invalid response from WAHA API")
                return None

    async def start_session(self, phone_number: str) -> Dict[str, Any]:
        """Start a new WhatsApp session"""
        try:
            # Remove '+' prefix if present
            clean_number = phone_number.replace('+', '')
            response = None
            
            try:
                # First create a new session
                create_response = await self._make_waha_request(
                    "sessions",
                    method="POST",
                    data={
                        "name": clean_number,
                        "config": {
                            "proxy": None,
                            "webhookUrl": f"{os.getenv('API_HOST')}:{os.getenv('API_PORT')}/api/webhook"
                        }
                    }
                )
                logging.info(f"Created session for {clean_number}")
                
                # Then start it
                start_response = await self._make_waha_request(
                    f"sessions/{clean_number}/start",
                    method="POST"
                )
                logging.info(f"Started session for {clean_number}")
                
                response = {**create_response, **start_response}
            except ValueError as e:
                # If session already exists, try to start it directly
                if "422" in str(e):
                    start_response = await self._make_waha_request(
                        f"sessions/{clean_number}/start",
                        method="POST"
                    )
                    response = start_response
                    logging.info(f"Started existing session for {clean_number}")
                else:
                    raise
            
            if not response:
                raise ValueError("Failed to start session: No response from WAHA API")

            if response.get("error"):
                raise ValueError(f"Failed to start session: {response['error']}")

            # Update existing session or create new one
            from sqlalchemy import select
            from sqlalchemy.dialects.postgresql import insert
            
            # Try to get existing session
            stmt = select(Session).where(Session.phone_number == clean_number)
            result = await self.db.execute(stmt)
            session = result.scalar_one_or_none()
            
            if session:
                # Update existing session
                session.status = "STARTING"
                session.last_active = datetime.utcnow()
                session.data = response
            else:
                # Create new session
                session = Session(
                    phone_number=clean_number,
                    status="STARTING",
                    last_active=datetime.utcnow(),
                    data=response
                )
                self.db.add(session)
            
            await self.db.commit()

            status = response.get("status", "STARTING")
            return {
                "status": status,
                "message": f"Session {status.lower()}",
                "phone_number": clean_number,
                "last_active": datetime.utcnow(),
                "data": response
            }
        except Exception as e:
            logging.error(f"Error starting session: {str(e)}")
            await self.db.rollback()
            raise
        except Exception as e:
            logging.error(f"Error starting session: {str(e)}")
            await self.db.rollback()
            raise

    async def get_qr_code(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """Get QR code for WhatsApp Web authentication"""
        try:
            # Remove '+' prefix if present
            clean_number = phone_number.replace('+', '')
            
            # Get session state first
            state = await self._make_waha_request(f"sessions/{clean_number}")
            if state.get("error"):
                raise ValueError(f"Session not found: {state['error']}")
            
            if state.get("status") == "CONNECTED":
                return {
                    "status": "CONNECTED",
                    "message": "Session is already connected"
                }

            # Get QR code from WAHA
            qr_image = await self._make_waha_request(f"{clean_number}/auth/qr", expect_json=False)
            if not qr_image:
                raise ValueError("Failed to get QR code")

            # Convert the image to base64
            qr_base64 = base64.b64encode(qr_image).decode('utf-8')

            return {
                "status": state.get("status", "UNKNOWN"),
                "qr_code": qr_base64,  # Base64 encoded QR code image
                "expires_at": datetime.utcnow() + timedelta(minutes=5)  # QR codes expire in ~5 minutes
            }
        except Exception as e:
            logging.error(f"Error getting QR code: {str(e)}")
            raise

    async def check_session_status(self, phone_number: str) -> Dict[str, Any]:
        """Check the status of a WhatsApp session"""
        try:
            # First check if session exists in WAHA
            response = await self._make_waha_request(f"sessions/{phone_number}")
            if response.get("error"):
                raise ValueError(f"Failed to check session status: {response['error']}")
                
            # Then get detailed status (FIX: remove /status)
            status_response = await self._make_waha_request(f"sessions/{phone_number}")
            response.update(status_response or {})
            
            # Update session in database
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            
            if session:
                session.status = response.get("status", "UNKNOWN")
                session.last_active = datetime.utcnow()
                session.data = response
                await self.db.commit()

            return {
                "status": response.get("status", "UNKNOWN"),
                "message": "Session status retrieved",
                "session_id": phone_number,
                **response  # Include all WAHA status details
            }
        except Exception as e:
            logging.error(f"Error checking session status: {str(e)}")
            await self.db.rollback()
            raise

    async def keep_session_alive(self, phone_number: str) -> bool:
        """Keep a session alive by sending a ping"""
        try:
            # Send a ping to keep session alive
            response = await self._make_waha_request(f"ping/{phone_number}")
            
            if response.get("success"):
                # Update last_active timestamp
                from sqlalchemy import select
                query = select(Session).where(Session.phone_number == phone_number)
                result = await self.db.execute(query)
                session = result.scalar_one_or_none()
                
                if session:
                    session.last_active = datetime.utcnow()
                    await self.db.commit()
                return True
            return False
        except Exception as e:
            logging.error(f"Error keeping session alive: {str(e)}")
            return False

    async def monitor_sessions(self):
        """Monitor and maintain active sessions"""
        try:
            # Get all sessions that need attention (last active > 10 days)
            from sqlalchemy import select
            query = select(Session).where(
                Session.status == 'CONNECTED',
                Session.last_active < datetime.utcnow() - timedelta(days=10)
            )
            result = await self.db.execute(query)
            sessions = result.scalars().all()
            
            for session in sessions:
                # Try to keep session alive
                await self.keep_session_alive(session.phone_number)
                
        except Exception as e:
            logging.error(f"Error monitoring sessions: {str(e)}")
            raise

    async def get_session_info(self, phone_number: str) -> Dict[str, Any]:
        """Get detailed information about a session"""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}")
            if response.get("error"):
                raise ValueError(f"Session not found: {response['error']}")
            return response
        except Exception as e:
            logging.error(f"Error getting session info: {str(e)}")
            raise ValueError(f"Failed to get session info: {str(e)}")

    async def stop_session(self, phone_number: str) -> Dict[str, Any]:
        """Stop a WhatsApp session"""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}/stop", method="POST")
            if response.get("error"):
                raise ValueError(f"Failed to stop session: {response['error']}")
            
            # Update session status in database
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            
            if session:
                session.status = "STOPPED"
                session.last_active = datetime.utcnow()
                await self.db.commit()
            
            return {"status": "STOPPED", "message": "Session stopped successfully"}
        except Exception as e:
            logging.error(f"Error stopping session: {str(e)}")
            await self.db.rollback()
            raise ValueError(f"Failed to stop session: {str(e)}")

    async def logout_session(self, phone_number: str) -> Dict[str, Any]:
        """Logout from WhatsApp Web"""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}/logout", method="POST")
            if response.get("error"):
                raise ValueError(f"Failed to logout session: {response['error']}")
            
            # Update session status in database
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            
            if session:
                session.status = "LOGGED_OUT"
                session.last_active = datetime.utcnow()
                await self.db.commit()
            
            return {"status": "LOGGED_OUT", "message": "Session logged out successfully"}
        except Exception as e:
            logging.error(f"Error logging out session: {str(e)}")
            await self.db.rollback()
            raise ValueError(f"Failed to logout session: {str(e)}")

    async def delete_session(self, phone_number: str) -> Dict[str, Any]:
        """Delete a WhatsApp session"""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}", method="DELETE")
            if response.get("error"):
                raise ValueError(f"Failed to delete session: {response['error']}")
            
            # Delete session from database
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            
            if session:
                await self.db.delete(session)
                await self.db.commit()
            
            return {"status": "DELETED", "message": "Session deleted successfully"}
        except Exception as e:
            logging.error(f"Error deleting session: {str(e)}")
            await self.db.rollback()
            raise ValueError(f"Failed to delete session: {str(e)}")

    async def health_check(self) -> Dict[str, Any]:
        """Check WAHA API health status"""
        try:
            response = await self._make_waha_request("status")
            return {
                "status": "HEALTHY" if response.get("success") else "UNHEALTHY",
                "message": "WAHA API is running" if response.get("success") else "WAHA API is not responding",
                "details": response
            }
        except Exception as e:
            logging.error(f"WAHA API health check failed: {str(e)}")
            return {
                "status": "UNHEALTHY",
                "message": f"WAHA API health check failed: {str(e)}",
                "details": None
            }

    async def get_me_info(self, phone_number: str) -> Dict[str, Any]:
        """Get information about the authenticated WhatsApp account"""
        try:
            # First verify the session exists and is connected
            session_info = await self._make_waha_request(f"sessions/{phone_number}")
            if session_info.get("error"):
                raise ValueError(f"Session not found: {session_info['error']}")
            
            valid_states = ["CONNECTED", "WORKING"]
            if session_info.get("status") not in valid_states:
                raise ValueError(f"Session is not connected. Current status: {session_info.get('status')}")

            # Get the me info from WAHA
            response = await self._make_waha_request(f"sessions/{phone_number}/me")
            if response.get("error"):
                raise ValueError(f"Failed to get account info: {response['error']}")

            # Update session in database with latest info
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            
            if session:
                session.last_active = datetime.utcnow()
                session.data = {**session.data, "me": response} if session.data else {"me": response}
                await self.db.commit()

            return {
                "id": response.get("id", ""),
                "pushname": response.get("pushname"),
                "number": phone_number,
                "platform": response.get("platform"),
                "connected": True,
                "me": response
            }
        except Exception as e:
            logging.error(f"Error getting account info for {phone_number}: {str(e)}")
            await self.db.rollback()
            raise ValueError(f"Failed to get account info: {str(e)}")

    async def list_sessions(self):
        """List all WAHA sessions"""
        try:
            response = await self._make_waha_request("sessions")
            # Ensure response is a list
            if isinstance(response, dict):
                return list(response.values())
            return response
        except Exception as e:
            logging.error(f"Error listing sessions: {str(e)}")
            raise
