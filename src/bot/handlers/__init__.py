from typing import Optional

from src.bot.handlers.base import BasePlatformHandler, HandlerContext, QualityCallbackContext
from src.bot.handlers.youtube import YouTubeHandler
from src.bot.handlers.instagram import InstagramHandler
from src.bot.handlers.tiktok import TikTokHandler

_REGISTRY: dict[str, BasePlatformHandler] = {
    "instagram": InstagramHandler(),
    "tiktok": TikTokHandler(),
}


def get_handler(platform: str) -> Optional[BasePlatformHandler]:
    return _REGISTRY.get(platform)


def register_handler(platform: str, handler: BasePlatformHandler) -> None:
    _REGISTRY[platform] = handler


__all__ = [
    "HandlerContext",
    "QualityCallbackContext",
    "BasePlatformHandler",
    "YouTubeHandler",
    "InstagramHandler",
    "TikTokHandler",
    "get_handler",
    "register_handler",
]
