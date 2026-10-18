"""FastAPI web app: a small browser front end for the ElevenLabs dubbing flow.

Deployed on Vercel, where the entrypoint is the top-level `app` object below.
Dubbing is asynchronous, so the page creates a job, polls for its status, and
then streams the finished MP3 back — no long-lived function call, no local disk.

Building the final MP4 (POST /api/dub/{id}/video) is the exception: it downloads
the source video, merges it with the MP3, and uploads the result to Vercel Blob
in one long-lived call, streaming newline-delimited JSON progress events.
"""

import hmac
import json
import os
import queue
import shutil
import tempfile
import threading
import traceback
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import agent
import core
import video_job

PUBLIC_DIR = Path(__file__).resolve().parent / "public"

app = FastAPI(title="Video Translator")


class DubRequest(BaseModel):
    url: str
    to: str
    source_lang: str | None = None
    speakers: int = Field(default=0, ge=0, le=20)
    no_clone: bool = False
    watermark: bool = False


class VideoRequest(BaseModel):
    source: str
    to: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=24)


def require_password(request: Request) -> None:
    """Gate the API behind APP_PASSWORD when that variable is set."""
    expected = os.getenv("APP_PASSWORD", "")
    if not expected:
        return
    provided = request.headers.get("x-app-password", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Wrong or missing password.")


def elevenlabs_failure(error: Exception) -> HTTPException:
    """Turn a core/requests error into a gateway response the page can show."""
    return HTTPException(status_code=502, detail=str(error))


@app.get("/api/config")
def get_config() -> dict:
    """What the page needs before it can show the form (no password required)."""
    return {
        "password_required": bool(os.getenv("APP_PASSWORD")),
        "api_key_set": core.api_key_set(),
        "agent_enabled": agent.agent_enabled(),
        "languages": [
            {"code": code, "name": name.title()}
            for name, code in sorted(core.LANGUAGES.items())
        ],
    }


@app.post("/api/dub", dependencies=[Depends(require_password)])
def start_dub(request: DubRequest) -> dict:
    """Create a dubbing job; the browser then polls it until it is ready."""
    if not request.url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="The source must be a full URL starting with http:// or https://",
        )

    try:
        target = core.resolve_language(request.to)
        source_lang = core.resolve_language(request.source_lang) if request.source_lang else None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None

    try:
        job = core.create_dub(
            request.url, target, source_lang, request.speakers, request.no_clone, request.watermark
        )
    except core.DubbingError as error:
        raise elevenlabs_failure(error) from None

    if not job.get("dubbing_id"):
        raise HTTPException(status_code=502, detail="ElevenLabs did not return a dubbing job id.")

    return core.job_payload(request.url, target, job)


@app.get("/api/dub/{dubbing_id}", dependencies=[Depends(require_password)])
def dub_status(dubbing_id: str) -> dict:
    """Current state of a job, for the page's polling loop."""
    try:
        job = core.get_dub(dubbing_id)
    except core.DubbingError as error:
        raise elevenlabs_failure(error) from None
    return {
        "status": job.get("status"),
        "expected_duration_sec": job.get("expected_duration_sec") or 0,
    }


@app.post("/api/chat", dependencies=[Depends(require_password)])
def chat(request: ChatRequest) -> dict:
    """Natural-language front door: the agent picks the options and starts the job."""
    if not agent.agent_enabled():
        raise HTTPException(
            status_code=503,
            detail="The chat agent is not configured on this server. Set LLM_API_KEY (and optionally LLM_MODEL / LLM_BASE_URL), then try again.",
        )

    messages = [message.model_dump() for message in request.messages]
    if messages[-1]["role"] != "user":
        raise HTTPException(status_code=400, detail="The last message must come from the user.")

    try:
        return agent.run(messages)
    except agent.AgentError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


