"""Command line: download video sources and dub their speech into other languages.

The web app (app.py) shares the same dubbing logic from core.py; this file is
for local work, including the FFmpeg merge step that needs a real disk.
"""

from pathlib import Path
import argparse
import os
import shutil
import time

import yt_dlp

import core  # Core loads .env on import, so VIDEO_DIR/TRANSLATION_DIR are ready below.


URL = "https://youtu.be/VD6xJq8NguY?si=eywi8Z34jWOnAUUP"
# Keep the source video and generated translations separate on the project drive.
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", Path.cwd() / "video"))
TRANSLATION_DIR = Path(os.environ.get("TRANSLATION_DIR", Path.cwd() / "translation"))


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
              speakers: int = 0, no_clone: bool = False, watermark: bool = False) -> Path:
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
    return out_path


def main() -> None:
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

    args = parser.parse_args()
    try:
        if args.command == "download":
            path = download_video(args.url)
            print(f"Saved: {path}" if path else "Download finished.")
        elif args.command == "dub":
            out_path = dub_audio(
                args.source, args.to, args.source_lang, args.out, args.speakers,
                args.no_clone, args.watermark,
            )
            print(f"Saved: {out_path}")
        else:
            parser.print_help()
    except (core.DubbingError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
