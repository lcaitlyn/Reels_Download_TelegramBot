"""
Основной модуль бота - Event Router + UI Adapter
"""
import os
import logging
from typing import Optional
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InputTextMessageContent,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest

from src.database.redis_db import Database
from src.downloader.downloader import VideoDownloader
from src.downloader.download_manager import DownloadManager
from src.utils.utils import normalize_url, is_supported_url, is_youtube_video, get_video_id_fast, get_platform
from src.services import LinkProcessingService
from src.services.service_factory import ServiceFactory
from src.models.download_response import DownloadResponse
from src.use_cases import (
    HandleInlineQueryUseCase,
    HandleStartUseCase,
    GetStatsUseCase
)

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Проверка переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле! Создайте .env файл с BOT_TOKEN=ваш_токен")
if not CHANNEL_ID:
    raise ValueError(
        "TELEGRAM_CHANNEL_ID не найден в .env файле!\n"
        "Создайте .env файл с TELEGRAM_CHANNEL_ID=ваш_id_канала\n"
        "ID канала можно получить через @userinfobot или @RawDataBot"
    )
if not BOT_USERNAME:
    raise ValueError("BOT_USERNAME не найден в .env файле!")

# Преобразуем CHANNEL_ID в int, если это число
try:
    CHANNEL_ID = int(CHANNEL_ID)
except ValueError:
    pass

# Инициализация бота с увеличенным таймаутом для больших файлов
session = AiohttpSession(timeout=600)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# Инициализация зависимостей
db = Database()
downloader = VideoDownloader()  # Для обратной совместимости (будет удален)

# Инициализация сервисов
service_factory = ServiceFactory(downloader)
link_processing_service = LinkProcessingService(service_factory)
download_manager = DownloadManager(db, link_processing_service)

# Инициализация use-cases (для специфичных задач)
handle_inline_query_use_case = HandleInlineQueryUseCase(db, downloader)
handle_start_use_case = HandleStartUseCase(db, downloader)
get_stats_use_case = GetStatsUseCase(db)


# ========== UI Adapter Methods ==========

async def send_wait_message(chat_id: int) -> types.Message:
    """Отправить сообщение '⏳ Ожидайте'"""
    return await bot.send_message(chat_id, "⏳")


async def edit_or_delete_wait_message(message: Optional[types.Message]):
    """Удалить сообщение со статусом"""
    if message:
        try:
            await message.delete()
        except:
            pass


async def send_video(chat_id: int, message_id: int) -> bool:
    """
    Отправить видео из канала пользователю
    
    Returns:
        True если успешно, False при ошибке
    """
    try:
        await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=CHANNEL_ID,
            message_id=message_id
        )
        return True
    except TelegramBadRequest as e:
        error_message = str(e).lower()
        if "message not found" in error_message or "message to copy not found" in error_message:
            logger.warning(f"⚠️ Видео не найдено в канале (message_id={message_id})")
            return False
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке видео: {e}", exc_info=True)
        return False


async def send_error(chat_id: int, reason: str):
    """Отправить сообщение об ошибке"""
    error_messages = {
        'unsupported_platform': "❌ Неподдерживаемая платформа.\nПоддерживаются: YouTube, Instagram, TikTok",
        'video_not_found': f"❌ Видео не найдено. Попробуй снова через inline-запрос {BOT_USERNAME}",
        'download_failed': "❌ Не удалось скачать видео.\n\nВозможные причины:\n• Видео недоступно или удалено\n• Видео приватное или требует авторизацию\n• Контент недоступен для скачивания\n• Проблемы с доступом к платформе\n\nПопробуй позже или проверь ссылку.",
        'service_unavailable': "❌ Сервис временно недоступен. Попробуй позже.",
        'generic': "❌ Произошла ошибка при отправке видео. Файл слишком большой или проблема с интернетом."
    }
    
    message = error_messages.get(reason, error_messages['generic'])
    await bot.send_message(chat_id, message)


