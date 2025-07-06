from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, or_
from datetime import datetime, timedelta
import asyncio
import logging
from app.models import models, schemas
from app.services.messaging import MessagingService
from app.services.waha_session import WAHASessionService
from app.services.message_queue import message_queue
import os

class CampaignService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.messaging = MessagingService()
        self.server_start_time = datetime.utcnow()
        
    async def create_campaign(self, campaign: schemas.CampaignCreate) -> schemas.CampaignResponse:
        """Create a new campaign and queue its messages"""
        # First check if the sender has an active session
        waha_service = WAHASessionService(self.db)
        try:
            session_status = await waha_service.check_session_status(campaign.sender_number)
        except Exception as e:
            raise ValueError("WhatsApp session not found or not connected. Please create a session and scan the QR code before sending messages.")
        if session_status.get('status') not in ('CONNECTED', 'WORKING'):
            raise ValueError("WhatsApp session not connected. Please scan QR code to authenticate.")
        
        # Create campaign record
        db_campaign = models.Campaign(
            sender_number=campaign.sender_number,
            template=campaign.template,
            status="QUEUED",
            total_messages=len(campaign.recipients),
            sent_messages=0,
            failed_messages=0,
            variables=campaign.variables,
            media_url=campaign.media_url
        )
        self.db.add(db_campaign)
        await self.db.flush()  # Get campaign ID
        
        # Create message records
        for recipient in campaign.recipients:
            message = models.Message(
                campaign_id=db_campaign.id,
                recipient=recipient,
                status="PENDING"
            )
            self.db.add(message)
        
        await self.db.commit()
        
        # Queue messages for sending using the singleton instance
        await message_queue.add_campaign(db_campaign.id)
        
        return schemas.CampaignResponse(
            id=db_campaign.id,
            status="QUEUED",
            total_messages=len(campaign.recipients),
            created_at=db_campaign.created_at
        )

    async def get_campaign(self, campaign_id: int) -> models.Campaign:
        """Get a campaign by ID"""
        query = select(models.Campaign).where(models.Campaign.id == campaign_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_campaign_status(self, campaign_id: int, include_details: bool = False) -> schemas.CampaignStatus:
        """Get campaign status with optional message details"""
        campaign = await self.get_campaign(campaign_id)
        if not campaign:
            raise ValueError("Campaign not found")
            
        status = schemas.CampaignStatus(
            id=campaign.id,
            status=campaign.status,
            total_messages=campaign.total_messages,
            sent_messages=campaign.sent_messages,
            failed_messages=campaign.failed_messages,
            created_at=campaign.created_at
        )
        
        if include_details:
            # Get message details
            query = select(models.Message).where(models.Message.campaign_id == campaign_id)
            result = await self.db.execute(query)
            status.messages = result.scalars().all()
            
        return status

    async def list_campaigns(
        self,
        sender_number: str = None,
        status: str = None,
        start_date: datetime = None,
        end_date: datetime = None
    ) -> list[schemas.CampaignList]:
        """List campaigns with optional filters"""
        query = select(models.Campaign)
        
        if sender_number:
            query = query.where(models.Campaign.sender_number == sender_number)
        if status:
            query = query.where(models.Campaign.status == status)
        if start_date:
            query = query.where(models.Campaign.created_at >= start_date)
        if end_date:
            query = query.where(models.Campaign.created_at <= end_date)
            
        query = query.order_by(models.Campaign.created_at.desc())
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_system_metrics(self, sender_number: str = None) -> schemas.SystemMetrics:
        """Get system metrics. If sender_number is provided, return stats for that sender only."""
        if sender_number:
            # Per-user stats
            session_query = select(func.count()).select_from(models.Session).where(
                models.Session.phone_number == sender_number,
                or_(models.Session.status == 'CONNECTED', models.Session.status == 'WORKING')
            )
            active_sessions = await self.db.execute(session_query)
            active_sessions = active_sessions.scalar() or 0

            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            messages_query = select(func.count()).select_from(models.Message).where(
                models.Message.sent_at >= today_start,
                models.Message.status == 'SENT',
                models.Message.campaign_id.in_(select(models.Campaign.id).where(models.Campaign.sender_number == sender_number))
            )
            messages_sent_today = await self.db.execute(messages_query)
            messages_sent_today = messages_sent_today.scalar() or 0

            queue_size = await message_queue.get_size()  # Per-user queue size not implemented, return global size

            campaigns_query = select(func.count()).select_from(models.Campaign).where(models.Campaign.sender_number == sender_number)
            total_campaigns = await self.db.execute(campaigns_query)
            total_campaigns = total_campaigns.scalar() or 0

            # Only 1 user for this sender
            total_users = 1
        else:
            # System-wide stats (admin)
            session_query = select(func.count()).select_from(models.Session).where(
                or_(models.Session.status == 'CONNECTED', models.Session.status == 'WORKING')
            )
            active_sessions = await self.db.execute(session_query)
            active_sessions = active_sessions.scalar() or 0
            
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            messages_query = select(func.count()).select_from(models.Message).where(
                models.Message.sent_at >= today_start,
                models.Message.status == 'SENT'
            )
            messages_sent_today = await self.db.execute(messages_query)
            messages_sent_today = messages_sent_today.scalar() or 0
            
            queue_size = await message_queue.get_size()
            
            campaigns_query = select(func.count()).select_from(models.Campaign)
            total_campaigns = await self.db.execute(campaigns_query)
            total_campaigns = total_campaigns.scalar() or 0
            
            users_query = select(func.count(models.Campaign.sender_number.distinct()))
            total_users = await self.db.execute(users_query)
            total_users = total_users.scalar() or 0
        
        uptime = (datetime.utcnow() - self.server_start_time).total_seconds() if hasattr(self, 'server_start_time') else 0
        return schemas.SystemMetrics(
            active_sessions=active_sessions,
            messages_sent_today=messages_sent_today,
            current_queue_size=queue_size,
            server_uptime=uptime,
            total_campaigns=total_campaigns,
            total_users=total_users
        )

    async def get_user_stats(self) -> list[schemas.UserStats]:
        """Get stats for all users"""
        # Get unique sender numbers with their campaign counts and message totals
        query = select(
            models.Campaign.sender_number,
            func.count(models.Campaign.id).label('total_campaigns'),
            func.sum(models.Campaign.total_messages).label('total_messages')
        ).group_by(models.Campaign.sender_number)
        
        result = await self.db.execute(query)
        users = result.fetchall()
        
        # Get session info for each user
        user_stats = []
        for user in users:
            session_query = select(models.Session).where(
                models.Session.phone_number == user.sender_number
            )
            session_result = await self.db.execute(session_query)
            session = session_result.scalar_one_or_none()

            # Get sent and failed messages for this user
            sent_query = select(func.sum(models.Campaign.sent_messages)).where(models.Campaign.sender_number == user.sender_number)
            sent_result = await self.db.execute(sent_query)
            sent_messages = sent_result.scalar() or 0

            failed_query = select(func.sum(models.Campaign.failed_messages)).where(models.Campaign.sender_number == user.sender_number)
            failed_result = await self.db.execute(failed_query)
            failed_messages = failed_result.scalar() or 0

            user_stats.append(schemas.UserStats(
                phone_number=user.sender_number,
                total_campaigns=user.total_campaigns,
                total_messages=user.total_messages or 0,
                sent_messages=sent_messages,
                failed_messages=failed_messages,
                active_session=bool(session and session.status == 'CONNECTED'),
                last_active=session.last_active if session else None
            ))
        return user_stats
