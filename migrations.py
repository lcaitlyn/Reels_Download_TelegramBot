"""
Скрипт для миграций БД
Запускать для инициализации или обновления схемы PostgreSQL
"""
import asyncio
import logging
import os
import asyncpg
from analytics_db import AnalyticsDB
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_database_if_not_exists():
    """Создать базу данных, если она не существует"""
    # Получаем параметры подключения
    postgres_host = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db = os.getenv("POSTGRES_DB", "analytics")
    postgres_user = os.getenv("POSTGRES_USER", "postgres")
    postgres_password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    # Подключаемся к postgres БД (системная БД) для создания analytics
    try:
        conn = await asyncpg.connect(
            host=postgres_host,
            port=postgres_port,
            user=postgres_user,
            password=postgres_password,
            database='postgres'  # Подключаемся к системной БД
        )
        
        # Проверяем, существует ли база данных
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", postgres_db
        )
        
        if not exists:
            logger.info(f"Создаю базу данных '{postgres_db}'...")
            await conn.execute(f'CREATE DATABASE "{postgres_db}"')
            logger.info(f"✅ База данных '{postgres_db}' создана")
        else:
            logger.info(f"База данных '{postgres_db}' уже существует")
        
        await conn.close()
    except Exception as e:
        logger.error(f"Ошибка при создании базы данных: {e}")
        raise


async def run_migrations():
    """Запустить миграции БД"""
    # Сначала создаем базу данных, если её нет
    try:
        await create_database_if_not_exists()
    except Exception as e:
        logger.warning(f"Не удалось создать базу данных автоматически: {e}")
        logger.info("Попробуйте создать базу данных вручную: createdb analytics")
    
    db = AnalyticsDB()
    
    try:
        await db.connect()
        await db.init_schema()
        logger.info("✅ Миграции успешно выполнены")
    except Exception as e:
        logger.error(f"❌ Ошибка при выполнении миграций: {e}", exc_info=True)
        raise
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(run_migrations())
