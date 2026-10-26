from __future__ import annotations

import json
import logging

from classlens.evaluator import list_mantle_models
from classlens.pipeline import check_transcription, handle_transcribe_event, process_job

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    if event.get("source") == "classlens.mantle" and event.get("action") == "list_models":
        return {"models": list_mantle_models()}
    if event.get("source") == "aws.transcribe":
        handle_transcribe_event(event)
        return {"status": "accepted"}
    failures = []
    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            payload = json.loads(record["body"])
            if payload.get("transcribe_check"):
                check_transcription(payload)
            else:
                process_job(payload["job_id"], payload["s3_key"])
        except Exception as exc:
            logger.exception(
                "Job processing failed for job %s (%s): %s",
                payload.get("job_id", "unknown"), type(exc).__name__, exc,
            )
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
