from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config
from ..jobs import enqueue_generation, job_from_payload, job_path, make_job, register_generation_handler, set_progress
from ..models import ARTICLES, JOBS, SESSIONS, Job
from ..ocr.engine import normalized_terms
from ..services.analysis import analyze_articles
from ..services.acquisition import acquire_sources, order_for_match_cuts
from ..services.demo import create_demo
from ..services.storage import add_upload, create_session, session_articles
from ..video.renderer import render_video, validate_articles

logger = logging.getLogger("matchcut.api")

router = APIRouter(prefix="/api")


class AnalyzeRequest(BaseModel):
    upload_id: str
    target_word: str
    article_ids: list[str] | None = None


class GenerateRequest(BaseModel):
    upload_id: str | None = None
    target_word: str
    article_ids: list[str] | None = None
    number_of_articles: int = Field(default=6, ge=3, le=36)
    duration: float = Field(default_factory=lambda: config.DEFAULT_DURATION, ge=3.0, le=30.0)
    fps: int = Field(default_factory=lambda: config.DEFAULT_FPS, ge=24, le=60)
    width: int = Field(default_factory=lambda: config.OUTPUT_WIDTH, ge=540, le=2160)
    height: int = Field(default_factory=lambda: config.OUTPUT_HEIGHT, ge=960, le=3840)
    sfx_enabled: bool = True
    background_enabled: bool = False
    sfx_volume: int = Field(default=72, ge=0, le=100)
    background_volume: int = Field(default=32, ge=0, le=100)
    caption: str = Field(default="", max_length=80)
    sound_style: Literal["automatic", "minimal", "documentary", "paper", "marker", "impact", "cinematic"] = "documentary"
    sfx_id: Literal["page_turn", "paper_flip", "paper_slide", "mouse_click", "soft_whoosh", "impact", "click", "typewriter", "typewriter_return", "typewriter_bell", "editorial_tick", "film_projector", "camera_shutter", "marker_write", "none"] = "click"
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:5", "4:3"] | None = None
    highlight_mode: Literal["default", "highlight", "underline"] = "default"


class SearchRequest(BaseModel):
    target_word: str
    number_of_articles: int = Field(default=6, ge=3, le=36)


class FeedbackRequest(BaseModel):
    rating: int = Field(default=5, ge=1, le=5)
    issue_type: str = Field(default="Feature request", max_length=80)
    message: str = Field(default="", max_length=1500)


ASPECT_DIMENSIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "4:3": (1440, 1080),
}


def _target(value: str) -> str:
    cleaned = value.strip()
    terms = normalized_terms(cleaned)
    if len(cleaned) > 64 or not terms or len(terms) > 4:
        raise HTTPException(status_code=422, detail="Enter a word or a short phrase of up to 4 words (64 characters) to find in the article images.")
    return cleaned


def _valid_session(upload_id: str):
    session = SESSIONS.get(upload_id)
    if not session:
        raise HTTPException(status_code=404, detail="Upload session not found. Upload the article images again.")
    return session


def _analysis_payload(upload_id: str, target_word: str, articles):
    found = sum(article.found for article in articles)
    errors = [article for article in articles if article.ocr_error]
    if articles and len(errors) == len(articles):
        state = "ocr_failed"
        message = errors[0].ocr_error or "OCR could not read the images."
    elif found < 3:
        state = "insufficient"
        message = f"Found “{target_word}” in {found} of {len(articles)} images. At least 3 readable matches are needed to make a video."
    else:
        state = "ready"
        message = f"Found “{target_word}” in {found} images. Review the focus frames, then generate."
    return {
        "status": state,
        "upload_id": upload_id,
        "target_word": target_word,
        "article_count": len(articles),
        "found_count": found,
        "eligible_count": found,
        "message": message,
        "articles": [article.public() for article in articles],
    }


_FEEDBACK_RATE_LIMIT: dict[str, float] = {}


def _feedback_store_path() -> Path:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / "feedback.json"


def _store_feedback(entry: dict) -> None:
    path = _feedback_store_path()
    current = []
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            current = []
    current.append(entry)
    path.write_text(json.dumps(current[-200:], indent=2), encoding="utf-8")


