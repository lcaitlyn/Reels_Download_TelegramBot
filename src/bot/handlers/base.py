from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Awaitable, Any

from src.models.link_info import LinkInfo


# TODO переименовать всё на handeler_{service}
@dataclass
class HandlerContext:
    message: Any
    normalized_url: str
    link_info: LinkInfo
    user_id: int
    is_inline_query_result: bool
    query_text: str

    send_wait_message: Callable[[int], Awaitable[Any]]
    send_error: Callable[[int, str], Awaitable[None]]
    edit_or_delete_wait_message: Callable[[Any], Awaitable[None]]
    process_video_download: Callable[..., Awaitable[None]] 


@dataclass
class QualityCallbackContext:
    """
    Контекст для обработки callback выбора качества (YouTube).
    """
    callback: Any
    video_id: str
    quality_label: str
    # Зависимости от бота
    db: Any
    download_manager: Any
    service_factory: Any
    send_video: Callable[[int, int], Awaitable[bool]]
    send_error: Callable[[int, str], Awaitable[None]]
    edit_message_safe: Callable[[Any, str], Awaitable[bool]]


class BasePlatformHandler(ABC):

    @abstractmethod
    async def handle(self, context: HandlerContext) -> None:
        pass
