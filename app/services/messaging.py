from aiohttp import ClientSession, ClientError
import os
from dotenv import load_dotenv
import logging
import json
from typing import Optional, Dict, Any

load_dotenv()

class MessagingService:
    def __init__(self):
        self.waha_url = f"{os.getenv('WAHA_HOST')}:{os.getenv('WAHA_PORT')}"
        self.api_key = os.getenv('WAHA_API_KEY')
        if not self.api_key:
            raise ValueError("WAHA_API_KEY environment variable is not set")
            
    async def _make_waha_request(self, endpoint: str, method: str = "POST", data: Dict = None) -> Dict[str, Any]:
        """Make a request to WAHA API with proper error handling"""
        url = f"{self.waha_url}/api/{endpoint}"
        headers = {"X-Api-Key": self.api_key}
        
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
        """Send a message with optional media"""
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
            # Send text message
            if message:
                text_response = await self._make_waha_request(
                    "sendText",
                    data=payload
                )
                
                if text_response.get("error"):
                    raise ValueError(f"Failed to send text message: {text_response['error']}")

            # Send media if provided
            if media_url:
                media_response = await self._make_waha_request(
                    "sendMedia",
                    data={
                        "chatId": recipient_id,
                        "mediaUrl": media_url
                    }
                )
                
                if media_response.get("error"):
                    raise ValueError(f"Failed to send media: {media_response['error']}")

            return {
                "status": "success",
                "message": "Message sent successfully",
                "details": {
                    "text_sent": bool(message),
                    "media_sent": bool(media_url)
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
