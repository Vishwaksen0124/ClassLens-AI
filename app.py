from __future__ import annotations

import json
import os
import time
from email import policy
from email.parser import BytesParser

import requests
import streamlit as st

API_URL = os.getenv("CLASSLENS_API_URL", "http://localhost:8000").rstrip("/")
PUBLIC_API_URL = os.getenv("CLASSLENS_PUBLIC_API_URL", API_URL).rstrip("/")

st.set_page_config(page_title="ClassLens AI", page_icon="🎓", layout="wide")
st.title("ClassLens AI")
st.caption("Evidence-based analysis of recorded lessons")
st.info("Videos and derived reports are retained for a limited time. Upload only recordings you are authorized to process.")


def render_report(report: dict) -> None:
    status = report.get("assessment_status", "completed")
    if report.get("overall_score") is None:
        st.warning("Insufficient evidence for an overall score")
    else:
        score_col, rating_col, coverage_col = st.columns(3)
        score_col.metric("Overall score", f"{report['overall_score']:.1f} / 100")
        rating_col.metric("Rating", report.get("rating", "—"))
        coverage_col.metric("Evidence coverage", f"{report.get('evidence_coverage_percent', 0):.0f}%")
    st.subheader("Summary")
    st.write(report.get("summary", "No summary was returned."))

    st.subheader("Dimension scores")
    for dimension in report.get("dimensions", []):
        score = dimension.get("score")
        label = "Insufficient evidence" if score is None else f"{score} / 5"
        with st.expander(f"{dimension['name']} · {label}", expanded=True):
            st.write(dimension.get("rationale", ""))
            if dimension.get("limitations"):
                st.caption("Limitations: " + " ".join(dimension["limitations"]))

    st.subheader("Evidence observations")
    observations = report.get("observations", [])
    if not observations:
        st.caption("No evidence-linked observations were returned.")
    for item in observations:
        ts = f"{item['timestamp_start']:.1f}s–{item['timestamp_end']:.1f}s"
        st.markdown(f"**{ts} · {item['dimension']} · {item['type'].title()}**")
        st.write(item["observation"])
        st.caption(f"Evidence: {', '.join(item['evidence_refs'])} · {item['impact']}")
        if item.get("recommendation"):
            st.write("Next step: " + item["recommendation"])

    left, right = st.columns(2)
    with left:
        st.subheader("Strengths")
        for item in report.get("strengths", []):
            st.markdown(f"**{item['title']}** — {item['explanation']}")
            st.caption("Evidence: " + ", ".join(item["evidence_refs"]))
    with right:
        st.subheader("Priority improvements")
        for item in report.get("priority_improvements", []):
            st.markdown(f"**{item['priority'].title()}: {item['title']}** — {item['explanation']}")
            st.write(item["recommendation"])
            st.caption("Evidence: " + ", ".join(item["evidence_refs"]))

    if report.get("limitations"):
        st.subheader("Assessment limitations")
        for limitation in report["limitations"]:
            st.markdown(f"- {limitation}")
    sampled_frames = report.get("sampled_frames", [])
    if sampled_frames:
        st.subheader("Sampled lesson frames")
        selected_frame = st.selectbox(
            "Choose a frame cited by the assessment",
            sampled_frames,
            format_func=lambda frame: f"{frame['id']} · {frame['timestamp']:.1f}s",
        )
        st.image(selected_frame["image_url"], caption=f"{selected_frame['id']} at {selected_frame['timestamp']:.1f} seconds", use_container_width=True)
    with st.expander("Transcript segments"):
        for segment in report.get("transcript_segments", []):
            st.markdown(f"**{segment['id']} · {segment['start']:.1f}s–{segment['end']:.1f}s** {segment['text']}")


def render_todos(job: dict) -> None:
    tasks = job.get("task_plan", [])
    statuses = job.get("task_statuses", {})
    if not tasks:
        return
    with st.expander("Agent TODO list", expanded=job.get("status") not in {"completed", "failed"}):
        for task in tasks:
            status = statuses.get(task["id"], "pending")
            icon = {"completed": "✅", "in_progress": "🔄", "pending": "⬜",
                    "skipped": "⏭️", "failed": "❌", "queued": "⬜"}.get(status, "⬜")
            st.markdown(f"{icon} **{task['title']}** · {status.replace('_', ' ')}")


with st.sidebar:
    st.header("Submit a lesson")
    analysis_request = st.text_area(
        "What should the agent focus on?",
        placeholder="For example: assess how clearly the instructor explains the key concepts.",
        height=100,
    )
    video = st.file_uploader("Video file", type=["mp4", "webm"], accept_multiple_files=False)
    submit = st.button("Queue video for analysis", type="primary", disabled=video is None, use_container_width=True)

