"""
Модуль для скачивания видео
"""
from .downloader import VideoDownloader  # Deprecated: будет удален после рефакторинга
from .download_manager import DownloadManager

__all__ = ['VideoDownloader', 'DownloadManager']
