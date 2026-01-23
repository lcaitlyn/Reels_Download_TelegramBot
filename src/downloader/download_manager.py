"""
DownloadManager - координатор системы скачивания видео
Управляет задачами, кэшем, блокировками, но НЕ скачивает видео
"""
import logging
from typing import Optional
from src.database.redis_db import Database
from src.services.link_processing_service import LinkProcessingService
from src.models.download_response import DownloadResponse
from src.models.link_info import LinkInfo
from src.utils.utils import normalize_url, is_supported_url, get_platform

logger = logging.getLogger(__name__)


class DownloadManager:
    """
    Координатор системы скачивания видео
    
    Ответственность:
    - Координация запросов на скачивание
    - Управление кэшем
    - Управление задачами и очередями
    - Управление блокировками
    - Связь пользователей и задач
    
    НЕ делает:
    - Не скачивает видео (это делает Worker)
    - Не знает о платформах (это знает LinkProcessingService)
    - Не работает с yt-dlp (это делает Worker через YtDlpService)
    - Не работает с Telegram (это делает bot.py и Worker)
    """
    
    def __init__(self, db: Database, link_processor: LinkProcessingService):
        """
        Args:
            db: Экземпляр Database для работы с Redis
            link_processor: Экземпляр LinkProcessingService для обработки ссылок
        """
        self.db = db
        self.link_processor = link_processor
    
    async def request_download(
        self,
        user_id: int,
        url: str,
        source: str = 'message',
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> DownloadResponse:
        """
        Главный метод координации - обработать запрос на скачивание
        
        Алгоритм:
        1. Нормализует URL
        2. Проверяет поддержку платформы
        3. Получает LinkInfo через LinkProcessingService (когда будет рефакторен)
        4. Проверяет кэш Redis
        5. Проверяет активную задачу (lock)
        6. Либо возвращает READY (если в кэше)
        7. Либо возвращает IN_PROGRESS (если уже скачивается)
        8. Либо создает задачу и возвращает QUEUED
        9. Либо возвращает ERROR
        
        Args:
            user_id: ID пользователя
            url: URL видео
            source: Источник запроса ('message', 'inline', 'deep_link')
            quality: Качество видео (для YouTube) или None
            format_id: ID формата из yt-dlp (для YouTube) или None
            
        Returns:
            DownloadResponse с статусом и данными
        """
        try:
            # 1. Нормализуем URL
            normalized_url = normalize_url(url)
            
            # 2. Проверяем поддержку платформы
            if not is_supported_url(normalized_url):
                return DownloadResponse(
                    status='ERROR',
                    error='Неподдерживаемая платформа. Поддерживаются: YouTube, Instagram, TikTok'
                )
            
            # 3. Получаем LinkInfo через LinkProcessingService (мозг системы)
            link_info = self.link_processor.process_link(normalized_url)
            if not link_info:
                return DownloadResponse(
                    status='ERROR',
                    error='Не удалось обработать ссылку. Проверьте, что ссылка корректна и платформа поддерживается.'
                )
            
            video_id = link_info.video_id
            platform = link_info.platform
            
            # 4. Проверяем кэш Redis
            try:
                cached_message_id = await self.db.get_cached_message_id(
                    video_id=video_id,
                    url=link_info.normalized_url,
                    quality=quality
                )
                
                if cached_message_id and cached_message_id != 0:
                    # Видео уже в кэше - возвращаем READY
                    cached_file_id = await self.db.get_cached_file_id(
                        video_id=video_id,
                        url=link_info.normalized_url,
                        quality=quality
                    )
                    
                    return DownloadResponse(
                        status='READY',
                        file_id=cached_file_id,
                        message_id=cached_message_id
                    )
            except Exception as redis_err:
                logger.error(f"⚠️ Redis недоступен при проверке кэша: {redis_err}")
                # Продолжаем без кэша
            
            # 5. Проверяем активную задачу (lock) и добавляем в очередь
            # ВАЖНО: Не получаем lock здесь! Lock получает Worker при обработке задачи.
            # Мы только проверяем, существует ли lock, и добавляем задачу в очередь.
            try:
                task_added = await self.db.add_download_task(
                    url=link_info.normalized_url,
                    video_id=video_id,
                    platform=platform,
                    quality=quality,
                    format_id=format_id
                )
                
                if task_added:
                    logger.info(f"Задача добавлена в очередь: video_id={video_id}, user_id={user_id}")
                    return DownloadResponse(
                        status='QUEUED',
                        job_id=video_id
                    )
                else:
                    # Задача уже обрабатывается (lock существует или уже в кэше)
                    return DownloadResponse(
                        status='IN_PROGRESS',
                        job_id=video_id
                    )
            except Exception as redis_err:
                logger.error(f"⚠️ Redis недоступен при добавлении задачи: {redis_err}")
                return DownloadResponse(
                    status='ERROR',
                    error='Сервис временно недоступен. Попробуйте позже.'
                )
                
        except Exception as e:
            logger.error(f"Ошибка в request_download: {e}", exc_info=True)
            return DownloadResponse(
                status='ERROR',
                error=f'Внутренняя ошибка: {str(e)}'
            )
