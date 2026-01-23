"""
Фабрика для создания сервисов платформ
"""
from typing import Optional
from src.downloader.downloader import VideoDownloader
from src.services.instagram import InstagramService
from src.services.tiktok import TikTokService
from src.services.youtube import YouTubeService
from src.services.base import BaseService
from src.utils.utils import get_platform


class ServiceFactory:
    """Фабрика для создания сервисов платформ"""
    
    def __init__(self, downloader: VideoDownloader):
        """
        Args:
            downloader: Экземпляр VideoDownloader
        """
        self.downloader = downloader
        self._services = {}
    
    def get_service(self, platform: str) -> Optional[BaseService]:
        """
        Получить сервис для платформы
        
        Args:
            platform: Название платформы ('instagram', 'tiktok', 'youtube')
            
        Returns:
            Сервис для платформы или None если платформа не поддерживается
        """
        if platform not in self._services:
            if platform == 'instagram':
                self._services[platform] = InstagramService(self.downloader)
            elif platform == 'tiktok':
                self._services[platform] = TikTokService(self.downloader)
            elif platform == 'youtube':
                self._services[platform] = YouTubeService(self.downloader)
            else:
                return None
        
        return self._services.get(platform)
    
    def get_service_by_url(self, url: str) -> Optional[BaseService]:
        """
        Получить сервис для URL
        
        Args:
            url: URL видео
            
        Returns:
            Сервис для платформы или None если платформа не поддерживается
        """
        platform = get_platform(url)
        return self.get_service(platform)
