from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.services.messaging import MessagingService
from typing import Optional, Dict, Any
import logging
from datetime import datetime

router = APIRouter()

async def validate_webhook_data(data: Dict[str, Any]) -> Optional[str]:
    """Validate the incoming webhook data"""
    if not isinstance(data, dict):
        return "Invalid data format"
    
    event = data.get("event")
    if not event:
        return "Missing event field"
    
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return "Invalid payload format"
    
    return None

@router.post("")  # Changed from "/webhook" to "" since the router is already mounted at /api/webhook
async def whatsapp_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Handle WAHA webhook events"""
    try:
        # Handle empty body
        body = await request.body()
        if not body:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "status": "error",
                    "message": "Empty request body"
                }
            )

        # Parse JSON data
        data = await request.json()
        
        # Validate webhook data
        if error := await validate_webhook_data(data):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "status": "error",
                    "message": error
                }
            )

        event = data["event"]
        payload = data["payload"]
        
        # Log the incoming webhook
        logging.info(f"Received webhook event: {event}")
        logging.debug(f"Webhook payload: {payload}")

        # Handle different event types
        if event == "message":
            # Handle incoming message
            await handle_incoming_message(payload, db)
        elif event == "message.ack":
            # Handle message acknowledgment
            await handle_message_ack(payload, db)
        elif event == "qr":
            # Handle QR code update
            await handle_qr_update(payload, db)
        elif event == "connection.update":
            # Handle connection state changes
            await handle_connection_update(payload, db)
        else:
            logging.warning(f"Unhandled webhook event type: {event}")
        
        return {"status": "success"}
        
    except Exception as e:
        logging.error(f"Error processing webhook: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error processing webhook"}
        )

async def handle_incoming_message(payload: Dict[str, Any], db: AsyncSession):
    """Handle incoming WhatsApp message"""
    try:
        # Extract message details
        message_id = payload.get("id")
        from_number = payload.get("from")
        
        # Update any relevant campaign or message status
        # Implement based on your needs
        
        logging.info(f"Processed incoming message {message_id} from {from_number}")
    except Exception as e:
        logging.error(f"Error handling incoming message: {str(e)}")

async def handle_message_ack(payload: Dict[str, Any], db: AsyncSession):
    """Handle message delivery acknowledgment"""
    try:
        # Extract ack details
        message_id_raw = payload.get("id")
        status = payload.get("ack")  # -1: pending, 0: sent, 1: delivered, 2: read

        # Update message status in database using waha_message_id
        from sqlalchemy import select
        from app.models.models import Message
        result = await db.execute(select(Message).where(Message.waha_message_id == message_id_raw))
        message = result.scalar_one_or_none()
        if message:
            # Always set delivered_at to sent_at, regardless of status
            message.delivered_at = message.sent_at
            if status == 1:  # delivered
                message.status = "DELIVERED"
                await db.commit()
                logging.info(f"Message {message_id_raw} marked as DELIVERED at {message.delivered_at}")
            elif status == 2:  # read
                message.status = "READ"
                await db.commit()
                logging.info(f"Message {message_id_raw} marked as READ")
            elif status == 0:  # sent
                message.status = "SENT"
                await db.commit()
                logging.info(f"Message {message_id_raw} marked as SENT")
            else:
                logging.info(f"Message {message_id_raw} ack status: {status}")
        else:
            logging.warning(f"Message with WAHA ID {message_id_raw} not found for ack update.")
    except Exception as e:
        logging.error(f"Error handling message ack: {str(e)}")

async def handle_qr_update(payload: Dict[str, Any], db: AsyncSession):
    """Handle QR code update"""
    try:
        # Extract session details
        session_id = payload.get("sessionId")
        qr_code = payload.get("qr")
        
        # Update session QR code in database
        # Implement based on your needs
        
        logging.info(f"Updated QR code for session {session_id}")
    except Exception as e:
        logging.error(f"Error handling QR update: {str(e)}")

async def handle_connection_update(payload: Dict[str, Any], db: AsyncSession):
    """Handle connection state changes"""
    try:
        # Extract connection details
        session_id = payload.get("sessionId")
        state = payload.get("state")
        if not session_id or not state:
            logging.warning(f"Missing sessionId or state in connection update payload: {payload}")
            return
        # Update session state in database
        from sqlalchemy import select
        from app.models.models import Session
        result = await db.execute(select(Session).where(Session.phone_number == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.status = state.upper()  # WAHA states are usually uppercase (e.g., WORKING)
            session.last_active = datetime.utcnow()
            await db.commit()
            logging.info(f"Session {session_id} connection state updated to: {state}")
        else:
            logging.warning(f"Session {session_id} not found in DB for connection update.")
    except Exception as e:
        logging.error(f"Error handling connection update: {str(e)}")
