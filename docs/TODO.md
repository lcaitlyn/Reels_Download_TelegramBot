# TODO: Аналитика и A/B тестирование

## Этап 1: Базовая инфраструктура аналитики

- [ ] Добавить PostgreSQL в requirements.txt и README
- [ ] Создать модуль `analytics_db.py` для работы с PostgreSQL
- [ ] Создать схему БД:
  - [ ] Таблица `users` (user_id, first_seen_at, last_seen_at, total_downloads, is_paid, referral_code, referred_by)
  - [ ] Таблица `downloads` (id, user_id, video_id, platform, created_at, source: 'message'|'inline'|'deep_link')
  - [ ] Таблица `videos` (video_id, platform, first_seen_at, total_downloads, last_download_at)
  - [ ] Таблица `referrals` (id, referrer_id, referred_user_id, created_at)
  - [ ] Таблица `click_events` (id, user_id, event_type: 'button_click'|'deep_link', video_id, created_at)
- [ ] Добавить миграции БД (Alembic или простой скрипт)
- [ ] Добавить REDIS_URL и DATABASE_URL в .env пример

## Этап 2: Redis счётчики для быстрых проверок

- [ ] Добавить методы в `database.py`:
  - [ ] `increment_user_downloads(user_id)` - инкремент счётчика скачиваний
  - [ ] `get_user_downloads_count(user_id)` - получить количество скачиваний
  - [ ] `increment_video_downloads(video_id)` - инкремент счётчика для видео
  - [ ] `get_user_downloads_today(user_id)` - счётчик за день (с TTL)
  - [ ] `get_user_downloads_month(user_id)` - счётчик за месяц (с TTL)
- [ ] Ключи Redis:
  - `user:{user_id}:downloads_total` - общее количество
  - `user:{user_id}:downloads:day:{YYYYMMDD}` - за день (TTL 3 дня)
  - `user:{user_id}:downloads:month:{YYYYMM}` - за месяц (TTL 2 месяца)
  - `video:{video_id}:downloads_total` - популярность видео

## Этап 3: Event-driven архитектура для аналитики

- [x] Создать модуль `events.py` с типами событий:
  - [x] `DownloadCompletedEvent` (user_id, video_id, platform, source, timestamp)
  - [x] `VideoViewClickedEvent` (user_id, video_id, event_type, timestamp)
  - [x] `UserReferredEvent` (referrer_id, new_user_id, timestamp)
- [x] Добавить очередь событий в Redis (`events:analytics_queue`)
- [x] Создать `analytics_worker.py`:
  - [x] Слушает очередь событий из Redis
  - [x] Обновляет Redis счётчики (быстро)
  - [x] Пишет в PostgreSQL (асинхронно, не блокирует)
  - [x] Обрабатывает ошибки gracefully (не падает на одном событии)

## Этап 4: Интеграция статистики в основной поток

- [x] В `bot.py` при успешном скачивании:
  - [x] Публиковать событие `DownloadCompletedEvent` в очередь (не блокируя ответ)
  - [x] Проверять лимит пользователя перед скачиванием (Redis get)
- [x] В `worker.py` после успешного скачивания:
  - [x] Публиковать событие `DownloadCompletedEvent` в очередь
- [x] В `inline_handler` при клике на кнопку:
  - [x] Публиковать событие `VideoViewClickedEvent` в очередь
- [x] В `cmd_start` при deep link:
  - [x] Публиковать событие `VideoViewClickedEvent` в очередь
  - [x] Обрабатывать referral код из параметра (заглушка, полная реализация в этапе 6)

## Этап 5: Лимиты и монетизация (10 бесплатных скачиваний)

- [x] Добавить проверку лимита в `download_and_send`:
  - [x] Проверять `user:{id}:downloads_total` из Redis
  - [x] Если > 10: пока пропускаем показ рекламы (заготовка на будущее)
  - [x] Если <= 10: обычный поток
