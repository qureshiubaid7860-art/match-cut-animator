from __future__ import annotations

from datetime import datetime, timezone

from .models import Job


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
            "at": datetime.now(timezone.utc).isoformat(),
        })


def make_job(job_id: str, upload_id: str, target_word: str) -> Job:
    job = Job(id=job_id, upload_id=upload_id, target_word=target_word)
    set_progress(job, "01 — Searching Sources", 1, "Starting automated public-source search", "queued")
    return job
