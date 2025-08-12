from celery import Celery
import os
from dotenv import load_dotenv

# Load .env so workers get the same configuration as the API
# Use override=True so .env wins over any inherited environment
load_dotenv(override=True)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "waha_backend",
    broker=REDIS_URL,
    backend=REDIS_URL
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

# Ensure tasks are registered when worker starts
from tasks import campaign_tasks  # noqa: F401
from tasks import message_tasks  # noqa: F401
