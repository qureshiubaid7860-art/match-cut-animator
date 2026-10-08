from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT_DIR / value


def _int_from_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_from_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


DATA_DIR = _path_from_env("MATCHCUT_DATA_DIR", "./data")
UPLOAD_DIR = DATA_DIR / "uploads"
THUMBNAIL_DIR = DATA_DIR / "thumbnails"
OUTPUT_DIR = _path_from_env("MATCHCUT_OUTPUT_DIR", str(DATA_DIR / "output"))
WORK_DIR = _path_from_env("MATCHCUT_TEMP_DIR", str(DATA_DIR / "work"))
JOBS_DIR = DATA_DIR / "jobs"
MAX_UPLOAD_BYTES = _int_from_env("MAX_UPLOAD_MB", 24) * 1024 * 1024
MAX_UPLOAD_FILES = _int_from_env("MAX_UPLOAD_FILES", 18)
MAX_ARTICLES = _int_from_env("MAX_ARTICLES", 36)
MAX_PDF_PAGES = _int_from_env("MAX_PDF_PAGES", 8)
DEFAULT_DURATION = _float_from_env("DEFAULT_DURATION", 10)
DEFAULT_FPS = _int_from_env("DEFAULT_FPS", 30)
OUTPUT_WIDTH = _int_from_env("OUTPUT_WIDTH", 1080)
OUTPUT_HEIGHT = _int_from_env("OUTPUT_HEIGHT", 1920)
FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "").strip()
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()
MAX_CONCURRENT_RENDERS = max(1, _int_from_env("MAX_CONCURRENT_RENDERS", 1))
MAX_RENDER_QUEUE = max(1, _int_from_env("MAX_RENDER_QUEUE", 4))
RENDER_TIMEOUT = max(30.0, _float_from_env("RENDER_TIMEOUT", 240))
API_BASE_URL = os.getenv("API_BASE_URL", "").strip().rstrip("/")
FRONTEND_URL = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000,https://match-cut-animator.fastapicloud.dev",
    ).split(",")
    if origin.strip()
]
if FRONTEND_URL and FRONTEND_URL not in CORS_ORIGINS:
    CORS_ORIGINS.append(FRONTEND_URL)

for directory in (UPLOAD_DIR, THUMBNAIL_DIR, OUTPUT_DIR, WORK_DIR, JOBS_DIR):
    directory.mkdir(parents=True, exist_ok=True)
