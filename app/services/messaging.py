from aiohttp import ClientSession, ClientError
import logging
import json
from typing import Optional, Dict, Any
from app.services.waha_session import WAHASessionService
from app.services.message_utils import update_last_active
from sqlalchemy.ext.asyncio import AsyncSession

class MessagingService:
    def __init__(self, db: Optional[AsyncSession] = None):
        self.db = db
        # No waha_url or api_key from env; always use per-worker credentials

    async def _make_waha_request(self, endpoint: str, method: str = "POST", data: Dict = None, waha_url: str = None, api_key: str = None) -> Dict[str, Any]:
        """Make a request to WAHA API with proper error handling, using per-worker credentials (required)"""
        if not waha_url or not api_key:
            raise ValueError("waha_url and api_key must be provided from worker DB for WAHA API calls.")
        url = f"{waha_url}/api/{endpoint}"
        headers = {"X-Api-Key": api_key}
        try:
            async with ClientSession() as client:
                if method == "GET":
                    async with client.get(url, headers=headers) as response:
                        if response.status == 401:
                            raise ValueError("Unauthorized: Invalid WAHA API key")
                        response.raise_for_status()
                        return await response.json()
                else:  # POST
                    async with client.post(url, json=data, headers=headers) as response:
                        if response.status == 401:
                            raise ValueError("Unauthorized: Invalid WAHA API key")
                        response.raise_for_status()
                        return await response.json()
        except ClientError as e:
            logging.error(f"WAHA API request failed: {str(e)}")
            raise ValueError(f"Failed to connect to WAHA API: {str(e)}")
        except json.JSONDecodeError as e:
            logging.error(f"Failed to decode WAHA API response: {str(e)}")
            raise ValueError("Invalid response from WAHA API")

    async def send_message(self, sender_number: str, recipient: str, message: str, media_url: Optional[str] = None) -> Dict[str, Any]:
        """Send a message with optional media using the correct WAHA worker for the sender_number"""
        try:
            # Remove '+' if present for WAHA chatId
            recipient_id = recipient.lstrip('+') + '@c.us'
            # Add session field to payload for WAHA compatibility
            payload = {
                "session": sender_number,  # or "sessionId": sender_number if WAHA expects that
                "chatId": recipient_id,
                "text": message
            }
            logging.info(f"Sending WAHA sendText payload: {payload}")

            # --- Per-worker sharding: get correct worker for sender_number ---
            if not self.db:
                raise ValueError("MessagingService requires a DB session for per-worker routing.")
            waha_service = WAHASessionService(self.db)
            waha_url, api_key = await waha_service._get_worker_for_session(sender_number)

            waha_message_id = None
            # Send text message
            if message:
                text_response = await self._make_waha_request(
                    "sendText",
                    data=payload,
                    waha_url=waha_url,
                    api_key=api_key
                )
                if text_response.get("error"):
                    raise ValueError(f"Failed to send text message: {text_response['error']}")
                waha_message_id = text_response.get("id")

            # Send media if provided
            if media_url:
                media_response = await self._make_waha_request(
                    "sendMedia",
                    data={
                        "chatId": recipient_id,
                        "mediaUrl": media_url
                    },
                    waha_url=waha_url,
                    api_key=api_key
                )
                if media_response.get("error"):
                    raise ValueError(f"Failed to send media: {media_response['error']}")
                if not waha_message_id:
                    waha_message_id = media_response.get("id")

            # Update last_active after successful send
            await update_last_active(self.db, sender_number)

            return {
                "status": "success",
                "message": "Message sent successfully",
                "details": {
                    "text_sent": bool(message),
                    "media_sent": bool(media_url),
                    "waha_message_id": waha_message_id
                }
            }
        except Exception as e:
            logging.error(f"Error sending message to {recipient}: {str(e)}")
            raise

    async def check_message_status(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Check the status of a message"""
        try:
            response = await self._make_waha_request(f"messages/{message_id}", method="GET")
            if response.get("error"):
                return None
            return response
        except Exception as e:
            logging.error(f"Error checking message status {message_id}: {str(e)}")
            return None