async def send_cached_video_from_result(chat_id: int, cached_result: dict) -> bool:
    """
    Отправить видео из кэша по результату use-case
    
    Returns:
        True если успешно, False при ошибке (включая невалидный message_id)
    """
    message_id = cached_result.get('message_id')
    if not message_id:
        return False
    
    success = await send_video(chat_id, message_id)
    
    if not success:
        # Удаляем невалидную запись из кэша
        try:
            video_id = cached_result.get('video_id')
            url = cached_result.get('url')
            await db.delete_from_cache(video_id=video_id, url=url)
        except Exception as e:
            logger.error(f"Ошибка при удалении из кэша: {e}")
    
    return success


# ========== Event Handlers ==========

@dp.message(Command("stats"))
async def stats_handler(message: types.Message):
    """Обработка команды /stats"""
    user_id = message.from_user.id if message.from_user else message.chat.id
    
    try:
        stats = await get_stats_use_case.execute(user_id)
        
        if stats.get('error'):
            await send_error(message.chat.id, 'generic')
            return
        
        stats_text = (
            f"📊 <b>Твоя статистика</b>\n\n"
            f"📥 Всего скачано: <b>{stats['downloads_total']}</b>\n"
            f"📅 Сегодня: <b>{stats['downloads_today']}</b>\n"
            f"📆 Этот месяц: <b>{stats['downloads_month']}</b>\n\n"
        )
        
        await message.answer(stats_text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}", exc_info=True)
        await send_error(message.chat.id, 'generic')


@dp.message(Command("start"))
async def start_handler(message: types.Message):
    """Обработка команды /start"""
    user_id = message.from_user.id if message.from_user else message.chat.id
    message_text = message.text or ""
    
    try:
        result = await handle_start_use_case.execute(message_text, user_id)
        
        if result['type'] == 'welcome':
            await message.answer(
                "👋 Привет! Отправь мне ссылку на видео из:\n"
                "• YouTube / YouTube Shorts\n"
                "• Instagram Reels / Posts\n"
                "• TikTok\n\n"
                f"Или используй {BOT_USERNAME} в любом чате для быстрого доступа!"
            )
        elif result['type'] == 'deep_link':
            # Проверяем кэш
            if result.get('cached_message_id'):
                success = await send_video(message.chat.id, result['cached_message_id'])
                if not success:
                    # Видео не найдено в канале - скачиваем заново
                    status_msg = await send_wait_message(message.chat.id)
                    await process_video_download(
                        result['url'],
                        message.chat.id,
                        status_msg,
                        user_id,
                        'deep_link'
                    )
            else:
                # Видео нет в кэше - скачиваем
                status_msg = await send_wait_message(message.chat.id)
                await process_video_download(
                    result['url'],
                    message.chat.id,
                    status_msg,
                    user_id,
                    'deep_link'
                )
        elif result['type'] == 'error':
            if result.get('error') == 'unsupported_platform':
                await send_error(message.chat.id, 'unsupported_platform')
            elif result.get('error') == 'video_not_found':
                await send_error(message.chat.id, 'video_not_found')
    except Exception as e:
        logger.error(f"Ошибка при обработке /start: {e}", exc_info=True)
        await send_error(message.chat.id, 'generic')


