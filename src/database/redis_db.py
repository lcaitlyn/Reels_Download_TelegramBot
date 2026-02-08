import json
import hashlib
import asyncio
import datetime as _dt
import logging
import time
from typing import Optional
from redis import asyncio as redis
import os
from dotenv import load_dotenv

from src.config import get_int_env, get_float_env

load_dotenv()

logger = logging.getLogger(__name__)

TTL_SECONDS = get_int_env("REDIS_CACHE_TTL_SECONDS", 7 * 24 * 60 * 60)
LOCK_TTL_SECONDS = get_int_env("REDIS_LOCK_TTL_SECONDS", 2 * 60)
WAIT_POLL_INTERVAL = get_float_env("REDIS_WAIT_POLL_INTERVAL", 1.0)


# TODO вынести все настройки redis в config.py
# TODO вынести все Магические числа в config.py
class Database:
    def __init__(self, redis_url: str = None):
        # TODO вынести настройки redis в config.py
        if not redis_url:
            redis_url = os.getenv("REDIS_URL")
            if not redis_url:
                redis_host = os.getenv("REDIS_HOST", "localhost")
                redis_port = os.getenv("REDIS_PORT", "6379")
                redis_db = os.getenv("REDIS_DB", "0")
                redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
        
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
    
    def get_url_hash(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()
    
    def _get_video_key(self, video_id: str, quality: str = None) -> str:
        video_hash = self.get_url_hash(video_id)
        if quality:
            return f"video:{video_hash}:{quality}"
        return f"video:{video_hash}"
    
    def _get_url_mapping_key(self, url: str) -> str:
        url_hash = self.get_url_hash(url)
        return f"url_mapping:{url_hash}"
    
    def _get_lock_key(self, video_id: str, quality: str = None) -> str:
        video_hash = self.get_url_hash(video_id)
        if quality:
            return f"lock:video:{video_hash}:{quality}"
        return f"lock:video:{video_hash}"
    
    def _get_task_queue_key(self) -> str:
        return "tasks:download_queue"
    
    def _get_analytics_queue_key(self) -> str:
        return "events:analytics_queue"

    def _get_user_downloads_total_key(self, user_id: int) -> str:
        return f"user:{user_id}:downloads_total"

    def _get_user_downloads_day_key(self, user_id: int, day: str) -> str:
        return f"user:{user_id}:downloads:day:{day}"

    def _get_user_downloads_month_key(self, user_id: int, month: str) -> str:
        return f"user:{user_id}:downloads:month:{month}"

    def _get_video_downloads_total_key(self, video_id: str) -> str:
        video_hash = self.get_url_hash(video_id)
        return f"video:{video_hash}:downloads_total"
    
    async def get_cached_message_id(self, video_id: str = None, url: str = None, quality: str = None) -> Optional[int]:
        key = None
        
        if video_id:
            key = self._get_video_key(video_id, quality=quality)
        elif url:
            url_mapping_key = self._get_url_mapping_key(url)
            video_id_from_mapping = await self.redis_client.get(url_mapping_key)
            if video_id_from_mapping:
                key = self._get_video_key(video_id_from_mapping)
            else:
                key = self._get_video_key(url)
        
        if not key:
            return None
        
        try:
            data_str = await self.redis_client.get(key)
            if data_str:
                data = json.loads(data_str)
                message_id = data.get('message_id')
                await self.redis_client.expire(key, TTL_SECONDS)
                return int(message_id) if message_id else None
        except Exception as e:
            logger.error(f"Ошибка при получении message_id из Redis: {e}")
        
        return None
    
    async def get_cached_file_id(self, video_id: str = None, url: str = None, quality: str = None) -> Optional[str]:
        key = None
        
        if video_id:
            key = self._get_video_key(video_id, quality=quality)
        elif url:
            url_mapping_key = self._get_url_mapping_key(url)
            video_id_from_mapping = await self.redis_client.get(url_mapping_key)
            if video_id_from_mapping:
                key = self._get_video_key(video_id_from_mapping, quality=quality)
            else:
                key = self._get_video_key(url, quality=quality)
        
        if not key:
            return None
        
        try:
            data_str = await self.redis_client.get(key)
            if data_str:
                data = json.loads(data_str)
                file_id = data.get('file_id')
                await self.redis_client.expire(key, TTL_SECONDS)
                return file_id
        except Exception as e:
            logger.error(f"Ошибка при получении file_id из Redis: {e}")
        
        return None
    
    async def get_cached_media_type(self, video_id: str = None, url: str = None, quality: str = None) -> Optional[str]:
        key = None
        if video_id:
            key = self._get_video_key(video_id, quality=quality)
        elif url:
            url_mapping_key = self._get_url_mapping_key(url)
            video_id_from_mapping = await self.redis_client.get(url_mapping_key)
            if video_id_from_mapping:
                key = self._get_video_key(video_id_from_mapping, quality=quality)
            else:
                key = self._get_video_key(url, quality=quality)
        if not key:
            return None
        try:
            data_str = await self.redis_client.get(key)
            if data_str:
                data = json.loads(data_str)
                return data.get('media_type') or 'video'
        except Exception as e:
            logger.error(f"Ошибка при получении media_type из Redis: {e}")
        return None
    
    async def save_to_cache(self, video_id: str, message_id: int, platform: str = None, file_id: str = None, original_url: str = None, quality: str = None, media_type: str = 'video'):
        key = self._get_video_key(video_id, quality=quality)
        
        try:
            existing_data_str = await self.redis_client.get(key)
            existing_data = json.loads(existing_data_str) if existing_data_str else {}
            
            if file_id is None and existing_data.get('file_id'):
                file_id = existing_data.get('file_id')
            
            original_url = original_url or video_id
            
            data = {
                'message_id': message_id,
                'file_id': file_id,
                'platform': platform,
                'original_url': original_url,
                'video_id': video_id,
                'quality': quality,
                'media_type': media_type or 'video'
            }
            await self.redis_client.set(key, json.dumps(data), ex=TTL_SECONDS)
            if original_url.startswith(('http://', 'https://')):
                url_mapping_key = self._get_url_mapping_key(original_url)
                await self.redis_client.set(url_mapping_key, video_id, ex=TTL_SECONDS)
            
            logger.info(f"Данные сохранены в Redis: key={key}, video_id={video_id}")
        except Exception as e:
            logger.error(f"Ошибка при сохранении в Redis: {e}")
    
    async def save_url_mapping(self, video_id: str, url: str, platform: str = None):
        try:
            url_mapping_key = self._get_url_mapping_key(url)
            await self.redis_client.set(url_mapping_key, video_id, ex=TTL_SECONDS)
            
            video_key = self._get_video_key(video_id)
            data = {
                'message_id': 0,
                'file_id': None,
                'platform': platform,
                'original_url': url,
                'video_id': video_id
            }
            await self.redis_client.set(video_key, json.dumps(data), ex=TTL_SECONDS)
            
            logger.info(f"Маппинг сохранен в Redis: video_id={video_id} -> url={url}")
        except Exception as e:
            logger.error(f"Ошибка при сохранении маппинга в Redis: {e}")

    _HELLO_GIF_FILE_ID_KEY = "bot:hello_gif_file_id"

    async def get_hello_gif_file_id(self) -> Optional[str]:
        try:
            return await self.redis_client.get(self._HELLO_GIF_FILE_ID_KEY)
        except Exception as e:
            logger.error(f"Ошибка при получении hello_gif file_id: {e}")
            return None

    async def set_hello_gif_file_id(self, file_id: str) -> None:
        try:
            await self.redis_client.set(self._HELLO_GIF_FILE_ID_KEY, file_id)
            logger.info("hello_gif file_id сохранён в Redis")
        except Exception as e:
            logger.error(f"Ошибка при сохранении hello_gif file_id: {e}")
    
    async def delete_from_cache(self, video_id: str = None, url: str = None):
        key = None
        if video_id:
            key = self._get_video_key(video_id)
        elif url:
            url_mapping_key = self._get_url_mapping_key(url)
            video_id_from_mapping = await self.redis_client.get(url_mapping_key)
            if video_id_from_mapping:
                key = self._get_video_key(video_id_from_mapping)
            else:
                key = self._get_video_key(url)
        
        if key:
            try:
                await self.redis_client.delete(key)
                logger.info(f"Запись удалена из кэша: video_id={video_id}, url={url}")
            except Exception as e:
                logger.error(f"Ошибка при удалении записи из кэша: {e}")
    
    # TODO вынести все настройки качества в config.py
    async def get_best_cached_quality(self, video_id: str) -> Optional[tuple[str, str]]:
        """
        Получить лучшее доступное качество из кэша для YouTube видео (по разрешению)
        
        Args:
            video_id: Канонический ID видео (например, "youtube:123")
            
        Returns:
            Tuple (quality_label, file_id) или None если ничего не найдено.
            Приоритет по разрешению: 1080p > 720p > 480p > 360p > 240p > 144p
        """
        quality_priority = ['1080p', '720p', '480p', '360p', '240p', '144p']
        
        for quality in quality_priority:
            file_id = await self.get_cached_file_id(video_id=video_id, quality=quality)
            if file_id:
                logger.info(
                    f"Найдено лучшее качество в кэше: video_id={video_id}, quality={quality}"
                )
                return (quality, file_id)
        
        return None
    
    # TODO какая-то страшная функция
    # TODO вынести все настройки качества в config.py
    async def get_default_quality_for_download(self, formats: dict) -> Optional[tuple[str, str]]:
        """
        Получить качество по умолчанию для скачивания (лучшее доступное качество)
        
        Args:
            formats: Словарь с доступными форматами из get_available_formats()
                     ключи вида '144p', '240p', '360p', '480p', '720p', '1080p'
            
        Returns:
            Tuple (quality_label, format_id) или None
            
        Логика:
        - Берём лучшее доступное качество по приоритету:
          1080p -> 720p -> 480p -> 360p -> 240p -> 144p
        """
        if not formats:
            return None
        
        priority = ['1080p', '720p', '480p', '360p', '240p', '144p']

        for q in priority:
            if q in formats:
                format_id = formats[q]['format_id']
                return (q, format_id)

        return None
    
    async def check_quality_in_cache(self, video_id: str, quality: str) -> bool:
        try:
            file_id = await self.get_cached_file_id(video_id=video_id, quality=quality)
            return file_id is not None and file_id != ""
        except Exception as e:
            logger.error(f"Ошибка при проверке качества в кэше: video_id={video_id}, quality={quality}, error={e}")
            return False
    
    # TODO какая-то страшная функция
    async def get_original_url_by_video_id(self, video_id: str) -> Optional[str]:
        key = self._get_video_key(video_id)
        
        try:
            data_str = await self.redis_client.get(key)
            if data_str:
                data = json.loads(data_str)
                original_url = data.get('original_url')
                
                # Если original_url не является URL (это video_id), возвращаем None
                if original_url and original_url.startswith(('http://', 'https://')):
                    # Обновляем TTL при обращении к записи
                    await self.redis_client.expire(key, TTL_SECONDS)
                    return original_url
        except Exception as e:
            logger.error(f"Ошибка при получении original_url из Redis: {e}")
        
        return None
    
    async def acquire_download_lock(self, video_id: str, quality: str = None) -> bool:
        lock_key = self._get_lock_key(video_id, quality=quality)
        
        try:
            result = await self.redis_client.set(lock_key, "1", ex=LOCK_TTL_SECONDS, nx=True)
            if result:
                logger.info(f"Lock получен для video_id: {video_id}")
                return True
            else:
                logger.info(f"Lock уже занят для video_id: {video_id} (ожидание...)")
                return False
        except Exception as e:
            logger.error(f"Ошибка при получении lock для video_id {video_id}: {e}")
            return False
    
    async def release_download_lock(self, video_id: str, quality: str = None):
        lock_key = self._get_lock_key(video_id, quality=quality)
        
        try:
            await self.redis_client.delete(lock_key)
            logger.info(f"Lock освобожден для video_id: {video_id}")
        except Exception as e:
            logger.error(f"Ошибка при освобождении lock для video_id {video_id}: {e}")
    
    async def wait_for_download(self, video_id: str, timeout: float = 1800.0, quality: str = None) -> Optional[int]:
        start_time = time.time()
        channel = self._get_event_channel(video_id)
        
        logger.info(f"Ожидание скачивания video_id: {video_id}, quality: {quality} (timeout: {timeout}s)")
        
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe(channel)
        
        try:
            event_timeout = min(5.0, WAIT_POLL_INTERVAL)
            
            while time.time() - start_time < timeout:
                message_id = await self.get_cached_message_id(video_id=video_id, quality=quality)
                
                if message_id and message_id != 0:
                    logger.info(f"Видео скачано! video_id: {video_id}, quality: {quality}, message_id: {message_id}")
                    return message_id
                
                try:
                    message = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=event_timeout)
                    
                    if message:
                        try:
                            event_data = json.loads(message['data'])
                            status = event_data.get('status')
                            
                            if status == 'completed':
                                message_id = await self.get_cached_message_id(video_id=video_id, quality=quality)
                                if message_id and message_id != 0:
                                    logger.info(f"Видео скачано (событие)! video_id: {video_id}, quality: {quality}, message_id: {message_id}")
                                    return message_id
                            elif status == 'failed':
                                logger.warning(f"Скачивание завершилось с ошибкой для video_id: {video_id}, quality: {quality}")
                                return None
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(f"Ошибка при парсинге события: {e}")
                            continue
                except asyncio.TimeoutError:
                    pass
                except Exception as e:
                    logger.warning(f"Ошибка при чтении события Pub/Sub: {e}")
                
                await asyncio.sleep(0.5)
        
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception as e:
                logger.warning(f"Ошибка при закрытии pubsub: {e}")
        
        logger.warning(f"Timeout ожидания скачивания video_id: {video_id}, quality: {quality}")
        return None
    
    def _get_event_channel(self, video_id: str) -> str:
        """Получить ключ Redis для канала событий о завершении скачивания video_id"""
        video_hash = self.get_url_hash(video_id)
        return f"video_download_event:{video_hash}"
    
    async def publish_video_download_event(self, video_id: str, status: str, message_id: Optional[int] = None, file_id: Optional[str] = None):
        channel = self._get_event_channel(video_id)
        event_data = json.dumps({
            "status": status,
            "message_id": message_id,
            "file_id": file_id
        })
        await self.redis_client.publish(channel, event_data)
        logger.info(f"Опубликовано событие для {video_id}: {status}")
    
    async def increment_user_downloads(self, user_id: int) -> None:
        try:
            now = _dt.datetime.utcnow()
            day = now.strftime("%Y%m%d")
            month = now.strftime("%Y%m")

            total_key = self._get_user_downloads_total_key(user_id)
            await self.redis_client.incr(total_key)

            day_key = self._get_user_downloads_day_key(user_id, day)
            await self.redis_client.incr(day_key)
            await self.redis_client.expire(day_key, 3 * 24 * 60 * 60)

            month_key = self._get_user_downloads_month_key(user_id, month)
            await self.redis_client.incr(month_key)
            await self.redis_client.expire(month_key, 60 * 24 * 60 * 60)

        except Exception as e:
            logger.error(f"Ошибка при инкременте счетчиков пользователя {user_id}: {e}")

    async def get_user_downloads_count(self, user_id: int) -> int:
        try:
            total_key = self._get_user_downloads_total_key(user_id)
            value = await self.redis_client.get(total_key)
            return int(value) if value is not None else 0
        except Exception as e:
            logger.error(f"Ошибка при получении общего количества скачиваний пользователя {user_id}: {e}")
            return 0

    async def get_user_downloads_today(self, user_id: int) -> int:
        try:
            day = _dt.datetime.utcnow().strftime("%Y%m%d")
            day_key = self._get_user_downloads_day_key(user_id, day)
            value = await self.redis_client.get(day_key)
            return int(value) if value is not None else 0
        except Exception as e:
            logger.error(f"Ошибка при получении количества скачиваний за день для пользователя {user_id}: {e}")
            return 0

    async def get_user_downloads_month(self, user_id: int) -> int:
        try:
            month = _dt.datetime.utcnow().strftime("%Y%m")
            month_key = self._get_user_downloads_month_key(user_id, month)
            value = await self.redis_client.get(month_key)
            return int(value) if value is not None else 0
        except Exception as e:
            logger.error(f"Ошибка при получении количества скачиваний за месяц для пользователя {user_id}: {e}")
            return 0

    async def increment_video_downloads(self, video_id: str) -> None:
        try:
            key = self._get_video_downloads_total_key(video_id)
            await self.redis_client.incr(key)
        except Exception as e:
            logger.error(f"Ошибка при инкременте счетчика для видео {video_id}: {e}")
    
    async def add_download_task(self, url: str, video_id: str, platform: str = None, quality: str = None, format_id: str = None) -> bool:
        task_queue_key = self._get_task_queue_key()
        
        try:
            cached_message_id = await self.get_cached_message_id(video_id=video_id, quality=quality)
            if cached_message_id and cached_message_id != 0:
                logger.info(f"Видео уже в кэше, не добавляем в очередь: video_id={video_id}, quality={quality}")
                return False
            
            lock_key = self._get_lock_key(video_id, quality=quality)
            lock_exists = await self.redis_client.exists(lock_key)
            if lock_exists:
                logger.info(f"Видео уже обрабатывается (lock существует), не добавляем в очередь: video_id={video_id}")
                return False
            
            task = {
                'url': url,
                'video_id': video_id,
                'platform': platform,
                'status': 'pending',
                'quality': quality,
                'format_id': format_id
            }
            task_json = json.dumps(task)
            
            await self.redis_client.lpush(task_queue_key, task_json)
            
            logger.info(f"Задача добавлена в очередь: video_id={video_id}, url={url}")
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении задачи в очередь: {e}")
            return False
    
    async def get_download_task(self, timeout: int = 5) -> Optional[dict]:
        task_queue_key = self._get_task_queue_key()
        
        try:
            result = await self.redis_client.brpop(task_queue_key, timeout=timeout)
            
            if result:
                _, task_json = result
                task = json.loads(task_json)
                logger.info(f"Задача получена из очереди: video_id={task.get('video_id')}")
                return task
            else:
                return None
        except Exception as e:
            logger.error(f"Ошибка при получении задачи из очереди: {e}")
            return None
    
    async def add_analytics_event(self, event_json: str) -> bool:
        queue_key = self._get_analytics_queue_key()
        
        try:
            await self.redis_client.lpush(queue_key, event_json)
            logger.debug(f"Событие аналитики добавлено в очередь")
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении события аналитики в очередь: {e}")
            return False
    
    async def get_analytics_event(self, timeout: int = 5) -> Optional[str]:
        queue_key = self._get_analytics_queue_key()
        
        try:
            result = await self.redis_client.brpop(queue_key, timeout=timeout)
            
            if result:
                _, event_json = result
                logger.debug(f"Событие аналитики получено из очереди")
                return event_json
            else:
                return None
        except Exception as e:
            logger.error(f"Ошибка при получении события аналитики из очереди: {e}")
            return None
    
    async def close(self):
        """Закрыть подключение к Redis"""
        await self.redis_client.close()
