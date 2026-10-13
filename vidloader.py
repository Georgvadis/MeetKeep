#!/usr/bin/env python3
"""Small local, cross-platform GUI for yt-dlp downloads."""

from __future__ import annotations

import html
import json
import hashlib
import hmac
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    import yt_dlp
    from websockets.sync.client import connect as websocket_connect
except ImportError:
    print("yt-dlp is missing. Install requirements.txt first.", file=sys.stderr)
    raise SystemExit(1)


APP_DIR = Path(__file__).resolve().parent
HTML = (APP_DIR / "index.html").read_bytes()
TOKEN = secrets.token_urlsafe(32)
PAIR_KEY = ""
EXTENSION_DIR = Path()
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# Extractors that name videos after the page or playlist file (HTML5MediaEmbed also appends " (1)").
GENERIC_EXTRACTORS = ("Generic", "HTML5MediaEmbed")
BROWSERS = ("none", "login", "chrome", "edge", "firefox", "safari", "brave", "cookies")
LOCK = threading.Lock()
SETTINGS = {"quality": "best", "folder": "~/Downloads", "insecure": False}
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


def config_dir() -> Path:
    if sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", str(Path.home()))) / "Vidloader"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "Vidloader"
    else:
        path = Path.home() / ".config" / "Vidloader"
    path.mkdir(parents=True, exist_ok=True)
    return path


LOGIN_PROFILE = config_dir() / "browser"


