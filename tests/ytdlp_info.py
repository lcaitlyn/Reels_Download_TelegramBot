"""
Ссылку вставляешь — получаешь инфу для скачивания из yt-dlp (форматы, разрешения, размеры, url).
Запуск: python tests/ytdlp_info.py "https://..."
   или: python tests/ytdlp_info.py
"""
import sys

import yt_dlp


def extract_info(url: str) -> dict | None:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }
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

    # Базовая инфа
    print("\n--- О ролике ---")
    print("id:      ", info.get("id"))
    print("title:   ", (info.get("title") or "")[:80])
    print("duration:", info.get("duration"), "сек")
    print("uploader:", info.get("uploader"))
    print("ext:     ", info.get("ext"))

    # Форматы для скачивания
    formats = info.get("formats") or []
    if not formats:
        print("\n--- Форматы --- нет (или один файл)")
        u = info.get("url") or ""
        if u:
            print("url:     ", u[:80] + "..." if len(u) > 80 else u)
        return

    # Показываем не больше 25 форматов, чтобы не спамить
    MAX_FORMATS = 25
    show = formats[:MAX_FORMATS]
    print(f"\n--- Форматы для скачивания (показано {len(show)} из {len(formats)}) ---")
    print(f"{'format_id':<12} {'ext':<6} {'resolution':<12} {'size':<10} {'fps':<6} {'vcodec':<12} {'acodec':<8}")
    print("-" * 70)
    for f in show:
        fid = (f.get("format_id") or "")[:12]
        ext = (f.get("ext") or "—")[:6]
        res = f.get("resolution") or (f.get("height") and f"{f.get('width', '?')}x{f.get('height')}") or "—"
        if isinstance(res, int):
            res = str(res)
        res = (str(res) or "—")[:12]
        size = size_str(f.get("filesize") or f.get("filesize_approx"))
        fps = f.get("fps") or "—"
        vcodec = (f.get("vcodec") or "—")[:12]
        acodec = (f.get("acodec") or "—")[:8]
        print(f"{fid:<12} {ext:<6} {res:<12} {size:<10} {fps!s:<6} {vcodec:<12} {acodec:<8}")

    # Что обычно берут как "best"
    print("\n--- Для скачивания ---")
    print("Селектор best:        -f best")
    print("Лучшее видео+аудио:   -f bestvideo+bestaudio/best")
    print("Конкретный формат:    -f <format_id>  (format_id из таблицы выше)")


if __name__ == "__main__":
    main()
