import os
import codecs
import re
import shutil
import time
import subprocess
import tempfile
import threading
import uuid
import json
import html
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import yt_dlp
try:
    import redis
except Exception:
    redis = None
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from yt_dlp.networking.impersonate import ImpersonateTarget

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except Exception:
        return default


def _clean_env_value(value: Optional[str]) -> str:
    if value is None:
        return ""
    cleaned = str(value).strip()
    if len(cleaned) >= 2 and cleaned[0] in ("`", "'", '"') and cleaned[-1] in ("`", "'", '"'):
        cleaned = cleaned[1:-1]
    cleaned = cleaned.strip().replace("`", "")
    return cleaned


WHISPER_API_URL = _clean_env_value(os.getenv("WHISPER_API_URL", "http://whisper-service:8000/transcribe"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/data/jobs"))
YTDLP_COOKIE_FILE = os.getenv("YTDLP_COOKIE_FILE", "/data/cookies.txt")
YTDLP_HTTP_CHUNK_SIZE = _env_int("YTDLP_HTTP_CHUNK_SIZE", 10 * 1024 * 1024)
YTDLP_IMPERSONATE = os.getenv("YTDLP_IMPERSONATE", "chrome").strip()
DOWNLOADER_USER_AGENT = os.getenv(
    "DOWNLOADER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)
INSTAGRAM_COOKIE_FILE = os.getenv("INSTAGRAM_COOKIE_FILE", "").strip()
INSTAGRAM_COOKIES_FROM_BROWSER = os.getenv("INSTAGRAM_COOKIES_FROM_BROWSER", "").strip()
INSTAGRAM_COOKIE_HEADER = os.getenv("INSTAGRAM_COOKIE_HEADER", "").strip()
PROXY_URL = _clean_env_value(os.getenv("PROXY_URL", ""))
PROXY_LOGIN = os.getenv("PROXY_LOGIN", "").strip()
PROXY_PASSWORD = os.getenv("PROXY_PASSWORD", "").strip()
PROXY_HOST = _clean_env_value(os.getenv("PROXY_HOST", ""))
PROXY_LIST_URL = _clean_env_value(os.getenv("PROXY_LIST_URL", ""))
PROXY_CACHE_TTL = _env_int("PROXY_CACHE_TTL", 300)
PROXY_FOR_SERVICES = [
    s.strip().lower()
    for s in os.getenv("PROXY_FOR_SERVICES", "tiktok,kwai").split(",")
    if s.strip()
]
VISOLIX_API_KEY = os.getenv("VISOLIX_API_KEY", "").strip()
VISOLIX_LICENSE_CODE = os.getenv("VISOLIX_LICENSE_CODE", "").strip()
VISOLIX_CLIENT_NAME = os.getenv("VISOLIX_CLIENT_NAME", "").strip()
VISOLIX_SITE_URL = _clean_env_value(os.getenv("VISOLIX_SITE_URL", ""))
VISOLIX_IP = os.getenv("VISOLIX_IP", "").strip()
_visolix_base_raw = _clean_env_value(os.getenv("VISOLIX_BASE_URL", ""))
VISOLIX_BASE_URL = (_visolix_base_raw or "https://developers.visolix.com").rstrip("/")
VISOLIX_LICENSE_URL = _clean_env_value(os.getenv("VISOLIX_LICENSE_URL", "https://visolix.com/fastapi/license/")).rstrip("/")
VISOLIX_LICENSE_ACTION = os.getenv("VISOLIX_LICENSE_ACTION", "visolix_activate").strip()
VISOLIX_PROGRESS_TIMEOUT = _env_int("VISOLIX_PROGRESS_TIMEOUT", 120)
VISOLIX_PROGRESS_INTERVAL = _env_float("VISOLIX_PROGRESS_INTERVAL", 2.0)
VISOLIX_SITE_URL_FALLBACK = os.getenv("PUBLIC_SITE_URL", "").strip()
_VISOLIX_LICENSE_STATE = {"checked": False, "ok": False, "message": ""}
VISOLIX_DEBUG = (os.getenv("VISOLIX_DEBUG", "").strip().lower() in ("1", "true", "yes"))
VISOLIX_REST_API_URL = _clean_env_value(os.getenv("VISOLIX_REST_API_URL", "")).rstrip("/")
VISOLIX_REST_API_KEY = os.getenv("VISOLIX_REST_API_KEY", "").strip()
VISOLIX_REST_YOUTUBE_FORMAT = os.getenv("VISOLIX_REST_YOUTUBE_FORMAT", "720").strip()
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_PREFIX = (os.getenv("REDIS_PREFIX", "downscribe").strip() or "downscribe")
DOWNLOAD_TTL_SECONDS = _env_int("DOWNLOAD_TTL_SECONDS", 3600)
CLEANUP_INTERVAL_SECONDS = _env_int("CLEANUP_INTERVAL_SECONDS", 300)

TIKTOK_API_HOSTNAMES = [
    h.strip()
    for h in os.getenv("TIKTOK_API_HOSTNAMES", "api16-normal-c-useast1a.tiktokv.com").split(",")
    if h.strip()
]
TIKTOK_APP_NAMES = [
    n.strip() for n in os.getenv("TIKTOK_APP_NAMES", "musical_ly,trill").split(",") if n.strip()
]
TIKTOK_IIDS = [i.strip() for i in os.getenv("TIKTOK_IIDS", "").split(",") if i.strip()]
SNAPTIK_BASE_URL = os.getenv("SNAPTIK_BASE_URL", "https://dev.snaptik.app").strip().rstrip("/")
TIKTOKDL_API_BASE_URL = os.getenv("TIKTOKDL_API_BASE_URL", "").strip().rstrip("/")
TIKWM_BASE_URL = os.getenv("TIKWM_BASE_URL", "https://www.tikwm.com").strip().rstrip("/")

DEFAULT_HTTP_HEADERS = {
    "User-Agent": DOWNLOADER_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

SHORTENER_HOSTS = {
    "t.co",
    "bit.ly",
    "goo.gl",
    "tinyurl.com",
    "youtu.be",
    "vm.tiktok.com",
    "vt.tiktok.com",
    "m.tiktok.com",
    "s.kwai.app",
    "kw.ai",
}

_redis_client = None


def _get_redis_client():
    global _redis_client
    if _redis_client is not None:
        return _redis_client if _redis_client is not False else None
    if not REDIS_URL or redis is None:
        _redis_client = False
        return None
    try:
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        _redis_client = client
        return client
    except Exception:
        _redis_client = False
        return None


def _redis_job_key(job_id: str) -> str:
    return f"{REDIS_PREFIX}:job:{job_id}"


def _redis_expires_key() -> str:
    return f"{REDIS_PREFIX}:jobs:expires"


def _register_job(job_id: str, job_dir: Path) -> None:
    ttl = max(60, DOWNLOAD_TTL_SECONDS)
    expires_at = int(time.time()) + ttl
    client = _get_redis_client()
    if not client:
        return
    try:
        client.hset(_redis_job_key(job_id), mapping={"dir": str(job_dir), "expires_at": str(expires_at)})
        client.expire(_redis_job_key(job_id), ttl)
        client.zadd(_redis_expires_key(), {job_id: expires_at})
    except Exception:
        return


def _cleanup_job_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _cleanup_redis_jobs(limit: int = 50) -> int:
    client = _get_redis_client()
    if not client:
        return 0
    now = int(time.time())
    try:
        job_ids = client.zrangebyscore(_redis_expires_key(), 0, now, start=0, num=limit)
    except Exception:
        return 0
    deleted = 0
    for job_id in job_ids:
        job_dir = None
        try:
            job_dir = client.hget(_redis_job_key(job_id), "dir")
        except Exception:
            job_dir = None
        if job_dir:
            _cleanup_job_dir(Path(job_dir))
        try:
            client.delete(_redis_job_key(job_id))
            client.zrem(_redis_expires_key(), job_id)
        except Exception:
            pass
        deleted += 1
    return deleted


def _cleanup_fs_jobs() -> int:
    if not OUTPUT_DIR.exists():
        return 0
    now = time.time()
    ttl = max(60, DOWNLOAD_TTL_SECONDS)
    deleted = 0
    for child in OUTPUT_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except Exception:
            continue
        if now - mtime >= ttl:
            _cleanup_job_dir(child)
            deleted += 1
    return deleted


def _cleanup_loop() -> None:
    interval = max(30, CLEANUP_INTERVAL_SECONDS)
    while True:
        try:
            deleted = _cleanup_redis_jobs()
            if deleted == 0:
                _cleanup_fs_jobs()
        except Exception:
            pass
        time.sleep(interval)

app = FastAPI(title="Downscribe Downloader Service")


@app.on_event("startup")
def _startup():
    _ensure_dir(OUTPUT_DIR)
    thread = threading.Thread(target=_cleanup_loop, daemon=True)
    thread.start()


class TranscriptRequest(BaseModel):
    url: HttpUrl
    model: str = "tiny"
    include_segments: bool = False


class DownloadRequest(BaseModel):
    url: HttpUrl
    cookies: Optional[str] = None


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_job_path(job_id: str, filename: str) -> Path:
    base = (OUTPUT_DIR / job_id).resolve()
    target = (base / filename).resolve()
    if base not in target.parents and base != target:
        raise HTTPException(status_code=400, detail="Caminho inválido.")
    return target


def _ffmpeg_to_wav(input_path: str, wav_path: str) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        wav_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Erro no ffmpeg: {e}") from e


def _ffmpeg_extract_audio_mp3(input_path: str, output_path: str) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        "192k",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"Erro no ffmpeg: {e}") from e


def _looks_like_mp4(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            header = f.read(16)
        return len(header) >= 8 and header[4:8] == b"ftyp"
    except Exception:
        return False


def _ffmpeg_remux_to_mp4(input_path: str, output_path: str) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-c",
        "copy",
        output_path,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ensure_mp4_video(job_dir: Path, video_path: Path) -> Path:
    target = job_dir / "video.mp4"
    if target.exists():
        return target

    if not video_path.exists():
        return video_path

    if video_path.suffix.lower() == ".unknown_video" or _looks_like_mp4(video_path):
        try:
            video_path.rename(target)
            return target
        except Exception:
            pass

    try:
        _ffmpeg_remux_to_mp4(str(video_path), str(target))
        return target
    except Exception:
        return video_path


def _headers_for_url(url: str) -> dict:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""

    headers = dict(DEFAULT_HTTP_HEADERS)
    if host.endswith("tiktok.com"):
        headers["Referer"] = "https://www.tiktok.com/"
        headers["Origin"] = "https://www.tiktok.com"
    if host.endswith("instagram.com") or host.endswith("instagr.am"):
        headers["Referer"] = "https://www.instagram.com/"
        headers["Origin"] = "https://www.instagram.com"
    if host.endswith("kwai.com") or host.endswith("s.kwai.app") or host.endswith("kw.ai"):
        headers["Referer"] = "https://www.kwai.com/"
        headers["Origin"] = "https://www.kwai.com"
    if host.endswith("kwai.net"):
        headers["Referer"] = "https://www.kwai.com/"
        headers["Origin"] = "https://www.kwai.com"
    return headers


def _impersonate_target_for_url(url: str) -> Optional[ImpersonateTarget]:
    if not YTDLP_IMPERSONATE:
        return None
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if "tiktok.com" not in host:
        return None
    try:
        return ImpersonateTarget.from_str(YTDLP_IMPERSONATE)
    except Exception:
        return None


def _candidate_urls(url: str) -> list[str]:
    urls: list[str] = []

    def add(u: str) -> None:
        u = (u or "").strip()
        if not u or u in urls:
            return
        urls.append(u)

    add(url)

    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""

    if host == "www.kwai.com":
        add(url.replace("://www.kwai.com", "://kwai.com"))
        add(url.replace("://www.kwai.com", "://m.kwai.com"))
    elif host == "kwai.com":
        add(url.replace("://kwai.com", "://www.kwai.com"))
        add(url.replace("://kwai.com", "://m.kwai.com"))
    elif host == "m.kwai.com":
        add(url.replace("://m.kwai.com", "://www.kwai.com"))
        add(url.replace("://m.kwai.com", "://kwai.com"))

    if host == "www.tiktok.com":
        add(url.replace("://www.tiktok.com", "://m.tiktok.com"))
    elif host == "m.tiktok.com":
        add(url.replace("://m.tiktok.com", "://www.tiktok.com"))

    m = re.search(r"/video/(\d+)", url)
    if m:
        add(f"https://www.tiktok.com/embed/v2/{m.group(1)}")

    return urls


def _resolve_url(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = ""

    if host not in SHORTENER_HOSTS:
        return url

    try:
        r = requests.get(url, headers=_headers_for_url(url), timeout=20, allow_redirects=True, stream=True)
        final_url = str(r.url) if r.url else url
        r.close()
        try:
            final_host = (urlparse(final_url).hostname or "").lower()
            final_path = urlparse(final_url).path or ""
        except Exception:
            final_host = ""
            final_path = ""

        if "tiktok.com" in final_host and final_path.startswith("/login"):
            return url

        return final_url
    except Exception:
        return url


def _clean_input_url(url: str) -> str:
    cleaned = str(url or "").strip()
    cleaned = cleaned.strip(" \t\r\n`'\"´｀“”‘’")
    if len(cleaned) >= 2 and cleaned[0] in ("`", "'", '"', "´", "｀", "“", "”", "‘", "’") and cleaned[-1] in ("`", "'", '"', "´", "｀", "“", "”", "‘", "’"):
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def _normalize_title(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def _extract_title_from_html(html_text: str) -> Optional[str]:
    if not html_text:
        return None
    patterns = [
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\']([^"\']+)["\']',
        r"<title[^>]*>(.*?)</title>",
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = html.unescape(match.group(1))
            normalized = _normalize_title(title)
            if normalized:
                return normalized
    return None


def _fetch_page_title(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=_headers_for_url(url), timeout=20, allow_redirects=True)
        return _extract_title_from_html(r.text or "")
    except Exception:
        return None


def _is_kwai_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host.endswith("kwai.com") or host.endswith("s.kwai.app") or host.endswith("kw.ai")


def _is_tiktok_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host.endswith("tiktok.com")


def _is_instagram_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host.endswith("instagram.com") or host.endswith("instagr.am")


def _is_youtube_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host.endswith("youtube.com") or host.endswith("youtu.be")


def _proxy_service_for_url(url: str) -> Optional[str]:
    if _is_instagram_url(url):
        return None
    if _is_youtube_url(url):
        return None
    if _is_tiktok_url(url):
        return "tiktok"
    if _is_kwai_url(url):
        return "kwai"
    return None


def _parse_cookies_from_browser(value: str) -> Optional[tuple]:
    parts = [part.strip() for part in (value or "").split(":") if part.strip()]
    if not parts:
        return None
    return tuple(parts)


def _is_valid_netscape_cookie_file(path: str) -> bool:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    except Exception:
        return False
    if not lines:
        return False
    for line in lines:
        if not line:
            continue
        if line.startswith("# Netscape HTTP Cookie File"):
            return True
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        return len(parts) >= 7
    return False


def _apply_cookie_settings(ydl_opts: dict, url: str) -> None:
    if _is_instagram_url(url) and INSTAGRAM_COOKIES_FROM_BROWSER:
        parsed = _parse_cookies_from_browser(INSTAGRAM_COOKIES_FROM_BROWSER)
        if parsed:
            ydl_opts["cookiesfrombrowser"] = parsed
            ydl_opts.pop("cookiefile", None)
            return
    cookie_file = None
    if _is_instagram_url(url) and INSTAGRAM_COOKIE_FILE and os.path.exists(INSTAGRAM_COOKIE_FILE):
        cookie_file = INSTAGRAM_COOKIE_FILE
    elif os.path.exists(YTDLP_COOKIE_FILE):
        cookie_file = YTDLP_COOKIE_FILE
    if cookie_file:
        if _is_instagram_url(url) and not _is_valid_netscape_cookie_file(cookie_file):
            raise HTTPException(
                status_code=400,
                detail="Arquivo de cookies inválido. Exporte em formato Netscape (cookies.txt) e tente novamente.",
            )
        ydl_opts["cookiefile"] = cookie_file


PROXY_CACHE_URL: Optional[str] = None
PROXY_CACHE_TS = 0.0


def _normalize_proxy_url(value: str, username: Optional[str] = None, password: Optional[str] = None) -> Optional[str]:
    raw = (value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    if not netloc:
        return None
    if username and password and "@" not in netloc:
        netloc = f"{username}:{password}@{netloc}"
    return f"{scheme}://{netloc}"


def _proxy_from_credentials() -> Optional[str]:
    if PROXY_HOST and PROXY_LOGIN and PROXY_PASSWORD:
        return _normalize_proxy_url(PROXY_HOST, PROXY_LOGIN, PROXY_PASSWORD)
    if PROXY_HOST:
        return _normalize_proxy_url(PROXY_HOST)
    return None


def _proxy_from_list() -> Optional[str]:
    if not PROXY_LIST_URL:
        return None
    try:
        r = requests.get(PROXY_LIST_URL, timeout=20)
        if r.status_code >= 400:
            return None
        lines = (r.text or "").splitlines()
        for line in lines:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            if "://" in value:
                proxy = _normalize_proxy_url(value)
                if proxy:
                    return proxy
            parts = [p.strip() for p in value.split(":") if p.strip()]
            if len(parts) >= 4:
                host = f"{parts[0]}:{parts[1]}"
                proxy = _normalize_proxy_url(host, parts[2], parts[3])
                if proxy:
                    return proxy
            if len(parts) == 2:
                host = f"{parts[0]}:{parts[1]}"
                proxy = _normalize_proxy_url(host, PROXY_LOGIN or None, PROXY_PASSWORD or None)
                if proxy:
                    return proxy
        return None
    except Exception:
        return None


def _get_cached_proxy() -> Optional[str]:
    global PROXY_CACHE_URL, PROXY_CACHE_TS
    now = time.time()
    if PROXY_CACHE_URL and (now - PROXY_CACHE_TS) < PROXY_CACHE_TTL:
        return PROXY_CACHE_URL
    proxy = PROXY_URL or _proxy_from_credentials() or _proxy_from_list()
    if proxy:
        PROXY_CACHE_URL = proxy
        PROXY_CACHE_TS = now
        return proxy
    return None


def _proxy_for_url(url: str) -> Optional[str]:
    service = _proxy_service_for_url(url)
    if not service or service not in PROXY_FOR_SERVICES:
        return None
    return _get_cached_proxy()


def _apply_network_settings(ydl_opts: dict, url: str) -> None:
    _apply_cookie_settings(ydl_opts, url)
    if _is_instagram_url(url) and INSTAGRAM_COOKIE_HEADER:
        headers = ydl_opts.get("http_headers") or {}
        headers["Cookie"] = INSTAGRAM_COOKIE_HEADER
        ydl_opts["http_headers"] = headers
    proxy = _proxy_for_url(url)
    if proxy:
        ydl_opts["proxy"] = proxy


def _is_proxy_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "proxy" in msg or "tunnel connection failed" in msg


def _extract_with_proxy_retry(attempt_opts: dict, url: str, download: bool, prepare_filename: bool = False) -> tuple[dict, Optional[str]]:
    proxy = attempt_opts.get("proxy")
    try:
        with yt_dlp.YoutubeDL(attempt_opts) as ydl:
            info = ydl.extract_info(url, download=download)
            filename = ydl.prepare_filename(info) if prepare_filename else None
        return info, filename
    except Exception as e:
        if proxy and _is_proxy_error(e):
            retry_opts = dict(attempt_opts)
            retry_opts.pop("proxy", None)
            with yt_dlp.YoutubeDL(retry_opts) as ydl:
                info = ydl.extract_info(url, download=download)
                filename = ydl.prepare_filename(info) if prepare_filename else None
            return info, filename
        raise


def _visolix_has_auth() -> bool:
    if VISOLIX_API_KEY:
        return True
    return bool(VISOLIX_LICENSE_CODE and VISOLIX_CLIENT_NAME)


def _visolix_is_success(data: dict) -> bool:
    success = data.get("success", None)
    if success in (1, True):
        return True
    status = data.get("status", None)
    return status in (1, True)

def _mask(value: Optional[str]) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    if len(s) <= 6:
        return "*" * len(s)
    return f"{s[:3]}***{s[-3:]}"

def _log_visolix(event: str, payload: dict) -> None:
    if not VISOLIX_DEBUG:
        return
    try:
        print(json.dumps({"visolix_event": event, **payload}, ensure_ascii=False))
    except Exception:
        pass

def _visolix_headers(platform: str, url: str, fmt: Optional[str] = None) -> dict:
    if VISOLIX_API_KEY:
        headers = {
            "X-API-KEY": VISOLIX_API_KEY,
            "X-PLATFORM": platform,
            "URL": url,
        }
        if fmt:
            headers["X-FORMAT"] = fmt
        _log_visolix("headers_api_key", {
            "platform": platform,
            "url": url,
            "fmt": fmt,
        })
        return headers
    if not (VISOLIX_LICENSE_CODE and VISOLIX_CLIENT_NAME):
        raise RuntimeError("VISOLIX_LICENSE_CODE ou VISOLIX_CLIENT_NAME não configurada.")
    headers = {
        "x-license-code": VISOLIX_LICENSE_CODE,
        "x-client-name": VISOLIX_CLIENT_NAME,
        "x-site-url": VISOLIX_SITE_URL or VISOLIX_SITE_URL_FALLBACK,
        "x-ip": VISOLIX_IP or "127.0.0.1",
        "X-PLATFORM": platform,
        "url": url,
    }
    if fmt:
        headers["X-FORMAT"] = fmt
    _log_visolix("headers_license", {
        "platform": platform,
        "url": url,
        "fmt": fmt,
        "x_license_code": _mask(VISOLIX_LICENSE_CODE),
        "x_client_name": VISOLIX_CLIENT_NAME,
        "x_site_url": headers["x-site-url"],
        "x_ip": headers["x-ip"],
    })
    return headers


def _visolix_activate_license() -> None:
    if VISOLIX_API_KEY:
        return
    if not (VISOLIX_LICENSE_CODE and VISOLIX_CLIENT_NAME):
        raise RuntimeError("VISOLIX_LICENSE_CODE ou VISOLIX_CLIENT_NAME não configurada.")
    if _VISOLIX_LICENSE_STATE["checked"]:
        if not _VISOLIX_LICENSE_STATE["ok"]:
            raise RuntimeError(_VISOLIX_LICENSE_STATE["message"] or "Falha ao ativar licença Visolix.")
        return
    _VISOLIX_LICENSE_STATE["checked"] = True
    site_url = VISOLIX_SITE_URL or VISOLIX_SITE_URL_FALLBACK
    if not site_url:
        _VISOLIX_LICENSE_STATE["message"] = "VISOLIX_SITE_URL não configurada."
        raise RuntimeError(_VISOLIX_LICENSE_STATE["message"])
    headers = {
        "Content-Type": "application/json",
        "licensecode": VISOLIX_LICENSE_CODE,
        "clientname": VISOLIX_CLIENT_NAME,
        "siteurl": site_url,
        "ip": VISOLIX_IP or "127.0.0.1",
        "action": VISOLIX_LICENSE_ACTION or "visolix_activate",
    }
    try:
        _log_visolix("activate_request", {
            "licensecode": _mask(VISOLIX_LICENSE_CODE),
            "clientname": VISOLIX_CLIENT_NAME,
            "siteurl": site_url,
            "ip": VISOLIX_IP or "127.0.0.1",
            "action": VISOLIX_LICENSE_ACTION or "visolix_activate",
            "url": VISOLIX_LICENSE_URL,
        })
        r = requests.get(VISOLIX_LICENSE_URL, headers=headers, timeout=60)
        _log_visolix("activate_response", {
            "status_code": r.status_code,
            "text": (r.text or "")[:500],
        })
        if r.status_code >= 400:
            detail = (r.text or "").strip()
            if detail:
                raise RuntimeError(f"Falha ao ativar licença Visolix ({r.status_code}): {detail}")
            raise RuntimeError(f"Falha ao ativar licença Visolix ({r.status_code}).")
        data = r.json()
    except Exception as e:
        _VISOLIX_LICENSE_STATE["message"] = f"Falha ao ativar licença Visolix: {e}"
        raise RuntimeError(_VISOLIX_LICENSE_STATE["message"]) from e
    if isinstance(data, dict) and data.get("status") in (1, True):
        _VISOLIX_LICENSE_STATE["ok"] = True
        _log_visolix("activate_success", {"message": data.get("message")})
        return
    message = ""
    if isinstance(data, dict):
        message = data.get("message") or ""
    lowered = message.lower()
    if "already active" in lowered or "maximum allowed" in lowered:
        _VISOLIX_LICENSE_STATE["ok"] = True
        _log_visolix("activate_already_active", {"message": message})
        return
    _VISOLIX_LICENSE_STATE["message"] = message or "Falha ao ativar licença Visolix."
    raise RuntimeError(_VISOLIX_LICENSE_STATE["message"])


def _visolix_request_download(platform: str, url: str, fmt: Optional[str] = None) -> dict:
    if not _visolix_has_auth():
        raise RuntimeError("VISOLIX_API_KEY ou VISOLIX_LICENSE_CODE não configurada.")
    if not VISOLIX_API_KEY:
        _visolix_activate_license()
    api_url = f"{VISOLIX_BASE_URL}/api/download"
    try:
        headers = _visolix_headers(platform, url, fmt)
        _log_visolix("download_request", {
            "api_url": api_url,
            "platform": platform,
            "url": url,
            "fmt": fmt,
            "headers": {
                "x-license-code": _mask(headers.get("x-license-code")),
                "x-client-name": headers.get("x-client-name"),
                "x-site-url": headers.get("x-site-url"),
                "x-ip": headers.get("x-ip"),
                "X-PLATFORM": headers.get("X-PLATFORM"),
                "URL": headers.get("URL"),
                "url": headers.get("url"),
                "X-FORMAT": headers.get("X-FORMAT"),
            },
        })
        r = requests.get(api_url, headers=headers, timeout=30)
        _log_visolix("download_response", {
            "status_code": r.status_code,
            "text": (r.text or "")[:500],
        })
        if r.status_code >= 400:
            detail = (r.text or "").strip()
            if detail:
                raise RuntimeError(f"Visolix download falhou ({r.status_code}): {detail}")
            raise RuntimeError(f"Visolix download falhou ({r.status_code}).")
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"Visolix download falhou: {e}") from e
    if not isinstance(data, dict) or not _visolix_is_success(data):
        raise RuntimeError("Visolix download não retornou sucesso.")
    return data


def _visolix_wait_progress(download_id: str) -> dict:
    if not _visolix_has_auth():
        raise RuntimeError("VISOLIX_API_KEY ou VISOLIX_LICENSE_CODE não configurada.")
    api_url = f"{VISOLIX_BASE_URL}/api/progress"
    deadline = time.time() + max(5, VISOLIX_PROGRESS_TIMEOUT)
    last_data: Optional[dict] = None
    while time.time() < deadline:
        try:
            r = requests.get(api_url, params={"id": download_id}, timeout=30)
            if r.status_code >= 400:
                raise RuntimeError(f"Visolix progress falhou ({r.status_code}).")
            data = r.json()
        except Exception as e:
            raise RuntimeError(f"Visolix progress falhou: {e}") from e
        if isinstance(data, dict):
            last_data = data
            if _visolix_is_success(data):
                return data
        time.sleep(max(0.5, VISOLIX_PROGRESS_INTERVAL))
    if isinstance(last_data, dict):
        text = last_data.get("text") or "Visolix não finalizou o download a tempo."
        raise RuntimeError(text)
    raise RuntimeError("Visolix não finalizou o download a tempo.")


def _visolix_rest_enabled() -> bool:
    return bool(VISOLIX_REST_API_URL and VISOLIX_REST_API_KEY)


def _visolix_rest_post(path: str, payload: dict) -> dict:
    api_url = f"{VISOLIX_REST_API_URL}/{path.lstrip('/')}"
    body = dict(payload)
    body["key"] = VISOLIX_REST_API_KEY
    try:
        try:
            print(json.dumps({
                "visolix_rest_event": "request",
                "path": path,
                "url": api_url,
            }, ensure_ascii=False))
        except Exception:
            pass
        r = requests.post(api_url, json=body, timeout=30)
    except Exception as e:
        raise RuntimeError(f"Visolix REST falhou: {e}") from e
    if r.status_code >= 400:
        detail = (r.text or "").strip()
        if detail:
            raise RuntimeError(f"Visolix REST falhou ({r.status_code}): {detail}")
        raise RuntimeError(f"Visolix REST falhou ({r.status_code}).")
    try:
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"Visolix REST retornou JSON inválido: {e}") from e
    try:
        print(json.dumps({
            "visolix_rest_event": "response",
            "path": path,
            "status_code": r.status_code,
            "ok": data.get("status") if isinstance(data, dict) else None,
        }, ensure_ascii=False))
    except Exception:
        pass
    if not isinstance(data, dict):
        raise RuntimeError("Visolix REST retornou resposta inválida.")
    return data


def _visolix_rest_video_data(url: str, fmt: Optional[str] = None) -> dict:
    payload: dict = {"url": url}
    if fmt:
        payload["format"] = fmt
    data = _visolix_rest_post("video-data", payload)
    if data.get("status") in (1, True):
        return data
    message = data.get("message") or "Visolix REST não retornou sucesso."
    raise RuntimeError(message)


def _visolix_rest_wait_progress(progress_id: str) -> dict:
    deadline = time.time() + max(5, VISOLIX_PROGRESS_TIMEOUT)
    last_data: Optional[dict] = None
    while time.time() < deadline:
        data = _visolix_rest_post("progress-check", {"id": progress_id})
        if isinstance(data, dict):
            last_data = data
            if data.get("success") in (1, True):
                return data
        time.sleep(max(0.5, VISOLIX_PROGRESS_INTERVAL))
    if isinstance(last_data, dict):
        text = last_data.get("text") or last_data.get("message") or "Visolix não finalizou o download a tempo."
        raise RuntimeError(text)
    raise RuntimeError("Visolix não finalizou o download a tempo.")


def _visolix_rest_pick_link(payload: dict, fmt: Optional[str] = None) -> Optional[dict]:
    links: list = []
    if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("links"), list):
        links = payload["data"]["links"]
    elif isinstance(payload.get("links"), list):
        links = payload["links"]
    candidates: list[dict] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        url = link.get("url") or link.get("download_url")
        if not url or url == "#":
            continue
        candidates.append(link)
    if fmt:
        for link in candidates:
            quality = str(link.get("quality") or "")
            if fmt in quality:
                return link
    for link in candidates:
        if (link.get("type") or "").lower() == "video":
            return link
    return candidates[0] if candidates else None


def _visolix_rest_progress_id(payload: dict) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ("id", "progress_id", "progressId"):
        if payload.get(key):
            return str(payload.get(key))
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ("id", "progress_id", "progressId"):
        if data.get(key):
            return str(data.get(key))
    return None


def _visolix_download_instagram(url: str, output_path: Path) -> dict:
    if not _visolix_rest_enabled():
        raise RuntimeError("Visolix REST não configurado.")
    payload = _visolix_rest_video_data(url)
    link = _visolix_rest_pick_link(payload)
    download_url = None
    if link:
        download_url = link.get("url") or link.get("download_url")
    if not download_url:
        download_url = payload.get("download_url")
    if not download_url:
        progress_id = _visolix_rest_progress_id(payload)
        if progress_id:
            progress = _visolix_rest_wait_progress(progress_id)
            download_url = progress.get("download_url")
    if not download_url:
        raise RuntimeError("Visolix REST não retornou URL de download.")
    _download_direct_video(str(download_url), output_path)
    info = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return info if isinstance(info, dict) else {}


def _visolix_download_youtube(url: str, output_path: Path, fmt: Optional[str] = None) -> dict:
    if not _visolix_rest_enabled():
        raise RuntimeError("Visolix REST não configurado.")
    payload = _visolix_rest_video_data(url, fmt)
    link = _visolix_rest_pick_link(payload, fmt)
    download_url = None
    if link:
        download_url = link.get("url") or link.get("download_url")
    if not download_url:
        download_url = payload.get("download_url")
    if not download_url:
        progress_id = _visolix_rest_progress_id(payload)
        if progress_id:
            progress = _visolix_rest_wait_progress(progress_id)
            download_url = progress.get("download_url")
    if not download_url:
        raise RuntimeError("Visolix REST não retornou URL de download.")
    _download_direct_video(str(download_url), output_path)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    info: dict = {}
    if isinstance(data, dict):
        info["title"] = data.get("title") or data.get("name")
        info["thumbnail"] = data.get("thumb") or data.get("thumbnail") or data.get("image")
    return info


def _snaptik_decode_js_string(value: str) -> str:
    try:
        return codecs.decode(value, "unicode_escape")
    except Exception:
        return value


def _snaptik_extract_eval_script(script: str) -> Optional[str]:
    m = re.search(r"eval\((['\"])(?P<content>(?:\\.|(?!\1).)*)\1\)", script, re.S)
    if not m:
        return None
    return _snaptik_decode_js_string(m.group("content"))


def _snaptik_extract_html(script: str) -> Optional[str]:
    script2 = _snaptik_extract_eval_script(script)
    if not script2:
        return None
    patterns = [
        r"innerHTML\s*=\s*(['\"])(?P<content>(?:\\.|(?!\1).)*)\1",
        r"\bhtml\s*=\s*(['\"])(?P<content>(?:\\.|(?!\1).)*)\1",
    ]
    for pattern in patterns:
        m = re.search(pattern, script2, re.S)
        if m:
            return _snaptik_decode_js_string(m.group("content"))
    return None


def _snaptik_get_token(session: requests.Session) -> Optional[str]:
    r = session.get(f"{SNAPTIK_BASE_URL}/", headers=_headers_for_url(SNAPTIK_BASE_URL), timeout=20)
    if r.status_code >= 400:
        return None
    m = re.search(r'name="token"\s+value="([^"]+)"', r.text or "")
    if not m:
        return None
    return m.group(1)


def _snaptik_get_script(session: requests.Session, url: str, token: str) -> Optional[str]:
    data = {"token": token, "url": url}
    r = session.post(f"{SNAPTIK_BASE_URL}/abc2.php", data=data, headers=_headers_for_url(SNAPTIK_BASE_URL), timeout=20)
    if r.status_code >= 400:
        return None
    return r.text or ""


def _snaptik_get_hd_url(session: requests.Session, token: str) -> Optional[str]:
    r = session.get(f"{SNAPTIK_BASE_URL}/getHdLink.php?token={token}", headers=_headers_for_url(SNAPTIK_BASE_URL), timeout=20)
    if r.status_code >= 400:
        return None
    try:
        payload = r.json()
    except Exception:
        return None
    if payload.get("error"):
        return None
    return payload.get("url")


def _snaptik_extract_video_url(html: str, session: requests.Session) -> Optional[str]:
    m = re.search(r'data-tokenhd="([^"]+)"', html)
    if m:
        hd_url = _snaptik_get_hd_url(session, m.group(1))
        if hd_url and not _is_watermarked_url(hd_url):
            return hd_url
    urls = re.findall(r"https?://[^\s\"'<>]+?\.mp4[^\s\"'<>]*", html)
    if urls:
        for value in urls:
            if not _is_watermarked_url(value):
                return value
    return None


def _snaptik_no_watermark_url(url: str) -> Optional[str]:
    try:
        session = requests.Session()
        token = _snaptik_get_token(session)
        if not token:
            return None
        script = _snaptik_get_script(session, url, token)
        if not script:
            return None
        html = _snaptik_extract_html(script)
        if not html:
            return None
        return _snaptik_extract_video_url(html, session)
    except Exception:
        return None


def _download_direct_video(url: str, output_path: Path) -> None:
    print(f"downloader_source_url={url}")
    with requests.get(url, headers=_headers_for_url(url), stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def _tiktokdl_no_watermark_url(url: str) -> Optional[str]:
    if not TIKTOKDL_API_BASE_URL:
        return None
    try:
        api_url = f"{TIKTOKDL_API_BASE_URL}/tiktok/api.php"
        r = requests.get(api_url, params={"url": url}, headers=_headers_for_url(api_url), timeout=20)
        if r.status_code >= 400:
            return None
        data = r.json()
    except Exception:
        return None
    if isinstance(data, dict):
        videos = data.get("video")
        if isinstance(videos, list) and videos:
            for value in videos:
                if isinstance(value, str) and value and not _is_watermarked_url(value):
                    return value
        for key in ("data", "result"):
            block = data.get(key)
            if isinstance(block, dict):
                videos = block.get("video")
                if isinstance(videos, list) and videos:
                    for value in videos:
                        if isinstance(value, str) and value and not _is_watermarked_url(value):
                            return value
    return None


def _tikwm_no_watermark_url(url: str) -> Optional[str]:
    try:
        api_url = f"{TIKWM_BASE_URL}/api/"
        r = requests.get(api_url, params={"url": url}, headers=_headers_for_url(api_url), timeout=20)
        if r.status_code >= 400:
            return None
        data = r.json()
    except Exception:
        return None
    if isinstance(data, dict):
        payload = data.get("data", data)
        if isinstance(payload, dict):
            media_id = payload.get("id")
            if isinstance(media_id, str) and media_id:
                candidate = f"{TIKWM_BASE_URL}/video/media/play/{media_id}.mp4"
                if not _is_watermarked_url(candidate):
                    return candidate
            for key in ("hdplay", "play"):
                value = payload.get(key)
                if isinstance(value, str) and value and not _is_watermarked_url(value):
                    return value
    return None


def _is_watermarked_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "playwm" in lowered or "watermark" in lowered


def _extract_kwai_direct_media_url_from_html(html: str) -> Optional[str]:
    m = re.search(r'"contentUrl"\s*:\s*"([^"]+)"', html)
    if m:
        raw = m.group(1)
        try:
            value = json.loads(f'"{raw}"')
        except Exception:
            value = raw.replace("\\/", "/")
        if isinstance(value, str) and ".mp4" in value:
            return value

    urls = re.findall(r"https?://[^\s\"'<>]+?\.mp4[^\s\"'<>]*", html)
    if urls:
        return urls[0]
    return None


def _kwai_direct_media_url(url: str) -> Optional[str]:
    try:
        r = requests.get(
            url,
            headers=_headers_for_url(url),
            timeout=20,
            allow_redirects=True,
        )
        html = r.text or ""
    except Exception:
        return None

    direct = _extract_kwai_direct_media_url_from_html(html)
    if direct:
        return direct
    return None


def _download_best_audio(tmp_dir: str, url: str):
    resolved_url = _resolve_url(_clean_input_url(url))
    if _is_instagram_url(resolved_url) or _is_youtube_url(resolved_url):
        if not _visolix_rest_enabled():
            raise HTTPException(status_code=400, detail="Visolix REST não configurado para Instagram/YouTube.")
        try:
            video_path = Path(tmp_dir) / "video.mp4"
            if _is_instagram_url(resolved_url):
                info = _visolix_download_instagram(resolved_url, video_path)
            else:
                info = _visolix_download_youtube(resolved_url, video_path, VISOLIX_REST_YOUTUBE_FORMAT)
            audio_path = Path(tmp_dir) / "audio.mp3"
            _ffmpeg_extract_audio_mp3(str(video_path), str(audio_path))
            return info if isinstance(info, dict) else {}, str(audio_path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro Visolix: {e}") from e
    extractor_args: dict = {
        "youtube": {
            "player_client": ["android", "web"],
        },
        "tiktok": {
            "api_hostname": TIKTOK_API_HOSTNAMES,
            "app_name": TIKTOK_APP_NAMES,
        },
    }
    if TIKTOK_IIDS:
        extractor_args["tiktok"]["iid"] = TIKTOK_IIDS

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(tmp_dir, "audio.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "overwrites": True,
        "http_headers": _headers_for_url(resolved_url),
        "http_chunk_size": YTDLP_HTTP_CHUNK_SIZE,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_args": extractor_args,
    }

    try:
        last_err: Optional[Exception] = None
        info = None
        downloaded = None
        kwai_direct_cache: dict[str, str] = {}
        page_title_cache: Optional[str] = None
        for candidate in _candidate_urls(resolved_url):
            try:
                candidate_media = candidate
                if _is_kwai_url(candidate) and not candidate.lower().endswith(".mp4"):
                    if candidate not in kwai_direct_cache:
                        kwai_direct_cache[candidate] = _kwai_direct_media_url(candidate) or candidate
                    candidate_media = kwai_direct_cache[candidate]
                attempt_opts = dict(ydl_opts)
                attempt_opts["http_headers"] = _headers_for_url(candidate_media)
                _apply_network_settings(attempt_opts, candidate_media)
                impersonate_target = _impersonate_target_for_url(candidate_media)
                if impersonate_target is not None:
                    attempt_opts["impersonate"] = impersonate_target
                info, downloaded = _extract_with_proxy_retry(attempt_opts, candidate_media, True, True)
                if isinstance(info, dict) and not info.get("title"):
                    if page_title_cache is None:
                        page_title_cache = _fetch_page_title(resolved_url) or _fetch_page_title(candidate)
                    if page_title_cache:
                        info["title"] = page_title_cache
                break
            except Exception as e:
                last_err = e
        if info is None or downloaded is None:
            raise last_err or RuntimeError("Falha ao baixar áudio.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao baixar áudio: {e}") from e

    if not os.path.exists(downloaded):
        candidates = list(Path(tmp_dir).glob("audio.*"))
        if candidates:
            downloaded = str(candidates[0])

    if not os.path.exists(downloaded):
        raise HTTPException(status_code=500, detail="Áudio não foi gerado pelo yt-dlp.")

    return info, downloaded


def _build_file_url(request: Request, job_id: str, filename: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/files/{job_id}/{filename}"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/files/{job_id}/{filename}")
def get_file(job_id: str, filename: str):
    path = _safe_job_path(job_id, filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    return FileResponse(str(path), filename=path.name)


@app.post("/download")
def download(req: DownloadRequest, request: Request):
    raw_url = _clean_input_url(str(req.url))
    resolved_url = _resolve_url(raw_url)
    try:
        print(json.dumps({
            "event": "download_start",
            "url": raw_url,
            "resolved_url": resolved_url,
            "cookies_provided": bool(req.cookies),
            "visolix_rest_enabled": _visolix_rest_enabled(),
            "visolix_has_auth": _visolix_has_auth(),
        }, ensure_ascii=False))
    except Exception:
        pass
    candidate_urls = _candidate_urls(resolved_url)
    _ensure_dir(OUTPUT_DIR)
    job_id = uuid.uuid4().hex
    job_dir = OUTPUT_DIR / job_id
    _ensure_dir(job_dir)

    video_outtmpl = str(job_dir / "video.%(ext)s")
    audio_outtmpl = str(job_dir / "audio.%(ext)s")

    info: Optional[dict] = None

    try:
        kwai_direct_cache: dict[str, str] = {}
        extractor_args: dict = {
            "youtube": {
                "player_client": ["android", "web"],
            },
            "tiktok": {
                "api_hostname": TIKTOK_API_HOSTNAMES,
                "app_name": TIKTOK_APP_NAMES,
            },
        }
        if TIKTOK_IIDS:
            extractor_args["tiktok"]["iid"] = TIKTOK_IIDS

        used_url: Optional[str] = None
        direct_video_downloaded = False

        if _is_instagram_url(resolved_url) or _is_youtube_url(resolved_url):
            if not _visolix_rest_enabled():
                raise RuntimeError("Visolix REST não configurado para Instagram/YouTube.")
            if _is_instagram_url(resolved_url):
                info = _visolix_download_instagram(resolved_url, job_dir / "video.mp4")
            else:
                info = _visolix_download_youtube(resolved_url, job_dir / "video.mp4", VISOLIX_REST_YOUTUBE_FORMAT)
            used_url = resolved_url
            direct_video_downloaded = True
        else:
            ydl_opts_video = {
                "format": "bestvideo*+bestaudio/best",
                "merge_output_format": "mp4",
                "outtmpl": video_outtmpl,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "overwrites": True,
                "http_headers": _headers_for_url(resolved_url),
                "http_chunk_size": YTDLP_HTTP_CHUNK_SIZE,
                "retries": 3,
                "fragment_retries": 3,
                "extractor_args": extractor_args,
            }
            last_err: Optional[Exception] = None
            for candidate in candidate_urls:
                try:
                    candidate_media = candidate
                    if _is_kwai_url(candidate) and not candidate.lower().endswith(".mp4"):
                        if candidate not in kwai_direct_cache:
                            kwai_direct_cache[candidate] = _kwai_direct_media_url(candidate) or candidate
                        candidate_media = kwai_direct_cache[candidate]
                    attempt_opts = dict(ydl_opts_video)
                    if _is_tiktok_url(candidate_media):
                        tikwm_url = _tikwm_no_watermark_url(candidate_media)
                        if tikwm_url:
                            print("tiktok_source=tikwm")
                            _download_direct_video(tikwm_url, job_dir / "video.mp4")
                            if not _looks_like_mp4(job_dir / "video.mp4"):
                                raise RuntimeError("TikWM retornou um arquivo inválido.")
                            info = {}
                            used_url = candidate_media
                            direct_video_downloaded = True
                            break
                        raise RuntimeError("Não foi possível obter vídeo sem marca d'água do TikTok.")
                    headers = _headers_for_url(candidate_media)
                    attempt_opts["http_headers"] = headers
                    _apply_network_settings(attempt_opts, candidate_media)
                    impersonate_target = _impersonate_target_for_url(candidate_media)
                    if impersonate_target is not None:
                        attempt_opts["impersonate"] = impersonate_target
                    info, _ = _extract_with_proxy_retry(attempt_opts, candidate_media, True)
                    used_url = candidate_media
                    break
                except Exception as e:
                    last_err = e
            if info is None:
                raise last_err or RuntimeError("Falha no yt-dlp.")

        if direct_video_downloaded:
            _ffmpeg_extract_audio_mp3(str(job_dir / "video.mp4"), str(job_dir / "audio.mp3"))
        else:
            ydl_opts_audio = {
                "format": "bestaudio/best",
                "outtmpl": audio_outtmpl,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "overwrites": True,
                "http_headers": _headers_for_url(used_url or resolved_url),
                "http_chunk_size": YTDLP_HTTP_CHUNK_SIZE,
                "retries": 3,
                "fragment_retries": 3,
                "extractor_args": extractor_args,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            last_err = None
            ok = False
            for candidate in ([used_url] if used_url else candidate_urls):
                if not candidate:
                    continue
                try:
                    candidate_media = candidate
                    if _is_kwai_url(candidate) and not candidate.lower().endswith(".mp4"):
                        if candidate not in kwai_direct_cache:
                            kwai_direct_cache[candidate] = _kwai_direct_media_url(candidate) or candidate
                        candidate_media = kwai_direct_cache[candidate]
                    attempt_opts = dict(ydl_opts_audio)
                    headers = _headers_for_url(candidate_media)
                    if _is_instagram_url(candidate_media) and req.cookies:
                        headers["Cookie"] = req.cookies
                    attempt_opts["http_headers"] = headers
                    _apply_network_settings(attempt_opts, candidate_media)
                    impersonate_target = _impersonate_target_for_url(candidate_media)
                    if impersonate_target is not None:
                        attempt_opts["impersonate"] = impersonate_target
                    _extract_with_proxy_retry(attempt_opts, candidate_media, True)
                    ok = True
                    break
                except Exception as e:
                    last_err = e
            if not ok:
                raise last_err or RuntimeError("Falha no yt-dlp.")
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        try:
            print(json.dumps({"event": "download_error", "message": str(e)}, ensure_ascii=False))
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Erro no download: {e}") from e

    video_path = job_dir / "video.mp4"
    audio_path = job_dir / "audio.mp3"

    if not video_path.exists():
        files = list(job_dir.glob("video.*"))
        if files:
            video_path = files[0]

    if not audio_path.exists():
        files = list(job_dir.glob("audio.*"))
        if files:
            audio_path = files[0]

    video_path = _ensure_mp4_video(job_dir, video_path)

    if not video_path.exists() or not audio_path.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Falha ao gerar MP4/MP3.")

    _register_job(job_id, job_dir)

    return {
        "job_id": job_id,
        "title": (info or {}).get("title"),
        "duration": (info or {}).get("duration"),
        "video_url": _build_file_url(request, job_id, video_path.name),
        "audio_url": _build_file_url(request, job_id, audio_path.name),
    }


@app.post("/transcript")
def transcript(req: TranscriptRequest):
    tmp_dir = tempfile.mkdtemp(prefix="downscribe_")
    wav_path = os.path.join(tmp_dir, "audio.wav")
    raw_url = _clean_input_url(str(req.url))

    try:
        info, audio_path = _download_best_audio(tmp_dir, raw_url)
        _ffmpeg_to_wav(audio_path, wav_path)

        try:
            with open(wav_path, "rb") as f:
                files = {"file": ("audio.wav", f, "audio/wav")}
                data = {"model": req.model, "include_segments": req.include_segments}
                r = requests.post(WHISPER_API_URL, files=files, data=data, timeout=600)
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Erro ao chamar whisper-service: {e}") from e

        result = r.json()
        transcript_text = result.get("text", "") or ""
        if not transcript_text.strip() and (req.model or "").strip().lower() != "small":
            try:
                with open(wav_path, "rb") as f:
                    files = {"file": ("audio.wav", f, "audio/wav")}
                    data = {"model": "small", "include_segments": req.include_segments}
                    r = requests.post(WHISPER_API_URL, files=files, data=data, timeout=600)
                r.raise_for_status()
                result = r.json()
                transcript_text = result.get("text", "") or ""
            except Exception:
                transcript_text = transcript_text or ""
        if not transcript_text.strip() and _is_instagram_url(raw_url):
            try:
                video_path = Path(tmp_dir) / "instagram_video.mp4"
                fallback_info = _visolix_download_instagram(raw_url, video_path)
                if isinstance(fallback_info, dict):
                    if not info.get("title") and fallback_info.get("title"):
                        info["title"] = fallback_info.get("title")
                    if not info.get("duration") and fallback_info.get("duration"):
                        info["duration"] = fallback_info.get("duration")
                audio_fallback = Path(tmp_dir) / "instagram_audio.mp3"
                _ffmpeg_extract_audio_mp3(str(video_path), str(audio_fallback))
                _ffmpeg_to_wav(str(audio_fallback), wav_path)
                with open(wav_path, "rb") as f:
                    files = {"file": ("audio.wav", f, "audio/wav")}
                    data = {"model": req.model, "include_segments": req.include_segments}
                    r = requests.post(WHISPER_API_URL, files=files, data=data, timeout=600)
                r.raise_for_status()
                result = r.json()
                transcript_text = result.get("text", "") or ""
            except Exception:
                transcript_text = transcript_text or ""

        payload = {
            "source_url": raw_url,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "transcript": transcript_text,
            "language": result.get("language"),
            "model": result.get("model"),
        }
        if req.include_segments:
            payload["segments"] = result.get("segments", [])
        return payload
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
