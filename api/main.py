from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from classlens.chat import answer_video_question, cache_conversation, cached_context
from classlens.config import settings
from classlens.document_agent import _execute_step, _make_plan, _multipart, _render_docx
from classlens.storage import (create_job, fetch_document, fetch_job, fetch_result, presign_frame,
                               save_document, send_job, update_job, upload_video)
from classlens.workflow import create_lecture_plan

app = FastAPI(title="ClassLens API", version="0.1.0")
origins = [item.strip() for item in settings.allowed_origins.split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=["GET", "POST"], allow_headers=["*"])
ALLOWED_EXTENSIONS = {".mp4", ".webm"}


class ChatMessage(BaseModel):
    message: str = Field(min_length=2, max_length=2500)

    @field_validator("message")
    @classmethod
    def message_has_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Message must contain at least two non-whitespace characters.")
        return value


class AgentDocumentRequest(BaseModel):
    request: str = Field(min_length=8, max_length=12000)
    job_id: str | None = None

    @field_validator("request")
    @classmethod
    def request_has_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("Request must contain at least eight non-whitespace characters.")
        return value


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", status_code=202)
async def submit_job(file: UploadFile = File(...), request: str = Form(default="")) -> dict:
    original_name = PurePath(file.filename or "lesson.mp4").name
    extension = PurePath(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Upload an MP4 or WebM video.")
    limit = settings.max_upload_mb * 1024 * 1024
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size == 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if size > limit:
        raise HTTPException(status_code=413, detail=f"Maximum video size is {settings.max_upload_mb} MB.")

    job_id = str(uuid4())
    now = datetime.now(timezone.utc)
    key = f"uploads/{job_id}/lecture{extension}"
    task_plan, task_statuses = create_lecture_plan(request)
    job = {"job_id": job_id, "status": "queued", "stage": "queued", "progress": 0,
           "created_at": now.isoformat(), "updated_at": now.isoformat(),
           "expires_at": int((now + timedelta(days=settings.retention_days)).timestamp()),
           "s3_key": key, "original_filename": original_name,
           "request": request.strip()[:2000], "task_plan": task_plan,
           "task_statuses": task_statuses, "conversation": []}
    job_saved = False
    try:
        stored_key = await run_in_threadpool(upload_video, job_id, f"lecture{extension}", file.file)
        job["s3_key"] = stored_key
        create_job(job)
        job_saved = True
        send_job(job_id, stored_key)
    except Exception as exc:
        # Do not expose AWS errors, bucket names, object keys, or signed URLs to clients.
        if job_saved:
            try:
                update_job(job_id, status="failed", stage="submission_failed", error="Could not submit this video for processing.")
            except Exception:
                pass
        raise HTTPException(status_code=503, detail="Could not submit this video right now. Please try again.") from exc
    return {"job_id": job_id, "status": "queued", "stage": "queued", "progress": 0}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    try:
        job = fetch_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Job status is temporarily unavailable.") from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    response = {key: job.get(key) for key in ("job_id", "status", "stage", "progress", "created_at", "updated_at", "error",
                                              "request", "task_plan", "task_statuses", "conversation")}
    response["documents"] = [{"document_id": item.get("document_id"),
                               "filename": item.get("filename"),
                               "created_at": item.get("created_at"),
                               "download_path": f"/jobs/{job_id}/documents/{item.get('document_id')}"}
                              for item in job.get("documents", [])]
    if job.get("status") == "completed" and job.get("result_key"):
        try:
            response["result"] = fetch_result(job["result_key"])
            for frame in response["result"].get("sampled_frames", []):
                frame["image_url"] = presign_frame(frame["object_key"])
        except Exception as exc:
            raise HTTPException(status_code=503, detail="The completed assessment is temporarily unavailable.") from exc
    return response


@app.post("/jobs/{job_id}/chat")
async def chat_about_video(job_id: str, payload: ChatMessage) -> dict:
    try:
        job = fetch_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Job state is temporarily unavailable.") from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("status") != "completed" or not job.get("result_key"):
        raise HTTPException(status_code=409, detail="Chat is available after video analysis is complete.")
    try:
        context = cached_context(job_id, job, fetch_result)
        history = context["conversation"]
        answer, provider = await answer_video_question(payload.message, context["result"], history)
        conversation = (history + [
            {"role": "user", "content": payload.message},
            {"role": "assistant", "content": answer},
        ])[-24:]
        update_job(job_id, conversation=conversation)
        updated = fetch_job(job_id) or job
        cache_conversation(job_id, str(updated.get("updated_at", "")), context["result"], conversation)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not answer from the lecture evidence right now.") from exc
    return {"job_id": job_id, "answer": answer, "provider": provider, "conversation": conversation}


