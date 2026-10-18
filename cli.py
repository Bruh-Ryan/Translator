"""Command line: download video sources and dub their speech into other languages.

The web app (app.py) shares the same dubbing logic from core.py; this file is
for local work, including the FFmpeg merge step that needs a real disk.
"""

from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import time

import core  # Core loads .env on import, so VIDEO_DIR/TRANSLATION_DIR are ready below.


URL = "https://youtu.be/VD6xJq8NguY?si=eywi8Z34jWOnAUUP"
PROJECT_DIR = Path(__file__).resolve().parent
# Keep the source video and generated translations separate on the project drive.
# Defaults sit next to this file (not the current directory), so the CLI, the web
# app, and mcp_server.py always share the same video/ and translation/ folders.
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", PROJECT_DIR / "video"))
TRANSLATION_DIR = Path(os.environ.get("TRANSLATION_DIR", PROJECT_DIR / "translation"))

# File types the merge step knows how to look for on its own.
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
# Folders that never count as sources when searching the project for files.
EXCLUDED_DIRS = {".venv", ".git", "__pycache__", "node_modules"}


def ensure_space(directory: Path, required_bytes: int) -> None:
    """Fail before downloading when there is not enough free disk space."""
    free_bytes = shutil.disk_usage(directory).free
    if free_bytes < required_bytes:
        raise RuntimeError(
            f"Not enough free space in {directory}. "
            f"Need about {required_bytes / 1024**2:.0f} MiB, but only "
            f"{free_bytes / 1024**2:.0f} MiB is available."
        )


