"""
Background Worker для обработки задач на скачивание видео из очереди Redis
Тупой исполнитель - исполняет DownloadPlan через YtDlpService
"""
import os
import io
import asyncio
import logging
from typing import Optional
from dotenv import load_dotenv
from aiogram import Bot, types
from aiogram.client.session.aiohttp import AiohttpSession

from src.database.redis_db import Database
from src.downloader.downloader import VideoDownloader
from src.utils.utils import get_platform
from src.events.events import DownloadCompletedEvent
from src.services.service_factory import ServiceFactory
from src.services.link_processing_service import LinkProcessingService
from src.services.ytdlp_service import YtDlpService

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
downloader = VideoDownloader()  # Для обратной совместимости (будет удален)
service_factory = ServiceFactory(downloader)
link_processor = LinkProcessingService(service_factory)
ytdlp_service = YtDlpService()


async def process_download_task(task: dict) -> Optional[int]:
    """
    Worker - тупой исполнитель
    
    Алгоритм:
    1. Берет DownloadTask из очереди
    2. Получает LinkInfo через LinkProcessingService
    3. Получает DownloadPlan через PlatformService
    4. Исполняет DownloadPlan через YtDlpService
    5. Загружает в Telegram канал
    6. Сохраняет file_id в Redis
    7. Публикует событие VIDEO_READY
    
    Args:
        task: Словарь с задачей (url, video_id, platform, quality, format_id)
        
    Returns:
        message_id или None при ошибке
    """
    url = task.get('url')
    video_id = task.get('video_id')
    platform = task.get('platform')
    quality = task.get('quality')
    format_id = task.get('format_id')
    
    logger.info(f"[worker] Начало обработки задачи: url={url}, video_id={video_id}, quality={quality}, format_id={format_id}")
    
    if not url or not video_id:
        logger.error(f"[worker] Невалидная задача: {task}")
        return None
    
    # Пытаемся получить lock на скачивание (с учетом качества)
    # ВАЖНО: Lock должен быть получен ДО входа в try, чтобы finally мог его освободить
    got_lock = await db.acquire_download_lock(video_id, quality=quality)
    
    if not got_lock:
        # Lock не получен - кто-то уже скачивает, не обрабатываем задачу повторно
        logger.info(f"[worker] Lock занят для video_id={video_id}, quality={quality}, пропускаем задачу (кто-то уже скачивает)")
        return None
    
    # Lock получен - обрабатываем задачу
    # ВАЖНО: Все return None внутри try будут освобождать lock в finally
    try:
        # Проверяем кэш еще раз (на случай если пока ждали lock, видео уже скачали)
        cached_message_id = await db.get_cached_message_id(video_id=video_id, quality=quality)
        if cached_message_id and cached_message_id != 0:
            logger.info(f"[worker] Видео уже в кэше: video_id={video_id}, quality={quality}, message_id={cached_message_id}")
            return cached_message_id
        
        # 1. Получаем LinkInfo через LinkProcessingService (мозг системы)
        logger.info(f"[worker] Получаю LinkInfo для URL: {url}")
        link_info = link_processor.process_link(url)
        if not link_info:
            logger.error(f"[worker] Не удалось обработать ссылку: {url}")
            # Публикуем событие об ошибке, чтобы пользователь получил уведомление
            await db.publish_video_download_event(video_id, 'failed')
            return None
        
        # 2. Получаем DownloadPlan через PlatformService
        logger.info(f"[worker] Получаю DownloadPlan для платформы: {link_info.platform}")
        download_plan = link_info.service.build_download_plan(
            url=link_info.normalized_url,
            quality=quality,
            format_id=format_id
        )
        if not download_plan:
            logger.error(f"[worker] Не удалось построить DownloadPlan для: {url}")
            logger.error(f"[worker] Возможные причины:")
            logger.error(f"  - Видео недоступно или удалено")
            logger.error(f"  - Видео приватное или требует авторизацию (Instagram/TikTok)")
            logger.error(f"  - Контент недоступен для определенной аудитории")
            logger.error(f"  - Проблемы с доступом к платформе")
            # Публикуем событие об ошибке, чтобы пользователь получил уведомление
            await db.publish_video_download_event(video_id, 'failed')
            return None
        
        logger.info(f"[worker] DownloadPlan создан: platform={download_plan.platform}, streamable={download_plan.streamable}")
        
        # 3. Исполняем DownloadPlan через YtDlpService
        # ВАЖНО: Всегда скачиваем напрямую в память (download_to_stream), не на диск
        logger.info(f"[worker] Исполняю DownloadPlan через YtDlpService (прямое скачивание в память)")
        result = ytdlp_service.download_to_stream(download_plan)
        
        # Если потоковое скачивание не сработало, пробуем через файл (fallback)
        if not result:
            logger.warning(f"[worker] Потоковое скачивание не удалось, пробую через файл (fallback)")
            result = ytdlp_service.download_to_file(download_plan)
        
        if not result:
            logger.error(f"[worker] ❌ Не удалось скачать видео: url={url}")
            logger.error(f"[worker] Возможные причины:")
            logger.error(f"  - Видео недоступно или удалено")
            logger.error(f"  - Видео приватное (Instagram/TikTok)")
            logger.error(f"  - Проблемы с сетью или доступом к платформе")
            logger.error(f"  - yt-dlp не может обработать этот тип контента")
            # Публикуем событие об ошибке, чтобы пользователь получил уведомление
            await db.publish_video_download_event(video_id, 'failed')
            return None
        
        video_data, file_size, filename = result
        file_size_mb = file_size / (1024 * 1024)
        logger.info(f"[worker] Размер файла: {file_size_mb:.2f} MB")
        
        # 4. Загружаем в Telegram канал
        # Для маленьких файлов (<50MB) - используем BufferedInputFile (из памяти)
        # Для больших файлов - используем FSInputFile (временный файл)
        try:
            if isinstance(video_data, io.BytesIO):
                # Маленький файл в памяти - используем BufferedInputFile
                logger.info(f"[worker] Загрузка в канал из памяти: {file_size_mb:.2f} MB")
                video_data.seek(0)  # Возвращаемся в начало потока
                message = await bot.send_video(
                    chat_id=CHANNEL_ID,
                    video=types.BufferedInputFile(
                        file=video_data.read(),
                        filename=filename
                    ),
                    caption=f"Source: {url}"
                )
            else:
                # Большой файл - используем FSInputFile (временный файл)
                logger.info(f"[worker] Загрузка в канал из файла: {video_data} ({file_size_mb:.2f} MB)")
                message = await bot.send_video(
                    chat_id=CHANNEL_ID,
                    video=types.FSInputFile(video_data),
                    caption=f"Source: {url}"
                )
            
            message_id = message.message_id
        finally:
            # Очищаем ресурсы
            if isinstance(video_data, io.BytesIO):
                video_data.close()
            elif isinstance(video_data, str) and os.path.exists(video_data):
                # Удаляем временный файл после отправки
                try:
                    os.remove(video_data)
                    logger.info(f"[worker] Временный файл удален: {video_data}")
                except Exception as e:
                    logger.warning(f"[worker] Не удалось удалить временный файл {video_data}: {e}")
        
        # 5. Получаем file_id из видео
        file_id = None
        if message.video:
            file_id = message.video.file_id
        elif message.document:
            file_id = message.document.file_id
        
        # 6. Сохраняем в кэш с указанием качества (если указано)
        platform = platform or download_plan.platform
        await db.save_to_cache(video_id, message_id, platform, file_id, original_url=url, quality=quality)
        
        logger.info(f"[worker] ✅ Видео успешно скачано и сохранено в кэш: video_id={video_id}, message_id={message_id}")
        
        # 7. Публикуем событие о завершении скачивания (для wait_for_download)
        await db.publish_video_download_event(video_id, 'completed', message_id, file_id)
        
        # Публикуем событие DownloadCompletedEvent в очередь аналитики
        # Примечание: в worker.py нет user_id, поэтому используем 0 (системный)
        # Реальное событие с user_id будет опубликовано в bot.py после отправки видео пользователю
        try:
            event = DownloadCompletedEvent(
                user_id=0,  # Системный user_id (worker не знает реального пользователя)
                video_id=video_id,
                platform=platform,
                source='worker'  # Специальный источник для событий от worker
            )
            await db.add_analytics_event(event.to_json())
        except Exception as e:
            logger.error(f"[worker] Ошибка при публикации события DownloadCompletedEvent: {e}")
        
        return message_id
        
    except Exception as e:
        logger.error(f"[worker] Ошибка при обработке задачи: {e}", exc_info=True)
        # Публикуем событие об ошибке
        await db.publish_video_download_event(video_id, 'failed')
        return None
    finally:
        # Освобождаем lock (с учетом качества)
        await db.release_download_lock(video_id, quality=quality)


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
