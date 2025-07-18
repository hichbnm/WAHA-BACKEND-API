from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from app.models import schemas
from app.services.session import SessionService
from app.services.waha_session import WAHASessionService
from app.utils.auth import verify_admin_token, get_optional_admin_token
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from fastapi.responses import JSONResponse
import logging
from typing import Optional, List
from datetime import timedelta
from app.models.models import Session  # Import the Session model
from app.routers.delays import normalize_number  # Reuse normalization utility

router = APIRouter()

@router.get("", response_model=List[schemas.SessionResponse])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    admin_token: Optional[str] = Depends(get_optional_admin_token)
):
    """List all sessions"""
    waha_service = WAHASessionService(db)
    try:
        sessions = await waha_service.list_sessions()
        if not admin_token:
            return []  # Only admins can see all sessions
        # Map each WAHA session to SessionResponse
        return [
            schemas.SessionResponse(
                phone_number=s.get("name", ""),
                status=s.get("status", "UNKNOWN"),
                message="OK",
                last_active=None,
                data=s
            ) for s in sessions
        ]
    except Exception as e:
        logging.error(f"Error listing sessions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{phone_number}", response_model=schemas.SessionResponse)
async def get_session(
    phone_number: str,
    db: AsyncSession = Depends(get_db)
):
    phone_number = normalize_number(phone_number)
    waha_service = WAHASessionService(db)
    try:
        result = await waha_service.get_session_info(phone_number)
        return result
    except Exception as e:
        logging.error(f"Error getting session {phone_number}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{phone_number}/start", response_model=schemas.SessionInfo)
async def start_session(
    phone_number: str,
    db: AsyncSession = Depends(get_db)
):
    phone_number = normalize_number(phone_number)
    waha_service = WAHASessionService(db)
    try:
        result = await waha_service.start_session(phone_number)
        return result
    except Exception as e:
        if (
            isinstance(e, HTTPException)
            and getattr(e, 'status_code', None) == 503
            and "No available WAHA worker found for new session" in str(e.detail)
        ):
            logging.info(f"Info starting session {phone_number}: {str(e)}")
            raise e
        else:
            logging.error(f"Error starting session {phone_number}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

@router.post("/{phone_number}/stop", response_model=schemas.SessionInfo)
async def stop_session(
    phone_number: str,
    db: AsyncSession = Depends(get_db)
):
    phone_number = normalize_number(phone_number)
    waha_service = WAHASessionService(db)
    try:
        result = await waha_service.stop_session(phone_number)
        return result
    except Exception as e:
        logging.error(f"Error stopping session {phone_number}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{phone_number}/logout", response_model=schemas.SessionInfo)
async def logout_session(
    phone_number: str,
    db: AsyncSession = Depends(get_db)
):
    phone_number = normalize_number(phone_number)
    waha_service = WAHASessionService(db)
    try:
        result = await waha_service.logout_session(phone_number)
        return result
    except Exception as e:
        logging.error(f"Error logging out session {phone_number}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{phone_number}", response_model=schemas.SessionInfo)
async def delete_session(
    phone_number: str,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token)  # Only admins can delete sessions
):
    phone_number = normalize_number(phone_number)
    waha_service = WAHASessionService(db)
    try:
        result = await waha_service.delete_session(phone_number)
        return result
    except HTTPException as e:
        # Let FastAPI handle HTTPExceptions (404, 400, etc.)
        raise
    except Exception as e:
        logging.error(f"Error deleting session {phone_number}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{phone_number}/qr", response_model=schemas.QRCodeResponse)
async def get_session_qr(
    phone_number: str,
    db: AsyncSession = Depends(get_db)
):
    phone_number = normalize_number(phone_number)
    waha_service = WAHASessionService(db)
    try:
        result = await waha_service.get_qr_code(phone_number)
        return result
    except Exception as e:
        logging.error(f"Error getting QR code for session {phone_number}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{phone_number}/me", response_model=schemas.MeInfo)
async def get_me_info(
    phone_number: str,
    db: AsyncSession = Depends(get_db)
):
    phone_number = normalize_number(phone_number)
    waha_service = WAHASessionService(db)
    try:
        result = await waha_service.get_me_info(phone_number)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logging.error(f"Error getting account info for {phone_number}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
