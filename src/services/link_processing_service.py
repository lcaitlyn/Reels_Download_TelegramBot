"""
LinkProcessingService - оркестратор для обработки ссылок на видео
Управляет статусами пользователей, кэшем, очередями и аналитикой
"""
import logging
from typing import Optional, Dict, Any
from src.database.redis_db import Database
from src.downloader.downloader import VideoDownloader
from src.utils.utils import normalize_url, is_supported_url, get_platform, get_video_id_fast, is_youtube_video
from src.events.events import DownloadCompletedEvent, VideoViewClickedEvent

logger = logging.getLogger(__name__)


class LinkProcessingService:
    """
    Сервис для обработки ссылок на видео
    Оркестрирует работу с кэшем, очередями, статусами пользователей и аналитикой
    """
    
    def __init__(self, db: Database, downloader: VideoDownloader):
        """
        Args:
            db: Экземпляр Database для работы с Redis
            downloader: Экземпляр VideoDownloader для работы с yt-dlp
        """
        self.db = db
        self.downloader = downloader
    
    def _get_user_status_key(self, user_id: int) -> str:
        """Получить ключ Redis для статуса пользователя"""
        return f"user:{user_id}:processing_status"
    
    async def get_user_processing_status(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Получить текущий статус обработки для пользователя
        
        Returns:
            dict с ключами:
                - video_id: ID видео, которое обрабатывается
                - url: URL видео
                - status: 'processing' или 'waiting'
                - timestamp: время начала обработки
            или None если пользователь не обрабатывает видео
        """
        import json
        import time
        
        try:
            key = self._get_user_status_key(user_id)
            status_str = await self.db.redis_client.get(key)
            
            if status_str:
                status = json.loads(status_str)
                # Проверяем, не истек ли статус (максимум 30 минут)
                timestamp = status.get('timestamp', 0)
                if time.time() - timestamp > 1800:  # 30 минут
                    await self.db.redis_client.delete(key)
                    return None
                return status
        except Exception as e:
            logger.error(f"Ошибка при получении статуса пользователя {user_id}: {e}")
        
        return None
    
    async def set_user_processing_status(
        self,
        user_id: int,
        video_id: str,
        url: str,
        status: str = 'processing'
    ):
        """
        Установить статус обработки для пользователя
        
        Args:
            user_id: ID пользователя
            video_id: ID видео
            url: URL видео
            status: 'processing' или 'waiting'
        """
        import json
        import time
        
        try:
            key = self._get_user_status_key(user_id)
            status_data = {
                'video_id': video_id,
                'url': url,
                'status': status,
                'timestamp': time.time()
            }
            # TTL 30 минут (максимальное время скачивания)
            await self.db.redis_client.set(key, json.dumps(status_data), ex=1800)
        except Exception as e:
            logger.error(f"Ошибка при установке статуса пользователя {user_id}: {e}")
    
    async def clear_user_processing_status(self, user_id: int):
        """Очистить статус обработки для пользователя"""
        try:
            key = self._get_user_status_key(user_id)
            await self.db.redis_client.delete(key)
        except Exception as e:
            logger.error(f"Ошибка при очистке статуса пользователя {user_id}: {e}")
    
    async def process_link(
        self,
        url: str,
        user_id: int,
        source: str = 'message',
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Основной метод обработки ссылки на видео
        
        Args:
            url: URL видео
            user_id: ID пользователя
            source: Источник запроса ('message', 'inline', 'deep_link')
            quality: Качество видео (для YouTube) или None
            format_id: ID формата из yt-dlp (для YouTube) или None
            
        Returns:
            dict с ключами:
                - status: 'cached', 'queued', 'processing', 'error'
                - message_id: ID сообщения в канале (если есть)
                - file_id: file_id для отправки (если есть)
                - video_id: канонический ID видео
                - error: сообщение об ошибке (если status='error')
        """
        # Нормализуем URL
        normalized_url = normalize_url(url)
        
        # Проверяем поддержку платформы
        if not is_supported_url(normalized_url):
            return {
                'status': 'error',
                'error': 'unsupported_platform',
                'error_message': 'Неподдерживаемая платформа'
            }
        
        # Получаем video_id
        video_id, normalized_url = get_video_id_fast(normalized_url)
        if not video_id:
            video_id = self.downloader.get_video_id(normalized_url)
        if not video_id:
            video_id = normalized_url  # Fallback
        
        # Проверяем статус пользователя - если уже обрабатывает это видео
        user_status = await self.get_user_processing_status(user_id)
        if user_status and user_status.get('video_id') == video_id:
            return {
                'status': 'processing',
                'video_id': video_id,
                'message': 'Видео уже обрабатывается, пожалуйста подождите...'
            }
        
        # Проверяем кэш
        try:
            cached_message_id = await self.db.get_cached_message_id(
                video_id=video_id,
                url=normalized_url,
                quality=quality
            )
            
            if cached_message_id and cached_message_id != 0:
                # Видео в кэше
                cached_file_id = await self.db.get_cached_file_id(
                    video_id=video_id,
                    url=normalized_url,
                    quality=quality
                )
                
                return {
                    'status': 'cached',
                    'message_id': cached_message_id,
                    'file_id': cached_file_id,
                    'video_id': video_id,
                    'url': normalized_url
                }
        except Exception as redis_err:
            logger.error(f"⚠️ Redis недоступен при проверке кэша: {redis_err}")
            # Продолжаем без кэша
        
        # Видео нет в кэше - добавляем в очередь
        platform = get_platform(normalized_url)
        
        try:
            # Устанавливаем статус "обработка"
            await self.set_user_processing_status(user_id, video_id, normalized_url, 'processing')
            
            # Добавляем задачу в очередь
            task_added = await self.db.add_download_task(
                url=normalized_url,
                video_id=video_id,
                platform=platform,
                quality=quality,
                format_id=format_id
            )
            
            if task_added:
                logger.info(f"Задача добавлена в очередь: video_id={video_id}, user_id={user_id}")
            else:
                logger.info(f"Задача уже обрабатывается: video_id={video_id}, user_id={user_id}")
            
            return {
                'status': 'queued',
                'video_id': video_id,
                'url': normalized_url,
                'platform': platform
            }
        except Exception as redis_err:
            logger.error(f"⚠️ Redis недоступен при добавлении задачи: {redis_err}")
            await self.clear_user_processing_status(user_id)
            return {
                'status': 'error',
                'error': 'service_unavailable',
                'error_message': 'Сервис временно недоступен'
            }
    
    async def wait_for_download_completion(
        self,
        video_id: str,
        user_id: int,
        timeout: float = 1800.0,
        quality: Optional[str] = None
    ) -> Optional[int]:
        """
        Ожидать завершения скачивания видео
        
        Args:
            video_id: Канонический ID видео
            user_id: ID пользователя (для обновления статуса)
            timeout: Максимальное время ожидания в секундах
            quality: Качество видео (для YouTube) или None
            
        Returns:
            message_id когда видео скачано, или None при timeout/ошибке
        """
        try:
            message_id = await self.db.wait_for_download(video_id, timeout=timeout, quality=quality)
            
            # Очищаем статус пользователя после завершения
            await self.clear_user_processing_status(user_id)
            
            return message_id
        except Exception as e:
            logger.error(f"Ошибка при ожидании скачивания: {e}")
            await self.clear_user_processing_status(user_id)
            return None
    
    async def get_cached_video(
        self,
        url: str,
        quality: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Получить видео из кэша
        
        Args:
            url: URL видео
            quality: Качество видео (для YouTube) или None
            
        Returns:
            dict с ключами:
                - message_id: ID сообщения в канале
                - file_id: file_id для отправки
                - video_id: канонический ID видео
            или None если видео не найдено в кэше
        """
        normalized_url = normalize_url(url)
        
        # Получаем video_id
        video_id, normalized_url = get_video_id_fast(normalized_url)
        if not video_id:
            video_id = self.downloader.get_video_id(normalized_url)
        if not video_id:
            video_id = normalized_url
        
        try:
            cached_message_id = await self.db.get_cached_message_id(
                video_id=video_id,
                url=normalized_url,
                quality=quality
            )
            
            if not cached_message_id or cached_message_id == 0:
                return None
            
            cached_file_id = await self.db.get_cached_file_id(
                video_id=video_id,
                url=normalized_url,
                quality=quality
            )
            
            return {
                'message_id': cached_message_id,
                'file_id': cached_file_id,
                'video_id': video_id,
                'url': normalized_url
            }
        except Exception as e:
            logger.error(f"Ошибка при получении из кэша: {e}")
            return None
    
    async def publish_download_analytics(
        self,
        user_id: int,
        video_id: str,
        platform: str,
        source: str
    ):
        """
        Публикует событие DownloadCompletedEvent в очередь аналитики
        
        Args:
            user_id: ID пользователя
            video_id: Канонический ID видео
            platform: Платформа (youtube, instagram, tiktok)
            source: Источник запроса
        """
        try:
            event = DownloadCompletedEvent(
                user_id=user_id,
                video_id=video_id,
                platform=platform,
                source=source
            )
            await self.db.add_analytics_event(event.to_json())
            logger.debug(f"Событие DownloadCompletedEvent опубликовано: user_id={user_id}, video_id={video_id}")
        except Exception as e:
            logger.error(f"Ошибка при публикации события DownloadCompletedEvent: {e}")
    
    async def publish_view_clicked_analytics(
        self,
        user_id: int,
        video_id: Optional[str],
        event_type: str = 'button_click'
    ):
        """
        Публикует событие VideoViewClickedEvent в очередь аналитики
        
        Args:
            user_id: ID пользователя
            video_id: Канонический ID видео (может быть None)
            event_type: Тип события ('button_click', 'deep_link')
        """
        try:
            event = VideoViewClickedEvent(
                user_id=user_id,
                video_id=video_id,
                event_type=event_type
            )
            await self.db.add_analytics_event(event.to_json())
            logger.debug(f"Событие VideoViewClickedEvent опубликовано: user_id={user_id}, video_id={video_id}")
        except Exception as e:
            logger.error(f"Ошибка при публикации события VideoViewClickedEvent: {e}")
    
    async def get_available_qualities(self, url: str, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить доступные качества для YouTube видео
        
        Args:
            url: URL YouTube видео
            video_id: Канонический ID видео
            
        Returns:
            dict с ключами:
                - formats: словарь форматов {quality_label: format_info}
                - cached_qualities: список качеств, которые есть в кэше
            или None при ошибке
        """
        if not is_youtube_video(url):
            return None
        
        formats = self.downloader.get_available_formats(url)
        
        if not formats:
            return None
        
        # Проверяем, какие качества есть в кэше
        cached_qualities = []
        for quality_label in ['480p', '720p', '1080p', 'audio']:
            if quality_label in formats:
                try:
                    cached = await self.db.check_quality_in_cache(video_id, quality_label)
                    if cached:
                        cached_qualities.append(quality_label)
                except Exception as e:
                    logger.error(f"Ошибка при проверке качества в кэше: {e}")
        
        return {
            'formats': formats,
            'cached_qualities': cached_qualities
        }
    
    async def get_quality_info(self, url: str, quality_label: str) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о формате для выбранного качества
        
        Args:
            url: URL YouTube видео
            quality_label: Метка качества ('480p', '720p', '1080p', 'audio')
            
        Returns:
            dict с информацией о формате или None
        """
        formats = self.downloader.get_available_formats(url)
        
        if not formats or quality_label not in formats:
            return None
        
        return formats[quality_label]
    
    async def get_cached_quality(
        self,
        video_id: str,
        quality_label: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить видео с указанным качеством из кэша
        
        Args:
            video_id: Канонический ID видео
            quality_label: Метка качества
            
        Returns:
            dict с ключами:
                - message_id: ID сообщения в канале
                - file_id: file_id для отправки
            или None если не найдено
        """
        try:
            cached_message_id = await self.db.get_cached_message_id(
                video_id=video_id,
                quality=quality_label
            )
            
            if not cached_message_id or cached_message_id == 0:
                return None
            
            cached_file_id = await self.db.get_cached_file_id(
                video_id=video_id,
                quality=quality_label
            )
            
            return {
                'message_id': cached_message_id,
                'file_id': cached_file_id
            }
        except Exception as e:
            logger.error(f"Ошибка при получении кэшированного качества: {e}")
            return None
