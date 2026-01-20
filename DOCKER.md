# Docker Setup для Downloader Bot

## Быстрый старт

1. **Создайте файл `.env`** на основе `.env.example`:
   ```bash
   cp .env.example .env
   ```

2. **Заполните необходимые переменные** в `.env`:
   - `BOT_TOKEN` - токен вашего Telegram бота
   - `TELEGRAM_CHANNEL_ID` - ID или username канала для кэширования видео

3. **Запустите все сервисы**:
   ```bash
   docker-compose up -d
   ```

4. **Проверьте статус**:
   ```bash
   docker-compose ps
   ```

5. **Просмотрите логи**:
   ```bash
   # Все логи
   docker-compose logs -f
   
   # Логи конкретного сервиса
   docker-compose logs -f bot
   docker-compose logs -f worker1
   docker-compose logs -f analytics_worker
   ```

## Структура сервисов

- **bot** - Основной Telegram бот
- **worker1, worker2** - Воркеры для скачивания видео (2 экземпляра)
- **analytics_worker** - Воркер для обработки событий аналитики
- **dashboard** - API дашборд (доступен на http://localhost:8000)
- **redis** - Redis для кэширования и очередей
- **postgres** - PostgreSQL для аналитики

## Полезные команды

### Остановка всех сервисов
```bash
docker-compose down
```

### Остановка с удалением volumes (удалит все данные!)
```bash
docker-compose down -v
```

### Пересборка образов
```bash
docker-compose build
```

### Перезапуск конкретного сервиса
```bash
docker-compose restart bot
```

### Масштабирование воркеров
Если нужно больше воркеров, можно запустить дополнительные:
```bash
docker-compose up -d --scale worker=3
```

Но лучше добавить worker3, worker4 в docker-compose.yml для продакшена.

## Первый запуск

При первом запуске нужно инициализировать схему PostgreSQL:

```bash
# Запустите миграции (профиль init)
docker-compose --profile init up migrations

# Или после запуска всех сервисов
docker-compose exec postgres psql -U postgres -d analytics -c "SELECT 1;" || docker-compose run --rm migrations
```

Миграции также можно запустить вручную:
```bash
docker-compose run --rm migrations
```

## Мониторинг

- **Dashboard API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Health check**: http://localhost:8000/health

## Production рекомендации

1. **Измените пароли** в `.env` для PostgreSQL
2. **Используйте секреты** вместо `.env` файла (Docker secrets, Kubernetes secrets и т.д.)
3. **Настройте резервное копирование** для PostgreSQL и Redis
4. **Используйте reverse proxy** (nginx) перед dashboard
5. **Настройте мониторинг** (Prometheus, Grafana)
6. **Ограничьте ресурсы** в docker-compose.yml:
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '0.5'
         memory: 512M
   ```

## Troubleshooting

### Ошибка DNS: "Name or service not known"

Если воркеры не могут скачать видео из-за ошибки DNS:

1. **Проверьте DNS настройки** в `docker-compose.yml` (должны быть `dns: [127.0.0.11, 8.8.8.8, 8.8.4.4]`)
   - `127.0.0.11` - Docker's internal DNS (использует DNS хоста, работает с VPN)
   - `8.8.8.8`, `8.8.4.4` - Google DNS как fallback

2. **Если используете VPN**:
   - Docker использует DNS хоста через `127.0.0.11`, что должно работать с VPN
   - Если VPN блокирует Google DNS, убедитесь что `127.0.0.11` первый в списке
   - Проверьте, что VPN не блокирует доступ к Instagram/YouTube/TikTok

3. **Перезапустите контейнеры**:
   ```bash
   docker-compose down
   docker-compose up -d
   ```

4. **Проверьте доступность интернета из контейнера**:
   ```bash
   # Проверка ping
   docker-compose exec worker1 ping -c 3 8.8.8.8
   
   # Проверка DNS разрешения (должно вернуть IP адреса)
   docker-compose exec worker1 nslookup instagram.com
   docker-compose exec worker1 nslookup www.youtube.com
   
   # Проверка DNS серверов в контейнере (должен быть 127.0.0.11)
   docker-compose exec worker1 cat /etc/resolv.conf
   ```

   **Ожидаемый результат `/etc/resolv.conf`**:
   ```
   nameserver 127.0.0.11
   options ndots:0
   ```
   
   Если видите `127.0.0.11` - это правильно! Docker использует внутренний DNS резолвер, который проксирует запросы к DNS хоста.

5. **Если проблема сохраняется**:
   ```bash
   # Проверьте DNS настройки хоста
   cat /etc/resolv.conf
   
   # Если VPN активен, проверьте его DNS настройки
   # Некоторые VPN требуют специальных настроек для Docker
   
   # Перезапустите Docker daemon (если возможно)
   sudo systemctl restart docker
   ```

6. **Альтернативное решение** (если VPN полностью блокирует DNS):
   - Временно отключите VPN для теста
   - Или настройте VPN так, чтобы он не перехватывал DNS запросы Docker
   - Или используйте другой DNS сервер, который работает через VPN

### Бот не запускается
- Проверьте `BOT_TOKEN` в `.env`
- Проверьте логи: `docker-compose logs bot`

### Воркеры не обрабатывают задачи
- Проверьте подключение к Redis: `docker-compose logs redis`
- Проверьте логи воркеров: `docker-compose logs worker1`
- Проверьте DNS (см. выше)

### Dashboard не работает
- Проверьте подключение к PostgreSQL: `docker-compose logs postgres`
- Проверьте логи dashboard: `docker-compose logs dashboard`

### Проблемы с дисковым пространством
- Очистите старые образы: `docker system prune -a`
- Очистите volumes (осторожно!): `docker volume prune`
