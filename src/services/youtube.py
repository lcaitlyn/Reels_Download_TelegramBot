"""
Сервис для работы с YouTube
Содержит всю специфичную логику для YouTube видео/Shorts
Знает только YouTube, формирует DownloadPlan
"""
import logging
from typing import Optional, Dict, Any, Tuple
from src.models.download_plan import DownloadPlan
from src.config import get_youtube_shorts_quality_order, get_youtube_quality_heights, get_max_file_size_mb
from .base import BaseService

logger = logging.getLogger(__name__)

class YouTubeService(BaseService):
    """
    Сервис для работы с YouTube видео
    """
    
    def can_handle(self, url: str) -> bool:
        return "youtube.com" in url.lower() or "youtu.be" in url.lower()

    def needs_pre_download_choice(self, url: str, query_text: Optional[str] = None) -> bool:
        """Для YouTube — показывать выбор качества только для обычных видео, не для Shorts."""
        if not self.can_handle(url):
            return False
        text = (query_text or url).lower()
        return "shorts" not in text

    @staticmethod
    def _is_shorts_url(url: str) -> bool:
        return "shorts" in url.lower()

    def _get_best_shorts_format_id(self, url: str) -> Optional[str]:
        quality_order = get_youtube_shorts_quality_order()
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'listformats': True,
        }
        info = self.downloader.get_info(url, ydl_opts)
        if not info:
            return None
        formats = info.get('formats', [])
        muxed = [
            f for f in formats
            if isinstance(f, dict)
            and (f.get('vcodec') or 'none') != 'none'
            and (f.get('acodec') or 'none') != 'none'
        ]
        if not muxed:
            logger.info("[YouTube] Shorts: нет форматов с видео+аудио, fallback на height-селектор")
            return None

        def priority_key(fmt: Dict[str, Any]) -> tuple:
            note = (fmt.get('format_note') or '').strip()
            prio = next(
                (i for i, q in enumerate(quality_order) if q in note or note == q or note.startswith(q)),
                len(quality_order),
            )
            height = fmt.get('height') or 0
            return (prio, -height)

        muxed.sort(key=priority_key)
        best = muxed[0]
        fid = best.get('format_id')
        logger.info(
            f"[YouTube] Shorts: выбран формат {fid} "
            f"(format_note={best.get('format_note')}, height={best.get('height')})"
        )
        return fid

    def get_video_id(self, url: str) -> Optional[str]:
        return self.downloader.get_video_id(url)

    def get_ydl_opts(self) -> Dict[str, Any]:
        """
        Опции yt-dlp для одного get_info (форматы + метаданные без скачивания).
        """
        return {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'extract_flat': False,
            'listformats': True,
        }

    def parse_info_for_quality_selection(
        self, info: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Из одного ответа get_info получить словарь форматов (1080p/720p/480p/audio -> format_id)
        и метаданные для превью. Только форматы с аудио (muxed).
        Качества и лимит размера берутся из конфига (YOUTUBE_QUALITY_HEIGHTS, MAX_FILE_SIZE_MB).

        Returns:
            (formats_dict, metadata_dict)
            formats_dict: {'1080p': {'format_id': '...', 'filesize': ..., 'ext': ...}, ...}
            metadata_dict: {'title': ..., 'thumbnail': ..., 'fulltitle': ...}
        """
        if not info:
            return None, None
        max_bytes = int(get_max_file_size_mb() * 1024 * 1024)
        formats_list = info.get('formats') or []
        muxed = [
            f for f in formats_list
            if isinstance(f, dict)
            and (f.get('vcodec') or 'none') != 'none'
            and (f.get('acodec') or 'none') != 'none'
        ]
        formats_dict: Dict[str, Any] = {}
        quality_heights = get_youtube_quality_heights()
        for heights, label in quality_heights:
            heights_list = [heights] if isinstance(heights, int) else list(heights)
            candidates = [
                f for f in muxed
                if (f.get('height') or 0) in heights_list
            ]
            if not candidates:
                continue
            best = min(
                candidates,
                key=lambda x: x.get('filesize') or x.get('filesize_approx') or float('inf')
            )
            filesize = best.get('filesize') or best.get('filesize_approx') or 0
            if filesize and filesize > max_bytes:
                continue
            fid = best.get('format_id')
            if fid and label not in formats_dict:
                formats_dict[label] = {
                    'format_id': fid,
                    'filesize': filesize,
                    'ext': best.get('ext', 'mp4'),
                    'height': best.get('height'),
                }
        # Audio-only
        audio_formats = [
            f for f in formats_list
            if isinstance(f, dict)
            and (f.get('vcodec') or 'none') == 'none'
            and (f.get('acodec') or 'none') != 'none'
            and (f.get('ext') or '') in ('m4a', 'webm', 'mp3', 'opus')
        ]
        if audio_formats:
            best_audio = max(
                audio_formats,
                key=lambda x: x.get('filesize') or x.get('filesize_approx') or 0
            )
            audio_size = best_audio.get('filesize') or best_audio.get('filesize_approx') or 0
            if not audio_size or audio_size <= max_bytes:
                fid = best_audio.get('format_id')
                if fid:
                    formats_dict['audio'] = {
                        'format_id': fid,
                        'filesize': audio_size,
                        'ext': best_audio.get('ext', 'm4a'),
                    }
        metadata = {
            'title': info.get('title') or info.get('fulltitle'),
            'thumbnail': info.get('thumbnail'),
            'fulltitle': info.get('fulltitle') or info.get('title'),
        }
        if not metadata.get('thumbnail') and info.get('thumbnails'):
            thumbnails = info.get('thumbnails') or []
            if thumbnails:
                last = thumbnails[-1]
                if isinstance(last, dict):
                    metadata['thumbnail'] = last.get('url')
                elif isinstance(last, str):
                    metadata['thumbnail'] = last
        return (formats_dict if formats_dict else None, metadata)

    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        opts = self.get_ydl_opts()
        info = self.downloader.get_info(url, opts)
        if not info:
            return None
        _, metadata = self.parse_info_for_quality_selection(info)
        return metadata

    def get_available_formats(self, url: str) -> Optional[Dict[str, Any]]:
        opts = self.get_ydl_opts()
        info = self.downloader.get_info(url, opts)
        if not info:
            return None
        formats_dict, _ = self.parse_info_for_quality_selection(info)
        return formats_dict
    
    # TODO требуется рефактор
    def build_download_plan(
        self,
        url: str,
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional[DownloadPlan]:
        """
        Построить план скачивания для YouTube
        
        Args:
            url: URL видео YouTube
            quality: Качество видео (480p, 720p, 1080p, audio) или None
            format_id: ID формата из yt-dlp (опционально)
            
        Returns:
            DownloadPlan или None при ошибке
        """
        video_id = self.get_video_id(url)
        if not video_id:
            logger.error("[YouTube] Не удалось извлечь video_id из URL")
            return None
        
        is_shorts = self._is_shorts_url(url) or quality == 'shorts'
        if is_shorts and not format_id:
            format_id = self._get_best_shorts_format_id(url)
        format_selector = self._prepare_format_selector(format_id, quality, is_shorts=is_shorts)
        
        ydl_opts = self._get_ydl_opts_for_youtube(format_selector)
        
        streamable = not is_shorts
        
        audio_only = quality == 'audio' if quality else False
        media_type = 'audio' if audio_only else 'video'
        telegram_caption = f"Source: {url}"
        
        return DownloadPlan(
            platform='youtube',
            video_id=f"youtube:{video_id}",
            url=url,
            format_selector=format_selector,
            quality=quality,
            audio_only=audio_only,
            streamable=streamable,
            media_type=media_type,
            telegram_caption=telegram_caption,
            ydl_opts=ydl_opts,
            metadata=None
        )
    
    # TODO нахуй он вообще нужен если я уже делаю по format_id закачку?
    def _prepare_format_selector(
        self,
        format_id: Optional[str],
        quality: Optional[str],
        *,
        is_shorts: bool = False
    ) -> str:
        """
        Подготовить селектор формата для YouTube.
        Shorts (9:16): один уже сведённый формат (без мержа), чтобы качать без ffmpeg.
        Приоритет: 720x1280 → 480x854 → 360 (один поток mp4/m3u8 с видео+звуком).
        """
        # Shorts: если уже выбран format_id (muxed по приоритету format_note) — используем его
        if is_shorts:
            if format_id:
                return format_id
            # Fallback: по высоте (один поток без мержа)
            if quality == '1080p':
                return 'best[height<=1920][ext=mp4]/best[height<=1920]'
            return 'best[height<=1280][ext=mp4]/best[height<=1280]/best[height<=854]/best'

        # Обычное видео (16:9): при выборе 480p/720p/1080p ВСЕГДА селектор по качеству, НЕ format_id.
        # (92, 93, 94, 300 и т.д. — HLS, при -f 300 дают "The downloaded file is empty".)
        if quality in ('480p', '720p', '1080p'):
            if quality == '480p':
                return 'bestvideo[height<=480][ext=mp4]+bestaudio/best'
            if quality == '720p':
                return 'bestvideo[height<=720][ext=mp4]+bestaudio/best'
            if quality == '1080p':
                return 'bestvideo[height<=1080][ext=mp4]+bestaudio/best'
        
        if format_id:
            audio_only_formats = ('140', '250', '251', '139', '141', '171', '249')
            if format_id.startswith(audio_only_formats) or format_id in audio_only_formats:
                logger.info(f"[YouTube] Использую audio-only формат {format_id} как есть")
                return format_id
            video_only_formats = ('135', '136', '137', '160', '133', '134', '298', '299', '264', '266', '138', '779', '780')
            if format_id.startswith(video_only_formats) or format_id in video_only_formats:
                return f"{format_id}+bestaudio/best"
            return format_id
        
        if quality == 'audio':
            return 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/140/250/251'
        
        return 'bestvideo[height<=360][ext=mp4]+bestaudio/best'
    
    def _get_ydl_opts_for_youtube(self, format_selector: str) -> Dict[str, Any]:
        """
        Получить опции yt-dlp для YouTube
        
        Args:
            format_selector: Селектор формата
            
        Returns:
            Словарь с опциями yt-dlp
        """
        return {
            'format': format_selector,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'extract_flat': False,
            'concurrent_fragments': 1,  # Меньше параллельных фрагментов (стабильнее на медленном интернете)
            'http_chunk_size': 1048576,  # 1MB чанки
            'postprocessors': [],  # Отключаем постобработку
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writethumbnail': False,
        }
    
