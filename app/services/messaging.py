from aiohttp import ClientSession, ClientError
import logging
import json
from typing import Optional, Dict, Any
from app.services.waha_session import WAHASessionService
from app.services.message_utils import update_last_active
from sqlalchemy.ext.asyncio import AsyncSession
import base64
import mimetypes
import os

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
                        if response.status >= 400:
                            body = await response.text()
                            raise ValueError(f"WAHA {endpoint} failed {response.status}: {body[:500]}")
                        return await response.json()
                else:  # POST
                    async with client.post(url, json=data, headers=headers) as response:
                        if response.status == 401:
                            raise ValueError("Unauthorized: Invalid WAHA API key")
                        if response.status >= 400:
                            body = await response.text()
                            raise ValueError(f"WAHA {endpoint} failed {response.status}: {body[:500]}")
                        return await response.json()
        except ClientError as e:
            logging.error(f"WAHA API request failed: {str(e)}")
            raise ValueError(f"Failed to connect to WAHA API: {str(e)}")
        except json.JSONDecodeError as e:
            logging.error(f"Failed to decode WAHA API response: {str(e)}")
            raise ValueError("Invalid response from WAHA API")

    async def send_message(self, sender_number: str, recipient: str, message: str, media_url: Optional[str] = None) -> Dict[str, Any]:
        """Send a message with optional media. If both message and media_url are provided, send as a single media message with caption."""
        try:
            recipient_id = recipient.lstrip('+') + '@c.us'
            if not self.db:
                raise ValueError("MessagingService requires a DB session for per-worker routing.")
            waha_service = WAHASessionService(self.db)
            waha_url, api_key = await waha_service._get_worker_for_session(sender_number)

            waha_message_id = None
            text_sent = False
            media_sent = False

            if media_url:
                # Determine mimetype and choose endpoint by type
                mimetype, _ = mimetypes.guess_type(media_url)
                if not mimetype:
                    mimetype = "application/octet-stream"
                endpoint = (
                    "sendImage" if mimetype.startswith("image/") else 
                    ("sendVideo" if mimetype.startswith("video/") else "sendFile")
                )
                filename = os.path.basename(media_url) or "file"
                payload = None
                # Local file path → read and send as base64
                if os.path.isfile(media_url):
                    with open(media_url, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                    payload = {
                        "chatId": recipient_id,
                        "file": {
                            "mimetype": mimetype,
                            "filename": filename,
                            "data": f"data:{mimetype};base64,{b64}"
                        },
                        "caption": message or "",
                        "session": sender_number
                    }
                # HTTP(S) URL → download and send as base64 (avoid WAHA fetching URL)
                elif media_url.lower().startswith(("http://", "https://")):
                    max_mb = int(os.getenv("MAX_UPLOAD_MB", "15"))
                    max_bytes = max_mb * 1024 * 1024
                    from aiohttp import ClientSession
                    data_bytes = bytearray()
                    async with ClientSession() as client:
                        async with client.get(media_url) as resp:
                            resp.raise_for_status()
                            ct = resp.headers.get("Content-Type")
                            if ct and ct != "application/octet-stream":
                                mimetype = ct.split(";")[0].strip()
                                endpoint = (
                                    "sendImage" if mimetype.startswith("image/") else 
                                    ("sendVideo" if mimetype.startswith("video/") else "sendFile")
                                )
                            while True:
                                chunk = await resp.content.read(1024 * 1024)
                                if not chunk:
                                    break
                                data_bytes.extend(chunk)
                                if len(data_bytes) > max_bytes:
                                    raise ValueError(f"Remote media exceeds size limit {max_mb} MB")
                    b64 = base64.b64encode(bytes(data_bytes)).decode("ascii")
                    # derive filename from URL path if available
                    try:
                        from urllib.parse import urlparse
                        parsed = urlparse(media_url)
                        base = os.path.basename(parsed.path)
                        if base:
                            filename = base
                    except Exception:
                        pass
                    payload = {
                        "chatId": recipient_id,
                        "file": {
                            "mimetype": mimetype,
                            "filename": filename,
                            "data": f"data:{mimetype};base64,{b64}"
                        },
                        "caption": message or "",
                        "session": sender_number
                    }
                else:
                    # Fallback: pass URL through
                    payload = {
                        "chatId": recipient_id,
                        "file": {
                            "mimetype": mimetype,
                            "filename": filename,
                            "url": media_url
                        },
                        "caption": message or "",
                        "session": sender_number
                    }
                # Attempt request with fallback strategies
                try:
                    media_response = await self._make_waha_request(
                        endpoint,
                        data=payload,
                        waha_url=waha_url,
                        api_key=api_key
                    )
                except Exception as e:
                    # Try same endpoint with raw base64 (without data: prefix) if we used base64
                    try_alt = False
                    alt_payload = payload
                    try:
                        file_obj = payload.get("file") if isinstance(payload, dict) else None
                        data_field = file_obj.get("data") if isinstance(file_obj, dict) else None
                        if data_field and isinstance(data_field, str) and data_field.startswith("data:"):
                            try_alt = True
                            raw_b64 = data_field.split(",", 1)[1]
                            alt_payload = {
                                **payload,
                                "file": {
                                    **file_obj,
                                    "data": raw_b64
                                }
                            }
                    except Exception:
                        try_alt = False
                    if try_alt:
                        try:
                            media_response = await self._make_waha_request(
                                endpoint,
                                data=alt_payload,
                                waha_url=waha_url,
                                api_key=api_key
                            )
                        except Exception:
                            # As a final fallback, try sendFile with alt payload
                            media_response = await self._make_waha_request(
                                "sendFile",
                                data=alt_payload,
                                waha_url=waha_url,
                                api_key=api_key
                            )
                    else:
                        # No base64 alt; try sendFile
                        media_response = await self._make_waha_request(
                            "sendFile",
                            data=payload,
                            waha_url=waha_url,
                            api_key=api_key
                        )
                if media_response.get("error"):
                    raise ValueError(f"Failed to send image: {media_response['error']}")
                waha_message_id = media_response.get("id")
                media_sent = True
                text_sent = bool(message)
            elif message:
                # Only text
                payload = {
                    "session": sender_number,
                    "chatId": recipient_id,
                    "text": message
                }
                logging.info(f"Sending WAHA sendText payload: {payload}")
                text_response = await self._make_waha_request(
                    "sendText",
                    data=payload,
                    waha_url=waha_url,
                    api_key=api_key
                )
                if text_response.get("error"):
                    raise ValueError(f"Failed to send text message: {text_response['error']}")
                waha_message_id = text_response.get("id")
                text_sent = True

            await update_last_active(self.db, sender_number)

            return {
                "status": "success",
                "message": "Message sent successfully",
                "details": {
                    "text_sent": text_sent,
                    "media_sent": media_sent,
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
