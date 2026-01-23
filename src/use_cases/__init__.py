"""
Use cases для бизнес-логики бота
"""
from src.use_cases.download_video import DownloadVideoUseCase
from src.use_cases.get_cached_video import GetCachedVideoUseCase
from src.use_cases.handle_inline_query import HandleInlineQueryUseCase
from src.use_cases.handle_quality_selection import HandleQualitySelectionUseCase
from src.use_cases.handle_start import HandleStartUseCase
from src.use_cases.get_stats import GetStatsUseCase

__all__ = [
    'DownloadVideoUseCase',
    'GetCachedVideoUseCase',
    'HandleInlineQueryUseCase',
    'HandleQualitySelectionUseCase',
    'HandleStartUseCase',
    'GetStatsUseCase',
]
