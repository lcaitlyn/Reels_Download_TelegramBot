"""
Утилиты для работы с URL и определения платформы
"""
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs


def normalize_url(url: str) -> str:
    """
    Нормализация URL для унификации разных форматов ссылок
    YouTube: youtube.com/watch?v=ABC -> youtube.com/watch?v=ABC
    YouTube Shorts: youtube.com/shorts/ABC -> youtube.com/watch?v=ABC
    Instagram: instagram.com/p/ABC/ -> instagram.com/p/ABC
    TikTok: tiktok.com/@user/video/123 -> tiktok.com/@user/video/123
    """
    url = url.strip()
    
    # YouTube normalization
    if 'youtube.com' in url or 'youtu.be' in url:
        # Извлечение video ID из разных форматов
        video_id = None
        
        # youtube.com/watch?v=ID
        match = re.search(r'[?&]v=([^&]+)', url)
        if match:
            video_id = match.group(1)
        
        # youtube.com/shorts/ID
        match = re.search(r'/shorts/([^/?]+)', url)
        if match:
            video_id = match.group(1)
        
        # youtu.be/ID
        match = re.search(r'youtu\.be/([^/?]+)', url)
        if match:
            video_id = match.group(1)
        
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    
    # Instagram normalization
    if 'instagram.com' in url:
        # instagram.com/p/POST_ID/
        match = re.search(r'instagram\.com/(?:p|reel)/([^/?]+)', url)
        if match:
            post_id = match.group(1)
            return f"https://www.instagram.com/p/{post_id}/"
    
    # TikTok normalization
    if 'tiktok.com' in url:
        # Сохраняем как есть, но убираем параметры
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    
    return url


def get_platform(url: str) -> str:
    """Определение платформы по URL"""
    url_lower = url.lower()
    
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'youtube'
    elif 'instagram.com' in url_lower:
        return 'instagram'
    elif 'tiktok.com' in url_lower:
        return 'tiktok'
    else:
        return 'unknown'


def is_supported_url(url: str) -> bool:
    """Проверка, поддерживается ли URL"""
    platforms = ['youtube.com', 'youtu.be', 'instagram.com', 'tiktok.com']
    url_lower = url.lower()
    return any(platform in url_lower for platform in platforms)


def is_youtube_video(url: str) -> bool:
    """
    Проверка, является ли URL YouTube видео (не Shorts)
    
    Args:
        url: URL для проверки
        
    Returns:
        True если это YouTube видео (не Shorts), False иначе
    """
    url_lower = url.lower()
    if 'youtube.com' not in url_lower and 'youtu.be' not in url_lower:
        return False
    
    # Shorts имеют /shorts/ в URL
    if '/shorts/' in url_lower:
        return False
    
    # Обычные видео имеют /watch?v= или youtu.be/
    if '/watch?v=' in url_lower or 'youtu.be/' in url_lower:
        return True
    
    return False


def get_video_id_fast(url: str) -> tuple[Optional[str], str]:
    """
    Быстрое извлечение video_id из URL БЕЗ HTTP-запросов (парсинг URL)
    Работает для YouTube и Instagram. Для TikTok может вернуть None (требуется HTTP-запрос)
    
    Args:
        url: URL видео
        
    Returns:
        tuple: (video_id в формате "platform:video_id" или None, normalized_url)
    """
    url = url.strip()
    url_lower = url.lower()
    
    # YouTube
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        video_id = None
        
        # youtube.com/watch?v=ID
        match = re.search(r'[?&]v=([^&]+)', url)
        if match:
            video_id = match.group(1)
        
        # youtube.com/shorts/ID
        if not video_id:
            match = re.search(r'/shorts/([^/?]+)', url)
            if match:
                video_id = match.group(1)
        
        # youtu.be/ID
        if not video_id:
            match = re.search(r'youtu\.be/([^/?]+)', url)
            if match:
                video_id = match.group(1)
        
        if video_id:
            normalized_url = f"https://www.youtube.com/watch?v={video_id}"
            return (f"youtube:{video_id}", normalized_url)
    
    # Instagram
    if 'instagram.com' in url_lower:
        # instagram.com/p/POST_ID/ или instagram.com/reel/POST_ID/
        match = re.search(r'instagram\.com/(?:p|reel)/([^/?]+)', url)
        if match:
            post_id = match.group(1)
            normalized_url = f"https://www.instagram.com/p/{post_id}/"
            # Instagram: reel и post с одним ID - это одно и то же видео, используем одинаковый формат
            return (f"instagram:{post_id}", normalized_url)
    
    # TikTok - сложнее, нужен HTTP-запрос для канонического ID
    # Можно попробовать извлечь из URL, но это не канонический ID
    # Для TikTok лучше использовать yt-dlp extractor
    if 'tiktok.com' in url_lower:
        # Сохраняем как есть, но нормализуем
        normalized_url = normalize_url(url)
        # Для TikTok лучше использовать yt-dlp для получения канонического ID
        # Здесь возвращаем None, чтобы fallback на yt-dlp
        return (None, normalized_url)
    
    # Неизвестная платформа
    normalized_url = normalize_url(url)
    return (None, normalized_url)