def download_video(url: str) -> Path | None:
    """Download a video-only source into video/ and return its file path."""
    import yt_dlp  # Lazy: dubbing and merging do not need yt-dlp installed.

    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    # A video-only source is enough for the final FFmpeg export: the dubbed
    # track is generated separately and replaces the original audio.
    options = {
        "outtmpl": str(VIDEO_DIR / "%(title)s.%(ext)s"),
        "format": "bestvideo[ext=mp4]/bestvideo",
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
        # Allow headroom for the source audio and the temporary MP3 conversion.
        estimated_size = info.get("filesize") or info.get("filesize_approx") or 0
        ensure_space(VIDEO_DIR, max(100 * 1024**2, estimated_size * 2))
        ydl.download([url])
        output = Path(ydl.prepare_filename(info))

    return output if output.exists() else None


def newest_file(directory: Path, extensions: set[str]) -> Path | None:
    """Newest file with one of these extensions in a single folder, or None."""
    if not directory.is_dir():
        return None
    files = [
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    ]
    return max(files, key=lambda path: path.stat().st_mtime, default=None)


def _newest_in_project(extensions: set[str]) -> Path | None:
    """Newest matching file anywhere below the project, skipping tool folders."""
    files: list[Path] = []
    for root, dirs, names in os.walk(PROJECT_DIR):
        dirs[:] = [name for name in dirs if name not in EXCLUDED_DIRS]
        files.extend(
            Path(root) / name for name in names if Path(name).suffix.lower() in extensions
        )
    return max(files, key=lambda path: path.stat().st_mtime, default=None)


def find_video(directory: Path) -> Path | None:
    """Newest video in the video folder, falling back to the newest in the project."""
    return newest_file(directory, VIDEO_EXTENSIONS) or _newest_in_project(VIDEO_EXTENSIONS)


def find_audio(directory: Path) -> Path | None:
    """Newest translated MP3 in the translation folder, falling back to the project."""
    return newest_file(directory, {".mp3"}) or _newest_in_project({".mp3"})


def merge_video(video: Path, audio: Path, out: Path | None = None, overwrite: bool = False) -> Path:
    """Put the translated audio into the silent source video with FFmpeg."""
    if not video.is_file():
        raise RuntimeError(f"Video file not found: {video}")
    if not audio.is_file():
        raise RuntimeError(f"Audio file not found: {audio}")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg was not found on PATH. Install it first (see the README).")

    out_path = Path(out) if out else audio.with_suffix(".mp4")
    if out_path.exists() and not overwrite:
        raise RuntimeError(f"{out_path} already exists. Pass --force to overwrite it.")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg", "-y",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]
    print("Running:", " ".join(command))
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        tail = "\n".join((result.stderr or "no details").strip().splitlines()[-8:])
        raise RuntimeError(f"FFmpeg failed:\n{tail}")
    return out_path


def wait_for_dub(dubbing_id: str, interval: float = 10.0, max_wait: float = 45 * 60) -> None:
    """Poll the job until it finishes; dubbing runs on ElevenLabs' servers for minutes."""
    started = time.time()
    while True:
        job = core.get_dub(dubbing_id)
        status = job.get("status")
        elapsed = int(time.time() - started)
        print(f"  status: {status} ({elapsed}s elapsed)")
        if status == "dubbed":
            return
        if status == "failed":
            raise core.DubbingError("Dubbing failed on ElevenLabs' side.")
        if elapsed > max_wait:
            raise core.DubbingError(f"Gave up waiting after {int(max_wait)}s.")
        time.sleep(interval)  # Don't hammer the API; wait, then ask again.


def download_dub(dubbing_id: str, lang_code: str, out_path: Path) -> None:
    """Save the finished audio, streamed in chunks so it never sits fully in RAM."""
    response = core.open_dub_audio(dubbing_id, lang_code)
    if response.status_code >= 400:
        response.close()
        raise core.DubbingError(f"ElevenLabs could not return the audio ({response.status_code}).")
    with response, out_path.open("wb") as out_file:  # "wb" = write bytes; overwrite if exists
        for chunk in response.iter_content(chunk_size=64 * 1024):
            out_file.write(chunk)


def dub_audio(source: str, to: str, source_lang: str | None = None, out: str | None = None,
              speakers: int = 0, no_clone: bool = False, watermark: bool = False,
              merge: bool = False) -> Path:
    """Speech-to-speech translate one file (or URL) and save the result in Saves/."""
    target = core.resolve_language(to)
    job = core.create_dub(
        source,
        target,
        core.resolve_language(source_lang) if source_lang else None,
        speakers,
        no_clone,
        watermark,
    )
    dubbing_id = job["dubbing_id"]
    duration = job.get("expected_duration_sec") or 0
    print(f"Job {dubbing_id} created (expected duration {duration:.0f}s)")

    # Reuse the disk-space guard: MP3 output is roughly 16 KB per second of audio.
    TRANSLATION_DIR.mkdir(parents=True, exist_ok=True)
    ensure_space(TRANSLATION_DIR, max(50 * 1024**2, int(duration * 16_000 * 2)))

    wait_for_dub(dubbing_id)

    if out:
        out_path = Path(out)
    else:
        stem = core.output_stem(source, dubbing_id)
        out_path = TRANSLATION_DIR / f"{stem} [{core.display_name(target)}].mp3"

    download_dub(dubbing_id, target, out_path)

    if merge:
        video = find_video(VIDEO_DIR)
        if not video and source.startswith(("http://", "https://")):
            video = download_video(source)
        if not video:
            raise RuntimeError(
                f"Saved {out_path.name}, but found no video to merge with. "
                "Download one first: python cli.py download"
            )
        print(f"Merging with: {video}")
        merged = merge_video(video, out_path, overwrite=True)
        print(f"Merged: {merged}")

    return out_path


def main() -> None:
    # Console-safe printing: replace characters the terminal cannot display.
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Download video sources and dub their speech.")
    sub = parser.add_subparsers(dest="command")

    p_download = sub.add_parser("download", help="Download a video-only source into video/")
    p_download.add_argument("url", nargs="?", default=URL, help="video URL (defaults to the built-in one)")

    p_dub = sub.add_parser("dub", help="Translate the speech into another language (ElevenLabs Dubbing)")
    p_dub.add_argument("source", help="audio file path or video URL")
    p_dub.add_argument("--to", required=True, help="target language, e.g. hindi or hi")
    p_dub.add_argument("--from", dest="source_lang", help="source language (optional, auto-detected)")
    p_dub.add_argument("--out", help="output file path (default: translation/<name> [<Language>].mp3)")
    p_dub.add_argument("--speakers", type=int, default=0, help="number of speakers, 0 = auto-detect")
    p_dub.add_argument("--no-clone", action="store_true", help="do not clone the original voice; use a library voice")
    p_dub.add_argument("--watermark", action="store_true", help="allow an ElevenLabs watermark (required on some plans)")
    p_dub.add_argument("--merge", action="store_true", help="also download the video and merge the result into an MP4")

    p_merge = sub.add_parser("merge", help="Merge a downloaded video with a translated MP3 (FFmpeg)")
    p_merge.add_argument("--video", help="video file (default: newest video in the video folder)")
    p_merge.add_argument("--audio", help="translated audio file (default: newest MP3 in the translation folder)")
    p_merge.add_argument("--out", help="output file (default: <audio name>.mp4 next to the audio)")
    p_merge.add_argument("--force", action="store_true", help="overwrite the output if it already exists")

    args = parser.parse_args()
    try:
        if args.command == "download":
            path = download_video(args.url)
            print(f"Saved: {path}" if path else "Download finished.")
        elif args.command == "dub":
            out_path = dub_audio(
                args.source, args.to, args.source_lang, args.out, args.speakers,
                args.no_clone, args.watermark, args.merge,
            )
            print(f"Saved: {out_path}")
        elif args.command == "merge":
            video = Path(args.video) if args.video else find_video(VIDEO_DIR)
            audio = Path(args.audio) if args.audio else find_audio(TRANSLATION_DIR)
            if video is None:
                raise RuntimeError("No video file found. Pass --video, or download one first: python cli.py download")
            if audio is None:
                raise RuntimeError("No translated MP3 found. Pass --audio, or dub one first: python cli.py dub ...")
            out_path = merge_video(video, audio, Path(args.out) if args.out else None, args.force)
            print(f"Saved: {out_path}")
        else:
            parser.print_help()
    except (core.DubbingError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
