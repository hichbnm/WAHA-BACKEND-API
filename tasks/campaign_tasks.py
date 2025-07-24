

from celery_app import celery_app
from app.services.message_queue import message_queue
import app.db.database
import asyncio

@celery_app.task
def process_campaign_task(campaign_id):
    # This function will be run by a Celery worker
    # Use the correct event loop handling for Celery/asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(message_queue.process_campaign_by_id(app.db.database.engine, campaign_id))
