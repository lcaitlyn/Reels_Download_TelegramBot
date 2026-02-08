# Алгоритм обработки YouTube в боте

## Общий поток (любая ссылка)

1. **Пользователь** шлёт ссылку (сообщением или inline).
2. **Бот** (`bot.py`): нормализует URL, определяет платформу (`get_platform` → youtube).
3. **Бот** вызывает `handle_youtube_quality_selection` или сразу ставит задачу в очередь.
4. **YouTube** → показывается меню качества (кнопки 480p, 720p, 1080p, Audio) — список форматов берётся из `get_available_formats(url)`.
5. **Пользователь** жмёт кнопку (например 720p).
6. **Бот** вызывает `download_manager.request_download(url, quality='720p', format_id=...)` — в очередь Redis кладётся задача `{ url, video_id, platform, quality, format_id }`.
7. **Воркер** забирает задачу, получает `LinkInfo` и `DownloadPlan`, качает через `YtDlpService`, заливает в канал, сохраняет в кэш, шлёт событие.
8. **Бот** по событию отдаёт пользователю видео из канала (`copy_message`).

---

## Как работает `downloader/downloader.py` (get_available_formats)

**Назначение:** по URL вернуть словарь форматов для кнопок качества: `{ '480p': { format_id, filesize, ext }, '720p': {...}, '1080p': {...}, 'audio': {...} }`.

1. Вызов **yt-dlp** `extract_info(url, download=False)` → список `formats` из ответа YouTube.
2. По каждому формату смотрим `height`, `vcodec`, `acodec`, `format_id`, `filesize`, `ext`.
3. **Аудио:** `vcodec=='none'` и `acodec!='none'` → в список `audio_formats`.
4. **Видео:** `vcodec!='none'` и есть `height` → в `video_formats[height]` с флагом `has_audio = (acodec != 'none')`.
5. Дальше — **два режима**.

### Режим Shorts (`'shorts' in url`)

Вертикаль 9:16. Берём **только форматы с видео+аудио** (muxed), приоритет по `format_note` из yt-dlp.

- **Настройка:** в `.env` задаётся порядок приоритета форматов:
  ```env
  YOUTUBE_SHORTS_QUALITY_ORDER=(original),(default),1080p60,1080p,720p60,720p
  ```
  Значения соответствуют полю `format_note` (например `(original)`, `(default)`, `1080p`, `720p60`). Список через запятую: от самого желаемого к fallback.

- **Логика:** `YtDlpService.get_best_shorts_format_id(url, quality_order)` получает форматы, оставляет только muxed (`vcodec` и `acodec` не `none`), сортирует по приоритету `format_note`, затем по высоте; возвращает `format_id` лучшего (например `96`). Если подходящего формата нет — используется fallback по высоте: `best[height<=1280][ext=mp4]/best[height<=1280]/best[height<=854]/best`.

### Режим обычное видео (не Shorts)

Учитываем и горизонталь 16:9, и вертикальные варианты (если есть): у одного видео могут быть и 854×480, и 480×854.

| Метка качества | 16:9 (горизонт.)        | 9:16 (вертик., если есть) | Поле `height` в yt-dlp для выбора |
|----------------|--------------------------|----------------------------|------------------------------------|
| **480p**       | 854×**480**              | 480×**854**                | **480** или **854**                |
| **720p**       | 1280×**720**             | 720×**1280**               | **720** или **1280**               |
| **1080p**      | 1920×**1080**            | 1080×**1920**              | **1080** или **1920**              |

- В коде: для 480p допустимые heights = `[480, 854]`, для 720p = `[720, 1280]`, для 1080p = `[1080, 1920]`.
- Для каждой метки собираем форматы с этими heights; приоритет — с аудио, затем меньший размер. Так 480p даёт 94 (480×854, height 854), а не 92 (240×426).
- Потом добавляется **audio** (лучший аудио-формат).

Итог: кнопки 480p, 720p, 1080p, Audio; для 480p — format_id 94 (480×854), для 720p — из 720 или 1280 и т.д.

---

## Как работает `services/youtube.py`

**Назначение:** по URL + качеству/format_id построить **DownloadPlan** (url, format_selector, ydl_opts и т.д.). Селектор формата определяет, что именно скачает yt-dlp.

### Определение Shorts

- `_is_shorts_url(url)` → `'shorts' in url.lower()` (например `youtube.com/shorts/VIDEO_ID`).

### build_download_plan(url, quality, format_id)

1. Достаём `video_id` из URL.
2. `is_shorts = _is_shorts_url(url)`.
3. **format_selector** = `_prepare_format_selector(format_id, quality, is_shorts=is_shorts)`.
4. Собираем `ydl_opts` (format, quiet, noplaylist и т.д.).
5. Возвращаем `DownloadPlan(platform='youtube', video_id, url, format_selector, quality, ydl_opts=...)`.

### _prepare_format_selector(format_id, quality, is_shorts)

Порядок проверок **критичен**.

