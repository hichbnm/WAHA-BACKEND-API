from fastapi import APIRouter, Depends
from typing import List
from app.models import schemas
from app.services.admin import AdminService
from app.utils.auth import verify_admin_token
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db

router = APIRouter()

@router.get("/metrics", response_model=schemas.SystemMetrics)
async def get_metrics(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token)
):
    """Get system-wide metrics (admin only)"""
    admin_service = AdminService(db)
    return await admin_service.get_system_metrics()

@router.get("/users", response_model=List[schemas.UserStats])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token)
):
    """Get stats for all users (admin only)"""
    admin_service = AdminService(db)
    return await admin_service.get_user_stats()

@router.get("/campaigns", response_model=schemas.CampaignStats)
async def get_campaign_stats(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token)
):
    """Get detailed campaign statistics (admin only)"""
    admin_service = AdminService(db)
    return await admin_service.get_campaign_stats()

@router.get("/sessions", response_model=List[schemas.SessionStatus])
async def list_active_sessions(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token)
):
    """List all active WhatsApp sessions (admin only)"""
    admin_service = AdminService(db)
    return await admin_service.get_active_sessions()
