"""
Скрипт для запуска миграций БД
Запускать из корневой директории проекта: python run_migrations.py
"""
import sys
import os

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
from migrations.migrations import run_migrations

if __name__ == "__main__":
    asyncio.run(run_migrations())
