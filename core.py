"""Shared ElevenLabs dubbing logic used by the CLI (cli.py) and the web app (app.py).

Dubbing is asynchronous: create a job, poll it until it is "dubbed", then
download the finished audio. The CLI waits in-process (see cli.wait_for_dub);
the web app lets the browser do the polling instead.
"""

from pathlib import Path
import mimetypes
import os

import requests
from dotenv import load_dotenv

load_dotenv()  # Puts values from .env into the environment; a no-op on Vercel.


API_BASE = "https://api.elevenlabs.io/v1"


class DubbingError(RuntimeError):
    """The dubbing request failed, or the API key is missing."""


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


def api_key_set() -> bool:
    """True when ELEVENLABS_API_KEY is available to this process."""
    return bool(os.getenv("ELEVENLABS_API_KEY"))


def _headers() -> dict:
    """Every ElevenLabs request authenticates with the key in this header."""
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        raise DubbingError("No API key. Add ELEVENLABS_API_KEY=... to .env or the environment.")
    return {"xi-api-key": key}


def resolve_language(name: str) -> str:
    """Turn 'Hindi' into 'hi'; a raw code like 'bn' passes through unchanged."""
    key = name.strip().lower()
    if key in LANGUAGES:
        return LANGUAGES[key]
    if len(key) in (2, 3):
        return key
    raise ValueError(f"Unknown language {name!r}. Use a code such as 'hi', or add it to LANGUAGES.")


def display_name(code: str) -> str:
    """Reverse lookup 'hi' -> 'Hindi' so output files read nicely."""
    for name, value in LANGUAGES.items():
        if value == code:
            return name.title()
    return code.upper()


def output_stem(source: str, dubbing_id: str) -> str:
    """'dub-1234abcd' for a URL source, otherwise the audio file's own name."""
    if source.startswith(("http://", "https://")):
        return f"dub-{dubbing_id[:8]}"
    return Path(source).stem


def job_payload(source: str, target_lang: str, job: dict) -> dict:
    """Shape a create-dub response for the browser (form or agent): id, status, filename."""
    dubbing_id = job.get("dubbing_id") or ""
    return {
        "dubbing_id": dubbing_id,
        "status": job.get("status"),
        "expected_duration_sec": job.get("expected_duration_sec") or 0,
        "target_lang": target_lang,
        "filename": f"{output_stem(source, dubbing_id)} [{display_name(target_lang)}].mp3",
    }


def create_dub(source: str, target_lang: str, source_lang: str | None = None,
               speakers: int = 0, no_clone: bool = False, watermark: bool = False) -> dict:
    """Upload the audio (or a URL) and start a dubbing job. Returns the job JSON."""
    headers = _headers()
    data = {"target_lang": target_lang, "num_speakers": str(speakers)}  # 0 = auto-detect
    if source_lang:
        data["source_lang"] = source_lang
    if no_clone:
        # Use a similar voice from the voice library instead of cloning the speaker.
        data["disable_voice_cloning"] = "true"
    if watermark:
        # Required by ElevenLabs for dubbing on plans without watermark-free access.
        data["watermark"] = "true"

    try:
        if source.startswith(("http://", "https://")):
            # Let ElevenLabs fetch the media itself; no download needed.
            data["source_url"] = source
            response = requests.post(f"{API_BASE}/dubbing", headers=headers, data=data, timeout=120)
        else:
            audio_path = Path(source)
            if not audio_path.exists():
                raise DubbingError(f"File not found: {audio_path}")
            content_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
            with audio_path.open("rb") as audio_file:  # "rb" = read bytes; audio is not text
                files = {"file": (audio_path.name, audio_file, content_type)}
                response = requests.post(
                    f"{API_BASE}/dubbing", headers=headers, data=data, files=files, timeout=120
                )
    except requests.RequestException as error:
        raise DubbingError(f"Could not reach ElevenLabs: {error}") from None

    if not response.ok:
        raise DubbingError(
            f"ElevenLabs rejected the request ({response.status_code}): {response.text[:300]}"
        )
    return response.json()


def get_dub(dubbing_id: str) -> dict:
    """Fetch the current state of a dubbing job (status, expected_duration_sec, ...)."""
    try:
        response = requests.get(f"{API_BASE}/dubbing/{dubbing_id}", headers=_headers(), timeout=30)
        response.raise_for_status()
    except requests.RequestException as error:
        raise DubbingError(f"ElevenLabs request failed: {error}") from None
    return response.json()


def open_dub_audio(dubbing_id: str, lang_code: str) -> requests.Response:
    """Start streaming the finished audio; the caller reads response.iter_content().

    The status code is checked by the caller, so the web app can map 401/403/404
    to friendly HTTP responses and the CLI to a clean error message.
    """
    url = f"{API_BASE}/dubbing/{dubbing_id}/audio/{lang_code}"
    try:
        return requests.get(url, headers=_headers(), stream=True, timeout=120)
    except requests.RequestException as error:
        raise DubbingError(f"Could not reach ElevenLabs: {error}") from None
