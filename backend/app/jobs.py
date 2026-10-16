from __future__ import annotations

import json
import logging
import queue
import threading
import gc
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import config
from .models import JOBS, Job

logger = logging.getLogger("matchcut.jobs")
GenerationHandler = Callable[[Job, Any], None]

_handler: GenerationHandler | None = None
_queue: queue.Queue[tuple[Job, Any]] = queue.Queue(maxsize=config.MAX_RENDER_QUEUE)
_worker_started = threading.Event()
_video_cleanup_started = threading.Event()
_active_renders = 0
_active_lock = threading.Lock()
_media_lock = threading.Lock()
_job_cache_lock = threading.RLock()
MAX_CACHED_JOBS = 80


def cleanup_expired_videos() -> None:
    """Remove temporary renders that were not downloaded before their TTL."""
    cutoff = time.time() - config.VIDEO_RETENTION_SECONDS
    for directory, pattern in (
        (config.WORK_DIR, "matchcut-video-*.mp4"),
        (config.OUTPUT_DIR, "*.mp4"),  # clean old persistent renders from prior versions
    ):
        for path in directory.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    logger.info("removed expired temporary video %s", path.name)
            except OSError:
                logger.warning("could not remove expired temporary video %s", path, exc_info=True)


def _video_cleanup_loop() -> None:
    while True:
        time.sleep(60)
        cleanup_expired_videos()


def start_video_cleanup() -> None:
    if _video_cleanup_started.is_set():
        return
    thread = threading.Thread(target=_video_cleanup_loop, name="matchcut-video-cleanup", daemon=True)
    thread.start()
    _video_cleanup_started.set()


def try_acquire_media_slot() -> bool:
    """Reserve this process's single memory-heavy media slot without waiting."""
    return _media_lock.acquire(blocking=False)


def acquire_media_slot() -> None:
    _media_lock.acquire()


def release_media_slot() -> None:
    _media_lock.release()


def _release_memory() -> None:
    gc.collect()
    # Return free Python/native heap pages to the OS on Linux when supported.
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (AttributeError, OSError):
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def job_path(job_id: str) -> Path:
    return config.JOBS_DIR / f"{job_id}.json"


def persist_job(job: Job) -> None:
    payload = {
        "id": job.id,
        "upload_id": job.upload_id,
        "target_word": job.target_word,
        "status": job.status,
        "stage": job.stage,
        "percent": job.percent,
        "message": job.message,
        "error": job.error,
        "video_path": job.video_path,
        "video_downloaded": job.video_downloaded,
        "sources": job.sources,
        "metadata": job.metadata,
        "events": job.events[-40:],
        "updated_at": _now(),
    }
    path = job_path(job.id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def job_from_payload(payload: dict[str, Any]) -> Job:
    job = Job(
        id=str(payload.get("id") or ""),
        upload_id=str(payload.get("upload_id") or ""),
        target_word=str(payload.get("target_word") or ""),
        status=str(payload.get("status") or "failed"),
        stage=str(payload.get("stage") or "09 — Finalizing MP4"),
        percent=int(payload.get("percent") or 0),
        message=str(payload.get("message") or ""),
        error=payload.get("error"),
        video_path=payload.get("video_path"),
        video_downloaded=bool(payload.get("video_downloaded", False)),
        sources=list(payload.get("sources") or []),
        metadata=dict(payload.get("metadata") or {}),
        events=list(payload.get("events") or []),
    )
    return job


def set_progress(job: Job, stage: str, percent: int, message: str, status: str | None = None) -> None:
    job.stage = stage
    job.percent = max(job.percent, min(100, int(percent)))
    job.message = message
    if status:
        job.status = status
    if not job.events or job.events[-1]["stage"] != stage or job.events[-1]["percent"] != job.percent:
        job.events.append({
            "stage": stage,
            "percent": job.percent,
            "message": message,
            "at": _now(),
        })
    try:
        persist_job(job)
    except OSError:
        logger.exception("Could not persist job %s", job.id)


def make_job(job_id: str, upload_id: str, target_word: str) -> Job:
    job = Job(id=job_id, upload_id=upload_id, target_word=target_word)
    set_progress(job, "01 — Searching Sources", 1, "Starting automated public-source search", "queued")
    return job


def cache_job(job: Job) -> None:
    """Keep recent job records in RAM; persisted job JSON remains reloadable."""
    with _job_cache_lock:
        while len(JOBS) >= MAX_CACHED_JOBS:
            stale_id = next((key for key, item in JOBS.items() if item.status in {"complete", "failed"}), None)
            if stale_id is None:
                break
            JOBS.pop(stale_id, None)
        JOBS[job.id] = job


def cached_job(job_id: str) -> Job | None:
    with _job_cache_lock:
        return JOBS.get(job_id)


def forget_job(job_id: str) -> None:
    with _job_cache_lock:
        JOBS.pop(job_id, None)


def register_generation_handler(handler: GenerationHandler) -> None:
    global _handler
    _handler = handler


def active_render_count() -> int:
    with _active_lock:
        return _active_renders


def enqueue_generation(job: Job, request: Any) -> None:
    set_progress(job, job.stage, job.percent, "Waiting for the renderer", "queued")
    try:
        _queue.put_nowait((job, request))
    except queue.Full as exc:
        raise RuntimeError("The render queue is full. Wait for the current video, then try again.") from exc
    logger.info("job=%s queued qsize=%s", job.id, _queue.qsize())


def _worker_loop() -> None:
    global _active_renders
    while True:
        job, request = _queue.get()
        if _handler is None:
            job.error = "Video generation worker is not ready. Please try again."
            set_progress(job, job.stage, max(job.percent, 1), job.error, "failed")
            _queue.task_done()
            continue
        media_locked = False
        with _active_lock:
            _active_renders += 1
        logger.info("job=%s render start active=%s", job.id, _active_renders)
        try:
            acquire_media_slot()
            media_locked = True
            _handler(job, request)
        except Exception:
            logger.exception("job=%s worker crashed", job.id)
            if job.status not in {"complete", "failed"}:
                job.error = "Video rendering failed because the worker stopped unexpectedly. Please try again."
                set_progress(job, "09 — Finalizing MP4", max(job.percent, 1), job.error, "failed")
        finally:
            _release_memory()
            if media_locked:
                release_media_slot()
            with _active_lock:
                _active_renders = max(0, _active_renders - 1)
            _queue.task_done()
            logger.info("job=%s render end active=%s", job.id, _active_renders)


def start_render_worker() -> None:
    if _worker_started.is_set():
        return
    for index in range(config.MAX_CONCURRENT_RENDERS):
        thread = threading.Thread(target=_worker_loop, name=f"matchcut-renderer-{index}", daemon=True)
        thread.start()
    _worker_started.set()
    logger.info("render workers started count=%s timeout=%s", config.MAX_CONCURRENT_RENDERS, config.RENDER_TIMEOUT)


def recover_jobs() -> None:
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    recovered = 0
    for path in sorted(config.JOBS_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:80]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            job = job_from_payload(payload)
            if not job.id:
                continue
            if job.status in {"queued", "running"}:
                job.error = "The server restarted during video rendering. Please generate the video again."
                job.status = "failed"
                job.message = job.error
                persist_job(job)
                recovered += 1
            cache_job(job)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.exception("Could not recover job file %s", path)
    logger.info("recovered %s interrupted jobs", recovered)
