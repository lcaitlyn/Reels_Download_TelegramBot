"""
DownloadResponse - ответ от DownloadManager пользователю
Содержит статус обработки запроса и необходимые данные
"""
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class DownloadResponse:
    """
    Ответ от DownloadManager на запрос скачивания
    
    Создается DownloadManager после обработки запроса request_download().
    Используется bot.py для определения дальнейших действий.
    
    Attributes:
        status: Статус обработки:
            - READY: Видео уже готово в кэше
            - QUEUED: Задача добавлена в очередь
            - IN_PROGRESS: Видео уже скачивается другим пользователем
            - ERROR: Ошибка при обработке
            - REQUIRES_USER_INPUT: Требуется выбор качества (YouTube)
        file_id: file_id видео в Telegram (если status == READY)
        message_id: ID сообщения в канале (если status == READY)
        job_id: ID задачи в очереди (если status == QUEUED или IN_PROGRESS)
        error: Сообщение об ошибке (если status == ERROR)
        available_qualities: Список доступных качеств (если status == REQUIRES_USER_INPUT)
    """
    status: str  # READY | QUEUED | IN_PROGRESS | ERROR | REQUIRES_USER_INPUT
    file_id: Optional[str] = None  # если READY
    message_id: Optional[int] = None  # если READY
    job_id: Optional[str] = None  # если QUEUED или IN_PROGRESS
    error: Optional[str] = None  # если ERROR
    available_qualities: Optional[List[str]] = None  # если REQUIRES_USER_INPUT
    
    def is_ready(self) -> bool:
        """Проверка, готово ли видео"""
        return self.status == 'READY'
    
    def is_queued(self) -> bool:
        """Проверка, добавлено ли в очередь"""
        return self.status == 'QUEUED'
    
    def is_in_progress(self) -> bool:
        """Проверка, скачивается ли уже"""
        return self.status == 'IN_PROGRESS'
    
    def is_error(self) -> bool:
        """Проверка, есть ли ошибка"""
        return self.status == 'ERROR'
    
    def requires_input(self) -> bool:
        """Проверка, требуется ли выбор качества"""
        return self.status == 'REQUIRES_USER_INPUT'
