from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    uploads_bucket: str = os.getenv("CLASSLENS_UPLOADS_BUCKET", "")
    jobs_table: str = os.getenv("CLASSLENS_JOBS_TABLE", "")
    queue_url: str = os.getenv("CLASSLENS_QUEUE_URL", "")
    bedrock_model_id: str = os.getenv("BEDROCK_MODEL_ID", "google.gemma-4-31b")
    bedrock_mantle_region: str = os.getenv(
        "BEDROCK_MANTLE_REGION", os.getenv("AWS_REGION", "us-east-1")
    )
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "500"))
    max_duration_seconds: int = int(os.getenv("MAX_DURATION_SECONDS", "10800"))
    frame_interval_seconds: int = int(os.getenv("FRAME_INTERVAL_SECONDS", "8"))
    max_frames: int = int(os.getenv("MAX_FRAMES", "48"))
    retention_days: int = int(os.getenv("RETENTION_DAYS", "7"))
    allowed_origins: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501")
    language_code: str = os.getenv("TRANSCRIBE_LANGUAGE_CODE", "en-US")


settings = Settings()
