#!/usr/bin/env python3
"""MeetKeep: a small desktop app that saves meeting recordings with yt-dlp."""

from __future__ import annotations

import faulthandler
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("yt-dlp is missing. Install requirements.txt first.", file=sys.stderr)
    raise SystemExit(1)


APP_NAME = "MeetKeep"
LEGACY_NAME = "Vidloader"
APP_DIR = Path(__file__).resolve().parent
# Frozen builds ship the React build as ui/; a source checkout uses frontend/dist.
UI_DIR = APP_DIR / "ui" if (APP_DIR / "ui").is_dir() else APP_DIR / "frontend" / "dist"
PLATFORM = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
TOKEN = secrets.token_urlsafe(32)
QUALITIES = ("best", "1080", "720", "480", "360")
BROWSERS = ("none", "chrome", "edge", "firefox", "safari", "brave", "cookies")
ACTIVE = ("connecting", "downloading", "processing")
# Extractors that name videos after the page or playlist file (HTML5MediaEmbed also appends " (1)").
GENERIC_EXTRACTORS = ("Generic", "HTML5MediaEmbed")
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".json": "application/json",
}

LOCK = threading.Lock()
CANCEL = threading.Event()
STOP = threading.Event()
WINDOW = None
SMOKE = "--smoke-test" in sys.argv
SMOKE_OK = False
PAIR_KEY = ""
EXTENSION_DIR = Path()
COOKIE_TEXT = ""
STATE: dict = {
    "stage": "idle", "message": "", "id": "", "title": "", "url": "", "filename": "",
    "percent": 0, "downloaded": 0, "total": 0, "speed": 0, "fragments": 0, "fragment": 0, "revision": 0,
}


class DownloadCancelled(Exception):
    pass


def config_dir() -> Path:
    if PLATFORM == "windows":
        root = Path(os.environ.get("APPDATA", str(Path.home())))
    elif PLATFORM == "macos":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    path, legacy = root / APP_NAME, root / LEGACY_NAME
    # Keep the extension pairing key and settings from the Vidloader days.
    if not path.exists() and legacy.is_dir():
        try:
            legacy.rename(path)
        except OSError:
            pass
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_DIR = config_dir()
SETTINGS_FILE = CONFIG_DIR / "settings.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
SETTINGS: dict = {
    "quality": "best",
    "folder": str(Path.home() / "Downloads"),
    "insecure": False,
    "browser": "chrome" if PLATFORM == "macos" else "none",
    "extension_seen": False,
}
HISTORY: list[dict] = []


def read_json(path: Path, default: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value: object) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def checked_settings(values: dict) -> dict:
    """Validate a settings change coming from the UI; unknown keys are ignored."""
    result = {}
    if "quality" in values:
        if values["quality"] not in QUALITIES:
            raise ValueError("Неизвестное качество")
        result["quality"] = values["quality"]
    if "folder" in values:
        if not isinstance(values["folder"], str) or not values["folder"].strip():
            raise ValueError("Укажите папку для записей")
        result["folder"] = values["folder"].strip()
    if "insecure" in values:
        if not isinstance(values["insecure"], bool):
            raise ValueError("Некорректная настройка сертификата")
        result["insecure"] = values["insecure"]
    if "browser" in values:
        if values["browser"] not in BROWSERS:
            raise ValueError("Неизвестный способ входа")
        result["browser"] = values["browser"]
    return result


def load_data() -> None:
    saved = read_json(SETTINGS_FILE, {})
    if isinstance(saved, dict):
        try:
            SETTINGS.update(checked_settings(saved))
        except ValueError:
            pass
        SETTINGS["extension_seen"] = saved.get("extension_seen") is True
    history = read_json(HISTORY_FILE, [])
    if isinstance(history, list):
        HISTORY[:] = [item for item in history if isinstance(item, dict) and isinstance(item.get("id"), str)][:200]


def changed() -> None:
    """Persist settings and history and let the UI know it should refetch them. Call with LOCK held."""
    STATE["revision"] += 1
    try:
        write_json(SETTINGS_FILE, SETTINGS)
        write_json(HISTORY_FILE, HISTORY)
    except OSError:
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


