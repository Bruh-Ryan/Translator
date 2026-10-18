"""Download audio and dub it into another language with the ElevenLabs Dubbing API."""

from pathlib import Path
import argparse
import mimetypes
import os
import shutil
import time

import requests
import yt_dlp
from dotenv import load_dotenv

load_dotenv()  # Puts values from .env into the environment.


URL = "https://youtu.be/VD6xJq8NguY?si=eywi8Z34jWOnAUUP"
# Keep the source video and generated translations separate on the project drive.
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", Path.cwd() / "video"))
TRANSLATION_DIR = Path(os.environ.get("TRANSLATION_DIR", Path.cwd() / "translation"))

API_BASE = "https://api.elevenlabs.io/v1"
API_KEY = os.getenv("ELEVENLABS_API_KEY")
# Every ElevenLabs request authenticates with the key in this header.
HEADERS = {"xi-api-key": API_KEY}

# Common language names -> ISO 639-1 codes the dubbing API expects.
# Any other ISO code works too if passed directly, e.g. --to sv.
LANGUAGES = {
    "english": "en",
    "hindi": "hi",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "turkish": "tr",
    "arabic": "ar",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "indonesian": "id",
    "dutch": "nl",
    "polish": "pl",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "no",
    "finnish": "fi",
    "czech": "cs",
    "greek": "el",
    "romanian": "ro",
    "ukrainian": "uk",
    "vietnamese": "vi",
    "thai": "th",
    "malay": "ms",
    "filipino": "fil",
    "urdu": "ur",
    "bengali": "bn",
    "tamil": "ta",
    "telugu": "te",
    "marathi": "mr",
    "gujarati": "gu",
    "punjabi": "pa",
    "malayalam": "ml",
    "kannada": "kn",
    "nepali": "ne",
}


def resolve_language(name: str) -> str:
    """Turn 'Hindi' into 'hi'; a raw code like 'bn' passes through unchanged."""
    key = name.strip().lower()
    if key in LANGUAGES:
        return LANGUAGES[key]
    if len(key) in (2, 3):
        return key
    raise SystemExit(f"Unknown language {name!r}. Use a code such as 'hi', or add it to LANGUAGES.")


def display_name(code: str) -> str:
    """Reverse lookup 'hi' -> 'Hindi' so output files read nicely."""
    for name, value in LANGUAGES.items():
        if value == code:
            return name.title()
    return code.upper()


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


# --- ElevenLabs Dubbing ------------------------------------------------------
# Dubbing is asynchronous: create a job, poll it until it is "dubbed",
# then download the finished audio.


def create_dub(source: str, target_lang: str, source_lang: str | None = None,
               speakers: int = 0, no_clone: bool = False, watermark: bool = False) -> dict:
    """Upload the audio (or a URL) and start a dubbing job. Returns the job JSON."""
    data = {"target_lang": target_lang, "num_speakers": str(speakers)}  # 0 = auto-detect
    if source_lang:
        data["source_lang"] = source_lang
    if no_clone:
        # Use a similar voice from the voice library instead of cloning the speaker.
        data["disable_voice_cloning"] = "true"
    if watermark:
        # Required by ElevenLabs for dubbing on plans without watermark-free access.
        data["watermark"] = "true"

    if source.startswith(("http://", "https://")):
        # Let ElevenLabs fetch the media itself; no download needed.
        data["source_url"] = source
        response = requests.post(f"{API_BASE}/dubbing", headers=HEADERS, data=data, timeout=120)
    else:
        audio_path = Path(source)
        if not audio_path.exists():
            raise SystemExit(f"File not found: {audio_path}")
        content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        with audio_path.open("rb") as audio_file:  # "rb" = read bytes; audio is not text
            files = {"file": (audio_path.name, audio_file, content_type)}
            response = requests.post(
                f"{API_BASE}/dubbing", headers=HEADERS, data=data, files=files, timeout=120
            )

    if not response.ok:
        raise SystemExit(
            f"ElevenLabs rejected the request ({response.status_code}): {response.text[:300]}"
        )
    return response.json()


def wait_for_dub(dubbing_id: str, interval: float = 10.0, max_wait: float = 45 * 60) -> None:
    """Poll the job until it finishes; dubbing runs on ElevenLabs' servers for minutes."""
    started = time.time()
    while True:
        response = requests.get(f"{API_BASE}/dubbing/{dubbing_id}", headers=HEADERS, timeout=30)
        response.raise_for_status()
        status = response.json().get("status")
        elapsed = int(time.time() - started)
        print(f"  status: {status} ({elapsed}s elapsed)")
        if status == "dubbed":
            return
        if status == "failed":
            raise SystemExit("Dubbing failed on ElevenLabs' side.")
        if elapsed > max_wait:
            raise SystemExit(f"Gave up waiting after {int(max_wait)}s.")
        time.sleep(interval)  # Don't hammer the API; wait, then ask again.


def download_dub(dubbing_id: str, lang_code: str, out_path: Path) -> None:
    """Save the finished audio, streamed in chunks so it never sits fully in RAM."""
    url = f"{API_BASE}/dubbing/{dubbing_id}/audio/{lang_code}"
    with requests.get(url, headers=HEADERS, stream=True, timeout=120) as response:
        response.raise_for_status()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("wb") as out_file:  # "wb" = write bytes; overwrite if exists
            for chunk in response.iter_content(chunk_size=64 * 1024):
                out_file.write(chunk)


def dub_audio(source: str, to: str, source_lang: str | None = None, out: str | None = None,
              speakers: int = 0, no_clone: bool = False, watermark: bool = False) -> Path:
    """Speech-to-speech translate one file (or URL) and save the result in Saves/."""
    if not API_KEY:
        raise SystemExit("No API key. Add ELEVENLABS_API_KEY=... to .env")

    target = resolve_language(to)
    job = create_dub(
        source,
        target,
        resolve_language(source_lang) if source_lang else None,
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
        is_url = source.startswith(("http://", "https://"))
        stem = f"dub-{dubbing_id[:8]}" if is_url else Path(source).stem
        out_path = TRANSLATION_DIR / f"{stem} [{display_name(target)}].mp3"

    download_dub(dubbing_id, target, out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Download audio and dub it into other languages.")
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


if __name__ == "__main__":
    main()
