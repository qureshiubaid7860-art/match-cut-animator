from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Article:
    id: str
    upload_id: str
    filename: str
    path: str
    thumbnail_path: str
    page: int = 1
    source: dict[str, Any] = field(default_factory=dict)
    found: bool = False
    text: str = ""
    confidence: float = 0.0
    bbox: list[float] | None = None  # x, y, width, height in source pixels
    image_width: int = 0
    image_height: int = 0
    ocr_error: str | None = None
    analysis_target: str = ""
    detections: list[dict[str, Any]] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        thumbnail_bbox = None
        if self.bbox and self.image_width and self.image_height:
            scale = min(500 / self.image_width, 620 / self.image_height)
            pad_x = (500 - self.image_width * scale) / 2
            pad_y = (620 - self.image_height * scale) / 2
            thumbnail_bbox = [
                round(pad_x + self.bbox[0] * scale, 2),
                round(pad_y + self.bbox[1] * scale, 2),
                round(self.bbox[2] * scale, 2),
                round(self.bbox[3] * scale, 2),
            ]
        return {
            "id": self.id,
            "upload_id": self.upload_id,
            "filename": self.filename,
            "page": self.page,
            "thumbnail_url": f"/media/thumbnails/{self.id}.jpg",
            "found": self.found,
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "bbox": self.bbox,
            "thumbnail_bbox": thumbnail_bbox,
            "thumbnail_width": 500,
            "thumbnail_height": 620,
            "image_width": self.image_width,
            "image_height": self.image_height,
            "source": self.source,
            "ocr_error": self.ocr_error,
            "detections": self.detections,
        }


@dataclass
class UploadSession:
    id: str
    target_word: str = ""
    analysis_target: str = ""
    article_ids: list[str] = field(default_factory=list)


@dataclass
class Job:
    id: str
    upload_id: str
    target_word: str
    status: str = "queued"
    stage: str = "01 — Collecting Sources"
    percent: int = 0
    message: str = "Waiting to start"
    error: str | None = None
    video_path: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def public_progress(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "percent": self.percent,
            "message": self.message,
            "error": self.error,
            "events": self.events,
        }

    def public_result(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "video_url": f"/media/output/{self.id}.mp4" if self.video_path else None,
            "metadata": self.metadata,
            "error": self.error,
        }


SESSIONS: dict[str, UploadSession] = {}
ARTICLES: dict[str, Article] = {}
JOBS: dict[str, Job] = {}


def as_dict(value: Any) -> dict[str, Any]:
    return asdict(value)
