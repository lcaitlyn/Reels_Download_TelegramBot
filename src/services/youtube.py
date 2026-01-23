"""
Сервис для работы с YouTube
Содержит всю специфичную логику для YouTube видео/Shorts
Знает только YouTube, формирует DownloadPlan
"""
import logging
from typing import Optional, Dict, Any
from src.models.download_plan import DownloadPlan
from .base import BaseService

logger = logging.getLogger(__name__)


class YouTubeService(BaseService):
    """
    Сервис для работы с YouTube видео
    
    Знает:
    - Форматы YouTube
    - Выбор качества
    - Опции yt-dlp для YouTube
    
    НЕ знает:
    - Redis
    - Telegram
    - Пользователей
    - Очереди
    """
    
    def can_handle(self, url: str) -> bool:
        """Может ли сервис обработать этот URL"""
        return 'youtube.com' in url.lower() or 'youtu.be' in url.lower()
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечь канонический ID видео YouTube"""
        return self.downloader.get_video_id(url)
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получить метаданные видео YouTube
        
        Args:
            url: URL видео
            
        Returns:
            Словарь с метаданными (id, duration, filesize, ext, etc.) или None
        """
        return self.downloader.get_video_info(url)
    
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """Получить доступные форматы для YouTube видео"""
        return self.downloader.get_available_formats(url)
    
    def build_download_plan(
        self,
        url: str,
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional[DownloadPlan]:
        """
        Построить план скачивания для YouTube
        
        Args:
            url: URL видео YouTube
            quality: Качество видео (480p, 720p, 1080p, audio) или None
            format_id: ID формата из yt-dlp (опционально)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        # Получаем метаданные
        metadata = self.get_metadata(url)
        if not metadata:
            logger.error("[YouTube] Не удалось получить метаданные")
            return None
        
        video_id = metadata.get('id')
        if not video_id:
            logger.error("[YouTube] Не удалось получить video_id из метаданных")
            return None
        
        # Определяем формат
        format_selector = self._prepare_format_selector(format_id, quality)
        
        # Определяем размер файла
        filesize = metadata.get('filesize') or metadata.get('filesize_approx', 0)
        filesize_mb = filesize / (1024 * 1024) if filesize else 0
        
        # Формируем опции yt-dlp для YouTube
        ydl_opts = self._get_ydl_opts_for_youtube(format_selector)
        
        # Определяем, можно ли стримить в память (<50MB)
        streamable = filesize_mb < 50 if filesize else False
        
        # Определяем, только ли аудио
        audio_only = quality == 'audio' if quality else False
        
        return DownloadPlan(
            platform='youtube',
            video_id=f"youtube:{video_id}",
            url=url,
            format_selector=format_selector,
            quality=quality,
            audio_only=audio_only,
            streamable=streamable,
            ydl_opts=ydl_opts,
            metadata=metadata
        )
    
    def _prepare_format_selector(
        self,
        format_id: Optional[str],
        quality: Optional[str]
    ) -> str:
        """
        Подготовить селектор формата для YouTube
        
        Args:
            format_id: ID формата (может быть video only, тогда добавляем аудио)
            quality: Качество видео (480p, 720p, 1080p, audio)
            
        Returns:
            Селектор формата для yt-dlp
        """
        # Если указан format_id, используем его
        if format_id:
            # Проверяем, является ли format_id форматом "video only"
            try:
                if format_id.isdigit() or format_id.startswith(('135', '136', '137', '160', '133', '134')):
                    # Это video only формат, добавляем аудио
                    format_selector = f"{format_id}+bestaudio/best"
                    logger.info(f"[YouTube] Добавляю аудио дорожку к формату {format_id}: {format_selector}")
                    return format_selector
            except:
                pass
            return format_id
        
        # Если указано качество, используем его
        if quality:
            if quality == 'audio':
                return 'bestaudio/best'
            elif quality == '480p':
                return 'best[height<=480][ext=mp4]/best[height<=480]'
            elif quality == '720p':
                return 'best[height<=720][ext=mp4]/best[height<=720]'
            elif quality == '1080p':
                return 'best[height<=1080][ext=mp4]/best[height<=1080]'
        
        # По умолчанию для Shorts - низкое качество
        return 'best[height<=360][ext=mp4]/best[height<=240][ext=mp4]/best[height<=144][ext=mp4]/best[ext=mp4]/best'
    
    def _get_ydl_opts_for_youtube(self, format_selector: str) -> Dict[str, Any]:
        """
        Получить опции yt-dlp для YouTube
        
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
            'concurrent_fragments': 1,  # Меньше параллельных фрагментов (стабильнее на медленном интернете)
            'http_chunk_size': 1048576,  # 1MB чанки
            'postprocessors': [],  # Отключаем постобработку
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writethumbnail': False,
        }
    
    # Методы для обратной совместимости (будут удалены)
    def get_video_id(self, url: str) -> Optional[str]:
        """DEPRECATED: Используйте extract_video_id()"""
        return self.extract_video_id(url)
    
    def get_default_format(self) -> str:
        """Формат по умолчанию для YouTube (для Shorts)"""
        return 'best[height<=360][ext=mp4]/best[height<=240][ext=mp4]/best[height<=144][ext=mp4]/best[ext=mp4]/best'
