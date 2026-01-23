# План рефакторинга архитектуры

## Ключевые принципы

✅ **Сервисы НЕ качают** - PlatformService возвращает DownloadPlan, не скачивает  
✅ **Воркеры НЕ думают** - Worker только исполняет DownloadPlan  
✅ **Downloader НЕ знает платформ** - DownloadManager только координирует  
✅ **PlatformService НЕ знает Telegram** - только знает свою платформу  

---

## Текущие проблемы

1. ❌ `downloader.py` содержит методы работы с yt-dlp (должно быть в Worker)
2. ❌ `PlatformService` скачивает видео напрямую (должен возвращать DownloadPlan)
3. ❌ `LinkProcessingService` смешивает координацию и обработку ссылок
4. ❌ `Worker` использует PlatformService для скачивания (должен использовать DownloadPlan)
5. ❌ Нет DownloadPlan как отдельной сущности

---

## План рефакторинга

### Этап 1: Создание контрактов (Data Classes)

#### 1.1. DownloadPlan
**Файл:** `src/models/download_plan.py`

```python
@dataclass
class DownloadPlan:
    platform: str  # instagram, youtube, tiktok
    video_id: str
    url: str
    format_selector: str  # для yt-dlp
    quality: Optional[str] = None  # 480p, 720p, 1080p, audio
    audio_only: bool = False
    streamable: bool = True  # можно ли стримить в память
    ydl_opts: Dict[str, Any]  # опции для yt-dlp
    metadata: Optional[Dict[str, Any]] = None  # duration, filesize, etc.
```

**Ответственность:** Контракт между логикой и воркером

---

#### 1.2. LinkInfo
**Файл:** `src/models/link_info.py`

```python
@dataclass
class LinkInfo:
    platform: str
    video_id: str
    normalized_url: str
    service: BaseService  # PlatformService для этой платформы
    requires_user_input: bool = False  # для YouTube - выбор качества
```

**Ответственность:** Результат обработки ссылки LinkProcessingService

---

#### 1.3. DownloadResponse
**Файл:** `src/models/download_response.py`

```python
@dataclass
class DownloadResponse:
    status: str  # READY | QUEUED | IN_PROGRESS | ERROR | REQUIRES_USER_INPUT
    file_id: Optional[str] = None  # если READY
    message_id: Optional[int] = None  # если READY
    job_id: Optional[str] = None  # если QUEUED или IN_PROGRESS
    error: Optional[str] = None  # если ERROR
    available_qualities: Optional[List[str]] = None  # если REQUIRES_USER_INPUT
```

**Ответственность:** Ответ от DownloadManager пользователю

---

### Этап 2: Рефакторинг DownloadManager

#### 2.1. Переименование и очистка
- Переименовать `downloader.py` → `download_manager.py`
- Переименовать класс `VideoDownloader` → `DownloadManager`

#### 2.2. Удалить методы работы с yt-dlp
❌ Удалить:
- `get_video_info()`
- `get_available_formats()`
- `download_with_ydl_opts()`

✅ Оставить только:
- `get_video_id()` - для получения canonical ID (можно делегировать LinkProcessingService)

#### 2.3. Новый публичный API

```python
class DownloadManager:
    def __init__(self, db: Database, link_processor: LinkProcessingService):
        self.db = db
        self.link_processor = link_processor
    
    async def request_download(
        self,
        user_id: int,
        url: str,
        source: str = 'message',
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> DownloadResponse:
        """
        Главный метод координации
        
        1. Нормализует URL
        2. Получает LinkInfo через LinkProcessingService
        3. Проверяет кэш
        4. Проверяет активную задачу
        5. Либо возвращает READY
        6. Либо создает задачу и возвращает QUEUED
        """
        pass
```

**Ответственность:** Координация, управление задачами, кэшем, блокировками

---

### Этап 3: Рефакторинг LinkProcessingService

#### 3.1. Убрать логику координации
❌ Удалить:
- Методы работы с Redis напрямую (кэш, очереди, статусы)
- Методы работы с пользователями
- Методы работы с задачами

