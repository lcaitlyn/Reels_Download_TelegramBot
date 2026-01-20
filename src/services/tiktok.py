"""
Сервис для работы с TikTok
"""
from typing import Optional, Dict, Any
import logging
from .base import BaseService

logger = logging.getLogger(__name__)


class TikTokService(BaseService):
    """Сервис для работы с TikTok видео"""
    
    def get_video_id(self, url: str) -> Optional[str]:
        """Получить канонический ID видео TikTok"""
        return self.downloader.get_video_id(url)
    
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """TikTok не поддерживает выбор качества, возвращаем None"""
        return None
    
    def get_default_format(self) -> str:
        """Формат по умолчанию для TikTok"""
        return 'worst[ext=mp4]/worst[ext=webm]/worst'
    
    def download_video(self, url: str, format_id: Optional[str] = None) -> Optional[tuple]:
        """Скачать видео TikTok"""
        return self.downloader.download_video_stream(url, format_id)
