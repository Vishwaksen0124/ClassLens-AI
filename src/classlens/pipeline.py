from __future__ import annotations

import os
import tempfile
from typing import Any

import boto3

from .config import settings
from .evaluator import evaluate
from .media import inspect_video, sample_frames
from .storage import fetch_job, save_frames, save_result, send_job, update_job
from .transcription import fetch_transcript, start_transcription, transcription_status
from .workflow import task_statuses_for_stage


def process_job(job_id: str, s3_key: str) -> dict[str, Any] | None:
    """SQS intake: inspect media, then let Transcribe run without holding a Lambda open."""
    try:
        existing = fetch_job(job_id)
        if existing and existing.get("status") == "completed":
            return None
        # DynamoDB is the durable source of truth. Recover a missing/blank key in a
        # queued message from the job record rather than passing an empty media URI
        # to Transcribe.
        if not s3_key and existing:
            s3_key = existing.get("s3_key", "")
        if not s3_key or not s3_key.startswith("uploads/"):
            raise ValueError(f"Job {job_id} has no valid uploaded video key")
        _set_stage(job_id, "downloading", 5)
        with tempfile.TemporaryDirectory(prefix="classlens-") as work_dir:
            video_path = os.path.join(work_dir, "lesson-video")
            _s3_client().download_file(settings.uploads_bucket, s3_key, video_path)
            _set_stage(job_id, "inspecting_video", 10)
            duration, has_audio = inspect_video(video_path)
            if has_audio:
                _set_stage(job_id, "transcribing", 25)
                job_name = start_transcription(s3_key, job_id)
                _set_stage(job_id, "transcribing", 30,
                           recording_duration_seconds=duration, has_audio=True,
                           transcription_job_name=job_name)
                # EventBridge normally resumes the job. This delayed check is a fallback because
                # Transcribe events are delivered on a best-effort basis.
                send_job(job_id, s3_key, delay_seconds=300, transcribe_job_name=job_name,
                         transcribe_check=True, duration=duration)
                return None
            return _finish_analysis(job_id, s3_key, duration, False, [])
    except Exception:
        _mark_failed(job_id)
        raise


def handle_transcribe_event(event: dict) -> None:
    detail = event.get("detail", {})
    job_name = detail.get("TranscriptionJobName", "")
    if not job_name.startswith("classlens-"):
        return
    job_id = job_name.removeprefix("classlens-")
    job = fetch_job(job_id)
    if not job or job.get("transcription_job_name") != job_name or job.get("status") == "completed":
        return
    if detail.get("TranscriptionJobStatus") == "FAILED":
        _mark_failed(job_id)
        return
    try:
        transcript = fetch_transcript(job_name)
        _finish_analysis(job_id, job["s3_key"], float(job["recording_duration_seconds"]), True, transcript)
    except Exception:
        # Let Lambda's async retries and the delayed SQS reconciliation retry transient failures.
        raise


def check_transcription(payload: dict) -> None:
    """Recover if the best-effort Transcribe completion event was not delivered."""
    job = fetch_job(payload["job_id"])
    if not job or job.get("status") == "completed":
        return
    job_name = payload["transcribe_job_name"]
    try:
        status = transcription_status(job_name)
        if status == "COMPLETED":
            transcript = fetch_transcript(job_name)
            _finish_analysis(payload["job_id"], payload["s3_key"], float(payload["duration"]), True, transcript)
        elif status == "FAILED":
            _mark_failed(payload["job_id"])
        else:
            send_job(payload["job_id"], payload["s3_key"], delay_seconds=300,
                     transcribe_job_name=job_name, transcribe_check=True, duration=payload["duration"])
    except Exception:
        _mark_failed(payload["job_id"])
        raise


def _finish_analysis(job_id: str, s3_key: str, duration: float, has_audio: bool,
                     transcript: list[dict]) -> dict[str, Any]:
    _set_stage(job_id, "sampling_frames", 60, has_audio=has_audio)
    with tempfile.TemporaryDirectory(prefix="classlens-") as work_dir:
        video_path = os.path.join(work_dir, "lesson-video")
        _s3_client().download_file(settings.uploads_bucket, s3_key, video_path)
        frames = sample_frames(video_path, duration, os.path.join(work_dir, "frames"))
        if not frames:
            raise ValueError("Could not extract readable video frames")

        _set_stage(job_id, "evaluating", 75, has_audio=has_audio)
        report = evaluate(duration, transcript, frames)
        report["recording_duration_seconds"] = duration
        report["transcript_segments"] = transcript
        report["sampled_frame_count"] = len(frames)
        report["sampled_frames"] = save_frames(job_id, frames)
        if not has_audio:
            report["limitations"].append("The video has no audio track; verbal communication could not be assessed.")
        else:
            report["limitations"].append("Audio was transcribed but the original sound was not assessed; audio and verbal communication could not be scored.")
        _set_dimension_insufficient(report, "Audio and Verbal Communication",
                                    "The evaluator received transcript text, not audio samples, so this dimension cannot be fairly scored.")
        result_key = save_result(job_id, report)
        _set_stage(job_id, "completed", 100, has_audio=has_audio,
                   status="completed", result_key=result_key, error=None)
        return report


def _s3_client():
    kwargs = {"region_name": settings.aws_region}
    if os.getenv("CLASSLENS_S3_ENDPOINT_URL"):
        kwargs["endpoint_url"] = os.environ["CLASSLENS_S3_ENDPOINT_URL"]
    return boto3.client("s3", **kwargs)


def _mark_failed(job_id: str) -> None:
    job = fetch_job(job_id) or {}
    stage = job.get("stage", "queued")
    task_statuses = task_statuses_for_stage(stage, failed=True,
                                           has_audio=bool(job.get("has_audio", True)))
    update_job(job_id, status="failed", stage="failed", progress=100, task_statuses=task_statuses,
               error="The assessment could not be completed. Please try again or contact support.")


def _set_stage(job_id: str, stage: str, progress: int, *, has_audio: bool = True,
               status: str = "processing", **fields: Any) -> None:
    update_job(job_id, status=status, stage=stage, progress=progress,
               task_statuses=task_statuses_for_stage(stage, has_audio=has_audio), **fields)


def _set_dimension_insufficient(report: dict, name: str, rationale: str) -> None:
    for dimension in report["dimensions"]:
        if dimension["name"] == name:
            dimension["score"] = None
            dimension["rationale"] = rationale
            dimension["limitations"] = [rationale]
    from .scoring import calculate_classlens_score
    aggregates = calculate_classlens_score({item["name"]: item["score"] for item in report["dimensions"]})
    report.update(aggregates)