if submit and video is not None:
    try:
        with st.spinner("Uploading video and creating the queued job…"):
            response = requests.post(f"{API_URL}/jobs",
                files={"file": (video.name, video.getvalue(), video.type)},
                data={"request": analysis_request}, timeout=(10, 1800))
        response.raise_for_status()
        st.session_state["classlens_job_id"] = response.json()["job_id"]
    except requests.RequestException:
        st.error("The video could not be submitted. Check that the API is running and try again.")

job_id = st.session_state.get("classlens_job_id")
current_job = None
if job_id:
    st.subheader(f"Job `{job_id}`")
    if st.button("Clear this job"):
        del st.session_state["classlens_job_id"]
        st.rerun()
    placeholder = st.empty()
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"{API_URL}/jobs/{job_id}", timeout=15)
            response.raise_for_status()
            job = response.json()
        except requests.RequestException:
            placeholder.error("Could not retrieve the job state. Retry in a moment.")
            break
        with placeholder.container():
            state = job.get("status", "unknown")
            stage = (job.get("stage") or state).replace("_", " ").title()
            st.progress(max(0, min(100, int(job.get("progress") or 0))), text=f"{state.title()} · {stage}")
            render_todos(job)
            if state == "failed":
                st.error(job.get("error") or "The assessment could not be completed.")
            elif state == "completed":
                render_report(job.get("result") or {})
                current_job = job
        if state in {"completed", "failed"}:
            break
        time.sleep(4)
    else:
        st.warning("The job is still running. Refresh this page to check its latest state.")

if current_job:
    saved_documents = current_job.get("documents", [])
    if saved_documents:
        st.subheader("Saved Word documents")
        for saved in saved_documents:
            st.link_button(f"Download {saved['filename']}", f"{PUBLIC_API_URL}{saved['download_path']}")
    st.divider()
    st.subheader("Chat about this lecture")
    st.caption("Questions are answered from the transcript and the assessment's transcript and visual evidence. Conversation history is saved with this job.")
    for item in current_job.get("conversation", []):
        with st.chat_message(item.get("role", "assistant")):
            st.write(item.get("content", ""))
    prompt = st.chat_input("Ask a question about the lecture…", key=f"chat_{job_id}")
    if prompt:
        try:
            with st.spinner("Looking through the lecture evidence…"):
                response = requests.post(f"{API_URL}/jobs/{job_id}/chat", json={"message": prompt}, timeout=(10, 180))
            response.raise_for_status()
            st.rerun()
        except requests.RequestException as exc:
            detail = "Could not send that question. Please try again."
            if getattr(exc, "response", None) is not None:
                detail = exc.response.json().get("detail", detail)
            st.error(detail)

    with st.expander("Create a Word document from this lecture and conversation"):
        document_request = st.text_area(
            "Describe the document you want",
            value="Create a structured lecture study guide with a summary, key concepts, examples, and follow-up questions.",
            key=f"document_request_{job_id}", height=100)
        if st.button("Plan and generate Word document", key=f"generate_doc_{job_id}"):
            try:
                with st.spinner("Planning the document and drafting each section…"):
                    response = requests.post(f"{API_URL}/agent",
                        json={"request": document_request, "job_id": job_id}, timeout=(10, 900))
                response.raise_for_status()
                message = BytesParser(policy=policy.default).parsebytes(
                    (f"Content-Type: {response.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode()) + response.content)
                result_part = next(part for part in message.iter_parts() if part.get_content_type() == "application/json")
                doc_part = next(part for part in message.iter_parts() if part.get_content_type().endswith("wordprocessingml.document"))
                st.session_state[f"generated_document_{job_id}"] = {
                    "manifest": json.loads(result_part.get_payload(decode=True).decode("utf-8")),
                    "bytes": doc_part.get_payload(decode=True),
                    "filename": doc_part.get_filename(),
                }
            except (requests.RequestException, KeyError, StopIteration, ValueError) as exc:
                st.error(f"Could not generate the Word document: {exc}")
        generated = st.session_state.get(f"generated_document_{job_id}")
        if generated:
            st.success(generated["manifest"].get("message", "Document generated."))
            st.caption(f"Plan: {len(generated['manifest'].get('plan', []))} steps · "
                       f"Provider: {generated['manifest'].get('provider', 'unknown')} · "
                       f"Conversation messages used: {generated['manifest'].get('conversation_messages_used', 0)}")
            execution = {item["id"]: item["status"] for item in generated["manifest"].get("execution", [])}
            with st.expander("Document plan and execution details"):
                for index, item in enumerate(generated["manifest"].get("plan", []), start=1):
                    status = execution.get(item["id"], "unknown").replace("_", " ")
                    st.markdown(f"**{index}. {item['heading']}** · {status}")
                    st.write(item["task"])
            st.download_button("Download Word document", data=generated["bytes"],
                file_name=generated["filename"],
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"download_doc_{job_id}")