1. **Shorts** (`is_shorts=True`):
   - Игнорируем `format_id` (мог прийти 779 и т.п.).
   - 720p → `bestvideo[height<=720][ext=mp4]+bestaudio/best`
   - 1080p → `bestvideo[height<=1080][ext=mp4]+bestaudio/best`
   - По умолчанию → то же, что 720p.

2. **Обычное видео, задано качество 480p/720p/1080p:**
   - **Сначала** проверяем `quality in ('480p','720p','1080p')` и возвращаем селектор по качеству (bestvideo[height<=...]+bestaudio/best).
   - **Не используем** format_id (92, 93, 94, 300 и т.д. — HLS; при `-f 300` yt-dlp часто даёт "The downloaded file is empty").

3. **Иначе, если передан format_id:**
   - audio-only (140, 250, …) → возвращаем как есть.
   - video-only (137, 134, 779, …) → возвращаем `format_id+bestaudio/best`.
   - Остальное → возвращаем format_id (риск пустого файла для HLS).

4. **Иначе, если quality == 'audio':**  
   `bestaudio[ext=m4a]/bestaudio[ext=webm]/...`

5. **По умолчанию:**  
   `bestvideo[height<=360][ext=mp4]+bestaudio/best`

Итог: для Shorts и для выбора 480p/720p/1080p у обычного видео воркер **всегда** получает селектор вида `bestvideo[height<=...]+bestaudio/best`, а не сырой format_id (92, 300 и т.д.).

---

## Что происходит при Shorts

1. URL вида `youtube.com/shorts/VIDEO_ID` или с путём `/shorts/`.
2. **get_available_formats(url):** в downloader по `'shorts' in url` включается режим Shorts → только 720p и 1080p с heights 1280 и 1920, muxed (95-0, 96-0 и т.д.).
3. **Бот** показывает только две кнопки: 720p и 1080p.
4. Пользователь жмёт, например, 1080p → в очередь уходит `quality=1080p`, `format_id=96-0` (или аналог).
5. **Воркер** вызывает `build_download_plan(url, quality='1080p', format_id='96-0')`. В youtube.py `_is_shorts_url(url)=True` → в `_prepare_format_selector` для Shorts **format_id не используется**, только quality → возвращается `bestvideo[height<=1080][ext=mp4]+bestaudio/best`.
6. **YtDlpService** получает план с этим селектором. В `download_to_stream` селектор содержит `+` → потоковый режим пропускается (return None). Используется `download_to_file_pipelined` или `download_to_file` → yt-dlp мержит видео+аудио в файл → звук есть.

---

## Что происходит при обычном видео

1. URL вида `youtube.com/watch?v=VIDEO_ID`.
2. **get_available_formats(url):** режим не Shorts → 480p/720p/1080p по парам (480,854), (720,1280), (1080,1920); плюс audio. Для 480p могут быть форматы с height 480 и 854 (94 и т.д.).
3. Пользователь жмёт, например, 720p → в очередь уходит `quality=720p`, `format_id=300` (или 93-0 и т.д.).
4. **Воркер** вызывает `build_download_plan(url, quality='720p', format_id='300')`. В `_prepare_format_selector` **сначала** проверяется `quality in ('480p','720p','1080p')` → возвращается `bestvideo[height<=720][ext=mp4]+bestaudio/best`, **format_id=300 не используется**.
5. Дальше то же, что для Shorts: селектор с `+` → не stream, а file/pipelined → мерж → видео со звуком.

---

## Как воркер качает (YtDlpService)

1. **download_to_stream(plan):** если в `format_selector` есть `+` → сразу return None (в pipe мерж ненадёжен, будет без звука). Иначе subprocess `yt-dlp -f <selector> -o -` и чтение stdout.
2. Если stream не сработал → **download_to_file_pipelined(plan):** subprocess `yt-dlp -f <selector> -o <tmp_path>.%(ext)s` → вывод в файл, yt-dlp сам мержит video+audio в файл.
3. Если и это не сработало → **download_to_file(plan):** Python API `yt_dlp.YoutubeDL(ydl_opts).download([url])` с тем же селектором и outtmpl в файл.

Для селектора `bestvideo+bestaudio` yt-dlp качает два потока и склеивает через ffmpeg; нужен ffmpeg в PATH (в Docker он есть).

---

## Почему было "The downloaded file is empty"

- Воркер получал задачу с **quality=720p** и **format_id=300**.
- В `_prepare_format_selector` проверка шла в порядке: **сначала** `if format_id`, **потом** `if quality`. Поэтому возвращался сырой **"300"** (HLS), а не селектор по 720p.
- При `-f 300` yt-dlp для такого формата часто отдаёт пустой файл.
- **Исправление:** для обычного видео при 480p/720p/1080p **сначала** проверять quality и возвращать `bestvideo[height<=...]+bestaudio/best`, и только потом учитывать format_id. Так воркер всегда получает рабочий селектор и качает со звуком.
