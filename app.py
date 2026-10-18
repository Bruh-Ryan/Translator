"""FastAPI web app: a small browser front end for the ElevenLabs dubbing flow.

Deployed on Vercel, where the entrypoint is the top-level `app` object below.
Dubbing is asynchronous, so the page creates a job, polls for its status, and
then streams the finished MP3 back — no long-lived function call, no local disk.
"""

import hmac
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import core

PUBLIC_DIR = Path(__file__).resolve().parent / "public"

app = FastAPI(title="Video Translator")


class DubRequest(BaseModel):
    url: str
    to: str
    source_lang: str | None = None
    speakers: int = Field(default=0, ge=0, le=20)
    no_clone: bool = False
    watermark: bool = False


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

    dubbing_id = job.get("dubbing_id")
    if not dubbing_id:
        raise HTTPException(status_code=502, detail="ElevenLabs did not return a dubbing job id.")

    return {
        "dubbing_id": dubbing_id,
        "status": job.get("status"),
        "expected_duration_sec": job.get("expected_duration_sec") or 0,
        "target_lang": target,
        "filename": f"{core.output_stem(request.url, dubbing_id)} [{core.display_name(target)}].mp3",
    }


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
