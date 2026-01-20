"""
Background Worker для обработки событий аналитики из очереди Redis
Обновляет Redis счётчики (быстро) и пишет в PostgreSQL (асинхронно)
Обрабатывает ошибки gracefully - не падает на одном событии
"""
import os
import asyncio
import logging
from typing import Optional
from dotenv import load_dotenv

from database import Database
from analytics_db import AnalyticsDB
from events import (
    DownloadCompletedEvent,
    VideoViewClickedEvent,
    UserReferredEvent
)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Инициализация компонентов
db = Database()  # Redis для счётчиков
analytics_db = AnalyticsDB()  # PostgreSQL для аналитики


async def process_download_completed_event(event: DownloadCompletedEvent):
    """
    Обработать событие завершения скачивания
    
    Args:
        event: Событие DownloadCompletedEvent
    """
    try:
        # 1. Быстро обновляем Redis счётчики (не блокирует)
        await db.increment_user_downloads(event.user_id)
        await db.increment_video_downloads(event.video_id)
        
        logger.info(f"[analytics] Обновлены Redis счётчики: user_id={event.user_id}, video_id={event.video_id}")
        
        # 2. Асинхронно пишем в PostgreSQL (не блокирует основной поток)
        await analytics_db.record_download(
            user_id=event.user_id,
            video_id=event.video_id,
            platform=event.platform,
            source=event.source
        )
        
        logger.info(f"[analytics] Записано скачивание в PostgreSQL: user_id={event.user_id}, video_id={event.video_id}")
        
    except Exception as e:
        # Обрабатываем ошибки gracefully - не падаем на одном событии
        logger.error(f"[analytics] Ошибка при обработке DownloadCompletedEvent: {e}", exc_info=True)


async def process_video_view_clicked_event(event: VideoViewClickedEvent):
    """
    Обработать событие клика на видео
    
    Args:
        event: Событие VideoViewClickedEvent
    """
    try:
        # Пишем в PostgreSQL
        await analytics_db.record_click_event(
            user_id=event.user_id,
            event_type=event.event_type,
            video_id=event.video_id
        )
        
        logger.info(f"[analytics] Записано событие клика: user_id={event.user_id}, event_type={event.event_type}")
        
    except Exception as e:
        logger.error(f"[analytics] Ошибка при обработке VideoViewClickedEvent: {e}", exc_info=True)


async def process_user_referred_event(event: UserReferredEvent):
    """
    Обработать событие реферального приглашения
    
    Args:
        event: Событие UserReferredEvent
    """
    try:
        # Пишем в PostgreSQL
        await analytics_db.record_referral(
            referrer_id=event.referrer_id,
            referred_user_id=event.new_user_id
        )
        
        logger.info(f"[analytics] Записано реферальное событие: referrer_id={event.referrer_id}, new_user_id={event.new_user_id}")
        
    except Exception as e:
        logger.error(f"[analytics] Ошибка при обработке UserReferredEvent: {e}", exc_info=True)


async def process_analytics_event(event_json: str):
    """
    Обработать событие аналитики из очереди
    
    Args:
        event_json: JSON-строка с событием
    """
    try:
        # Пытаемся определить тип события по структуре JSON
        import json
        event_data = json.loads(event_json)
        
        # Определяем тип события по наличию полей
        if 'video_id' in event_data and 'platform' in event_data and 'source' in event_data:
            # DownloadCompletedEvent
            event = DownloadCompletedEvent.from_json(event_json)
            await process_download_completed_event(event)
        elif 'event_type' in event_data:
            # VideoViewClickedEvent
            event = VideoViewClickedEvent.from_json(event_json)
            await process_video_view_clicked_event(event)
        elif 'referrer_id' in event_data and 'new_user_id' in event_data:
            # UserReferredEvent
            event = UserReferredEvent.from_json(event_json)
            await process_user_referred_event(event)
        else:
            logger.warning(f"[analytics] Неизвестный тип события: {event_json}")
            
    except json.JSONDecodeError as e:
        logger.error(f"[analytics] Ошибка парсинга JSON события: {e}, event_json={event_json}")
    except Exception as e:
        # Обрабатываем любые другие ошибки gracefully
        logger.error(f"[analytics] Ошибка при обработке события: {e}, event_json={event_json}", exc_info=True)


async def analytics_worker_loop():
    """
    Основной цикл worker'а для обработки событий аналитики из очереди Redis
    """
    logger.info("[analytics] Analytics worker запущен, ожидаю события...")
    
    while True:
        try:
            # Получаем событие из очереди (блокирующее ожидание, timeout 5 секунд)
            event_json = await db.get_analytics_event(timeout=5)
            
            if event_json:
                # Обрабатываем событие
                await process_analytics_event(event_json)
            else:
                # Timeout - нет событий в очереди, продолжаем цикл
                await asyncio.sleep(0.1)  # Небольшая задержка, чтобы не спамить Redis
                
        except KeyboardInterrupt:
            logger.info("[analytics] Получен сигнал остановки")
            break
        except Exception as e:
            # Обрабатываем ошибки gracefully - не падаем на одном событии
            logger.error(f"[analytics] Ошибка в основном цикле worker'а: {e}", exc_info=True)
            await asyncio.sleep(1)  # Небольшая задержка перед следующей попыткой


async def main():
    """Главная функция analytics worker'а"""
    try:
        # Подключаемся к PostgreSQL
        await analytics_db.connect()
        logger.info("[analytics] Подключение к PostgreSQL установлено")
        
        # Запускаем основной цикл
        await analytics_worker_loop()
        
    except KeyboardInterrupt:
        logger.info("[analytics] Получен сигнал остановки")
    except Exception as e:
        logger.error(f"[analytics] Критическая ошибка: {e}", exc_info=True)
    finally:
        # Закрываем подключения
        await db.close()
        await analytics_db.close()
        logger.info("[analytics] Analytics worker остановлен")


if __name__ == "__main__":
    asyncio.run(main())
