const POLL_MS = 8000;

const els = {
  form: document.querySelector("#dub-form"),
  passwordField: document.querySelector("#password-field"),
  password: document.querySelector("#password"),
  url: document.querySelector("#url"),
  to: document.querySelector("#to"),
  sourceLang: document.querySelector("#source-lang"),
  speakers: document.querySelector("#speakers"),
  noClone: document.querySelector("#no-clone"),
  watermark: document.querySelector("#watermark"),
  submit: document.querySelector("#submit"),
  setupWarning: document.querySelector("#setup-warning"),
  progress: document.querySelector("#progress"),
  statusLine: document.querySelector("#status-line"),
  statusDetail: document.querySelector("#status-detail"),
  result: document.querySelector("#result"),
  player: document.querySelector("#player"),
  download: document.querySelector("#download"),
  mp4Block: document.querySelector("#mp4-block"),
  mp4Button: document.querySelector("#mp4-button"),
  mp4Progress: document.querySelector("#mp4-progress"),
  mp4Status: document.querySelector("#mp4-status"),
  mp4Result: document.querySelector("#mp4-result"),
  mp4Player: document.querySelector("#mp4-player"),
  mp4Download: document.querySelector("#mp4-download"),
  error: document.querySelector("#error"),
  tabForm: document.querySelector("#tab-form"),
  tabChat: document.querySelector("#tab-chat"),
  chatPanel: document.querySelector("#chat-panel"),
  chatMessages: document.querySelector("#chat-messages"),
  chatForm: document.querySelector("#chat-form"),
  chatInput: document.querySelector("#chat-input"),
  chatSend: document.querySelector("#chat-send"),
};

const state = {
  password: localStorage.getItem("translator-password") || "",
  passwordRequired: false,
  jobId: null,
  target: null,
  source: null,
  filename: null,
  expected: 0,
  startedAt: 0,
};

const STATUS_LABELS = {
  queued: "Queued on ElevenLabs…",
  processing: "Dubbing the speech…",
  dubbing: "Dubbing the speech…",
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (state.password) headers["x-app-password"] = state.password;

  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const data = await response.json();
      if (data.detail) {
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
      }
    } catch {
      // Not JSON; keep the generic message.
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response;
}

function fillLanguages(select, languages, includeAuto) {
  select.innerHTML = "";
  if (includeAuto) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Detect automatically";
    select.append(option);
  }
  for (const language of languages) {
    const option = document.createElement("option");
    option.value = language.code;
    option.textContent = language.name;
    select.append(option);
  }
}

function showError(message) {
  const hints = [
    [/watermark_not_allowed/i, 'Tick "Allow a watermark" under advanced options, or use a plan with watermark-free dubbing.'],
    [/quota_exceeded/i, "Your ElevenLabs credits ran out. Add credits or use a shorter video."],
    [/dubbing_write/i, "Enable the Dubbing write permission on your ElevenLabs API key."],
    [/no api key/i, "The server is missing ELEVENLABS_API_KEY. Add it in Vercel's project settings."],
  ];
  const hint = hints.find(([pattern]) => pattern.test(message));
  els.error.textContent = hint ? `${message} ${hint[1]}` : message;
  els.error.classList.remove("hidden");
}

function clearError() {
  els.error.classList.add("hidden");
  els.error.textContent = "";
}

function setBusy(busy) {
  els.submit.disabled = busy;
  els.submit.textContent = busy ? "Working…" : "Translate";
}

function showProgress(line, detail = "") {
  clearError();
  els.progress.classList.remove("hidden");
  els.result.classList.add("hidden");
  els.statusLine.textContent = line;
  els.statusDetail.textContent = detail;
}

function progressDetail() {
  const parts = [`${elapsedSeconds()}s elapsed`];
  if (state.expected) parts.push(`about ${Math.round(state.expected)}s of audio`);
  if (state.jobId) parts.push(`job ${state.jobId.slice(0, 8)}`);
  return parts.join(" • ");
}

function elapsedSeconds() {
  return Math.max(0, Math.round((Date.now() - state.startedAt) / 1000));
}

function saveJob() {
  localStorage.setItem("translator-job", JSON.stringify({
    id: state.jobId,
    target: state.target,
    source: state.source,
    filename: state.filename,
    expected: state.expected,
    startedAt: state.startedAt,
  }));
}

function clearJob() {
  localStorage.removeItem("translator-job");
}

function capturePassword() {
  const typedPassword = els.password.value.trim();
  if (typedPassword) {
    state.password = typedPassword;
    localStorage.setItem("translator-password", typedPassword);
  }
}

function handleError(error) {
  if (error.status === 401) {
    els.passwordField.classList.remove("hidden");
    showError("Wrong or missing password. Enter the shared password and try again.");
    return;
  }
  showError(error.message || String(error));
}

