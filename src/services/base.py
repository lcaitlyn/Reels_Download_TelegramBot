"""
Базовый класс для сервисов платформ
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class BaseService(ABC):
    """Базовый класс для всех сервисов платформ"""
    
    def __init__(self, downloader):
        """
        Args:
            downloader: Экземпляр VideoDownloader для скачивания видео
        """
        self.downloader = downloader
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    @abstractmethod
    def get_video_id(self, url: str) -> Optional[str]:
        """
        Получить канонический ID видео
        
        Args:
            url: URL видео
            
        Returns:
            Канонический ID в формате "platform:video_id" или None
        """
        pass
    
    @abstractmethod
    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получить доступные форматы для видео
        
        Args:
            url: URL видео
            
        Returns:
            Словарь с доступными форматами или None
        """
        pass
    
    @abstractmethod
    def get_default_format(self) -> str:
        """
        Получить формат по умолчанию для скачивания
        
        Returns:
            Строка формата для yt-dlp
        """
        pass
    
    @abstractmethod
    def download_video(self, url: str, format_id: Optional[str] = None) -> Optional[tuple]:
        """
        Скачать видео
        
        Args:
            url: URL видео
            format_id: ID формата (опционально)
            
        Returns:
            Tuple (io.BytesIO или путь к файлу, размер в байтах, имя файла) или None
        """
        pass
