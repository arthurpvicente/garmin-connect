import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.core.database import AsyncSessionFactory
from app.services.sync_service import SyncService
from app.services.strava_client import StravaClient

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


def start_scheduler():
    # Note: the weekly WhatsApp send is handled by GitHub Actions
    # (.github/workflows/weekly.yml) so it fires even when this app is off.
    scheduler.add_job(auto_sync_job, 'interval', minutes=30, id='auto_sync')
    scheduler.start()
    logger.info('Auto-sync every 30 min (weekly WhatsApp handled by GitHub Actions)')


def stop_scheduler():
    scheduler.shutdown()
