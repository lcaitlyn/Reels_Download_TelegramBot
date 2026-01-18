"""
Модуль для работы с Redis
Хранит кэш: video_id -> {message_id, file_id, platform, original_url}
TTL: 7 дней (604800 секунд)
"""
import json
import hashlib
import asyncio
from typing import Optional
from redis import asyncio as redis
import os
from dotenv import load_dotenv

load_dotenv()

# TTL в секундах (7 дней = 7 * 24 * 60 * 60)
TTL_SECONDS = 7 * 24 * 60 * 60  # 604800 секунд

# TTL для lock (максимальное время скачивания видео - 30 минут)
LOCK_TTL_SECONDS = 30 * 60  # 1800 секунд

# Интервал проверки при ожидании скачивания (в секундах)
WAIT_POLL_INTERVAL = 1.0  # 1 секунда


class Database:
    def __init__(self, redis_url: str = None):
        """
        Инициализация Redis подключения
        
        Args:
            redis_url: URL для подключения к Redis (по умолчанию из .env или localhost)
        """
        redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
        self.logger = None  # Будет установлен после инициализации logging
    
    def _get_logger(self):
        """Получить logger (ленивая инициализация)"""
        if self.logger is None:
            import logging
            self.logger = logging.getLogger(__name__)
        return self.logger
    
    def get_url_hash(self, key: str) -> str:
        """Генерация хэша ключа для использования как часть ключа в Redis"""
        return hashlib.sha256(key.encode()).hexdigest()
    
    def _get_video_key(self, video_id: str) -> str:
        """Получить ключ Redis для video_id"""
        video_hash = self.get_url_hash(video_id)
        return f"video:{video_hash}"
    
    def _get_url_mapping_key(self, url: str) -> str:
        """Получить ключ Redis для маппинга URL -> video_id"""
        url_hash = self.get_url_hash(url)
        return f"url_mapping:{url_hash}"
    
    def _get_lock_key(self, video_id: str) -> str:
        """Получить ключ Redis для lock на скачивание video_id"""
        video_hash = self.get_url_hash(video_id)
        return f"lock:video:{video_hash}"
    
    def _get_task_queue_key(self) -> str:
        """Получить ключ Redis для очереди задач"""
        return "tasks:download_queue"
    
    async def get_cached_message_id(self, video_id: str = None, url: str = None) -> Optional[int]:
        """
        Получить message_id из кэша по video_id или URL
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            url: URL видео (для обратной совместимости)
            
        Returns:
            message_id или None
        """
        key = None
        
        if video_id:
            key = self._get_video_key(video_id)
        elif url:
            # Сначала пытаемся получить video_id из маппинга URL
            url_mapping_key = self._get_url_mapping_key(url)
            video_id_from_mapping = await self.redis_client.get(url_mapping_key)
            if video_id_from_mapping:
                key = self._get_video_key(video_id_from_mapping)
            else:
                # Fallback: используем URL как ключ (обратная совместимость)
                key = self._get_video_key(url)
        
        if not key:
            return None
        
        try:
            data_str = await self.redis_client.get(key)
            if data_str:
                data = json.loads(data_str)
                message_id = data.get('message_id')
                
                # Обновляем TTL при обращении к записи
                await self.redis_client.expire(key, TTL_SECONDS)
                
                return int(message_id) if message_id else None
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении message_id из Redis: {e}")
        
        return None
    
    async def get_cached_file_id(self, video_id: str = None, url: str = None) -> Optional[str]:
        """
        Получить file_id из кэша по video_id или URL
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            url: URL видео (для обратной совместимости)
            
        Returns:
            file_id или None
        """
        key = None
        
        if video_id:
            key = self._get_video_key(video_id)
        elif url:
            # Сначала пытаемся получить video_id из маппинга URL
            url_mapping_key = self._get_url_mapping_key(url)
            video_id_from_mapping = await self.redis_client.get(url_mapping_key)
            if video_id_from_mapping:
                key = self._get_video_key(video_id_from_mapping)
            else:
                # Fallback: используем URL как ключ (обратная совместимость)
                key = self._get_video_key(url)
        
        if not key:
            return None
        
        try:
            data_str = await self.redis_client.get(key)
            if data_str:
                data = json.loads(data_str)
                file_id = data.get('file_id')
                
                # Обновляем TTL при обращении к записи
                await self.redis_client.expire(key, TTL_SECONDS)
                
                return file_id
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении file_id из Redis: {e}")
        
        return None
    
    async def save_to_cache(self, video_id: str, message_id: int, platform: str = None, file_id: str = None, original_url: str = None):
        """
        Сохранить message_id и file_id в кэш используя video_id как ключ
        Это предотвращает дубликаты для разных URL одного и того же видео
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            message_id: ID сообщения в Telegram канале
            platform: Платформа (youtube, instagram, tiktok)
            file_id: file_id для InlineQueryResultCachedVideo
            original_url: Оригинальный URL для справки
        """
        key = self._get_video_key(video_id)
        
        try:
            # Пытаемся получить существующие данные
            existing_data_str = await self.redis_client.get(key)
            existing_data = json.loads(existing_data_str) if existing_data_str else {}
            
            # Сохраняем file_id, если он не передан или None
            if file_id is None and existing_data.get('file_id'):
                file_id = existing_data.get('file_id')
            
            # Используем video_id как original_url для хранения канонического идентификатора
            original_url = original_url or video_id
            
            # Подготавливаем данные для сохранения
            data = {
                'message_id': message_id,
                'file_id': file_id,
                'platform': platform,
                'original_url': original_url,
                'video_id': video_id  # Сохраняем video_id для удобства
            }
            
            # Сохраняем в Redis с TTL
            await self.redis_client.set(key, json.dumps(data), ex=TTL_SECONDS)
            
            # Если original_url является URL (не video_id), сохраняем маппинг URL -> video_id
            if original_url.startswith(('http://', 'https://')):
                url_mapping_key = self._get_url_mapping_key(original_url)
                await self.redis_client.set(url_mapping_key, video_id, ex=TTL_SECONDS)
            
            self._get_logger().info(f"Данные сохранены в Redis: key={key}, video_id={video_id}")
        except Exception as e:
            self._get_logger().error(f"Ошибка при сохранении в Redis: {e}")
    
    async def save_url_mapping(self, video_id: str, url: str, platform: str = None):
        """
        Сохранить маппинг video_id -> url БЕЗ message_id (до скачивания видео)
        Используется для сохранения URL при inline-запросе
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            url: URL видео
            platform: Платформа (youtube, instagram, tiktok)
        """
        try:
            # Сохраняем маппинг URL -> video_id
            url_mapping_key = self._get_url_mapping_key(url)
            await self.redis_client.set(url_mapping_key, video_id, ex=TTL_SECONDS)
            
            # Сохраняем запись для video_id с message_id = 0 (означает "еще не скачано")
            video_key = self._get_video_key(video_id)
            data = {
                'message_id': 0,  # 0 означает "еще не скачано"
                'file_id': None,
                'platform': platform,
                'original_url': url,
                'video_id': video_id
            }
            await self.redis_client.set(video_key, json.dumps(data), ex=TTL_SECONDS)
            
            self._get_logger().info(f"Маппинг сохранен в Redis: video_id={video_id} -> url={url}")
        except Exception as e:
            self._get_logger().error(f"Ошибка при сохранении маппинга в Redis: {e}")
    
    async def get_original_url_by_video_id(self, video_id: str) -> Optional[str]:
        """
        Получить original_url по video_id
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            
        Returns:
            original_url или None
        """
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
            self._get_logger().error(f"Ошибка при получении original_url из Redis: {e}")
        
        return None
    
    async def acquire_download_lock(self, video_id: str) -> bool:
        """
        Попытаться получить lock на скачивание video_id
        Использует Redis SET NX (set if not exists) для атомарности
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            
        Returns:
            True если получили lock (первый запрос), False если lock уже занят (ждущие запросы)
        """
        lock_key = self._get_lock_key(video_id)
        
        try:
            # SET NX - устанавливает значение только если ключ не существует (атомарно)
            result = await self.redis_client.set(lock_key, "1", ex=LOCK_TTL_SECONDS, nx=True)
            if result:
                self._get_logger().info(f"Lock получен для video_id: {video_id}")
                return True
            else:
                self._get_logger().info(f"Lock уже занят для video_id: {video_id} (ожидание...)")
                return False
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении lock для video_id {video_id}: {e}")
            return False
    
    async def release_download_lock(self, video_id: str):
        """
        Освободить lock на скачивание video_id
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
        """
        lock_key = self._get_lock_key(video_id)
        
        try:
            await self.redis_client.delete(lock_key)
            self._get_logger().info(f"Lock освобожден для video_id: {video_id}")
        except Exception as e:
            self._get_logger().error(f"Ошибка при освобождении lock для video_id {video_id}: {e}")
    
    async def wait_for_download(self, video_id: str, timeout: float = 1800.0) -> Optional[int]:
        """
        Ожидать завершения скачивания video_id (для запросов, которые не получили lock)
        Периодически проверяет кэш, пока не появится message_id
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            timeout: Максимальное время ожидания в секундах (по умолчанию 30 минут)
            
        Returns:
            message_id когда видео скачано, или None при timeout
        """
        import time
        
        start_time = time.time()
        
        self._get_logger().info(f"Ожидание скачивания video_id: {video_id} (timeout: {timeout}s)")
        
        while time.time() - start_time < timeout:
            # Проверяем кэш
            message_id = await self.get_cached_message_id(video_id=video_id)
            
            # message_id != 0 и != None означает, что видео скачано
            if message_id and message_id != 0:
                self._get_logger().info(f"Видео скачано! video_id: {video_id}, message_id: {message_id}")
                return message_id
            
            # Ждем перед следующей проверкой
            await asyncio.sleep(WAIT_POLL_INTERVAL)
        
        self._get_logger().warning(f"Timeout ожидания скачивания video_id: {video_id}")
        return None
    
    def _get_event_channel(self, video_id: str) -> str:
        """Получить ключ Redis для канала событий о завершении скачивания video_id"""
        video_hash = self.get_url_hash(video_id)
        return f"video_download_event:{video_hash}"
    
    async def publish_video_download_event(self, video_id: str, status: str, message_id: Optional[int] = None, file_id: Optional[str] = None):
        """
        Публикует событие о завершении скачивания видео через Redis Pub/Sub
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            status: Статус события ('completed' или 'failed')
            message_id: ID сообщения в Telegram канале (для статуса 'completed')
            file_id: file_id видео (для статуса 'completed')
        """
        channel = self._get_event_channel(video_id)
        event_data = json.dumps({
            "status": status,
            "message_id": message_id,
            "file_id": file_id
        })
        await self.redis_client.publish(channel, event_data)
        self._get_logger().info(f"Опубликовано событие для {video_id}: {status}")
    
    async def add_download_task(self, url: str, video_id: str, platform: str = None) -> bool:
        """
        Добавить задачу на скачивание в очередь для background worker
        
        Args:
            url: URL видео для скачивания
            video_id: Канонический ID видео (например, "instagram:123")
            platform: Платформа (youtube, instagram, tiktok)
            
        Returns:
            True если задача добавлена, False если уже в очереди или кэше
        """
        task_queue_key = self._get_task_queue_key()
        
        try:
            # Проверяем, не скачивается ли уже это видео (lock или в кэше)
            cached_message_id = await self.get_cached_message_id(video_id=video_id)
            if cached_message_id and cached_message_id != 0:
                self._get_logger().info(f"Видео уже в кэше, не добавляем в очередь: video_id={video_id}")
                return False
            
            # Проверяем, не обрабатывается ли уже задача (lock)
            lock_key = self._get_lock_key(video_id)
            lock_exists = await self.redis_client.exists(lock_key)
            if lock_exists:
                self._get_logger().info(f"Видео уже обрабатывается (lock существует), не добавляем в очередь: video_id={video_id}")
                return False
            
            # Формируем задачу
            task = {
                'url': url,
                'video_id': video_id,
                'platform': platform,
                'status': 'pending'
            }
            task_json = json.dumps(task)
            
            # Добавляем задачу в очередь (LPUSH - добавляет в начало списка)
            await self.redis_client.lpush(task_queue_key, task_json)
            
            self._get_logger().info(f"Задача добавлена в очередь: video_id={video_id}, url={url}")
            return True
        except Exception as e:
            self._get_logger().error(f"Ошибка при добавлении задачи в очередь: {e}")
            return False
    
    async def get_download_task(self, timeout: int = 5) -> Optional[dict]:
        """
        Получить задачу на скачивание из очереди (блокирующее ожидание)
        
        Args:
            timeout: Максимальное время ожидания задачи в секундах (по умолчанию 5 секунд)
            
        Returns:
            Словарь с задачей (url, video_id, platform) или None при timeout
        """
        task_queue_key = self._get_task_queue_key()
        
        try:
            # BRPOP - блокирующее извлечение элемента из конца списка (FIFO)
            # Возвращает (key, value) или None при timeout
            result = await self.redis_client.brpop(task_queue_key, timeout=timeout)
            
            if result:
                _, task_json = result
                task = json.loads(task_json)
                self._get_logger().info(f"Задача получена из очереди: video_id={task.get('video_id')}")
                return task
            else:
                # Timeout - нет задач в очереди
                return None
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении задачи из очереди: {e}")
            return None
    
    async def close(self):
        """Закрыть подключение к Redis"""
        await self.redis_client.close()
