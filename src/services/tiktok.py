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

    def get_ydl_opts(self) -> Dict[str, Any]:
        """Опции yt-dlp для get_info (только информация)."""
        return {'quiet': True, 'no_warnings': True, 'extract_flat': False}

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
        
        ОПТИМИЗАЦИЯ: Не получаем метаданные здесь - это блокирует event loop.
        Метаданные будут получены во время скачивания через yt-dlp.
        
        Args:
            url: URL видео TikTok
            quality: Качество (для TikTok не используется)
            format_id: ID формата (опционально)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        # Извлекаем video_id из URL (быстро, без запросов к API)
        video_id = self.extract_video_id(url)
        if not video_id:
            logger.error("[TikTok] Не удалось извлечь video_id из URL")
            return None
        
        # Формируем опции yt-dlp для TikTok
        format_selector = format_id if format_id else 'worst[ext=mp4]/worst[ext=webm]/worst'
        ydl_opts = self._get_ydl_opts_for_tiktok(format_selector)
        
        # Не знаем размер файла заранее - будем определять во время скачивания
        # По умолчанию считаем, что можно стримить (для маленьких файлов)
        # Если файл окажется большим, worker переключится на файловый режим
        streamable = True  # Будет переопределено во время скачивания
        telegram_caption = f"Source: {url}"
        
        return DownloadPlan(
            platform='tiktok',
            video_id=f"tiktok:{video_id}",
            url=url,
            format_selector=format_selector,
            streamable=streamable,
            media_type='video',
            telegram_caption=telegram_caption,
            ydl_opts=ydl_opts,
            metadata=None  # Метаданные будут получены во время скачивания
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
    
