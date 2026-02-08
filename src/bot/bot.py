"""
Основной модуль бота - Event Router + UI Adapter
"""
import os
import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedVideo,
    InlineQueryResultCachedPhoto,
    InputTextMessageContent,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest

from src.database.redis_db import Database
from src.downloader.download_manager import DownloadManager
from src.utils.utils import normalize_url, is_supported_url, get_platform, ensure_url_protocol
from src.services import LinkProcessingService
from src.services.service_factory import ServiceFactory
from src.services.ytdlp_service import YtDlpService
from src.models.download_response import DownloadResponse
from src.use_cases import (
    HandleInlineQueryUseCase,
    HandleStartUseCase,
    GetStatsUseCase,
)
from src.bot.handlers import get_handler, HandlerContext, QualityCallbackContext, register_handler, YouTubeHandler
from src.bot.process_download_response import process_download_response

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

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

try:
    CHANNEL_ID = int(CHANNEL_ID)
except ValueError:
    pass

HELLO_GIF_PATH = Path(__file__).resolve().parent.parent / "src" / "resources" / "hello.gif"

session = AiohttpSession(timeout=600)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

db = Database()
downloader = YtDlpService()

service_factory = ServiceFactory(downloader)
link_processing_service = LinkProcessingService(service_factory)
download_manager = DownloadManager(db, link_processing_service)

handle_inline_query_use_case = HandleInlineQueryUseCase(db, downloader)
handle_start_use_case = HandleStartUseCase(db, downloader)
register_handler("youtube", YouTubeHandler(db, downloader, service_factory))
get_stats_use_case = GetStatsUseCase(db)


async def send_wait_message(chat_id: int) -> types.Message:
    """Отправить сообщение '⏳ Ожидайте'"""
    return await bot.send_message(chat_id, "⏳")


async def edit_or_delete_wait_message(message: Optional[types.Message]):
    if message:
        try:
            await message.delete()
        except:
            pass


