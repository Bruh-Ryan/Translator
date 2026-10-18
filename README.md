# Video Translator

Download a video-only source from YouTube, translate its spoken content with the ElevenLabs Dubbing API, then replace the video's audio with the translated track.

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
git clone <your-repository-url>
cd translator
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```env
ELEVENLABS_API_KEY=your_elevenlabs_api_key
```

Never commit this file. It is already excluded by `.gitignore`.

## Usage

### 1. Download the source video

This downloads a video-only file to `video/`, keeping it ready for the final export:

```powershell
python main.py download "https://youtu.be/VIDEO_ID"
```

### 2. Create a translated dub

Use the original YouTube URL as the dubbing source because the local file in `video/` intentionally has no audio.

```powershell
python main.py dub "https://youtu.be/VIDEO_ID" --from hi --to en --speakers 1 --watermark
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
python main.py -h
python main.py dub -h
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
├── main.py            # CLI downloader and ElevenLabs Dubbing client
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
