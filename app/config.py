from pydantic_settings import BaseSettings
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    # API Server Settings
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "5000"))
    api_reload: bool = os.getenv("API_RELOAD", "true").lower() == "true"

    # WAHA Settings
    waha_host: str = os.getenv("WAHA_HOST", "http://localhost")
    waha_port: int = int(os.getenv("WAHA_PORT", "3000"))

    # Database Settings
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./whatsapp_campaigns.db")
    db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "5"))
    db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))

    # Security Settings
    admin_api_key: str = os.getenv("ADMIN_API_KEY", "your-secure-admin-key-here")

    # Message Queue Settings
    message_delay: int = int(os.getenv("MESSAGE_DELAY", "2"))
    sender_switch_delay: int = int(os.getenv("SENDER_SWITCH_DELAY", "5"))

    # Logging Settings
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "./logs/whatsapp_backend.log")

settings = Settings()