- [x] Создать систему показа рекламы (заготовка):
  - [x] Таблица `ad_campaigns` (id, name, message, button_text, button_url, is_active, weight)
  - [ ] Ротация рекламы (A/B тесты на разных сообщениях) - будет реализовано позже
  - [x] Логирование показов рекламы в `ad_impressions` таблицу
- [x] Добавить команду `/stats` для пользователя:
  - [x] Показывать сколько скачиваний использовано
  - [x] Показывать сколько осталось бесплатных

## Этап 6: Реферальная система

- [ ] Генерация уникального referral кода для каждого пользователя
- [ ] Deep link с referral: `@bot?start=ref_ABC123`
- [ ] При первом использовании referral:
  - [ ] Сохранять связь в таблицу `referrals`
  - [ ] Давать бонусы (например, +5 бесплатных скачиваний)
  - [ ] Событие `UserReferredEvent`
- [ ] Команда `/referral` для получения своего кода
- [ ] Статистика рефералов для пользователя

## Этап 7: A/B тестирование инфраструктура

- [ ] Таблица `ab_tests` (id, name, description, is_active, start_date, end_date)
- [ ] Таблица `ab_test_variants` (id, test_id, variant_name, variant_config: JSON, traffic_percentage)
- [ ] Таблица `ab_test_assignments` (user_id, test_id, variant_id, assigned_at)
- [ ] Модуль `ab_testing.py`:
  - [ ] `assign_user_to_variant(user_id, test_name)` - назначение варианта
  - [ ] `get_user_variant(user_id, test_name)` - получение варианта пользователя
  - [ ] Использовать consistent hashing (user_id + test_name) для стабильности
- [ ] Интеграция A/B тестов:
  - [ ] A/B тест на текст рекламы (разные сообщения)
  - [ ] A/B тест на лимиты (10 vs 15 бесплатных)
  - [ ] A/B тест на UI элементов (кнопки, сообщения)

## Этап 8: Дашборд и отчёты

- [ ] Создать `dashboard.py` или веб-интерфейс:
  - [ ] Общая статистика (всего пользователей, скачиваний)
  - [ ] Топ видео (самые популярные)
  - [ ] Топ платформ (YT/IG/TT)
  - [ ] Активные пользователи по дням
  - [ ] Статистика рефералов
  - [ ] Результаты A/B тестов (конверсия, метрики)
- [ ] API endpoints для дашборда (FastAPI или Flask)
- [ ] Визуализация (графики, таблицы)

## Этап 9: Оптимизация производительности

- [ ] Batch обработка событий в analytics_worker (группировать по 10-50 событий)
- [ ] Индексы в PostgreSQL (user_id, video_id, created_at)
- [ ] Партиционирование таблицы `downloads` по датам (если будет много данных)
- [ ] Кэширование популярных запросов (топ видео, статистика)
- [ ] Мониторинг производительности (логирование медленных запросов)

## Этап 10: Расширенная аналитика

- [ ] Геолокация пользователей (по часовому поясу или IP)
- [ ] Время суток активности (когда больше всего скачиваний)
- [ ] Конверсия: клики по кнопке → скачивания
- [ ] Retention: сколько пользователей возвращаются
- [ ] Cohort анализ (группы пользователей по дате регистрации)
- [ ] Funnel анализ (inline query → click → download)

Создать статистику (рефералы, те кто клиает по "Посмотреть видео")
Добавить GIF в README.md
Сделать КЭШ не в тг канале, а более адекватный и устойчивый
Сделать обработку YouTube видел (480p, 720p, 1080p, music only и т.д)
Поддержка языков
Добавление в группу (админ в группу, парсит ссылки выдает резульаты)
Добавить GIF как пользоваться
Добавить Docker
Пофиксить загрузку YouTube video
Пофиксить ошибку при скачивании - сообщать пользователю
Пофиксить фичу чтобы качал сразу в Telegram IO
Удалить все .md файлы
Сделать Service, раскидатать по папкам