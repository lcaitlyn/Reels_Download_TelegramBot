"""
События для аналитики
"""
from .events import (
    DownloadCompletedEvent,
    VideoViewClickedEvent,
    UserReferredEvent
)

__all__ = [
    'DownloadCompletedEvent',
    'VideoViewClickedEvent',
    'UserReferredEvent'
]
