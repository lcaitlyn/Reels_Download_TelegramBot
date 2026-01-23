"""
Модуль для скачивания видео через yt-dlp
"""
import os
import logging
import yt_dlp
from typing import Optional

from src.utils.utils import get_platform

logger = logging.getLogger(__name__)


class VideoDownloader:
    def __init__(self, download_dir: str = "downloads", compress_short_videos: bool = True, max_file_size_mb: float = 1000.0):
        """
        Инициализация VideoDownloader
        
        Args:
            download_dir: Директория для скачанных файлов
            compress_short_videos: Сжимать ли короткие видео (TikTok/Reels/Shorts)
            max_file_size_mb: Максимальный размер файла в МБ (по умолчанию 5 МБ)
        """
        self.download_dir = download_dir
        self.compress_short_videos = compress_short_videos
        self.max_file_size_mb = max_file_size_mb
        os.makedirs(download_dir, exist_ok=True)
    
    def _get_format_for_platform(self, platform: str) -> str:
        """
        Определение формата качества в зависимости от платформы
        Для коротких видео используем меньшее качество для быстрой загрузки
        При медленном интернете - самое худшее качество для максимальной скорости
        
        ВАЖНО: Используем только форматы с видео+аудио (не video+audio отдельно),
        чтобы не требовался ffmpeg для объединения
        
        Args:
            platform: Платформа (youtube, tiktok, instagram)
        """
        if not self.compress_short_videos:
            return 'best[ext=mp4]/best'
        
        # Для коротких видео (TikTok, Instagram Reels, YouTube Shorts) - самое худшее качество
        # Это максимально уменьшает размер файла и время скачивания
        if platform == 'instagram':
            # Для Instagram используем best качество, так как Instagram обычно предоставляет готовые mp4
            # Приоритет: best mp4 > best любой формат
            return 'best[ext=mp4]/best'
        elif platform == 'tiktok':
            # ВАЖНО: Используем только форматы, которые уже содержат видео+аудио
            # Не используем worstvideo+worstaudio, так как это требует ffmpeg
            # Приоритет: worst mp4 с аудио > worst любой формат с аудио > worst
            return 'worst[ext=mp4]/worst[ext=webm]/worst'
        elif platform == 'youtube':
            # Для YouTube используем более надежный формат (работает без JS runtime)
            # Приоритет: лучшее качество ≤360p > ≤240p > ≤144p > любое mp4
            # ВАЖНО: Используем только форматы с видео+аудио
            return 'best[height<=360][ext=mp4]/best[height<=240][ext=mp4]/best[height<=144][ext=mp4]/best[ext=mp4]/best'
        else:
            # Для неизвестных платформ - worst качество для скорости
            return 'worst[ext=mp4]/worst'
    
    def get_available_formats(self, url: str) -> Optional[dict]:
        """
        Получить доступные форматы для YouTube видео
        
        Args:
            url: URL YouTube видео
            
        Returns:
            Словарь с доступными форматами:
            {
                '480p': {'format_id': '...', 'filesize': ...},
                '720p': {'format_id': '...', 'filesize': ...},
                '1080p': {'format_id': '...', 'filesize': ...},
                'audio': {'format_id': '...', 'filesize': ...}
            }
            или None при ошибке
        """
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'listformats': True,
            }
            
            formats_dict = {}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                formats = info.get('formats', [])
                
                # Ищем видео форматы по разрешению
                video_formats = {}
                audio_formats = []
                
                for fmt in formats:
                    height = fmt.get('height')
                    vcodec = fmt.get('vcodec', 'none')
                    acodec = fmt.get('acodec', 'none')
                    format_id = fmt.get('format_id')
                    filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
                    ext = fmt.get('ext', 'mp4')
                    
                    # Аудио формат (только аудио, без видео)
                    if vcodec == 'none' and acodec != 'none' and ext in ['m4a', 'webm', 'mp3']:
                        audio_formats.append({
                            'format_id': format_id,
                            'filesize': filesize,
                            'ext': ext
                        })
                    
                    # Видео форматы (с видео кодеком)
                    # ВАЖНО: сохраняем информацию о наличии аудио
                    if vcodec != 'none' and height:
                        if height not in video_formats:
                            video_formats[height] = []
                        video_formats[height].append({
                            'format_id': format_id,
                            'filesize': filesize,
                            'ext': ext,
                            'height': height,
                            'has_audio': acodec != 'none'  # Есть ли аудио в этом формате
                        })
                
                # Выбираем лучшие форматы для каждого разрешения
                # ВАЖНО: выбираем только форматы с видео+аудио (не video only)
                target_heights = [480, 720, 1080]
                for target_height in target_heights:
                    # Ищем ближайшее разрешение
                    closest_height = None
                    min_diff = float('inf')
                    
                    for height in video_formats.keys():
                        if height and height <= target_height:
                            diff = target_height - height
                            if diff < min_diff:
                                min_diff = diff
                                closest_height = height
                    
                    if closest_height:
                        # Фильтруем форматы: выбираем только те, которые содержат и видео, и аудио
                        # Приоритет: форматы с аудио > video only
                        formats_with_audio = [f for f in video_formats[closest_height] if f.get('has_audio', False)]
                        
                        if formats_with_audio:
                            # Выбираем формат с наименьшим размером файла среди форматов с аудио
                            best_format = min(formats_with_audio, 
                                            key=lambda x: x['filesize'] if x['filesize'] else float('inf'))
                            
                            height_label = f"{closest_height}p"
                            if height_label not in formats_dict:
                                formats_dict[height_label] = {
                                    'format_id': best_format['format_id'],
                                    'filesize': best_format['filesize'],
                                    'ext': best_format['ext'],
                                    'height': closest_height
                                }
                        else:
                            # Если нет форматов с аудио, используем video only + bestaudio
                            # Но это менее предпочтительно, поэтому выбираем наименьший video only
                            video_only_formats = [f for f in video_formats[closest_height] if not f.get('has_audio', False)]
                            if video_only_formats:
                                best_format = min(video_only_formats, 
                                                key=lambda x: x['filesize'] if x['filesize'] else float('inf'))
                                
                                height_label = f"{closest_height}p"
                                if height_label not in formats_dict:
                                    # Сохраняем format_id, но при скачивании добавим аудио
                                    formats_dict[height_label] = {
                                        'format_id': best_format['format_id'],
                                        'filesize': best_format['filesize'],
                                        'ext': best_format['ext'],
                                        'height': closest_height,
                                        'needs_audio': True  # Флаг, что нужно добавить аудио
                                    }
                
                # Выбираем лучший аудио формат (лучшее качество, не наименьший размер)
                # Сортируем по размеру файла в обратном порядке (больше = лучше качество)
                if audio_formats:
                    # Фильтруем только качественные аудио форматы (medium, high)
                    # Исключаем low качество (49k, 53k)
                    quality_audio = [f for f in audio_formats if f.get('filesize', 0) > 1000000]  # > 1MB обычно medium+
                    
                    if quality_audio:
                        # Выбираем лучшее качество (наибольший размер = лучшее качество)
                        best_audio = max(quality_audio, 
                                        key=lambda x: x['filesize'] if x['filesize'] else 0)
                    else:
                        # Если нет качественных, берем лучшее из доступных
                        best_audio = max(audio_formats, 
                                        key=lambda x: x['filesize'] if x['filesize'] else 0)
                    
                    formats_dict['audio'] = {
                        'format_id': best_audio['format_id'],
                        'filesize': best_audio['filesize'],
                        'ext': best_audio['ext']
                    }
                
                logger.info(f"Доступные форматы для {url}: {list(formats_dict.keys())}")
                return formats_dict if formats_dict else None
                
        except Exception as e:
            logger.error(f"Ошибка при получении форматов для {url}: {e}", exc_info=True)
            return None
    
    def download_video_with_format(self, url: str, format_id: str) -> Optional[str]:
        """
        Скачать видео с конкретным форматом (старый метод для обратной совместимости)
        
        Args:
            url: URL видео
            format_id: ID формата из yt-dlp
            
        Returns:
            Путь к скачанному файлу или None
        """
        # Проверяем, является ли format_id форматом "video only"
        # Если да, то добавляем аудио дорожку
        format_selector = format_id
        
        # Для YouTube: если формат video only (например, "135"), добавляем лучший аудио
        # yt-dlp автоматически объединит video + audio
        try:
            # Проверяем, есть ли в формате аудио
            # Если format_id это просто число (video only), добавляем bestaudio
            if format_id.isdigit() or format_id.startswith(('135', '136', '137', '160', '133', '134')):
                # Это video only формат, добавляем аудио
                format_selector = f"{format_id}+bestaudio/best"
                logger.info(f"Добавляю аудио дорожку к формату {format_id}: {format_selector}")
        except:
            pass
        
        result = self._download_with_format(url, get_platform(url), format_selector)
        if result:
            file_path, file_size_mb = result
            if file_size_mb > self.max_file_size_mb:
                logger.error(f"Файл {file_size_mb:.2f} МБ превышает лимит {self.max_file_size_mb} МБ")
                try:
                    os.remove(file_path)
                except:
                    pass
                return None
            return file_path
        return None
    
    def download_video_stream(self, url: str, format_id: str = None) -> Optional[tuple]:
        """
        Скачать видео в поток (stream) для прямой передачи в Telegram
        Оптимизированная версия: для маленьких файлов (<50MB) - в память, для больших - временный файл
        
        Args:
            url: URL видео
            format_id: ID формата из yt-dlp (опционально)
            
        Returns:
            Tuple (io.BytesIO или путь к файлу, размер в байтах, имя файла) или None
            - Для маленьких файлов (<50MB): (BytesIO, размер, имя)
            - Для больших файлов: (путь к временному файлу, размер, имя)
        """
        import io
        import tempfile
        import subprocess
        
        try:
            platform = get_platform(url)
            
            # Формируем format_selector
            if format_id:
                format_selector = format_id
                # Проверяем, является ли format_id форматом "video only"
                try:
                    if format_id.isdigit() or format_id.startswith(('135', '136', '137', '160', '133', '134')):
                        format_selector = f"{format_id}+bestaudio/best"
                        logger.info(f"Добавляю аудио дорожку к формату {format_id}: {format_selector}")
                except:
                    pass
            else:
                format_selector = self._get_format_for_platform(platform)
            
            logger.info(f"Начинаю потоковое скачивание: {url} (формат: {format_selector})")
            
            # Получаем информацию о видео для определения размера
            info_opts = {'quiet': True, 'extract_flat': False}
            
            # Специальные опции для Instagram
            if platform == 'instagram':
                info_opts['quiet'] = False  # Включаем вывод для отладки
                info_opts['no_warnings'] = False
                try:
                    info_opts['extractor_args'] = {'instagram': {'webpage_download': False}}
                except:
                    pass
            
            try:
                with yt_dlp.YoutubeDL(info_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    video_id = info.get('id', 'video')
                    filesize = info.get('filesize') or info.get('filesize_approx')
                    ext = info.get('ext', 'mp4') or 'mp4'
                    
                    # Проверяем доступность видео для Instagram
                    if platform == 'instagram':
                        if not info or not info.get('url'):
                            logger.error("[Instagram] ❌ Не удалось получить информацию о видео. Возможные причины:")
                            logger.error("  - Видео приватное или требует авторизацию")
                            logger.error("  - Видео удалено или недоступно")
                            logger.error("  - Instagram заблокировал доступ")
                            return None
            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                logger.error(f"❌ yt-dlp DownloadError при получении информации о видео: {error_msg}")
                if platform == 'instagram':
                    if 'login' in error_msg.lower() or 'private' in error_msg.lower():
                        logger.error("[Instagram] ⚠️ Видео приватное или требует авторизацию")
                    elif 'unavailable' in error_msg.lower() or 'not found' in error_msg.lower():
                        logger.error("[Instagram] ⚠️ Видео недоступно или удалено")
                return None
            except Exception as e:
                logger.error(f"❌ Ошибка при получении информации о видео: {e}", exc_info=True)
                return None
                
                if filesize:
                    filesize_mb = filesize / (1024 * 1024)
                    logger.info(f"Размер видео: {filesize_mb:.2f} MB")
                    
                    if filesize_mb > self.max_file_size_mb:
                        logger.error(f"Размер файла {filesize_mb:.2f} МБ превышает лимит {self.max_file_size_mb} МБ")
                        return None
                    
                    # Для маленьких файлов (<50MB) - используем subprocess с pipe для прямой передачи в память
                    if filesize_mb < 50:
                        logger.info(f"Скачиваю маленький файл ({filesize_mb:.2f} MB) напрямую в память")
                        
                        # Используем subprocess для получения данных через pipe
                        # yt-dlp с опцией -o - выводит в stdout
                        cmd = [
                            'yt-dlp',
                            '-f', format_selector,
                            '-o', '-',  # Вывод в stdout
                            '--no-warnings',
                            '--quiet',
                            url
                        ]
                        
                        try:
                            process = subprocess.Popen(
                                cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                bufsize=0  # Небуферизованный вывод
                            )
                            
                            # Читаем данные в buffer
                            buffer = io.BytesIO()
                            chunk_size = 8192  # 8KB чанки
                            
                            while True:
                                chunk = process.stdout.read(chunk_size)
                                if not chunk:
                                    break
                                buffer.write(chunk)
                            
                            process.wait()
                            
                            if process.returncode != 0:
                                error = process.stderr.read().decode('utf-8', errors='ignore')
                                logger.error(f"Ошибка yt-dlp: {error}")
                                return None
                            
                            buffer.seek(0)
                            file_size = len(buffer.getvalue())
                            filename = f"{video_id}.{ext}"
                            
                            if file_size == 0:
                                logger.error("Скачанный файл пустой")
                                return None
                            
                            logger.info(f"Видео загружено в память: {file_size / (1024 * 1024):.2f} MB")
                            return (buffer, file_size, filename)
                            
                        except FileNotFoundError:
                            logger.warning("yt-dlp не найден в PATH, используем временный файл")
                            # Fallback на временный файл
                        except Exception as e:
                            logger.warning(f"Ошибка при потоковом скачивании в память: {e}, используем временный файл")
                    
                    # Для больших файлов или если subprocess не сработал - используем временный файл
                    # Используем NamedTemporaryFile с delete=False, чтобы контролировать удаление
                    logger.info(f"Скачиваю файл ({filesize_mb:.2f} MB) во временный файл")
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}', dir=self.download_dir) as tmp_file:
                        tmp_path = tmp_file.name
                    
                    ydl_opts = {
                        'format': format_selector,
                        'outtmpl': tmp_path,
                        'quiet': False,  # Включаем вывод для отладки
                        'no_warnings': False,
                        'noplaylist': True,
                        'extract_flat': False,
                        'postprocessors': [],  # Отключаем постобработку (не требуется ffmpeg)
                        'writesubtitles': False,
                        'writeautomaticsub': False,
                        'writethumbnail': False,
                    }
                    
                    # Специальные опции для Instagram
                    if platform == 'instagram':
                        ydl_opts['quiet'] = False  # Включаем вывод для отладки Instagram
                        ydl_opts['no_warnings'] = False  # Показываем предупреждения
                        # Instagram может требовать дополнительные опции
                        try:
                            ydl_opts['extractor_args'] = {'instagram': {'webpage_download': False}}
                        except:
                            pass
                        # Добавляем опции для обхода ограничений Instagram
                        ydl_opts['cookiefile'] = None  # Можно указать путь к cookies файлу
                        ydl_opts['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            # Логируем детальную информацию для Instagram
                            if platform == 'instagram':
                                logger.info(f"[Instagram] Пытаюсь скачать: {url}")
                                logger.info(f"[Instagram] Опции yt-dlp: {ydl_opts}")
                            
                            ydl.download([url])
                    except yt_dlp.utils.DownloadError as e:
                        error_msg = str(e)
                        logger.error(f"❌ yt-dlp DownloadError при скачивании {url}: {error_msg}")
                        if platform == 'instagram':
                            logger.error(f"[Instagram] Детали ошибки: {error_msg}")
                            # Instagram часто требует аутентификацию
                            if 'login' in error_msg.lower() or 'private' in error_msg.lower():
                                logger.error("[Instagram] ⚠️ Видео может быть приватным или требовать авторизацию")
                    except Exception as e:
                        logger.error(f"❌ Неожиданная ошибка при скачивании {url}: {e}", exc_info=True)
                        # Пробуем альтернативный формат
                        pass
                    
                    file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                    filename = f"{video_id}.{ext}"
                    
                    if file_size == 0:
                        logger.warning(f"Скачанный файл пустой, пробую альтернативные форматы")
                        # Пробуем несколько альтернативных форматов
                        alt_formats = ['best', 'worst', 'best[ext=mp4]', 'worst[ext=mp4]', 'bestvideo+bestaudio/best']
                        
                        for alt_format in alt_formats:
                            logger.info(f"Пробую альтернативный формат: {alt_format}")
                            ydl_opts['format'] = alt_format
                            
                            try:
                                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                                    ydl.download([url])
                                
                                file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                                
                                if file_size > 0:
                                    logger.info(f"✅ Успешно скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB")
                                    break
                            except Exception as e:
                                logger.warning(f"Ошибка при скачивании с форматом {alt_format}: {e}")
                                continue
                        
                        if file_size == 0:
                            logger.error("❌ Не удалось скачать видео ни с одним форматом")
                            try:
                                os.remove(tmp_path)
                            except:
                                pass
                            return None
                    
                    logger.info(f"Видео скачано во временный файл: {file_size / (1024 * 1024):.2f} MB")
                    return (tmp_path, file_size, filename)
                else:
                    # Размер неизвестен - используем временный файл
                    logger.info("Размер видео неизвестен, использую временный файл")
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}', dir=self.download_dir) as tmp_file:
                        tmp_path = tmp_file.name
                    
                    ydl_opts = {
                        'format': format_selector,
                        'outtmpl': tmp_path,
                        'quiet': False,  # Включаем вывод для отладки
                        'no_warnings': False,
                        'noplaylist': True,
                        'extract_flat': False,
                        'postprocessors': [],  # Отключаем постобработку (не требуется ffmpeg)
                        'writesubtitles': False,
                        'writeautomaticsub': False,
                        'writethumbnail': False,
                    }
                    
                    # Специальные опции для Instagram
                    if platform == 'instagram':
                        ydl_opts['quiet'] = False  # Включаем вывод для отладки Instagram
                        ydl_opts['no_warnings'] = False  # Показываем предупреждения
                        try:
                            ydl_opts['extractor_args'] = {'instagram': {'webpage_download': False}}
                        except:
                            pass
                        # Добавляем опции для обхода ограничений Instagram
                        ydl_opts['cookiefile'] = None  # Можно указать путь к cookies файлу
                        ydl_opts['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            # Логируем детальную информацию для Instagram
                            if platform == 'instagram':
                                logger.info(f"[Instagram] Пытаюсь скачать: {url}")
                            
                            ydl.download([url])
                    except yt_dlp.utils.DownloadError as e:
                        error_msg = str(e)
                        logger.error(f"❌ yt-dlp DownloadError при скачивании {url}: {error_msg}")
                        if platform == 'instagram':
                            logger.error(f"[Instagram] Детали ошибки: {error_msg}")
                            # Instagram часто требует аутентификацию
                            if 'login' in error_msg.lower() or 'private' in error_msg.lower():
                                logger.error("[Instagram] ⚠️ Видео может быть приватным или требовать авторизацию")
                    except Exception as e:
                        logger.error(f"❌ Неожиданная ошибка при скачивании {url}: {e}", exc_info=True)
                        # Пробуем альтернативный формат
                        pass
                    
                    file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                    filename = f"{video_id}.{ext}"
                    
                    if file_size == 0:
                        logger.warning(f"Скачанный файл пустой, пробую альтернативные форматы")
                        # Пробуем несколько альтернативных форматов
                        alt_formats = ['best', 'worst', 'best[ext=mp4]', 'worst[ext=mp4]', 'bestvideo+bestaudio/best']
                        
                        for alt_format in alt_formats:
                            logger.info(f"Пробую альтернативный формат: {alt_format}")
                            ydl_opts['format'] = alt_format
                            
                            try:
                                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                                    ydl.download([url])
                                
                                file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                                
                                if file_size > 0:
                                    logger.info(f"✅ Успешно скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB")
                                    break
                            except yt_dlp.utils.DownloadError as e:
                                error_msg = str(e)
                                logger.warning(f"❌ yt-dlp DownloadError с форматом {alt_format}: {error_msg}")
                                if platform == 'instagram' and ('login' in error_msg.lower() or 'private' in error_msg.lower()):
                                    logger.error("[Instagram] ⚠️ Видео приватное или требует авторизацию - прекращаю попытки")
                                    break
                            except Exception as e:
                                logger.warning(f"Ошибка при скачивании с форматом {alt_format}: {e}")
                                continue
                        
                        if file_size == 0:
                            logger.error("❌ Не удалось скачать видео ни с одним форматом")
                            if platform == 'instagram':
                                logger.error("[Instagram] Возможные причины:")
                                logger.error("  - Видео приватное или требует авторизацию")
                                logger.error("  - Видео удалено или недоступно")
                                logger.error("  - Instagram заблокировал доступ (нужны cookies)")
                            try:
                                os.remove(tmp_path)
                            except:
                                pass
                            return None
                    
                    logger.info(f"Видео скачано во временный файл (размер неизвестен): {file_size / (1024 * 1024):.2f} MB")
                    return (tmp_path, file_size, filename)
                        
        except Exception as e:
            logger.error(f"Ошибка при потоковом скачивании {url}: {e}", exc_info=True)
            return None
    
    def get_video_id(self, url: str) -> Optional[str]:
        """
        Получить канонический ID видео через yt-dlp extractor
        Используется для нормализации URL и предотвращения дубликатов в кэше
        Например: instagram.com/reels/123 и instagram.com/p/123 -> один ID
        
        Args:
            url: URL видео
            
        Returns:
            Канонический идентификатор в формате "platform:video_id" или None
        """
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                video_id = info.get('id')
                platform = info.get('extractor_key', 'unknown').lower()
                
                if video_id and platform:
                    # Возвращаем в формате "platform:video_id" для уникальности (основной формат в БД)
                    canonical_id = f"{platform}:{video_id}"
                    logger.info(f"Канонический ID для {url}: {canonical_id}")
                    return canonical_id
                    
        except Exception as e:
            logger.warning(f"Не удалось получить канонический ID для {url}: {e}")
        
        return None
    
    def download_video(self, url: str) -> Optional[str]:
        """
        Скачать видео по URL с ограничением размера файла
        Возвращает путь к скачанному файлу или None при ошибке
        
        Args:
            url: URL видео для скачивания
            
        Returns:
            Путь к скачанному файлу или None
        """
        platform = get_platform(url)
        format_selector = self._get_format_for_platform(platform)
        
        result = self._download_with_format(url, platform, format_selector)
        if result:
            file_path, file_size_mb = result
            # Проверяем размер файла после скачивания
            if file_size_mb > self.max_file_size_mb:
                logger.error(f"Файл {file_size_mb:.2f} МБ превышает лимит {self.max_file_size_mb} МБ")
                try:
                    os.remove(file_path)
                except:
                    pass
                return None
            return file_path
        
        return None
    
    def _download_with_format(self, url: str, platform: str, format_selector: str) -> Optional[tuple]:
        """
        Внутренний метод для скачивания с конкретным форматом
        
        Returns:
            Tuple (путь к файлу, размер в МБ) или None
        """
        
        ydl_opts = {
            'format': format_selector,
            'outtmpl': os.path.join(self.download_dir, '%(id)s.%(ext)s'),
            'quiet': True,  # Убираем лишний вывод
            'no_warnings': False,
            'noplaylist': True,  # Не скачивать плейлисты
            'extract_flat': False,
            # Опции для ускорения при медленном интернете
            'concurrent_fragments': 1,  # Меньше параллельных фрагментов (стабильнее на медленном интернете)
            'http_chunk_size': 1048576,  # 1MB чанки (меньше для медленного интернета)
            # Опции для уменьшения размера файла
            'postprocessors': [],  # Отключаем постобработку (экономит время и место)
            'writesubtitles': False,  # Не скачивать субтитры
            'writeautomaticsub': False,  # Не скачивать автосубтитры
            'writethumbnail': False,  # Не скачивать миниатюры
        }
        
        logger.info(f"Начинаю скачивание: {url} (платформа: {platform}, формат: {format_selector})")
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Получаем информацию о видео (без скачивания)
                info = ydl.extract_info(url, download=False)
                video_id = info.get('id', 'video')
                duration = info.get('duration', 0)
                
                # Проверяем размер выбранного формата ДО скачивания (если известен)
                filesize = info.get('filesize') or info.get('filesize_approx')
                if filesize:
                    filesize_mb = filesize / (1024 * 1024)
                    logger.info(f"Информация о видео: ID={video_id}, длительность={duration}с, размер={filesize_mb:.2f} МБ")
                    
                    # Если размер превышает лимит - не скачиваем, возвращаем ошибку
                    if filesize_mb > self.max_file_size_mb:
                        logger.error(f"Размер файла {filesize_mb:.2f} МБ превышает лимит {self.max_file_size_mb} МБ")
                        return None
                else:
                    logger.info(f"Информация о видео: ID={video_id}, длительность={duration}с (размер неизвестен)")
                
                # Скачиваем видео
                ydl.download([url])
                
                logger.info(f"Видео скачано, ищу файл: {video_id}")
                
                # Находим скачанный файл
                # yt-dlp может скачать в разных форматах
                for ext in ['mp4', 'webm', 'mkv', 'm4a']:
                    file_path = os.path.join(self.download_dir, f"{video_id}.{ext}")
                    if os.path.exists(file_path):
                        file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
                        # Проверяем, что файл не пустой
                        if file_size == 0:
                            logger.warning(f"Файл пустой, пропускаю: {file_path}")
                            try:
                                os.remove(file_path)
                            except:
                                pass
                            continue
                        logger.info(f"Файл найден: {file_path} ({file_size:.2f} MB)")
                        return (file_path, file_size)
                
                # Если не нашли по ID, ищем последний измененный файл
                logger.warning(f"Файл не найден по ID {video_id}, ищу последний файл")
                files = [
                    os.path.join(self.download_dir, f)
                    for f in os.listdir(self.download_dir)
                    if os.path.isfile(os.path.join(self.download_dir, f))
                    and not f.endswith('.part')  # Пропускаем частично скачанные файлы
                ]
                if files:
                    latest_file = max(files, key=os.path.getmtime)
                    # Проверяем, что файл не пустой
                    file_size = os.path.getsize(latest_file) / (1024 * 1024)  # MB
                    if file_size == 0:
                        logger.error(f"Последний файл тоже пустой: {latest_file}")
                        try:
                            os.remove(latest_file)
                        except:
                            pass
                        return None
                    logger.info(f"Использую последний файл: {latest_file} ({file_size:.2f} MB)")
                    return (latest_file, file_size)
                
                logger.error(f"Файл не найден после скачивания: {url}")
                return None
                
        except yt_dlp.utils.DownloadError as e:
            logger.error(f"Ошибка скачивания {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка при скачивании {url}: {e}", exc_info=True)
            return None
