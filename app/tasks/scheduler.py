import os
import logging
from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.core.database import AsyncSessionFactory
from app.core.config import settings
from app.services.sync_service import SyncService
from app.services.strava_client import StravaClient
from app.services.whatsapp import send_message, format_weekly_report
from app.models.user import User

# Pick up TZ from env (e.g. Railway). Defaults to UTC if unset.
SCHEDULER_TZ = os.environ.get('TZ', 'UTC')

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def auto_sync_job():
    logger.info('Auto-sync triggered')
    async with AsyncSessionFactory() as db:
        try:
            strava = StravaClient(db)
            service = SyncService(db=db, strava=strava)
            result = await service.run()
            await db.commit()
            logger.info(f'Auto-sync complete: {result}')
        except Exception:
            await db.rollback()
            logger.exception('Auto-sync failed')


async def weekly_whatsapp_job():
    if not (settings.twilio_account_sid and settings.twilio_auth_token
            and settings.twilio_whatsapp_from and settings.twilio_whatsapp_to):
        logger.info('Twilio WhatsApp not configured — skipping weekly report')
        return
    logger.info('Weekly WhatsApp report triggered')
    async with AsyncSessionFactory() as db:
        user = (await db.execute(select(User))).scalars().first()
        if not user:
            logger.warning('No user in DB — skipping weekly WhatsApp report')
            return
        from app.api.analytics import weekly_report as get_report
        data = await get_report(user_id=user.id, week_offset=0, db=db)
    await send_message(format_weekly_report(data))


def start_scheduler():
    scheduler.add_job(auto_sync_job, 'interval', minutes=30, id='auto_sync')
    scheduler.add_job(
        weekly_whatsapp_job,
        'cron', day_of_week='sat', hour=9, minute=0,
        id='weekly_whatsapp',
        timezone=SCHEDULER_TZ,
    )
    scheduler.start()
    logger.info('Auto-sync every 30 min · Weekly WhatsApp every Sat 9:00 (%s)', SCHEDULER_TZ)


def stop_scheduler():
    scheduler.shutdown()