async function startJob(event) {
  event.preventDefault();
  clearError();

  const url = els.url.value.trim();
  if (!/^https?:\/\//i.test(url)) {
    showError("Enter a full video URL starting with http:// or https://");
    return;
  }

  capturePassword();

  state.target = els.to.value;
  state.source = url;
  state.filename = null;
  setBusy(true);
  showProgress("Creating the dubbing job…");

  try {
    const response = await api("/api/dub", {
      method: "POST",
      body: JSON.stringify({
        url,
        to: state.target,
        source_lang: els.sourceLang.value || null,
        speakers: Number(els.speakers.value) || 0,
        no_clone: els.noClone.checked,
        watermark: els.watermark.checked,
      }),
    });
    const job = await response.json();
    state.jobId = job.dubbing_id;
    state.filename = job.filename;
    state.expected = job.expected_duration_sec || 0;
    state.startedAt = Date.now();
    showProgress("Waiting for ElevenLabs…", progressDetail());
    saveJob();
    poll();
  } catch (error) {
    setBusy(false);
    els.progress.classList.add("hidden");
    handleError(error);
  }
}

async function poll() {
  try {
    const response = await api(`/api/dub/${state.jobId}`);
    const job = await response.json();
    const status = job.status || "processing";

    if (status === "dubbed") {
      els.statusLine.textContent = "Downloading the dubbed audio…";
      els.statusDetail.textContent = progressDetail();
      await fetchAudio();
      return;
    }
    if (status === "failed") {
      throw new Error("ElevenLabs could not dub this source. Check the job on elevenlabs.io, then try again.");
    }

    els.statusLine.textContent = STATUS_LABELS[status] || `Status: ${status}`;
    els.statusDetail.textContent = progressDetail();
    setTimeout(poll, POLL_MS);
  } catch (error) {
    setBusy(false);
    els.progress.classList.add("hidden");
    handleError(error);
  }
}

async function fetchAudio() {
  const query = new URLSearchParams({ lang: state.target });
  if (state.filename) query.set("filename", state.filename);
  const response = await api(`/api/dub/${state.jobId}/audio?${query}`);

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  els.player.src = objectUrl;
  els.download.href = objectUrl;
  els.download.download = state.filename || "translated.mp3";

  els.progress.classList.add("hidden");
  els.result.classList.remove("hidden");
  setBusy(false);
  clearJob();
  resetMp4();
}

function restoreJob() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem("translator-job") || "null");
  } catch {
    // Corrupt state; ignore it.
  }
  if (!saved || !saved.id) return;

  state.jobId = saved.id;
  state.target = saved.target;
  state.source = saved.source || null;
  state.filename = saved.filename;
  state.expected = saved.expected || 0;
  state.startedAt = saved.startedAt || Date.now();
  setBusy(true);
  showProgress("Checking on your previous job…", progressDetail());
  poll();
}

async function init() {
  try {
    const response = await fetch("/api/config");
    const body = await response.text();
    let config = null;
    try {
      config = JSON.parse(body);
    } catch {
      // Not JSON — most likely an error page from the host.
    }
    if (!response.ok || !config || !Array.isArray(config.languages)) {
      const snippet = body.slice(0, 120).replace(/\s+/g, " ").trim();
      throw new Error(`HTTP ${response.status}${snippet ? ` — ${snippet}` : ""}`);
    }
    state.passwordRequired = config.password_required;

    if (state.passwordRequired) {
      els.passwordField.classList.remove("hidden");
      els.password.value = state.password;
    }
    if (!config.api_key_set) {
      els.setupWarning.textContent = "The server has no ElevenLabs API key yet. Add ELEVENLABS_API_KEY in the Vercel project settings, then redeploy.";
      els.setupWarning.classList.remove("hidden");
    }

    fillLanguages(els.to, config.languages, false);
    if (!config.languages.some((language) => language.code === "hi")) {
      els.to.value = config.languages[0]?.code || "";
    } else {
      els.to.value = "hi";
    }
    fillLanguages(els.sourceLang, config.languages, true);
    if (config.agent_enabled) {
      els.tabChat.classList.remove("hidden");
    }
  } catch (error) {
    showError(`Could not load the app configuration (${error.message}). If you just deployed, check the function in the Vercel dashboard.`);
  }

  restoreJob();
}

let chatHistory = [];

function setMode(mode) {
  const useChat = mode === "chat";
  els.form.classList.toggle("hidden", useChat);
  els.chatPanel.classList.toggle("hidden", !useChat);
  els.tabForm.classList.toggle("active", !useChat);
  els.tabChat.classList.toggle("active", useChat);
  els.tabForm.setAttribute("aria-selected", String(!useChat));
  els.tabChat.setAttribute("aria-selected", String(useChat));
  if (useChat) els.chatInput.focus();
}

