# Video Translator

Download a video-only source from YouTube, translate its spoken content with the ElevenLabs Dubbing API, then replace the video's audio with the translated track.

There are three front doors for that flow: the `cli.py` command line, a small FastAPI web page (`app.py` + `public/`) that runs the whole pipeline in a browser — MP3 *and* the merged MP4 — and deploys to Vercel, and an MCP server (`mcp_server.py`) that exposes the pipeline as tools for local agents such as OpenCode.

## What it does

1. Downloads a **video-only** source into `video/`.
2. Sends a source URL or media file to ElevenLabs Dubbing.
3. Saves the translated MP3 in `translation/`.
4. Lets you merge the original video and translated audio with FFmpeg.

ElevenLabs controls the dubbing timing and delivery. For recognizable voice preservation, use the service only with the speaker's authorization.

## Requirements

- Python 3.10 or later
- [FFmpeg](https://ffmpeg.org/download.html), available on your `PATH` (required for the final MP4 export)
- For YouTube downloads: Node.js 22+ if present, otherwise the bundled `vendor/qjs` is used automatically as the JavaScript runtime `yt-dlp` needs
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

The whole flow also runs as a small web page: paste a video URL, pick a language, then play or download the translated MP3. When that is ready, a **Create MP4 video** button downloads the source video, merges it with the translated audio, and stores the result on Vercel Blob — preview and download the finished video right from the page.

Run it locally (it reads `.env` like the CLI does):

```powershell
uvicorn app:app --reload
```

Then open http://127.0.0.1:8000 in a browser.

### Chat assistant (optional)

With an LLM key set, the page shows a **Chat** tab next to the form. Instead of filling in fields, you can write "dub this into Tamil: https://youtu.be/…" and the assistant works out the language and options itself, asks follow-up questions when something is missing, and starts the same dubbing job the form would. Without a key the tab stays hidden and the page behaves exactly as before.

Set the variables in `.env` (locally) or in the Vercel project settings:

```env
LLM_API_KEY=your_llm_api_key
# Optional:
LLM_MODEL=gpt-4.1-mini                       # default model
LLM_BASE_URL=https://openrouter.ai/api/v1    # any OpenAI-compatible endpoint
```

The chat only starts the job; the MP3 and the MP4 button then arrive through the same flow as the form.

### Deploy to Vercel

1. Import this repository at [vercel.com/new](https://vercel.com/new). Vercel detects FastAPI automatically; keep the default settings.
2. In **Settings → Environment Variables**, add:
   - `ELEVENLABS_API_KEY` — your API key (`.env` is never deployed).
   - `APP_PASSWORD` — optional. When set, visitors must type this shared password before the page can spend any credits.
   - `LLM_API_KEY` (plus optional `LLM_MODEL`, `LLM_BASE_URL`) — optional. Enables the chat assistant; see above.
   - `BLOB_READ_WRITE_TOKEN` — needed for the MP4 button. Create a Blob store in the **Storage** tab and connect it to this project; Vercel adds the variable automatically.
3. Deploy, or redeploy after adding the variables.

The page creates an ElevenLabs job, then polls it from the browser until the audio is ready. Building the MP4 is one longer call (usually under two minutes) that streams its progress to the page.

### Web app limits

- The MP4 build downloads the source video from Vercel's servers, so YouTube has to allow it. Shorts and ordinary videos usually work; if YouTube refuses (bot check), the error message suggests the CLI instead.
- The web version handles clips up to about three minutes (`MAX_VIDEO_SECONDS`, default 180) and 1080p (`MAX_VIDEO_HEIGHT`). The CLI has no such limits.
- Vercel caps a single function response at about 4.5 MB, which is why finished MP4s are stored on Vercel Blob. Keep the Blob store connected, or the MP4 step stops with a clear message.
- Unless `APP_PASSWORD` is set, anyone who can open the URL can spend your ElevenLabs credits.
- The chat assistant adds a language-model round trip to each message. If a turn times out on Vercel, raise the function's max duration in the Vercel project settings.

## MCP server (local agents)

`mcp_server.py` exposes the local pipeline as MCP tools, so an agent such as OpenCode or Claude Desktop can run it on your machine — download, dub, wait, save, and merge.

Run it directly (it speaks MCP over stdio):

```powershell
python mcp_server.py
```

Connect it in OpenCode from this project:

```powershell
opencode mcp add translator -- .venv\Scripts\python.exe mcp_server.py
```

Or add it to `opencode.json` by hand:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "translator": {
        "type": "local",
        "command": [".venv/Scripts/python.exe", "mcp_server.py"],
        "cwd": "."
      }
    }
  }
}
```

Tools: `list_languages`, `download_video`, `start_dub`, `dub_status`, `save_dub_audio`, `dub_and_wait`, `merge_video`, and `translate_video` (one call for the whole download → dub → merge run).

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
--merge            Also download the video and save the final MP4 in one step.
```