@dp.message(F.text)
async def message_handler(message: types.Message):
    """Обработка текстовых сообщений"""
    text = message.text.strip()
    
    # Проверяем, пришло ли сообщение через inline query и это не URL
    is_inline_query_result = message.via_bot and message.via_bot.id == bot.id
    
    # TODO сделать логику чтобы работало и без http://, 
    # типо если пришлют ссылку в формате instagram.com/reels/...

    is_url = text.startswith(('http://', 'https://'))
    
    # Остальная логика для URL
    if not is_url:
        await message.answer("❌ Пожалуйста, отправь корректную ссылку на видео.")
        return
    
    normalized_url = normalize_url(text)
    
    # Проверяем поддержку платформы
    if not is_supported_url(normalized_url):
        await send_error(message.chat.id, 'unsupported_platform')
        return
    
    # Для YouTube видео (не Shorts) - показываем выбор качества
    if is_youtube_video(normalized_url):
        video_id, _ = get_video_id_fast(normalized_url)
        if not video_id:
            video_id = downloader.get_video_id(normalized_url)
        if not video_id:
            video_id = normalized_url
        
        # Получаем доступные форматы через YouTubeService
        youtube_service = service_factory.get_service('youtube')
        formats = youtube_service.get_available_formats(normalized_url) if youtube_service else None
        
        # Формируем quality_info для совместимости
        quality_info = None
        if formats:
            # Проверяем, какие качества есть в кэше
            cached_qualities = []
            for quality_label in ['480p', '720p', '1080p', 'audio']:
                if quality_label in formats:
                    try:
                        cached = await db.check_quality_in_cache(video_id, quality_label)
                        if cached:
                            cached_qualities.append(quality_label)
                    except Exception as e:
                        logger.error(f"Ошибка при проверке качества в кэше: {e}")
            
            quality_info = {
                'formats': formats,
                'cached_qualities': cached_qualities
            }
        
        if quality_info and quality_info.get('formats'):
            # Сохраняем URL в маппинг
            await db.save_url_mapping(video_id, normalized_url, 'youtube')
            
            # Создаем кнопки с вариантами качества
            keyboard_buttons = []
            row = []
            
            for quality_label in ['480p', '720p', '1080p', 'audio']:
                if quality_label in quality_info['formats']:
                    cached = quality_label in quality_info['cached_qualities']
                    icon = "⚡️" if cached else "⏳"
                    
                    callback_data = f"quality:{video_id}:{quality_label}"
                    
                    row.append(
                        InlineKeyboardButton(
                            text=f"{icon} {quality_label}",
                            callback_data=callback_data
                        )
                    )
                    
                    if len(row) == 2:
                        keyboard_buttons.append(row)
                        row = []
            
            if row:
                keyboard_buttons.append(row)
            
            if keyboard_buttons:
                # Удаляем сообщение со ссылкой (если это inline-результат)
                if is_inline_query_result:
                    try:
                        await message.delete()
                    except:
                        pass
                
                await message.answer(
                    "📹 Выбери качество видео:",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
                )
                return
    
    # Обычная обработка для не-YouTube видео
    # Удаляем сообщение со ссылкой (если это inline-результат)
    if is_inline_query_result:
        try:
            await message.delete()
        except:
            pass
    
    status_msg = await send_wait_message(message.chat.id)
    user_id = message.from_user.id if message.from_user else message.chat.id
    source = 'inline' if is_inline_query_result else 'message'
    await process_video_download(normalized_url, message.chat.id, status_msg, user_id, source)


@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    """Обработка inline-запросов"""
    query = inline_query.query.strip()
    results = []
    
    try:
        result = await handle_inline_query_use_case.execute(query, bot)
        
        if result['type'] == 'help':
            results.append(
                InlineQueryResultArticle(
                    id="help",
                    title="💡 Как использовать бота?",
                    description="Вставь ссылку на видео из YouTube/Instagram/TikTok",
                    input_message_content=InputTextMessageContent(
                        message_text="Вставь ссылку на видео боту для скачивания!"
                    )
                )
            )
        elif result['type'] == 'unsupported':
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
        elif result['type'] == 'cached':
            for item in result['results']:
                if item['type'] == 'cached_video':
                    results.append(
                        InlineQueryResultCachedVideo(
                            id=f"cached_{abs(hash(query))}",
                            video_file_id=item['file_id'],
                            title=item['title'],
                            description=item['description']
                        )
                    )
        elif result['type'] == 'link':
            for item in result['results']:
                if item['type'] == 'article':
                    results.append(
                        InlineQueryResultArticle(
                            id=f"link_{abs(hash(query))}",
                            title=item['title'],
                            description=item['description'],
                            input_message_content=InputTextMessageContent(
                                message_text=item['message_text']
                            ),
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text="👀 Посмотреть видео",
                                            url=item['deep_link']
                                        )
                                    ]
                                ]
                            )
                        )
                    )
        elif result['type'] == 'text':
            results.append(
                InlineQueryResultArticle(
                    id=f"text_{abs(hash(query))}",
                    title=f"📝 Отправить: {query[:50]}",
                    description="Нажмите чтобы отправить фото с текстом",
                    input_message_content=InputTextMessageContent(
                        message_text=query
                    )
                )
            )
    except Exception as e:
        logger.error(f"Ошибка при обработке inline query: {e}", exc_info=True)
    
    await inline_query.answer(results, cache_time=0)


