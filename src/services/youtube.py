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
    
    @staticmethod
    def _is_shorts_url(url: str) -> bool:
        """Shorts: вертикальное видео 9:16 (720x1280, 1080x1920)."""
        return 'shorts' in url.lower()
    
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
        
        ОПТИМИЗАЦИЯ: Не получаем метаданные здесь - это блокирует event loop.
        Метаданные будут получены во время скачивания через yt-dlp.
        
        Args:
            url: URL видео YouTube
            quality: Качество видео (480p, 720p, 1080p, audio) или None
            format_id: ID формата из yt-dlp (опционально)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        # Извлекаем video_id из URL (быстро, без запросов к API)
        video_id = self.extract_video_id(url)
        if not video_id:
            logger.error("[YouTube] Не удалось извлечь video_id из URL")
            return None
        
        # Определяем формат (для Shorts — вертикальное 9:16: 720x1280 → 480x854 → 360, без выбора качества)
        is_shorts = self._is_shorts_url(url) or quality == 'shorts'
        format_selector = self._prepare_format_selector(format_id, quality, is_shorts=is_shorts)
        
        # Формируем опции yt-dlp для YouTube
        ydl_opts = self._get_ydl_opts_for_youtube(format_selector)
        
        # Shorts часто идут как HLS (m3u8) — в stdout дают 0 байт, стрим не годится. Качаем только в файл.
        streamable = not is_shorts
        
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
            metadata=None  # Метаданные будут получены во время скачивания
        )
    
    def _prepare_format_selector(
        self,
        format_id: Optional[str],
        quality: Optional[str],
        *,
        is_shorts: bool = False
    ) -> str:
        """
        Подготовить селектор формата для YouTube.
        Shorts (9:16): один уже сведённый формат (без мержа), чтобы качать без ffmpeg.
        Приоритет: 720x1280 → 480x854 → 360 (один поток mp4/m3u8 с видео+звуком).
        """
        # Shorts: один формат (без bestvideo+bestaudio), без ffmpeg. Лучший готовый поток до 1280.
        if is_shorts:
            if quality == '1080p':
                return 'best[height<=1920][ext=mp4]/best[height<=1920]'
            return 'best[height<=1280][ext=mp4]/best[height<=1280]/best[height<=854]/best'
        
        # Обычное видео (16:9): при выборе 480p/720p/1080p ВСЕГДА селектор по качеству, НЕ format_id.
        # (92, 93, 94, 300 и т.д. — HLS, при -f 300 дают "The downloaded file is empty".)
        if quality in ('480p', '720p', '1080p'):
            if quality == '480p':
                return 'bestvideo[height<=480][ext=mp4]+bestaudio/best'
            if quality == '720p':
                return 'bestvideo[height<=720][ext=mp4]+bestaudio/best'
            if quality == '1080p':
                return 'bestvideo[height<=1080][ext=mp4]+bestaudio/best'
        
        if format_id:
            audio_only_formats = ('140', '250', '251', '139', '141', '171', '249')
            if format_id.startswith(audio_only_formats) or format_id in audio_only_formats:
                logger.info(f"[YouTube] Использую audio-only формат {format_id} как есть")
                return format_id
            video_only_formats = ('135', '136', '137', '160', '133', '134', '298', '299', '264', '266', '138', '779', '780')
            if format_id.startswith(video_only_formats) or format_id in video_only_formats:
                return f"{format_id}+bestaudio/best"
            return format_id
        
        if quality == 'audio':
            return 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/140/250/251'
        
        return 'bestvideo[height<=360][ext=mp4]+bestaudio/best'
    
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
        """Формат по умолчанию для YouTube (для Shorts) — видео со звуком"""
        return 'bestvideo[height<=360][ext=mp4]+bestaudio/best'
