"""
Модели данных для системы скачивания видео
"""
from .download_plan import DownloadPlan
from .link_info import LinkInfo
from .download_response import DownloadResponse

__all__ = ['DownloadPlan', 'LinkInfo', 'DownloadResponse']