@dp.callback_query(F.data.startswith("quality:"))
async def callback_quality_handler(callback: CallbackQuery):
    """Обработка выбора качества для YouTube видео"""
    # Формат: quality:video_id:quality_label
    if not callback.data.startswith("quality:"):
        await callback.answer("❌ Ошибка в данных")
        return
    
    data_without_prefix = callback.data[8:]
    last_colon_index = data_without_prefix.rfind(":")
    
    if last_colon_index == -1:
        await callback.answer("❌ Ошибка в данных")
        return
    
    video_id = data_without_prefix[:last_colon_index]
    quality_label = data_without_prefix[last_colon_index + 1:]
    
    # Получаем URL из маппинга
    normalized_url = await db.get_original_url_by_video_id(video_id)
    
    if not normalized_url:
        # Пытаемся восстановить из video_id
        if video_id.startswith("youtube:"):
            video_id_only = video_id.split(":", 1)[1]
            normalized_url = f"https://www.youtube.com/watch?v={video_id_only}"
            await db.save_url_mapping(video_id, normalized_url, 'youtube')
        else:
            await callback.answer("❌ URL не найден. Попробуй отправить ссылку заново.")
            return
    
    await callback.answer(f"⏳ Скачиваю {quality_label}...")
    
    # Получаем информацию о формате через YouTubeService
    youtube_service = service_factory.get_service('youtube')
    if not youtube_service:
        await callback.message.edit_text("❌ Ошибка: сервис YouTube недоступен")
        return
    
    formats = youtube_service.get_available_formats(normalized_url)
    if not formats or quality_label not in formats:
        await callback.message.edit_text("❌ Выбранное качество недоступно")
        return
    
    format_info = formats[quality_label]
    format_id = format_info.get('format_id')
    
    # Проверяем кэш для этого качества
    cached_message_id = await db.get_cached_message_id(video_id=video_id, quality=quality_label)
    cached_file_id = None
    if cached_message_id and cached_message_id != 0:
        cached_file_id = await db.get_cached_file_id(video_id=video_id, quality=quality_label)
    
    if cached_message_id and cached_message_id != 0:
        # Видео уже в кэше
        try:
            await callback.message.delete()
        except:
            pass
        
        success = await send_video(callback.message.chat.id, cached_message_id)
        if success:
            # Публикуем событие
            try:
                from src.events.events import DownloadCompletedEvent
                event = DownloadCompletedEvent(
                    user_id=callback.from_user.id,
                    video_id=video_id,
                    platform='youtube',
                    source='message'
                )
                await db.add_analytics_event(event.to_json())
            except Exception as e:
                logger.error(f"Ошибка при публикации события аналитики: {e}")
        else:
            # Видео не найдено в канале - удаляем из кэша и скачиваем заново
            await db.delete_from_cache(video_id=video_id, quality=quality_label)
            cached_message_id = None
    
    # Видео нет в кэше - скачиваем через DownloadManager
    if not cached_message_id or cached_message_id == 0:
        await callback.message.edit_text(f"⏳ Скачиваю {quality_label}...")
        
        # Запрашиваем скачивание через DownloadManager
        response = await download_manager.request_download(
            user_id=callback.from_user.id,
            url=normalized_url,
            source='message',
            quality=quality_label,
            format_id=format_id
        )
        
        if response.is_error():
            await send_error(callback.message.chat.id, 'generic')
            if response.error:
                logger.error(f"Ошибка при запросе скачивания: {response.error}")
            return
        
        if response.is_ready():
            # Видео появилось в кэше
            try:
                await callback.message.delete()
            except:
                pass
            success = await send_video(callback.message.chat.id, response.message_id)
            if success:
                try:
                    from src.events.events import DownloadCompletedEvent
                    event = DownloadCompletedEvent(
                        user_id=callback.from_user.id,
                        video_id=response.job_id or video_id,
                        platform='youtube',
                        source='message'
                    )
                    await db.add_analytics_event(event.to_json())
                except Exception as e:
                    logger.error(f"Ошибка при публикации события аналитики: {e}")
            return
        
        if response.is_in_progress() or response.is_queued():
            # Ждем завершения скачивания
            message_id = await db.wait_for_download(response.job_id or video_id, timeout=1800.0, quality=quality_label)
            
            if message_id:
                try:
                    await callback.message.delete()
                except:
                    pass
                
                success = await send_video(callback.message.chat.id, message_id)
                if success:
                    # Публикуем событие
                    try:
                        from src.events.events import DownloadCompletedEvent
                        event = DownloadCompletedEvent(
                            user_id=callback.from_user.id,
                            video_id=response.job_id or video_id,
                            platform='youtube',
                            source='message'
                        )
                        await db.add_analytics_event(event.to_json())
                    except Exception as e:
                        logger.error(f"Ошибка при публикации события аналитики: {e}")
                else:
                    await send_error(callback.message.chat.id, 'generic')
            else:
                await send_error(callback.message.chat.id, 'download_failed')


