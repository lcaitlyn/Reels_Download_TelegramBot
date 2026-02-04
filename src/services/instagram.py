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
        
        ОПТИМИЗАЦИЯ: Не получаем метаданные здесь - это блокирует event loop.
        Метаданные будут получены во время скачивания через yt-dlp.
        
        Args:
            url: URL видео Instagram
            quality: Качество (для Instagram не используется)
            format_id: ID формата (для Instagram не используется)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        # Извлекаем video_id из URL (быстро, без запросов к API)
        video_id = self.extract_video_id(url)
        if not video_id:
            logger.error("[Instagram] Не удалось извлечь video_id из URL")
            return None
        
        # Формируем опции yt-dlp для Instagram
        format_selector = 'best[ext=mp4]/best'
        ydl_opts = self._get_ydl_opts_for_instagram(format_selector)
        
        # Не знаем размер файла заранее - будем определять во время скачивания
        # По умолчанию считаем, что можно стримить (для маленьких файлов)
        # Если файл окажется большим, worker переключится на файловый режим
        streamable = True  # Будет переопределено во время скачивания
        
        return DownloadPlan(
            platform='instagram',
            video_id=f"instagram:{video_id}",
            url=url,
            format_selector=format_selector,
            streamable=streamable,
            ydl_opts=ydl_opts,
            metadata=None  # Метаданные будут получены во время скачивания
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
