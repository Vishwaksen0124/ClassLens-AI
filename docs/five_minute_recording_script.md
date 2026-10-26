# ClassLens: five-minute recording transcript

**Estimated length:** about five minutes at a clear presentation pace.

## Transcript

Hi, I’m going to walk through ClassLens, a lecture-analysis application that combines video evidence, speech transcription, and an AI-generated assessment. The goal is to help someone review how a lecture was delivered, ask questions grounded in that lecture, and turn the findings into a polished Word document.

The user starts in a Streamlit web interface. They upload an MP4 or WebM video and can describe what they want the analysis to focus on. The frontend sends the request to a FastAPI backend. The API checks the file, places the video in a private S3 bucket, writes a job record and its TODO list to DynamoDB, and sends a message to SQS. The interface can then show the job’s status and progress while the analysis runs asynchronously.

The processing worker is a container-based AWS Lambda function. It takes one queue message at a time. FFmpeg reads the video duration and prepares the audio and frame sampling. OpenCV compares sampled frames with perceptual hashes. If two nearby frames show essentially the same slide, one can be removed. This reduces redundant visual input while preserving meaningful slide changes. It is a practical deduplication heuristic, so a real deployment should still be evaluated on different lecture styles and camera layouts.

For the spoken content, the worker starts an Amazon Transcribe job using the audio in S3. Transcribe writes a timestamped transcript back to S3. When Transcribe finishes, EventBridge signals the application, and the job is queued again so the worker can continue. The worker combines the transcript with selected video frames and sends that evidence to the evaluator. The SQS dead-letter queue provides a place to isolate messages that fail repeatedly.

ClassLens uses one language model throughout: Gemma 4 31B through Amazon Bedrock Mantle. The assessment prompt guides the model to make evidence-based observations from both the transcript and the images. The model can call a Python scoring tool for calculations. That matters because percentages and weighted scores should be calculated deterministically in code, rather than trusted to a language model’s mental arithmetic. The resulting report and intermediate artifacts are stored in S3, while DynamoDB keeps the job status, progress, TODO states, and conversation history.

After analysis, the user can open the report in the frontend and ask questions about the lecture. The chat answer can draw on transcript timestamps, sampled-frame observations, and the recent conversation. This gives the user a way to ask, for example, where a concept was introduced or what evidence supports a particular observation. The API keeps a small in-memory cache for recent context, while DynamoDB remains the durable source for job and conversation state.

There is also a document agent at the FastAPI endpoint called slash agent. The user submits a natural-language request, optionally linked to a completed video job. The agent creates an ordered plan, executes the writing steps in sequence using the same Bedrock Gemma model, and renders a DOCX file. The response includes both the document and a manifest showing the plan and each step’s status. When the request is tied to a lecture, the document can include relevant assessment evidence and conversation context.

For the deployed frontend and API, ClassLens runs Streamlit and FastAPI as containers on Amazon ECS Fargate behind an Application Load Balancer. The data and processing path uses S3, DynamoDB, SQS, Lambda, Amazon Transcribe, EventBridge, and Bedrock Mantle. This separates interactive web requests from longer-running video processing.

One operational limitation to keep in mind is that the current public endpoint uses HTTP and the starter application does not yet provide user sign-in or per-user job authorization. It is suitable for a controlled demonstration, but sensitive lecture recordings need HTTPS and access control before broader use. Also, the frame sampling and AI observations should be reviewed for quality; they are decision support, not a substitute for human judgment.

That’s the ClassLens architecture: submit a lecture, follow its processing status, review evidence-backed results, continue the conversation, and export a structured Word document—all using one configured AI model with code handling the calculations and AWS services handling storage and asynchronous processing. Thanks.
