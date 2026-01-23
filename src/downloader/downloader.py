"""
DEPRECATED: Этот модуль будет удален после рефакторинга.
Используйте DownloadManager для координации и YtDlpService для работы с yt-dlp.

Общий сервис для работы с yt-dlp
Не содержит специфичной логики платформ - только базовые методы работы с yt-dlp
"""
import os
import logging
import yt_dlp
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class VideoDownloader:
    """
    DEPRECATED: Используйте DownloadManager для координации.
    Методы get_video_info(), get_available_formats(), download_with_ydl_opts() 
    будут перенесены в YtDlpService.
    """
    """
    Общий сервис для работы с yt-dlp
    Предоставляет базовые методы для получения информации и скачивания видео
    Специфичная логика платформ должна быть в сервисах (InstagramService, TikTokService, etc.)
    """
    
    def __init__(self, download_dir: str = "downloads", max_file_size_mb: float = 1000.0):
        """
        Инициализация VideoDownloader
        
        Args:
            download_dir: Директория для скачанных файлов
            max_file_size_mb: Максимальный размер файла в МБ
        """
        self.download_dir = download_dir
        self.max_file_size_mb = max_file_size_mb
        os.makedirs(download_dir, exist_ok=True)
    
    def get_video_info(self, url: str, ydl_opts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        DEPRECATED: Будет перенесен в YtDlpService
        """
        """
        Получить информацию о видео через yt-dlp
        
        Args:
            url: URL видео
            ydl_opts: Дополнительные опции для yt-dlp (опционально)
            
        Returns:
            Словарь с информацией о видео или None при ошибке
        """
        if ydl_opts is None:
            ydl_opts = {'quiet': True, 'extract_flat': False}
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except Exception as e:
            logger.error(f"Ошибка при получении информации о видео {url}: {e}", exc_info=True)
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
    
    def get_available_formats(self, url: str, ydl_opts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        DEPRECATED: Будет перенесен в YtDlpService или PlatformService
        """
        """
        Получить доступные форматы для видео
        
        Args:
            url: URL видео
            ydl_opts: Дополнительные опции для yt-dlp (опционально)
            
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
        if ydl_opts is None:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'listformats': True,
            }
        
        try:
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
    
    def download_with_ydl_opts(
        self,
        url: str,
        ydl_opts: Dict[str, Any],
        output_path: Optional[str] = None
    ) -> Optional[tuple]:
        """
        DEPRECATED: Будет перенесен в YtDlpService
        """
        """
        Скачать видео с заданными опциями yt-dlp
        
        Args:
            url: URL видео
            ydl_opts: Опции для yt-dlp (должны содержать 'format' и 'outtmpl')
            output_path: Путь для сохранения файла (опционально, если не указан в ydl_opts)
            
        Returns:
            Tuple (путь к файлу, размер в МБ) или None при ошибке
        """
        try:
            # Если output_path указан, но не в ydl_opts - добавляем
            if output_path and 'outtmpl' not in ydl_opts:
                ydl_opts['outtmpl'] = output_path
            
            logger.info(f"Начинаю скачивание: {url} с опциями: {ydl_opts.get('format', 'default')}")
            
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
                
                # Определяем путь к файлу
                if output_path:
                    file_path = output_path
                elif 'outtmpl' in ydl_opts:
                    # Парсим outtmpl для получения пути
                    outtmpl = ydl_opts['outtmpl']
                    if '%(id)s' in outtmpl and '%(ext)s' in outtmpl:
                        ext = info.get('ext', 'mp4') or 'mp4'
                        file_path = outtmpl.replace('%(id)s', video_id).replace('%(ext)s', ext)
                    else:
                        file_path = outtmpl
                else:
                    # Ищем файл по ID в download_dir
                    file_path = None
                    for ext in ['mp4', 'webm', 'mkv', 'm4a']:
                        potential_path = os.path.join(self.download_dir, f"{video_id}.{ext}")
                        if os.path.exists(potential_path):
                            file_path = potential_path
                            break
                
                if file_path and os.path.exists(file_path):
                    file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
                    # Проверяем, что файл не пустой
                    if file_size == 0:
                        logger.warning(f"Файл пустой: {file_path}")
                        try:
                            os.remove(file_path)
                        except:
                            pass
                        return None
                    logger.info(f"Файл найден: {file_path} ({file_size:.2f} MB)")
                    return (file_path, file_size)
                
                # Если не нашли по пути, ищем последний измененный файл
                logger.warning(f"Файл не найден по пути {file_path}, ищу последний файл")
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
