from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.models.delays import DelayConfig
import logging
from typing import Optional

async def get_delay_config(db: AsyncSession) -> DelayConfig:
    result = await db.execute(select(DelayConfig).order_by(DelayConfig.id.desc()))
    config = result.scalar_one_or_none()
    if not config:
        # Create default config from env if not exists, with safe parsing
        import os
        def _env_int(name: str, default: int) -> int:
            val = os.getenv(name)
            if val is None or str(val).strip() == "":
                return default
            try:
                cleaned = str(val).split('#', 1)[0].strip()
                return int(cleaned)
            except Exception:
                return default
        md = _env_int("MESSAGE_DELAY", 2)
        ssd = _env_int("SENDER_SWITCH_DELAY", 5)
        cd = _env_int("CAMPAIGN_DELAY", 10)
        logging.debug(f"[DelayConfig] Seeding defaults: MESSAGE_DELAY={md}, SENDER_SWITCH_DELAY={ssd}, CAMPAIGN_DELAY={cd}")
        config = DelayConfig(
            message_delay=md,
            sender_switch_delay=ssd,
            campaign_delay=cd
        )
        db.add(config)
        await db.commit()
        await db.refresh(config)
    else:
        # Normalize if existing row has null/invalid values
        updated = False
        def _fix(val: Optional[int], default: int, min_val: int = 1) -> int:
            try:
                if val is None:
                    return default
                v = int(val)
                return max(min_val, v)
            except Exception:
                return default
        import os
        def _env_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                return int(str(raw).split('#', 1)[0].strip())
            except Exception:
                return default
        md_default = _env_int("MESSAGE_DELAY", 2)
        ssd_default = _env_int("SENDER_SWITCH_DELAY", 5)
        cd_default = _env_int("CAMPAIGN_DELAY", 10)
        new_md = _fix(getattr(config, 'message_delay', None), md_default)
        new_ssd = _fix(getattr(config, 'sender_switch_delay', None), ssd_default)
        new_cd = _fix(getattr(config, 'campaign_delay', None), cd_default)
        if new_md != config.message_delay:
            config.message_delay = new_md
            updated = True
        if new_ssd != config.sender_switch_delay:
            config.sender_switch_delay = new_ssd
            updated = True
        if new_cd != config.campaign_delay:
            config.campaign_delay = new_cd
            updated = True
        if updated:
            logging.debug(f"[DelayConfig] Normalized existing config: MESSAGE_DELAY={config.message_delay}, SENDER_SWITCH_DELAY={config.sender_switch_delay}, CAMPAIGN_DELAY={config.campaign_delay}")
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
