"""Shared Bedrock Mantle client for all ClassLens text and vision reasoning."""
from __future__ import annotations

from aws_bedrock_token_generator import provide_token
from openai import AsyncOpenAI

from .config import settings


def _client() -> AsyncOpenAI:
    if not settings.bedrock_model_id:
        raise RuntimeError("BEDROCK_MODEL_ID is required")
    region = settings.bedrock_mantle_region
    return AsyncOpenAI(
        api_key=provide_token(region=region),
        base_url=f"https://bedrock-mantle.{region}.api.aws/openai/v1",
        timeout=180.0,
    )


async def generate_text(prompt: str, *, system: str = "", json_mode: bool = False) -> str:
    """Generate text using the one configured Bedrock Mantle model."""
    content = []
    if system:
        content.append({"role": "system", "content": system})
    content.append({"role": "user", "content": prompt})
    params = {"model": settings.bedrock_model_id, "input": content}
    if json_mode:
        params["text"] = {"format": {"type": "json_object"}}
    async with _client() as client:
        response = await client.responses.create(**params)
        return response.output_text
