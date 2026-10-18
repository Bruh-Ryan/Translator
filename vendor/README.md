# vendor/

Pre-built helper binaries that the web app ships so it works on hosts with
nothing installed (Vercel).

## qjs / qjs.exe

[QuickJS-NG](https://github.com/quickjs-ng/quickjs) v0.17.0 — a small JavaScript
engine (MIT license). yt-dlp needs an external JavaScript runtime to solve
YouTube's player challenges since 2026; without one, YouTube downloads fail.

- `qjs` — Linux x86_64, used inside the Vercel function.
- `qjs.exe` — Windows x86_64, used locally when Node.js (>= 22) is not on PATH.

Sources:

- https://github.com/quickjs-ng/quickjs/releases/download/v0.17.0/qjs-linux-x86_64
- https://github.com/quickjs-ng/quickjs/releases/download/v0.17.0/qjs-windows-x86_64.exe

`video_job.js_runtime_args()` picks whichever runtime is available, in order:
`YTDLP_JS_RUNTIME` environment variable, `node` on PATH, this bundled `qjs`,
then `qjs` on PATH.
