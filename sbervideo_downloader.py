#!/usr/bin/env python3
"""Small local, cross-platform GUI for yt-dlp downloads."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("yt-dlp is missing. Install requirements.txt first.", file=sys.stderr)
    raise SystemExit(1)


APP_DIR = Path(__file__).resolve().parent
HTML = (APP_DIR / "index.html").read_bytes()
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
STATE: dict = {
    "stage": "idle",
    "message": "Готово к загрузке",
    "percent": 0,
    "speed": "",
    "bytes_text": "",
    "filename": "",
}
CANCEL = threading.Event()


class DownloadCancelled(Exception):
    pass


def update(**changes: object) -> None:
    with LOCK:
        STATE.update(changes)


def snapshot() -> dict:
    with LOCK:
        return dict(STATE)


def normalize_url(raw: str) -> tuple[str, str]:
    """Convert SberVideo meetup pages to the HLS URL seen in their player."""
    parsed = urllib.parse.urlsplit(raw.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Вставьте полную ссылку, начинающуюся с https://")
    if parsed.username or parsed.password:
        raise ValueError("Ссылки с логином или паролем в адресе не поддерживаются")

    host = (parsed.hostname or "").lower()
    parts = parsed.path.rstrip("/").split("/")
    if host == "sbervideo.sberbank.ru" and len(parts) == 3 and parts[1] == "meetups":
        meetup_id = parts[2]
        if meetup_id and all(ch.isalnum() or ch in "-_" for ch in meetup_id):
            stream = f"https://sbervideo.sberbank.ru/access_proxy/meetup/{meetup_id}/index.m3u8"
            page = f"https://sbervideo.sberbank.ru/meetups/{meetup_id}"
            return stream, page
    return raw.strip(), raw.strip()


def human_bytes(value: float | int | None) -> str:
    if value is None:
        return ""
    value = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} {unit}"
        value /= 1024
    return ""


def format_selector(quality: str) -> str:
    if quality == "best":
        return "bv+ba/b"
    height = {"1080": 1080, "720": 720, "480": 480, "360": 360}[quality]
    return f"bv[height<={height}]+ba/b[height<={height}]/best"


def download(payload: dict) -> None:
    cookie_dir = None
    try:
        url, referer = normalize_url(payload["url"])
        folder = Path(payload["folder"]).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir():
            raise ValueError("Путь сохранения не является папкой")

        browser = payload["browser"]
        quality = payload["quality"]
        insecure = payload["insecure"]
        if browser not in ("none", "chrome", "edge", "firefox", "safari", "brave", "cookies"):
            raise ValueError("Неизвестный браузер")
        if quality not in ("best", "1080", "720", "480", "360"):
            raise ValueError("Неизвестное качество")

        def progress(info: dict) -> None:
            if CANCEL.is_set():
                raise DownloadCancelled()
            status = info.get("status")
            if status == "downloading":
                got = info.get("downloaded_bytes") or 0
                total = info.get("total_bytes") or info.get("total_bytes_estimate")
                count = info.get("fragment_count")
                index = info.get("fragment_index")
                fragments = isinstance(count, int) and count > 0 and isinstance(index, int)
                done = min(count, max(0, index)) if fragments else 0
                percent = round(done * 100 / count, 1) if fragments else (round(got * 100 / total, 1) if total else 0)
                speed = human_bytes(info.get("speed"))
                update(
                    stage="downloading",
                    message=f"Фрагменты: {done} из {count} · осталось {count - done}" if fragments else f"Скачано {human_bytes(got)}" + (f" из {human_bytes(total)}" if total else ""),
                    percent=percent,
                    speed=f"{speed}/с" if speed else "",
                    bytes_text=f"Скачано {human_bytes(got)}" if fragments else "",
                )
            elif status == "finished":
                update(stage="processing", message="Собираю видеофайл…", percent=100)

        class Logger:
            def debug(self, message: str) -> None:
                pass

            def warning(self, message: str) -> None:
                pass

            def error(self, message: str) -> None:
                update(message=message)

        bundled_ffmpeg = Path(getattr(sys, "_MEIPASS", "")) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        ffmpeg = str(bundled_ffmpeg) if bundled_ffmpeg.is_file() else shutil.which("ffmpeg")
        if not ffmpeg and sys.platform == "darwin":
            for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
                if Path(candidate).is_file():
                    ffmpeg = candidate
                    break
        options = {
            "format": format_selector(quality),
            "outtmpl": str(folder / "%(title).180B [%(id)s].%(ext)s"),
            "merge_output_format": "mp4",
            "windowsfilenames": True,
            "noplaylist": True,
            "continuedl": True,
            "nocheckcertificate": insecure,
            "http_headers": {"Referer": referer},
            "progress_hooks": [progress],
            "logger": Logger(),
            "quiet": True,
            "no_warnings": False,
        }
        if browser == "cookies":
            cookie_text = payload["cookie_text"]
            cookie_dir = Path(tempfile.mkdtemp(prefix="vidloader-cookies-"))
            cookie_file = cookie_dir / "cookies.txt"
            cookie_file.write_bytes(cookie_text.encode("utf-8"))
            os.chmod(cookie_file, 0o600)
            options["cookiefile"] = str(cookie_file)
        elif browser != "none":
            options["cookiesfrombrowser"] = (browser,)
        if ffmpeg:
            options["ffmpeg_location"] = ffmpeg

        update(stage="connecting", message="Подключаюсь к сайту…")
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.extract_info(url, download=True)
            if CANCEL.is_set():
                raise DownloadCancelled()
            filename = ""
            if result:
                requested = result.get("requested_downloads") or []
                filename = requested[0].get("filepath", "") if requested else ""
                if not filename:
                    filename = ydl.prepare_filename(result)
            update(stage="done", message="Загрузка завершена", percent=100, filename=filename)
    except DownloadCancelled:
        update(stage="cancelled", message="Загрузка остановлена")
    except Exception as exc:
        update(stage="error", message=str(exc))
    finally:
        if cookie_dir is not None:
            shutil.rmtree(cookie_dir, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def respond(self, code: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return self.headers.get("X-App-Token") == TOKEN

    def do_GET(self) -> None:
        if self.path == "/":
            body = HTML.replace(b"__APP_TOKEN__", TOKEN.encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status" and self.authorized():
            self.respond(200, snapshot())
        else:
            self.respond(404, {"error": "Не найдено"})

    def do_POST(self) -> None:
        if not self.authorized():
            self.respond(403, {"error": "Нет доступа"})
            return
        if self.path == "/api/shutdown":
            if snapshot()["stage"] in ("connecting", "downloading", "processing"):
                self.respond(409, {"error": "Сначала остановите загрузку"})
                return
            self.respond(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path == "/api/cancel":
            CANCEL.set()
            self.respond(200, {"ok": True})
            return
        if self.path != "/api/start":
            self.respond(404, {"error": "Не найдено"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 8_000_000:
                raise ValueError("Слишком большой запрос")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Некорректные данные")
            normalize_url(str(payload.get("url", "")))
            if not isinstance(payload.get("insecure"), bool):
                raise ValueError("Некорректная настройка сертификата")
            if payload.get("browser") not in ("none", "chrome", "edge", "firefox", "safari", "brave", "cookies"):
                raise ValueError("Неизвестный браузер")
            if payload["browser"] == "cookies":
                cookie_text = payload.get("cookie_text")
                if not isinstance(cookie_text, str) or not cookie_text.startswith(("# HTTP Cookie File", "# Netscape HTTP Cookie File")) or len(cookie_text) > 4_000_000:
                    raise ValueError("Выберите файл cookies.txt в формате Netscape")
            if payload.get("quality") not in ("best", "1080", "720", "480", "360"):
                raise ValueError("Неизвестное качество")
            if not isinstance(payload.get("folder"), str) or not payload["folder"].strip():
                raise ValueError("Укажите папку для сохранения")
            with LOCK:
                if STATE["stage"] in ("connecting", "downloading", "processing"):
                    self.respond(409, {"error": "Загрузка уже идёт"})
                    return
                STATE.update(stage="connecting", message="Начинаю загрузку…", percent=0, speed="", bytes_text="", filename="")
                CANCEL.clear()
            threading.Thread(target=download, args=(payload,), daemon=True).start()
            self.respond(200, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    address = f"http://127.0.0.1:{port}/"
    print(f"Откройте приложение: {address}")
    print("Чтобы закрыть приложение, нажмите Ctrl+C в этом окне.")
    if os.environ.get("SBERVIDEO_NO_BROWSER") != "1":
        threading.Timer(0.4, webbrowser.open, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
