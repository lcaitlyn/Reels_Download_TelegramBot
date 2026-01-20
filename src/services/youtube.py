"""
Сервис для работы с YouTube
"""
from typing import Optional, Dict, Any
import logging
from .base import BaseService

logger = logging.getLogger(__name__)


class YouTubeService(BaseService):
    """Сервис для работы с YouTube видео"""
    
    def get_video_id(self, url: str) -> Optional[str]:
        """Получить канонический ID видео YouTube"""
        return self.downloader.get_video_id(url)
    
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """Получить доступные форматы для YouTube видео"""
        return self.downloader.get_available_formats(url)
    
    def get_default_format(self) -> str:
        """Формат по умолчанию для YouTube (для Shorts)"""
        return 'best[height<=360][ext=mp4]/best[height<=240][ext=mp4]/best[height<=144][ext=mp4]/best[ext=mp4]/best'
    
    def download_video(self, url: str, format_id: Optional[str] = None) -> Optional[tuple]:
        """Скачать видео YouTube"""
        return self.downloader.download_video_stream(url, format_id)
