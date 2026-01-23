"""
Use case: Обработка команды /start
"""
import logging
from typing import Optional
from urllib.parse import unquote
from src.database.redis_db import Database
from src.utils.utils import normalize_url, is_supported_url, get_video_id_fast
from src.events.events import VideoViewClickedEvent

logger = logging.getLogger(__name__)


class HandleStartUseCase:
    """Use case для обработки команды /start"""
    
    def __init__(self, db: Database, downloader):
        """
        Args:
            db: Экземпляр Database для работы с кэшем
            downloader: Экземпляр VideoDownloader для получения video_id
        """
        self.db = db
        self.downloader = downloader
    
    async def execute(self, message_text: str, user_id: int) -> dict:
        """
        Обработать команду /start
        
        Args:
            message_text: Текст сообщения (может содержать параметры после /start)
            user_id: ID пользователя
            
        Returns:
            dict с ключами:
                - type: 'welcome' или 'deep_link'
                - url: URL для скачивания (если deep_link)
                - video_id: video_id (если deep_link)
                - cached_message_id: message_id из кэша (если есть)
        """
        # Проверяем, есть ли параметр после /start (deep link)
        args = message_text.split(maxsplit=1)[1:] if message_text else []
        args_str = args[0] if args else None
        
        if not args_str:
            # Обычная команда /start без параметров
            return {'type': 'welcome'}
        
        param = args_str.strip()
        
        # Параметр может быть:
        # 1. video_id в формате "platform_video_id" (короткий deep link с _)
        # 2. URL (старый формат, для обратной совместимости)
        
        url = None
        video_id = None
        
        # Проверяем, является ли параметр video_id (формат "platform_id" с подчеркиванием)
        if '_' in param and not param.startswith(('http://', 'https://')):
            # Это похоже на video_id из deep link (например, "instagram_DQHEHA1CAyr")
            # Заменяем _ на : для поиска в БД (в БД храним platform:video_id)
            video_id = param.replace('_', ':')
            logger.info(f"Параметр deep link: {param} -> video_id для БД: {video_id}")
            
            # Пытаемся получить original_url из кэша по video_id
            url = await self.db.get_original_url_by_video_id(video_id)
            
            # Проверяем, есть ли видео в кэше
            cached_message_id = await self.db.get_cached_message_id(video_id=video_id)
            
            if cached_message_id:
                # Публикуем событие VideoViewClickedEvent для deep link
                try:
                    event = VideoViewClickedEvent(
                        user_id=user_id,
                        video_id=video_id,
                        event_type='deep_link'
                    )
                    await self.db.add_analytics_event(event.to_json())
                except Exception as e:
                    logger.error(f"Ошибка при публикации события VideoViewClickedEvent: {e}")
                
                return {
                    'type': 'deep_link',
                    'url': url,
                    'video_id': video_id,
                    'cached_message_id': cached_message_id
                }
            
            # Видео нет в кэше, но URL найден
            if url:
                # Публикуем событие VideoViewClickedEvent для deep link
                try:
                    event = VideoViewClickedEvent(
                        user_id=user_id,
                        video_id=video_id,
                        event_type='deep_link'
                    )
                    await self.db.add_analytics_event(event.to_json())
                except Exception as e:
                    logger.error(f"Ошибка при публикации события VideoViewClickedEvent: {e}")
                
                return {
                    'type': 'deep_link',
                    'url': url,
                    'video_id': video_id,
                    'cached_message_id': None
                }
            else:
                # URL не найден - это ошибка
                return {
                    'type': 'error',
                    'error': 'video_not_found'
                }
        else:
            # Это URL (старый формат или закодированный URL)
            url = unquote(param)
            normalized_url = normalize_url(url)
            
            # Проверяем, поддерживается ли платформа
            if not is_supported_url(normalized_url):
                return {
                    'type': 'error',
                    'error': 'unsupported_platform'
                }
            
            # Получаем video_id для проверки кэша
            video_id, normalized_url = get_video_id_fast(normalized_url)
            url = normalized_url
            
            # Проверяем кэш
            cached_message_id = await self.db.get_cached_message_id(video_id=video_id, url=url)
            
            if cached_message_id:
                # Публикуем событие VideoViewClickedEvent для deep link
                try:
                    event = VideoViewClickedEvent(
                        user_id=user_id,
                        video_id=video_id,
                        event_type='deep_link'
                    )
                    await self.db.add_analytics_event(event.to_json())
                except Exception as e:
                    logger.error(f"Ошибка при публикации события VideoViewClickedEvent: {e}")
                
                return {
                    'type': 'deep_link',
                    'url': url,
                    'video_id': video_id,
                    'cached_message_id': cached_message_id
                }
            
            # Публикуем событие VideoViewClickedEvent для deep link
            try:
                event = VideoViewClickedEvent(
                    user_id=user_id,
                    video_id=video_id,
                    event_type='deep_link'
                )
                await self.db.add_analytics_event(event.to_json())
            except Exception as e:
                logger.error(f"Ошибка при публикации события VideoViewClickedEvent: {e}")
            
            return {
                'type': 'deep_link',
                'url': url,
                'video_id': video_id,
                'cached_message_id': None
            }
