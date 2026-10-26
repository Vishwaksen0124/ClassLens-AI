# ClassLens AI — Engineering Handoff

Updated: 2026-10-26

## What this repository does

ClassLens accepts lecture video uploads, queues analysis work, transcribes speech, samples and deduplicates video frames, and produces an evidence-based teaching assessment. Users can then chat about the lecture and generate a polished Word document from a request, optionally grounded in the lecture and conversation.

## Current architecture

| Service | Local runtime | Responsibility |
|---|---|---|
| FastAPI API | Docker Compose, port 8000 | Upload and job APIs, DynamoDB/SQS coordination, chat, DOCX generation/download |
| Streamlit UI | Docker Compose, port 8501 | Video upload, job progress/TODOs, assessment, chat, document requests/downloads |
| Polling worker | Docker Compose | Local queue consumer for queued analysis jobs |
| Document agent | FastAPI `/agent` route | Single planner and sequential writer that returns one DOCX and execution manifest |
| AWS services | Deployed stack | Private S3 media/artifacts, DynamoDB job state, SQS plus DLQ, Lambda worker, Amazon Transcribe, EventBridge, Bedrock Mantle |
| Bedrock Mantle | AWS managed model endpoint | Sole LLM for lecture assessment, video chat, and document generation (`google.gemma-4-31b`) |

## Current AWS deployment

- Frontend: <http://classl-Appli-ep5W7Cvux4wL-997264582.us-east-2.elb.amazonaws.com>
- API health: <http://classl-Appli-ep5W7Cvux4wL-997264582.us-east-2.elb.amazonaws.com/health>
- AWS region: `us-east-2`; data/worker stack: `classlens-ai`; web stack: `classlens-ai-web`; ECS cluster/service: `classlens-ai` / `classlens-web`.
- The web stack was recreated on 2026-10-26 using the pinned Gemma-only API and Streamlit images. CloudFormation is `CREATE_COMPLETE`; ECS desired/running/pending counts are 1/1/0. API `/health` and Streamlit `/_stcore/health` both returned HTTP 200.
- The Lambda SQS event source mapping is enabled, reserved concurrency is 1, and the Transcribe EventBridge rule is enabled. The queue had no pending or in-flight messages when processing was restored.
- The data stack preserves the private S3 bucket `classlens-ai-videobucket-refix4txh0t3`, DynamoDB table `classlens-ai-JobsTable-139JIKC6Y3OVX`, and SQS queues. The SAM companion stack and ECR repositories (`classlens-api`, `classlens-ui`, and the worker image repository) remain.
- The configured model is Bedrock Mantle Gemma 4 31B (`google.gemma-4-31b`). A prior `/agent` smoke request returned a valid DOCX; three of five sections used Gemma output and two used deterministic recovery text. No real lecture was submitted during deployment verification.
- The ALB currently uses HTTP because the account had no issued domain certificate. The starter app also has no user sign-in or per-user job authorization; avoid sensitive recordings until HTTPS and access control are configured.

The analysis evaluator is configured for Bedrock Mantle with `google.gemma-4-31b`. The Python scoring tool owns aggregate percentages and weighted scores; the model supplies evidence-based observations and dimension judgments. The full infrastructure and data-flow notes are in [README.md](../README.md).

## Local setup

### Prerequisites

- Docker Engine and Docker Compose v2.
- Python 3.10+ for running the test suite outside containers.
- AWS CLI credentials with access to the deployed ClassLens resources and configured Bedrock Mantle model.
- FFmpeg/ffprobe are packaged in the worker image.
- AWS credentials with access to the deployed resources and Bedrock Mantle Gemma.

### Configure environment

From the repository root:

```bash
cp .env.example .env
```

Edit `.env` with the deployed values:

- `AWS_PROFILE`, `AWS_REGION`
- `CLASSLENS_UPLOADS_BUCKET` — private S3 bucket
- `CLASSLENS_JOBS_TABLE` — DynamoDB jobs table
- `CLASSLENS_QUEUE_URL` — SQS jobs queue URL
- `BEDROCK_MODEL_ID=google.gemma-4-31b`
- `BEDROCK_MANTLE_REGION=us-east-2` (or the region where Mantle access is enabled)
- `LOCAL_UID` and `LOCAL_GID` (`id -u` and `id -g` on Linux)

