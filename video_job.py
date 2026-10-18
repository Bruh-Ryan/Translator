"""Build the final MP4 on the server: download, mux, upload to Vercel Blob.

The CLI does the same three steps locally (see cli.py: `download` and `merge`).
This module is the server-side version used by the web app on Vercel:

1. yt-dlp downloads the video-only stream (no audio needed, capped at 1080p)
2. FFmpeg muxes it with the translated MP3 that ElevenLabs produced
3. The MP4 is uploaded to Vercel Blob, because a function response is capped
   at 4.5 MB and finished videos are usually bigger than that

FFmpeg comes from the system when present (local runs) and from the bundled
imageio-ffmpeg static build otherwise (Vercel has no system FFmpeg).
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

# Blob uploads go through this control-plane address; the flow mirrors the
# official SDK (vercel/storage), including the API version and headers.
BLOB_API_URL = "https://vercel.com/api/blob/"
BLOB_API_VERSION = "13"

# Higher sources take too long to download and upload inside one function call.
MAX_HEIGHT = int(os.getenv("MAX_VIDEO_HEIGHT", "1080"))
MAX_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "180"))


class VideoJobError(RuntimeError):
    """The MP4 could not be produced."""


class VideoTooLong(VideoJobError):
    """The source is longer than the web version is set up to handle."""


class BlobNotConfigured(VideoJobError):
    """BLOB_READ_WRITE_TOKEN is missing, so the MP4 cannot be hosted."""


def _make_runnable(path: Path) -> None:
    """Bundles are unpacked without the exec bit on Vercel; make sure it is set."""
    try:
        path.chmod(path.stat().st_mode | 0o111)
    except OSError:
        pass


def _node_major_version(path: str) -> int:
    """yt-dlp needs Node 22+; report 0 when the binary cannot be run or parsed."""
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=15, errors="replace"
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    match = re.match(r"v(\d+)", (result.stdout or "").strip())
    return int(match.group(1)) if match else 0


_JS_RUNTIME_CACHE: dict | None = None


def js_runtime_options() -> dict:
    """The yt-dlp "js_runtimes" option — YouTube now needs an external JS engine.

    Order of preference: the YTDLP_JS_RUNTIME environment variable ("name" or
    "name:path"), Node.js 22+ on PATH (local runs), the bundled QuickJS binary
    in vendor/ (static build, works on Vercel), then qjs on PATH. Returns {}
    when nothing is available, which leaves yt-dlp's own default in place.
    """
    global _JS_RUNTIME_CACHE
    if _JS_RUNTIME_CACHE is not None:
        return _JS_RUNTIME_CACHE

    override = os.getenv("YTDLP_JS_RUNTIME", "").strip()
    if override:
        name, _, path = override.partition(":")
        _JS_RUNTIME_CACHE = {"js_runtimes": {name: {"path": path} if path else {}}}
        return _JS_RUNTIME_CACHE

    node = shutil.which("node")
    if node and _node_major_version(node) >= 22:
        _JS_RUNTIME_CACHE = {"js_runtimes": {"node": {}}}
        return _JS_RUNTIME_CACHE

    bundled = Path(__file__).parent / "vendor" / ("qjs.exe" if os.name == "nt" else "qjs")
    if bundled.exists():
        _make_runnable(bundled)
        _JS_RUNTIME_CACHE = {"js_runtimes": {"quickjs": {"path": str(bundled)}}}
        return _JS_RUNTIME_CACHE

    qjs = shutil.which("qjs")
    if qjs:
        _JS_RUNTIME_CACHE = {"js_runtimes": {"quickjs": {"path": qjs}}}
        return _JS_RUNTIME_CACHE

    _JS_RUNTIME_CACHE = {}
    return _JS_RUNTIME_CACHE


def ffmpeg_path() -> str:
    """System FFmpeg when present (local runs), else the bundled static build."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg
    except ImportError:
        raise VideoJobError(
            "FFmpeg is not available. Install it and put it on PATH, or add imageio-ffmpeg to the environment."
        ) from None
    binary = Path(imageio_ffmpeg.get_ffmpeg_exe())
    if not binary.exists():
        raise VideoJobError("The bundled FFmpeg binary could not be found.")
    _make_runnable(binary)
    return str(binary)


def _friendly_download_error(message: str) -> str:
    """Turn yt-dlp's error spew into one useful sentence for the page."""
    text = message.replace("ERROR: ", "").strip()
    lower = text.lower()
    if "javascript runtime" in lower or "js runtime" in lower:
        return (
            "No JavaScript runtime is available to answer YouTube's download challenge. "
            "Install Node.js 22+ (or QuickJS), or use the local CLI."
        )
    if "not a bot" in lower or "sign in" in lower or "cookies" in lower:
        return (
            "YouTube refused the download from this server (its address looks like a data centre). "
            "This is a known limit of the hosted version — the local CLI can still do it: "
            "python cli.py download, then python cli.py merge."
        )
    if "video unavailable" in lower or "private" in lower:
        return "YouTube says that video is unavailable (private, removed, or region-locked)."
    if "unsupported url" in lower:
        return "This site is not supported for direct video downloads. Use a YouTube link, or merge locally with the CLI."
    first_line = text.splitlines()[0][:300] if text else "The video download failed."
    return f"Video download failed: {first_line}"


