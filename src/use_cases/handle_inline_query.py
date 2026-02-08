"""
Use case: Обработка inline-запросов
"""
import logging
from typing import List, Optional
from src.database.redis_db import Database
from src.utils.utils import normalize_url, is_supported_url, get_video_id_fast, is_youtube_video, get_platform

logger = logging.getLogger(__name__)


# TODO пизда как-тут вообще че и как используется
class HandleInlineQueryUseCase:
    """Use case для обработки inline-запросов"""
    
    def __init__(self, db: Database, downloader, bot_username: str = None):
        """
        Args:
            db: Экземпляр Database для работы с кэшем
            downloader: Экземпляр VideoDownloader для получения video_id
            bot_username: Username бота для deep links
        """
        self.db = db
        self.downloader = downloader
        self._bot_username = bot_username
    
    async def get_bot_username(self, bot) -> str:
        """Получить username бота (кэшируем)"""
        if not self._bot_username:
            if not hasattr(bot, '_cached_username'):
                bot_info = await bot.get_me()
                bot._cached_username = bot_info.username
            self._bot_username = bot._cached_username
        return self._bot_username
    
    async def execute(self, query: str, bot) -> dict:
        """
        Обработать inline-запрос
        
        Args:
            query: Текст запроса
            bot: Экземпляр Bot для получения username
            
        Returns:
            dict с ключами:
                - type: 'help', 'unsupported', 'cached', 'link', 'text'
                - results: список результатов (для Telegram API)
        """
        query = query.strip()
        
        if not query:
            return {
                'type': 'help',
                'results': []
            }
        
        if query.startswith(('http://', 'https://')):
            normalized_url = normalize_url(query)
            
            if not is_supported_url(normalized_url):
                return {
                    'type': 'unsupported',
                    'results': []
                }
            
            platform = get_platform(normalized_url)
            
            video_id, normalized_url = get_video_id_fast(query)
            
            # Для YouTube видео (не Shorts) - особая логика
            if is_youtube_video(normalized_url) and video_id:
                return await self._handle_youtube_video(normalized_url, video_id, platform, bot)
            else:
                # Для TikTok, Instagram, Shorts - обычная логика
                return await self._handle_other_platforms(normalized_url, video_id, platform, bot)
        else:
            # Если запрос не URL - показываем кнопку для отправки текста
            return {
                'type': 'text',
                'results': []
            }
    
    async def _handle_youtube_video(self, url: str, video_id: str, platform: str, bot) -> dict:
        """Обработка YouTube видео"""
        # Проверяем кэш на наличие лучшего качества
        best_quality_result = await self.db.get_best_cached_quality(video_id)
        
        if best_quality_result:
            # Видео есть в кэше - отправляем лучшее качество
            quality_label, cached_file_id = best_quality_result
            media_type = await self.db.get_cached_media_type(video_id=video_id, quality=quality_label) or 'video'
            return {
                'type': 'cached',
                'results': [{
                    'type': 'cached_media',
                    'media_type': media_type,
                    'file_id': cached_file_id,
                    'title': f"✅ Видео из кэша ({platform}, {quality_label})",
                    'description': url
                }]
            }
        
        # Видео нет в кэше - получаем форматы и выбираем качество по умолчанию
        formats = self.downloader.get_available_formats(url)
        if formats:
            default_quality = await self.db.get_default_quality_for_download(formats)
            if default_quality:
                quality_label, format_id = default_quality
                
                # Сохраняем URL в кэш для маппинга
                await self.db.save_url_mapping(video_id, url, platform)
                
                # Добавляем задачу в очередь для фонового скачивания
                await self.db.add_download_task(
                    url=url,
                    video_id=video_id,
                    platform=platform,
                    quality=quality_label,
                    format_id=format_id
                )
                logger.info(f"Задача добавлена в очередь для YouTube видео с качеством {quality_label}: {url}")
                
                # Создаем deep link
                bot_username = await self.get_bot_username(bot)
                video_id_for_deeplink = video_id.replace(':', '_')
                deep_link = f"https://t.me/{bot_username}?start={video_id_for_deeplink}"
                
                return {
                    'type': 'link',
                    'results': [{
                        'type': 'article',
                        'title': f"🔗 YouTube видео ({platform})",
                        'description': f"Скачать {quality_label} (ближайшее к 480p)",
                        'message_text': url,
                        'deep_link': deep_link
                    }]
                }
        
        # Fallback
        return await self._handle_other_platforms(url, video_id, platform, bot)
    
    async def _handle_other_platforms(self, url: str, video_id: Optional[str], platform: str, bot) -> dict:
        """Обработка других платформ (TikTok, Instagram, Shorts)"""
        cached_file_id = await self.db.get_cached_file_id(video_id=video_id, url=url)
        if cached_file_id:
            media_type = await self.db.get_cached_media_type(video_id=video_id, url=url) or 'video'
            return {
                'type': 'cached',
                'results': [{
                    'type': 'cached_media',
                    'media_type': media_type,
                    'file_id': cached_file_id,
                    'title': f"✅ Из кэша ({platform})",
                    'description': url
                }]
            }
        
        # Видео нет в кэше - отправляем ссылку на видео + кнопку с deep link
        bot_username = await self.get_bot_username(bot)
        
        if video_id:
            # Используем video_id в deep link
            video_id_for_deeplink = video_id.replace(':', '_')
            deep_link = f"https://t.me/{bot_username}?start={video_id_for_deeplink}"
            
            # Сохраняем URL в кэш для маппинга
            await self.db.save_url_mapping(video_id, url, platform)
            logger.info(f"Сохранен маппинг video_id -> URL: {video_id} -> {url}")
            
            # Добавляем задачу в очередь для фонового скачивания
            await self.db.add_download_task(
                url=url,
                video_id=video_id,
                platform=platform
            )
            logger.info(f"Задача добавлена в очередь для видео: {url}")
        else:
            # Fallback: используем URL (может не работать из-за лимита длины)
            from urllib.parse import quote
            encoded_url = quote(url, safe='')
            deep_link = f"https://t.me/{bot_username}?start={encoded_url}"
            logger.warning(f"Используется fallback с URL в deep link (video_id не получен)")
        
        return {
            'type': 'link',
            'results': [{
                'type': 'article',
                'title': f"🔗 Ссылка на видео ({platform})",
                'description': "Нажмите кнопку чтобы скачать видео",
                'message_text': url,
                'deep_link': deep_link
            }]
        }
    
