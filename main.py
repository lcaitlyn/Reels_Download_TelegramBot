"""
Точка входа в приложение
"""
import asyncio
from src.bot.bot import run_bot

if __name__ == "__main__":
    asyncio.run(run_bot())
