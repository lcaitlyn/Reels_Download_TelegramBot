"""
DownloadPlan - контракт между логикой и воркером
Содержит всю информацию, необходимую для скачивания видео через yt-dlp
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class DownloadPlan:
    """
    План скачивания видео
    
    Создается PlatformService на основе URL и опций пользователя.
    Используется Worker для исполнения через yt-dlp.
    
    Attributes:
        platform: Платформа (instagram, youtube, tiktok)
        video_id: Канонический ID видео (platform:video_id)
        url: Оригинальный URL видео
        format_selector: Селектор формата для yt-dlp (например, 'best[ext=mp4]/best')
        quality: Качество видео (480p, 720p, 1080p, audio) или None
        audio_only: Только аудио (True) или видео с аудио (False)
        streamable: Можно ли стримить в память (True для файлов <50MB)
        ydl_opts: Опции для yt-dlp (format, outtmpl, extractor_args, etc.)
        metadata: Метаданные видео (duration, filesize, title, etc.) или None
    """
    platform: str  # instagram, youtube, tiktok
    video_id: str
    url: str
    format_selector: str  # для yt-dlp
    quality: Optional[str] = None  # 480p, 720p, 1080p, audio
    audio_only: bool = False
    streamable: bool = True  # можно ли стримить в память
    ydl_opts: Dict[str, Any] = None  # опции для yt-dlp
    metadata: Optional[Dict[str, Any]] = None  # duration, filesize, etc.
    
    def __post_init__(self):
        """Проверка обязательных полей"""
        if self.ydl_opts is None:
            self.ydl_opts = {}