@router.get("/health")
def health():
    from ..video.renderer import ffmpeg_path
    try:
        ffmpeg = Path(ffmpeg_path()).name
        ffmpeg_ready = True
    except Exception:
        ffmpeg, ffmpeg_ready = "Unavailable", False
    return {
        "status": "ok",
        "ocr": "RapidOCR · local models" if _rapidocr_installed() else "not installed",
        "ffmpeg": ffmpeg,
        "ffmpeg_ready": ffmpeg_ready,
        "auto_search_configured": True,
        "auto_search_provider": "GDELT DOC",
        "supported_formats": ["jpg", "jpeg", "png", "webp", "pdf"],
        "output": {"width": config.OUTPUT_WIDTH, "height": config.OUTPUT_HEIGHT, "fps": config.DEFAULT_FPS, "duration": config.DEFAULT_DURATION},
        "render_timeout": config.RENDER_TIMEOUT,
        "max_concurrent_renders": config.MAX_CONCURRENT_RENDERS,
    }


def _rapidocr_installed():
    try:
        import rapidocr  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


@router.post("/upload")
async def upload_articles(
    files: Annotated[list[UploadFile], File(...)],
    upload_id: Annotated[str | None, Form()] = None,
    target_word: Annotated[str | None, Form()] = None,
):
    if not files:
        raise HTTPException(status_code=422, detail="Choose at least one article image or PDF.")
    if len(files) > config.MAX_UPLOAD_FILES:
        raise HTTPException(status_code=413, detail=f"Upload at most {config.MAX_UPLOAD_FILES} files at a time.")
    session = SESSIONS.get(upload_id) if upload_id else None
    if upload_id and not session:
        raise HTTPException(status_code=404, detail="Upload session not found. Start a new upload.")
    session = session or create_session()
    added = []
    total_bytes = 0
    errors = []
    try:
        for uploaded_file in files:
            filename = Path(uploaded_file.filename or "article").name
            payload = await uploaded_file.read(config.MAX_UPLOAD_BYTES + 1)
            total_bytes += len(payload)
            if len(payload) > config.MAX_UPLOAD_BYTES:
                errors.append(f"{filename}: file exceeds {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
                continue
            if total_bytes > config.MAX_UPLOAD_BYTES * min(len(files), 5):
                errors.append("The total upload is too large. Try fewer or smaller files.")
                break
            try:
                batch = add_upload(session.id, filename, payload)
                if len(session.article_ids) + len(batch) > config.MAX_ARTICLES:
                    for article in batch:
                        Path(article.path).unlink(missing_ok=True)
                        Path(article.thumbnail_path).unlink(missing_ok=True)
                        ARTICLES.pop(article.id, None)
                    errors.append(f"{filename}: this project can contain at most {config.MAX_ARTICLES} image pages.")
                    continue
                for article in batch:
                    session.article_ids.append(article.id)
                added.extend(batch)
            except ValueError as exc:
                errors.append(str(exc))
    finally:
        for uploaded_file in files:
            await uploaded_file.close()
    if target_word:
        session.target_word = _target(target_word)
    if not added:
        raise HTTPException(status_code=422, detail=" ".join(errors) if errors else "No valid article images were uploaded.")
    return {
        "upload_id": session.id,
        "target_word": session.target_word,
        "articles": [article.public() for article in added],
        "errors": errors,
        "message": f"Added {len(added)} article image{'s' if len(added) != 1 else ''}. Run OCR to find the target word.",
    }


@router.post("/analyze")
def analyze(request: AnalyzeRequest):
    target_word = _target(request.target_word)
    session = _valid_session(request.upload_id)
    articles = session_articles(request.upload_id, request.article_ids)
    if not articles:
        raise HTTPException(status_code=422, detail="This upload does not contain any article images to analyze.")
    session.target_word = target_word
    analyze_articles(articles, target_word)
    session.analysis_target = target_word
    return _analysis_payload(request.upload_id, target_word, articles)


def _safe_error(exc: BaseException) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if any(token in lowered for token in ("api_key", "token=", "password", "secret", "authorization")):
        return "Video generation failed because a configured service rejected the request."
    if "memory" in lowered or "killed" in lowered:
        return "The server ran out of memory while rendering. Try fewer pages or a shorter duration, then generate again."
    if isinstance(exc, TimeoutError) or "timed out" in lowered:
        return "Video rendering timed out. Try a shorter duration or fewer pages, then generate again."
    if "ffmpeg" in lowered and "unavailable" in lowered:
        return "FFmpeg is not available on the server, so the MP4 cannot be encoded."
    if "job not found" in lowered or "restarted" in lowered:
        return "The server restarted during rendering. Please generate the video again."
    return message[:500]


def _generation_worker(job: Job, request: GenerateRequest) -> None:
    try:
        logger.info(
            "job=%s generate start word=%s pages=%s ratio=%s duration=%s fps=%s size=%sx%s sfx=%s highlight=%s",
            job.id, request.target_word, request.number_of_articles, request.aspect_ratio,
            request.duration, request.fps, request.width, request.height, request.sfx_id, request.highlight_mode,
        )
        word = _target(request.target_word)
        if request.upload_id:
            session = SESSIONS.get(request.upload_id)
            if not session:
                raise ValueError("Source session expired. Start a new automatic search.")
            job.upload_id = session.id
            set_progress(job, "01 — Searching Sources", 4, "Using the selected article pages", "running")
            articles = session_articles(session.id, request.article_ids)
            if not articles:
                raise ValueError("No article pages were selected for this video.")
            session.target_word = word
            if any(article.analysis_target != word for article in articles) or any(article.ocr_error for article in articles) or any(article.found and not article.bbox for article in articles):
                set_progress(job, "03 — Running OCR", 20, "Reading article text and coordinates")
                def ocr_progress(value: int, message: str):
                    set_progress(job, "03 — Running OCR", 20 + round(value * 0.20), message)
                analyze_articles(articles, word, ocr_progress)
        else:
            def acquisition_progress(stage: str, percent: int, message: str):
                set_progress(job, stage, percent, message, "running")
            session, articles = acquire_sources(word, request.number_of_articles, acquisition_progress)
            job.upload_id = session.id
        if articles and all(article.ocr_error for article in articles):
            raise RuntimeError(f"OCR could not read these images: {articles[0].ocr_error}")
        session.analysis_target = word
        matches = order_for_match_cuts(
            [article for article in articles if article.found and article.bbox]
        )
        set_progress(job, "04 — Finding Target Word", 44, f"Target word found in {len(matches)} of {len(articles)} pages")
        if len(matches) < 3:
            raise ValueError(f"Found “{word}” in {len(matches)} images. At least 3 readable matches are needed to render a video.")
        matches = matches[:request.number_of_articles]
        validate_articles(matches)
        logger.info(
            "job=%s articles=%s dims=%s",
            job.id, len(matches),
            [(article.image_width, article.image_height, article.filename) for article in matches],
        )
        set_progress(job, "05 — Selecting Best Matches", 52, f"Ranking OCR clarity and target-word framing across {len(matches)} pages")
        set_progress(job, "06 — Building Match Cut Timeline", 60, "Locking the word to one shared screen position and scale")
        set_progress(job, "07 — Designing Sound", 67, f"Preparing the {request.sfx_id} sound effect and {request.sound_style} background")
        output_width, output_height = ASPECT_DIMENSIONS[request.aspect_ratio] if request.aspect_ratio else (request.width, request.height)
        set_progress(job, "08 — Rendering Video", 73, f"Encoding {output_width} × {output_height} H.264 frames")
        output_path = config.OUTPUT_DIR / f"{job.id}.mp4"
        def render_progress(value: int, message: str):
            percent = 73 + round(value * 0.23)
            stage = "07 — Designing Sound" if "sound timeline" in message.lower() else "08 — Rendering Video"
            set_progress(job, stage, percent, message)
        metadata = render_video(
            matches, output_path,
            duration=request.duration,
            fps=request.fps,
            width=output_width,
            height=output_height,
            sfx_enabled=request.sfx_enabled,
            background_enabled=request.background_enabled,
            sfx_volume=request.sfx_volume,
            background_volume=request.background_volume,
            caption=request.caption,
            sound_style=request.sound_style,
            sfx_id=request.sfx_id,
            aspect_ratio=request.aspect_ratio,
            target_word=word,
            highlight_mode=request.highlight_mode,
            progress=render_progress,
        )
        if request.caption.strip():
            metadata["caption"] = request.caption.strip()
        sources = []
        for article in matches:
            data = dict(article.source or {})
            data.setdefault("filename", article.filename)
            data.setdefault("page", article.page)
            data["article_id"] = article.id
            data["thumbnail_url"] = f"/media/thumbnails/{article.id}.jpg"
            data["confidence"] = round(article.confidence, 4)
            data["target_bbox"] = article.bbox
            data["bbox"] = article.bbox
            data["image_width"] = article.image_width
            data["image_height"] = article.image_height
            if article.bbox and article.image_width and article.image_height:
                thumb_scale = min(500 / article.image_width, 620 / article.image_height)
                data["thumbnail_bbox"] = [
                    round((500 - article.image_width * thumb_scale) / 2 + article.bbox[0] * thumb_scale, 2),
                    round((620 - article.image_height * thumb_scale) / 2 + article.bbox[1] * thumb_scale, 2),
                    round(article.bbox[2] * thumb_scale, 2),
                    round(article.bbox[3] * thumb_scale, 2),
                ]
                data["thumbnail_width"] = 500
                data["thumbnail_height"] = 620
            sources.append(data)
        job.video_path = str(output_path)
        job.metadata = metadata
        job.sources = sources
        logger.info("job=%s complete bytes=%s url=/media/output/%s.mp4", job.id, output_path.stat().st_size, job.id)
        set_progress(job, "09 — Finalizing MP4", 100, "MP4 is ready to preview and download", "complete")
    except Exception as exc:
        logger.exception("job=%s generate failed", job.id)
        job.error = _safe_error(exc)
        set_progress(job, "09 — Finalizing MP4", max(job.percent, 1), job.error, "failed")


register_generation_handler(_generation_worker)


@router.post("/generate")
def generate(request: GenerateRequest):
    if request.upload_id:
        _valid_session(request.upload_id)
    _target(request.target_word)
    job_id = uuid.uuid4().hex
    job = make_job(job_id, request.upload_id or "", request.target_word.strip())
    JOBS[job_id] = job
    try:
        enqueue_generation(job, request)
    except RuntimeError as exc:
        JOBS.pop(job_id, None)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"job_id": job.id, "status": job.status, "message": "Automatic source search and video generation have started."}


