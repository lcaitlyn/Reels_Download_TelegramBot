"""
Use case: Получение статистики пользователя
"""
import logging
from typing import Dict
from src.database.redis_db import Database

logger = logging.getLogger(__name__)


class GetStatsUseCase:
    """Use case для получения статистики пользователя"""
    
    def __init__(self, db: Database):
        """
        Args:
            db: Экземпляр Database для работы с данными
        """
        self.db = db
    
    async def execute(self, user_id: int) -> Dict[str, any]:
        """
        Получить статистику пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            dict с ключами:
                - downloads_total: общее количество скачиваний
                - downloads_today: скачиваний сегодня
                - downloads_month: скачиваний в этом месяце
                - remaining_free: оставшихся бесплатных скачиваний
        """
        try:
            downloads_total = await self.db.get_user_downloads_count(user_id)
            downloads_today = await self.db.get_user_downloads_today(user_id)
            downloads_month = await self.db.get_user_downloads_month(user_id)
            
            # Вычисляем оставшиеся бесплатные скачивания
            free_limit = 10
            remaining_free = max(0, free_limit - downloads_total)
            
            return {
                'downloads_total': downloads_total,
                'downloads_today': downloads_today,
                'downloads_month': downloads_month,
                'remaining_free': remaining_free,
                'free_limit': free_limit
            }
        except Exception as e:
            logger.error(f"Ошибка при получении статистики для пользователя {user_id}: {e}", exc_info=True)
            return {
                'downloads_total': 0,
                'downloads_today': 0,
                'downloads_month': 0,
                'remaining_free': 10,
                'free_limit': 10,
                'error': True
            }
