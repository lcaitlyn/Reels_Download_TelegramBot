"""
Скрипт для запуска download worker
Запускать из корневой директории проекта: python run_download_worker.py
"""
import sys
import os

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import asyncio
from src.workers.download_worker import main

if __name__ == "__main__":
    asyncio.run(main())
