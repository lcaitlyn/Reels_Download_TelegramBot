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
            downloader: Объект, предоставляющий методы работы с yt-dlp
                       (YtDlpService или совместимый по интерфейсу)
        """
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self.downloader = downloader
    
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
    def get_video_id(self, url: str) -> Optional[str]:
        """
        Извлечь канонический video_id
        
        Args:
            url: URL видео
            
        Returns:
            Канонический ID в формате "platform:video_id" или None
        """
        pass
    
    def get_ydl_opts(self) -> Dict[str, Any]:
        """
        Опции yt-dlp для get_info (только информация, без скачивания).
        Переопределяется в сервисах платформ (YouTube, Instagram, TikTok).
        """
        return {}

    def needs_pre_download_choice(self, url: str, query_text: Optional[str] = None) -> bool:
        """
        Нужен ли выбор пользователя перед скачиванием (качество, подтверждение и т.д.).
        Переопределяется в сервисах, где есть такой шаг (например YouTube — выбор качества).
        """
        return False

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
