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
        if not redis_url:
            # Сначала проверяем полный URL
            redis_url = os.getenv("REDIS_URL")
            if not redis_url:
                # Если нет полного URL, собираем из отдельных переменных
                redis_host = os.getenv("REDIS_HOST", "localhost")
                redis_port = os.getenv("REDIS_PORT", "6379")
                redis_db = os.getenv("REDIS_DB", "0")
                redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
        
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
    
    def _get_video_key(self, video_id: str, quality: str = None) -> str:
        """
        Получить ключ Redis для video_id с опциональным качеством
        
        Args:
            video_id: Канонический ID видео (например, "youtube:ABC123")
            quality: Качество видео (например, "480p", "720p", "1080p", "audio") или None для базового ключа
        """
        video_hash = self.get_url_hash(video_id)
        if quality:
            return f"video:{video_hash}:{quality}"
        return f"video:{video_hash}"
    
    def _get_url_mapping_key(self, url: str) -> str:
        """Получить ключ Redis для маппинга URL -> video_id"""
        url_hash = self.get_url_hash(url)
        return f"url_mapping:{url_hash}"
    
    def _get_lock_key(self, video_id: str, quality: str = None) -> str:
        """
        Получить ключ Redis для lock на скачивание video_id
        
        Args:
            video_id: Канонический ID видео
            quality: Качество видео (если указано, lock будет на уровне video_id:quality)
        """
        video_hash = self.get_url_hash(video_id)
        if quality:
            return f"lock:video:{video_hash}:{quality}"
        return f"lock:video:{video_hash}"
    
    def _get_task_queue_key(self) -> str:
        """Получить ключ Redis для очереди задач"""
        return "tasks:download_queue"
    
    def _get_analytics_queue_key(self) -> str:
        """Получить ключ Redis для очереди событий аналитики"""
        return "events:analytics_queue"

    # --------- Ключи для счетчиков аналитики ---------

    def _get_user_downloads_total_key(self, user_id: int) -> str:
        """Ключ для общего количества скачиваний пользователя"""
        return f"user:{user_id}:downloads_total"

    def _get_user_downloads_day_key(self, user_id: int, day: str) -> str:
        """Ключ для количества скачиваний пользователя за день (формат day: YYYYMMDD)"""
        return f"user:{user_id}:downloads:day:{day}"

    def _get_user_downloads_month_key(self, user_id: int, month: str) -> str:
        """Ключ для количества скачиваний пользователя за месяц (формат month: YYYYMM)"""
        return f"user:{user_id}:downloads:month:{month}"

    def _get_video_downloads_total_key(self, video_id: str) -> str:
        """Ключ для общего количества скачиваний видео"""
        video_hash = self.get_url_hash(video_id)
        return f"video:{video_hash}:downloads_total"
    
    async def get_cached_message_id(self, video_id: str = None, url: str = None, quality: str = None) -> Optional[int]:
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
            key = self._get_video_key(video_id, quality=quality)
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
    
    async def get_cached_file_id(self, video_id: str = None, url: str = None, quality: str = None) -> Optional[str]:
        """
        Получить file_id из кэша по video_id или URL
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            url: URL видео (для обратной совместимости)
            quality: Качество видео (например, "480p", "720p", "1080p", "audio") или None для базового ключа
            
        Returns:
            file_id или None
        """
        key = None
        
        if video_id:
            key = self._get_video_key(video_id, quality=quality)
        elif url:
            # Сначала пытаемся получить video_id из маппинга URL
            url_mapping_key = self._get_url_mapping_key(url)
            video_id_from_mapping = await self.redis_client.get(url_mapping_key)
            if video_id_from_mapping:
                key = self._get_video_key(video_id_from_mapping, quality=quality)
            else:
                # Fallback: используем URL как ключ (обратная совместимость)
                key = self._get_video_key(url, quality=quality)
        
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
    
    async def save_to_cache(self, video_id: str, message_id: int, platform: str = None, file_id: str = None, original_url: str = None, quality: str = None):
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
        key = self._get_video_key(video_id, quality=quality)
        
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
                'video_id': video_id,  # Сохраняем video_id для удобства
                'quality': quality  # Сохраняем качество (если указано)
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
    
    async def delete_from_cache(self, video_id: str = None, url: str = None):
        """
        Удалить запись из кэша (например, если message_id невалидный)
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            url: URL видео (для обратной совместимости)
        """
        key = None
        
        # Удаляем базовую запись (без качества) для обратной совместимости
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
                self._get_logger().info(f"Запись удалена из кэша: video_id={video_id}, url={url}")
            except Exception as e:
                self._get_logger().error(f"Ошибка при удалении записи из кэша: {e}")
    
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
                self._get_logger().info(
                    f"Найдено лучшее качество в кэше: video_id={video_id}, quality={quality}"
                )
                return (quality, file_id)
        
        return None
    
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
        """
        Проверить, есть ли видео с указанным качеством в кэше
        
        Args:
            video_id: Канонический ID видео (например, "youtube:123")
            quality: Качество видео (например, "480p", "720p", "1080p")
            
        Returns:
            True если видео с таким качеством есть в кэше, False иначе
        """
        try:
            file_id = await self.get_cached_file_id(video_id=video_id, quality=quality)
            return file_id is not None and file_id != ""
        except Exception as e:
            self._get_logger().error(f"Ошибка при проверке качества в кэше: video_id={video_id}, quality={quality}, error={e}")
            return False
    
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
    
    async def acquire_download_lock(self, video_id: str, quality: str = None) -> bool:
        """
        Попытаться получить lock на скачивание video_id
        Использует Redis SET NX (set if not exists) для атомарности
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            
        Returns:
            True если получили lock (первый запрос), False если lock уже занят (ждущие запросы)
        """
        lock_key = self._get_lock_key(video_id, quality=quality)
        
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
    
    async def release_download_lock(self, video_id: str, quality: str = None):
        """
        Освободить lock на скачивание video_id
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
        """
        lock_key = self._get_lock_key(video_id, quality=quality)
        
        try:
            await self.redis_client.delete(lock_key)
            self._get_logger().info(f"Lock освобожден для video_id: {video_id}")
        except Exception as e:
            self._get_logger().error(f"Ошибка при освобождении lock для video_id {video_id}: {e}")
    
    async def wait_for_download(self, video_id: str, timeout: float = 1800.0, quality: str = None) -> Optional[int]:
        """
        Ожидать завершения скачивания video_id (для запросов, которые не получили lock)
        Слушает события Pub/Sub и периодически проверяет кэш
        
        Args:
            video_id: Канонический ID видео (например, "instagram:123")
            timeout: Максимальное время ожидания в секундах (по умолчанию 30 минут)
            quality: Качество видео (например, "480p", "720p", "1080p", "audio") или None для базового ключа
            
        Returns:
            message_id когда видео скачано, или None при timeout/ошибке
        """
        import time
        
        start_time = time.time()
        channel = self._get_event_channel(video_id)
        
        self._get_logger().info(f"Ожидание скачивания video_id: {video_id}, quality: {quality} (timeout: {timeout}s)")
        
        # Создаем подписку на канал событий
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe(channel)
        
        try:
            # Слушаем события с таймаутом
            event_timeout = min(5.0, WAIT_POLL_INTERVAL)  # Таймаут для получения события (5 секунд или WAIT_POLL_INTERVAL)
            
            while time.time() - start_time < timeout:
                # Сначала проверяем кэш (быстрая проверка)
                message_id = await self.get_cached_message_id(video_id=video_id, quality=quality)
                
                # message_id != 0 и != None означает, что видео скачано
                if message_id and message_id != 0:
                    self._get_logger().info(f"Видео скачано! video_id: {video_id}, quality: {quality}, message_id: {message_id}")
                    return message_id
                
                # Проверяем события Pub/Sub (неблокирующее чтение)
                try:
                    # Получаем сообщение с таймаутом
                    message = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True), timeout=event_timeout)
                    
                    if message:
                        try:
                            event_data = json.loads(message['data'])
                            status = event_data.get('status')
                            
                            if status == 'completed':
                                # Видео скачано - проверяем кэш еще раз
                                message_id = await self.get_cached_message_id(video_id=video_id, quality=quality)
                                if message_id and message_id != 0:
                                    self._get_logger().info(f"Видео скачано (событие)! video_id: {video_id}, quality: {quality}, message_id: {message_id}")
                                    return message_id
                            elif status == 'failed':
                                # Скачивание завершилось с ошибкой
                                self._get_logger().warning(f"Скачивание завершилось с ошибкой для video_id: {video_id}, quality: {quality}")
                                return None
                        except (json.JSONDecodeError, KeyError) as e:
                            self._get_logger().warning(f"Ошибка при парсинге события: {e}")
                            continue
                except asyncio.TimeoutError:
                    # Таймаут при получении события - это нормально, продолжаем проверку кэша
                    pass
                except Exception as e:
                    self._get_logger().warning(f"Ошибка при чтении события Pub/Sub: {e}")
                
                # Небольшая задержка перед следующей проверкой
                await asyncio.sleep(0.5)
        
        finally:
            # Отписываемся от канала
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception as e:
                self._get_logger().warning(f"Ошибка при закрытии pubsub: {e}")
        
        self._get_logger().warning(f"Timeout ожидания скачивания video_id: {video_id}, quality: {quality}")
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

    # --------- Счетчики скачиваний (для быстрых проверок) ---------

    async def increment_user_downloads(self, user_id: int) -> None:
        """
        Инкремент общего количества скачиваний пользователя и счетчиков за день/месяц.
        Не бросает исключения наружу, логирует ошибки.
        """
        import datetime as _dt

        try:
            now = _dt.datetime.utcnow()
            day = now.strftime("%Y%m%d")
            month = now.strftime("%Y%m")

            # Общий счетчик
            total_key = self._get_user_downloads_total_key(user_id)
            await self.redis_client.incr(total_key)

            # За день (TTL ~3 дня)
            day_key = self._get_user_downloads_day_key(user_id, day)
            await self.redis_client.incr(day_key)
            await self.redis_client.expire(day_key, 3 * 24 * 60 * 60)

            # За месяц (TTL ~2 месяца)
            month_key = self._get_user_downloads_month_key(user_id, month)
            await self.redis_client.incr(month_key)
            await self.redis_client.expire(month_key, 60 * 24 * 60 * 60)

        except Exception as e:
            self._get_logger().error(f"Ошибка при инкременте счетчиков пользователя {user_id}: {e}")

    async def get_user_downloads_count(self, user_id: int) -> int:
        """
        Получить общее количество скачиваний пользователя.
        Возвращает 0 при отсутствии данных или ошибке.
        """
        try:
            total_key = self._get_user_downloads_total_key(user_id)
            value = await self.redis_client.get(total_key)
            return int(value) if value is not None else 0
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении общего количества скачиваний пользователя {user_id}: {e}")
            return 0

    async def get_user_downloads_today(self, user_id: int) -> int:
        """
        Получить количество скачиваний пользователя за текущий день.
        Возвращает 0 при отсутствии данных или ошибке.
        """
        import datetime as _dt

        try:
            day = _dt.datetime.utcnow().strftime("%Y%m%d")
            day_key = self._get_user_downloads_day_key(user_id, day)
            value = await self.redis_client.get(day_key)
            return int(value) if value is not None else 0
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении количества скачиваний за день для пользователя {user_id}: {e}")
            return 0

    async def get_user_downloads_month(self, user_id: int) -> int:
        """
        Получить количество скачиваний пользователя за текущий месяц.
        Возвращает 0 при отсутствии данных или ошибке.
        """
        import datetime as _dt

        try:
            month = _dt.datetime.utcnow().strftime("%Y%m")
            month_key = self._get_user_downloads_month_key(user_id, month)
            value = await self.redis_client.get(month_key)
            return int(value) if value is not None else 0
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении количества скачиваний за месяц для пользователя {user_id}: {e}")
            return 0

    async def increment_video_downloads(self, video_id: str) -> None:
        """
        Инкремент общего количества скачиваний видео.
        Не бросает исключения наружу, логирует ошибки.
        """
        try:
            key = self._get_video_downloads_total_key(video_id)
            await self.redis_client.incr(key)
        except Exception as e:
            self._get_logger().error(f"Ошибка при инкременте счетчика для видео {video_id}: {e}")
    
    async def add_download_task(self, url: str, video_id: str, platform: str = None, quality: str = None, format_id: str = None) -> bool:
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
            # Если указано качество, проверяем кэш для этого качества
            cached_message_id = await self.get_cached_message_id(video_id=video_id, quality=quality)
            if cached_message_id and cached_message_id != 0:
                self._get_logger().info(f"Видео уже в кэше, не добавляем в очередь: video_id={video_id}, quality={quality}")
                return False
            
            # Проверяем, не обрабатывается ли уже задача (lock)
            lock_key = self._get_lock_key(video_id, quality=quality)
            lock_exists = await self.redis_client.exists(lock_key)
            if lock_exists:
                self._get_logger().info(f"Видео уже обрабатывается (lock существует), не добавляем в очередь: video_id={video_id}")
                return False
            
            # Формируем задачу
            task = {
                'url': url,
                'video_id': video_id,
                'platform': platform,
                'status': 'pending',
                'quality': quality,  # Качество (480p, 720p, 1080p, audio)
                'format_id': format_id  # ID формата из yt-dlp
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
    
    async def add_analytics_event(self, event_json: str) -> bool:
        """
        Добавить событие аналитики в очередь Redis
        
        Args:
            event_json: JSON-строка с событием (сериализованное событие из events.py)
            
        Returns:
            True если событие добавлено, False при ошибке
        """
        queue_key = self._get_analytics_queue_key()
        
        try:
            await self.redis_client.lpush(queue_key, event_json)
            self._get_logger().debug(f"Событие аналитики добавлено в очередь")
            return True
        except Exception as e:
            self._get_logger().error(f"Ошибка при добавлении события аналитики в очередь: {e}")
            return False
    
    async def get_analytics_event(self, timeout: int = 5) -> Optional[str]:
        """
        Получить событие аналитики из очереди (блокирующее ожидание)
        
        Args:
            timeout: Максимальное время ожидания события в секундах (по умолчанию 5 секунд)
            
        Returns:
            JSON-строка с событием или None при timeout
        """
        queue_key = self._get_analytics_queue_key()
        
        try:
            # BRPOP - блокирующее извлечение элемента из конца списка (FIFO)
            result = await self.redis_client.brpop(queue_key, timeout=timeout)
            
            if result:
                _, event_json = result
                self._get_logger().debug(f"Событие аналитики получено из очереди")
                return event_json
            else:
                # Timeout - нет событий в очереди
                return None
        except Exception as e:
            self._get_logger().error(f"Ошибка при получении события аналитики из очереди: {e}")
            return None
    
    async def close(self):
        """Закрыть подключение к Redis"""
        await self.redis_client.close()