✅ Оставить только:
- `process_link(url) -> LinkInfo` - обработка ссылки

#### 3.2. Новая структура

```python
class LinkProcessingService:
    def __init__(self, service_factory: ServiceFactory):
        self.service_factory = service_factory
    
    def process_link(self, url: str) -> LinkInfo:
        """
        Мозг системы:
        1. Нормализует URL
        2. Определяет платформу
        3. Получает PlatformService
        4. Извлекает video_id через сервис
        5. Возвращает LinkInfo
        """
        pass
```

**Ответственность:** Понимание ссылки, выбор сервиса, извлечение video_id

---

### Этап 4: Рефакторинг PlatformService

#### 4.1. Убрать скачивание
❌ Удалить:
- `download_video()` - весь метод
- Все вызовы yt-dlp
- Все вызовы subprocess
- Работу с файлами и потоками

✅ Добавить:
- `build_download_plan(options) -> DownloadPlan`

#### 4.2. Новый интерфейс BaseService

```python
class BaseService(ABC):
    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Может ли сервис обработать этот URL"""
        pass
    
    @abstractmethod
    def extract_video_id(self, url: str) -> Optional[str]:
        """Извлечь canonical video_id"""
        pass
    
    @abstractmethod
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """Получить метаданные (duration, filesize, etc.)"""
        pass
    
    @abstractmethod
    def build_download_plan(
        self,
        url: str,
        quality: Optional[str] = None,
        format_id: Optional[str] = None
    ) -> Optional[DownloadPlan]:
        """
        Построить план скачивания
        
        Returns:
            DownloadPlan или None при ошибке
        """
        pass
```

#### 4.3. Пример InstagramService

```python
class InstagramService(BaseService):
    def build_download_plan(self, url, quality=None, format_id=None) -> Optional[DownloadPlan]:
        # Получаем метаданные
        metadata = self.get_metadata(url)
        if not metadata:
            return None
        
        # Формируем опции yt-dlp для Instagram
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'quiet': False,
            'no_warnings': False,
            'extractor_args': {'instagram': {'webpage_download': False}},
            'user_agent': 'Mozilla/5.0...',
            # ... другие опции
        }
        
        return DownloadPlan(
            platform='instagram',
            video_id=metadata['id'],
            url=url,
            format_selector='best[ext=mp4]/best',
            streamable=metadata.get('filesize', 0) < 50 * 1024 * 1024,  # <50MB
            ydl_opts=ydl_opts,
            metadata=metadata
        )
```

**Ответственность:** Знание платформы, формирование DownloadPlan

---

### Этап 5: Создание YtDlpService

#### 5.1. Новый сервис для работы с yt-dlp
**Файл:** `src/services/ytdlp_service.py`

```python
class YtDlpService:
    """
    Низкоуровневый сервис для работы с yt-dlp
    Используется ТОЛЬКО в Worker
    """
    
    def get_info(self, url: str, ydl_opts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Получить информацию о видео"""
        pass
    
    def download_to_stream(
        self,
        download_plan: DownloadPlan
    ) -> Optional[Tuple[io.BytesIO, int, str]]:
        """
        Скачать в поток (для маленьких файлов)
        Returns: (BytesIO, size, filename) или None
        """
        pass
    
    def download_to_file(
        self,
        download_plan: DownloadPlan,
        output_path: str
    ) -> Optional[Tuple[str, int, str]]:
        """
        Скачать в файл (для больших файлов)
        Returns: (file_path, size, filename) или None
        """
        pass
```

**Ответственность:** Работа с yt-dlp, скачивание файлов

---

### Этап 6: Рефакторинг Worker

#### 6.1. Сделать тупым исполнителем

