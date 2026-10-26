from __future__ import annotations

import json
from pathlib import PurePosixPath

import boto3

from .config import settings


def start_transcription(s3_key: str, job_id: str) -> str:
    """Start an asynchronous Transcribe job; EventBridge resumes analysis on completion."""
    if not settings.uploads_bucket or not s3_key or not s3_key.startswith("uploads/"):
        raise ValueError("Transcription requires a configured bucket and a valid uploaded video key")
    client = boto3.client("transcribe", region_name=settings.aws_region)
    job_name = f"classlens-{job_id}"[:200]
    media_uri = f"s3://{settings.uploads_bucket}/{s3_key}"
    try:
        client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={"MediaFileUri": media_uri},
            MediaFormat="webm" if PurePosixPath(s3_key).suffix.lower() == ".webm" else "mp4",
            LanguageCode=settings.language_code,
            OutputBucketName=settings.uploads_bucket,
            OutputKey=f"transcripts/{job_id}/",
        )
    except client.exceptions.ConflictException:
        # An SQS retry after a transient failure may find the first transcription still running.
        client.get_transcription_job(TranscriptionJobName=job_name)
    return job_name


def fetch_transcript(job_name: str) -> list[dict]:
    client = boto3.client("transcribe", region_name=settings.aws_region)
    result = client.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
    if result["TranscriptionJobStatus"] != "COMPLETED":
        raise RuntimeError("Transcription completion event arrived before the result was available")
    transcript_uri = result["Transcript"]["TranscriptFileUri"]
    from urllib.parse import urlparse
    parsed = urlparse(transcript_uri)
    if parsed.scheme == "s3":
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
    else:
        # Transcribe returns an HTTPS URI for a configured output bucket in some regions.
        key = f"transcripts/{job_name.removeprefix('classlens-')}/{job_name}.json"
        bucket = settings.uploads_bucket
    kwargs = {"region_name": settings.aws_region}
    import os
    if os.getenv("CLASSLENS_S3_ENDPOINT_URL"):
        kwargs["endpoint_url"] = os.environ["CLASSLENS_S3_ENDPOINT_URL"]
    response = boto3.client("s3", **kwargs).get_object(Bucket=bucket, Key=key)
    return _segments(json.loads(response["Body"].read()))


def transcription_status(job_name: str) -> str:
    client = boto3.client("transcribe", region_name=settings.aws_region)
    return client.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJobStatus"]


def _segments(document: dict) -> list[dict]:
    items = document.get("results", {}).get("items", [])
    segments: list[dict] = []
    words: list[str] = []
    start: float | None = None
    end = 0.0
    for item in items:
        alternatives = item.get("alternatives", [])
        if not alternatives:
            continue
        if item.get("type") == "punctuation":
            if words:
                words[-1] += alternatives[0].get("content", "")
            continue
        content = alternatives[0].get("content", "")
        start = start if start is not None else float(item.get("start_time", 0))
        end = float(item.get("end_time", start))
        words.append(content)
        if end - start >= 15:
            segments.append({"id": f"segment_{len(segments) + 1:03d}", "start": start, "end": end,
                             "text": " ".join(words)})
            words, start = [], None
    if words and start is not None:
        segments.append({"id": f"segment_{len(segments) + 1:03d}", "start": start, "end": end,
                         "text": " ".join(words)})
    return segments
