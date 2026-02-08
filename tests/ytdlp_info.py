"""
Ссылку вставляешь — получаешь инфу для скачивания из yt-dlp (форматы, разрешения, размеры, url).
Запуск: python tests/ytdlp_info.py "https://..."
   или: python tests/ytdlp_info.py
"""
import sys
import json
from pathlib import Path

import yt_dlp


# Разрешения для 1080p, 720p, 480p (height может быть 1080/1920, 720/1280, 480/854)
ALLOWED_HEIGHTS = {1080, 720, 480, 1920, 1280, 854}


def _filter_formats(formats: list) -> list:
    """Оставить только 1080p, 720p, 480p (видео+звук) и audio."""
    result = []
    for f in formats or []:
        if not isinstance(f, dict):
            continue
        vcodec = (f.get("vcodec") or "none").strip().lower()
        acodec = (f.get("acodec") or "none").strip().lower()
        height = f.get("height")
        ext = (f.get("ext") or "").lower()
        # Видео со звуком: только разрешения 1080p, 720p, 480p
        if vcodec != "none" and acodec != "none":
            if height in ALLOWED_HEIGHTS:
                result.append(f)
        # Только аудио
        elif vcodec == "none" and acodec != "none" and ext in ("m4a", "webm", "mp3", "opus"):
            result.append(f)
    return result


def extract_info(url: str) -> dict | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "format_sort": ["height", "acodec"],  # сортировка: сначала по высоте, потом по наличию аудио
    }
    # Cookies для Instagram и др. (cookies/cookies.txt в корне проекта)
    cookies_path = Path(__file__).resolve().parent.parent / "cookies" / "cookies.txt"
    if cookies_path.exists():
        opts["cookiefile"] = str(cookies_path)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def size_str(n):
    if n is None:
        return "—"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1].strip()
    else:
        url = input("Вставь ссылку: ").strip()
    if not url:
        print("Ссылка пустая.")
        sys.exit(1)
    print("Запрос к yt-dlp (1–4 сек: качает страницу и список форматов с сайта)...")
    info = extract_info(url)
    if info is None:
        print("Не удалось получить инфу.")
        sys.exit(1)

    out_path = Path(__file__).resolve().parent.parent / "z4.json"
    out_data = {
        "id": info.get("id"),
        "title": info.get("title"),
        "formats": info.get("formats"),
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    # Базовая инфа
    print("\n--- О ролике ---")
    print("id:      ", info.get("id"))
    print("title:   ", (info.get("title") or "")[:80])
    print("duration:", info.get("duration"), "сек")
    print("uploader:", info.get("uploader"))
    print("ext:     ", info.get("ext"))

    # Форматы: только 1080p, 720p, 480p (видео+звук) и audio
    raw_formats = info.get("formats") or []
    filtered_formats = _filter_formats(raw_formats)
    skip_keys = {"url", "fragments", "http_headers"}
    sanitized_formats = [
        {k: v for k, v in f.items() if k not in skip_keys}
        for f in filtered_formats
    ]

    # Сохраняем отфильтрованные форматы в JSON
    out_path = Path(__file__).resolve().parent.parent / "4.json"
    out_data = {
        "id": info.get("id"),
        "title": info.get("title"),
        "formats": filtered_formats,
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(f"Форматы: {len(raw_formats)} всего → {len(filtered_formats)} после фильтра (1080p, 720p, 480p, audio)")
    print(f"formats сохранены в JSON: {out_path}")


    # # Форматы для скачивания
    # formats = info.get("formats") or []
    # if not formats:
    #     print("\n--- Форматы --- нет (или один файл)")
    #     u = info.get("url") or ""
    #     if u:
    #         print("url:     ", u[:80] + "..." if len(u) > 80 else u)
    #     return

    # # Показываем не больше 25 форматов, чтобы не спамить
    # MAX_FORMATS = 25
    # show = formats[:MAX_FORMATS]
    # print(f"\n--- Форматы для скачивания (показано {len(show)} из {len(formats)}) ---")
    # print(f"{'format_id':<12} {'ext':<6} {'resolution':<12} {'size':<10} {'fps':<6} {'vcodec':<12} {'acodec':<8}")
    # print("-" * 70)
    # for f in show:
    #     fid = (f.get("format_id") or "")[:12]
    #     ext = (f.get("ext") or "—")[:6]
    #     res = f.get("resolution") or (f.get("height") and f"{f.get('width', '?')}x{f.get('height')}") or "—"
    #     if isinstance(res, int):
    #         res = str(res)
    #     res = (str(res) or "—")[:12]
    #     size = size_str(f.get("filesize") or f.get("filesize_approx"))
    #     fps = f.get("fps") or "—"
    #     vcodec = (f.get("vcodec") or "—")[:12]
    #     acodec = (f.get("acodec") or "—")[:8]
    #     print(f"{fid:<12} {ext:<6} {res:<12} {size:<10} {fps!s:<6} {vcodec:<12} {acodec:<8}")

    # # Что обычно берут как "best"
    # print("\n--- Для скачивания ---")
    # print("Селектор best:        -f best")
    # print("Лучшее видео+аудио:   -f bestvideo+bestaudio/best")
    # print("Конкретный формат:    -f <format_id>  (format_id из таблицы выше)")


if __name__ == "__main__":
    main()
