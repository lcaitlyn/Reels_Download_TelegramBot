"""
LinkProcessingService - мозг системы обработки ссылок
Определяет платформу, выбирает сервис, извлекает video_id
НЕ знает о Redis, очередях, пользователях, Telegram
"""
import logging
from typing import Optional
from src.services.service_factory import ServiceFactory
from src.models.link_info import LinkInfo
from src.utils.utils import normalize_url, is_supported_url, get_platform, is_youtube_video

logger = logging.getLogger(__name__)


class LinkProcessingService:
    """
    Обрабатывает ссылки и возвращает информацию о них
    """
    
    def __init__(self, service_factory: ServiceFactory):
        """
        Args:
            service_factory: Фабрика для создания сервисов платформ
        """
        self.service_factory = service_factory
    
    def process_link(self, url: str) -> Optional[LinkInfo]:
        """
        Обработать ссылку и получить информацию о ней
        
        Args:
            url: URL видео
            
        Returns:
            LinkInfo с информацией о ссылке или None при ошибке
        """
        try:
            normalized_url = normalize_url(url)
            
            if not is_supported_url(normalized_url):
                logger.warning(f"Неподдерживаемая платформа для URL: {normalized_url}")
                return None
            
            platform = get_platform(normalized_url)
            if not platform:
                logger.warning(f"Не удалось определить платформу для URL: {normalized_url}")
                return None
            
            service = self.service_factory.get_service(platform)
            if not service:
                logger.warning(f"Не найден сервис для платформы: {platform}")
                return None
            
            # TODO вот тут вообще не понятно. Какой смысл делать запрос в extract info, когда у некоторых сервисом можно ссылку получить?
            video_id = service.get_video_id(normalized_url)
            if not video_id:
                logger.warning(f"Не удалось извлечь video_id для URL: {normalized_url}")
                video_id = normalized_url
            
            return LinkInfo(
                platform=platform,
                video_id=video_id,
                normalized_url=normalized_url,
                service=service
            )
            
        except Exception as e:
            logger.error(f"Ошибка при обработке ссылки {url}: {e}", exc_info=True)
            return None
