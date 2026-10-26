from __future__ import annotations

import json

from fastapi.testclient import TestClient

from api import main as api
from classlens import pipeline
from classlens.scoring import DIMENSION_WEIGHTS
from worker import handler as worker


def test_video_upload_runs_through_worker_and_report_api(monkeypatch):
    """Exercise upload -> queued message -> worker -> completed report retrieval."""
    jobs: dict[str, dict] = {}
    results: dict[str, dict] = {}
    queued_messages: list[dict] = []

    def upload_video(job_id, filename, _file_obj):
        return f"uploads/{job_id}/{filename}"

    def create_job(job):
        jobs[job["job_id"]] = job.copy()

    def queue_job(job_id, s3_key, **kwargs):
        queued_messages.append({"job_id": job_id, "s3_key": s3_key, **kwargs})

    monkeypatch.setattr(api, "upload_video", upload_video)
    monkeypatch.setattr(api, "create_job", create_job)
    monkeypatch.setattr(api, "send_job", queue_job)
    monkeypatch.setattr(api, "fetch_job", lambda job_id: jobs.get(job_id))
    monkeypatch.setattr(api, "fetch_result", lambda result_key: results[result_key])
    monkeypatch.setattr(api, "presign_frame", lambda object_key: f"https://example.test/{object_key}")

    monkeypatch.setattr(pipeline, "fetch_job", lambda job_id: jobs.get(job_id))

    def update_job(job_id, **values):
        jobs[job_id].update(values)

    monkeypatch.setattr(pipeline, "update_job", update_job)

    class FakeS3:
        @staticmethod
        def download_file(_bucket, _key, path):
            with open(path, "wb") as video:
                video.write(b"synthetic video bytes")

    monkeypatch.setattr(pipeline, "_s3_client", lambda: FakeS3())
    monkeypatch.setattr(pipeline, "inspect_video", lambda _path: (42.0, False))
    monkeypatch.setattr(
        pipeline,
        "sample_frames",
        lambda _video, _duration, _directory: [
            {"id": "frame_001", "timestamp": 8.0, "bytes": b"jpeg"}
        ],
    )

    def evaluate(duration, transcript, frames):
        assert duration == 42.0
        assert transcript == []
        assert len(frames) == 1
        return {
            "assessment_status": "completed",
            "overall_score": 75.0,
            "rating": "Good",
            "evidence_coverage_percent": 80.0,
            "summary": "A clear sample assessment.",
            "dimensions": [
                {"name": name, "score": 4, "rationale": "Observed in the lesson.", "limitations": []}
                for name in DIMENSION_WEIGHTS
            ],
            "observations": [],
            "strengths": [],
            "priority_improvements": [],
            "limitations": [],
            "dimension_percentages": {name: 75.0 for name in DIMENSION_WEIGHTS},
        }

    monkeypatch.setattr(pipeline, "evaluate", evaluate)
    monkeypatch.setattr(
        pipeline,
        "save_frames",
        lambda job_id, _frames: [
            {"id": "frame_001", "timestamp": 8.0, "object_key": f"frames/{job_id}/frame_001.jpg"}
        ],
    )

    def save_result(job_id, report):
        key = f"results/{job_id}/assessment.json"
        results[key] = report.copy()
        return key

    monkeypatch.setattr(pipeline, "save_result", save_result)

    with TestClient(api.app) as client:
        response = client.post(
            "/jobs",
            files={"file": ("lesson.mp4", b"synthetic video", "video/mp4")},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert response.json()["status"] == "queued"
        assert jobs[job_id]["status"] == "queued"
        assert len(queued_messages) == 1

        message = queued_messages.pop()
        worker_response = worker.handler(
            {
                "Records": [
                    {"messageId": "message-1", "body": json.dumps(message)}
                ]
            },
            None,
        )
        assert worker_response == {"batchItemFailures": []}
        assert jobs[job_id]["status"] == "completed"

        report_response = client.get(f"/jobs/{job_id}")
        assert report_response.status_code == 200
        report = report_response.json()["result"]
        assert report["recording_duration_seconds"] == 42.0
        assert report["sampled_frame_count"] == 1
        assert report["dimensions"][3]["score"] is None  # transcript cannot assess audio
        assert report["sampled_frames"][0]["image_url"].startswith("https://example.test/")
