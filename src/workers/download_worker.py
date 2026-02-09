import os
import io
import asyncio
import logging
from typing import Optional
from dotenv import load_dotenv
from aiogram import Bot, types
from aiogram.client.session.aiohttp import AiohttpSession

from src.database.redis_db import Database
from src.events.events import DownloadCompletedEvent
from src.services.service_factory import ServiceFactory
from src.services.link_processing_service import LinkProcessingService
from src.services.ytdlp_service import YtDlpService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")
if not CHANNEL_ID:
    raise ValueError("TELEGRAM_CHANNEL_ID не установлен в переменных окружения")

try:
    CHANNEL_ID = int(CHANNEL_ID)
except ValueError:
    pass

session = AiohttpSession(timeout=600)
bot = Bot(token=BOT_TOKEN, session=session)
db = Database()
downloader = YtDlpService()
service_factory = ServiceFactory(downloader)
link_processor = LinkProcessingService(service_factory)
ytdlp_service = YtDlpService()

# TODO сделать рефактор функции на более читаемый код
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
    
    got_lock = await db.acquire_download_lock(video_id, quality=quality)
    
    if not got_lock:
        logger.info(f"[worker] Lock занят для video_id={video_id}, quality={quality}, пропускаем задачу (кто-то уже скачивает)")
        return None
    
    try:
        cached_message_id = await db.get_cached_message_id(video_id=video_id, quality=quality)
        if cached_message_id and cached_message_id != 0:
            logger.info(f"[worker] Видео уже в кэше: video_id={video_id}, quality={quality}, message_id={cached_message_id}")
            return cached_message_id
        
        logger.info(f"[worker] Получаю LinkInfo для URL: {url}")
        link_info = link_processor.process_link(url)
        if not link_info:
            logger.error(f"[worker] Не удалось обработать ссылку: {url}")
            await db.publish_video_download_event(video_id, 'failed')
            return None
        
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
            await db.publish_video_download_event(video_id, 'failed')
            return None
        
        logger.info(f"[worker] DownloadPlan создан: platform={download_plan.platform}, streamable={download_plan.streamable}")
        
        # Стратегия:
        # - Для маленьких файлов (<50MB): скачиваем в память (download_to_stream) - быстрее
        # - Для больших файлов: используем pipelined метод (download_to_file_pipelined) - оптимизированный subprocess
        # - Fallback: обычный download_to_file через yt-dlp Python API
        logger.info(f"[worker] Исполняю DownloadPlan через YtDlpService")
        
        is_shorts = getattr(download_plan, 'quality', None) == 'shorts'
        result = None
        if not is_shorts:
            result = ytdlp_service.download_to_stream(download_plan)
        if not result and not is_shorts:
            logger.info(f"[worker] Потоковое скачивание не удалось, пробую pipelined метод (оптимизированный subprocess)")
            result = await ytdlp_service.download_to_file_pipelined(download_plan)
        if not result:
            if is_shorts:
                logger.info(f"[worker] Shorts: качаю в файл через yt-dlp Python API (HLS)")
            else:
                logger.warning(f"[worker] Pipelined не удался, пробую yt-dlp Python API (fallback)")
            result = ytdlp_service.download_to_file(download_plan)
        
        if not result:
            logger.error(f"[worker] ❌ Не удалось скачать видео: url={url}")
            await db.publish_video_download_event(video_id, 'failed')
            return None
        
        video_data, file_size, filename = result
        file_size_mb = file_size / (1024 * 1024)
        logger.info(f"[worker] Размер файла: {file_size_mb:.2f} MB")
        
        audio_extensions = {'.m4a', '.webm', '.opus', '.mp3', '.ogg', '.flac', '.wav', '.m4b'}
        image_extensions = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
        
        if isinstance(video_data, str):
            file_ext = os.path.splitext(video_data)[1].lower()
        else:
            file_ext = os.path.splitext(filename)[1].lower()
        
        is_audio = download_plan.audio_only or file_ext in audio_extensions
        is_photo = (not is_audio) and file_ext in image_extensions
        
        # Тип отправки:
        # - audio: по флагу audio_only или аудио‑расширению файла
        # - photo: если итоговый файл — изображение (решение по факту работы yt-dlp)
        # - иначе: video (по плану или по умолчанию)
        if is_audio:
            send_media_type = 'audio'
        elif is_photo:
            send_media_type = 'photo'
        else:
            send_media_type = download_plan.media_type or 'video'
        
        if is_audio:
            logger.info(f"[worker] Определен тип: audio (расширение: {file_ext}, audio_only: {download_plan.audio_only})")
        elif is_photo:
            logger.info(f"[worker] Определен тип: photo (по расширению файла: {file_ext})")
        else:
            logger.info(f"[worker] Определен тип: video (расширение: {file_ext})")
        
        # Подпись и опции из плана (сервис формирует описание сообщения для Telegram)
        caption = getattr(download_plan, 'telegram_caption', None) or f"Source: {url}"
        parse_mode = getattr(download_plan, 'telegram_parse_mode', None)
        send_kw = dict(caption=caption)
        if parse_mode:
            send_kw['parse_mode'] = parse_mode

        # Сервис задал media_type (video/photo/audio) — отправляем в канал соответствующим методом
        # Для маленьких файлов (<50MB) — BufferedInputFile, для больших — FSInputFile
        try:
            if isinstance(video_data, io.BytesIO):
                logger.info(f"[worker] Загрузка в канал из памяти: {file_size_mb:.2f} MB (тип: {send_media_type})")
                video_data.seek(0)
                inp = types.BufferedInputFile(file=video_data.read(), filename=filename)
            else:
                logger.info(f"[worker] Загрузка в канал из файла: {video_data} ({file_size_mb:.2f} MB, тип: {send_media_type})")
                inp = types.FSInputFile(video_data)

            if send_media_type == 'audio':
                message = await bot.send_audio(chat_id=CHANNEL_ID, audio=inp, **send_kw)
            elif send_media_type == 'photo':
                message = await bot.send_photo(chat_id=CHANNEL_ID, photo=inp, **send_kw)
            else:
                message = await bot.send_video(chat_id=CHANNEL_ID, video=inp, **send_kw)
            
            message_id = message.message_id
        finally:
            if isinstance(video_data, io.BytesIO):
                video_data.close()
            elif isinstance(video_data, str) and os.path.exists(video_data):
                try:
                    os.remove(video_data)
                    logger.info(f"[worker] Временный файл удален: {video_data}")
                except Exception as e:
                    logger.warning(f"[worker] Не удалось удалить временный файл {video_data}: {e}")
        
        file_id = None
        if message.audio:
            file_id = message.audio.file_id
        elif message.video:
            file_id = message.video.file_id
        elif message.photo:
            file_id = message.photo[-1].file_id
        elif message.document:
            file_id = message.document.file_id
        
        platform = platform or download_plan.platform
        await db.save_to_cache(video_id, message_id, platform, file_id, original_url=url, quality=quality, media_type=send_media_type)
        
        logger.info(f"[worker] ✅ Видео успешно скачано и сохранено в кэш: video_id={video_id}, message_id={message_id}")
        
        await db.publish_video_download_event(video_id, 'completed', message_id, file_id)
        
        try:
            event = DownloadCompletedEvent(
                user_id=0,
                video_id=video_id,
                platform=platform,
                source='worker'
            )
            await db.add_analytics_event(event.to_json())
        except Exception as e:
            logger.error(f"[worker] Ошибка при публикации события DownloadCompletedEvent: {e}")
        
        return message_id
        
    except Exception as e:
        logger.error(f"[worker] Ошибка при обработке задачи: {e}", exc_info=True)
        await db.publish_video_download_event(video_id, 'failed')
        return None
    finally:
        await db.release_download_lock(video_id, quality=quality)


async def worker_loop():
    """
    Основной цикл worker'а - слушает очередь задач и обрабатывает их
    """
    logger.info("[worker] Background worker запущен")
    logger.info("[worker] Ожидание задач из очереди Redis...")
    
    while True:
        try:
            task = await db.get_download_task(timeout=5)
            
            if task:
                logger.info(f"[worker] Получена задача: video_id={task.get('video_id')}")
                await process_download_task(task)
            else:
                await asyncio.sleep(0.1)
                
        except KeyboardInterrupt:
            logger.info("[worker] Получен сигнал остановки (KeyboardInterrupt)")
            break
        except Exception as e:
            logger.error(f"[worker] Ошибка в worker_loop: {e}", exc_info=True)
            await asyncio.sleep(1)


async def main():
    """Главная функция worker'а"""
    try:
        await worker_loop()
    except KeyboardInterrupt:
        logger.info("[worker] Получен сигнал остановки")
    finally:
        await db.close()
        await bot.session.close()
        logger.info("[worker] Worker остановлен")


if __name__ == "__main__":
    asyncio.run(main())
