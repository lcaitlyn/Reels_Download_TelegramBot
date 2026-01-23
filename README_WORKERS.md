# 🚀 Запуск Workers и Миграций

## Проблема

При запуске скриптов из поддиректорий возникает ошибка:
```
ModuleNotFoundError: No module named 'src'
```

Это происходит потому, что Python не знает, где находится корневая директория проекта.

## ✅ Решение

### Вариант 1: Использовать скрипты запуска (рекомендуется)

В корне проекта созданы скрипты для запуска:

```bash
# Запуск бота
python main.py

# Запуск download worker
python run_download_worker.py

# Запуск analytics worker
python run_analytics_worker.py

# Запуск миграций БД
python run_migrations.py
```

### Вариант 2: Запуск как модуль

```bash
# Из корневой директории проекта
python -m src.workers.download_worker
python -m src.workers.analytics_worker
python -m src.bot.bot
python -m migrations.migrations
```

### Вариант 3: Установить PYTHONPATH

**Windows (PowerShell):**
```powershell
$env:PYTHONPATH = "$PWD"
python src/workers/download_worker.py
```

**Windows (CMD):**
```cmd
set PYTHONPATH=%CD%
python src/workers/download_worker.py
python migrations/migrations.py
```

**Linux/Mac:**
```bash
export PYTHONPATH=$(pwd)
python3 src/workers/download_worker.py
python3 migrations/migrations.py
```

### Вариант 4: Использовать относительные импорты (не рекомендуется)

Можно изменить импорты в worker'ах, но это усложнит код.

## 📋 Полный запуск системы

Для работы бота нужно запустить 3 процесса:

### Терминал 1: Бот
```bash
python main.py
```

### Терминал 2: Download Worker
```bash
python run_download_worker.py
```

### Терминал 3: Analytics Worker
```bash
python run_analytics_worker.py
```

## 🐳 Или через Docker

Если используете Docker, workers запускаются автоматически через `docker-compose.yml`.
