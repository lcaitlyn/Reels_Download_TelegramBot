"""
Точка входа в приложение
"""
import asyncio
from src.bot.bot import run_bot

# TODO возможно сделать все настройки прилы прямо тут
# типо сканировать .env и config.py тут
# настройки Posgres и прочего
# настройки cookies для инстаграма и тд
if __name__ == "__main__":
    asyncio.run(run_bot())
