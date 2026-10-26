from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import cv2
import numpy as np

from .config import settings


def inspect_video(path: str) -> tuple[float, bool]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_entries",
         "stream=codec_type", "-of", "json", path], capture_output=True, text=True, check=True, timeout=60,
    )
    info = json.loads(result.stdout)
    duration = float(info.get("format", {}).get("duration", 0))
    if duration <= 0 or duration > settings.max_duration_seconds:
        raise ValueError(f"Video duration must be between 1 second and {settings.max_duration_seconds} seconds")
    return duration, any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))


def sample_frames(path: str, duration: float, output_dir: str) -> list[dict]:
    """Sample with FFmpeg; pHash removes near-identical frames including small presenter motion."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    pattern = os.path.join(output_dir, "sample-%06d.jpg")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-vf",
         f"fps=1/{settings.frame_interval_seconds},scale=1024:-2", "-q:v", "4", pattern],
        check=True, timeout=max(120, int(duration * 2)),
    )
    frames = []
    hashes: list[np.ndarray] = []
    candidates = sorted(Path(output_dir).glob("sample-*.jpg"))
    if len(candidates) > settings.max_frames:
        stride = len(candidates) / settings.max_frames
        candidates = [candidates[int(i * stride)] for i in range(settings.max_frames)]
    for index, image_path in enumerate(candidates):
        image = cv2.imread(str(image_path))
        if image is None:
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        dct = cv2.dct(np.float32(resized))[:8, :8]
        threshold = np.median(dct[1:, 1:])
        current = dct > threshold
        if any(np.count_nonzero(current != old) <= 6 for old in hashes):
            continue
        hashes.append(current)
        sample_number = int(image_path.stem.rsplit("-", 1)[1])
        timestamp = min((sample_number - 1) * settings.frame_interval_seconds, duration)
        frames.append({"id": f"frame_{len(frames) + 1:03d}", "timestamp": timestamp,
                       "path": str(image_path), "bytes": image_path.read_bytes()})
    if not frames and candidates:
        image_path = candidates[0]
        frames.append({"id": "frame_001", "timestamp": 0.0, "path": str(image_path), "bytes": image_path.read_bytes()})
    return frames
