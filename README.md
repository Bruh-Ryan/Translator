# Video Translator

Download a video-only source from YouTube, translate its spoken content with the ElevenLabs Dubbing API, then replace the video's audio with the translated track.

There are two front ends for that flow: the `cli.py` command line, and a small FastAPI web page (`app.py` + `public/`) that runs the dubbing step in a browser and deploys to Vercel.

## What it does

1. Downloads a **video-only** source into `video/`.
2. Sends a source URL or media file to ElevenLabs Dubbing.
3. Saves the translated MP3 in `translation/`.
4. Lets you merge the original video and translated audio with FFmpeg.

ElevenLabs controls the dubbing timing and delivery. For recognizable voice preservation, use the service only with the speaker's authorization.

## Requirements

- Python 3.10 or later
- [FFmpeg](https://ffmpeg.org/download.html), available on your `PATH` (required for the final MP4 export)
- An ElevenLabs account, API key, and sufficient Dubbing credits
- An ElevenLabs API key with the `dubbing_write` permission

Watermark-free dubbing requires an ElevenLabs plan that supports it. Accounts without that entitlement can use the `--watermark` option.

## Installation

```powershell
git clone https://github.com/Bruh-Ryan/Translator
cd Translator
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
ELEVENLABS_API_KEY=your_elevenlabs_api_key
```

Never commit this file. It is already excluded by `.gitignore`.

## Web app

The dubbing step also runs as a small web page: paste a video URL, pick a language, then play or download the translated MP3. It is built for Vercel, where video downloads and FFmpeg are not available, so the page produces the MP3 only.

Run it locally (it reads `.env` like the CLI does):

```powershell
uvicorn app:app --reload
```

Then open http://127.0.0.1:8000 in a browser.

### Deploy to Vercel

1. Import this repository at [vercel.com/new](https://vercel.com/new). Vercel detects FastAPI automatically; keep the default settings.
2. In **Settings → Environment Variables**, add:
   - `ELEVENLABS_API_KEY` — your API key (`.env` is never deployed).
   - `APP_PASSWORD` — optional. When set, visitors must type this shared password before the page can spend any credits.
3. Deploy, or redeploy after adding the variables.

The page creates an ElevenLabs job, then polls it from the browser until the audio is ready — no long-running server process and no storage needed.

### Web app limits

- Vercel caps function responses at about 4.5 MB, so very long videos may not download through the browser. Use the CLI for those.
- Video downloads, file uploads, and the final MP4 merge stay on your machine.
- Unless `APP_PASSWORD` is set, anyone who can open the URL can spend your ElevenLabs credits.

## Usage

### 1. Download the source video

This downloads a video-only file to `video/`, keeping it ready for the final export:

```powershell
python cli.py download "https://youtu.be/VIDEO_ID"
```

### 2. Create a translated dub

Use the original YouTube URL as the dubbing source because the local file in `video/` intentionally has no audio.

```powershell
python cli.py dub "https://youtu.be/VIDEO_ID" --from hi --to en --speakers 1 --watermark
```

The translated MP3 is written to `translation/`.

Common options:

```text
--from hi          Source language. Omit to let ElevenLabs detect it.
--to en            Target language. Names such as hindi, spanish, tamil, and english also work.
--speakers 1       Number of speakers. Use 0 (the default) for automatic detection.
--watermark        Allow an ElevenLabs watermark when required by the account plan.
--no-clone         Use a similar library voice instead of cloning the source speaker.
--out FILE.mp3     Choose a custom output path.
```

See all commands:

```powershell
python cli.py -h
python cli.py dub -h
```

### 3. Export the translated video

Replace the paths with the filenames created in `video/` and `translation/`:

```powershell
ffmpeg -i "video\source.mp4" `
  -i "translation\source [English].mp3" `
  -map 0:v:0 -map 1:a:0 `
  -c:v copy -c:a aac -b:a 192k `
  -shortest `
  "translation\source [English].mp4"
```

`-c:v copy` keeps the original video stream without re-encoding it. The final MP4 is saved in `translation/`.

## Project layout

```text
translator/
├── cli.py             # Command line: download sources and dub them
├── core.py            # Shared ElevenLabs dubbing logic
├── app.py             # FastAPI web app (the Vercel entrypoint)
├── public/            # Frontend: index.html, style.css, app.js
├── requirements.txt
├── .env               # Your local API key (not committed)
├── video/             # Downloaded video-only sources (not committed)
└── translation/       # Generated MP3/MP4 translations (not committed)
```

## Troubleshooting

| Error | Solution |
| --- | --- |
| `missing permission dubbing_write` | Create or edit the ElevenLabs key and enable its Dubbing write permission. |
| `watermark_not_allowed` | Pass `--watermark`, or use an account plan with watermark-free dubbing. |
| `quota_exceeded` | Dub a shorter source or add ElevenLabs credits. |
| `No space left on device` | Clear disk space or set `VIDEO_DIR` / `TRANSLATION_DIR` to a drive with space. |
| `Wrong or missing password` | Enter the `APP_PASSWORD` you set in Vercel, or remove that variable to leave the page open. |
| `The server has no ElevenLabs API key yet` | Add `ELEVENLABS_API_KEY` in Vercel → Settings → Environment Variables, then redeploy. |
| Vercel asks for a login before the page loads | Deployment Protection is on (Vercel → Settings → Deployment Protection). Turn it off, or log in with the Vercel account that owns the project. |

## Configuration

By default, files are stored under the project directory. Override either location for a single PowerShell session:

```powershell
$env:VIDEO_DIR = "E:\VideoTranslator\video"
$env:TRANSLATION_DIR = "E:\VideoTranslator\translation"
```

## Notes

- Dubbing cost depends on the source duration. Check your ElevenLabs credits before processing long videos.
- The duration of translated speech can differ slightly from the original because languages have different pacing and word lengths.
- Respect copyright, platform terms, and the speaker's voice and consent rights.