async def send_video(chat_id: int, message_id: int) -> bool:
    """
    Скопировать сообщение из канала пользователю (copy_message).
    Тип медиа сохраняется: видео, фото или аудио — в зависимости от того,
    что загрузил воркер по плану сервиса (media_type).
    
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


async def _publish_download_analytics(user_id: int, video_id: str, platform: str, source: str) -> None:
    """Публикует событие аналитики после успешной отправки видео."""
    try:
        from src.events.events import DownloadCompletedEvent
        event = DownloadCompletedEvent(
            user_id=user_id,
            video_id=video_id,
            platform=platform,
            source=source,
        )
        await db.add_analytics_event(event.to_json())
    except Exception as e:
        logger.error("Ошибка при публикации события аналитики: %s", e)


async def edit_message_safe(message: types.Message, text: str) -> bool:
    """
    Безопасно редактирует сообщение (текст или подпись к фото)
    
    Args:
        message: Сообщение для редактирования
        text: Новый текст/подпись
        
    Returns:
        True если успешно, False при ошибке
    """
    try:
        if message.photo:
            # Сообщение с фото - редактируем подпись
            await message.edit_caption(caption=text)
        else:
            # Текстовое сообщение - редактируем текст
            await message.edit_text(text)
        return True
    except Exception as e:
        logger.warning(f"Не удалось отредактировать сообщение: {e}")
        # Если не удалось отредактировать, отправляем новое сообщение
        try:
            await message.answer(text)
        except:
            pass
        return False


@dp.message(Command("stats"))
async def stats_handler(message: types.Message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    logger.info(f"[REQUEST] /stats от user_id={user_id}")
    
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
    user_id = message.from_user.id if message.from_user else message.chat.id
    logger.info(f"[REQUEST] /start от user_id={user_id}")
    message_text = message.text or ""
    
    try:
        result = await handle_start_use_case.execute(message_text, user_id)
        
        if result['type'] == 'welcome':
            welcome_text = (
                "👋 Привет! Отправь мне ссылку на видео из:\n"
                "• YouTube / YouTube Shorts\n"
                "• Instagram Reels / Posts\n"
                "• TikTok\n\n"
                f"Или используй {BOT_USERNAME} в любом чате для быстрого доступа!"
            )
            cached_file_id = await db.get_hello_gif_file_id()
            if cached_file_id:
                await message.answer_animation(cached_file_id, caption=welcome_text)
            else:
                if HELLO_GIF_PATH.is_file():
                    sent = await message.answer_animation(
                        FSInputFile(HELLO_GIF_PATH),
                        caption=welcome_text,
                    )
                    if sent.animation and sent.animation.file_id:
                        await db.set_hello_gif_file_id(sent.animation.file_id)
                else:
                    await message.answer(welcome_text)
        # TODO тут разве не use case для handle_inline_query должен быть?
        elif result['type'] == 'deep_link':
            # TODO какого хуя тут вообще shorts?
            quality = 'shorts' if result.get('is_shorts') else None

            if result.get('cached_message_id'):
                success = await send_video(message.chat.id, result['cached_message_id'])
                if not success:
                    status_msg = await send_wait_message(message.chat.id)
                    await process_video_download(
                        result['url'],
                        message.chat.id,
                        status_msg,
                        user_id,
                        'deep_link',
                        quality=quality
                    )
            else:
                status_msg = await send_wait_message(message.chat.id)
                await process_video_download(
                    result['url'],
                    message.chat.id,
                    status_msg,
                    user_id,
                    'deep_link',
                    quality=quality
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
    user_id = message.from_user.id if message.from_user else message.chat.id
    text = message.text.strip()
    logger.info(f"[REQUEST] Текстовое сообщение от user_id={user_id}: {text[:100]}")

    is_inline_query_result = message.via_bot and message.via_bot.id == bot.id

    text, is_valid_url = ensure_url_protocol(text)
    if not is_valid_url:
        await message.answer(
            "❌ Пожалуйста, отправь корректную ссылку на видео.\nПоддерживаются: YouTube, Instagram, TikTok"
        )
        return

    normalized_url = normalize_url(text)
    if not is_supported_url(normalized_url):
        await send_error(message.chat.id, "unsupported_platform")
        return

    link_info = link_processing_service.process_link(normalized_url)
    if not link_info:
        await send_error(message.chat.id, "video_not_found")
        return

    handler = get_handler(link_info.platform)
    if not handler:
        await send_error(message.chat.id, "unsupported_platform")
        return

    context = HandlerContext(
        message=message,
        normalized_url=normalized_url,
        link_info=link_info,
        user_id=user_id,
        is_inline_query_result=is_inline_query_result,
        query_text=text,
        send_wait_message=send_wait_message,
        send_error=send_error,
        edit_or_delete_wait_message=edit_or_delete_wait_message,
        process_video_download=process_video_download,
    )
    await handler.handle(context)


@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    """Обработка inline-запросов"""
    user_id = inline_query.from_user.id if inline_query.from_user else 0
    query = inline_query.query.strip()
    logger.info(f"[REQUEST] Inline query от user_id={user_id}: {query[:100]}")
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
        if result['type'] == 'unsupported':
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
        if result['type'] == 'cached':
            for item in result['results']:
                if item.get('type') == 'cached_media' or item.get('type') == 'cached_video':
                    mid = f"cached_{abs(hash(query))}"
                    if item.get('media_type') == 'photo':
                        results.append(
                            InlineQueryResultCachedPhoto(
                                id=mid,
                                photo_file_id=item['file_id'],
                                title=item.get('title', ''),
                                description=item.get('description', '')
                            )
                        )
                    else:
                        results.append(
                            InlineQueryResultCachedVideo(
                                id=mid,
                                video_file_id=item['file_id'],
                                title=item.get('title', ''),
                                description=item.get('description', '')
                            )
                        )
        if result['type'] == 'link':
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
        if result['type'] == 'text':
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
    # Формат: quality:video_id:quality_label
    data_without_prefix = callback.data[8:]
    last_colon_index = data_without_prefix.rfind(":")
    if last_colon_index == -1:
        await callback.answer("❌ Ошибка в данных")
        return
    video_id = data_without_prefix[:last_colon_index]
    quality_label = data_without_prefix[last_colon_index + 1:]

    handler = get_handler("youtube")
    if not handler or not hasattr(handler, "handle_quality_callback"):
        await callback.answer("❌ Сервис недоступен")
        return

    context = QualityCallbackContext(
        callback=callback,
        video_id=video_id,
        quality_label=quality_label,
        db=db,
        download_manager=download_manager,
        service_factory=service_factory,
        send_video=send_video,
        send_error=send_error,
        edit_message_safe=edit_message_safe,
    )
    await handler.handle_quality_callback(context)


@dp.chosen_inline_result()
async def chosen_inline_handler(chosen: types.ChosenInlineResult):
    """Обработка выбора inline-результата (для логирования)"""
    logger.info(f"Выбран inline-результат: result_id={chosen.result_id}, query={chosen.query}")



async def process_video_download(
    url: str,
    chat_id: int,
    status_msg: types.Message,
    user_id: int,
    source: str,
    quality: Optional[str] = None,
    format_id: Optional[str] = None,
):
    """
    Обработать скачивание видео через DownloadManager.
    Делегирует обработку ответа в process_download_response.
    """
    try:
        response = await download_manager.request_download(
            user_id=user_id,
            url=url,
            source=source,
            quality=quality,
            format_id=format_id,
        )
        job_id = response.job_id or url
        platform = get_platform(url)

        async def clear_status() -> None:
            await edit_or_delete_wait_message(status_msg)

        async def notify_in_progress() -> None:
            await bot.send_message(chat_id, "⏳ Видео уже обрабатывается, пожалуйста подождите...")

        async def notify_queued() -> None:
            await bot.send_message(chat_id, "⏳ Скачиваю видео...")

        await process_download_response(
            response,
            chat_id=chat_id,
            user_id=user_id,
            job_id=job_id,
            platform=platform,
            source=source,
            quality=quality,
            db=db,
            clear_status=clear_status,
            send_video=send_video,
            send_error=send_error,
            publish_analytics=_publish_download_analytics,
            notify_in_progress=notify_in_progress,
            notify_queued=notify_queued,
        )
    except Exception as e:
        logger.error("Ошибка при обработке скачивания видео: %s", e, exc_info=True)
        await edit_or_delete_wait_message(status_msg)
        await send_error(chat_id, "generic")


async def run_bot():
    """Запуск бота"""
    logger.info("Бот запущен!")
    logger.info("Ожидаю обновления...")
    await dp.start_polling(bot)
