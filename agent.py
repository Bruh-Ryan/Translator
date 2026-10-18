"""Chat assistant for the web app: turns a conversation into a dubbing job.

The model never talks to ElevenLabs directly. It only picks which tool to call
and with what arguments; the tools are the same functions the form uses
(core.create_dub / core.get_dub), so both front doors behave identically.
The browser polls and downloads the MP3 exactly like it does for the form.

Configured by environment variables (all optional except the key):
  LLM_API_KEY   - API key for an OpenAI-compatible endpoint
  LLM_MODEL     - model name (default: gpt-4.1-mini)
  LLM_BASE_URL  - custom endpoint, e.g. a gateway or OpenRouter
"""

import json
import os

import core

MAX_ROUNDS = 4          # Tool-calling rounds before giving up on one turn.
HISTORY_LIMIT = 20      # Most recent messages sent back to the model.
MESSAGE_LIMIT = 4000    # Characters per message, same as the API model.

SYSTEM_PROMPT = f"""You are the assistant inside a video translation web app that uses the ElevenLabs Dubbing API.

Rules:
- Dubs can only be started from full http(s) video URLs. Never guess a URL: ask for it if missing. File uploads are not supported.
- Always ask for the target language if the user has not said it.
- Languages (name - code): {", ".join(f"{name} ({code})" for name, code in sorted(core.LANGUAGES.items()))}. Other ISO codes also work if the user gives one.
- Leave speakers at 0 (auto-detect) unless the user says how many people speak.
- Call start_dub once you have a URL and a target language. Do not call it more than once for the same request.
- If a call fails with watermark_not_allowed, retry the same call once with watermark=true and warn the user the dub will carry an ElevenLabs watermark.
- After a job starts, tell the user the first 8 characters of the job id, the expected audio length, and that the finished MP3 will appear on the page. Never invent job ids or statuses; only report what the tools return.
- This web app only produces the MP3. The final MP4 merge happens on the user's machine; mention `python cli.py merge` when it is relevant.
- Keep replies short (2-4 sentences) and answer in the language the user is writing in."""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_dub",
            "description": "Create an ElevenLabs dubbing job that translates the speech in a video URL into the target language.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full http(s) video URL to dub."},
                    "to": {"type": "string", "description": "Target language name or ISO code, e.g. 'hindi' or 'hi'."},
                    "source_lang": {"type": "string", "description": "Source language, only if the user states it."},
                    "speakers": {"type": "integer", "description": "Number of speakers, 0 = auto-detect."},
                    "no_clone": {"type": "boolean", "description": "Use a similar library voice instead of cloning the speaker."},
                    "watermark": {"type": "boolean", "description": "Allow an ElevenLabs watermark when the plan requires one."},
                },
                "required": ["url", "to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dub_status",
            "description": "Check the status of a dubbing job that was started earlier in the conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dubbing_id": {"type": "string", "description": "Job id returned by start_dub."},
                },
                "required": ["dubbing_id"],
            },
        },
    },
]


class AgentError(RuntimeError):
    """The chat request could not be completed (missing key, LLM unreachable)."""


def agent_enabled() -> bool:
    """True when an LLM key is configured; the page hides the chat tab otherwise."""
    return bool(_api_key())


def _api_key() -> str:
    return os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""


def _client():
    """OpenAI-compatible client; imported lazily so the app boots without the package."""
    try:
        from openai import OpenAI
    except ImportError:
        raise AgentError("The 'openai' package is not installed. Run: pip install openai") from None
    return OpenAI(api_key=_api_key(), base_url=os.getenv("LLM_BASE_URL") or None, timeout=60.0)


def _as_bool(value) -> bool:
    """Models sometimes send 'true'/'false' strings instead of JSON booleans."""
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _start_dub(args: dict) -> tuple[dict, dict | None]:
    """Run the start_dub tool. Returns (tool result, job payload) for the model and browser."""
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"error": "The source must be a full URL starting with http:// or https://."}, None
    try:
        target = core.resolve_language(str(args.get("to") or ""))
        source_lang = core.resolve_language(str(args["source_lang"])) if args.get("source_lang") else None
    except ValueError as error:
        return {"error": str(error)}, None

    try:
        speakers = int(args.get("speakers") or 0)
    except (TypeError, ValueError):
        speakers = 0

    try:
        job = core.create_dub(
            url, target, source_lang, max(0, min(20, speakers)),
            _as_bool(args.get("no_clone")), _as_bool(args.get("watermark")),
        )
    except core.DubbingError as error:
        return {"error": str(error)}, None

    if not job.get("dubbing_id"):
        return {"error": "ElevenLabs did not return a dubbing job id."}, None
    payload = core.job_payload(url, target, job)
    return payload, payload


def _dub_status(args: dict) -> tuple[dict, dict | None]:
    """Run the dub_status tool for jobs started in this conversation."""
    dubbing_id = str(args.get("dubbing_id") or "").strip()
    if not dubbing_id:
        return {"error": "A dubbing_id is required."}, None
    try:
        job = core.get_dub(dubbing_id)
    except core.DubbingError as error:
        return {"error": str(error)}, None
    return {
        "status": job.get("status"),
        "expected_duration_sec": job.get("expected_duration_sec") or 0,
    }, None


TOOL_RUNNERS = {
    "start_dub": _start_dub,
    "dub_status": _dub_status,
}


def run(messages: list[dict]) -> dict:
    """Run one agent turn. Returns {"reply": str, "job": dict | None}."""
    model = os.getenv("LLM_MODEL", "gpt-4.1-mini")

    chat = [{"role": "system", "content": SYSTEM_PROMPT}]
    for message in messages[-HISTORY_LIMIT:]:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()[:MESSAGE_LIMIT]
        if role in ("user", "assistant") and content:
            chat.append({"role": role, "content": content})
    if not chat or chat[-1]["role"] != "user":
        raise AgentError("The last message must come from the user.")

    job: dict | None = None
    client = _client()
    for _ in range(MAX_ROUNDS):
        try:
            completion = client.chat.completions.create(model=model, messages=chat, tools=TOOLS)
        except Exception as error:  # OpenAI SDK errors and network failures
            raise AgentError(f"The language model request failed: {error}") from None
        message = completion.choices[0].message

        if not message.tool_calls:
            reply = (message.content or "").strip()
            return {"reply": reply or "I could not produce a reply. Try rephrasing your request.", "job": job}

        chat.append(message)
        for call in message.tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            runner = TOOL_RUNNERS.get(call.function.name)
            result, found_job = runner(args) if runner else ({"error": f"Unknown tool: {call.function.name}"}, None)
            if found_job and job is None:
                job = found_job
            chat.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    # The model kept calling tools without answering; report what we have.
    if job:
        return {
            "reply": f"Started the dubbing job {job['dubbing_id'][:8]}. It will appear on the page when it is ready.",
            "job": job,
        }
    raise AgentError("The assistant could not finish the request. Please try rephrasing it.")