def find_login_browser() -> str | None:
    """Chrome or Edge used for the login window with Vidloader's own profile."""
    if sys.platform == "win32":
        candidates = []
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates += [Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                               Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"]
    elif sys.platform == "darwin":
        candidates = [Path(f"/Applications/{name}.app/Contents/MacOS/{name}") for name in ("Google Chrome", "Microsoft Edge", "Chromium")]
    else:
        candidates = [shutil.which(name) for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def login_endpoint() -> str | None:
    """DevTools address of the running login window, read from its profile."""
    try:
        port, path = (LOGIN_PROFILE / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()[:2]
        if not re.fullmatch(r"/devtools/browser/[\w-]+", path):
            return None
        with NO_PROXY.open(f"http://127.0.0.1:{int(port)}/json/version", timeout=0.5) as response:
            return f"ws://127.0.0.1:{int(port)}{path}" if response.status == 200 else None
    except (OSError, ValueError):
        return None


def open_login_window(url: str) -> None:
    browser = find_login_browser()
    if not browser:
        raise ValueError("Не найден Chrome или Edge для окна входа")
    LOGIN_PROFILE.mkdir(parents=True, exist_ok=True)
    # A separate profile is required: Chrome 136+ ignores remote debugging for the default one.
    subprocess.Popen(
        [browser, f"--user-data-dir={LOGIN_PROFILE}", "--remote-debugging-port=0",
         "--no-first-run", "--no-default-browser-check", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def login_cookies() -> str:
    """Export cookies of the login window as a Netscape cookies.txt text."""
    endpoint = login_endpoint()
    if not endpoint:
        raise ValueError("Окно входа закрыто. Нажмите «Открыть окно входа», войдите на сайт и повторите")
    with websocket_connect(endpoint, proxy=None, max_size=None, open_timeout=5) as socket:
        socket.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            reply = json.loads(socket.recv(timeout=10))
            if reply.get("id") == 1:
                break
    if "result" not in reply:
        raise ValueError("Не удалось получить cookies из окна входа")
    lines = ["# Netscape HTTP Cookie File"]
    for cookie in reply["result"].get("cookies", []):
        domain, path, name, value = (str(cookie.get(key, "")) for key in ("domain", "path", "name", "value"))
        if not domain or any(ch in field for field in (domain, path, name, value) for ch in "\t\r\n"):
            continue
        lines.append("\t".join([
            ("#HttpOnly_" if cookie.get("httpOnly") else "") + domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            path or "/",
            "TRUE" if cookie.get("secure") else "FALSE",
            str(max(0, int(cookie.get("expires") or 0))),
            name, value,
        ]))
    return "\n".join(lines) + "\n"


def same_page(first: str, second: str) -> bool:
    a, b = urllib.parse.urlsplit(first), urllib.parse.urlsplit(second)
    return (a.scheme, a.netloc.lower(), a.path.rstrip("/")) == (b.scheme, b.netloc.lower(), b.path.rstrip("/"))


def clean_title(value: object) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()[:300]


def login_tab_title(page: str) -> str:
    """Title of the page as shown in the login window tab (works for JS-rendered sites)."""
    endpoint = login_endpoint()
    if not endpoint:
        return ""
    try:
        with NO_PROXY.open(endpoint.replace("ws://", "http://", 1).split("/devtools/")[0] + "/json/list", timeout=1) as response:
            tabs = json.load(response)
    except (OSError, ValueError):
        return ""
    for tab in tabs:
        if tab.get("type") == "page" and same_page(str(tab.get("url", "")), page):
            return clean_title(tab.get("title"))
    return ""


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


def prepare_chrome_extension() -> Path:
    global PAIR_KEY, EXTENSION_DIR
    config = config_dir()
    secret_file = config / "chrome-pair-key"
    if secret_file.exists():
        key = secret_file.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise RuntimeError("Повреждён ключ подключения Chrome")
    else:
        key = secrets.token_hex(32)
        with secret_file.open("x", encoding="ascii") as stream:
            stream.write(key + "\n")
        os.chmod(secret_file, 0o600)
    extension_dir = config / "extension"
    extension_dir.mkdir(exist_ok=True)
    for name in ("manifest.json", "background.js"):
        shutil.copy2(APP_DIR / "extension" / name, extension_dir / name)
    config_file = extension_dir / "config.js"
    config_file.write_text(f'export const PAIR_KEY = "{key}";\n', encoding="ascii")
    os.chmod(config_file, 0o600)
    PAIR_KEY = key
    EXTENSION_DIR = extension_dir
    return extension_dir


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
        if browser not in BROWSERS:
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
            "outtmpl": str(folder / "%(title).180B.%(ext)s"),
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
        if browser in ("cookies", "login"):
            cookie_text = payload["cookie_text"] if browser == "cookies" else login_cookies()
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
            info = ydl.extract_info(url, download=False, process=False)
            if CANCEL.is_set():
                raise DownloadCancelled()
            # Site extractors already know the real title; the generic one only sees a page or playlist name.
            if info.get("extractor_key") in GENERIC_EXTRACTORS:
                title = clean_title(payload.get("title")) or login_tab_title(referer)
                # Only fetch when the address is an HTML page, not the media file itself.
                if not title and (referer != url or info["extractor_key"] == "HTML5MediaEmbed"):
                    title = html_title(ydl, referer)
                if title:
                    info["title"] = title
            if info.get("_type", "video") == "video":
                unique_template(ydl, info, folder)
            result = ydl.process_ie_result(info, download=True)
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
        message = str(exc)
        if sys.platform == "win32" and ("Could not copy Chrome cookie database" in message or "Failed to decrypt with DPAPI" in message):
            message = "Chrome не отдаёт cookies напрямую. Используйте расширение Vidloader в Chrome"
        update(stage="error", message=message)
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
        if self.path.startswith("/api/extension/"):
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return self.headers.get("X-App-Token") == TOKEN

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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Vidloader-Pair-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.startswith("/api/extension/challenge?"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            nonce = query.get("nonce", [""])[0]
            if not re.fullmatch(r"[a-f0-9]{32}", nonce):
                self.respond(400, {"error": "Некорректный запрос"})
                return
            proof = hmac.new(bytes.fromhex(PAIR_KEY), nonce.encode("ascii"), hashlib.sha256).hexdigest()
            self.respond(200, {"proof": proof})
        elif self.path == "/":
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
            self.respond(200, {**snapshot(), "login_open": login_endpoint() is not None})
        elif self.path == "/api/extension/setup" and self.authorized():
            self.respond(200, {"folder": str(EXTENSION_DIR)})
        else:
            self.respond(404, {"error": "Не найдено"})

    def do_POST(self) -> None:
        from_extension = self.path == "/api/extension/start"
        if from_extension:
            if not PAIR_KEY or not hmac.compare_digest(self.headers.get("X-Vidloader-Pair-Key", ""), PAIR_KEY):
                self.respond(403, {"error": "Нет доступа"})
                return
        elif not self.authorized():
            self.respond(403, {"error": "Нет доступа"})
            return
        if self.path == "/api/extension/open-setup":
            self.respond(200, {"ok": True})
            threading.Thread(target=open_chrome_setup, daemon=True).start()
            return
        if self.path == "/api/login/open":
            try:
                raw = str(self.read_json().get("url", "")).strip()
                open_login_window(normalize_url(raw)[1] if raw else "about:blank")
                self.respond(200, {"ok": True})
            except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
                self.respond(400, {"error": str(exc)})
            return
        if self.path == "/api/reveal":
            state = snapshot()
            if state["stage"] != "done" or not Path(state["filename"]).is_file():
                self.respond(404, {"error": "Файл не найден"})
                return
            reveal(Path(state["filename"]))
            self.respond(200, {"ok": True})
            return
        if self.path == "/api/settings":
            try:
                values = self.read_json()
                if values.get("quality") not in ("best", "1080", "720", "480", "360"):
                    raise ValueError("Неизвестное качество")
                if not isinstance(values.get("folder"), str) or not values["folder"].strip():
                    raise ValueError("Укажите папку сохранения")
                if not isinstance(values.get("insecure"), bool):
                    raise ValueError("Некорректная настройка сертификата")
                with LOCK:
                    SETTINGS.update(quality=values["quality"], folder=values["folder"], insecure=values["insecure"])
                self.respond(200, {"ok": True})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.respond(400, {"error": str(exc)})
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
        if self.path not in ("/api/start", "/api/extension/start"):
            self.respond(404, {"error": "Не найдено"})
            return
        try:
            payload = self.read_json()
            if from_extension:
                cookie_text = payload.get("cookie_text")
                if not isinstance(cookie_text, str) or len(cookie_text) > 4_000_000 or not cookie_text.startswith("# Netscape HTTP Cookie File\n"):
                    raise ValueError("Некорректные cookies")
                with LOCK:
                    settings = dict(SETTINGS)
                title = payload.get("title")
                payload = {"url": payload.get("url"), "title": title if isinstance(title, str) else "", "browser": "cookies" if cookie_text.strip() != "# Netscape HTTP Cookie File" else "none", "cookie_text": cookie_text, **settings}
            normalize_url(str(payload.get("url", "")))
            if not isinstance(payload.get("insecure"), bool):
                raise ValueError("Некорректная настройка сертификата")
            if payload.get("browser") not in BROWSERS:
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


def open_interface(address: str) -> None:
    if sys.platform == "win32":
        candidates = [shutil.which("msedge")]
        for root in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(str(Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                try:
                    subprocess.Popen([candidate, f"--app={address}", "--new-window"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                except OSError:
                    continue
    webbrowser.open(address)


def reveal(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def open_chrome_setup() -> None:
    candidates = [shutil.which("chrome"), shutil.which("google-chrome")]
    if sys.platform == "win32":
        for root in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if root:
                candidates.append(str(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            subprocess.Popen([candidate, "chrome://extensions/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
    webbrowser.open("chrome://extensions/")


def main() -> None:
    if sys.platform == "win32":
        prepare_chrome_extension()
        server = None
        for port in range(8765, 8785):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError("Нет свободного локального порта для Vidloader")
    else:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    address = f"http://127.0.0.1:{port}/"
    print(f"Откройте приложение: {address}")
    print("Чтобы закрыть приложение, нажмите Ctrl+C в этом окне.")
    if os.environ.get("VIDLOADER_NO_BROWSER") != "1":
        threading.Timer(0.4, open_interface, args=(address,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