def _extract_info(url: str) -> dict:
    """Fetch metadata (title, duration) without downloading the video."""
    import yt_dlp  # keep the heavy import off the page-load path

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        **js_runtime_options(),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    if info and info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None)
    if not info:
        raise VideoJobError("No playable video was found at that URL.")
    return info


def download_source_video(url: str, dest_dir: Path, on_progress=None) -> tuple[Path, dict]:
    """Download the video-only stream (capped at MAX_HEIGHT) into dest_dir.

    on_progress, when given, is called as on_progress(stage, percent) with
    stage "downloading" (percent may be None) or "processing".
    """
    import yt_dlp

    def hook(data: dict) -> None:
        if on_progress is None:
            return
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            got = data.get("downloaded_bytes") or 0
            on_progress("downloading", min(99, int(got * 100 / total)) if total else None)
        elif status == "finished":
            on_progress("processing", None)

    try:
        info = _extract_info(url)
    except yt_dlp.utils.DownloadError as error:
        raise VideoJobError(_friendly_download_error(str(error))) from None
    except SystemExit:
        raise VideoJobError(
            "Could not read the video's information (yt-dlp refused to continue). "
            "Try another link, or use the local CLI."
        ) from None

    duration = info.get("duration") or 0
    if duration > MAX_SECONDS:
        raise VideoTooLong(
            f"That video is {duration // 60} min {duration % 60} sec long. The web version handles up to "
            f"{MAX_SECONDS // 60} minutes — use the local CLI for longer videos: "
            "python cli.py download, then python cli.py merge."
        )

    options = {
        # Prefer MP4 streams so the merge can copy the video without re-encoding.
        "format": (
            f"bestvideo[height<={MAX_HEIGHT}][ext=mp4]"
            f"/best[height<={MAX_HEIGHT}][ext=mp4]"
            f"/bestvideo[height<={MAX_HEIGHT}]/best[height<={MAX_HEIGHT}]/best"
        ),
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "ffmpeg_location": ffmpeg_path(),
        "noplaylist": True,
        "playlist_items": "1",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 2,
        "progress_hooks": [hook],
        **js_runtime_options(),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as error:
        raise VideoJobError(_friendly_download_error(str(error))) from None
    except SystemExit:
        # yt-dlp calls sys.exit on some download failures; keep it a normal error.
        raise VideoJobError(
            "The video download stopped early (yt-dlp refused to continue). "
            "YouTube sometimes blocks server addresses; the local CLI may still work."
        ) from None

    downloads = sorted(
        (path for path in dest_dir.glob("source.*") if path.suffix != ".part"),
        key=lambda path: path.stat().st_mtime,
    )
    if not downloads:
        raise VideoJobError("The video download finished, but no file was produced.")
    return downloads[-1], info


def mux_with_audio(video: Path, audio: Path, out: Path) -> None:
    """Put the translated audio into the video, copying the picture as-is."""
    command = [
        ffmpeg_path(), "-y",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(out),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise VideoJobError("FFmpeg took too long to merge the files.") from None
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "no details").strip().splitlines()[-6:])
        raise VideoJobError(f"FFmpeg could not merge the video and audio:\n{tail}")


# Only plain ASCII survives: the name also travels in HTTP headers and a query
# string, where non-latin characters (emoji in titles) behave inconsistently.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()\-\[\]]+")


def safe_filename(title: str, lang_name: str) -> str:
    """'Have a coffee with Tim [Hindi].mp4' — short and safe for headers and URLs."""
    clean = re.sub(r"\s+", " ", _UNSAFE_NAME.sub(" ", title)).strip(" .")
    if len(clean) < 3:
        clean = "translated-video"
    return f"{clean[:80].strip()} [{lang_name}].mp4"


def upload_to_blob(file_path: Path, filename: str) -> tuple[str, str]:
    """Upload the MP4 to Vercel Blob; returns (url, download_url).

    The store id comes from the read-write token itself, exactly like the SDK
    does it: "vercel_blob_rw_<storeId>_<secret>".
    """
    token = os.getenv("BLOB_READ_WRITE_TOKEN", "").strip()
    if not token:
        raise BlobNotConfigured(
            "The MP4 was built, but this server has no Blob storage configured (BLOB_READ_WRITE_TOKEN is missing). "
            "Connect a Blob store to the Vercel project, or merge locally: python cli.py merge"
        )

    parts = token.split("_")
    store_id = parts[3] if len(parts) > 3 else ""
    pathname = f"videos/{filename}"
    headers = {
        "authorization": f"Bearer {token}",
        "x-api-version": BLOB_API_VERSION,
        "x-vercel-blob-store-id": store_id,
        "x-vercel-blob-access": "public",
        "x-content-type": "video/mp4",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
        "x-cache-control-max-age": "3600",
    }

    try:
        with file_path.open("rb") as handle:
            response = requests.put(
                f"{BLOB_API_URL}?{urlencode({'pathname': pathname})}",
                headers=headers,
                data=handle,  # requests sends the file with a Content-Length
                timeout=(30, 600),
            )
    except requests.RequestException as error:
        raise VideoJobError(f"Could not reach Vercel Blob: {error}") from None

    if not response.ok:
        raise VideoJobError(
            f"Blob storage rejected the upload ({response.status_code}): {response.text[:300]}"
        )
    try:
        payload = response.json()
    except ValueError:
        raise VideoJobError("Blob storage returned an unexpected response.") from None
    url = payload.get("url")
    if not url:
        raise VideoJobError("Blob storage did not return a URL for the upload.")
    return url, payload.get("downloadUrl") or url
