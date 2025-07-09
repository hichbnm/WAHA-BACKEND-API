from sqlalchemy.ext.asyncio import AsyncSession
import aiohttp
import json
import os
from datetime import datetime, timedelta
from app.models.models import Session
from typing import Optional, Dict, Any
import logging
import base64
from collections import deque, defaultdict
import itertools
import asyncio
from fastapi import HTTPException

class WAHASessionService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.waha_url = f"{os.getenv('WAHA_HOST')}:{os.getenv('WAHA_PORT')}"
        self.api_key = os.getenv('WAHA_API_KEY')
        if not self.api_key:
            raise ValueError("WAHA_API_KEY environment variable is not set")
        # Round-robin queue: user_id -> deque of (phone_number, future)
        self.session_start_queues = defaultdict(deque)
        self.queue_users = deque()  # Keeps round-robin order of users with pending requests
        self.queue_lock = asyncio.Lock()
        self.processing_queue = False

    async def _make_waha_request(self, endpoint: str, method: str = "GET", data: Dict = None, expect_json: bool = True) -> Any:
        """Make a request to WAHA API"""
        url = f"{self.waha_url}/api/{endpoint}"
        headers = {"X-Api-Key": self.api_key}
        logging.info(f"WAHA REQUEST: {method} {url} data={data}")  # <--- Add this line for debug
        
        async with aiohttp.ClientSession() as session:
            try:
                if method == "GET":
                    async with session.get(url, headers=headers) as response:
                        if response.status == 401:
                            raise ValueError("Unauthorized: Invalid WAHA API key")
                        if response.status == 404:
                            # Do not log as error, let caller handle
                            raise ValueError("Not Found")
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
                elif method == "DELETE":
                    async with session.delete(url, headers=headers) as response:
                        if response.status == 401:
                            raise ValueError("Unauthorized: Invalid WAHA API key")
                        response.raise_for_status()
                        # PATCH: Handle empty or non-JSON response gracefully
                        content_type = response.headers.get('Content-Type', '')
                        text = await response.text()
                        if not text.strip():
                            return {"success": True, "message": "Session deleted (empty response)"}
                        if 'application/json' in content_type:
                            return await response.json()
                        else:
                            return {"success": True, "message": text}
            except aiohttp.ClientError as e:
                logging.error(f"WAHA API request failed: {str(e)}")
                raise ValueError(f"Failed to connect to WAHA API: {str(e)}")
            except json.JSONDecodeError as e:
                if expect_json:
                    logging.error(f"Failed to decode WAHA API response: {str(e)}")
                    raise ValueError("Invalid response from WAHA API")
                return None

    async def start_session(self, phone_number: str, user_id: str = None) -> Dict[str, Any]:
        """Start a new WhatsApp session, enforcing max live session limit and round-robin queueing by user"""
        from app.config import settings
        from sqlalchemy import select
        # Count live sessions (CONNECTED or WORKING)
        stmt = select(Session).where(Session.status.in_(["CONNECTED", "WORKING"]))
        result = await self.db.execute(stmt)
        live_sessions = result.scalars().all()
        max_live = int(os.getenv("MAX_LIVE_SESSIONS", 100))
        if len(live_sessions) >= max_live:
            # Queue the request by user and return queued status
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            user_key = user_id or "anonymous"
            async with self.queue_lock:
                self.session_start_queues[user_key].append((phone_number, future))
                if user_key not in self.queue_users:
                    self.queue_users.append(user_key)
                if not self.processing_queue:
                    loop.create_task(self._process_session_queue())
                    self.processing_queue = True
            return {
                "phone_number": phone_number,
                "status": "QUEUED",
                "message": f"Session start request queued . Will be processed when a slot is available.",
                "last_active": None,
                "data": None
            }

        try:
            # Remove '+' prefix if present
            clean_number = phone_number.replace('+', '')
            response = None

            # First, check if the session already exists in WAHA
            try:
                session_check = await self._make_waha_request(f"sessions/{clean_number}")
                session_exists = session_check and not session_check.get("error")
            except Exception as e:
                # Suppress/log 404 as info, not error
                if "404" in str(e) or "Not Found" in str(e):
                    logging.info(f"WAHA session {clean_number} does not exist yet (404). Proceeding to create.")
                    session_exists = False
                else:
                    logging.error(f"Error checking WAHA session existence: {e}")
                    session_exists = False

            if not session_exists:
                # Create a new session if it does not exist
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
            else:
                logging.info(f"Session for {clean_number} already exists in WAHA")

            # Start the session (always safe to call)
            start_response = await self._make_waha_request(
                f"sessions/{clean_number}/start",
                method="POST"
            )
            logging.info(f"Started session for {clean_number}")
            response = start_response

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
                # Always reset status to 'STARTING' when (re)starting a session
                session.status = "STARTING"
                session.last_active = datetime.utcnow()
                session.data = response
                await self.db.commit()
                logging.debug(f"[start_session] Set status=STARTING, last_active={session.last_active} for {clean_number}")
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
                logging.debug(f"[start_session] Created new session with status=STARTING, last_active={session.last_active} for {clean_number}")

            # Poll WAHA for status after starting session
            poll_delay = 2  # seconds
            latest_status = response.get("status", "STARTING").upper()
            attempt = 1
            while True:
                await asyncio.sleep(poll_delay)
                try:
                    session_info = await self.get_session_info(clean_number)
                    polled_status = session_info.get("status", "UNKNOWN").upper()
                    logging.info(f"Polling attempt {attempt}: WAHA status for {clean_number} is '{polled_status}'")
                    if polled_status not in ("STARTING", "UNKNOWN", "SCAN_QR_CODE"):
                        # Update DB with new status if changed
                        latest_status = polled_status
                        if session.status != latest_status:
                            session.status = latest_status
                            session.last_active = datetime.utcnow()
                            await self.db.commit()
                            logging.debug(f"[start_session] Polled status transition: {clean_number} -> {latest_status}, last_active={session.last_active}")
                        break
                except Exception as poll_e:
                    logging.warning(f"Polling WAHA for session status failed: {poll_e}")
                attempt += 1
            return {
                "status": latest_status,
                "message": f"Session {latest_status}",
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

    async def _process_session_queue(self):
        """Process queued session start requests in round-robin by user"""
        from sqlalchemy import select
        from app.db.database import async_session
        import logging
        logging.info("[QUEUE] Session queue processor started.")
        while self.queue_users:
            async with self.queue_lock:
                # Find next user with a pending request
                for _ in range(len(self.queue_users)):
                    user_key = self.queue_users.popleft()
                    if self.session_start_queues[user_key]:
                        phone_number, future = self.session_start_queues[user_key].popleft()
                        # If user still has requests, re-append to round-robin
                        if self.session_start_queues[user_key]:
                            self.queue_users.append(user_key)
                        else:
                            del self.session_start_queues[user_key]
                        break
                    else:
                        del self.session_start_queues[user_key]
                else:
                    # No pending requests
                    break
            # Use a fresh DB session for each queue operation
            async with async_session() as session:
                stmt = select(Session).where(Session.status.in_(["STARTING", "CONNECTED", "WORKING"]))
                result = await session.execute(stmt)
                live_sessions = result.scalars().all()
                max_live = int(os.getenv("MAX_LIVE_SESSIONS", 100))
                logging.info(f"[QUEUE] Live sessions: {len(live_sessions)}/{max_live} (processing {phone_number})")
                if len(live_sessions) < max_live:
                    # Slot available, start session
                    try:
                        waha_service = WAHASessionService(session)
                        session_info = await waha_service.start_session(phone_number, user_id=user_key)
                        logging.info(f"[QUEUE] Started session for {phone_number} (user {user_key})")
                        if not future.done():
                            future.set_result(session_info)
                    except Exception as e:
                        logging.error(f"[QUEUE] Failed to start session for {phone_number}: {e}")
                        if not future.done():
                            future.set_exception(e)
                else:
                    # No slot, requeue and wait
                    logging.info(f"[QUEUE] No slot for {phone_number}, requeuing.")
                    async with self.queue_lock:
                        self.session_start_queues[user_key].appendleft((phone_number, future))
                        if user_key not in self.queue_users:
                            self.queue_users.appendleft(user_key)
        logging.info("[QUEUE] Session queue processor finished.")
        self.processing_queue = False

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
        """Keep a session alive by checking its status (no ping endpoint in WAHA)"""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}")
            if response.get("status") in ("WORKING", "CONNECTED"):
                # Only update last_active if session is truly active
                from sqlalchemy import select
                query = select(Session).where(Session.phone_number == phone_number)
                result = await self.db.execute(query)
                session = result.scalar_one_or_none()
                if session:
                    old_last_active = session.last_active
                    session.last_active = datetime.utcnow()
                    await self.db.commit()
                    logging.debug(f"[keep_session_alive] Refreshed last_active for {phone_number}: {old_last_active} -> {session.last_active}")
                return True
            return False
        except Exception as e:
            logging.error(f"Error keeping session alive: {str(e)}")
            return False

    async def monitor_sessions(self):
        """Monitor and maintain active sessions, auto-stop if lifetime exceeded"""
        try:
            from sqlalchemy import select
            import os
            session_lifetime = int(os.getenv("SESSION_LIFETIME_SECONDS", 0))
            now = datetime.utcnow()
            # Get all sessions that are CONNECTED or WORKING
            query = select(Session).where(Session.status.in_(["CONNECTED", "WORKING"]))
            result = await self.db.execute(query)
            sessions = result.scalars().all()
            for session in sessions:
                # Auto-stop if lifetime exceeded
                if session_lifetime > 0 and (now - session.last_active).total_seconds() > session_lifetime:
                    logging.info(f"Auto-stopping session {session.phone_number} (lifetime exceeded)")
                    await self.stop_session(session.phone_number)
                else:
                    # Try to keep session alive if not expired
                    await self.keep_session_alive(session.phone_number)
        except Exception as e:
            logging.error(f"Error monitoring sessions: {str(e)}")
            raise

    async def get_session_info(self, phone_number: str) -> Dict[str, Any]:
        """Get detailed information about a session and update DB status if changed"""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}")
            if response.get("error"):
                raise ValueError(f"Session not found: {response['error']}")
            # Update DB if status has changed
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            new_status = response.get("status", "UNKNOWN")
            if session and session.status != new_status:
                old_status = session.status
                session.status = new_status
                session.last_active = datetime.utcnow()
                session.data = response
                await self.db.commit()
                logging.debug(f"[get_session_info] Status changed for {phone_number}: {old_status} -> {new_status}, last_active={session.last_active}")
            return {
                "phone_number": phone_number,
                "status": new_status,
                "message": "OK",
                "last_active": session.last_active if session else None,
                "data": response
            }
        except Exception as e:
            logging.error(f"Error getting session info: {str(e)}")
            raise ValueError(f"Failed to get session info: {str(e)}")

    async def stop_session(self, phone_number: str) -> Dict[str, Any]:
        """Stop a WhatsApp session. Always fetch the Session ORM object inside this method's async context."""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}/stop", method="POST")
            if response.get("error"):
                raise ValueError(f"Failed to stop session: {response['error']}")
            # Always fetch the session ORM object here, never pass it in from outside
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            # Removed invalid hash_key check
            if session:
                session.status = "STOPPED"
                session.last_active = datetime.utcnow()
                await self.db.commit()
                logging.debug(f"[stop_session] Set status=STOPPED, last_active={session.last_active} for {phone_number}")
            return {
                "phone_number": phone_number,
                "status": "STOPPED",
                "message": "Session stopped successfully",
                "last_active": session.last_active if session else None,
                "data": None
            }
        except Exception as e:
            if "greenlet_spawn has not been called" in str(e):
                logging.info(f"Session {phone_number} already stopped or context closed, ")
            else:
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
                logging.debug(f"[logout_session] Set status=LOGGED_OUT, last_active={session.last_active} for {phone_number}")
            return {
                "phone_number": phone_number,
                "status": "LOGGED_OUT",
                "message": "Session logged out successfully",
                "last_active": session.last_active if session else None,
                "data": None
            }
        except Exception as e:
            logging.error(f"Error logging out session: {str(e)}")
            await self.db.rollback()
            raise ValueError(f"Failed to logout session: {str(e)}")

    async def delete_session(self, phone_number: str) -> Dict[str, Any]:
        """Delete a WhatsApp session"""
        try:
            response = await self._make_waha_request(f"sessions/{phone_number}", method="DELETE")
            # Always try to delete from database, regardless of WAHA response
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            if session:
                await self.db.delete(session)
                await self.db.commit()
            if response is None:
                raise HTTPException(status_code=404, detail="WAHA API did not respond or session does not exist.")
            if isinstance(response, dict) and response.get("error"):
                raise HTTPException(status_code=400, detail=f"Failed to delete session: {response['error']}")
            return {
                "phone_number": phone_number,
                "status": "DELETED",
                "message": "Session deleted successfully",
                "last_active": None,
                "data": None
            }
        except HTTPException as e:
            logging.error(f"Error deleting session: {e.detail}")
            raise
        except Exception as e:
            logging.error(f"Error deleting session: {str(e)}")
            await self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to delete session: {str(e)}")

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
