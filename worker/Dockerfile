FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src:/app PIP_DEFAULT_TIMEOUT=120
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-local.txt ./
RUN pip install --no-cache-dir -r requirements-local.txt
COPY src ./src
COPY worker ./worker
COPY prompts ./prompts
CMD ["python", "-m", "worker.local_runner"]