@router.post("/demo")
def demo(duration: float = 10.0):
    upload_id, articles = create_demo()
    job_id = uuid.uuid4().hex
    request = GenerateRequest(upload_id=upload_id, target_word="NASA", duration=duration)
    job = make_job(job_id, upload_id, "NASA")
    JOBS[job_id] = job
    try:
        enqueue_generation(job, request)
    except RuntimeError as exc:
        JOBS.pop(job_id, None)
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {
        "job_id": job.id,
        "upload_id": upload_id,
        "target_word": "NASA",
        "status": job.status,
        "articles": [article.public() for article in articles],
        "message": "Created six local newspaper layouts. OCR and MP4 rendering are running now.",
    }


def _load_job(job_id: str) -> Job:
    job = JOBS.get(job_id)
    if job:
        return job
    path = job_path(job_id)
    if path.is_file():
        try:
            job = job_from_payload(json.loads(path.read_text(encoding="utf-8")))
            JOBS[job.id] = job
            return job
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.exception("Could not read persisted job %s", job_id)
    raise HTTPException(status_code=404, detail="Generation job not found or the server has restarted. Please generate the video again.")


@router.get("/progress/{job_id}")
def progress(job_id: str):
    return _load_job(job_id).public_progress()


@router.get("/result/{job_id}")
def result(job_id: str):
    return _load_job(job_id).public_result()


