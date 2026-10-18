"""MCP server: the local download → dub → merge pipeline as tools for agents.

Runs over stdio (`python mcp_server.py`), so OpenCode or any other MCP client
can launch it and call tools such as `dub_and_wait` or `translate_video`. The
ElevenLabs key is read from .env by core.py, exactly like the CLI.

Two details matter here:
- The CLI helpers print progress to stdout, but stdout is the MCP protocol
  channel, so every tool call runs with stdout temporarily redirected to stderr.
- The MCP SDK masks uncaught exceptions behind a generic message, so the
  _as_result decorator turns failures into {"error": ...} results the calling
  agent can read (the traceback still lands in the server's log).
"""

import contextlib
import functools
from pathlib import Path
import sys
import traceback

from mcp.server.mcpserver import MCPServer

import cli
import core

mcp = MCPServer("translator")  # v2 of the MCP Python SDK (formerly FastMCP)


@contextlib.contextmanager
def _quiet():
    """Route CLI/yt-dlp/ffmpeg chatter to stderr; MCP needs stdout protocol-clean."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _as_result(fn):
    """Report failures as {"error": ...} so the agent can react to them."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            return {"error": str(error) or type(error).__name__}
    return wrapper


@mcp.tool()
@_as_result
def list_languages() -> dict[str, str]:
    """Map of the language names to the ISO codes the ElevenLabs dubbing API accepts."""
    return {name.title(): code for name, code in sorted(core.LANGUAGES.items())}


@mcp.tool()
@_as_result
def download_video(url: str) -> dict:
    """Download a video-only source (no audio) into the video folder with yt-dlp. Returns its file path."""
    with _quiet():
        path = cli.download_video(url)
    if not path:
        return {"error": "The download finished but no output file was found."}
    return {"path": str(path)}


@mcp.tool()
@_as_result
def start_dub(source: str, to: str, source_lang: str | None = None, speakers: int = 0,
              no_clone: bool = False, watermark: bool = False) -> dict:
    """Start an asynchronous ElevenLabs dubbing job. 'source' is a video URL or a local audio/video file path; 'to' is the target language name or ISO code. Returns the job id — poll it with dub_status, then fetch the audio with save_dub_audio."""
    target = core.resolve_language(to)
    source_lang_code = core.resolve_language(source_lang) if source_lang else None
    job = core.create_dub(source, target, source_lang_code, speakers, no_clone, watermark)
    if not job.get("dubbing_id"):
        return {"error": "ElevenLabs did not return a dubbing job id."}
    return core.job_payload(source, target, job)


@mcp.tool()
@_as_result
def dub_status(dubbing_id: str) -> dict:
    """Check a dubbing job: status (queued/processing/dubbed/failed) and the expected audio length in seconds."""
    job = core.get_dub(dubbing_id)
    return {
        "status": job.get("status"),
        "expected_duration_sec": job.get("expected_duration_sec") or 0,
    }


@mcp.tool()
@_as_result
def save_dub_audio(dubbing_id: str, to: str, out: str | None = None) -> dict:
    """Save the finished dubbed audio (status must be 'dubbed') as an MP3 file in translation/ and return its path. 'to' is the target language the job was created with."""
    target = core.resolve_language(to)
    if out:
        out_path = Path(out)
    else:
        out_path = cli.TRANSLATION_DIR / f"dub-{dubbing_id[:8]} [{core.display_name(target)}].mp3"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with _quiet():
        cli.download_dub(dubbing_id, target, out_path)
    return {"path": str(out_path)}


@mcp.tool()
@_as_result
def dub_and_wait(source: str, to: str, source_lang: str | None = None, speakers: int = 0,
                 no_clone: bool = False, watermark: bool = False, out: str | None = None) -> dict:
    """Start a dub and wait until ElevenLabs finishes (usually minutes), then save the MP3. Returns its file path. Prefer this over start_dub unless you need the job id while it runs."""
    with _quiet():
        path = cli.dub_audio(source, to, source_lang, out, speakers, no_clone, watermark)
    return {"path": str(path)}


@mcp.tool()
@_as_result
def merge_video(video: str, audio: str, out: str | None = None, overwrite: bool = False) -> dict:
    """Merge a (silent) source video and a translated MP3 into one MP4 with FFmpeg, copying the video stream untouched. Returns the MP4 path."""
    with _quiet():
        path = cli.merge_video(Path(video), Path(audio), Path(out) if out else None, overwrite)
    return {"path": str(path)}


@mcp.tool()
@_as_result
def translate_video(url: str, to: str, source_lang: str | None = None, speakers: int = 0,
                    no_clone: bool = False, watermark: bool = False) -> dict:
    """One-call pipeline for a video URL: download the best video-only stream, dub the URL's speech with ElevenLabs, then merge both into an MP4. Can take several minutes. Returns the MP4 and MP3 paths."""
    with _quiet():
        video = cli.download_video(url)
        if not video:
            raise RuntimeError("The video download finished but no output file was found.")
        audio = cli.dub_audio(url, to, source_lang, None, speakers, no_clone, watermark)
        merged = cli.merge_video(video, audio, overwrite=True)
    return {"mp4": str(merged), "mp3": str(audio)}


if __name__ == "__main__":
    mcp.run()  # stdio transport: the MCP client launches this file directly