def clean_title(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()[:300]


def html_title(ydl: "yt_dlp.YoutubeDL", page: str) -> str:
    """og:title or <title> of the page, fetched with the same cookies as the video."""
    try:
        with ydl.urlopen(page) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            text = response.read(2_000_000).decode(charset, "replace")
    except Exception:
        return ""
    for pattern in (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title',
                    r"<title[^>]*>([^<]+)</title>"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match and clean_title(match.group(1)):
            return clean_title(match.group(1))
    return ""


def unique_template(ydl: "yt_dlp.YoutubeDL", info: dict, folder: Path) -> None:
    """Add " (2)", " (3)"… when a finished video with the same name is already there."""
    stem = Path(ydl.prepare_filename({**info, "ext": "mp4"})).stem
    taken = lambda name: any((folder / f"{name}.{ext}").exists() for ext in ("mp4", "mkv", "webm", "mov", "m4a"))
    if not taken(stem):
        return
    number = 2
    while taken(f"{stem} ({number})"):
        number += 1
    ydl.params["outtmpl"]["default"] = str(folder / f"{stem} ({number})".replace("%", "%%")) + ".%(ext)s"


def format_selector(quality: str) -> str:
    if quality == "best":
        return "bv+ba/b"
    return f"bv[height<={quality}]+ba/b[height<={quality}]/best"


def bundled_ffmpeg() -> str | None:
    bundled = Path(getattr(sys, "_MEIPASS", "")) / ("ffmpeg.exe" if PLATFORM == "windows" else "ffmpeg")
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ffmpeg")
    if not found and PLATFORM == "macos":
        found = next((path for path in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg") if Path(path).is_file()), None)
    return found


def friendly_error(message: str, browser: str) -> str:
    text = re.sub(r"^ERROR:\s*", "", message.strip())
    if "Could not copy Chrome cookie database" in text or "Failed to decrypt with DPAPI" in text:
        return "Chrome не отдаёт вход напрямую. Откройте встречу в Chrome и нажмите значок MeetKeep"
    if browser == "none" and re.search(r"HTTP Error 40[13]", text):
        if PLATFORM == "windows":
            return "Нужен вход на сайт. Откройте встречу в Chrome и нажмите значок MeetKeep"
        return "Нужен вход на сайт. Выберите в настройках браузер, в котором вы вошли"
    return text or "Не удалось скачать запись"


def download(payload: dict) -> None:
    cookie_dir = None
    details: dict = {}
    browser = payload["browser"]
    try:
        url, referer = normalize_url(payload["url"])
        folder = Path(payload["folder"]).expanduser().resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.is_dir():
            raise ValueError("Путь сохранения не является папкой")

        def progress(info: dict) -> None:
            if CANCEL.is_set():
                raise DownloadCancelled()
            if info.get("status") == "downloading":
                got = info.get("downloaded_bytes") or 0
                total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
                count, index = info.get("fragment_count"), info.get("fragment_index")
                fragments = isinstance(count, int) and count > 0 and isinstance(index, int)
                done = min(count, max(0, index)) if fragments else 0
                update(
                    stage="downloading",
                    percent=round(done * 100 / count, 1) if fragments else (round(got * 100 / total, 1) if total else 0),
                    downloaded=got, total=0 if fragments else total, speed=info.get("speed") or 0,
                    fragments=count if fragments else 0, fragment=done,
                )
            elif info.get("status") == "finished":
                update(stage="processing", percent=100)

        class Logger:
            def debug(self, message: str) -> None:
                pass

            def warning(self, message: str) -> None:
                pass

            def error(self, message: str) -> None:
                update(message=message)

        options = {
            "format": format_selector(payload["quality"]),
            "outtmpl": str(folder / "%(title).180B.%(ext)s"),
            "merge_output_format": "mp4",
            "windowsfilenames": True,
            "noplaylist": True,
            "continuedl": True,
            "nocheckcertificate": payload["insecure"],
            "http_headers": {"Referer": referer},
            "progress_hooks": [progress],
            "logger": Logger(),
            "quiet": True,
            "no_warnings": False,
        }
        if browser == "cookies":
            cookie_dir = Path(tempfile.mkdtemp(prefix="meetkeep-cookies-"))
            cookie_file = cookie_dir / "cookies.txt"
            cookie_file.write_bytes(payload["cookie_text"].encode("utf-8"))
            os.chmod(cookie_file, 0o600)
            options["cookiefile"] = str(cookie_file)
        elif browser != "none":
            options["cookiesfrombrowser"] = (browser,)
        ffmpeg = bundled_ffmpeg()
        if ffmpeg:
            options["ffmpeg_location"] = ffmpeg

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            if CANCEL.is_set():
                raise DownloadCancelled()
            # Site extractors already know the real title; the generic one only sees a page or playlist name.
            if info.get("extractor_key") in GENERIC_EXTRACTORS:
                title = clean_title(payload.get("title"))
                # Only fetch when the address is an HTML page, not the media file itself.
                if not title and (referer != url or info["extractor_key"] == "HTML5MediaEmbed"):
                    title = html_title(ydl, referer)
                if title:
                    info["title"] = title
            update(title=clean_title(info.get("title")) or clean_title(payload.get("title")))
            if info.get("_type", "video") == "video":
                unique_template(ydl, info, folder)
            result = ydl.process_ie_result(info, download=True)
            if CANCEL.is_set():
                raise DownloadCancelled()
            filename = ""
            if result:
                requested = result.get("requested_downloads") or []
                filename = requested[0].get("filepath", "") if requested else ""
                filename = filename or ydl.prepare_filename(result)
                details["height"] = result.get("height") or 0
                if result.get("title"):
                    update(title=clean_title(result["title"]))
            update(stage="done", message="", percent=100, filename=filename)
    except DownloadCancelled:
        update(stage="cancelled", message="Загрузка остановлена")
    except Exception as exc:
        update(stage="error", message=friendly_error(str(exc), browser))
    finally:
        if cookie_dir is not None:
            shutil.rmtree(cookie_dir, ignore_errors=True)
        record(payload, details)


def record(payload: dict, details: dict) -> None:
    """Move the finished, failed or stopped download into the history."""
    with LOCK:
        state = dict(STATE)
        if state["stage"] not in ("done", "error", "cancelled") or state["id"] != payload["id"]:
            return
        filename = state["filename"]
        try:
            size = Path(filename).stat().st_size if filename else 0
        except OSError:
            size = 0
        HISTORY.insert(0, {
            "id": payload["id"], "url": payload["url"], "title": state["title"] or payload["url"],
            "status": state["stage"], "message": state["message"], "filename": filename,
            "size": size, "height": details.get("height", 0), "finished_at": time.time(),
        })
        del HISTORY[200:]
        changed()


def start_download(url: str, extra: dict | None = None) -> None:
    """Validate and start a download with the saved settings; raises ValueError for the user."""
    normalize_url(url)
    with LOCK:
        payload = {**SETTINGS, "url": url.strip(), **(extra or {})}
        if payload["browser"] == "cookies":
            payload["cookie_text"] = payload.get("cookie_text") or COOKIE_TEXT
            if not payload["cookie_text"]:
                raise ValueError("Выберите файл cookies.txt в настройках")
        if STATE["stage"] in ACTIVE:
            raise ValueError("Дождитесь окончания текущей загрузки")
        payload["id"] = secrets.token_hex(8)
        STATE.update(stage="connecting", message="", id=payload["id"], title=clean_title(payload.get("title")),
                     url=payload["url"], filename="", percent=0, downloaded=0, total=0, speed=0, fragments=0, fragment=0)
        CANCEL.clear()
    threading.Thread(target=download, args=(payload,), daemon=True).start()


def history_item(item_id: object) -> dict:
    with LOCK:
        for item in HISTORY:
            if item["id"] == item_id:
                return dict(item)
    raise ValueError("Запись не найдена")


def prepare_chrome_extension() -> None:
    global PAIR_KEY, EXTENSION_DIR
    secret_file = CONFIG_DIR / "chrome-pair-key"
    if secret_file.exists():
        key = secret_file.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise RuntimeError("Повреждён ключ подключения Chrome")
    else:
        key = secrets.token_hex(32)
        with secret_file.open("x", encoding="ascii") as stream:
            stream.write(key + "\n")
        os.chmod(secret_file, 0o600)
    extension_dir = CONFIG_DIR / "extension"
    extension_dir.mkdir(exist_ok=True)
    for name in ("manifest.json", "background.js"):
        shutil.copy2(APP_DIR / "extension" / name, extension_dir / name)
    config_file = extension_dir / "config.js"
    config_file.write_text(f'export const PAIR_KEY = "{key}";\n', encoding="ascii")
    os.chmod(config_file, 0o600)
    PAIR_KEY, EXTENSION_DIR = key, extension_dir


def open_path(path: Path, select: bool = False) -> None:
    if PLATFORM == "windows":
        if select:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(path)  # type: ignore[attr-defined]
    elif PLATFORM == "macos":
        subprocess.Popen(["open", "-R", str(path)] if select else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if select else path)])


def open_chrome_extensions() -> None:
    candidates = [shutil.which("chrome"), shutil.which("google-chrome")]
    if PLATFORM == "windows":
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            subprocess.Popen([candidate, "chrome://extensions/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open("chrome://extensions/")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def respond(self, code: int, value: dict) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.path.startswith("/api/extension/"):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, body: bytes | None = None) -> None:
        body = path.read_bytes() if body is None else body
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if path.suffix == ".html":
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return hmac.compare_digest(self.headers.get("X-App-Token", ""), TOKEN)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 8_000_000:
            raise ValueError("Слишком большой запрос")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("Некорректные данные")
        return payload

    def do_OPTIONS(self) -> None:
        if not self.path.startswith("/api/extension/"):
            self.respond(404, {"error": "Не найдено"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MeetKeep-Pair-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/extension/challenge":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            nonce = query.get("nonce", [""])[0]
            if not PAIR_KEY or not re.fullmatch(r"[a-f0-9]{32}", nonce):
                self.respond(400, {"error": "Некорректный запрос"})
                return
            self.respond(200, {"proof": hmac.new(bytes.fromhex(PAIR_KEY), nonce.encode("ascii"), hashlib.sha256).hexdigest()})
        elif path == "/":
            page = (UI_DIR / "index.html").read_bytes()
            for key, value in (("__APP_TOKEN__", TOKEN), ("__PLATFORM__", PLATFORM), ("__SHELL__", "app" if WINDOW else "browser")):
                page = page.replace(key.encode(), value.encode())
            self.send_file(UI_DIR / "index.html", page)
        elif path.startswith("/assets/"):
            target = (UI_DIR / path.lstrip("/")).resolve()
            if target.is_file() and target.is_relative_to(UI_DIR.resolve()):
                self.send_file(target)
            else:
                self.respond(404, {"error": "Не найдено"})
        elif not self.authorized():
            self.respond(404, {"error": "Не найдено"})
        elif path == "/api/status":
            self.respond(200, snapshot())
        elif path == "/api/history":
            with LOCK:
                self.respond(200, {"items": list(HISTORY)})
        elif path == "/api/settings":
            with LOCK:
                self.respond(200, {**SETTINGS, "cookies_loaded": bool(COOKIE_TEXT), "extension_folder": str(EXTENSION_DIR) if PAIR_KEY else ""})
        else:
            self.respond(404, {"error": "Не найдено"})

    def do_POST(self) -> None:
        global COOKIE_TEXT
        if self.path == "/api/extension/start":
            self.extension_start()
            return
        if not self.authorized():
            self.respond(403, {"error": "Нет доступа"})
            return
        try:
            data = self.read_json() if int(self.headers.get("Content-Length", "0")) else {}
            if self.path == "/api/start":
                start_download(str(data.get("url", "")))
            elif self.path == "/api/cancel":
                CANCEL.set()
            elif self.path == "/api/retry":
                start_download(history_item(data.get("id"))["url"])
            elif self.path == "/api/settings":
                values = checked_settings(data)
                with LOCK:
                    SETTINGS.update(values)
                    changed()
            elif self.path == "/api/cookies":
                text = data.get("cookie_text")
                if not isinstance(text, str) or not text.startswith(("# HTTP Cookie File", "# Netscape HTTP Cookie File")) or len(text) > 4_000_000:
                    raise ValueError("Нужен файл cookies.txt в формате Netscape")
                with LOCK:
                    COOKIE_TEXT = text
                    changed()
            elif self.path == "/api/pick-folder":
                self.respond(200, {"folder": pick_folder()})
                return
            elif self.path == "/api/open-folder":
                folder = Path(SETTINGS["folder"]).expanduser()
                folder.mkdir(parents=True, exist_ok=True)
                open_path(folder)
            elif self.path == "/api/reveal":
                item = history_item(data.get("id"))
                if not item["filename"] or not Path(item["filename"]).is_file():
                    raise ValueError("Файл не найден — возможно, его переместили или удалили")
                open_path(Path(item["filename"]), select=True)
            elif self.path == "/api/history/remove":
                with LOCK:
                    HISTORY[:] = [item for item in HISTORY if item["id"] != data.get("id")]
                    changed()
            elif self.path == "/api/history/clear":
                with LOCK:
                    HISTORY.clear()
                    changed()
            elif self.path == "/api/extension/open-setup":
                threading.Thread(target=open_chrome_extensions, daemon=True).start()
            elif self.path == "/api/shutdown":
                CANCEL.set()
                STOP.set()
            else:
                self.respond(404, {"error": "Не найдено"})
                return
            self.respond(200, {"ok": True})
        except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})

    def extension_start(self) -> None:
        if not PAIR_KEY or not hmac.compare_digest(self.headers.get("X-MeetKeep-Pair-Key", ""), PAIR_KEY):
            self.respond(403, {"error": "Нет доступа"})
            return
        try:
            data = self.read_json()
            cookie_text = data.get("cookie_text")
            if not isinstance(cookie_text, str) or len(cookie_text) > 4_000_000 or not cookie_text.startswith("# Netscape HTTP Cookie File\n"):
                raise ValueError("Некорректные cookies")
            title = data.get("title")
            has_cookies = cookie_text.strip() != "# Netscape HTTP Cookie File"
            start_download(str(data.get("url", "")), {
                "title": title if isinstance(title, str) else "",
                "browser": "cookies" if has_cookies else "none",
                "cookie_text": cookie_text if has_cookies else "",
            })
            with LOCK:
                if not SETTINGS["extension_seen"]:
                    SETTINGS["extension_seen"] = True
                    changed()
            self.respond(200, {"ok": True})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.respond(400, {"error": str(exc)})


def pick_folder() -> str:
    if WINDOW is None:
        raise ValueError("Выбор папки доступен только в окне приложения")
    import webview

    result = WINDOW.create_file_dialog(webview.FileDialog.FOLDER, directory=str(Path(SETTINGS["folder"]).expanduser()))
    if not result:
        return ""
    folder = str(result[0] if isinstance(result, (list, tuple)) else result)
    with LOCK:
        SETTINGS["folder"] = folder
        changed()
    return folder


def system_dark() -> bool:
    try:
        if PLATFORM == "windows":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        if PLATFORM == "macos":
            result = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2)
            return result.stdout.strip() == "Dark"
    except (OSError, subprocess.SubprocessError):
        pass
    return False


RU_MENU = {
    "global.quitConfirmation": "Закрыть MeetKeep?", "global.ok": "OK", "global.quit": "Выйти", "global.cancel": "Отмена",
    "global.saveFile": "Сохранить файл", "cocoa.menu.about": "О программе", "cocoa.menu.services": "Службы",
    "cocoa.menu.view": "Вид", "cocoa.menu.edit": "Правка", "cocoa.menu.hide": "Скрыть", "cocoa.menu.hideOthers": "Скрыть остальные",
    "cocoa.menu.showAll": "Показать все", "cocoa.menu.quit": "Завершить", "cocoa.menu.fullscreen": "Во весь экран",
    "cocoa.menu.cut": "Вырезать", "cocoa.menu.copy": "Копировать", "cocoa.menu.paste": "Вставить", "cocoa.menu.selectAll": "Выбрать все",
}


def setup_log() -> None:
    """Frozen windowed apps have no console: keep output and crashes in the data folder."""
    if not getattr(sys, "frozen", False):
        return
    log = CONFIG_DIR / "meetkeep.log"
    try:
        if log.exists() and log.stat().st_size > 1_000_000:
            log.unlink()
        stream = open(log, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = sys.stderr = stream
    faulthandler.enable(stream)
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {APP_NAME} on {PLATFORM}, args {sys.argv[1:]}")


def smoke_check() -> None:
    """--smoke-test: wait until the React UI is on screen in the real window, then close it."""
    global SMOKE_OK
    try:
        if WINDOW.events.loaded.wait(60):
            for _ in range(40):
                if WINDOW.evaluate_js("document.querySelector('.app') ? 'ok' : ''") == "ok":
                    SMOKE_OK = True
                    break
                time.sleep(0.5)
        print(f"smoke test: {'UI loaded' if SMOKE_OK else 'UI did not load'}")
    except Exception:
        traceback.print_exc()
    finally:
        WINDOW.destroy()


def run_window(address: str) -> bool:
    """Show the UI in a native window. Returns False when no suitable web view is available."""
    global WINDOW
    try:
        import webview

        if PLATFORM == "windows":
            from webview.platforms import winforms

            # Without WebView2 pywebview falls back to Internet Explorer, which cannot render the UI.
            if not winforms.is_chromium:
                return False
        dark = system_dark()
        background = {"windows": "#202020" if dark else "#fafafa", "macos": "#1e1e20" if dark else "#ffffff"}.get(PLATFORM, "#ffffff")
        WINDOW = webview.create_window(APP_NAME, address, width=1000, height=660, min_size=(760, 520), background_color=background)
        if SMOKE:
            threading.Thread(target=smoke_check, daemon=True).start()
        print("window: starting")
        webview.start(localization=RU_MENU)
        print("window: closed")
        return True
    except Exception:
        traceback.print_exc()
        WINDOW = None
        return False


def open_in_browser(address: str) -> None:
    if PLATFORM == "windows":
        for root in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            edge = Path(root or "") / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if root and edge.is_file():
                subprocess.Popen([str(edge), f"--app={address}", "--new-window"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
    webbrowser.open(address)


def self_test() -> int:
    """Used by CI on the built app: check that the bundled pieces load on this platform."""
    try:
        import webview  # noqa: F401

        if PLATFORM == "windows":
            from webview.platforms import winforms  # noqa: F401
        elif PLATFORM == "macos":
            from webview.platforms import cocoa  # noqa: F401
        assert (UI_DIR / "index.html").is_file(), "UI is missing"
        assert any((UI_DIR / "assets").glob("*.js")), "UI scripts are missing"
        if getattr(sys, "frozen", False):
            assert bundled_ffmpeg(), "FFmpeg is missing"
            if PLATFORM == "windows":
                assert (APP_DIR / "extension" / "manifest.json").is_file(), "Chrome extension is missing"
        return 0
    except Exception:
        report = Path(tempfile.gettempdir()) / "meetkeep-self-test.txt"
        report.write_text(traceback.format_exc(), encoding="utf-8")
        return 1


def main() -> None:
    setup_log()
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    if SMOKE:
        # A window that never shows up must fail CI instead of hanging it.
        threading.Timer(90, lambda: os._exit(3)).start()
    load_data()
    if PLATFORM == "windows":
        prepare_chrome_extension()
        server = None
        # The Chrome extension looks for MeetKeep on these ports.
        for port in range(8765, 8785):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError("Нет свободного локального порта для MeetKeep")
    else:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"server: {address}", flush=True)
    try:
        if os.environ.get("MEETKEEP_NO_WINDOW") == "1":
            STOP.wait()
        elif not run_window(address) and not SMOKE:
            print("window: unavailable, opening the browser")
            open_in_browser(address)
            STOP.wait()
    except KeyboardInterrupt:
        pass
    finally:
        CANCEL.set()
        server.shutdown()
        server.server_close()
    if SMOKE:
        os._exit(0 if SMOKE_OK else 1)


if __name__ == "__main__":
    main()