@app.post("/agent")
async def create_document(payload: AgentDocumentRequest) -> Response:
    source_context = ""
    conversation: list[dict] = []
    if payload.job_id:
        try:
            job = fetch_job(payload.job_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Job state is temporarily unavailable.") from exc
        if job is None:
            raise HTTPException(status_code=404, detail="Video job not found.")
        if job.get("status") != "completed" or not job.get("result_key"):
            raise HTTPException(status_code=409, detail="The lecture must finish processing before creating its document.")
        try:
            result = fetch_result(job["result_key"])
            conversation = job.get("conversation", [])
            source_context = _document_context(result, conversation)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Lecture evidence is temporarily unavailable.") from exc

    plan, provider = await _make_plan(payload.request, source_context)
    sections = []
    execution = []
    for index, step in enumerate(plan["steps"]):
        content, status = await _execute_step(payload.request, step, index, source_context)
        sections.append({"heading": step["heading"], "task": step["task"],
                         "status": status, "content": content})
        execution.append({"id": step["id"], "heading": step["heading"], "status": status})
    document = _render_docx(plan, sections, provider, source_context)
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", str(plan.get("title", "lecture_document"))).strip("_.")[:70] or "lecture_document"
    document_id = str(uuid4())
    download_path = None
    if payload.job_id:
        try:
            object_key = save_document(payload.job_id, document_id, f"{filename}.docx", document)
            document_record = {"document_id": document_id, "filename": f"{filename}.docx",
                               "object_key": object_key,
                               "created_at": datetime.now(timezone.utc).isoformat()}
            update_job(payload.job_id, documents=(job.get("documents", []) + [document_record])[-10:])
            download_path = f"/jobs/{payload.job_id}/documents/{document_id}"
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Could not save the generated document.") from exc
    manifest = {"job_id": str(uuid4()), "status": "completed",
        "message": "The request was planned and executed; the Word document is attached.",
        "source_video_job_id": payload.job_id,
        "document_id": document_id, "download_path": download_path,
        "document": {"filename": f"{filename}.docx",
                     "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        "provider": provider,
        "conversation_messages_used": min(len(conversation), 4),
        "plan": [{key: step[key] for key in ("id", "task", "heading")} for step in plan["steps"]],
        "execution": execution}
    return _multipart(manifest, document, f"{filename}.docx")


@app.get("/jobs/{job_id}/documents/{document_id}")
def download_document(job_id: str, document_id: str) -> Response:
    try:
        job = fetch_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Job state is temporarily unavailable.") from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    record = next((item for item in job.get("documents", [])
                   if item.get("document_id") == document_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    try:
        content = fetch_document(record["object_key"])
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Document is temporarily unavailable.") from exc
    return Response(content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{record["filename"]}"'})


def _document_context(result: dict, conversation: list[dict]) -> str:
    lines = ["Summary:", str(result.get("summary", "No summary available."))[:600]]
    lines.append("Overall result:")
    lines.append(f"- Score: {result.get('overall_score')} / 100; rating: {result.get('rating')}; "
                 f"evidence coverage: {result.get('evidence_coverage_percent', 0)}%.")
    lines.append("Dimension scores:")
    for item in result.get("dimensions", []):
        lines.append(f"- {item.get('name')}: {item.get('score')}/5. {str(item.get('rationale', ''))[:180]}")
    lines.append("Evidence observations:")
    for item in result.get("observations", [])[:5]:
        lines.append(f"- {item.get('timestamp_start', 0):.1f}s–{item.get('timestamp_end', 0):.1f}s, "
                     f"{item.get('dimension')}, {item.get('type')}: {str(item.get('observation', ''))[:180]} "
                     f"Evidence: {', '.join(item.get('evidence_refs', []))}. "
                     f"Recommendation: {str(item.get('recommendation', ''))[:100]}")
    lines.append("Strengths:")
    for item in result.get("strengths", [])[:3]:
        lines.append(f"- {item.get('title')}: {str(item.get('explanation', ''))[:180]} "
                     f"Evidence: {', '.join(item.get('evidence_refs', []))}")
    lines.append("Priority improvements:")
    for item in result.get("priority_improvements", [])[:3]:
        lines.append(f"- {item.get('priority')}: {item.get('title')}. {str(item.get('recommendation', ''))[:140]} "
                     f"Evidence: {', '.join(item.get('evidence_refs', []))}")
    lines.append("Assessment limitations:")
    for limitation in result.get("limitations", [])[:5]:
        lines.append(f"- {str(limitation)[:160]}")
    lines.append("Sampled video frames:")
    for frame in result.get("sampled_frames", [])[:12]:
        lines.append(f"- {frame.get('id')} at {float(frame.get('timestamp', 0)):.1f}s")
    transcript = result.get("transcript_segments", [])
    if len(transcript) > 12:
        stride = max(1, len(transcript) // 12)
        transcript = transcript[::stride][:12]
    transcript = [{**segment, "text": str(segment.get("text", ""))[:120]} for segment in transcript]
    lines.append("Timestamped transcript:")
    for segment in transcript:
        lines.append(f"- {segment.get('id')} ({segment.get('start', 0):.1f}s–{segment.get('end', 0):.1f}s): "
                     f"{str(segment.get('text', ''))[:240]}")
    if conversation:
        lines.append("Conversation history:")
        for item in conversation[-4:]:
            role = "User" if item.get("role") == "user" else "Agent"
            lines.append(f"- {role}: {str(item.get('content', ''))[:350]}")
    return "\n".join(lines)[:10000]
