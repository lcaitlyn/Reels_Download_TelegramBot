"""
Сервис для работы с TikTok
Содержит всю специфичную логику для TikTok видео
Знает только TikTok, формирует DownloadPlan
"""
import logging
from typing import Optional, Dict, Any
from src.models.download_plan import DownloadPlan
from .base import BaseService

logger = logging.getLogger(__name__)


class TikTokService(BaseService):
    """
    Сервис для работы с TikTok видео
    
    Знает:
    - Форматы TikTok
    - Опции yt-dlp для TikTok
    
    НЕ знает:
    - Redis
    - Telegram
    - Пользователей
    - Очереди
    """
    
    def can_handle(self, url: str) -> bool:
        """Может ли сервис обработать этот URL"""
        return 'tiktok.com' in url.lower()
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечь канонический ID видео TikTok"""
        return self.downloader.get_video_id(url)
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получить метаданные видео TikTok
        
        Args:
            url: URL видео
            
        Returns:
            Словарь с метаданными (id, duration, filesize, ext, etc.) или None
        """
        return self.downloader.get_video_info(url)
    
    def build_download_plan(
        self,
        url: str,
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional[DownloadPlan]:
        """
        Построить план скачивания для TikTok
        
        Args:
            url: URL видео TikTok
            quality: Качество (для TikTok не используется)
            format_id: ID формата (опционально)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        # Получаем метаданные
        metadata = self.get_metadata(url)
        if not metadata:
            logger.error("[TikTok] Не удалось получить метаданные")
            return None
        
        video_id = metadata.get('id')
        if not video_id:
            logger.error("[TikTok] Не удалось получить video_id из метаданных")
            return None
        
        # Определяем размер файла
        filesize = metadata.get('filesize') or metadata.get('filesize_approx', 0)
        filesize_mb = filesize / (1024 * 1024) if filesize else 0
        
        # Формируем опции yt-dlp для TikTok
        format_selector = format_id if format_id else 'worst[ext=mp4]/worst[ext=webm]/worst'
        ydl_opts = self._get_ydl_opts_for_tiktok(format_selector)
        
        # Определяем, можно ли стримить в память (<50MB)
        streamable = filesize_mb < 50 if filesize else False
        
        return DownloadPlan(
            platform='tiktok',
            video_id=f"tiktok:{video_id}",
            url=url,
            format_selector=format_selector,
            streamable=streamable,
            ydl_opts=ydl_opts,
            metadata=metadata
        )
    
    def _get_ydl_opts_for_tiktok(self, format_selector: str) -> Dict[str, Any]:
        """
        Получить опции yt-dlp для TikTok
        
        Args:
            format_selector: Селектор формата
            
        Returns:
            Словарь с опциями yt-dlp
        """
        return {
            'format': format_selector,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'extract_flat': False,
            'postprocessors': [],  # Отключаем постобработку (не требуется ffmpeg)
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writethumbnail': False,
        }
    
    # Методы для обратной совместимости (будут удалены)
    def get_video_id(self, url: str) -> Optional[str]:
        """DEPRECATED: Используйте extract_video_id()"""
        return self.extract_video_id(url)
    
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """TikTok не поддерживает выбор качества"""
        return None
    
    def get_default_format(self) -> str:
        """Формат по умолчанию для TikTok"""
        return 'worst[ext=mp4]/worst[ext=webm]/worst'
