"""
Сервис для работы с Instagram
Содержит всю специфичную логику для Instagram Reels/Posts
Знает только Instagram, формирует DownloadPlan
"""
import logging
import os
import re
from typing import Optional, Dict, Any
from urllib.parse import urlparse
from src.models.download_plan import DownloadPlan
from .base import BaseService

logger = logging.getLogger(__name__)


class InstagramService(BaseService):

    _SHORTCODE_PATTERN = re.compile(
        r'/(?:reels?|p|tv)/([A-Za-z0-9_-]{10,})',
        re.IGNORECASE
    )
    
    def can_handle(self, url: str) -> bool:
        """Может ли сервис обработать этот URL"""
        return 'instagram.com' in url.lower()

    

    def get_video_id(self, url: str) -> Optional[str]:
        """
        Returns:
            Строка вида "instagram:shortcode" или None.
        """
        shortcode = self._parse_shortcode_from_url(url)
        if shortcode:
            return f"instagram:{shortcode}"
        return None

    def extract_video_id(self, url: str) -> Optional[str]:
        return self._parse_shortcode_from_url(url)

    def _parse_shortcode_from_url(self, url: str) -> Optional[str]:
        if not url or 'instagram.com' not in url.lower():
            return None
        try:
            parsed = urlparse(url)
            path = (parsed.path or '').rstrip('/')
            match = self._SHORTCODE_PATTERN.search(path)
            if match:
                return match.group(1)
        except Exception as e:
            logger.debug("Ошибка парсинга URL Instagram %s: %s", url, e)
        return None

    def get_media_type(self, url: str) -> str:
        """
        Тип контента по URL: посты /p/ — фото, рилсы и IGTV — видео.
        Returns: 'photo' | 'video'
        """
        if not url or 'instagram.com' not in url.lower():
            return 'video'
        try:
            parsed = urlparse(url)
            path = (parsed.path or '').rstrip('/').lower()
            if '/p/' in path:
                return 'photo'
        except Exception:
            pass
        return 'video'

    def get_ydl_opts(self) -> Dict[str, Any]:
        """Опции yt-dlp для get_info (только информация)."""
        return self._get_info_opts_for_instagram()

    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Получить метаданные видео Instagram
        
        Args:
            url: URL видео
            
        Returns:
            Словарь с метаданными (id, duration, filesize, ext, etc.) или None
        """
        info_opts = self._get_info_opts_for_instagram()
        return self.downloader.get_video_info(url, info_opts)
    
    def build_download_plan(
        self,
        url: str,
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional[DownloadPlan]:
        """
        Построить план скачивания для Instagram
        
        ОПТИМИЗАЦИЯ: Не получаем метаданные здесь - это блокирует event loop.
        Метаданные будут получены во время скачивания через yt-dlp.
        
        Args:
            url: URL видео Instagram
            quality: Качество (для Instagram не используется)
            format_id: ID формата (для Instagram не используется)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        # Извлекаем video_id из URL (быстро, без запросов к API)
        video_id = self.extract_video_id(url)
        if not video_id:
            logger.error("[Instagram] Не удалось извлечь video_id из URL")
            return None
        
        # Формируем опции yt-dlp для Instagram
        format_selector = 'best[ext=mp4]/best'
        ydl_opts = self._get_ydl_opts_for_instagram(format_selector)
        
        # Не знаем размер файла заранее - будем определять во время скачивания
        # По умолчанию считаем, что можно стримить (для маленьких файлов)
        # Если файл окажется большим, worker переключится на файловый режим
        streamable = True  # Будет переопределено во время скачивания
        media_type = self.get_media_type(url)
        telegram_caption = f"Source: {url}"
        
        return DownloadPlan(
            platform='instagram',
            video_id=f"instagram:{video_id}",
            url=url,
            format_selector=format_selector,
            streamable=streamable,
            media_type=media_type,
            telegram_caption=telegram_caption,
            ydl_opts=ydl_opts,
            metadata=None  # Метаданные будут получены во время скачивания
        )
    
    def _get_info_opts_for_instagram(self) -> Dict[str, Any]:
        """Получить опции для получения информации о видео Instagram"""
        info_opts = {
            # ВАЖНО: для отладки Instagram используем подробный вывод yt-dlp
            # Аналогично совету из issue: удаляем quiet и включаем verbose
            'verbose': True,
            'quiet': False,
            'no_warnings': False,  # Показываем предупреждения
            'extract_flat': False,
        }
        
        try:
            info_opts['extractor_args'] = {'instagram': {'webpage_download': False}}
        except:
            pass

        # Используем те же cookies, что и при скачивании,
        # чтобы запрос info проходил под залогиненным аккаунтом.
        cookiefile = self._get_instagram_cookiefile()
        if cookiefile:
            info_opts['cookiefile'] = cookiefile
        
        return info_opts
    
    def _get_ydl_opts_for_instagram(self, format_selector: str) -> Dict[str, Any]:
        """
        Получить опции yt-dlp для Instagram
        
        Args:
            format_selector: Селектор формата
            
        Returns:
            Словарь с опциями yt-dlp
        """
        ydl_opts = {
            'format': format_selector,
            # ВАЖНО: следуем рекомендациям yt-dlp для отладки Instagram:
            # не используем quiet=True и явно включаем verbose-режим
            'verbose': True,
            'quiet': False,
            'no_warnings': False,  # Показываем предупреждения
            'noplaylist': True,
            'extract_flat': False,
            'postprocessors': [],  # Отключаем постобработку (не требуется ffmpeg)
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writethumbnail': False,
            'nopart': True,  # Не создавать частичные файлы
            'continue_dl': False,  # Не продолжать скачивание (всегда скачивать заново)
        }
        
        # Специальные опции для Instagram
        try:
            ydl_opts['extractor_args'] = {'instagram': {'webpage_download': False}}
        except Exception:
            pass

        # Добавляем опции для обхода ограничений Instagram:
        # 1) сначала пробуем переменную окружения INSTAGRAM_COOKIES_FILE
        # 2) если её нет, используем репозиторный файл cookies/cookies.txt, если он существует.
        cookiefile = self._get_instagram_cookiefile()
        if cookiefile:
            ydl_opts['cookiefile'] = cookiefile
        else:
            ydl_opts['cookiefile'] = None

        ydl_opts['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

        return ydl_opts

    def _get_instagram_cookiefile(self) -> Optional[str]:
        """
        Определить путь к cookies-файлу для Instagram.
        Приоритет:
        1) переменная окружения INSTAGRAM_COOKIES_FILE
        2) файл репозитория cookies/cookies.txt (если существует)
        """
        # 1. Переменная окружения (используется в docker-compose.prod)
        cookiefile = os.getenv('INSTAGRAM_COOKIES_FILE')
        if cookiefile:
            return cookiefile

        # 2. Файл в репозитории: ./cookies/cookies.txt
        try:
            # instagram.py лежит в src/services/, нужно подняться на два уровня до корня проекта
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            default_path = os.path.join(base_dir, 'cookies', 'cookies.txt')
            if os.path.exists(default_path):
                return default_path
        except Exception:
            pass

        return None
    