@dp.callback_query(F.data.startswith("resend:"))
async def callback_resend_handler(callback: CallbackQuery):
    """Обработка нажатия кнопки 'Отправить еще раз'"""
    await callback.answer("📤 Отправляю видео...")
    
    url = callback.data.split(":", 1)[1]
    normalized_url = normalize_url(url)
    
    chat_id = callback.message.chat.id if callback.message else callback.from_user.id
    
    try:
        # Получаем video_id для проверки кэша
        link_info = link_processing_service.process_link(normalized_url)
        if not link_info:
            await send_error(chat_id, 'video_not_found')
            return
        
        video_id = link_info.video_id
        
        # Проверяем кэш
        cached_message_id = await db.get_cached_message_id(video_id=video_id, url=normalized_url)
        if not cached_message_id or cached_message_id == 0:
            await send_error(chat_id, 'video_not_found')
            return
        
        cached_file_id = await db.get_cached_file_id(video_id=video_id, url=normalized_url)
        
        success = await send_video(chat_id, cached_message_id)
        
        if not success:
            await send_error(chat_id, 'video_not_found')
    except Exception as e:
        logger.error(f"Ошибка при отправке видео из кэша: {e}", exc_info=True)
        await send_error(chat_id, 'generic')


@dp.chosen_inline_result()
async def chosen_inline_handler(chosen: types.ChosenInlineResult):
    """Обработка выбора inline-результата (для логирования)"""
    logger.info(f"Выбран inline-результат: result_id={chosen.result_id}, query={chosen.query}")


# ========== Helper Functions ==========

