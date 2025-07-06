from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta
from app.models.models import Campaign, Message, Session
from app.services.message_queue import message_queue
from typing import List, Dict, Any
import logging

class AdminService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_system_metrics(self) -> Dict[str, Any]:
        """Get system-wide metrics"""
        try:
            # Get active sessions count
            session_query = select(func.count()).select_from(Session).where(
                Session.status == 'CONNECTED'
            )
            active_sessions = await self.db.execute(session_query)
            active_sessions = active_sessions.scalar() or 0
            
            # Get messages sent today
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            messages_query = select(func.count()).select_from(Message).where(
                Message.sent_at >= today_start,
                Message.status == 'SENT'
            )
            messages_sent_today = await self.db.execute(messages_query)
            messages_sent_today = messages_sent_today.scalar() or 0
            
            # Get current queue size from the singleton instance
            queue_size = await message_queue.get_size()
            
            # Get total campaigns and users
            campaigns_query = select(func.count()).select_from(Campaign)
            total_campaigns = await self.db.execute(campaigns_query)
            total_campaigns = total_campaigns.scalar() or 0
            
            users_query = select(func.count(Campaign.sender_number.distinct()))
            total_users = await self.db.execute(users_query)
            total_users = total_users.scalar() or 0
            
            return {
                "active_sessions": active_sessions,
                "messages_sent_today": messages_sent_today,
                "current_queue_size": queue_size,
                "total_campaigns": total_campaigns,
                "total_users": total_users,
                "last_refresh": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logging.error(f"Error getting system metrics: {str(e)}")
            raise

    async def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get all active WhatsApp sessions"""
        try:
            query = select(Session).where(Session.status == 'CONNECTED')
            result = await self.db.execute(query)
            sessions = result.scalars().all()
            
            return [
                {
                    "phone_number": session.phone_number,
                    "status": session.status,
                    "last_active": session.last_active.isoformat(),
                    "data": session.data
                }
                for session in sessions
            ]
        except Exception as e:
            logging.error(f"Error getting active sessions: {str(e)}")
            raise

    async def get_campaign_stats(self) -> Dict[str, Any]:
        """Get campaign statistics"""
        try:
            # Get total campaigns by status
            status_query = select(
                Campaign.status,
                func.count(Campaign.id).label('count')
            ).group_by(Campaign.status)
            status_result = await self.db.execute(status_query)
            status_counts = dict(status_result.fetchall())
            
            # Get message statistics
            message_query = select(
                Message.status,
                func.count(Message.id).label('count')
            ).group_by(Message.status)
            message_result = await self.db.execute(message_query)
            message_counts = dict(message_result.fetchall())
            
            # Get recent campaigns
            recent_query = select(Campaign).order_by(Campaign.created_at.desc()).limit(5)
            recent_result = await self.db.execute(recent_query)
            recent_campaigns = recent_result.scalars().all()
            
            return {
                "campaign_status": status_counts,
                "message_status": message_counts,
                "recent_campaigns": [
                    {
                        "id": c.id,
                        "sender_number": c.sender_number,
                        "status": c.status,
                        "total_messages": c.total_messages,
                        "sent_messages": c.sent_messages,
                        "failed_messages": c.failed_messages,
                        "created_at": c.created_at.isoformat()
                    }
                    for c in recent_campaigns
                ],
                "last_refresh": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logging.error(f"Error getting campaign statistics: {str(e)}")
            raise

    async def get_user_stats(self) -> List[Dict[str, Any]]:
        """Get statistics for all users"""
        try:
            # Query to get user campaign and message statistics
            query = select(
                Campaign.sender_number,
                func.count(Campaign.id).label('total_campaigns'),
                func.sum(Campaign.total_messages).label('total_messages'),
                func.sum(Campaign.sent_messages).label('sent_messages'),
                func.sum(Campaign.failed_messages).label('failed_messages')
            ).group_by(Campaign.sender_number)

            result = await self.db.execute(query)
            campaign_stats = result.fetchall()

            # Get session information for each user
            user_stats = []
            for stats in campaign_stats:
                # Get session status
                session_query = select(Session).where(
                    Session.phone_number == stats.sender_number
                )
                session_result = await self.db.execute(session_query)
                session = session_result.scalar_one_or_none()

                # Get recent campaign
                recent_campaign_query = (
                    select(Campaign)
                    .where(Campaign.sender_number == stats.sender_number)
                    .order_by(Campaign.created_at.desc())
                    .limit(1)
                )
                recent_result = await self.db.execute(recent_campaign_query)
                recent_campaign = recent_result.scalar_one_or_none()

                user_stats.append({
                    "phone_number": stats.sender_number,
                    "total_campaigns": stats.total_campaigns,
                    "total_messages": stats.total_messages or 0,
                    "sent_messages": stats.sent_messages or 0,
                    "failed_messages": stats.failed_messages or 0,
                    "active_session": bool(session and session.status == 'CONNECTED'),
                    "last_active": session.last_active.isoformat() if session else None,
                    "last_campaign": recent_campaign.created_at.isoformat() if recent_campaign else None
                })

            return user_stats
        except Exception as e:
            logging.error(f"Error getting user statistics: {str(e)}")
            raise
