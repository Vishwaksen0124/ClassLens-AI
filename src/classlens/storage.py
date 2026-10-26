from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from typing import Any

import boto3

from .config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _endpoint(service: str) -> str | None:
    return os.getenv(f"CLASSLENS_{service.upper()}_ENDPOINT_URL")


def get_table():
    if not settings.jobs_table:
        raise RuntimeError("CLASSLENS_JOBS_TABLE is required")
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("dynamodb"):
        kwargs["endpoint_url"] = _endpoint("dynamodb")
    return boto3.resource("dynamodb", **kwargs).Table(settings.jobs_table)


def create_job(job: dict[str, Any]) -> None:
    get_table().put_item(Item=job, ConditionExpression="attribute_not_exists(job_id)")


def update_job(job_id: str, **values: Any) -> None:
    if not values:
        return
    names, vals, assignments = {}, {":updated": _now()}, []
    for index, (key, value) in enumerate(values.items()):
        name, token = f"#n{index}", f":v{index}"
        names[name] = key
        vals[token] = _to_dynamo(value)
        assignments.append(f"{name} = {token}")
    assignments.append("updated_at = :updated")
    get_table().update_item(Key={"job_id": job_id}, UpdateExpression="SET " + ", ".join(assignments),
                            ExpressionAttributeNames=names, ExpressionAttributeValues=vals)


def fetch_job(job_id: str) -> dict[str, Any] | None:
    item = get_table().get_item(Key={"job_id": job_id}, ConsistentRead=True).get("Item")
    if not item:
        return None
    return json.loads(json.dumps(item, default=_json_default))


def save_result(job_id: str, result: dict[str, Any]) -> str:
    if not settings.uploads_bucket:
        raise RuntimeError("CLASSLENS_UPLOADS_BUCKET is required")
    key = f"results/{job_id}/assessment.json"
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("s3"):
        kwargs["endpoint_url"] = _endpoint("s3")
    boto3.client("s3", **kwargs).put_object(
        Bucket=settings.uploads_bucket, Key=key,
        Body=json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        ContentType="application/json", ServerSideEncryption="AES256",
    )
    return key


def fetch_result(key: str) -> dict[str, Any]:
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("s3"):
        kwargs["endpoint_url"] = _endpoint("s3")
    response = boto3.client("s3", **kwargs).get_object(Bucket=settings.uploads_bucket, Key=key)
    return json.loads(response["Body"].read())


def save_document(job_id: str, document_id: str, filename: str, content: bytes) -> str:
    """Persist a generated Word document privately alongside the lecture job."""
    if not settings.uploads_bucket:
        raise RuntimeError("CLASSLENS_UPLOADS_BUCKET is required")
    key = f"documents/{job_id}/{document_id}.docx"
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("s3"):
        kwargs["endpoint_url"] = _endpoint("s3")
    boto3.client("s3", **kwargs).put_object(
        Bucket=settings.uploads_bucket, Key=key, Body=content,
        ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ServerSideEncryption="AES256",
        Metadata={"filename": filename[:200], "retention-days": str(settings.retention_days)},
    )
    return key


def fetch_document(key: str) -> bytes:
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("s3"):
        kwargs["endpoint_url"] = _endpoint("s3")
    response = boto3.client("s3", **kwargs).get_object(Bucket=settings.uploads_bucket, Key=key)
    return response["Body"].read()


def save_frames(job_id: str, frames: list[dict]) -> list[dict[str, Any]]:
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("s3"):
        kwargs["endpoint_url"] = _endpoint("s3")
    client = boto3.client("s3", **kwargs)
    saved = []
    for frame in frames:
        key = f"frames/{job_id}/{frame['id']}.jpg"
        client.put_object(Bucket=settings.uploads_bucket, Key=key, Body=frame["bytes"],
                          ContentType="image/jpeg", ServerSideEncryption="AES256")
        saved.append({"id": frame["id"], "timestamp": frame["timestamp"], "object_key": key})
    return saved


def presign_frame(object_key: str) -> str:
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("s3"):
        kwargs["endpoint_url"] = _endpoint("s3")
    return boto3.client("s3", **kwargs).generate_presigned_url(
        "get_object", Params={"Bucket": settings.uploads_bucket, "Key": object_key}, ExpiresIn=900,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"cannot encode {type(value).__name__}")


def _to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_to_dynamo(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_dynamo(item) for key, item in value.items()}
    return value


def send_job(job_id: str, s3_key: str, *, delay_seconds: int = 0,
             transcribe_job_name: str | None = None, transcribe_check: bool = False,
             duration: float | None = None) -> None:
    if not settings.queue_url:
        raise RuntimeError("CLASSLENS_QUEUE_URL is required")
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("sqs"):
        kwargs["endpoint_url"] = _endpoint("sqs")
    body = {"job_id": job_id, "s3_key": s3_key}
    if transcribe_job_name:
        body["transcribe_job_name"] = transcribe_job_name
    if transcribe_check:
        body["transcribe_check"] = True
    if duration is not None:
        body["duration"] = duration
    request = {"QueueUrl": settings.queue_url, "MessageBody": json.dumps(body)}
    if delay_seconds:
        request["DelaySeconds"] = min(max(delay_seconds, 0), 900)
    boto3.client("sqs", **kwargs).send_message(**request)


def upload_video(job_id: str, filename: str, file_obj) -> str:
    if not settings.uploads_bucket:
        raise RuntimeError("CLASSLENS_UPLOADS_BUCKET is required")
    key = f"uploads/{job_id}/{filename}"
    kwargs = {"region_name": settings.aws_region}
    if _endpoint("s3"):
        kwargs["endpoint_url"] = _endpoint("s3")
    boto3.client("s3", **kwargs).upload_fileobj(
        file_obj, settings.uploads_bucket, key,
        ExtraArgs={"ContentType": "video/webm" if filename.endswith(".webm") else "video/mp4",
                   "ServerSideEncryption": "AES256", "Metadata": {"retention-days": str(settings.retention_days)}},
    )
    return key
