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

    async def _make_waha_request(self, endpoint: str, method: str = "GET", data: Dict = None, expect_json: bool = True, waha_url: str = None, api_key: str = None) -> Any:
        """Make a request to WAHA API"""
        url = f"{(waha_url or self.waha_url)}/api/{endpoint}"
        headers = {"X-Api-Key": api_key or self.api_key}
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
        """Start a new WhatsApp session, enforcing max live session limit and round-robin queueing by user, and assign to a worker."""
        from app.config import settings
        from sqlalchemy import select
        from app.services.worker import WorkerService
        from app.models.models import WAHASession
        # Remove global live session count check here
        try:
            # Remove '+' prefix if present
            clean_number = phone_number.replace('+', '')
            response = None

            # --- SHARDING LOGIC: Use existing worker if WAHASession exists ---
            wahasession_stmt = select(WAHASession).where(WAHASession.phone_number == clean_number)
            wahasession_result = await self.db.execute(wahasession_stmt)
            wahasession = wahasession_result.scalar_one_or_none()
            if wahasession:
                # Use the same worker as before
                from app.models.models import Worker
                worker_stmt = select(Worker).where(Worker.id == wahasession.worker_id)
                worker_result = await self.db.execute(worker_stmt)
                best_worker = worker_result.scalar_one_or_none()
                # --- FIX: Only use previous worker if still healthy ---
                if not best_worker or not best_worker.is_healthy:
                    # Select a new healthy worker
                    from app.services.worker import WorkerService
                    worker_service = WorkerService(self.db)
                    best_worker = await worker_service.get_available_worker()
                    if not best_worker:
                        raise HTTPException(status_code=503, detail="No available WAHA worker found for existing session (previous worker unhealthy or missing).")
                    # --- UPDATE EXISTING WAHASession TO NEW WORKER ---
                    wahasession.worker_id = best_worker.id
                    await self.db.commit()
            else:
                # Assign to best available worker
                worker_service = WorkerService(self.db)
                best_worker = await worker_service.get_available_worker()
                if not best_worker:
                    raise HTTPException(status_code=503, detail="No available WAHA worker found for new session.")
            waha_url = best_worker.url
            api_key = best_worker.api_key

            # --- ENFORCE MAX_LIVE_SESSIONS PER WORKER ---
            from app.models.models import WAHASession
            from sqlalchemy import select, func
            session_count_result = await self.db.execute(
                select(func.count()).select_from(WAHASession).where(
                    WAHASession.worker_id == best_worker.id,
                    WAHASession.status.in_(["CONNECTED", "WORKING"])
                )
            )
            live_sessions_on_worker = session_count_result.scalar() or 0
            max_live = int(os.getenv("MAX_LIVE_SESSIONS", 100))
            if live_sessions_on_worker >= max_live:
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
                    "message": f"Session start request queued. Will be processed when a slot is available on worker {best_worker.id}.",
                    "last_active": None,
                    "data": None
                }

            # First, check if the session already exists in WAHA (on the selected worker)
            try:
                session_check = await self._make_waha_request(f"sessions/{clean_number}", waha_url=waha_url, api_key=api_key)
                session_exists = session_check and not session_check.get("error")
            except Exception as e:
                if "404" in str(e) or "Not Found" in str(e):
                    logging.info(f"WAHA session {clean_number} does not exist yet (404). Proceeding to create.")
                    session_exists = False
                else:
                    logging.error(f"Error checking WAHA session existence: {e}")
                    session_exists = False

            if not session_exists:
                create_response = await self._make_waha_request(
                    "sessions",
                    method="POST",
                    data={
                        "name": clean_number,
                        "config": {
                            "proxy": None,
                            "webhookUrl": f"{os.getenv('API_HOST')}:{os.getenv('API_PORT')}/api/webhook"
                        }
                    },
                    waha_url=waha_url,
                    api_key=api_key
                )
                logging.info(f"Created session for {clean_number}")
            else:
                logging.info(f"Session for {clean_number} already exists in WAHA")

            # Start the session (always safe to call)
            start_response = await self._make_waha_request(
                f"sessions/{clean_number}/start",
                method="POST",
                waha_url=waha_url,
                api_key=api_key
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
                session.status = "STARTING"
                session.last_active = datetime.utcnow()
                session.data = response
                await self.db.commit()
                logging.debug(f"[start_session] Set status=STARTING, last_active={session.last_active} for {clean_number}")
            else:
                session = Session(
                    phone_number=clean_number,
                    status="STARTING",
                    last_active=datetime.utcnow(),
                    data=response
                )
                self.db.add(session)
                await self.db.commit()
                logging.debug(f"[start_session] Created new session with status=STARTING, last_active={session.last_active} for {clean_number}")

            # --- Create WAHASession record for sharding ---
            # Check if WAHASession already exists for this phone_number
            wahasession_stmt = select(WAHASession).where(WAHASession.phone_number == clean_number)
            wahasession_result = await self.db.execute(wahasession_stmt)
            wahasession = wahasession_result.scalar_one_or_none()
            if not wahasession:
                wahasession = WAHASession(
                    name=clean_number,
                    user_id=user_id or "anonymous",
                    worker_id=best_worker.id,
                    phone_number=clean_number,
                    status="STARTING",
                    last_active=datetime.utcnow(),
                    data=response
                )
                self.db.add(wahasession)
                await self.db.commit()
                logging.debug(f"[start_session] Created WAHASession for {clean_number} on worker {best_worker.id}")
            # Return immediately after starting session
            return {
                "status": "STARTING",
                "message": "Session is starting. Poll for status and QR code.",
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
                # --- Determine the worker for this phone_number ---
                from sqlalchemy import select, func
                from app.models.models import WAHASession, Worker
                clean_number = phone_number.replace('+', '')
                wahasession_stmt = select(WAHASession).where(WAHASession.phone_number == clean_number)
                wahasession_result = await session.execute(wahasession_stmt)
                wahasession = wahasession_result.scalar_one_or_none()
                if wahasession:
                    worker_id = wahasession.worker_id
                else:
                    # Assign to best available worker (same as in start_session)
                    from app.services.worker import WorkerService
                    worker_service = WorkerService(session)
                    best_worker = await worker_service.get_available_worker()
                    if not best_worker:
                        logging.info(f"[QUEUE] No available worker for {phone_number}, requeuing.")
                        async with self.queue_lock:
                            self.session_start_queues[user_key].appendleft((phone_number, future))
                            if user_key not in self.queue_users:
                                self.queue_users.appendleft(user_key)
                        continue
                    worker_id = best_worker.id
                # Count live sessions for this worker only
                session_count_result = await session.execute(
                    select(func.count()).select_from(WAHASession).where(
                        WAHASession.worker_id == worker_id,
                        WAHASession.status.in_(["CONNECTED", "WORKING"])
                    )
                )
                live_sessions_on_worker = session_count_result.scalar() or 0
                max_live = int(os.getenv("MAX_LIVE_SESSIONS", 100))
                logging.info(f"[QUEUE] Live sessions on worker {worker_id}: {live_sessions_on_worker}/{max_live} (processing {phone_number})")
                if live_sessions_on_worker < max_live:
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
                    logging.info(f"[QUEUE] No slot for {phone_number} on worker {worker_id}, requeuing.")
                    async with self.queue_lock:
                        self.session_start_queues[user_key].appendleft((phone_number, future))
                        if user_key not in self.queue_users:
                            self.queue_users.appendleft(user_key)
        logging.info("[QUEUE] Session queue processor finished.")
        self.processing_queue = False

    async def get_qr_code(self, phone_number: str) -> Optional[Dict[str, Any]]:
        """Get QR code for WhatsApp Web authentication"""
        try:
            clean_number = phone_number.replace('+', '')
            waha_url, api_key = await self._get_worker_for_session(clean_number)
            state = await self._make_waha_request(f"sessions/{clean_number}", waha_url=waha_url, api_key=api_key)
            if state.get("error"):
                raise ValueError(f"Session not found: {state['error']}")
            if state.get("status") == "CONNECTED":
                return {
                    "status": "CONNECTED",
                    "message": "Session is already connected"
                }
            qr_image = await self._make_waha_request(f"{clean_number}/auth/qr", expect_json=False, waha_url=waha_url, api_key=api_key)
            if not qr_image:
                raise ValueError("Failed to get QR code")
            qr_base64 = base64.b64encode(qr_image).decode('utf-8')
            return {
                "status": state.get("status", "UNKNOWN"),
                "qr_code": qr_base64,
                "expires_at": datetime.utcnow() + timedelta(minutes=5)
            }
        except Exception as e:
            logging.error(f"Error getting QR code: {str(e)}")
            raise

    async def check_session_status(self, phone_number: str) -> Dict[str, Any]:
        """Check the status of a WhatsApp session"""
        try:
            waha_url, api_key = await self._get_worker_for_session(phone_number)
            response = await self._make_waha_request(f"sessions/{phone_number}", waha_url=waha_url, api_key=api_key)
            if response.get("error"):
                raise ValueError(f"Failed to check session status: {response['error']}")
            status_response = await self._make_waha_request(f"sessions/{phone_number}", waha_url=waha_url, api_key=api_key)
            response.update(status_response or {})
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            new_status = response.get("status", "UNKNOWN")
            if session:
                session.status = new_status
                session.last_active = datetime.utcnow()
                session.data = response
                await self.db.commit()
            # --- Update WAHASession status as well ---
            from app.models.models import WAHASession
            wahasession_stmt = select(WAHASession).where(WAHASession.phone_number == phone_number)
            wahasession_result = await self.db.execute(wahasession_stmt)
            wahasession = wahasession_result.scalar_one_or_none()
            if wahasession and wahasession.status != new_status:
                wahasession.status = new_status
                wahasession.last_active = datetime.utcnow()
                wahasession.data = response
                await self.db.commit()
            return {
                "status": new_status,
                "message": "Session status retrieved",
                "session_id": phone_number,
                **response
            }
        except Exception as e:
            logging.error(f"Error checking session status: {str(e)}")
            await self.db.rollback()
            raise

    async def keep_session_alive(self, phone_number: str) -> bool:
        """Keep a session alive by checking its status (no ping endpoint in WAHA)"""
        try:
            waha_url, api_key = await self._get_worker_for_session(phone_number)
            response = await self._make_waha_request(f"sessions/{phone_number}", waha_url=waha_url, api_key=api_key)
            if response.get("status") in ("WORKING", "CONNECTED"):
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

    async def logout_session(self, phone_number: str) -> Dict[str, Any]:
        """Logout from WhatsApp Web"""
        try:
            waha_url, api_key = await self._get_worker_for_session(phone_number)
            response = await self._make_waha_request(f"sessions/{phone_number}/logout", method="POST", waha_url=waha_url, api_key=api_key)
            if response.get("error"):
                raise ValueError(f"Failed to logout session: {response['error']}")
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
            waha_url, api_key = await self._get_worker_for_session(phone_number)
            response = await self._make_waha_request(f"sessions/{phone_number}", method="DELETE", waha_url=waha_url, api_key=api_key)
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
            waha_url, api_key = await self._get_worker_for_session(phone_number)
            session_info = await self._make_waha_request(f"sessions/{phone_number}", waha_url=waha_url, api_key=api_key)
            if session_info.get("error"):
                raise ValueError(f"Session not found: {session_info['error']}")
            valid_states = ["CONNECTED", "WORKING"]
            if session_info.get("status") not in valid_states:
                raise ValueError(f"Session is not connected. Current status: {session_info.get('status')}")
            response = await self._make_waha_request(f"sessions/{phone_number}/me", waha_url=waha_url, api_key=api_key)
            if response.get("error"):
                raise ValueError(f"Failed to get account info: {response['error']}")
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
        """List all WAHA sessions across all workers"""
        try:
            from sqlalchemy import select
            from app.models.models import Worker
            workers_result = await self.db.execute(select(Worker))
            workers = workers_result.scalars().all()
            all_sessions = []
            for worker in workers:
                try:
                    sessions = await self._make_waha_request("sessions", waha_url=worker.url, api_key=worker.api_key)
                    if isinstance(sessions, dict):
                        sessions = list(sessions.values())
                    all_sessions.extend(sessions)
                except Exception as e:
                    logging.error(f"Error listing sessions from worker {worker.id}: {e}")
            return all_sessions
        except Exception as e:
            logging.error(f"Error listing sessions: {str(e)}")
            raise

    async def monitor_sessions(self):
        """Monitor and maintain active sessions, auto-stop if lifetime exceeded. Also update WAHASession status."""
        try:
            from sqlalchemy import select
            import os
            from app.models.models import WAHASession
            session_lifetime = int(os.getenv("SESSION_LIFETIME_SECONDS", 0))
            now = datetime.utcnow()
            # Get all sessions that are in any active state
            monitored_statuses = ["STARTING", "CONNECTED", "WORKING", "SCAN_QR_CODE"]
            query = select(Session).where(Session.status.in_(monitored_statuses))
            result = await self.db.execute(query)
            sessions = result.scalars().all()
            for session in sessions:
                # Poll WAHA API for latest status and update DB
                try:
                    await self.get_session_info(session.phone_number)
                except Exception as e:
                    logging.error(f"[monitor_sessions] Failed to update status for {session.phone_number}: {e}")
                # Auto-stop only for WORKING status
                if session.status == "WORKING" and session_lifetime > 0 and (now - session.last_active).total_seconds() > session_lifetime:
                    logging.info(f"Auto-stopping session {session.phone_number} (lifetime exceeded)")
                    await self.stop_session(session.phone_number)
                else:
                    # Only keep alive if session is AFK for 12 days (1036800 seconds)
                    afk_seconds = 12 * 24 * 60 * 60
                    if (now - session.last_active).total_seconds() > afk_seconds:
                        await self.keep_session_alive(session.phone_number)
        except Exception as e:
            logging.error(f"Error monitoring sessions: {str(e)}")
            raise

    async def _get_worker_for_session(self, phone_number: str):
        """Helper to get the worker's url and api_key for a given session."""
        from sqlalchemy import select
        from app.models.models import WAHASession, Worker
        wahasession_stmt = select(WAHASession).where(WAHASession.phone_number == phone_number)
        wahasession_result = await self.db.execute(wahasession_stmt)
        wahasession = wahasession_result.scalar_one_or_none()
        if not wahasession:
            raise ValueError(f"No WAHASession found for phone_number {phone_number}")
        worker_stmt = select(Worker).where(Worker.id == wahasession.worker_id)
        worker_result = await self.db.execute(worker_stmt)
        worker = worker_result.scalar_one_or_none()
        if not worker:
            raise ValueError(f"No Worker found for worker_id {wahasession.worker_id}")
        return worker.url, worker.api_key

    async def get_session_info(self, phone_number: str) -> Dict[str, Any]:
        """Get detailed information about a session and update DB status if changed"""
        try:
            waha_url, api_key = await self._get_worker_for_session(phone_number)
            response = await self._make_waha_request(f"sessions/{phone_number}", waha_url=waha_url, api_key=api_key)
            logging.info(f"[get_session_info] WAHA API response for {phone_number}: {response}")
            if response.get("error"):
                raise ValueError(f"Session not found: {response['error']}")
            # Update DB if status has changed
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            new_status = response.get("status", "UNKNOWN")
            if session:
                logging.info(f"[get_session_info] Session found: {phone_number}, DB status: {session.status}, WAHA status: {new_status}")
                if session.status != new_status:
                    old_status = session.status
                    session.status = new_status
                    session.last_active = datetime.utcnow()
                    session.data = response
                    await self.db.commit()
                    logging.info(f"[get_session_info] Updated Session: {phone_number}, {old_status} -> {new_status}, last_active={session.last_active}")
                else:
                    logging.info(f"[get_session_info] No update needed for Session: {phone_number}, status unchanged: {session.status}")
            else:
                logging.warning(f"[get_session_info] No Session found for phone_number: {phone_number}")
            # --- Update WAHASession status as well ---
            from app.models.models import WAHASession
            wahasession_stmt = select(WAHASession).where(WAHASession.phone_number == phone_number)
            wahasession_result = await self.db.execute(wahasession_stmt)
            wahasession = wahasession_result.scalar_one_or_none()
            if wahasession:
                logging.info(f"[get_session_info] WAHASession found: {phone_number}, DB status: {wahasession.status}, WAHA status: {new_status}")
                if wahasession.status != new_status:
                    old_status = wahasession.status
                    wahasession.status = new_status
                    wahasession.last_active = datetime.utcnow()
                    wahasession.data = response
                    await self.db.commit()
                    logging.info(f"[get_session_info] Updated WAHASession: {phone_number}, {old_status} -> {new_status}, last_active={wahasession.last_active}")
                else:
                    logging.info(f"[get_session_info] No update needed for WAHASession: {phone_number}, status unchanged: {wahasession.status}")
            else:
                logging.warning(f"[get_session_info] No WAHASession found for phone_number: {phone_number}")
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
            waha_url, api_key = await self._get_worker_for_session(phone_number)
            response = await self._make_waha_request(f"sessions/{phone_number}/stop", method="POST", waha_url=waha_url, api_key=api_key)
            if response.get("error"):
                raise ValueError(f"Failed to stop session: {response['error']}")
            from sqlalchemy import select
            query = select(Session).where(Session.phone_number == phone_number)
            result = await self.db.execute(query)
            session = result.scalar_one_or_none()
            if session:
                session.status = "STOPPED"
                session.last_active = datetime.utcnow()
                # --- Update session.data to reflect STOPPED status ---
                if session.data:
                    try:
                        session_data = session.data.copy() if isinstance(session.data, dict) else dict(session.data)
                        session_data["status"] = "STOPPED"
                        if "engine" in session_data and isinstance(session_data["engine"], dict):
                            session_data["engine"]["state"] = "STOPPED"
                        session.data = session_data
                    except Exception as e:
                        logging.warning(f"Could not update session.data for STOPPED: {e}")
                await self.db.commit()
                logging.debug(f"[stop_session] Set status=STOPPED, last_active={session.last_active} for {phone_number}")
            # --- Update WAHASession status as well ---
            from app.models.models import WAHASession
            wahasession_stmt = select(WAHASession).where(WAHASession.phone_number == phone_number)
            wahasession_result = await self.db.execute(wahasession_stmt)
            wahasession = wahasession_result.scalar_one_or_none()
            if wahasession and wahasession.status != "STOPPED":
                wahasession.status = "STOPPED"
                wahasession.last_active = datetime.utcnow()
                wahasession.data = response
                await self.db.commit()
                logging.info(f"[stop_session] Set WAHASession status=STOPPED, last_active={wahasession.last_active} for {phone_number}")
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

    async def get_available_worker(self):
        """Select the best available worker based on health and available capacity (weighted, health-aware)."""
        from sqlalchemy import select, func
        from app.models.models import Worker, WAHASession
        # Only select healthy workers
        workers_result = await self.db.execute(select(Worker).where(Worker.is_healthy == True))
        workers = workers_result.scalars().all()
        best_worker = None
        max_capacity_left = -1
        for worker in workers:
            # Count active sessions on this worker
            session_count = await self.db.scalar(
                select(func.count()).select_from(WAHASession).where(
                    WAHASession.worker_id == worker.id,
                    WAHASession.status.in_(["STARTING", "CONNECTED", "WORKING"])
                )
            )
            capacity_left = worker.capacity - (session_count or 0)
            if capacity_left > max_capacity_left:
                max_capacity_left = capacity_left
                best_worker = worker
        return best_worker if best_worker and max_capacity_left > 0 else None
