# Downloader Bot

Telegram бот для скачивания видео из YouTube Shorts, Instagram Reels/Posts, TikTok.

## Установка

1. **Создайте виртуальное окружение** (решает проблему `externally-managed-environment` на macOS):
```bash
python3 -m venv venv
source venv/bin/activate  # Для macOS/Linux
# или
venv\Scripts\activate  # Для Windows
```

2. **Установите зависимости**:
```bash
pip install -r requirements.txt
```

3. **Скопируйте `.env.example` в `.env` и заполните токены**:
```bash
cp .env.example .env
# Отредактируйте .env файл, добавив ваш BOT_TOKEN и TELEGRAM_CHANNEL_ID
```

4. **Запустите бота**:
```bash
python main.py
```

> **Примечание**: Если у вас ошибка `externally-managed-environment`, обязательно используйте виртуальное окружение (venv). Это стандартная практика для Python проектов.

## Настройка

- `BOT_TOKEN` - токен бота от @BotFather
- `TELEGRAM_CHANNEL_ID` - ID приватного канала для хранения видео (бот должен быть админом)
