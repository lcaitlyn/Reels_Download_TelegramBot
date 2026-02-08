import logging
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from src.bot.handlers.base import BasePlatformHandler, HandlerContext, QualityCallbackContext
from src.bot.process_download_response import process_download_response
from src.config import get_youtube_quality_labels, get_youtube_quality_display_map

if TYPE_CHECKING:
    from aiogram.types import Message

logger = logging.getLogger(__name__)

BUTTONS_PER_ROW = 2
MAX_TITLE_LENGTH = 100


def _build_quality_keyboard(
    quality_order: List[str],
    formats: dict,
    cached_qualities: List[str],
    video_id: str,
) -> List[List[InlineKeyboardButton]]:
    display_map = get_youtube_quality_display_map()
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for quality_label in quality_order:
        if quality_label not in formats:
            continue
        cached = quality_label in cached_qualities
        icon = "⚡️" if cached else "⏳"
        display_label = display_map.get(quality_label, quality_label)
        callback_data = f"quality:{video_id}:{quality_label}"
        row.append(
            InlineKeyboardButton(
                text=f"{display_label} {icon}",
                callback_data=callback_data,
            )
        )
        if len(row) == BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _get_preview_content(metadata: Optional[dict]) -> Tuple[str, Optional[str]]:
    """Из метаданных получить заголовок и URL превью для сообщения."""
    video_title = "📹 Выбери качество видео:"
    thumbnail_url = None
    if not metadata:
        return video_title, thumbnail_url
    title = metadata.get("title") or metadata.get("fulltitle")
    if title:
        video_title = (
            f"📹 {(title[:MAX_TITLE_LENGTH] + '...') if len(title) > MAX_TITLE_LENGTH else title}\n\n"
            "Выбери качество:"
        )
    thumb = metadata.get("thumbnail")
    if not thumb and metadata.get("thumbnails"):
        thumbnails = metadata.get("thumbnails") or []
        if thumbnails:
            last = thumbnails[-1]
            thumb = last.get("url") if isinstance(last, dict) else last
    if thumb:
        thumbnail_url = thumb
    return video_title, thumbnail_url


async def _send_quality_message(
    message: "Message",
    video_title: str,
    thumbnail_url: Optional[str],
    keyboard_rows: List[List[InlineKeyboardButton]],
) -> None:
    """Отправить сообщение с выбором качества (фото с подписью или текст)."""
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    if thumbnail_url:
        try:
            await message.answer_photo(
                photo=thumbnail_url,
                caption=video_title,
                reply_markup=markup,
            )
        except Exception as e:
            logger.warning("Не удалось отправить превью: %s", e)
            await message.answer(video_title, reply_markup=markup)
    else:
        await message.answer(video_title, reply_markup=markup)


class YouTubeHandler(BasePlatformHandler):

    def __init__(self, db: Any, downloader: Any, service_factory: Any) -> None:
        self.db = db
        self.downloader = downloader
        self.service_factory = service_factory

    async def _show_quality_selection(
        self,
        message: "Message",
        normalized_url: str,
        video_id: str,
        is_inline_query_result: bool,
    ) -> bool:
        youtube_service = self.service_factory.get_service("youtube")
        if not youtube_service:
            return False

        opts = youtube_service.get_ydl_opts()
        info = await self.downloader.get_info_async(normalized_url, opts)
        if not info:
            return False

        formats, metadata = youtube_service.parse_info_for_quality_selection(info)
        if not formats:
            return False

        quality_order = get_youtube_quality_labels()
        cached_qualities = []
        for quality_label in quality_order:
            if quality_label in formats:
                try:
                    if await self.db.check_quality_in_cache(video_id, quality_label):
                        cached_qualities.append(quality_label)
                except Exception as e:
                    logger.error("Ошибка при проверке качества в кэше: %s", e)

        await self.db.save_url_mapping(video_id, normalized_url, "youtube")

        keyboard_rows = _build_quality_keyboard(
            quality_order, formats, cached_qualities, video_id
        )
        if not keyboard_rows:
            return False

        if is_inline_query_result:
            try:
                await message.delete()
            except Exception:
                pass

        video_title, thumbnail_url = _get_preview_content(metadata)
        await _send_quality_message(message, video_title, thumbnail_url, keyboard_rows)
        return True

    def _is_shorts(self, context: HandlerContext) -> bool:
        return not context.link_info.service.needs_pre_download_choice(
            context.normalized_url, context.query_text
        )

    async def _handle_shorts(self, context: HandlerContext) -> None:
        if context.is_inline_query_result:
            try:
                await context.message.delete()
            except Exception:
                pass
        status_msg = await context.send_wait_message(context.message.chat.id)
        source = "inline" if context.is_inline_query_result else "message"
        await context.process_video_download(
            context.normalized_url,
            context.message.chat.id,
            status_msg,
            context.user_id,
            source,
        )

    async def _handle_regular_video(self, context: HandlerContext) -> bool:
        return await self._show_quality_selection(
            context.message,
            context.normalized_url,
            context.link_info.video_id,
            context.is_inline_query_result,
        )

    async def handle(self, context: HandlerContext) -> None:
        if self._is_shorts(context):
            await self._handle_shorts(context)
            return
        if await self._handle_regular_video(context):
            return
        await self._handle_shorts(context)

    async def handle_quality_callback(self, context: QualityCallbackContext) -> None:
        callback = context.callback
        video_id = context.video_id
        quality_label = context.quality_label
        display_map = get_youtube_quality_display_map()
        display_label = display_map.get(quality_label, quality_label)

        normalized_url = await context.db.get_original_url_by_video_id(video_id)
        if not normalized_url:
            if video_id.startswith("youtube:"):
                video_id_only = video_id.split(":", 1)[1]
                normalized_url = f"https://www.youtube.com/watch?v={video_id_only}"
                await context.db.save_url_mapping(video_id, normalized_url, "youtube")
            else:
                await callback.answer("❌ URL не найден. Попробуй отправить ссылку заново.")
                return

        await callback.answer(f"⏳ Скачиваю {display_label}...")

        youtube_service = context.service_factory.get_service("youtube")
        if not youtube_service:
            await context.edit_message_safe(callback.message, "❌ Ошибка: сервис YouTube недоступен")
            return

        formats = youtube_service.get_available_formats(normalized_url)
        if not formats or quality_label not in formats:
            await context.edit_message_safe(callback.message, "❌ Выбранное качество недоступно")
            return

        format_id = formats[quality_label].get("format_id")
        await context.edit_message_safe(callback.message, f"⏳ Скачиваю {display_label}...")

        response = await context.download_manager.request_download(
            user_id=callback.from_user.id if callback.from_user else 0,
            url=normalized_url,
            source="message",
            quality=quality_label,
            format_id=format_id,
        )

        user_id = callback.from_user.id if callback.from_user else 0
        job_id = response.job_id or video_id

        async def clear_status() -> None:
            try:
                await callback.message.delete()
            except Exception:
                pass

        async def publish_analytics(uid: int, vid: str, platform: str, source: str) -> None:
            await _publish_analytics(context.db, uid, vid, platform, source)

        await process_download_response(
            response,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            job_id=job_id,
            platform="youtube",
            source="message",
            quality=quality_label,
            db=context.db,
            clear_status=clear_status,
            send_video=context.send_video,
            send_error=context.send_error,
            publish_analytics=publish_analytics,
        )


async def _publish_analytics(
    db: Any, user_id: int, video_id: str, platform: str, source: str
) -> None:
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
