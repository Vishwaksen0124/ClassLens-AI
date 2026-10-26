# Video Chat Agent and Word Document Workflow

ClassLens supports an autonomous workflow around each uploaded lecture: the API creates a TODO plan, the existing SQS worker executes video analysis, the user chats about transcript and visual assessment evidence, and `/agent` can produce a polished Microsoft Word document grounded in the lecture and saved conversation.

## User workflow

1. Upload an MP4 or WebM in Streamlit and optionally describe what to focus on.
2. The API stores a plan and initial task statuses with the DynamoDB job, uploads the video to private S3, and sends its ID to SQS.
3. The worker inspects the video, transcribes speech, samples and deduplicates frames, analyzes transcript and frames, calculates scores in Python, and saves the report. Worker stage updates change the stored TODO statuses.
4. Streamlit shows the plan and progress alongside the evidence-based assessment.
5. The user asks follow-up questions. The agent receives the conversation, timestamped transcript, assessment observations, strengths, recommendations, and sampled-frame timestamps. It cites evidence IDs/timestamps where possible and should say when the evidence is insufficient.
6. The user requests a study guide, report, project plan, meeting minutes, SOP, or similar business document. The agent plans 4–8 writing tasks, executes them in order, and returns the `.docx` with the plan and task statuses.

## API

### Upload a lecture

`POST /jobs` accepts multipart fields `file` (MP4/WebM) and optional `request` (the analysis focus). It returns a queued job ID. `GET /jobs/{job_id}` returns state, stage, progress, `task_plan`, `task_statuses`, recent `conversation`, and the completed assessment.

```bash
curl -F 'file=@lecture.mp4' \
  -F 'request=Focus on how the instructor explains difficult concepts' \
  http://localhost:8000/jobs
```

### Ask about the lecture

`POST /jobs/{job_id}/chat` accepts JSON:

```json
{"message":"What examples did the instructor use to explain the main idea?"}
```

The response includes an `answer`, the `provider`, and the saved `conversation` array. Chat is available after analysis completes. Conversation turns are persisted in DynamoDB, bounded to the most recent 24 messages. The API keeps a bounded process-local cache of assessment context and invalidates it when the DynamoDB job revision changes. DynamoDB remains the source of truth.

```bash
curl -X POST http://localhost:8000/jobs/<job-id>/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What examples did the instructor use?"}'
```

### Generate a Word document

`POST /agent` accepts JSON with a natural-language `request` and optional completed lecture `job_id`:

```json
{"request":"Create a study guide with key concepts, lecture examples, and review questions.","job_id":"<completed-job-id>"}
```

Without `job_id`, the agent creates a general document from the request. With `job_id`, it uses the lecture summary, transcript, evidence-linked observations, score rationales, and saved conversation as source material.

The response is `multipart/mixed`: one `application/json` part contains the execution manifest, and the other part is an `application/vnd.openxmlformats-officedocument.wordprocessingml.document` attachment. When `job_id` is supplied, the Word file is also saved privately in S3 and listed on `GET /jobs/{job_id}`. Download it later from the returned `download_path`, which maps to `GET /jobs/{job_id}/documents/{document_id}`. Documents follow the configured S3 retention policy.

```bash
curl -X POST http://localhost:8000/agent \
  -H 'Content-Type: application/json' \
  -d '{"request":"Create a study guide with key concepts, examples, and review questions.","job_id":"<completed-job-id>"}' \
  -o lecture-document.multipart
```

## Model

Lecture assessment, video chat, and document planning and writing use the same Bedrock Mantle model: `google.gemma-4-31b`. Set `BEDROCK_MODEL_ID` and `BEDROCK_MANTLE_REGION` in `.env`, and provide AWS credentials with `bedrock-mantle:CreateInference` and `bedrock-mantle:CallWithBearerToken` permission. On a provider error, chat returns an evidence lookup and document generation uses a deterministic template; the manifest labels fallback execution.

## Engineering improvement: multi-step planning

The required engineering improvement is **multi-step planning**. I chose it because video analysis and document creation naturally break into steps that can be tracked and reviewed. Each video job stores a TODO plan and task-status map in DynamoDB; worker stage transitions update that map, and the frontend shows progress. For document generation, the language model derives an ordered plan of 4–8 writing tasks, executes each task sequentially, and returns per-step statuses with the Word file. This exposes agent decisions, makes execution traceable, and shows where work failed or used a fallback. A local template planner keeps document generation available without paid model access.

Conversation memory is also supported: up to 24 messages persist with the job; the latest four messages are sent with a chat question and up to four recent messages are included in document context. A bounded cache avoids repeated S3 result reads while DynamoDB remains authoritative. Request validation limits message and document prompt sizes, and Bedrock failures fall back to evidence lookup or deterministic document templates with the fallback provider stated in the response.
