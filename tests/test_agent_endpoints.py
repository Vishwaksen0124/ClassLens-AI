from __future__ import annotations

import io
import json
import zipfile
from email import policy
from email.parser import BytesParser

from docx import Document
from fastapi.testclient import TestClient

from api import main as api


def _multipart_parts(response):
    message = BytesParser(policy=policy.default).parsebytes(
        (f"Content-Type: {response.headers['content-type']}\r\nMIME-Version: 1.0\r\n\r\n".encode())
        + response.content
    )
    return list(message.iter_parts())


def test_agent_returns_execution_manifest_and_valid_docx(monkeypatch):
    plan = {
        "title": "Lecture Study Guide",
        "document_type": "Study Guide",
        "steps": [
            {"id": "summary", "task": "Summarize the lecture request.", "heading": "Summary"},
            {"id": "concepts", "task": "Identify the key concepts.", "heading": "Key Concepts"},
            {"id": "review", "task": "Prepare review questions.", "heading": "Review Questions"},
        ],
    }

    async def make_plan(_request, _context=""):
        return plan, "test-model"

    async def execute_step(_request, step, _index, _context=""):
        return {"paragraphs": [f"Draft for {step['heading']}"], "bullets": []}, "completed"

    monkeypatch.setattr(api, "_make_plan", make_plan)
    monkeypatch.setattr(api, "_execute_step", execute_step)

    with TestClient(api.app) as client:
        response = client.post("/agent", json={"request": "Create a lecture study guide."})

    assert response.status_code == 200
    parts = _multipart_parts(response)
    assert len(parts) == 2
    manifest = json.loads(parts[0].get_payload(decode=True))
    document_bytes = parts[1].get_payload(decode=True)
    assert manifest["status"] == "completed"
    assert len(manifest["plan"]) == 3
    assert [item["status"] for item in manifest["execution"]] == ["completed"] * 3
    assert parts[1].get_filename() == "Lecture_Study_Guide.docx"
    assert zipfile.ZipFile(io.BytesIO(document_bytes)).testzip() is None
    document = Document(io.BytesIO(document_bytes))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Lecture Study Guide" in text
    assert "Agent Execution Record" in text


def test_video_chat_persists_and_reuses_conversation_history(monkeypatch):
    job_id = "chat-job-regression"
    jobs = {job_id: {"job_id": job_id, "status": "completed", "result_key": "result-key",
                     "updated_at": "revision-0", "conversation": []}}
    result = {"summary": "A clear lecture", "transcript_segments": [], "observations": []}
    history_seen = []

    monkeypatch.setattr(api, "fetch_job", lambda _job_id: jobs.get(_job_id))
    monkeypatch.setattr(api, "fetch_result", lambda _key: result)

    def update_job(_job_id, **values):
        jobs[_job_id].update(values)
        jobs[_job_id]["updated_at"] = f"revision-{len(history_seen) + 1}"

    async def answer_question(question, _result, history):
        history_seen.append(list(history))
        return f"Answer to: {question}", "test-model"

    monkeypatch.setattr(api, "update_job", update_job)
    monkeypatch.setattr(api, "answer_video_question", answer_question)

    with TestClient(api.app) as client:
        first = client.post(f"/jobs/{job_id}/chat", json={"message": "What was the key idea?"})
        second = client.post(f"/jobs/{job_id}/chat", json={"message": "Can you explain it?"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert history_seen[0] == []
    assert len(history_seen[1]) == 2
    assert history_seen[1][0] == {"role": "user", "content": "What was the key idea?"}
    assert len(jobs[job_id]["conversation"]) == 4
    assert second.json()["provider"] == "test-model"