```python
async def process_download_task(task: dict) -> Optional[int]:
    """
    Worker - тупой исполнитель
    
    1. Берет DownloadTask из очереди
    2. Получает LinkInfo через LinkProcessingService
    3. Получает DownloadPlan через PlatformService
    4. Исполняет DownloadPlan через YtDlpService
    5. Загружает в Telegram канал
    6. Сохраняет file_id в Redis
    7. Публикует событие VIDEO_READY
    """
    url = task['url']
    video_id = task['video_id']
    
    # Получаем LinkInfo
    link_info = link_processor.process_link(url)
    if not link_info:
        return None
    
    # Получаем DownloadPlan
    download_plan = link_info.service.build_download_plan(
        url,
        quality=task.get('quality'),
        format_id=task.get('format_id')
    )
    if not download_plan:
        return None
    
    # Исполняем через YtDlpService
    if download_plan.streamable:
        result = ytdlp_service.download_to_stream(download_plan)
    else:
        result = ytdlp_service.download_to_file(download_plan, tmp_path)
    
    if not result:
        return None
    
    # Загружаем в Telegram
    message = await bot.send_video(...)
    
    # Сохраняем в Redis
    await db.save_to_cache(...)
    
    # Публикуем событие
    await db.publish_video_download_event(...)
    
    return message.message_id
```

**Ответственность:** Исполнение DownloadPlan, загрузка в Telegram, сохранение результата

---

### Этап 7: Обновление bot.py

#### 7.1. Использование DownloadManager

```python
# Вместо:
# service = service_factory.get_service_by_url(url)
# result = service.download_video(url)

# Теперь:
response = download_manager.request_download(
    user_id=user_id,
    url=url,
    source='message',
    quality=quality,
    format_id=format_id
)

if response.status == 'READY':
    # Видео уже готово
    await bot.send_video(chat_id, video=response.file_id)
elif response.status == 'QUEUED':
    # Видео в очереди
    await message.answer("⏳ Скачиваю видео...")
    # Ждем события
elif response.status == 'REQUIRES_USER_INPUT':
    # Нужно выбрать качество (YouTube)
    await show_quality_selector(response.available_qualities)
```

---

## Порядок выполнения

1. ✅ Создать модели (DownloadPlan, LinkInfo, DownloadResponse)
2. ✅ Создать YtDlpService
3. ✅ Рефакторить PlatformService (убрать download_video, добавить build_download_plan)
4. ✅ Рефакторить LinkProcessingService (убрать координацию, оставить process_link)
5. ✅ Рефакторить DownloadManager (убрать yt-dlp, добавить request_download)
6. ✅ Рефакторить Worker (использовать DownloadPlan и YtDlpService)
7. ✅ Обновить bot.py (использовать DownloadManager)
8. ✅ Обновить все импорты

---

## Границы ответственности (финальная проверка)

| Компонент | Знает | НЕ знает |
|-----------|-------|----------|
| **DownloadManager** | Redis, очереди, кэш, пользователи | Платформы, yt-dlp, Telegram |
| **LinkProcessingService** | Платформы, сервисы, video_id | Redis, очереди, Telegram, yt-dlp |
| **PlatformService** | Свою платформу, форматы, опции | Redis, Telegram, yt-dlp (вызовы) |
| **YtDlpService** | yt-dlp, файлы, потоки | Платформы, Redis, Telegram |
| **Worker** | DownloadPlan, YtDlpService, Telegram | Пользователи, платформы, бизнес-логика |
| **bot.py** | Пользователи, UI, Telegram | Платформы, yt-dlp, Redis (напрямую) |

---

## Преимущества новой архитектуры

✅ **Легко менять настройки:**
- Формат Instagram → InstagramService.build_download_plan()
- yt-dlp аргументы → YtDlpService
- UX → bot.py
- Дедупликация → DownloadManager

✅ **Нет каскадных изменений:**
- Изменение Instagram не влияет на YouTube
- Изменение Worker не влияет на PlatformService
- Изменение DownloadManager не влияет на платформы

✅ **Легко тестировать:**
- Каждый компонент изолирован
- Можно мокировать зависимости
- Четкие контракты (DownloadPlan, LinkInfo)

✅ **Легко расширять:**
- Новая платформа = новый PlatformService
- Новый формат = изменение build_download_plan()
- Новая логика координации = изменение DownloadManager