function appendMessage(role, text) {
  const message = document.createElement("div");
  message.className = `msg ${role}`;
  message.textContent = text;
  els.chatMessages.append(message);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
  return message;
}

async function sendChat(event) {
  event.preventDefault();
  clearError();

  const text = els.chatInput.value.trim();
  if (!text) return;
  capturePassword();

  els.chatInput.value = "";
  appendMessage("user", text);
  chatHistory.push({ role: "user", content: text });

  els.chatSend.disabled = true;
  els.chatSend.textContent = "Thinking…";
  const pending = appendMessage("assistant", "…");
  pending.classList.add("pending");

  try {
    const response = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ messages: chatHistory.slice(-16) }),
    });
    const data = await response.json();

    pending.classList.remove("pending");
    pending.textContent = data.reply || "(no reply)";
    els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
    chatHistory.push({ role: "assistant", content: data.reply || "" });

    if (data.job && data.job.dubbing_id) {
      // The agent started a job; reuse the same polling/download flow as the form.
      state.jobId = data.job.dubbing_id;
      state.target = data.job.target_lang;
      state.source = data.job.source || null;
      state.filename = data.job.filename;
      state.expected = data.job.expected_duration_sec || 0;
      state.startedAt = Date.now();
      saveJob();
      showProgress("Waiting for ElevenLabs…", progressDetail());
      poll();
    }
  } catch (error) {
    pending.remove();
    handleError(error);
  } finally {
    els.chatSend.disabled = false;
    els.chatSend.textContent = "Send";
  }
}

els.tabForm.addEventListener("click", () => setMode("form"));
els.tabChat.addEventListener("click", () => setMode("chat"));
els.chatForm.addEventListener("submit", sendChat);

els.form.addEventListener("submit", startJob);
setInterval(() => {
  if (!els.progress.classList.contains("hidden") && state.startedAt) {
    els.statusDetail.textContent = progressDetail();
  }
}, 1000);

const MP4_STAGES = {
  audio: "Fetching the translated audio…",
  downloading: "Downloading the source video…",
  processing: "Finishing the video download…",
  merging: "Merging the video and audio…",
  uploading: "Uploading the finished MP4…",
};

function resetMp4() {
  els.mp4Block.classList.toggle("hidden", !state.source);
  els.mp4Button.classList.remove("hidden");
  els.mp4Button.disabled = false;
  els.mp4Button.textContent = "Create MP4 video";
  els.mp4Progress.classList.add("hidden");
  els.mp4Result.classList.add("hidden");
  els.mp4Status.textContent = "";
  els.mp4Player.removeAttribute("src");
}

function setMp4Stage(text) {
  els.mp4Progress.classList.remove("hidden");
  els.mp4Status.textContent = text;
}

function showMp4(data) {
  els.mp4Progress.classList.add("hidden");
  els.mp4Result.classList.remove("hidden");
  els.mp4Button.classList.add("hidden");
  els.mp4Player.src = data.url;
  els.mp4Download.href = data.download_url || data.url;
  els.mp4Download.download = data.filename || "translated.mp4";
  const megabytes = data.size ? ` (${(data.size / 1048576).toFixed(1)} MB)` : "";
  els.mp4Download.textContent = `Download MP4${megabytes}`;
}

async function createVideo() {
  if (!state.jobId || !state.source) return;
  clearError();
  els.mp4Button.disabled = true;
  els.mp4Button.textContent = "Building the video…";
  setMp4Stage("Starting…");

  try {
    const headers = { "Content-Type": "application/json" };
    if (state.password) headers["x-app-password"] = state.password;

    // The server streams newline-delimited JSON: stage updates, then exactly
    // one final {"done": ...} or {"error": ...} line.
    const response = await fetch(`/api/dub/${state.jobId}/video`, {
      method: "POST",
      headers,
      body: JSON.stringify({ source: state.source, to: state.target }),
    });
    if (!response.ok || !response.body) {
      let detail = `Request failed (${response.status})`;
      try {
        const data = await response.json();
        if (data.detail) {
          detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
        }
      } catch {
        // Not JSON; keep the generic message.
      }
      throw new Error(detail);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.error) throw new Error(event.error);
        if (event.done) {
          finished = event;
        } else if (event.stage) {
          const label = MP4_STAGES[event.stage] || "Working…";
          setMp4Stage(event.percent === null || event.percent === undefined ? label : `${label} ${event.percent}%`);
        }
      }
    }
    if (!finished) {
      throw new Error("The server closed the connection before finishing. Try again, or merge locally: python cli.py merge");
    }
    showMp4(finished);
  } catch (error) {
    resetMp4();
    handleError(error);
  }
}

els.mp4Button.addEventListener("click", createVideo);

init();
