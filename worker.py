"""
Background Worker для обработки задач на скачивание видео из очереди Redis
Работает как отдельный процесс, слушает очередь задач и скачивает видео
"""
import os
import asyncio
import logging
from typing import Optional
from dotenv import load_dotenv
from aiogram import Bot, types
from aiogram.client.session.aiohttp import AiohttpSession

from database import Database
from downloader import VideoDownloader
from utils import get_platform

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Проверка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")
if not CHANNEL_ID:
    raise ValueError("TELEGRAM_CHANNEL_ID не установлен в переменных окружения")

# Преобразуем CHANNEL_ID в int, если это число
try:
    CHANNEL_ID = int(CHANNEL_ID)
except ValueError:
    pass  # Оставляем строку, если это username канала

# Инициализация компонентов
session = AiohttpSession(timeout=600)
bot = Bot(token=BOT_TOKEN, session=session)
db = Database()
downloader = VideoDownloader()


async def process_download_task(task: dict) -> Optional[int]:
    """
    Обработать задачу на скачивание видео
    
    Args:
        task: Словарь с задачей (url, video_id, platform)
        
    Returns:
        message_id или None при ошибке
    """
    url = task.get('url')
    video_id = task.get('video_id')
    platform = task.get('platform')
    
    logger.info(f"[worker] Начало обработки задачи: url={url}, video_id={video_id}")
    
    if not url or not video_id:
        logger.error(f"[worker] Невалидная задача: {task}")
        return None
    
    # Пытаемся получить lock на скачивание
    got_lock = await db.acquire_download_lock(video_id)
    
    if not got_lock:
        # Lock не получен - кто-то уже скачивает, не обрабатываем задачу повторно
        logger.info(f"[worker] Lock занят для video_id={video_id}, пропускаем задачу (кто-то уже скачивает)")
        return None
    
    try:
        # Проверяем кэш еще раз (на случай если пока ждали lock, видео уже скачали)
        cached_message_id = await db.get_cached_message_id(video_id=video_id)
        if cached_message_id and cached_message_id != 0:
            logger.info(f"[worker] Видео уже в кэше: video_id={video_id}, message_id={cached_message_id}")
            return cached_message_id
        
        # Скачиваем видео
        logger.info(f"[worker] Начало скачивания: url={url}, video_id={video_id}")
        video_path = downloader.download_video(url)
        
        if not video_path:
            logger.error(f"[worker] Не удалось скачать видео: url={url}")
            return None
        
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        logger.info(f"[worker] Размер файла: {file_size_mb:.2f} MB")
        
        # Отправляем видео в канал
        logger.info(f"[worker] Загрузка в канал: {video_path}")
        message = await bot.send_video(
            chat_id=CHANNEL_ID,
            video=types.FSInputFile(video_path),
            caption=f"Source: {url}"
        )
        message_id = message.message_id
        
        # Получаем file_id из видео
        file_id = None
        if message.video:
            file_id = message.video.file_id
        elif message.document:
            file_id = message.document.file_id
        
        # Сохраняем в кэш
        platform = platform or get_platform(url)
        await db.save_to_cache(video_id, message_id, platform, file_id, original_url=url)
        
        logger.info(f"[worker] ✅ Видео успешно скачано и сохранено в кэш: video_id={video_id}, message_id={message_id}")
        
        # Публикуем событие о завершении скачивания (для wait_for_download)
        await db.publish_video_download_event(video_id, 'completed', message_id, file_id)
        
        return message_id
        
    except Exception as e:
        logger.error(f"[worker] Ошибка при обработке задачи: {e}", exc_info=True)
        # Публикуем событие об ошибке
        await db.publish_video_download_event(video_id, 'failed')
        return None
    finally:
        # Удаляем временный файл
        try:
            if 'video_path' in locals() and video_path and os.path.exists(video_path):
                os.remove(video_path)
                logger.info(f"[worker] Временный файл удален: {video_path}")
        except Exception as e:
            logger.warning(f"[worker] Не удалось удалить файл {video_path}: {e}")
        
        # Освобождаем lock
        await db.release_download_lock(video_id)


async def worker_loop():
    """
    Основной цикл worker'а - слушает очередь задач и обрабатывает их
    """
    logger.info("[worker] Background worker запущен")
    logger.info("[worker] Ожидание задач из очереди Redis...")
    
    while True:
        try:
            # Получаем задачу из очереди (блокирующее ожидание, timeout 5 секунд)
            task = await db.get_download_task(timeout=5)
            
            if task:
                # Задача получена - обрабатываем
                logger.info(f"[worker] Получена задача: video_id={task.get('video_id')}")
                await process_download_task(task)
            else:
                # Timeout - нет задач, продолжаем ожидание
                # Можно добавить небольшую задержку, чтобы не нагружать Redis
                await asyncio.sleep(0.1)
                
        except KeyboardInterrupt:
            logger.info("[worker] Получен сигнал остановки (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.error(f"[worker] Ошибка в worker_loop: {e}", exc_info=True)
            # Небольшая задержка перед повторной попыткой
            await asyncio.sleep(1)


async def main():
    """Главная функция worker'а"""
    try:
        await worker_loop()
    except KeyboardInterrupt:
        logger.info("[worker] Получен сигнал остановки")
    finally:
        # Закрываем соединения
        await db.close()
        await bot.session.close()
        logger.info("[worker] Worker остановлен")


if __name__ == "__main__":
    asyncio.run(main())
