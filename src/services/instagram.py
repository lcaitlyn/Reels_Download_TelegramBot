"""
Сервис для работы с Instagram
Содержит всю специфичную логику для Instagram Reels/Posts
Знает только Instagram, формирует DownloadPlan
"""
import logging
from typing import Optional, Dict, Any
from src.models.download_plan import DownloadPlan
from .base import BaseService

logger = logging.getLogger(__name__)


class InstagramService(BaseService):
    """
    Сервис для работы с Instagram видео
    
    Знает:
    - Форматы Instagram
    - Опции yt-dlp для Instagram
    - Ограничения Instagram
    
    НЕ знает:
    - Redis
    - Telegram
    - Пользователей
    - Очереди
    """
    
    def can_handle(self, url: str) -> bool:
        """Может ли сервис обработать этот URL"""
        return 'instagram.com' in url.lower()
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечь канонический ID видео Instagram"""
        return self.downloader.get_video_id(url)
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получить метаданные видео Instagram
        
        Args:
            url: URL видео
            
        Returns:
            Словарь с метаданными (id, duration, filesize, ext, etc.) или None
        """
        info_opts = self._get_info_opts_for_instagram()
        return self.downloader.get_video_info(url, info_opts)
    
    def build_download_plan(
        self,
        url: str,
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional[DownloadPlan]:
        """
        Построить план скачивания для Instagram
        
        Args:
            url: URL видео Instagram
            quality: Качество (для Instagram не используется)
            format_id: ID формата (для Instagram не используется)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        # Получаем метаданные
        metadata = self.get_metadata(url)
        if not metadata:
            logger.error("[Instagram] Не удалось получить метаданные")
            return None
        
        video_id = metadata.get('id')
        if not video_id:
            logger.error("[Instagram] Не удалось получить video_id из метаданных")
            return None
        
        # Проверяем доступность видео
        if not metadata.get('url'):
            logger.error("[Instagram] ❌ Видео недоступно. Возможные причины:")
            logger.error("  - Видео приватное или требует авторизацию")
            logger.error("  - Видео удалено или недоступно")
            logger.error("  - Instagram заблокировал доступ")
            return None
        
        # Определяем размер файла
        filesize = metadata.get('filesize') or metadata.get('filesize_approx', 0)
        filesize_mb = filesize / (1024 * 1024) if filesize else 0
        
        # Формируем опции yt-dlp для Instagram
        format_selector = 'best[ext=mp4]/best'
        ydl_opts = self._get_ydl_opts_for_instagram(format_selector)
        
        # Определяем, можно ли стримить в память (<50MB)
        streamable = filesize_mb < 50 if filesize else False
        
        return DownloadPlan(
            platform='instagram',
            video_id=f"instagram:{video_id}",
            url=url,
            format_selector=format_selector,
            streamable=streamable,
            ydl_opts=ydl_opts,
            metadata=metadata
        )
    
    def _get_info_opts_for_instagram(self) -> Dict[str, Any]:
        """Получить опции для получения информации о видео Instagram"""
        info_opts = {
            'quiet': False,  # Включаем вывод для отладки
            'no_warnings': False,
            'extract_flat': False,
        }
        
        try:
            info_opts['extractor_args'] = {'instagram': {'webpage_download': False}}
        except:
            pass
        
        return info_opts
    
    def _get_ydl_opts_for_instagram(self, format_selector: str) -> Dict[str, Any]:
        """
        Получить опции yt-dlp для Instagram
        
        Args:
            format_selector: Селектор формата
            
        Returns:
            Словарь с опциями yt-dlp
        """
        ydl_opts = {
            'format': format_selector,
            'quiet': False,  # Включаем вывод для отладки Instagram
            'no_warnings': False,  # Показываем предупреждения
            'noplaylist': True,
            'extract_flat': False,
            'postprocessors': [],  # Отключаем постобработку (не требуется ffmpeg)
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writethumbnail': False,
            'nopart': True,  # Не создавать частичные файлы
            'continue_dl': False,  # Не продолжать скачивание (всегда скачивать заново)
        }
        
        # Специальные опции для Instagram
        try:
            ydl_opts['extractor_args'] = {'instagram': {'webpage_download': False}}
        except:
            pass
        
        # Добавляем опции для обхода ограничений Instagram
        ydl_opts['cookiefile'] = None  # Можно указать путь к cookies файлу
        ydl_opts['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        
        return ydl_opts
    
    # Методы для обратной совместимости (будут удалены)
    def get_video_id(self, url: str) -> Optional[str]:
        """DEPRECATED: Используйте extract_video_id()"""
        return self.extract_video_id(url)
    
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """Instagram не поддерживает выбор качества"""
        return None
    
    def get_default_format(self) -> str:
        """Формат по умолчанию для Instagram"""
        return 'best[ext=mp4]/best'