Do not commit `.env`, AWS credentials, tokens, or uploaded lecture content. The Compose configuration mounts the host AWS profile for local development. The AWS resources must exist before starting the local API and worker; see the deployment commands in [README.md](../README.md#deploy-the-aws-stack).

Set `BEDROCK_MODEL_ID=google.gemma-4-31b` and `BEDROCK_MANTLE_REGION=us-east-2`. This is the only model used by ClassLens.

### Start and inspect containers

```bash
docker compose up --build -d
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8501/_stcore/health
```

Open <http://localhost:8501> for the Streamlit interface and <http://localhost:8000/docs> for the FastAPI docs. Follow logs with:

```bash
docker compose logs -f api worker frontend
```

Stop local services with `docker compose down`. This stops the local containers; it does not delete cloud resources or stored S3 artifacts.

## Test and smoke-check

Create an isolated virtual environment and run the suite:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
pytest -q
```

The API image dependencies include the OpenAI SDK, Bedrock token helper, and tolerant JSON repair for Gemma outputs. The public `/agent` smoke check returned a valid DOCX and confirmed Gemma was the planner provider. Two section responses used deterministic recovery text, so review the generated content and improve response normalization if fully model-authored sections are required. No real lecture was processed during deployment verification.

Useful operational checks:

```bash
docker compose ps
docker compose logs --tail=100 api worker
curl -sS http://localhost:8000/health
curl -sS http://localhost:8501/_stcore/health
```

## API surface

- `POST /jobs` — multipart `file` upload and optional analysis `request`; returns a queued job ID.
- `GET /jobs/{job_id}` — job lifecycle, TODO statuses, saved conversation, and assessment when complete.
- `POST /jobs/{job_id}/chat` — asks a question grounded in completed lecture evidence and saved conversation.
- `POST /agent` — JSON `{ "request": "...", "job_id": "optional-completed-job-id" }`; returns a multipart response with execution-plan manifest and `.docx` attachment.
- `GET /jobs/{job_id}/documents/{document_id}` — downloads a DOCX persisted for a lecture job.
- `GET /health` — API health endpoint.

There is one ClassLens agent flow, exposed through `/agent` in `api/main.py`; `src/classlens/document_agent.py` contains its planner, step executor, fallback, and DOCX renderer. The same Bedrock Mantle Gemma model serves this flow, video chat, and video assessment.

## Processing and state notes

1. API validates the upload, writes it to private S3, records the job and TODO plan in DynamoDB, and enqueues the job ID in SQS.
2. The local Compose worker polls SQS for development. The deployed AWS stack uses a container-image Lambda consumer with reserved concurrency one and a DLQ.
3. FFmpeg reads video duration and extracts audio; Amazon Transcribe produces timestamped transcript data. EventBridge signals completion, with queue polling/recovery handling for delayed/missed completion events.
4. FFmpeg/OpenCV sampling chooses timestamped frames and removes perceptual near-duplicates before multimodal evaluation.
5. The evaluator follows `prompts/classlens_evaluation_prompt.md`; Python validates evidence and calculates aggregate scores.
6. Reports/transcripts/documents are stored as S3 objects; job state and bounded conversation history are in DynamoDB. The API process has a bounded cache for recently used assessment context, while DynamoDB remains authoritative.

The audio/verbal-communication rubric dimension is `null` when only transcript and image evidence are available. Lambda's 15-minute execution ceiling may require moving the consumer to ECS for unusually long videos. See the README for security and deployment constraints.

## Known boundaries and follow-up work

- The starter service does not implement user authentication or per-user job ownership. Keep it behind trusted access controls until those checks are added.
- A successful container health check only proves the local processes are responsive. AWS permissions, resource names, model availability, S3 access, and a real video are needed for a full cloud-backed analysis.
- Model output quality and frame sampling are heuristic. Review evidence references and score calibration before using reports for consequential decisions.
- The transcript and frame set are intentionally bounded for model payload size and cost. Long recordings may have sparse visual evidence.
- For deployment, provision AWS resources with SAM, then deploy API and Streamlit images with the ECS template. Streamlit is hosted on Fargate; Amplify Hosting does not run a Streamlit server process.

## Key implementation files

- `api/main.py` — FastAPI routes and job/document orchestration.
- `worker/handler.py`, `worker/local_runner.py` — Lambda entry point and local queue runner.
- `src/classlens/pipeline.py` — processing lifecycle orchestration.
- `src/classlens/media.py`, `transcription.py`, `evaluator.py`, `scoring.py` — media, transcription, AI evaluation, and deterministic scoring.
- `src/classlens/chat.py`, `document_agent.py`, `workflow.py` — lecture chat, plan/execute document workflow, and TODO state.
- `app.py` — Streamlit frontend.
- `infrastructure/template.yaml`, `infrastructure/ecs-web.yaml` — AWS worker/data stack and web services.
- `tests/` — API endpoint and job lifecycle coverage.
