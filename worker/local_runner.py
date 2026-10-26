"""Local Docker SQS consumer; AWS production uses the Lambda SQS event source."""
from __future__ import annotations

import json
import logging
import time
import os

import boto3

from classlens.config import settings
from classlens.pipeline import check_transcription, process_job

logging.basicConfig(level=logging.INFO)
client_kwargs = {"region_name": settings.aws_region}
if os.getenv("CLASSLENS_SQS_ENDPOINT_URL"):
    client_kwargs["endpoint_url"] = os.environ["CLASSLENS_SQS_ENDPOINT_URL"]
client = boto3.client("sqs", **client_kwargs)

while True:
    try:
        response = client.receive_message(QueueUrl=settings.queue_url, WaitTimeSeconds=20,
                                          VisibilityTimeout=5400, MaxNumberOfMessages=1)
        for message in response.get("Messages", []):
            try:
                payload = json.loads(message["Body"])
                if payload.get("transcribe_check"):
                    check_transcription(payload)
                else:
                    process_job(payload["job_id"], payload["s3_key"])
                client.delete_message(QueueUrl=settings.queue_url, ReceiptHandle=message["ReceiptHandle"])
            except Exception:
                logging.exception("Queue job failed; leaving it for retry")
    except Exception:
        logging.exception("Queue poll failed")
        time.sleep(5)
