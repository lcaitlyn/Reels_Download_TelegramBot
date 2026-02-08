import os
from typing import List, Tuple, Union

# Загружаем .env при импорте (если есть dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_int_env(name: str, default: int) -> int:
    """Прочитать переменную окружения как int; при ошибке или отсутствии — default."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_float_env(name: str, default: float) -> float:
    """Прочитать переменную окружения как float; при ошибке или отсутствии — default."""
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def get_youtube_quality_heights() -> List[Tuple[Union[int, List[int]], str]]:
    """
    Список качеств для выбора при скачивании YouTube (обычное видео, не Shorts).
    Каждый элемент: (высота или список высот в пикселях, метка для кнопки).

    Переменная окружения: YOUTUBE_QUALITY_HEIGHTS (через запятую, числа в пикселях).
    Пример: YOUTUBE_QUALITY_HEIGHTS=1080,720,480
    По умолчанию: 1080p, 720p, 480p (audio добавляется в коде отдельно).
    """
    raw = os.getenv("YOUTUBE_QUALITY_HEIGHTS", "1080,720,480")
    result: List[Tuple[Union[int, List[int]], str]] = []
    for s in raw.split(","):
        s = s.strip()
        if not s:
            continue
        try:
            height = int(s)
            result.append((height, f"{height}p"))
        except ValueError:
            continue
    return result if result else [(1080, "1080p"), (720, "720p"), (480, "480p")]


def get_youtube_quality_labels() -> List[str]:
    """
    Порядок меток качеств для кнопок (1080p, 720p, 480p, audio).
    Совпадает с get_youtube_quality_heights() + audio.
    """
    return [label for _, label in get_youtube_quality_heights()] + ["audio"]


def get_youtube_quality_display_map() -> dict:
    """Словарь метка -> отображаемая строка для кнопок (🎦 1080p, 🎵 Audio и т.д.)."""
    labels = get_youtube_quality_labels()
    return {
        label: "🎵 Audio" if label == "audio" else f"🎦 {label}"
        for label in labels
    }


def get_youtube_shorts_quality_order() -> List[str]:
    """
    Порядок приоритета форматов для YouTube Shorts (только форматы с видео+аудио).
    
    Значения соответствуют format_note из yt-dlp, например:
    (original), (default), 1080p60, 1080p, 720p60, 720p.
    
    Переменная окружения: YOUTUBE_SHORTS_QUALITY_ORDER (через запятую).
    По умолчанию: (original),(default),1080p60,1080p,720p60,720p
    """
    raw = os.getenv(
        "YOUTUBE_SHORTS_QUALITY_ORDER",
        "(original),(default),1080p60,1080p,720p60,720p"
    )
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_max_file_size_mb() -> float:
    """
    Максимальный размер загружаемого файла в МБ.
    Файлы больше лимита не сохраняются и не отправляются в канал.

    Переменная окружения: MAX_FILE_SIZE_MB (число).
    По умолчанию: 100
    """
    raw = os.getenv("MAX_FILE_SIZE_MB", "100")
    try:
        return float(raw)
    except ValueError:
        return 100.0
