import logging
from typing import Optional
from src.database.redis_db import Database
from src.services.link_processing_service import LinkProcessingService
from src.models.download_response import DownloadResponse
from src.models.link_info import LinkInfo
from src.utils.utils import normalize_url, is_supported_url, get_platform

logger = logging.getLogger(__name__)


class DownloadManager:
    
    def __init__(self, db: Database, link_processor: LinkProcessingService):
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
        Returns:
            DownloadResponse с статусом и данными
        """
        try:
            normalized_url = normalize_url(url)
            
            if not is_supported_url(normalized_url):
                return DownloadResponse(
                    status='ERROR',
                    error='Неподдерживаемая платформа. Поддерживаются: YouTube, Instagram, TikTok'
                )
            
            link_info = self.link_processor.process_link(normalized_url)
            if not link_info:
                return DownloadResponse(
                    status='ERROR',
                    error='Не удалось обработать ссылку. Проверьте, что ссылка корректна и платформа поддерживается.'
                )
            
            video_id = link_info.video_id
            platform = link_info.platform
            
            try:
                cached_message_id = await self.db.get_cached_message_id(
                    video_id=video_id,
                    url=link_info.normalized_url,
                    quality=quality
                )
                
                if cached_message_id and cached_message_id != 0:
                    cached_file_id = await self.db.get_cached_file_id(
                        video_id=video_id,
                        url=link_info.normalized_url,
                        quality=quality
                    )
                    cached_media_type = await self.db.get_cached_media_type(
                        video_id=video_id,
                        url=link_info.normalized_url,
                        quality=quality
                    ) or 'video'
                    return DownloadResponse(
                        status='READY',
                        file_id=cached_file_id,
                        message_id=cached_message_id,
                        media_type=cached_media_type
                    )
            except Exception as redis_err:
                logger.error(f"⚠️ Redis недоступен при проверке кэша: {redis_err}")
            
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
