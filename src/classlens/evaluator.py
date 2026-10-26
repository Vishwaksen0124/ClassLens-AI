from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from aws_bedrock_token_generator import provide_token
from openai import OpenAI
from pydantic import ValidationError
from strands import Agent, tool
from strands.models.openai_responses import OpenAIResponsesModel

from .config import settings
from .schemas import Assessment
from .scoring import DIMENSION_WEIGHTS, calculate_classlens_score as score_with_python


@tool
def calculate_classlens_score(dimension_scores: dict[str, int | None]) -> str:
    """Calculate the authoritative weighted ClassLens score from five dimension scores.

    Args:
        dimension_scores: All five exact rubric dimension names and integer scores 1-5 or null.
    """
    return json.dumps(score_with_python(dimension_scores))


def list_mantle_models() -> list[str]:
    """Return model IDs visible to the worker role in Bedrock Mantle."""
    region = settings.bedrock_mantle_region
    client = OpenAI(
        api_key=provide_token(region=region),
        # Mantle lists models on /v1/models even when a model is served on /openai/v1.
        base_url=f"https://bedrock-mantle.{region}.api.aws/v1",
    )
    return sorted(model.id for model in client.models.list().data)


def evaluate(duration: float, transcript: list[dict], frames: list[dict]) -> dict[str, Any]:
    system_prompt = _system_prompt()
    if not settings.bedrock_model_id:
        raise RuntimeError("BEDROCK_MODEL_ID is required")
    model = OpenAIResponsesModel(
        model_id=settings.bedrock_model_id,
        bedrock_mantle_config={"region": settings.bedrock_mantle_region},
    )
    agent = Agent(model=model, tools=[calculate_classlens_score], system_prompt=system_prompt)
    analyzed_frames = _frames_within_mantle_limit(duration, transcript, frames)
    content = [{"text": json.dumps({
        "recording_duration_seconds": duration,
        "transcript_segments": transcript,
        "video_frames": [{"id": f["id"], "timestamp": f["timestamp"]} for f in analyzed_frames],
    }, ensure_ascii=False)}]
    for frame in analyzed_frames:
        content.extend([
            {"text": f"Attached image is {frame['id']} at {frame['timestamp']:.2f} seconds."},
            {"image": {"format": "jpeg", "source": {"bytes": frame["bytes"]}}},
        ])
    response = agent(content)
    message = getattr(response, "message", None)
    if isinstance(message, dict):
        response_text = "\n".join(block.get("text", "") for block in message.get("content", []) if isinstance(block, dict))
    else:
        response_text = str(response)
    payload = _parse_json(response_text)
    try:
        report = Assessment.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("Evaluator returned a report that does not match the required schema") from exc
    _validate_references(report.model_dump(), transcript, analyzed_frames, duration)
    scores = {dimension.name: dimension.score for dimension in report.dimensions}
    report_data = report.model_dump()
    # Always replace model-provided aggregate values with the deterministic Python result.
    report_data.update(score_with_python(scores))
    return report_data


def _frames_within_mantle_limit(duration: float, transcript: list[dict], frames: list[dict]) -> list[dict]:
    """Keep a uniform temporal sample under Gemma 4 Mantle's 3.5 MB request cap."""
    if not frames:
        return []
    max_request_bytes = 3_000_000  # headroom for prompt, tool schema, and API framing

    def estimate(selected: list[dict]) -> int:
        metadata = json.dumps({
            "recording_duration_seconds": duration,
            "transcript_segments": transcript,
            "video_frames": [{"id": f["id"], "timestamp": f["timestamp"]} for f in selected],
        }, ensure_ascii=False).encode("utf-8")
        # OpenAI image inputs are base64 encoded; account for that expansion and JSON overhead.
        image_payload_bytes = sum(4 * ((len(frame["bytes"]) + 2) // 3) + 256 for frame in selected)
        return len(metadata) + image_payload_bytes + 64_000

    for count in range(len(frames), 0, -1):
        if count == len(frames):
            selected = frames
        else:
            indices = sorted({round(i * (len(frames) - 1) / max(count - 1, 1)) for i in range(count)})
            if len(indices) < count:
                indices = list(range(count))
            selected = [frames[index] for index in indices]
        if estimate(selected) <= max_request_bytes:
            return selected
    raise ValueError("Transcript and one video frame exceed Gemma 4 Mantle's request size limit")


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """Cache stable evaluator instructions across warm worker invocations."""
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "classlens_evaluation_prompt.md"
    return prompt_path.read_text(encoding="utf-8")


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("Evaluator response did not contain a JSON object")
    payload, _ = decoder.raw_decode(text[start:])
    if not isinstance(payload, dict):
        raise ValueError("Evaluator response must be a JSON object")
    return payload


def _validate_references(report: dict, transcript: list[dict], frames: list[dict], duration: float) -> None:
    valid_ids = {item["id"] for item in transcript} | {item["id"] for item in frames}
    evidence_bounds = {item["id"]: (item["start"], item["end"]) for item in transcript}
    evidence_bounds.update({item["id"]: (item["timestamp"], item["timestamp"]) for item in frames})
    ref_groups = [report["observations"], report["strengths"], report["priority_improvements"]]
    for group in ref_groups:
        for item in group:
            refs = item.get("evidence_refs", [])
            if not refs or any(ref not in valid_ids for ref in refs):
                raise ValueError("Evaluator returned a missing or unknown evidence reference")
    for observation in report["observations"]:
        if observation["timestamp_end"] > duration or observation["timestamp_start"] > observation["timestamp_end"]:
            raise ValueError("Evaluator returned an observation timestamp outside the recording")
        if observation["dimension"] not in DIMENSION_WEIGHTS:
            raise ValueError("Evaluator returned an unknown observation dimension")
        if not any(evidence_bounds[ref][0] <= observation["timestamp_end"]
                   and evidence_bounds[ref][1] >= observation["timestamp_start"]
                   for ref in observation["evidence_refs"]):
            raise ValueError("Evaluator observation time does not overlap its cited evidence")
