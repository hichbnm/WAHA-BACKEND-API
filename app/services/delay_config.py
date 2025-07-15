from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.delays import DelayConfig

async def get_delay_config(db: AsyncSession) -> DelayConfig:
    result = await db.execute(select(DelayConfig).order_by(DelayConfig.id.desc()))
    config = result.scalar_one_or_none()
    if not config:
        # Create default config from env if not exists
        import os
        config = DelayConfig(
            message_delay=int(os.getenv("MESSAGE_DELAY", 2)),
            sender_switch_delay=int(os.getenv("SENDER_SWITCH_DELAY", 5)),
            campaign_delay=int(os.getenv("CAMPAIGN_DELAY", 10))
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)
    return config

async def set_delay_config(db: AsyncSession, message_delay=None, sender_switch_delay=None, campaign_delay=None):
    config = await get_delay_config(db)
    if message_delay is not None:
        config.message_delay = message_delay
    if sender_switch_delay is not None:
        config.sender_switch_delay = sender_switch_delay
    if campaign_delay is not None:
        config.campaign_delay = campaign_delay
    await db.commit()
    await db.refresh(config)
    return config
