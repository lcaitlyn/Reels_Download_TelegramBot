"""
Основной модуль бота
"""
import os
import logging
import asyncio
from typing import Optional
from urllib.parse import quote, unquote
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineQuery, 
    InlineQueryResultArticle, 
    InlineQueryResultCachedVideo,
    InputTextMessageContent,
    InputMediaVideo,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.client.session.aiohttp import AiohttpSession

from database import Database
from utils import normalize_url, get_platform, is_supported_url, get_video_id_fast
from downloader import VideoDownloader

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Проверка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле! Создайте .env файл с BOT_TOKEN=ваш_токен")
if not CHANNEL_ID:
    raise ValueError(
        "TELEGRAM_CHANNEL_ID не найден в .env файле!\n"
        "Создайте .env файл с TELEGRAM_CHANNEL_ID=ваш_id_канала\n"
        "ID канала можно получить через @userinfobot или @RawDataBot"
    )

# Преобразуем CHANNEL_ID в int, если это число (для каналов это обычно строка с -100...)
try:
    CHANNEL_ID = int(CHANNEL_ID)
except ValueError:
    # Если не число, оставляем как строку (для username каналов типа @channel)
    pass

# Инициализация бота с увеличенным таймаутом для больших файлов (10 минут = 600 секунд)
# Используем числовое значение для совместимости с polling
session = AiohttpSession(timeout=600)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

db = Database()
downloader = VideoDownloader()

# Путь к фото для inline query
PHOTO_PATH = "test.png"


def get_cache_key(url: str) -> tuple[Optional[str], str]:
    """
    Получить ключ для кэша: пытается получить video_id, fallback на нормализованный URL
    Возвращает (video_id или None, normalized_url)
    """
    normalized_url = normalize_url(url)
    video_id = downloader.get_video_id(url)
    if video_id:
        return (video_id, normalized_url)
    return (None, normalized_url)


