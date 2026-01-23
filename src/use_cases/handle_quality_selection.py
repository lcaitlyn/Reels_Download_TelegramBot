"""
Use case: Обработка выбора качества для YouTube видео
"""
import logging
from typing import Optional
from src.database.redis_db import Database
from src.utils.utils import get_platform

logger = logging.getLogger(__name__)


class HandleQualitySelectionUseCase:
    """Use case для обработки выбора качества YouTube видео"""
    
    def __init__(self, db: Database, downloader):
        """
        Args:
            db: Экземпляр Database для работы с кэшем
            downloader: Экземпляр VideoDownloader для получения форматов
        """
        self.db = db
        self.downloader = downloader
    
    async def get_available_qualities(self, url: str, video_id: str) -> Optional[dict]:
        """
        Получить доступные качества для YouTube видео
        
        Args:
            url: URL YouTube видео
            video_id: Канонический ID видео
            
        Returns:
            dict с ключами:
                - formats: словарь форматов {quality_label: format_info}
                - cached_qualities: список качеств, которые есть в кэше
        """
        formats = self.downloader.get_available_formats(url)
        
        if not formats:
            return None
        
        # Проверяем, какие качества есть в кэше
        cached_qualities = []
        for quality_label in ['480p', '720p', '1080p', 'audio']:
            if quality_label in formats:
                cached = await self.db.check_quality_in_cache(video_id, quality_label)
                if cached:
                    cached_qualities.append(quality_label)
        
        return {
            'formats': formats,
            'cached_qualities': cached_qualities
        }
    
    async def get_quality_info(self, url: str, quality_label: str) -> Optional[dict]:
        """
        Получить информацию о формате для выбранного качества
        
        Args:
            url: URL YouTube видео
            quality_label: Метка качества ('480p', '720p', '1080p', 'audio')
            
        Returns:
            dict с ключами:
                - format_id: ID формата из yt-dlp
                - filesize: размер файла (если доступен)
            или None если качество недоступно
        """
        formats = self.downloader.get_available_formats(url)
        
        if not formats or quality_label not in formats:
            return None
        
        return formats[quality_label]
    
    async def get_cached_quality(self, video_id: str, quality_label: str) -> Optional[dict]:
        """
        Получить видео с указанным качеством из кэша
        
        Args:
            video_id: Канонический ID видео
            quality_label: Метка качества
            
        Returns:
            dict с ключами:
                - message_id: ID сообщения в канале
                - file_id: file_id для отправки
            или None если не найдено
        """
        cached_message_id = await self.db.get_cached_message_id(video_id=video_id, quality=quality_label)
        
        if not cached_message_id or cached_message_id == 0:
            return None
        
        cached_file_id = await self.db.get_cached_file_id(video_id=video_id, quality=quality_label)
        
        return {
            'message_id': cached_message_id,
            'file_id': cached_file_id
        }
