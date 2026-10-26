"""Lecture-processing TODO plan and stage-to-task status mapping."""
from __future__ import annotations


def create_lecture_plan(request: str) -> tuple[list[dict[str, str]], dict[str, str]]:
    focus = request.strip() or "general lesson quality"
    tasks = [
        {"id": "prepare", "title": "Validate and prepare the uploaded lecture"},
        {"id": "transcript", "title": "Transcribe speech with timestamps"},
        {"id": "frames", "title": "Extract and deduplicate representative video frames"},
        {"id": "analysis", "title": f"Analyze transcript and frames, focusing on: {focus[:180]}"},
        {"id": "report", "title": "Validate evidence, calculate scores, and publish results"},
    ]
    return tasks, {task["id"]: "pending" for task in tasks}


def task_statuses_for_stage(stage: str, *, failed: bool = False, has_audio: bool = True) -> dict[str, str]:
    """Derive persisted TODO statuses from the current durable pipeline stage."""
    order = ["prepare", "transcript", "frames", "analysis", "report"]
    stage_index = {
        "queued": 0, "downloading": 0, "inspecting_video": 0,
        "transcribing": 1, "sampling_frames": 2, "evaluating": 3,
        "completed": 5, "failed": 0,
    }.get(stage, 0)
    statuses = {}
    for index, task_id in enumerate(order):
        if index < stage_index:
            statuses[task_id] = "completed"
        elif index == stage_index and stage not in {"queued", "completed"}:
            statuses[task_id] = "failed" if failed else "in_progress"
        elif stage == "completed":
            statuses[task_id] = "completed"
        elif failed and index == 0:
            statuses[task_id] = "failed"
        else:
            statuses[task_id] = "pending"
    if not has_audio and stage_index >= 2:
        statuses["transcript"] = "skipped"
    return statuses
