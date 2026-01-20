"""
Сервис для работы с Instagram
"""
from typing import Optional, Dict, Any
import logging
from .base import BaseService

logger = logging.getLogger(__name__)


class InstagramService(BaseService):
    """Сервис для работы с Instagram видео"""
    
    def get_video_id(self, url: str) -> Optional[str]:
        """Получить канонический ID видео Instagram"""
        return self.downloader.get_video_id(url)
    
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """Instagram не поддерживает выбор качества, возвращаем None"""
        return None
    
    def get_default_format(self) -> str:
        """Формат по умолчанию для Instagram"""
        return 'best[ext=mp4]/best'
    
    def download_video(self, url: str, format_id: Optional[str] = None) -> Optional[tuple]:
        """Скачать видео Instagram"""
        return self.downloader.download_video_stream(url, format_id)
