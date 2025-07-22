from celery_app import celery_app
from app.services.message_queue import message_queue
from app.db.database import engine
import asyncio

@celery_app.task
def process_campaign_task(campaign_id):
    # This function will be run by a Celery worker
    # You may need to refactor your message_queue logic to support this
    loop = asyncio.get_event_loop()
    loop.run_until_complete(message_queue.process_campaign_by_id(engine, campaign_id))
