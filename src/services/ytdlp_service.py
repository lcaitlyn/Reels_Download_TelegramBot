"""
YtDlpService - низкоуровневый сервис для работы с yt-dlp
Используется ТОЛЬКО в Worker для исполнения DownloadPlan
"""
import io
import os
import subprocess
import tempfile
import logging
import asyncio
import time
import threading
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
            import time
            start_time = time.time()
            logger.info(f"[extract_info] Начало получения информации: {url}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            
            elapsed_time = time.time() - start_time
            logger.info(f"[extract_info] Информация получена за {elapsed_time:.2f} сек: {url}")
            
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
        format_selector = download_plan.format_selector or ''
        
        # Для форматов с мержем (bestvideo+bestaudio) вывод в stdout ненадёжен — yt-dlp не мержит в pipe.
        # Итог: видео без звука. Пропускаем stream, worker использует download_to_file/pipelined (мерж в файл).
        if '+' in format_selector:
            logger.info(f"[YtDlpService] Формат с мержем (video+audio), пропускаю stream: {format_selector[:50]}...")
            return None
        
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

        # Добавляем cookies-файл, если указан (для Instagram и др.)
        cookiefile = ydl_opts.get('cookiefile')
        if cookiefile:
            cmd.extend(['--cookies', cookiefile])
        
        # Добавляем URL в конец
        cmd.append(url)
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0  # Небуферизованный вывод
            )
            
            # Читаем данные в buffer с логированием прогресса
            buffer = io.BytesIO()
            chunk_size = 8192  # 8KB чанки
            total_bytes = 0
            last_log_time = time.time()
            log_interval = 5.0  # Логируем прогресс каждые 5 секунд
            start_download_time = time.time()
            chunks_list = []  # Список чанков для thread-safe чтения
            
            logger.info(f"[YtDlpService] Начало чтения данных из yt-dlp процесса...")
            
            # Функция для чтения данных в отдельном потоке
            def read_stdout():
                nonlocal total_bytes, last_log_time
                try:
                    while True:
                        chunk = process.stdout.read(chunk_size)
                        if not chunk:
                            # Проверяем, завершился ли процесс
                            if process.poll() is not None:
                                break
                            # Если процесс еще работает, ждем немного
                            time.sleep(0.1)
                            continue
                        
                        chunks_list.append(chunk)
                        total_bytes += len(chunk)
                        
                        # Логируем прогресс каждые N секунд
                        current_time = time.time()
                        elapsed = current_time - start_download_time
                        if current_time - last_log_time >= log_interval:
                            mb_downloaded = total_bytes / (1024 * 1024)
                            logger.info(f"[YtDlpService] Прогресс скачивания: {mb_downloaded:.2f} MB за {elapsed:.1f} сек")
                            last_log_time = current_time
                except Exception as e:
                    logger.error(f"[YtDlpService] Ошибка при чтении данных: {e}")
            
            # Запускаем чтение в отдельном потоке
            read_thread = threading.Thread(target=read_stdout, daemon=True)
            read_thread.start()
            
            # Ждем завершения процесса с таймаутом
            max_download_time = 600  # Максимальное время скачивания: 10 минут
            try:
                process.wait(timeout=max_download_time)
            except subprocess.TimeoutExpired:
                logger.error(f"[YtDlpService] Таймаут скачивания ({max_download_time} сек), прерываем процесс")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                return None
            
            # Ждем завершения потока чтения
            read_thread.join(timeout=5)
            
            # Объединяем все чанки в buffer
            for chunk in chunks_list:
                buffer.write(chunk)
            
            logger.info(f"[YtDlpService] Завершено чтение данных: {total_bytes / (1024 * 1024):.2f} MB")
            
            if process.returncode != 0:
                error = process.stderr.read().decode('utf-8', errors='ignore')
                logger.error(f"[YtDlpService] Ошибка yt-dlp (код {process.returncode}): {error}")
                return None
            
            logger.info(f"[YtDlpService] Завершено чтение данных: {total_bytes / (1024 * 1024):.2f} MB")
            
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
        # Используем шаблон с %(ext)s, чтобы yt-dlp сам определил правильное расширение
        # Это важно для audio-only файлов (m4a, webm, opus и т.д.)
        ydl_opts['outtmpl'] = f"{tmp_path}.%(ext)s"
        # Важно: отключаем продолжение скачивания и частичные файлы
        ydl_opts['nopart'] = True  # Не создавать частичные файлы
        ydl_opts['continue_dl'] = False  # Не продолжать скачивание
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            logger.error(f"[YtDlpService] ❌ yt-dlp DownloadError при скачивании {url}: {error_msg}")
            
            # YouTube Shorts: один готовый поток без мержа (без ffmpeg)
            if download_plan.platform == 'youtube' and getattr(download_plan, 'quality', None) == 'shorts':
                logger.warning(f"[YtDlpService] Пробую альтернативные форматы для YouTube Shorts")
                alt_formats = ['best[height<=1280][ext=mp4]/best[height<=1280]/best', 'best[ext=mp4]/best', 'best']
                for alt_format in alt_formats:
                    logger.info(f"[YtDlpService] Пробую формат: {alt_format}")
                    for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                        candidate_path = f"{tmp_path}.{ext}"
                        if os.path.exists(candidate_path):
                            try:
                                os.remove(candidate_path)
                            except Exception:
                                pass
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                    ydl_opts['format'] = alt_format
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])
                        actual_file_path = None
                        for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                            candidate_path = f"{tmp_path}.{ext}"
                            if os.path.exists(candidate_path):
                                actual_file_path = candidate_path
                                break
                        if not actual_file_path and os.path.exists(tmp_path):
                            actual_file_path = tmp_path
                        file_size = os.path.getsize(actual_file_path) if actual_file_path and os.path.exists(actual_file_path) else 0
                        if file_size > 0:
                            logger.info(f"[YtDlpService] ✅ Shorts скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB")
                            tmp_path = actual_file_path
                            break
                    except Exception as alt_e:
                        logger.warning(f"[YtDlpService] Ошибка с форматом {alt_format}: {alt_e}")
                        continue
                else:
                    for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                        candidate_path = f"{tmp_path}.{ext}"
                        if os.path.exists(candidate_path):
                            try:
                                os.remove(candidate_path)
                            except Exception:
                                pass
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass
                    return None
            # Для Instagram пробуем альтернативные форматы
            elif download_plan.platform == 'instagram':
                logger.warning(f"[YtDlpService] Пробую альтернативные форматы для Instagram")
                alt_formats = ['best', 'worst', 'best[ext=mp4]', 'worst[ext=mp4]', 'bestvideo+bestaudio/best']
                
                for alt_format in alt_formats:
                    logger.info(f"[YtDlpService] Пробую альтернативный формат: {alt_format}")
                    # Удаляем файл перед каждой попыткой
                    for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                        candidate_path = f"{tmp_path}.{ext}"
                        if os.path.exists(candidate_path):
                            try:
                                os.remove(candidate_path)
                            except:
                                pass
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except:
                            pass
                    
                    ydl_opts['format'] = alt_format
                    
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])
                        
                        # Ищем файл с расширением
                        actual_file_path = None
                        for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                            candidate_path = f"{tmp_path}.{ext}"
                            if os.path.exists(candidate_path):
                                actual_file_path = candidate_path
                                break
                        if not actual_file_path and os.path.exists(tmp_path):
                            actual_file_path = tmp_path
                        
                        file_size = os.path.getsize(actual_file_path) if actual_file_path and os.path.exists(actual_file_path) else 0
                        if file_size > 0:
                            logger.info(f"[YtDlpService] ✅ Успешно скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB")
                            tmp_path = actual_file_path  # Обновляем путь для дальнейшей обработки
                            break
                    except Exception as e:
                        logger.warning(f"[YtDlpService] Ошибка при скачивании с форматом {alt_format}: {e}")
                        continue
                else:
                    # Все форматы не сработали
                    logger.error("[YtDlpService] ❌ Не удалось скачать видео ни с одним форматом")
                    # Удаляем все возможные файлы с расширениями
                    for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                        candidate_path = f"{tmp_path}.{ext}"
                        if os.path.exists(candidate_path):
                            try:
                                os.remove(candidate_path)
                            except:
                                pass
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except:
                            pass
                    return None
            else:
                # Для других платформ просто возвращаем ошибку
                # Удаляем все возможные файлы с расширениями
                for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                    candidate_path = f"{tmp_path}.{ext}"
                    if os.path.exists(candidate_path):
                        try:
                            os.remove(candidate_path)
                        except:
                            pass
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except:
                        pass
                return None
        except Exception as e:
            logger.error(f"[YtDlpService] ❌ Неожиданная ошибка при скачивании {url}: {e}", exc_info=True)
            # Удаляем все возможные файлы с расширениями
            for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                candidate_path = f"{tmp_path}.{ext}"
                if os.path.exists(candidate_path):
                    try:
                        os.remove(candidate_path)
                    except:
                        pass
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass
            return None
        
        # yt-dlp создал файл с расширением, определяем реальный путь
        # Ищем файл с расширением (yt-dlp добавил .%(ext)s к tmp_path)
        actual_file_path = None
        for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
            candidate_path = f"{tmp_path}.{ext}"
            if os.path.exists(candidate_path):
                actual_file_path = candidate_path
                break
        
        # Если не нашли файл с расширением, проверяем оригинальный путь
        if not actual_file_path:
            if os.path.exists(tmp_path):
                actual_file_path = tmp_path
            else:
                logger.error("[YtDlpService] Скачанный файл не найден")
                return None
        
        # Проверяем размер файла
        file_size = os.path.getsize(actual_file_path) if os.path.exists(actual_file_path) else 0
        
        if file_size == 0 and download_plan.platform == 'youtube' and getattr(download_plan, 'quality', None) == 'shorts':
            try:
                if actual_file_path != tmp_path:
                    os.remove(actual_file_path)
            except Exception:
                pass
            logger.warning("[YtDlpService] Shorts: файл пустой, пробую альтернативные форматы")
            for alt_format in ['best[ext=mp4]/best', 'best']:
                ydl_opts['format'] = alt_format
                for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                    p = f"{tmp_path}.{ext}"
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                except Exception:
                    continue
                for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                    candidate_path = f"{tmp_path}.{ext}"
                    if os.path.exists(candidate_path):
                        sz = os.path.getsize(candidate_path)
                        if sz > 0:
                            actual_file_path = candidate_path
                            file_size = sz
                            logger.info(f"[YtDlpService] ✅ Shorts скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB")
                            break
                if file_size > 0:
                    break
        if file_size == 0:
            logger.error("[YtDlpService] Скачанный файл пустой")
            try:
                if actual_file_path != tmp_path and os.path.exists(actual_file_path):
                    os.remove(actual_file_path)
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return None
        
        # Получаем реальное расширение из имени файла
        _, actual_ext = os.path.splitext(actual_file_path)
        actual_ext = actual_ext.lstrip('.') if actual_ext else 'mp4'
        
        # Получаем имя файла
        if download_plan.metadata:
            video_id = download_plan.metadata.get('id', 'video')
        else:
            # Извлекаем из video_id (формат: platform:video_id)
            parts = download_plan.video_id.split(':', 1)
            video_id = parts[1] if len(parts) > 1 else 'video'
        
        filename = f"{video_id}.{actual_ext}"
        
        logger.info(f"[YtDlpService] Видео скачано в файл: {actual_file_path} ({file_size / (1024 * 1024):.2f} MB, расширение: {actual_ext})")
        return (actual_file_path, file_size, filename)
    
    async def download_to_file_pipelined(
        self,
        download_plan: DownloadPlan,
        output_path: Optional[str] = None
    ) -> Optional[Tuple[str, int, str]]:
        """
        Скачать видео в файл с пайплайном через subprocess (быстрее для больших файлов)
        
        Использует subprocess напрямую вместо yt-dlp Python API для лучшей производительности.
        Это позволяет начать запись файла раньше и потенциально начать отправку параллельно.
        
        Args:
            download_plan: План скачивания с опциями yt-dlp
            output_path: Путь для сохранения файла (опционально)
            
        Returns:
            Tuple (путь к файлу, размер в байтах, имя файла) или None при ошибке
        """
        url = download_plan.url
        format_selector = download_plan.format_selector
        
        logger.info(f"[YtDlpService] Скачиваю в файл (pipelined): {url} (формат: {format_selector})")
        
        # Определяем путь к файлу
        if output_path:
            tmp_path = output_path
        else:
            # Создаем временный файл БЕЗ расширения - yt-dlp сам определит правильное расширение
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='', dir=self.download_dir)
            tmp_path = tmp_file.name
            tmp_file.close()
        
        # Удаляем файл, если он существует (и все возможные файлы с расширениями)
        for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
            candidate_path = f"{tmp_path}.{ext}"
            if os.path.exists(candidate_path):
                try:
                    os.remove(candidate_path)
                except Exception as e:
                    logger.warning(f"[YtDlpService] Не удалось удалить существующий файл {candidate_path}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.warning(f"[YtDlpService] Не удалось удалить существующий файл {tmp_path}: {e}")
        
        # Формируем команду yt-dlp с шаблоном %(ext)s для автоматического определения расширения
        cmd = ['yt-dlp', '-f', format_selector, '-o', f"{tmp_path}.%(ext)s"]
        
        # Добавляем опции из download_plan.ydl_opts
        ydl_opts = download_plan.ydl_opts.copy() if download_plan.ydl_opts else {}
        
        if ydl_opts.get('quiet') is not False:
            cmd.append('--quiet')

        if ydl_opts.get('no_warnings') is not False:
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

        # Добавляем cookies-файл, если указан (для Instagram и др.)
        cookiefile = ydl_opts.get('cookiefile')
        if cookiefile:
            cmd.extend(['--cookies', cookiefile])
        
        # Добавляем URL в конец
        cmd.append(url)
        
        try:
            # Запускаем процесс в отдельном потоке для неблокирующего выполнения
            loop = asyncio.get_event_loop()
            process = await loop.run_in_executor(
                None,
                lambda: subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            )
            
            # Ждем завершения процесса
            returncode = await loop.run_in_executor(None, process.wait)
            
            if returncode != 0:
                error = process.stderr.read().decode('utf-8', errors='ignore')
                logger.error(f"[YtDlpService] Ошибка yt-dlp (pipelined): {error}")
                
                # Для Instagram пробуем альтернативные форматы
                if download_plan.platform == 'instagram':
                    logger.warning(f"[YtDlpService] Пробую альтернативные форматы для Instagram (pipelined)")
                    alt_formats = ['best', 'worst', 'best[ext=mp4]', 'worst[ext=mp4]', 'bestvideo+bestaudio/best']
                    
                    for alt_format in alt_formats:
                        logger.info(f"[YtDlpService] Пробую альтернативный формат: {alt_format}")
                        # Удаляем все возможные файлы с расширениями
                        for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                            candidate_path = f"{tmp_path}.{ext}"
                            if os.path.exists(candidate_path):
                                try:
                                    os.remove(candidate_path)
                                except:
                                    pass
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except:
                                pass
                        
                        # Обновляем команду с новым форматом
                        cmd_alt = cmd.copy()
                        format_idx = cmd_alt.index('-f')
                        cmd_alt[format_idx + 1] = alt_format
                        
                        try:
                            process_alt = await loop.run_in_executor(
                                None,
                                lambda: subprocess.Popen(
                                    cmd_alt,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE
                                )
                            )
                            returncode_alt = await loop.run_in_executor(None, process_alt.wait)
                            
                            # Ищем файл с расширением
                            actual_file_path = None
                            for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                                candidate_path = f"{tmp_path}.{ext}"
                                if os.path.exists(candidate_path):
                                    actual_file_path = candidate_path
                                    break
                            if not actual_file_path and os.path.exists(tmp_path):
                                actual_file_path = tmp_path
                            
                            if actual_file_path:
                                file_size = os.path.getsize(actual_file_path)
                                if file_size > 0:
                                    logger.info(f"[YtDlpService] ✅ Успешно скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB")
                                    tmp_path = actual_file_path  # Обновляем путь
                                    break
                        except Exception as e:
                            logger.warning(f"[YtDlpService] Ошибка при скачивании с форматом {alt_format}: {e}")
                            continue
                    else:
                        logger.error("[YtDlpService] ❌ Не удалось скачать видео ни с одним форматом")
                        # Удаляем все возможные файлы с расширениями
                        for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                            candidate_path = f"{tmp_path}.{ext}"
                            if os.path.exists(candidate_path):
                                try:
                                    os.remove(candidate_path)
                                except:
                                    pass
                        if os.path.exists(tmp_path):
                            try:
                                os.remove(tmp_path)
                            except:
                                pass
                        return None
                else:
                    # Удаляем все возможные файлы с расширениями
                    for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                        candidate_path = f"{tmp_path}.{ext}"
                        if os.path.exists(candidate_path):
                            try:
                                os.remove(candidate_path)
                            except:
                                pass
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except:
                            pass
                    return None
            
            # yt-dlp создал файл с расширением, определяем реальный путь
            actual_file_path = None
            for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                candidate_path = f"{tmp_path}.{ext}"
                if os.path.exists(candidate_path):
                    actual_file_path = candidate_path
                    break
            
            # Если не нашли файл с расширением, проверяем оригинальный путь
            if not actual_file_path:
                if os.path.exists(tmp_path):
                    actual_file_path = tmp_path
                else:
                    logger.error("[YtDlpService] Скачанный файл не найден (pipelined)")
                    return None
            
            # Проверяем размер файла
            file_size = os.path.getsize(actual_file_path) if os.path.exists(actual_file_path) else 0
            
            if file_size == 0:
                logger.error("[YtDlpService] Скачанный файл пустой (pipelined)")
                try:
                    if actual_file_path != tmp_path:
                        os.remove(actual_file_path)
                    os.remove(tmp_path)
                except:
                    pass
                return None
            
            # Получаем реальное расширение из имени файла
            _, actual_ext = os.path.splitext(actual_file_path)
            actual_ext = actual_ext.lstrip('.') if actual_ext else 'mp4'
            
            # Получаем имя файла
            if download_plan.metadata:
                video_id = download_plan.metadata.get('id', 'video')
            else:
                parts = download_plan.video_id.split(':', 1)
                video_id = parts[1] if len(parts) > 1 else 'video'
            
            filename = f"{video_id}.{actual_ext}"
            
            logger.info(f"[YtDlpService] Видео скачано в файл (pipelined): {actual_file_path} ({file_size / (1024 * 1024):.2f} MB, расширение: {actual_ext})")
            return (actual_file_path, file_size, filename)
            
        except FileNotFoundError:
            logger.warning("[YtDlpService] yt-dlp не найден в PATH")
            return None
        except Exception as e:
            logger.error(f"[YtDlpService] Ошибка при pipelined скачивании: {e}", exc_info=True)
            # Удаляем все возможные файлы с расширениями
            for ext in ['mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3']:
                candidate_path = f"{tmp_path}.{ext}"
                if os.path.exists(candidate_path):
                    try:
                        os.remove(candidate_path)
                    except:
                        pass
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except:
                    pass
            return None
