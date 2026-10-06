from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..models import Article
from ..ocr.engine import analyze_image, write_box_thumbnail


def analyze_articles(articles: list[Article], target_word: str, progress: Callable[[int, str], None] | None = None) -> list[Article]:
    for index, article in enumerate(articles):
        article.analysis_target = target_word
        try:
            result = analyze_image(article.path, target_word)
            article.image_width = result["image_width"]
            article.image_height = result["image_height"]
            article.text = result["detected_text"]
            article.detections = result["detections"]
            selected = result["selected"]
            article.found = selected is not None
            article.bbox = selected["bbox"] if selected else None
            article.confidence = selected["confidence"] if selected else 0.0
            article.ocr_error = None
            write_box_thumbnail(article.path, article.thumbnail_path, article.bbox)
        except Exception as exc:
            article.found = False
            article.bbox = None
            article.confidence = 0.0
            article.detections = []
            article.ocr_error = str(exc)
        if progress:
            progress(round((index + 1) / max(len(articles), 1) * 100), f"Read {index + 1} of {len(articles)} image{'s' if len(articles) != 1 else ''}")
    return articles