@router.get("/download/{job_id}")
def download(job_id: str):
    job = _load_job(job_id)
    if not getattr(job, 'video_path', None):
        raise HTTPException(status_code=404, detail="Video not found")
    path = Path(job.video_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(path, media_type="video/mp4", filename=f"{job_id}.mp4")

@router.get("/sources/{job_id}")
def sources(job_id: str):
    job = _load_job(job_id)
    return {"job_id": job.id, "sources": job.sources}


@router.get("/capabilities")
def capabilities():
    return {
        "auto_search_configured": True,
        "auto_search_provider": "GDELT DOC" + (" + NewsAPI" if config.NEWSAPI_KEY else ""),
        "provider_message": "Public article search is ready. Clearly labeled fictional editorials fill any remaining slots automatically.",
        "generated_fallback": True,
    }


@router.post("/feedback")
def feedback(request: FeedbackRequest, req: Request):
    client_key = (req.headers.get("x-forwarded-for") or req.headers.get("x-real-ip") or str(req.client.host if req.client else "unknown")).strip()
    now = time.time()
    last_seen = _FEEDBACK_RATE_LIMIT.get(client_key, 0.0)
    if now - last_seen < 30:
        raise HTTPException(status_code=429, detail="Too many feedback submissions. Please wait a moment and try again.")
    _FEEDBACK_RATE_LIMIT[client_key] = now
    entry = {
        "rating": request.rating,
        "issue_type": request.issue_type,
        "message": request.message.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _store_feedback(entry)
    return {"ok": True, "message": "Thanks for your feedback."}


@router.post("/search")
def search(request: SearchRequest):
    word = _target(request.target_word)
    try:
        session, added = acquire_sources(word, request.number_of_articles)
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Automatic source acquisition failed: {str(exc)[:220]}") from exc
    payload = _analysis_payload(session.id, word, added)
    return {**payload, "provider": "GDELT DOC with fictional-page fallback", "source_count": len(added)}
from fastapi.responses import FileResponse
