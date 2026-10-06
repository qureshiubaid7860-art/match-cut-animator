from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT_DIR / value


DATA_DIR = _path_from_env("MATCHCUT_DATA_DIR", "./data")
UPLOAD_DIR = DATA_DIR / "uploads"
THUMBNAIL_DIR = DATA_DIR / "thumbnails"
OUTPUT_DIR = DATA_DIR / "output"
WORK_DIR = DATA_DIR / "work"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "24")) * 1024 * 1024
MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", "18"))
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "36"))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "8"))
DEFAULT_DURATION = float(os.getenv("DEFAULT_DURATION", "10"))
DEFAULT_FPS = int(os.getenv("DEFAULT_FPS", "30"))
OUTPUT_WIDTH = int(os.getenv("OUTPUT_WIDTH", "1080"))
OUTPUT_HEIGHT = int(os.getenv("OUTPUT_HEIGHT", "1920"))
FFMPEG_BINARY = os.getenv("FFMPEG_BINARY", "").strip()
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()

for directory in (UPLOAD_DIR, THUMBNAIL_DIR, OUTPUT_DIR, WORK_DIR):
    directory.mkdir(parents=True, exist_ok=True)