async def process_video_download(
    url: str,
    chat_id: int,
    status_msg: types.Message,
    user_id: int,
    source: str,
    quality: Optional[str] = None,
    format_id: Optional[str] = None
):
    """
    Обработать скачивание видео через DownloadManager
    
    Args:
        url: URL видео
        chat_id: ID чата
        status_msg: Сообщение со статусом для удаления
        user_id: ID пользователя
        source: Источник запроса
        quality: Качество видео (для YouTube) или None
        format_id: ID формата (для YouTube) или None
    """
    try:
        # Запрашиваем скачивание через DownloadManager
        response = await download_manager.request_download(
            user_id=user_id,
            url=url,
            source=source,
            quality=quality,
            format_id=format_id
        )
        
        if response.is_error():
            await edit_or_delete_wait_message(status_msg)
            await send_error(chat_id, 'generic')
            if response.error:
                logger.error(f"Ошибка при запросе скачивания: {response.error}")
            return
        
        if response.is_ready():
            # Видео уже готово в кэше - отправляем сразу
            await edit_or_delete_wait_message(status_msg)
            success = await send_video(chat_id, response.message_id)
            
            if success:
                # Публикуем событие аналитики
                platform = get_platform(url)
                # TODO: Добавить метод publish_download_analytics в DownloadManager или использовать напрямую db
                try:
                    from src.events.events import DownloadCompletedEvent
                    event = DownloadCompletedEvent(
                        user_id=user_id,
                        video_id=response.job_id or url,  # Используем job_id как video_id
                        platform=platform,
                        source=source
                    )
                    await db.add_analytics_event(event.to_json())
                except Exception as e:
                    logger.error(f"Ошибка при публикации события аналитики: {e}")
            else:
                await send_error(chat_id, 'generic')
            return
        
        if response.is_in_progress():
            # Видео уже скачивается другим пользователем
            await edit_or_delete_wait_message(status_msg)
            await bot.send_message(chat_id, "⏳ Видео уже обрабатывается, пожалуйста подождите...")
            # Ждем завершения
            message_id = await db.wait_for_download(response.job_id, timeout=1800.0, quality=quality)
            if message_id:
                success = await send_video(chat_id, message_id)
                if success:
                    # Публикуем событие
                    platform = get_platform(url)
                    try:
                        from src.events.events import DownloadCompletedEvent
                        event = DownloadCompletedEvent(
                            user_id=user_id,
                            video_id=response.job_id,
                            platform=platform,
                            source=source
                        )
                        await db.add_analytics_event(event.to_json())
                    except Exception as e:
                        logger.error(f"Ошибка при публикации события аналитики: {e}")
                else:
                    await send_error(chat_id, 'generic')
            else:
                await send_error(chat_id, 'download_failed')
            return
        
        if response.is_queued():
            # Видео добавлено в очередь - ждем завершения
            await edit_or_delete_wait_message(status_msg)
            await bot.send_message(chat_id, "⏳ Скачиваю видео...")
            
            message_id = await db.wait_for_download(response.job_id, timeout=1800.0, quality=quality)
            
            if message_id:
                success = await send_video(chat_id, message_id)
                if success:
                    # Публикуем событие
                    platform = get_platform(url)
                    try:
                        from src.events.events import DownloadCompletedEvent
                        event = DownloadCompletedEvent(
                            user_id=user_id,
                            video_id=response.job_id,
                            platform=platform,
                            source=source
                        )
                        await db.add_analytics_event(event.to_json())
                    except Exception as e:
                        logger.error(f"Ошибка при публикации события аналитики: {e}")
                else:
                    await send_error(chat_id, 'generic')
            else:
                await send_error(chat_id, 'download_failed')
            return
        
        # Неожиданный статус
        logger.warning(f"Неожиданный статус DownloadResponse: {response.status}")
        await edit_or_delete_wait_message(status_msg)
        await send_error(chat_id, 'generic')
            
    except Exception as e:
        logger.error(f"Ошибка при обработке скачивания видео: {e}", exc_info=True)
        await edit_or_delete_wait_message(status_msg)
        await send_error(chat_id, 'generic')


async def run_bot():
    """Запуск бота"""
    logger.info("Бот запущен!")
    logger.info("Ожидаю обновления...")
    await dp.start_polling(bot)
