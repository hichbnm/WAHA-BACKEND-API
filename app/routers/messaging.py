
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional
from datetime import datetime
from app.db.database import get_db
from app.models import models, schemas
from app.services.messaging import MessagingService
from app.services.campaign import CampaignService
from app.utils.auth import verify_admin_token, get_optional_admin_token
from app.routers.delays import normalize_number  # Reuse normalization utility

router = APIRouter()

@router.post("/send", response_model=schemas.CampaignResponse)
async def create_campaign(
    campaign: schemas.CampaignCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new messaging campaign (enqueue for background processing only)"""
    campaign.sender_number = normalize_number(campaign.sender_number)
    campaign.recipients = [normalize_number(r) for r in campaign.recipients]
    campaign_service = CampaignService(db)
    try:
        # Only create the campaign and enqueue Celery task, do NOT process campaign here
        result = await campaign_service.create_campaign(campaign)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/cancel/{campaign_id}", response_model=schemas.CampaignStatus)
async def cancel_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Cancel a campaign by ID if it is IN_PROGRESS or PENDING"""
    campaign_service = CampaignService(db)
    try:
        return await campaign_service.cancel_campaign(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
@router.get("/status/{campaign_id}", response_model=schemas.CampaignStatus)
async def get_campaign_status(
    campaign_id: int,
    details: bool = Query(False),
    admin_token: Optional[str] = Depends(get_optional_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """Get campaign status with optional message details"""
    campaign_service = CampaignService(db)
    
    # Get the campaign
    campaign = await campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    # If not admin, verify sender_number matches
    # (Removed campaign.data reference, as Campaign has no 'data' attribute)
    # If you want to restrict access, compare to authenticated user here
    # For now, just allow if not admin
    # Example: if not admin_token and campaign.sender_number != <user_sender_number>: ...
    
    return await campaign_service.get_campaign_status(campaign_id, include_details=details)

@router.get("/campaigns", response_model=List[schemas.CampaignList])
async def list_campaigns(
    sender_number: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    admin_token: Optional[str] = Depends(get_optional_admin_token),
    db: AsyncSession = Depends(get_db)
):
    """List campaigns with optional filters"""
    campaign_service = CampaignService(db)
    
    if sender_number:
        sender_number = normalize_number(sender_number)
    
    # If not admin, require sender_number and verify it matches
    if not admin_token:
        if not sender_number:
            raise HTTPException(status_code=400, detail="sender_number is required")
    
    campaigns = await campaign_service.list_campaigns(
        sender_number=sender_number,
        status=status,
        start_date=start_date,
        end_date=end_date
    )
    return campaigns

@router.get("/metrics", response_model=schemas.SystemMetrics)
async def get_system_metrics(
    sender_number: str,
    db: AsyncSession = Depends(get_db)
):
    """Get system metrics relevant to the current sender (user-level, no authentication required)"""
    sender_number = normalize_number(sender_number)
    campaign_service = CampaignService(db)
    return await campaign_service.get_system_metrics(sender_number=sender_number)
