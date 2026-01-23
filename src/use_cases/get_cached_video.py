"""
Use case: Получение видео из кэша
"""
import logging
from typing import Optional
from src.database.redis_db import Database
from src.utils.utils import normalize_url, get_video_id_fast

logger = logging.getLogger(__name__)


class GetCachedVideoUseCase:
    """Use case для получения видео из кэша"""
    
    def __init__(self, db: Database, downloader):
        """
        Args:
            db: Экземпляр Database для работы с кэшем
            downloader: Экземпляр VideoDownloader для получения video_id
        """
        self.db = db
        self.downloader = downloader
    
    async def execute(self, url: str, quality: Optional[str] = None) -> Optional[dict]:
        """
        Получить видео из кэша
        
        Args:
            url: URL видео
            quality: Качество видео (для YouTube) или None
            
        Returns:
            dict с ключами:
                - message_id: ID сообщения в канале
                - file_id: file_id для отправки
                - video_id: канонический ID видео
            или None если видео не найдено в кэше
        """
        normalized_url = normalize_url(url)
        
        # Сначала проверяем кэш по URL напрямую (БЫСТРО)
        cached_message_id = await self.db.get_cached_message_id(url=normalized_url, quality=quality)
        
        # Если не нашли по URL, пытаемся получить video_id и проверить по нему
        if not cached_message_id:
            # Сначала пытаемся получить video_id быстрым способом (без HTTP-запросов)
            video_id, _ = get_video_id_fast(normalized_url)
            if video_id:
                cached_message_id = await self.db.get_cached_message_id(video_id=video_id, quality=quality)
            # Если быстрый способ не сработал (например, для TikTok), используем yt-dlp (МЕДЛЕННО)
            if not cached_message_id:
                video_id = self.downloader.get_video_id(normalized_url)
                if video_id:
                    cached_message_id = await self.db.get_cached_message_id(video_id=video_id, quality=quality)
        
        if not cached_message_id or cached_message_id == 0:
            return None
        
        # Получаем file_id из кэша
        video_id, _ = get_video_id_fast(normalized_url)
        if not video_id:
            video_id = self.downloader.get_video_id(normalized_url)
        if not video_id:
            video_id = normalized_url  # Fallback
        
        cached_file_id = await self.db.get_cached_file_id(video_id=video_id, url=normalized_url, quality=quality)
        
        return {
            'message_id': cached_message_id,
            'file_id': cached_file_id,
            'video_id': video_id,
            'url': normalized_url
        }
