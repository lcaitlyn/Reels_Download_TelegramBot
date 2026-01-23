"""
Базовый класс для сервисов платформ
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class BaseService(ABC):
    """
    Базовый класс для всех сервисов платформ
    
    Каждый сервис знает только свою платформу и формирует DownloadPlan.
    НЕ скачивает видео, НЕ работает с Redis, НЕ работает с Telegram.
    """
    
    def __init__(self, downloader):
        """
        Args:
            downloader: Экземпляр VideoDownloader для получения информации о видео
        """
        self.downloader = downloader
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """
        Может ли сервис обработать этот URL
        
        Args:
            url: URL видео
            
        Returns:
            True если сервис может обработать URL, False иначе
        """
        pass
    
    @abstractmethod
    def extract_video_id(self, url: str) -> Optional[str]:
        """
        Извлечь канонический video_id
        
        Args:
            url: URL видео
            
        Returns:
            Канонический ID в формате "platform:video_id" или None
        """
        pass
    
    @abstractmethod
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получить метаданные видео
        
        Args:
            url: URL видео
            
        Returns:
            Словарь с метаданными (id, duration, filesize, title, ext, etc.) или None
        """
        pass
    
    @abstractmethod
    def build_download_plan(
        self,
        url: str,
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional['DownloadPlan']:
        """
        Построить план скачивания
        
        Args:
            url: URL видео
            quality: Качество видео (для YouTube) или None
            format_id: ID формата из yt-dlp (для YouTube) или None
            
        Returns:
            DownloadPlan или None при ошибке
        """
        pass
    
    # Методы для обратной совместимости (будут удалены после полного рефакторинга)
    def get_video_id(self, url: str) -> Optional[str]:
        """
        DEPRECATED: Используйте extract_video_id()
        """
        return self.extract_video_id(url)
    
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """
        DEPRECATED: Будет удален или перенесен в специфичные сервисы
        """
        return None
    
    def get_default_format(self) -> str:
        """
        DEPRECATED: Используйте build_download_plan()
        """
        return 'best[ext=mp4]/best'
    
    def download_video(self, url: str, format_id: Optional[str] = None) -> Optional[tuple]:
        """
        DEPRECATED: Будет удален. Скачивание теперь делает Worker через YtDlpService
        """
        raise NotImplementedError("download_video() удален. Используйте build_download_plan()")
