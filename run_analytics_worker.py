"""
Скрипт для запуска analytics worker
Запускать из корневой директории проекта: python run_analytics_worker.py
"""
import sys
import os

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
from src.workers.analytics_worker import main

if __name__ == "__main__":
    asyncio.run(main())
