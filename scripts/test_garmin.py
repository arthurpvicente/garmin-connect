import asyncio
import datetime
from app.services.garmin_client import fetch_daily_metrics

if __name__ == "__main__":
    result = asyncio.run(fetch_daily_metrics(datetime.date.today()))
    print(result)
