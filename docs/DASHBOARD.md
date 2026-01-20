# Dashboard API - Инструкция по использованию

Dashboard предоставляет REST API для просмотра аналитики бота.

## 🚀 Запуск

### Вариант 1: Docker (рекомендуется)
```bash
# Dashboard запускается автоматически с docker-compose
docker-compose up -d dashboard

# Проверьте логи
docker-compose logs -f dashboard
```

### Вариант 2: Локально
```bash
# Убедитесь, что PostgreSQL запущен и база данных создана
python3 migrations.py

# Запустите dashboard
python3 dashboard.py
# или
uvicorn dashboard:app --host 0.0.0.0 --port 8000
```

Dashboard будет доступен по адресу: **http://localhost:8000**

## 📊 Доступные эндпоинты

### 1. Health Check
**GET** `/health`

Проверка работоспособности сервиса.

**Пример:**
```bash
curl http://localhost:8000/health
```

**Ответ:**
```json
{
  "status": "ok"
}
```

---

### 2. Общая сводка
**GET** `/stats/summary`

Получить общую статистику:
- Количество пользователей
- Количество видео
- Общее количество скачиваний

**Пример:**
```bash
curl http://localhost:8000/stats/summary
```

**Ответ:**
```json
{
  "users_count": 150,
  "videos_count": 500,
  "total_downloads": 2500
}
```

---

### 3. Топ популярных видео
**GET** `/stats/top-videos?limit=10`

Получить список самых популярных видео.

**Параметры:**
- `limit` (опционально) - количество видео (по умолчанию 10, максимум 100)

**Пример:**
```bash
curl http://localhost:8000/stats/top-videos?limit=20
```

**Ответ:**
```json
{
  "items": [
    {
      "video_id": "youtube:ABC123",
      "platform": "youtube",
      "total_downloads": 150,
      "last_download_at": "2026-01-20T10:30:00"
    },
    ...
  ],
  "limit": 20
}
```

---

### 4. Статистика по платформам
**GET** `/stats/platforms`

Получить количество скачиваний по каждой платформе.

**Пример:**
```bash
curl http://localhost:8000/stats/platforms
```

**Ответ:**
```json
{
  "youtube": 1200,
  "instagram": 800,
  "tiktok": 500
}
```

---

### 5. Активные пользователи
**GET** `/stats/active-users?days=7`

Получить количество активных пользователей за последние N дней.

**Параметры:**
- `days` (опционально) - количество дней (по умолчанию 7, максимум 365)

**Пример:**
```bash
curl http://localhost:8000/stats/active-users?days=30
```

**Ответ:**
```json
{
  "days": 30,
  "active_users": 45
}
```

---

## 📖 Интерактивная документация (Swagger UI)

FastAPI автоматически генерирует интерактивную документацию:

**Swagger UI:** http://localhost:8000/docs

**ReDoc:** http://localhost:8000/redoc

В Swagger UI вы можете:
- Просмотреть все эндпоинты
- Протестировать API прямо в браузере
- Увидеть примеры запросов и ответов

---

## 💡 Примеры использования

### Получить всю статистику одной командой:
```bash
# Общая сводка
curl http://localhost:8000/stats/summary

# Топ 10 видео
curl http://localhost:8000/stats/top-videos

# Статистика по платформам
curl http://localhost:8000/stats/platforms

# Активные пользователи за неделю
curl http://localhost:8000/stats/active-users?days=7
```

### Использование в скриптах (Python):
```python
import requests

# Получить общую статистику
response = requests.get("http://localhost:8000/stats/summary")
data = response.json()
print(f"Пользователей: {data['users_count']}")
print(f"Видео: {data['videos_count']}")
print(f"Скачиваний: {data['total_downloads']}")

# Получить топ видео
response = requests.get("http://localhost:8000/stats/top-videos?limit=5")
top_videos = response.json()['items']
for video in top_videos:
    print(f"{video['platform']}: {video['total_downloads']} скачиваний")
```

### Использование в JavaScript (fetch):
```javascript
// Получить статистику
fetch('http://localhost:8000/stats/summary')
  .then(response => response.json())
  .then(data => {
    console.log('Пользователей:', data.users_count);
    console.log('Видео:', data.videos_count);
    console.log('Скачиваний:', data.total_downloads);
  });
```

---

## 🔒 Безопасность

**Важно:** Текущая версия Dashboard не имеет аутентификации. Для продакшена рекомендуется:

1. Добавить аутентификацию (API ключи, JWT токены)
2. Использовать reverse proxy (nginx) с базовой аутентификацией
3. Ограничить доступ по IP (firewall)
4. Использовать HTTPS

Пример с nginx и базовой аутентификацией:
```nginx
location /dashboard/ {
    auth_basic "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://localhost:8000/;
}
```

---

## 🐛 Troubleshooting

### Dashboard не запускается
```bash
# Проверьте логи
docker-compose logs dashboard

# Проверьте подключение к PostgreSQL
docker-compose exec dashboard python -c "from analytics_db import AnalyticsDB; import asyncio; db = AnalyticsDB(); asyncio.run(db.connect())"
```

### Нет данных в ответах
- Убедитесь, что `analytics_worker` запущен и обрабатывает события
- Проверьте, что в PostgreSQL есть данные:
  ```sql
  SELECT COUNT(*) FROM users;
  SELECT COUNT(*) FROM downloads;
  ```

### Ошибка подключения к PostgreSQL
- Проверьте переменные окружения: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- Убедитесь, что PostgreSQL запущен и доступен
