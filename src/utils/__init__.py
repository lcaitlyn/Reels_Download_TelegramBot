"""
Утилиты для работы с URL и определения платформы
"""
from .utils import (
    normalize_url,
    get_platform,
    is_supported_url,
    is_youtube_video,
    get_video_id_fast
)

__all__ = [
    'normalize_url',
    'get_platform',
    'is_supported_url',
    'is_youtube_video',
    'get_video_id_fast'
]
