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
    Мозг системы - обрабатывает ссылки и возвращает информацию о них
    
    Ответственность:
    - Нормализация URL
    - Определение платформы
    - Выбор PlatformService
    - Извлечение video_id
    
    НЕ делает:
    - Не работает с Redis (кэш, очереди, статусы)
    - Не работает с пользователями
    - Не работает с Telegram
    - Не скачивает видео
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
        
        Алгоритм:
        1. Нормализует URL
        2. Проверяет поддержку платформы
        3. Определяет платформу
        4. Получает PlatformService через ServiceFactory
        5. Извлекает video_id через сервис
        6. Определяет, требуется ли выбор качества (для YouTube)
        7. Возвращает LinkInfo
        
        Args:
            url: URL видео
            
        Returns:
            LinkInfo с информацией о ссылке или None при ошибке
        """
        try:
            # Нормализуем URL (идемпотентная операция - безопасно вызывать несколько раз)
            normalized_url = normalize_url(url)
            
            # 2. Проверяем поддержку платформы
            if not is_supported_url(normalized_url):
                logger.warning(f"Неподдерживаемая платформа для URL: {normalized_url}")
                return None
            
            # 3. Определяем платформу
            platform = get_platform(normalized_url)
            if not platform:
                logger.warning(f"Не удалось определить платформу для URL: {normalized_url}")
                return None
            
            # 4. Получаем PlatformService через ServiceFactory
            service = self.service_factory.get_service(platform)
            if not service:
                logger.warning(f"Не найден сервис для платформы: {platform}")
                return None
            
            # 5. Извлекаем video_id через сервис
            video_id = service.get_video_id(normalized_url)
            if not video_id:
                logger.warning(f"Не удалось извлечь video_id для URL: {normalized_url}")
                # Fallback: используем normalized_url как video_id
                video_id = normalized_url
            
            # 6. Определяем, требуется ли выбор качества (для YouTube)
            requires_user_input = False
            if platform == 'youtube' and is_youtube_video(normalized_url):
                # Для YouTube видео (не Shorts) может потребоваться выбор качества
                # Это будет определяться позже при построении DownloadPlan
                # Пока оставляем False, так как качество может быть указано в запросе
                requires_user_input = False
            
            # 7. Возвращаем LinkInfo
            return LinkInfo(
                platform=platform,
                video_id=video_id,
                normalized_url=normalized_url,
                service=service,
                requires_user_input=requires_user_input
            )
            
        except Exception as e:
            logger.error(f"Ошибка при обработке ссылки {url}: {e}", exc_info=True)
            return None
