"""
YtDlpService - низкоуровневый сервис для работы с yt-dlp
Используется ТОЛЬКО в Worker для исполнения DownloadPlan
"""
import io
import os
import subprocess
import tempfile
import logging
from typing import Optional, Dict, Any, Tuple
import yt_dlp

from src.models.download_plan import DownloadPlan

logger = logging.getLogger(__name__)


class YtDlpService:
    """
    Низкоуровневый сервис для работы с yt-dlp
    
    Ответственность:
    - Получение информации о видео через yt-dlp
    - Скачивание видео в поток (для маленьких файлов)
    - Скачивание видео в файл (для больших файлов)
    
    Используется ТОЛЬКО в Worker.
    НЕ знает о платформах, пользователях, Redis, Telegram.
    """
    
    def __init__(self, download_dir: str = "downloads", max_file_size_mb: float = 1000.0):
        """
        Args:
            download_dir: Директория для временных файлов
            max_file_size_mb: Максимальный размер файла в МБ
        """
        self.download_dir = download_dir
        self.max_file_size_mb = max_file_size_mb
        os.makedirs(download_dir, exist_ok=True)
    
    def get_info(self, url: str, ydl_opts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о видео через yt-dlp
        
        Args:
            url: URL видео
            ydl_opts: Опции для yt-dlp (опционально)
            
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
    
    def download_to_stream(
        self,
        download_plan: DownloadPlan
    ) -> Optional[Tuple[io.BytesIO, int, str]]:
        """
        Скачать видео в поток (для маленьких файлов <50MB)
        
        Использует subprocess для прямой передачи данных через pipe.
        Это быстрее и эффективнее для маленьких файлов.
        
        Args:
            download_plan: План скачивания с опциями yt-dlp
            
        Returns:
            Tuple (BytesIO, размер в байтах, имя файла) или None при ошибке
        """
        url = download_plan.url
        format_selector = download_plan.format_selector
        
        logger.info(f"[YtDlpService] Скачиваю в поток: {url} (формат: {format_selector})")
        
        # Формируем команду yt-dlp
        # Базовые опции для потокового скачивания
        cmd = ['yt-dlp', '-f', format_selector, '-o', '-']  # Вывод в stdout
        
        # Добавляем опции из download_plan.ydl_opts
        # Конвертируем опции yt-dlp в аргументы командной строки
        ydl_opts = download_plan.ydl_opts.copy() if download_plan.ydl_opts else {}
        
        # Обрабатываем специальные опции
        # quiet: по умолчанию True для потокового скачивания (меньше логов)
        if ydl_opts.get('quiet') is False:
            # Если явно указано quiet=False, не добавляем --quiet
            pass
        else:
            # По умолчанию используем --quiet для потокового скачивания
            cmd.append('--quiet')
        
        # no_warnings: по умолчанию True
        if ydl_opts.get('no_warnings') is False:
            pass  # Не добавляем --no-warnings
        else:
            cmd.append('--no-warnings')
        
        if ydl_opts.get('noplaylist'):
            cmd.append('--no-playlist')
        
        # Добавляем extractor_args для Instagram
        if 'extractor_args' in ydl_opts:
            extractor_args = ydl_opts['extractor_args']
            if 'instagram' in extractor_args:
                instagram_args = extractor_args['instagram']
                if instagram_args.get('webpage_download') is False:
                    cmd.extend(['--extractor-args', 'instagram:webpage_download=False'])
        
        # Добавляем user-agent если указан
        if ydl_opts.get('user_agent'):
            cmd.extend(['--user-agent', ydl_opts['user_agent']])
        
        # Добавляем URL в конец
        cmd.append(url)
        
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
                logger.error(f"[YtDlpService] Ошибка yt-dlp: {error}")
                return None
            
            buffer.seek(0)
            file_size = len(buffer.getvalue())
            
            # Получаем имя файла из метаданных или используем video_id
            if download_plan.metadata:
                video_id = download_plan.metadata.get('id', 'video')
                ext = download_plan.metadata.get('ext', 'mp4') or 'mp4'
            else:
                # Извлекаем из video_id (формат: platform:video_id)
                parts = download_plan.video_id.split(':', 1)
                video_id = parts[1] if len(parts) > 1 else 'video'
                ext = 'mp4'
            
            filename = f"{video_id}.{ext}"
            
            if file_size == 0:
                logger.error("[YtDlpService] Скачанный файл пустой")
                return None
            
            logger.info(f"[YtDlpService] Видео загружено в память: {file_size / (1024 * 1024):.2f} MB")
            return (buffer, file_size, filename)
            
        except FileNotFoundError:
            logger.warning("[YtDlpService] yt-dlp не найден в PATH, используем download_to_file")
            return None
        except Exception as e:
            logger.error(f"[YtDlpService] Ошибка при потоковом скачивании: {e}", exc_info=True)
            return None
    
    def download_to_file(
        self,
        download_plan: DownloadPlan,
        output_path: Optional[str] = None
    ) -> Optional[Tuple[str, int, str]]:
        """
        Скачать видео в файл (для больших файлов)
        
        Args:
            download_plan: План скачивания с опциями yt-dlp
            output_path: Путь для сохранения файла (опционально, если не указан создается временный)
            
        Returns:
            Tuple (путь к файлу, размер в байтах, имя файла) или None при ошибке
        """
        url = download_plan.url
        format_selector = download_plan.format_selector
        
        logger.info(f"[YtDlpService] Скачиваю в файл: {url} (формат: {format_selector})")
        
        # Определяем путь к файлу
        if output_path:
            tmp_path = output_path
        else:
            # Создаем временный файл
            if download_plan.metadata:
                ext = download_plan.metadata.get('ext', 'mp4') or 'mp4'
            else:
                ext = 'mp4'
            
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}', dir=self.download_dir)
            tmp_path = tmp_file.name
            tmp_file.close()
        
        # Удаляем файл, если он существует (чтобы yt-dlp не думал, что он уже скачан)
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                logger.debug(f"[YtDlpService] Удален существующий файл: {tmp_path}")
            except Exception as e:
                logger.warning(f"[YtDlpService] Не удалось удалить существующий файл {tmp_path}: {e}")
        
        # Формируем опции yt-dlp
        ydl_opts = download_plan.ydl_opts.copy()
        ydl_opts['format'] = format_selector
        ydl_opts['outtmpl'] = tmp_path
        # Важно: отключаем продолжение скачивания и частичные файлы
        ydl_opts['nopart'] = True  # Не создавать частичные файлы
        ydl_opts['continue_dl'] = False  # Не продолжать скачивание
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            logger.error(f"[YtDlpService] ❌ yt-dlp DownloadError при скачивании {url}: {error_msg}")
            
            # Для Instagram пробуем альтернативные форматы
            if download_plan.platform == 'instagram':
                logger.warning(f"[YtDlpService] Пробую альтернативные форматы для Instagram")
                alt_formats = ['best', 'worst', 'best[ext=mp4]', 'worst[ext=mp4]', 'bestvideo+bestaudio/best']
                
                for alt_format in alt_formats:
                    logger.info(f"[YtDlpService] Пробую альтернативный формат: {alt_format}")
                    # Удаляем файл перед каждой попыткой
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except:
                            pass
                    
                    ydl_opts['format'] = alt_format
                    
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])
                        
                        file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
                        if file_size > 0:
                            logger.info(f"[YtDlpService] ✅ Успешно скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB")
                            break
                    except Exception as e:
                        logger.warning(f"[YtDlpService] Ошибка при скачивании с форматом {alt_format}: {e}")
                        continue
                else:
                    # Все форматы не сработали
                    logger.error("[YtDlpService] ❌ Не удалось скачать видео ни с одним форматом")
                    try:
                        os.remove(tmp_path)
                    except:
                        pass
                    return None
            else:
                # Для других платформ просто возвращаем ошибку
                try:
                    os.remove(tmp_path)
                except:
                    pass
                return None
        except Exception as e:
            logger.error(f"[YtDlpService] ❌ Неожиданная ошибка при скачивании {url}: {e}", exc_info=True)
            try:
                os.remove(tmp_path)
            except:
                pass
            return None
        
        # Проверяем размер файла
        file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
        
        if file_size == 0:
            logger.error("[YtDlpService] Скачанный файл пустой")
            try:
                os.remove(tmp_path)
            except:
                pass
            return None
        
        # Получаем имя файла
        if download_plan.metadata:
            video_id = download_plan.metadata.get('id', 'video')
            ext = download_plan.metadata.get('ext', 'mp4') or 'mp4'
        else:
            # Извлекаем из video_id (формат: platform:video_id)
            parts = download_plan.video_id.split(':', 1)
            video_id = parts[1] if len(parts) > 1 else 'video'
            ext = 'mp4'
        
        filename = f"{video_id}.{ext}"
        
        logger.info(f"[YtDlpService] Видео скачано в файл: {tmp_path} ({file_size / (1024 * 1024):.2f} MB)")
        return (tmp_path, file_size, filename)
