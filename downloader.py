"""
Модуль для скачивания видео через yt-dlp
"""
import os
import logging
import yt_dlp
from typing import Optional

from utils import get_platform

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
        
        Args:
            platform: Платформа (youtube, tiktok, instagram)
        """
        if not self.compress_short_videos:
            return 'best[ext=mp4]/best'
        
        # Для коротких видео (TikTok, Instagram Reels, YouTube Shorts) - самое худшее качество
        # Это максимально уменьшает размер файла и время скачивания
        if platform in ['tiktok', 'instagram']:
            # Самый быстрый вариант - worst качество (минимальный размер файла)
            # Приоритет: worst mp4 > worst любой формат
            return 'worst[ext=mp4]/worstvideo[ext=mp4]+worstaudio/worst'
        elif platform == 'youtube':
            # Для YouTube используем более надежный формат (работает без JS runtime)
            # Приоритет: лучшее качество ≤360p > ≤240p > ≤144p > любое mp4
            return 'best[height<=360][ext=mp4]/best[height<=240][ext=mp4]/best[height<=144][ext=mp4]/best[ext=mp4]/best'
        else:
            # Для неизвестных платформ - worst качество для скорости
            return 'worst[ext=mp4]/worst'
    
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
