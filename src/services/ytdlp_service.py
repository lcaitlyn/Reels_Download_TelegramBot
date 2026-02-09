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

# TODO требуется рефактор
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
    
    def __init__(self, download_dir: str = "downloads", max_file_size_mb: Optional[float] = None):
        """
        Args:
            download_dir: Директория для временных файлов
            max_file_size_mb: Максимальный размер файла в МБ (если None — из конфига/MAX_FILE_SIZE_MB)
        """
        self.download_dir = download_dir
        if max_file_size_mb is None:
            from src.config import get_max_file_size_mb
            max_file_size_mb = get_max_file_size_mb()
        self.max_file_size_mb = max_file_size_mb
        os.makedirs(download_dir, exist_ok=True)
        # Расширения, которые мы считаем потенциальными выходными файлами yt-dlp
        self._video_audio_exts = ('mp4', 'webm', 'm4a', 'opus', 'mkv', 'mp3')

    # === Вспомогательные методы для очистки и поиска файлов ===

    def _cleanup_paths(self, *paths: str) -> None:
        """Удалить указанные файлы, тихо игнорируя ошибки."""
        for path in paths:
            if not path:
                continue
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception as e:
                    logger.warning(f"[YtDlpService] Не удалось удалить файл {path}: {e}")

    def _cleanup_temp_family(self, base_path: str) -> None:
        """Удалить базовый временный файл и все варианты с расширениями."""
        for ext in self._video_audio_exts:
            self._cleanup_paths(f"{base_path}.{ext}")
        self._cleanup_paths(base_path)

    def _exceeds_size_limit(self, file_size_bytes: int) -> bool:
        """Проверить, превышает ли размер файла лимит (в МБ)."""
        if file_size_bytes <= 0:
            return False
        limit_bytes = self.max_file_size_mb * (1024 * 1024)
        return file_size_bytes > limit_bytes

    def _create_tmp_path(
        self,
        download_plan: DownloadPlan,
        output_path: Optional[str],
        *,
        with_extension: bool,
    ) -> str:
        """
        Создать (или использовать переданный) временный путь для скачивания.
        
        Args:
            download_plan: План скачивания (нужен для metadata.ext при with_extension=True)
            output_path: Явно заданный путь (если есть — просто возвращаем его)
            with_extension: Если True — создаём файл с расширением (используется обычным download_to_file),
                            если False — без расширения (pipelined-режим сам добавляет %(ext)s)
        """
        if output_path:
            return output_path

        if with_extension:
            if download_plan.metadata:
                ext = download_plan.metadata.get('ext', 'mp4') or 'mp4'
            else:
                ext = 'mp4'
            suffix = f'.{ext}'
        else:
            suffix = ''

        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=self.download_dir)
        tmp_path = tmp_file.name
        tmp_file.close()
        return tmp_path

    def _build_filename(
        self,
        download_plan: DownloadPlan,
        actual_file_path: str,
    ) -> tuple[str, str]:
        """
        Построить имя файла и расширение на основе пути и DownloadPlan.
        
        Returns:
            (filename, actual_ext)
        """
        _, actual_ext = os.path.splitext(actual_file_path)
        actual_ext = actual_ext.lstrip('.') if actual_ext else 'mp4'

        if download_plan.metadata:
            video_id = download_plan.metadata.get('id', 'video')
        else:
            # Извлекаем из video_id (формат: platform:video_id)
            parts = download_plan.video_id.split(':', 1)
            video_id = parts[1] if len(parts) > 1 else 'video'

        filename = f"{video_id}.{actual_ext}"
        return filename, actual_ext

    def _find_output_file(self, base_path: str) -> Optional[str]:
        """
        Найти фактический файл, созданный yt-dlp.
        Сначала пробуем base_path.ext, затем сам base_path.
        """
        for ext in self._video_audio_exts:
            candidate = f"{base_path}.{ext}"
            if os.path.exists(candidate):
                return candidate
        return base_path if os.path.exists(base_path) else None

    # === Платформенные ретраи ===

    def _retry_youtube_shorts_download(
        self,
        url: str,
        tmp_path: str,
        ydl_opts: Dict[str, Any],
        alt_formats: Optional[list[str]] = None,
    ) -> Optional[str]:
        """
        Специальные ретраи для YouTube Shorts: пробуем несколько формат-селекторов.
        
        Возвращает путь к файлу или None при неудаче.
        """
        logger.warning("[YtDlpService] Пробую альтернативные форматы для YouTube Shorts")
        if alt_formats is None:
            alt_formats = [
                'best[height<=1280][ext=mp4]/best[height<=1280]/best',
                'best[ext=mp4]/best',
                'best',
            ]
        for alt_format in alt_formats:
            logger.info(f"[YtDlpService] Пробую формат: {alt_format}")
            self._cleanup_temp_family(tmp_path)
            ydl_opts['format'] = alt_format
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                actual_file_path = self._find_output_file(tmp_path)
                file_size = os.path.getsize(actual_file_path) if actual_file_path and os.path.exists(actual_file_path) else 0
                if actual_file_path and file_size > 0:
                    logger.info(
                        f"[YtDlpService] ✅ Shorts скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB"
                    )
                    return actual_file_path
            except Exception as alt_e:
                logger.warning(f"[YtDlpService] Ошибка с форматом {alt_format}: {alt_e}")
                continue
        # Ничего не сработало
        self._cleanup_temp_family(tmp_path)
        return None

    def _retry_instagram_download(self, url: str, tmp_path: str, ydl_opts: Dict[str, Any]) -> Optional[str]:
        """
        Специальные ретраи для Instagram: пробуем несколько формат-селекторов.
        
        Возвращает путь к файлу или None при неудаче.
        """
        logger.warning("[YtDlpService] Пробую альтернативные форматы для Instagram")
        alt_formats = ['best', 'worst', 'best[ext=mp4]', 'worst[ext=mp4]', 'bestvideo+bestaudio/best']

        for alt_format in alt_formats:
            logger.info(f"[YtDlpService] Пробую альтернативный формат: {alt_format}")
            # Удаляем файлы перед каждой попыткой
            self._cleanup_temp_family(tmp_path)

            ydl_opts['format'] = alt_format

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                actual_file_path = self._find_output_file(tmp_path)
                file_size = os.path.getsize(actual_file_path) if actual_file_path and os.path.exists(actual_file_path) else 0

                if actual_file_path and file_size > 0:
                    logger.info(
                        f"[YtDlpService] ✅ Успешно скачано с форматом {alt_format}: {file_size / (1024 * 1024):.2f} MB"
                    )
                    return actual_file_path
            except Exception as e:
                logger.warning(f"[YtDlpService] Ошибка при скачивании с форматом {alt_format}: {e}")
                continue

        logger.error("[YtDlpService] ❌ Не удалось скачать видео ни с одним форматом (Instagram)")
        self._cleanup_temp_family(tmp_path)
        return None
    
    def get_info(self, url: str, ydl_opts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о видео через yt-dlp
        
        Args:
            url: URL видео
            ydl_opts: Опции для yt-dlp (опционально)
            
        Returns:
            Словарь с информацией о видео или None при ошибке
        """
        #TODO какого хуя тут настройки? они должны настраивается в конкретном сервисе
        if ydl_opts is None:
            ydl_opts = {
                'verbose': True,
                'quiet': False,
                'no_warnings': False,
                'extract_flat': False,
            }
        
        try:
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

    async def get_info_async(
        self, url: str, ydl_opts: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Асинхронная обёртка над get_info: выполняет в executor, чтобы не блокировать event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.get_info(url, ydl_opts))


    def get_video_info(self, url: str, ydl_opts: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Backwards-compatible обертка для get_info().
        Используется старыми сервисами/юзкейсами, ожидающими VideoDownloader.
        """
        return self.get_info(url, ydl_opts)

    def get_video_id(self, url: str) -> Optional[str]:
        """
        Получить канонический ID видео через yt-dlp extractor

        Args:
            url: URL видео
        Returns:
            Идентификатор в формате "platform:video_id".
        """
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
            }
            
            start_time = time.time()
            logger.info(f"[extract_info] Начало получения video_id: {url}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            
            elapsed_time = time.time() - start_time
            logger.info(f"[extract_info] video_id получен за {elapsed_time:.2f} сек: {url}")
            
            video_id = info.get('id')
            platform = info.get('extractor_key', 'unknown').lower()
            
            if video_id and platform:
                canonical_id = f"{platform}:{video_id}"
                logger.info(f"Канонический ID для {url}: {canonical_id}")
                return canonical_id
                    
        except Exception as e:
            logger.warning(f"Не удалось получить канонический ID для {url}: {e}")
        
        return None

    def get_available_formats(
        self,
        url: str,
        ydl_opts: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Получить доступные форматы для видео (совместимо с VideoDownloader.get_available_formats).
        
        Returns:
            Словарь с форматами вида:
            {
                '480p': {'format_id': '...', 'filesize': ...},
                '720p': {'format_id': '...', 'filesize': ...},
                '1080p': {'format_id': '...', 'filesize': ...},
                'audio': {'format_id': '...', 'filesize': ...}
            }
        """
        if ydl_opts is None:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'listformats': True,
            }
        
        try:
            formats_dict: Dict[str, Any] = {}
            
            start_time = time.time()
            logger.info(f"[extract_info] Начало получения информации о форматах: {url}")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
            elapsed_time = time.time() - start_time
            logger.info(f"[extract_info] Информация о форматах получена за {elapsed_time:.2f} сек: {url}")
            
            formats = info.get('formats', [])
            
            video_formats: Dict[int, list] = {}
            audio_formats = []
            
            for fmt in formats:
                height = fmt.get('height')
                vcodec = fmt.get('vcodec', 'none')
                acodec = fmt.get('acodec', 'none')
                format_id = fmt.get('format_id')
                filesize = fmt.get('filesize') or fmt.get('filesize_approx', 0)
                ext = fmt.get('ext', 'mp4')
                
                if vcodec == 'none' and acodec != 'none' and ext in ['m4a', 'webm', 'mp3']:
                    audio_formats.append({
                        'format_id': format_id,
                        'filesize': filesize,
                        'ext': ext
                    })
                
                if vcodec != 'none' and height:
                    if height not in video_formats:
                        video_formats[height] = []
                    video_formats[height].append({
                        'format_id': format_id,
                        'filesize': filesize,
                        'ext': ext,
                        'height': height,
                        'has_audio': acodec != 'none'
                    })
            
            is_shorts = 'shorts' in url.lower()
            if is_shorts:
                target_heights_with_labels = [(1280, '720p'), (1920, '1080p')]
            else:
                target_heights_with_labels = [
                    ([480, 854], '480p'),
                    ([720, 1280], '720p'),
                    ([1080, 1920], '1080p'),
                ]
            
            for item, height_label in target_heights_with_labels:
                heights_to_use = [item] if isinstance(item, int) else list(item)
                
                candidates = []
                for h in heights_to_use:
                    if h in video_formats:
                        candidates.extend(video_formats[h])
                
                if not candidates:
                    continue
                
                formats_with_audio = [f for f in candidates if f.get('has_audio', False)]
                if formats_with_audio:
                    best_format = min(
                        formats_with_audio,
                        key=lambda x: x['filesize'] if x['filesize'] else float('inf')
                    )
                    if height_label not in formats_dict:
                        formats_dict[height_label] = {
                            'format_id': best_format['format_id'],
                            'filesize': best_format['filesize'],
                            'ext': best_format['ext'],
                            'height': best_format.get('height')
                        }
                else:
                    video_only = [f for f in candidates if not f.get('has_audio', False)]
                    if video_only:
                        best_format = min(
                            video_only,
                            key=lambda x: x['filesize'] if x['filesize'] else float('inf')
                        )
                        if height_label not in formats_dict:
                            formats_dict[height_label] = {
                                'format_id': best_format['format_id'],
                                'filesize': best_format['filesize'],
                                'ext': best_format['ext'],
                                'height': best_format.get('height'),
                                'needs_audio': True
                            }
            
            if audio_formats and not is_shorts:
                quality_audio = [f for f in audio_formats if f.get('filesize', 0) > 1000000]
                
                if quality_audio:
                    best_audio = max(
                        quality_audio,
                        key=lambda x: x['filesize'] if x['filesize'] else 0
                    )
                else:
                    best_audio = max(
                        audio_formats,
                        key=lambda x: x['filesize'] if x['filesize'] else 0
                    )
                
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
        # quiet / no_warnings: по умолчанию НЕ подавляем вывод,
        # чтобы видеть ошибки (особенно для Instagram).
        if ydl_opts.get('quiet'):
            cmd.append('--quiet')
        
        if ydl_opts.get('no_warnings'):
            cmd.append('--no-warnings')

        # verbose: если включён в ydl_opts, пробрасываем в yt-dlp
        if ydl_opts.get('verbose'):
            cmd.append('--verbose')
        
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
            if self._exceeds_size_limit(file_size):
                logger.error(
                    f"[YtDlpService] Размер файла {file_size / (1024 * 1024):.1f} MB превышает лимит {self.max_file_size_mb} MB"
                )
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
        
        tmp_path = self._create_tmp_path(download_plan, output_path, with_extension=True)
        
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                logger.debug(f"[YtDlpService] Удален существующий файл: {tmp_path}")
            except Exception as e:
                logger.warning(f"[YtDlpService] Не удалось удалить существующий файл {tmp_path}: {e}")
        
        ydl_opts = download_plan.ydl_opts.copy()
        ydl_opts['format'] = format_selector
        ydl_opts['outtmpl'] = f"{tmp_path}.%(ext)s"
        ydl_opts['nopart'] = True 
        ydl_opts['continue_dl'] = False
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            logger.error(f"[YtDlpService] ❌ yt-dlp DownloadError при скачивании {url}: {error_msg}")

            actual_file_path = None

            if download_plan.platform == 'youtube' and getattr(download_plan, 'quality', None) == 'shorts':
                actual_file_path = self._retry_youtube_shorts_download(url, tmp_path, ydl_opts)
            
            elif download_plan.platform == 'instagram':
                actual_file_path = self._retry_instagram_download(url, tmp_path, ydl_opts)
            else:
                self._cleanup_temp_family(tmp_path)
                return None

            if not actual_file_path:
                return None
            tmp_path = actual_file_path

        except Exception as e:
            logger.error(f"[YtDlpService] ❌ Неожиданная ошибка при скачивании {url}: {e}", exc_info=True)
            self._cleanup_temp_family(tmp_path)
            return None

        # Определяем фактический путь к скачанному файлу
        actual_file_path = self._find_output_file(tmp_path)
        if not actual_file_path:
            logger.error("[YtDlpService] Скачанный файл не найден")
            return None

        file_size = os.path.getsize(actual_file_path) if os.path.exists(actual_file_path) else 0

        if file_size == 0 and download_plan.platform == 'youtube' and getattr(download_plan, 'quality', None) == 'shorts':
            # Файл пустой — пробуем ещё несколько, более общих, форматов
            self._cleanup_paths(actual_file_path if actual_file_path != tmp_path else None)
            logger.warning("[YtDlpService] Shorts: файл пустой, пробую альтернативные форматы (fallback)")
            fallback_formats = ['best[ext=mp4]/best', 'best']
            new_path = self._retry_youtube_shorts_download(url, tmp_path, ydl_opts, alt_formats=fallback_formats)
            if not new_path:
                return None
            actual_file_path = new_path
            file_size = os.path.getsize(actual_file_path) if os.path.exists(actual_file_path) else 0

        if file_size == 0:
            logger.error("[YtDlpService] Скачанный файл пустой")
            self._cleanup_paths(actual_file_path if actual_file_path != tmp_path else None)
            self._cleanup_temp_family(tmp_path)
            return None
        if self._exceeds_size_limit(file_size):
            logger.error(
                f"[YtDlpService] Размер файла {file_size / (1024 * 1024):.1f} MB превышает лимит {self.max_file_size_mb} MB"
            )
            self._cleanup_paths(actual_file_path if actual_file_path != tmp_path else None)
            self._cleanup_temp_family(tmp_path)
            return None

        filename, actual_ext = self._build_filename(download_plan, actual_file_path)

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

        tmp_path = self._create_tmp_path(download_plan, output_path, with_extension=False)

        self._cleanup_temp_family(tmp_path)

        cmd = ['yt-dlp', '-f', format_selector, '-o', f"{tmp_path}.%(ext)s"]

        ydl_opts = download_plan.ydl_opts.copy() if download_plan.ydl_opts else {}

        if ydl_opts.get('quiet'):
            cmd.append('--quiet')
        if ydl_opts.get('no_warnings'):
            cmd.append('--no-warnings')
        if ydl_opts.get('verbose'):
            cmd.append('--verbose')
        if ydl_opts.get('noplaylist'):
            cmd.append('--no-playlist')

        if 'extractor_args' in ydl_opts:
            extractor_args = ydl_opts['extractor_args']
            if 'instagram' in extractor_args:
                instagram_args = extractor_args['instagram']
                if instagram_args.get('webpage_download') is False:
                    cmd.extend(['--extractor-args', 'instagram:webpage_download=False'])

        if ydl_opts.get('user_agent'):
            cmd.extend(['--user-agent', ydl_opts['user_agent']])

        cookiefile = ydl_opts.get('cookiefile')
        if cookiefile:
            cmd.extend(['--cookies', cookiefile])

        cmd.append(url)

        try:
            loop = asyncio.get_event_loop()
            process = await loop.run_in_executor(
                None,
                lambda: subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            )

            returncode = await loop.run_in_executor(None, process.wait)

            if returncode != 0:
                error = process.stderr.read().decode('utf-8', errors='ignore')
                logger.error(f"[YtDlpService] Ошибка yt-dlp (pipelined): {error}")

                if download_plan.platform == 'instagram':
                    actual_file_path = self._retry_instagram_download(url, tmp_path, ydl_opts)
                    if not actual_file_path:
                        return None
                    tmp_path = actual_file_path
                else:
                    self._cleanup_temp_family(tmp_path)
                    return None

            actual_file_path = self._find_output_file(tmp_path)
            if not actual_file_path:
                logger.error("[YtDlpService] Скачанный файл не найден (pipelined)")
                return None

            file_size = os.path.getsize(actual_file_path) if os.path.exists(actual_file_path) else 0

            if file_size == 0:
                logger.error("[YtDlpService] Скачанный файл пустой (pipelined)")
                self._cleanup_paths(actual_file_path if actual_file_path != tmp_path else None)
                self._cleanup_temp_family(tmp_path)
                return None
            if self._exceeds_size_limit(file_size):
                logger.error(
                    f"[YtDlpService] Размер файла {file_size / (1024 * 1024):.1f} MB превышает лимит {self.max_file_size_mb} MB (pipelined)"
                )
                self._cleanup_paths(actual_file_path if actual_file_path != tmp_path else None)
                self._cleanup_temp_family(tmp_path)
                return None

            filename, actual_ext = self._build_filename(download_plan, actual_file_path)

            logger.info(
                f"[YtDlpService] Видео скачано в файл (pipelined): "
                f"{actual_file_path} ({file_size / (1024 * 1024):.2f} MB, расширение: {actual_ext})"
            )
            return (actual_file_path, file_size, filename)

        except FileNotFoundError:
            logger.warning("[YtDlpService] yt-dlp не найден в PATH")
            return None
        except Exception as e:
            logger.error(f"[YtDlpService] Ошибка при pipelined скачивании: {e}", exc_info=True)
            self._cleanup_temp_family(tmp_path)
            return None
