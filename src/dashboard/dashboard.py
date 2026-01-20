"""
Простой dashboard API для просмотра аналитики.
Отдельный сервис, не влияет на основной бот.
"""
import os
import logging
from typing import Optional

from fastapi import FastAPI, Query

from src.database.analytics_db import AnalyticsDB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Downloader Bot Analytics", version="0.1.0")

analytics_db: Optional[AnalyticsDB] = None


@app.on_event("startup")
async def on_startup():
    global analytics_db
    analytics_db = AnalyticsDB()
    await analytics_db.connect()
    logger.info("AnalyticsDB подключен для dashboard")


@app.on_event("shutdown")
async def on_shutdown():
    global analytics_db
    if analytics_db:
        await analytics_db.close()
        logger.info("AnalyticsDB отключен для dashboard")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats/summary")
async def stats_summary():
    """
    Общая сводка:
    - количество пользователей
    - количество видео
    - общее количество скачиваний
    """
    data = await analytics_db.get_summary()
    return data


@app.get("/stats/top-videos")
async def stats_top_videos(limit: int = Query(10, ge=1, le=100)):
    """
    Топ популярных видео.
    """
    videos = await analytics_db.get_top_videos(limit=limit)
    return {"items": videos, "limit": limit}


@app.get("/stats/platforms")
async def stats_platforms():
    """
    Сводка по платформам (YouTube/Instagram/TikTok).
    """
    platforms = await analytics_db.get_platform_stats()
    return platforms


@app.get("/stats/active-users")
async def stats_active_users(days: int = Query(7, ge=1, le=365)):
    """
    Количество активных пользователей за последние N дней.
    """
    count = await analytics_db.get_active_users_count(days=days)
    return {"days": days, "active_users": count}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("dashboard:app", host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8000")), reload=False)