@app.get("/api/dub/{dubbing_id}/audio", dependencies=[Depends(require_password)])
def dub_audio(dubbing_id: str, lang: str, filename: str | None = None):
    """Stream the finished MP3 through the server so the API key stays secret."""
    try:
        response = core.open_dub_audio(dubbing_id, lang)
    except core.DubbingError as error:
        raise elevenlabs_failure(error) from None

    if response.status_code in (401, 403):
        response.close()
        raise HTTPException(status_code=502, detail="ElevenLabs rejected the server's API key.")
    if response.status_code >= 400:
        response.close()
        raise HTTPException(status_code=409, detail="The dub is not ready yet. Try again in a moment.")

    # Drop quotes so a crafted filename cannot escape the header.
    name = (filename or f"dub-{dubbing_id[:8]} [{lang}].mp3").replace('"', "")

    def chunks():
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                yield chunk
        finally:
            response.close()

    return StreamingResponse(
        chunks(),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/api/dub/{dubbing_id}/video", dependencies=[Depends(require_password)])
def dub_video(dubbing_id: str, request: VideoRequest) -> StreamingResponse:
    """Build the final MP4 (original picture + translated audio) and host it on Blob.

    This is the one genuinely long call in the app (tens of seconds up to a few
    minutes), so it streams newline-delimited JSON progress events: zero or more
    {"stage": ..., "percent": ...} lines, then exactly one final {"done": ...}
    or {"error": ...} line. The browser keeps reading until it sees that last one.
    """
    if not request.source.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="The source must be a full URL starting with http:// or https://",
        )
    try:
        target = core.resolve_language(request.to)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None

    events: queue.Queue[dict] = queue.Queue()

    def work() -> None:
        """Everything slow happens here; the generator below just relays it."""
        workspace = Path(tempfile.mkdtemp(prefix="translator-mp4-"))
        try:
            job = core.get_dub(dubbing_id)
            status = job.get("status")
            if status == "failed":
                raise video_job.VideoJobError("ElevenLabs could not dub this source, so there is no audio to merge.")
            if status != "dubbed":
                raise video_job.VideoJobError("The dub is not ready yet. Wait for the translation to finish, then try again.")

            events.put({"stage": "audio"})
            audio_path = workspace / "translated.mp3"
            response = core.open_dub_audio(dubbing_id, target)
            if response.status_code in (401, 403):
                response.close()
                raise video_job.VideoJobError("ElevenLabs rejected the server's API key.")
            if response.status_code >= 400:
                response.close()
                raise video_job.VideoJobError("The translated audio is not ready yet. Wait a moment and try again.")
            with audio_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    handle.write(chunk)
            response.close()

            video_path, info = video_job.download_source_video(
                request.source,
                workspace,
                lambda stage, percent: events.put({"stage": stage, "percent": percent}),
            )

            events.put({"stage": "merging"})
            final_path = workspace / "final.mp4"
            video_job.mux_with_audio(video_path, audio_path, final_path)

            events.put({"stage": "uploading"})
            filename = video_job.safe_filename(info.get("title") or "", core.display_name(target))
            url, download_url = video_job.upload_to_blob(final_path, filename)

            events.put({
                "done": True,
                "url": url,
                "download_url": download_url,
                "filename": filename,
                "size": final_path.stat().st_size,
            })
        except Exception as error:  # noqa: BLE001 - every failure becomes one final stream event
            if not isinstance(error, (core.DubbingError, video_job.VideoJobError)):
                traceback.print_exc()
            events.put({"error": str(error)})
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event.get("done") or event.get("error"):
                return

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )


# The frontend in public/ is served by Vercel's CDN at these root paths, and
# Vercel explicitly warns against mounting that directory from FastAPI. These
# three routes cover local runs (`uvicorn app:app`) and act as a safety net if
# the CDN ever misses a file; on Vercel the CDN copy usually answers first.
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html", media_type="text/html")


@app.get("/app.js", include_in_schema=False)
def app_js() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "app.js", media_type="text/javascript")


@app.get("/style.css", include_in_schema=False)
def style_css() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "style.css", media_type="text/css")
