"""
Модуль для работы с PostgreSQL - аналитика и статистика
Использует asyncpg для асинхронной работы с БД
"""
import os
import asyncio
from typing import Optional, List, Dict
from datetime import datetime
import asyncpg
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)

# Типы источников скачивания
DOWNLOAD_SOURCE_MESSAGE = 'message'
DOWNLOAD_SOURCE_INLINE = 'inline'
DOWNLOAD_SOURCE_DEEP_LINK = 'deep_link'

# Типы событий кликов
CLICK_EVENT_BUTTON = 'button_click'
CLICK_EVENT_DEEP_LINK = 'deep_link'


class AnalyticsDB:
    """Класс для работы с аналитической БД PostgreSQL"""
    
    def __init__(self, database_url: str = None):
        """
        Инициализация подключения к PostgreSQL
        
        Args:
            database_url: URL для подключения к PostgreSQL (по умолчанию из .env)
        """
        if not database_url:
            # Сначала проверяем полный URL
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                # Если нет полного URL, собираем из отдельных переменных
                postgres_host = os.getenv("POSTGRES_HOST", "localhost")
                postgres_port = os.getenv("POSTGRES_PORT", "5432")
                postgres_db = os.getenv("POSTGRES_DB", "analytics")
                postgres_user = os.getenv("POSTGRES_USER", "postgres")
                postgres_password = os.getenv("POSTGRES_PASSWORD", "postgres")
                database_url = f"postgresql://{postgres_user}:{postgres_password}@{postgres_host}:{postgres_port}/{postgres_db}"
        
        self.database_url = database_url
        self.pool: Optional[asyncpg.Pool] = None
        self.logger = logger
    
    async def connect(self):
        """Создать пул подключений к PostgreSQL"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
                command_timeout=60
            )
            self.logger.info("Подключение к PostgreSQL установлено")
        except Exception as e:
            self.logger.error(f"Ошибка подключения к PostgreSQL: {e}")
            raise
    
    async def close(self):
        """Закрыть пул подключений"""
        if self.pool:
            await self.pool.close()
            self.logger.info("Подключение к PostgreSQL закрыто")
    
    async def init_schema(self):
        """Инициализация схемы БД (создание таблиц)"""
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            # Таблица users
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    total_downloads INTEGER NOT NULL DEFAULT 0,
                    is_paid BOOLEAN NOT NULL DEFAULT FALSE,
                    referral_code VARCHAR(20) UNIQUE,
                    referred_by BIGINT REFERENCES users(user_id),
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            
            # Таблица videos
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    video_id VARCHAR(255) PRIMARY KEY,
                    platform VARCHAR(50) NOT NULL,
                    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    total_downloads INTEGER NOT NULL DEFAULT 0,
                    last_download_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            
            # Таблица downloads
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id),
                    video_id VARCHAR(255) NOT NULL REFERENCES videos(video_id),
                    platform VARCHAR(50) NOT NULL,
                    source VARCHAR(20) NOT NULL CHECK (source IN ('message', 'inline', 'deep_link')),
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            
            # Таблица referrals
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    referrer_id BIGINT NOT NULL REFERENCES users(user_id),
                    referred_user_id BIGINT NOT NULL REFERENCES users(user_id),
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    UNIQUE(referrer_id, referred_user_id)
                )
            """)
            
            # Таблица click_events
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS click_events (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id),
                    event_type VARCHAR(20) NOT NULL CHECK (event_type IN ('button_click', 'deep_link')),
                    video_id VARCHAR(255) REFERENCES videos(video_id),
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            
            # Таблица ad_campaigns (заготовка для рекламы)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ad_campaigns (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    message TEXT NOT NULL,
                    button_text VARCHAR(100),
                    button_url VARCHAR(500),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    weight INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            
            # Таблица ad_impressions (логирование показов рекламы)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ad_impressions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id),
                    campaign_id INTEGER NOT NULL REFERENCES ad_campaigns(id),
                    clicked BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            
            # Индексы для производительности
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_user_id ON downloads(user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_video_id ON downloads(video_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_created_at ON downloads(created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_click_events_user_id ON click_events(user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_click_events_video_id ON click_events(video_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_click_events_created_at ON click_events(created_at)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer_id ON referrals(referrer_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referred_user_id ON referrals(referred_user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_campaigns_is_active ON ad_campaigns(is_active)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_impressions_user_id ON ad_impressions(user_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_impressions_campaign_id ON ad_impressions(campaign_id)")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_ad_impressions_created_at ON ad_impressions(created_at)")
            
            self.logger.info("Схема БД инициализирована")
    
    async def upsert_user(self, user_id: int, referral_code: str = None, referred_by: int = None):
        """
        Создать или обновить пользователя
        
        Args:
            user_id: ID пользователя Telegram
            referral_code: Уникальный реферальный код пользователя
            referred_by: ID пользователя, который пригласил (если есть)
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, referral_code, referred_by, last_seen_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    last_seen_at = NOW(),
                    referral_code = COALESCE(EXCLUDED.referral_code, users.referral_code),
                    referred_by = COALESCE(EXCLUDED.referred_by, users.referred_by),
                    updated_at = NOW()
            """, user_id, referral_code, referred_by)
    
    async def record_download(self, user_id: int, video_id: str, platform: str, source: str):
        """
        Записать факт скачивания видео
        
        Args:
            user_id: ID пользователя
            video_id: Канонический ID видео (например, "instagram:ABC123")
            platform: Платформа (youtube, instagram, tiktok)
            source: Источник запроса ('message', 'inline', 'deep_link')
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Обновляем/создаём пользователя
                await conn.execute("""
                    INSERT INTO users (user_id, last_seen_at)
                    VALUES ($1, NOW())
                    ON CONFLICT (user_id) 
                    DO UPDATE SET 
                        last_seen_at = NOW(),
                        total_downloads = users.total_downloads + 1,
                        updated_at = NOW()
                """, user_id)
                
                # Обновляем/создаём видео
                await conn.execute("""
                    INSERT INTO videos (video_id, platform, last_download_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (video_id)
                    DO UPDATE SET
                        total_downloads = videos.total_downloads + 1,
                        last_download_at = NOW(),
                        updated_at = NOW()
                """, video_id, platform)
                
                # Записываем скачивание
                await conn.execute("""
                    INSERT INTO downloads (user_id, video_id, platform, source)
                    VALUES ($1, $2, $3, $4)
                """, user_id, video_id, platform, source)
    
    async def record_click_event(self, user_id: int, event_type: str, video_id: str = None):
        """
        Записать событие клика (кнопка или deep link)
        
        Args:
            user_id: ID пользователя
            event_type: Тип события ('button_click' или 'deep_link')
            video_id: ID видео (опционально)
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            # Создаём пользователя, если его нет
            await conn.execute("""
                INSERT INTO users (user_id, last_seen_at)
                VALUES ($1, NOW())
                ON CONFLICT (user_id) 
                DO UPDATE SET last_seen_at = NOW()
            """, user_id)
            
            # Записываем событие
            await conn.execute("""
                INSERT INTO click_events (user_id, event_type, video_id)
                VALUES ($1, $2, $3)
            """, user_id, event_type, video_id)
    
    async def record_referral(self, referrer_id: int, referred_user_id: int):
        """
        Записать реферальную связь
        
        Args:
            referrer_id: ID пользователя, который пригласил
            referred_user_id: ID нового пользователя
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO referrals (referrer_id, referred_user_id)
                VALUES ($1, $2)
                ON CONFLICT (referrer_id, referred_user_id) DO NOTHING
            """, referrer_id, referred_user_id)
            
            # Обновляем referred_by у нового пользователя
            await conn.execute("""
                UPDATE users
                SET referred_by = $1
                WHERE user_id = $2 AND referred_by IS NULL
            """, referrer_id, referred_user_id)
    
    async def get_user_stats(self, user_id: int) -> Optional[Dict]:
        """
        Получить статистику пользователя
        
        Args:
            user_id: ID пользователя
            
        Returns:
            Словарь со статистикой или None
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    user_id,
                    first_seen_at,
                    last_seen_at,
                    total_downloads,
                    is_paid,
                    referral_code,
                    referred_by
                FROM users
                WHERE user_id = $1
            """, user_id)
            
            if row:
                return dict(row)
            return None
    
    async def get_top_videos(self, limit: int = 10) -> List[Dict]:
        """
        Получить топ популярных видео
        
        Args:
            limit: Количество видео в топе
            
        Returns:
            Список словарей с информацией о видео
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    video_id,
                    platform,
                    total_downloads,
                    last_download_at
                FROM videos
                ORDER BY total_downloads DESC
                LIMIT $1
            """, limit)
            
            return [dict(row) for row in rows]
    
    async def get_platform_stats(self) -> Dict[str, int]:
        """
        Получить статистику по платформам
        
        Returns:
            Словарь {platform: count}
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT platform, COUNT(*) as count
                FROM downloads
                GROUP BY platform
            """)
            
            return {row['platform']: row['count'] for row in rows}

    async def get_summary(self) -> Dict[str, int]:
        """
        Получить общую сводную статистику:
        - количество пользователей
        - количество видео
        - общее количество скачиваний
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
            videos_count = await conn.fetchval("SELECT COUNT(*) FROM videos")
            total_downloads = await conn.fetchval("SELECT COALESCE(SUM(total_downloads), 0) FROM users")
        
        return {
            "users_count": int(users_count or 0),
            "videos_count": int(videos_count or 0),
            "total_downloads": int(total_downloads or 0),
        }

    async def get_active_users_count(self, days: int = 7) -> int:
        """
        Получить количество активных пользователей за последние N дней
        (по таблице downloads)
        """
        if not self.pool:
            await self.connect()
        
        async with self.pool.acquire() as conn:
            # Используем безопасный параметр для INTERVAL
            interval_str = f"{days} days"
            count = await conn.fetchval(
                "SELECT COUNT(DISTINCT user_id) "
                "FROM downloads "
                "WHERE created_at >= NOW() - $1::interval",
                interval_str,
            )
        
        return int(count or 0)