async def download_and_cache(url: str, user_id: int) -> Optional[int]:
    """
    Скачать видео, загрузить в канал, сохранить в кэш
    Использует lock для предотвращения одновременных скачиваний одного видео
    Возвращает message_id или None при ошибке
    """
    # Получаем канонический video_id через yt-dlp (предотвращает дубликаты)
    video_id = downloader.get_video_id(url)
    if not video_id:
        logger.warning(f"Не удалось получить video_id для {url}, использую URL как ключ")
        video_id = normalize_url(url)  # Fallback на нормализованный URL
    
    # Проверяем кэш - если видео уже скачано, возвращаем сразу
    cached_message_id = await db.get_cached_message_id(video_id=video_id)
    if cached_message_id and cached_message_id != 0:
        logger.info(f"Видео уже в кэше: video_id={video_id}, message_id={cached_message_id}")
        return cached_message_id
    
    # Пытаемся получить lock на скачивание
    got_lock = await db.acquire_download_lock(video_id)
    
    if not got_lock:
        # Lock не получен - кто-то уже скачивает, ждем
        logger.info(f"Lock занят для video_id={video_id}, ожидание завершения скачивания...")
        message_id = await db.wait_for_download(video_id)
        return message_id
    
    # Lock получен - мы первые, скачиваем
    logger.info(f"Lock получен для video_id={video_id}, начинаю скачивание: {url}")
    
    try:
        # Скачиваем видео
        video_path = downloader.download_video(url)
        if not video_path:
            await db.release_download_lock(video_id)
            return None
        
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        logger.info(f"Размер файла: {file_size_mb:.2f} MB")
        
        # Отправляем видео в канал с прогрессом
        logger.info(f"Начинаю загрузку в канал: {video_path}")
        message = await bot.send_video(
            chat_id=CHANNEL_ID,
            video=types.FSInputFile(video_path),
            #caption=f"Ссылка: {url}"
        )
        message_id = message.message_id
        
        # Получаем file_id из видео
        file_id = None
        if message.video:
            file_id = message.video.file_id
        elif message.document:
            file_id = message.document.file_id
        
        # Сохраняем в кэш используя video_id как ключ (предотвращает дубликаты)
        platform = get_platform(url)
        await db.save_to_cache(video_id, message_id, platform, file_id, original_url=url)
        
        logger.info(f"✅ Видео сохранено в кэш: video_id={video_id}, url={url} -> message_id={message_id}, file_id={file_id}")
        
        return message_id
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении в канал: {e}")
        return None
    finally:
        # Удаляем временный файл после отправки в канал
        try:
            if 'video_path' in locals() and video_path and os.path.exists(video_path):
                os.remove(video_path)
                logger.info(f"Временный файл удален: {video_path}")
        except Exception as e:
            logger.warning(f"Не удалось удалить файл {video_path}: {e}")
        
        # Освобождаем lock после завершения (успешного или с ошибкой)
        await db.release_download_lock(video_id)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start с поддержкой deep link"""
    # Логируем для отладки
    logger.info(f"[cmd_start] Вызван: message.text={message.text}, user={message.from_user.id if message.from_user else None}")
    
    # Проверяем, есть ли параметр после /start (deep link)
    # Параметры идут после /start, например: /start https://example.com
    args = message.text.split(maxsplit=1)[1:] if message.text else []
    args_str = args[0] if args else None
    
    logger.info(f"[cmd_start] args={args}, args_str={args_str}")
    
    if args_str:
        param = args_str.strip()
        logger.info(f"[cmd_start] Параметр deep link: {param}")
        
        # Параметр может быть:
        # 1. video_id в формате "platform_video_id" (короткий deep link с _, например "instagram_DQHEHA1CAyr")
        # 2. URL (старый формат, для обратной совместимости)
        
        url = None
        video_id = None
        
        # Проверяем, является ли параметр video_id (формат "platform_id" с подчеркиванием для deep link)
        if '_' in param and not param.startswith(('http://', 'https://')):
            # Это похоже на video_id из deep link (например, "instagram_DQHEHA1CAyr")
            # Заменяем _ на : для поиска в БД (в БД храним platform:video_id)
            video_id = param.replace('_', ':')
            logger.info(f"[cmd_start] Параметр deep link: {param} -> video_id для БД: {video_id}")
            
            # Пытаемся получить original_url из кэша по video_id
            url = await db.get_original_url_by_video_id(video_id)
            
            # Проверяем, есть ли видео в кэше (скачано ли оно)
            cached_message_id = await db.get_cached_message_id(video_id=video_id)
            
            if cached_message_id:
                # Видео есть в кэше - отправляем сразу
                try:
                    await bot.copy_message(
                        chat_id=message.chat.id,
                        from_chat_id=CHANNEL_ID,
                        message_id=cached_message_id
                    )
                    logger.info(f"✅ Видео отправлено из кэша через deep link (video_id): {video_id}")
                    return
                except Exception as e:
                    logger.error(f"❌ Ошибка при отправке из кэша: {e}")
            
            # Видео нет в кэше (еще не скачано или скачивается)
            if url:
                # URL найден - видео скачивается, отправляем ⏳ и ждем
                status_msg = await message.answer("⏳")
                # Запускаем скачивание и ждем завершения
                await download_and_send(url, message.chat.id, status_msg=status_msg)
                return
            else:
                # URL не найден - это ошибка, видео должно было быть сохранено при inline-запросе
                await message.answer("❌ Видео не найдено. Попробуй снова через inline-запрос @botname")
                return
        else:
            # Это URL (старый формат или закодированный URL)
            url = unquote(param)
            logger.info(f"[cmd_start] Параметр является URL: {url}")
            
            # Проверяем, поддерживается ли платформа
            normalized_url = normalize_url(url)
            if not is_supported_url(normalized_url):
                await message.answer(
                    "❌ Неподдерживаемая платформа.\n"
                    "Поддерживаются: YouTube, Instagram, TikTok"
                )
                return
            
            # Получаем video_id для проверки кэша
            video_id, normalized_url = get_cache_key(url)
            url = normalized_url
        
        logger.info(f"[cmd_start] Deep link: url={url}, video_id={video_id}, user={message.from_user.id}")
        
        # Проверяем кэш (пытаемся получить video_id, проверяем по обоим ключам)
        cached_message_id = await db.get_cached_message_id(video_id=video_id, url=url)
        
        if cached_message_id:
            # Видео есть в кэше - отправляем сразу
            try:
                await bot.copy_message(
                    chat_id=message.chat.id,
                    from_chat_id=CHANNEL_ID,
                    message_id=cached_message_id
                )
                logger.info(f"✅ Видео отправлено из кэша через deep link: {url}")
                return
            except Exception as e:
                logger.error(f"❌ Ошибка при отправке из кэша: {e}")
        
        # Видео нет в кэше - скачиваем
        status_msg = await message.answer("⏳")
        await download_and_send(url, message.chat.id, status_msg=status_msg)
    else:
        # Обычная команда /start без параметров
        await message.answer(
            "👋 Привет! Отправь мне ссылку на видео из:\n"
            "• YouTube / YouTube Shorts\n"
            "• Instagram Reels / Posts\n"
            "• TikTok\n\n"
            "Или используй @botname в любом чате для быстрого доступа!"
        )


@dp.message(F.text)
async def handle_message(message: types.Message):
    """Обработка текстовых сообщений со ссылками"""
    logger.info(f"[handle_message] Вызван: text={message.text[:50] if message.text else None}..., chat_id={message.chat.id}, from_user={message.from_user.id if message.from_user else None}, via_bot={message.via_bot.id if message.via_bot else None}")
    
    text = message.text.strip()
    
    # Проверяем, пришло ли сообщение через inline query и это не URL
    is_inline_query_result = message.via_bot and message.via_bot.id == bot.id
    is_url = text.startswith(('http://', 'https://'))
    
    # Если это inline query результат и не URL - отправляем фото с текстом
    if is_inline_query_result and not is_url:
        try:
            # Удаляем текстовое сообщение
            try:
                await message.delete()
            except:
                pass
            
            # Отправляем фото с подписью
            if os.path.exists(PHOTO_PATH):
                await bot.send_photo(
                    chat_id=message.chat.id,
                    photo=types.FSInputFile(PHOTO_PATH),
                    caption=f"<b>{text}</b>",
                    parse_mode="HTML"
                )
            else:
                # Если фото не найдено - отправляем только текст
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=f"<b>{text}</b>",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
        return
    
    # Остальная логика для URL
    url = text
    
    # Проверяем, является ли это URL
    if not is_url:
        await message.answer("❌ Пожалуйста, отправь корректную ссылку на видео.")
        return
    
    # Нормализуем URL (БЕЗ вызова get_video_id для скорости)
    normalized_url = normalize_url(url)
    
    # Проверяем поддержку платформы
    if not is_supported_url(normalized_url):
        await message.answer(
            "❌ Неподдерживаемая платформа.\n"
            "Поддерживаются: YouTube, Instagram, TikTok"
        )
        return
    
    # Сначала проверяем кэш по URL напрямую (БЫСТРО, без yt-dlp extractor)
    cached_message_id = await db.get_cached_message_id(url=normalized_url)
    
    # Если не нашли по URL, пытаемся получить video_id и проверить по нему
    if not cached_message_id:
        # Сначала пытаемся получить video_id быстрым способом (без HTTP-запросов)
        video_id, _ = get_video_id_fast(normalized_url)
        if video_id:
            cached_message_id = await db.get_cached_message_id(video_id=video_id)
        # Если быстрый способ не сработал (например, для TikTok), используем yt-dlp (МЕДЛЕННО)
        if not cached_message_id:
            video_id = downloader.get_video_id(normalized_url)
            if video_id:
                cached_message_id = await db.get_cached_message_id(video_id=video_id)
    
    if cached_message_id:
        # Копируем из кэша (без пометки "Переслано из...")
        try:
            # Удаляем сообщение со ссылкой ПЕРЕД отправкой видео (если это inline-результат)
            is_inline = message.via_bot and message.via_bot.id == bot.id
            if is_inline:
                try:
                    await message.delete()
                    logger.info(f"Сообщение со ссылкой удалено перед отправкой видео")
                except Exception as e:
                    logger.warning(f"Не удалось удалить сообщение со ссылкой: {e}")
            
            # Отправляем видео в чат
            logger.info(f"Отправляю видео из кэша в chat_id={message.chat.id}, message_id={cached_message_id}")
            result = await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=CHANNEL_ID,
                message_id=cached_message_id
            )
            logger.info(f"✅ Видео успешно скопировано из кэша в chat_id={message.chat.id}, result_message_id={result.message_id}: {normalized_url}")
        except Exception as e:
            logger.error(f"❌ Ошибка при пересылке из кэша: {e}", exc_info=True)
            # Если пересылка не удалась, скачиваем заново
            status_msg = await message.answer("⏳")
            await download_and_send(normalized_url, message.chat.id, status_msg=status_msg)
    else:
        # Скачиваем новое видео - сначала удаляем сообщение со ссылкой
        if message.via_bot and message.via_bot.id == bot.id:
            try:
                await message.delete()
            except:
                pass
        
        status_msg = await message.answer("⏳")
        await download_and_send(normalized_url, message.chat.id, status_msg=status_msg)


async def background_download(url: str, video_id: str):
    """Фоновое скачивание видео без отправки пользователю (для кэширования)"""
    try:
        logger.info(f"[background_download] Начало фонового скачивания: {url} (video_id: {video_id})")
        message_id = await download_and_cache(url, 0)  # user_id = 0 для фоновых задач
        if message_id:
            logger.info(f"[background_download] ✅ Видео успешно скачано и сохранено в кэш: {url} (video_id: {video_id})")
        else:
            logger.warning(f"[background_download] ❌ Не удалось скачать видео: {url} (video_id: {video_id})")
    except Exception as e:
        logger.error(f"[background_download] ❌ Ошибка при фоновом скачивании: {url} (video_id: {video_id}): {e}", exc_info=True)


async def download_and_send(url: str, chat_id: int, status_msg: types.Message = None):
    """
    Добавить задачу на скачивание в очередь для background worker
    Ожидает завершения скачивания и отправляет видео пользователю
    
    Args:
        url: URL видео для скачивания
        chat_id: ID чата для отправки видео
        status_msg: Сообщение со статусом "⏳" для удаления после скачивания
    """
    try:
        # Получаем video_id для проверки кэша и добавления задачи
        video_id, normalized_url = get_video_id_fast(url)
        if not video_id:
            # Если быстрый способ не сработал (например, TikTok), используем yt-dlp
            video_id = downloader.get_video_id(url)
        
        if not video_id:
            video_id = normalize_url(url)  # Fallback
        
        platform = get_platform(url)
        
        # Проверяем кэш - если видео уже скачано, отправляем сразу
        cached_message_id = await db.get_cached_message_id(video_id=video_id, url=normalized_url)
        
        if cached_message_id and cached_message_id != 0:
            # Видео уже в кэше - отправляем сразу
            # Удаляем сообщение со статусом перед отправкой видео
            if status_msg:
                try:
                    await status_msg.delete()
                except:
                    pass
            
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=CHANNEL_ID,
                message_id=cached_message_id
            )
            logger.info("Видео скопировано из кэша")
            return
        
        # Видео нет в кэше - добавляем задачу в очередь для background worker
        task_added = await db.add_download_task(url, video_id, platform)
        
        if task_added:
            # Задача добавлена в очередь - ждем завершения скачивания
            logger.info(f"Задача добавлена в очередь для video_id={video_id}, ожидание завершения...")
            message_id = await db.wait_for_download(video_id, timeout=1800.0)  # 30 минут timeout
            
            if message_id:
                # Видео скачано - удаляем сообщение со статусом и отправляем видео
                if status_msg:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=CHANNEL_ID,
                    message_id=message_id
                )
                logger.info("Видео скопировано из кэша после обработки worker'ом")
            else:
                # Timeout - видео не скачалось
                if status_msg:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                await bot.send_message(chat_id, "❌ Не удалось скачать видео за отведенное время. Попробуй позже.")
        else:
            # Задача не добавлена (уже в очереди или кэше) - ждем завершения
            logger.info(f"Задача уже обрабатывается для video_id={video_id}, ожидание...")
            message_id = await db.wait_for_download(video_id, timeout=1800.0)
            
            if message_id:
                # Видео скачано - удаляем сообщение со статусом и отправляем видео
                if status_msg:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=CHANNEL_ID,
                    message_id=message_id
                )
                logger.info("Видео скопировано из кэша после ожидания")
            else:
                # Timeout - видео не скачалось
                if status_msg:
                    try:
                        await status_msg.delete()
                    except:
                        pass
                await bot.send_message(chat_id, "❌ Не удалось скачать видео за отведенное время. Попробуй позже.")
                
    except Exception as e:
        logger.error(f"Ошибка при отправке видео: {e}", exc_info=True)
        await bot.send_message(chat_id, "❌ Произошла ошибка при отправке видео. Файл слишком большой или проблема с интернетом.")


@dp.inline_query()
async def inline_handler(inline_query: InlineQuery):
    """Обработка inline-запросов (@botname)"""
    logger.info(f"[inline_handler] Вызван: query={inline_query.query[:50] if inline_query.query else None}, user={inline_query.from_user.id}")
    query = inline_query.query.strip()
    results = []
    
    # Если запрос пустой - показываем подсказку
    if not query:
        results.append(
            InlineQueryResultArticle(
                id="help",
                title="💡 Как использовать бота?",
                description="Отправь ссылку на видео из YouTube/Instagram/TikTok",
                input_message_content=InputTextMessageContent(
                    message_text="Отправь ссылку на видео боту для скачивания!"
                )
            )
        )
    # Если запрос похож на URL
    elif query.startswith(('http://', 'https://')):
        # Нормализуем URL
        normalized_url = normalize_url(query)
        
        # Проверяем, поддерживается ли платформа
        if not is_supported_url(normalized_url):
            results.append(
                InlineQueryResultArticle(
                    id="unsupported",
                    title="❌ Неподдерживаемая платформа",
                    description="Поддерживаются: YouTube, Instagram, TikTok",
                    input_message_content=InputTextMessageContent(
                        message_text=query
                    )
                )
            )
        else:
            # Проверяем кэш (БЫСТРО, без yt-dlp extractor)
            platform = get_platform(normalized_url)
            # Сначала пытаемся получить video_id быстрым способом (без HTTP-запросов)
            video_id, normalized_url = get_video_id_fast(query)
            cached_file_id = await db.get_cached_file_id(video_id=video_id, url=normalized_url)
            
            if cached_file_id:
                # Видео найдено в кэше - используем InlineQueryResultCachedVideo
                results.append(
                    InlineQueryResultCachedVideo(
                        id=f"cached_{abs(hash(normalized_url))}",
                        video_file_id=cached_file_id,
                        title=f"✅ Видео из кэша ({platform})",
                        description=normalized_url
                    )
                )
            else:
                # Видео нет в кэше - отправляем ссылку на видео + кнопку с deep link
                # Получаем username бота для deep link (кэшируем для скорости)
                if not hasattr(bot, '_cached_username'):
                    bot_info = await bot.get_me()
                    bot._cached_username = bot_info.username
                bot_username = bot._cached_username
                
                # Используем video_id в deep link (короткий формат с _, работает в Telegram)
                # Если video_id не получен быстрым способом (например, TikTok) - используем yt-dlp (МЕДЛЕННО)
                # Но только если видео не в кэше (иначе не нужно)
                if not video_id:
                    # Для TikTok может потребоваться yt-dlp, но это медленно - делаем только если критично
                    # Можно отложить до момента, когда пользователь нажмет кнопку
                    video_id = None  # Не получаем через yt-dlp здесь для скорости
                
                if video_id:
                    # video_id в БД хранится в формате "platform:video_id" (с :)
                    # Для deep link заменяем : на _ (Telegram не поддерживает : в параметрах)
                    video_id_for_deeplink = video_id.replace(':', '_')
                    
                    # Сохраняем URL в кэш для маппинга video_id -> url (до скачивания)
                    # Это позволит найти URL в /start по video_id
                    # В БД храним в формате platform:video_id
                    await db.save_url_mapping(video_id, normalized_url, platform)
                    logger.info(f"[inline_handler] Сохранен маппинг video_id -> URL: {video_id} -> {normalized_url}")
                    
                    # Запускаем фоновое скачивание видео
                    asyncio.create_task(background_download(normalized_url, video_id))
                    logger.info(f"[inline_handler] Запущено фоновое скачивание видео: {normalized_url}")
                    
                    # Используем короткий video_id в deep link (формат platform_video_id с _ для Telegram)
                    deep_link = f"https://t.me/{bot_username}?start={video_id_for_deeplink}"
                    logger.info(f"[inline_handler] Deep link с video_id (deep link): {video_id_for_deeplink}, БД: {video_id}")
                else:
                    # Fallback: используем URL (может не работать из-за лимита длины)
                    encoded_url = quote(normalized_url, safe='')
                    deep_link = f"https://t.me/{bot_username}?start={encoded_url}"
                    logger.warning(f"[inline_handler] Используется fallback с URL в deep link (video_id не получен)")
                
                result_id = f"link_{abs(hash(normalized_url))}"
                results.append(
                    InlineQueryResultArticle(
                        id=result_id,
                        title=f"🔗 Ссылка на видео ({platform})",
                        description="Нажмите кнопку чтобы скачать видео",
                        input_message_content=InputTextMessageContent(
                            message_text=normalized_url
                        ),
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="👀 Посмотреть видео",
                                        url=deep_link
                                    )
                                ]
                            ]
                        )
                    )
                )
    else:
        # Если запрос не URL - показываем кнопку для отправки текста
        # При нажатии будет отправлено текстовое сообщение, которое обработает handle_message
        query_id = f"text_{abs(hash(query))}"
        results.append(
            InlineQueryResultArticle(
                id=query_id,
                title=f"📝 Отправить: {query[:50]}",
                description="Нажмите чтобы отправить фото с текстом",
                input_message_content=InputTextMessageContent(
                    message_text=query  # Отправляем просто текст, без разметки
                )
            )
        )
    
    logger.info(f"[inline_handler] Отвечаю на inline-запрос: {len(results)} результатов")
    await inline_query.answer(results, cache_time=0)  # Кэш отключен для отладки


@dp.callback_query(F.data.startswith("download:"))
async def callback_download_handler(callback: CallbackQuery):
    """Обработка нажатия кнопки 'Скачать и отправить'"""
    logger.info(f"[callback_download_handler] Вызван: callback_data={callback.data}, chat_id={callback.message.chat.id if callback.message else None}")
    
    # Отвечаем на callback_query (обязательно)
    await callback.answer("⏳ Скачиваю видео, подожди...")
    
    # Извлекаем URL из callback_data
    url = callback.data.split(":", 1)[1]
    normalized_url = normalize_url(url)
    
    # Получаем chat_id и message_id из сообщения
    if not callback.message:
        await bot.send_message(callback.from_user.id, "❌ Ошибка: сообщение не найдено.")
        return
    
    chat_id = callback.message.chat.id
    message_id = callback.message.message_id
    
    try:
        # Сохраняем в кэш (file_id сохраняется автоматически в download_and_cache)
        # download_and_cache использует lock, поэтому не будет дублирования скачиваний
        cached_message_id = await download_and_cache(normalized_url, callback.from_user.id)
        if not cached_message_id:
            await callback.message.edit_text("❌ Ошибка при сохранении в кэш.")
            return
        
        # Получаем file_id из кэша (должен быть сохранен в download_and_cache)
        video_id, normalized_url = get_cache_key(url)
        cached_file_id = await db.get_cached_file_id(video_id=video_id, url=normalized_url)
        if not cached_file_id:
            logger.warning(f"file_id не найден в кэше для {normalized_url}, возможно видео было отправлено как document")
            await callback.message.edit_text("❌ Ошибка: file_id не найден в кэше. Видео может быть слишком большим.")
            return
        
        # Редактируем сообщение: заменяем текст на видео с кнопкой "Отправить еще раз"
        await callback.message.edit_media(
            media=InputMediaVideo(
                media=cached_file_id,
                caption=f"Источник: {normalized_url}"
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="📤 Отправить еще раз",
                            callback_data=f"resend:{normalized_url}"
                        )
                    ]
                ]
            )
        )
        
        logger.info(f"✅ Видео успешно скачано и сообщение обновлено в chat_id={chat_id}, message_id={message_id}: {normalized_url}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при скачивании/отправке видео: {e}", exc_info=True)
        try:
            await callback.message.edit_text("❌ Произошла ошибка при скачивании видео. Попробуй позже.")
        except:
            await bot.send_message(chat_id, "❌ Произошла ошибка при скачивании видео. Попробуй позже.")


@dp.callback_query(F.data.startswith("resend:"))
async def callback_resend_handler(callback: CallbackQuery):
    """Обработка нажатия кнопки 'Отправить еще раз'"""
    logger.info(f"[callback_resend_handler] Вызван: callback_data={callback.data}, chat_id={callback.message.chat.id if callback.message else None}")
    
    # Отвечаем на callback_query (обязательно)
    await callback.answer("📤 Отправляю видео...")
    
    # Извлекаем URL из callback_data
    url = callback.data.split(":", 1)[1]
    normalized_url = normalize_url(url)
    
    # Получаем chat_id
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    
    try:
        # Сначала проверяем кэш по URL напрямую (БЫСТРО, без yt-dlp extractor)
        cached_message_id = await db.get_cached_message_id(url=normalized_url)
        
        # Если не нашли по URL, пытаемся получить video_id и проверить по нему
        if not cached_message_id:
            # Сначала пытаемся получить video_id быстрым способом (без HTTP-запросов)
            video_id, _ = get_video_id_fast(normalized_url)
            if video_id:
                cached_message_id = await db.get_cached_message_id(video_id=video_id)
            # Если быстрый способ не сработал (например, для TikTok), используем yt-dlp (МЕДЛЕННО)
            if not cached_message_id:
                video_id = downloader.get_video_id(normalized_url)
                if video_id:
                    cached_message_id = await db.get_cached_message_id(video_id=video_id)
        
        if not cached_message_id:
            await bot.send_message(chat_id, "❌ Видео не найдено в кэше.")
            return
        
        # Отправляем видео из кэша (новое сообщение)
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=CHANNEL_ID,
            message_id=cached_message_id
        )
        
        logger.info(f"✅ Видео успешно отправлено еще раз в chat_id={chat_id}: {normalized_url}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке видео из кэша: {e}", exc_info=True)
        await bot.send_message(chat_id, "❌ Произошла ошибка при отправке видео. Попробуй позже.")


@dp.chosen_inline_result()
async def chosen_inline_handler(chosen: types.ChosenInlineResult):
    """Обработка выбора inline-результата (для логирования)"""
    logger.info(f"[chosen_inline_result] Выбран результат: result_id={chosen.result_id}, query={chosen.query}, user={chosen.from_user.id}")


async def run_bot():
    """Запуск бота"""
    logger.info("Бот запущен!")
    logger.info("Ожидаю обновления...")
    await dp.start_polling(bot)
