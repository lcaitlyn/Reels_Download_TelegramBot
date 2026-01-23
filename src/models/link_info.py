"""
LinkInfo - результат обработки ссылки LinkProcessingService
Содержит информацию о платформе, video_id и сервисе для дальнейшей обработки
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.services.base import BaseService


@dataclass
class LinkInfo:
    """
    Информация о обработанной ссылке
    
    Создается LinkProcessingService после анализа URL.
    Используется DownloadManager для получения PlatformService и построения DownloadPlan.
    
    Attributes:
        platform: Платформа (instagram, youtube, tiktok)
        video_id: Канонический ID видео (platform:video_id)
        normalized_url: Нормализованный URL
        service: PlatformService для этой платформы (InstagramService, YouTubeService, etc.)
        requires_user_input: Требуется ли выбор качества от пользователя (True для YouTube)
    """
    platform: str
    video_id: str
    normalized_url: str
    service: 'BaseService'  # PlatformService для этой платформы
    requires_user_input: bool = False  # для YouTube - выбор качества
