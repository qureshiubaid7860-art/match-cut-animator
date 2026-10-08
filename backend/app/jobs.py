from __future__ import annotations

import json
import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import config
from .models import JOBS, Job

logger = logging.getLogger("matchcut.jobs")
GenerationHandler = Callable[[Job, Any], None]

_handler: GenerationHandler | None = None
_queue: queue.Queue[tuple[Job, Any]] = queue.Queue()
_worker_started = threading.Event()
_active_renders = 0
_active_lock = threading.Lock()


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


def register_generation_handler(handler: GenerationHandler) -> None:
    global _handler
    _handler = handler


def active_render_count() -> int:
    with _active_lock:
        return _active_renders


def enqueue_generation(job: Job, request: Any) -> None:
    if _queue.qsize() >= config.MAX_RENDER_QUEUE:
        raise RuntimeError("Another video is already rendering. Wait for it to finish, then try again.")
    set_progress(job, job.stage, job.percent, "Waiting for the renderer", "queued")
    _queue.put((job, request))
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
        with _active_lock:
            _active_renders += 1
        logger.info("job=%s render start active=%s", job.id, _active_renders)
        try:
            _handler(job, request)
        except Exception:
            logger.exception("job=%s worker crashed", job.id)
            if job.status not in {"complete", "failed"}:
                job.error = "Video rendering failed because the worker stopped unexpectedly. Please try again."
                set_progress(job, "09 — Finalizing MP4", max(job.percent, 1), job.error, "failed")
        finally:
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
            JOBS[job.id] = job
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            logger.exception("Could not recover job file %s", path)
    logger.info("recovered %s interrupted jobs", recovered)
