"""
Скрипт для запуска dashboard API
Запускать из корневой директории проекта: python run_dashboard.py
"""
import sys
import os

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    uvicorn.run("src.dashboard.dashboard:app", host="0.0.0.0", port=port, reload=False)
