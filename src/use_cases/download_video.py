"""
Use case: Скачивание видео
"""
import logging
from typing import Optional
from src.database.redis_db import Database
from src.utils.utils import normalize_url, get_video_id_fast, get_platform
from src.events.events import DownloadCompletedEvent

logger = logging.getLogger(__name__)


class DownloadVideoUseCase:
    """Use case для скачивания видео"""
    
    def __init__(self, db: Database, downloader):
        """
        Args:
            db: Экземпляр Database для работы с кэшем и очередями
            downloader: Экземпляр VideoDownloader для скачивания
        """
        self.db = db
        self.downloader = downloader
    
    async def execute(
        self,
        url: str,
        user_id: int,
        source: str = 'message',
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional[dict]:
        """
        Скачать видео или добавить в очередь
        
        Args:
            url: URL видео для скачивания
            user_id: ID пользователя (для аналитики)
            source: Источник запроса ('message', 'inline', 'deep_link')
            quality: Качество видео (для YouTube) или None
            format_id: ID формата из yt-dlp (для YouTube) или None
            
        Returns:
            dict с ключами:
                - message_id: ID сообщения в канале (если уже в кэше)
                - file_id: file_id для отправки (если уже в кэше)
                - video_id: канонический ID видео
                - status: 'cached' или 'queued'
            или None при ошибке
        """
        normalized_url = normalize_url(url)
        platform = get_platform(normalized_url)
        
        # Получаем video_id
        video_id, _ = get_video_id_fast(normalized_url)
        if not video_id:
            video_id = self.downloader.get_video_id(normalized_url)
        if not video_id:
            video_id = normalized_url  # Fallback
        
        # Проверяем кэш
        try:
            cached_message_id = await self.db.get_cached_message_id(
                video_id=video_id,
                url=normalized_url,
                quality=quality
            )
        except Exception as redis_err:
            logger.error(f"⚠️ Redis недоступен: {redis_err}, работаю без кэша")
            cached_message_id = None
        
        if cached_message_id and cached_message_id != 0:
            # Видео уже в кэше
            cached_file_id = await self.db.get_cached_file_id(
                video_id=video_id,
                url=normalized_url,
                quality=quality
            )
            return {
                'message_id': cached_message_id,
                'file_id': cached_file_id,
                'video_id': video_id,
                'status': 'cached'
            }
        
        # Видео нет в кэше - добавляем задачу в очередь
        try:
            task_added = await self.db.add_download_task(
                url=normalized_url,
                video_id=video_id,
                platform=platform,
                quality=quality,
                format_id=format_id
            )
        except Exception as redis_err:
            logger.error(f"⚠️ Redis недоступен при добавлении задачи: {redis_err}")
            return None
        
        if task_added:
            logger.info(f"Задача добавлена в очередь для video_id={video_id}")
        else:
            logger.info(f"Задача уже обрабатывается для video_id={video_id}")
        
        return {
            'video_id': video_id,
            'status': 'queued'
        }
    
    async def wait_for_completion(
        self,
        video_id: str,
        timeout: float = 1800.0,
        quality: Optional[str] = None
    ) -> Optional[int]:
        """
        Ожидать завершения скачивания видео
        
        Args:
            video_id: Канонический ID видео
            timeout: Максимальное время ожидания в секундах
            quality: Качество видео (для YouTube) или None
            
        Returns:
            message_id когда видео скачано, или None при timeout/ошибке
        """
        return await self.db.wait_for_download(video_id, timeout=timeout, quality=quality)
    
    async def publish_download_event(
        self,
        user_id: int,
        video_id: str,
        platform: str,
        source: str
    ):
        """
        Публикует событие DownloadCompletedEvent (не блокируя)
        
        Args:
            user_id: ID пользователя
            video_id: Канонический ID видео
            platform: Платформа (youtube, instagram, tiktok)
            source: Источник запроса
        """
        try:
            event = DownloadCompletedEvent(
                user_id=user_id,
                video_id=video_id,
                platform=platform,
                source=source
            )
            await self.db.add_analytics_event(event.to_json())
        except Exception as e:
            logger.error(f"Ошибка при публикации события DownloadCompletedEvent: {e}")
