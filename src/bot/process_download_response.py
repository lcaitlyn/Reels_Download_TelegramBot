"""
Общая логика обработки ответа DownloadManager после request_download.
Используется в process_video_download (bot) и в YouTubeHandler.handle_quality_callback.
"""
import logging
from typing import Optional, Callable, Awaitable, Any

from src.models.download_response import DownloadResponse

logger = logging.getLogger(__name__)


async def process_download_response(
    response: DownloadResponse,
    *,
    chat_id: int,
    user_id: int,
    job_id: str,
    platform: str,
    source: str,
    quality: Optional[str] = None,
    db: Any,
    clear_status: Callable[[], Awaitable[None]],
    send_video: Callable[[int, int], Awaitable[bool]],
    send_error: Callable[[int, str], Awaitable[None]],
    publish_analytics: Callable[[int, str, str, str], Awaitable[None]],
    notify_in_progress: Optional[Callable[[], Awaitable[None]]] = None,
    notify_queued: Optional[Callable[[], Awaitable[None]]] = None,
) -> None:
    """
    Обработать ответ DownloadManager: error / ready / in_progress / queued.
    Убирает статусное сообщение, отправляет видео или ошибку, публикует аналитику при успехе.
    """
    if response.is_error():
        await clear_status()
        await send_error(chat_id, "generic")
        if getattr(response, "error", None):
            logger.error("Ошибка при запросе скачивания: %s", response.error)
        return

    if response.is_ready():
        await clear_status()
        success = await send_video(chat_id, response.message_id)
        if success:
            await publish_analytics(user_id, job_id, platform, source)
        else:
            await send_error(chat_id, "generic")
        return

    if response.is_in_progress():
        await clear_status()
        if notify_in_progress:
            await notify_in_progress()
        # TODO добавить timeout из config
        message_id = await db.wait_for_download(job_id, timeout=1800.0, quality=quality)
        if message_id:
            success = await send_video(chat_id, message_id)
            if success:
                await publish_analytics(user_id, job_id, platform, source)
            else:
                await send_error(chat_id, "generic")
        else:
            await send_error(chat_id, "download_failed")
        return

    if response.is_queued():
        await clear_status()
        if notify_queued:
            await notify_queued()
        # TODO добавить timeout из config
        message_id = await db.wait_for_download(job_id, timeout=1800.0, quality=quality)
        if message_id:
            success = await send_video(chat_id, message_id)
            if success:
                await publish_analytics(user_id, job_id, platform, source)
            else:
                await send_error(chat_id, "generic")
        else:
            await send_error(chat_id, "download_failed")
        return

    logger.warning("Неожиданный статус DownloadResponse: %s", response.status)
    await clear_status()
    await send_error(chat_id, "generic")
