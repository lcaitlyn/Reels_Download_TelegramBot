import logging

from src.bot.handlers.base import BasePlatformHandler, HandlerContext

logger = logging.getLogger(__name__)



class TikTokHandler(BasePlatformHandler):

    # TODO создать обработу post (каруселей фотографий)
    async def handle(self, ctx: HandlerContext) -> None:
        if ctx.is_inline_query_result:
            try:
                await ctx.message.delete()
            except Exception:
                pass

        status_msg = await ctx.send_wait_message(ctx.message.chat.id)
        source = "inline" if ctx.is_inline_query_result else "message"
        
        await ctx.process_video_download(
            ctx.normalized_url,
            ctx.message.chat.id,
            status_msg,
            ctx.user_id,
            source,
        )