See all commands:

```powershell
python cli.py -h
python cli.py dub -h
```

### 3. Export the translated video

Download the source video if you have not already (the copy in `video/` has no audio by design):

```powershell
python cli.py download "https://youtu.be/VIDEO_ID"
```

Then merge it with the translated MP3:

```powershell
python cli.py merge
```

`merge` picks the newest video in `video/` and the newest MP3 in `translation/` (falling back to the newest MP3 anywhere in the project) and writes the MP4 next to the audio. Pass the files explicitly when the automatic pick is not the one you want:

```powershell
python cli.py merge --video "video\source.mp4" --audio "translation\source [English].mp3" --out "translation\final.mp4"
```

Add `--force` to overwrite an existing output.

You can also do everything in one command by adding `--merge` to `dub`; it downloads the video and merges as soon as the dub finishes:

```powershell
python cli.py dub "https://youtu.be/VIDEO_ID" --to en --merge
```

#### The same merge with plain FFmpeg

Replace the paths with your own filenames:

```powershell
ffmpeg -i "video\source.mp4" `
  -i "translation\source [English].mp3" `
  -map 0:v:0 -map 1:a:0 `
  -c:v copy -c:a aac -b:a 192k `
  -shortest `
  "translation\source [English].mp4"
```

`-c:v copy` keeps the original video stream without re-encoding it. The translated audio replaces the original track entirely, because the downloaded source has no audio.

## Project layout

```text
translator/
├── cli.py             # Command line: download sources, dub, merge
├── core.py            # Shared ElevenLabs dubbing logic
├── app.py             # FastAPI web app (the Vercel entrypoint)
├── video_job.py       # Server-side MP4 build: yt-dlp + FFmpeg + Vercel Blob
├── agent.py           # Chat assistant for app.py (needs LLM_API_KEY)
├── mcp_server.py      # MCP tools for local agents (OpenCode, Claude Desktop)
├── public/            # Frontend: index.html, style.css, app.js
├── requirements.txt
├── vercel.json        # Function settings (300s max duration) on Vercel
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
| `The chat agent is not configured` | Set `LLM_API_KEY` (plus optional `LLM_MODEL`, `LLM_BASE_URL`) in the environment and restart or redeploy. |
| `The language model request failed` | Check `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL`; the endpoint must be OpenAI-compatible. |
| `The server has no ElevenLabs API key yet` | Add `ELEVENLABS_API_KEY` in Vercel → Settings → Environment Variables, then redeploy. |
| `BLOB_READ_WRITE_TOKEN is missing` | Create a Blob store in Vercel's **Storage** tab and connect it to the project, then redeploy. |
| `YouTube refused the download from this server` | Vercel's data-centre address was bot-checked. Try again, use another video, or merge on your machine: `python cli.py merge`. |
| Vercel asks for a login before the page loads | Deployment Protection is on (Vercel → Settings → Deployment Protection). Turn it off, or log in with the Vercel account that owns the project. |

## Configuration

By default, files are stored under the project directory. Override either location for a single PowerShell session:

```powershell
$env:VIDEO_DIR = "E:\VideoTranslator\video"
$env:TRANSLATION_DIR = "E:\VideoTranslator\translation"
```

The web app's chat assistant reads `LLM_API_KEY`, `LLM_MODEL` (default: `gpt-4.1-mini`), and `LLM_BASE_URL` from the same environment. The MP4 step reads `BLOB_READ_WRITE_TOKEN` (where finished videos are stored) plus the optional `MAX_VIDEO_SECONDS` (default `180`) and `MAX_VIDEO_HEIGHT` (default `1080`).

## Notes

- Dubbing cost depends on the source duration. Check your ElevenLabs credits before processing long videos.
- The duration of translated speech can differ slightly from the original because languages have different pacing and word lengths.
- Respect copyright, platform terms, and the speaker's voice and consent rights.
