"""
Use cases для бизнес-логики бота
"""
from src.use_cases.handle_inline_query import HandleInlineQueryUseCase
from src.use_cases.handle_start import HandleStartUseCase
from src.use_cases.get_stats import GetStatsUseCase

__all__ = [
    'HandleInlineQueryUseCase',
    'HandleStartUseCase',
    'GetStatsUseCase',
]
