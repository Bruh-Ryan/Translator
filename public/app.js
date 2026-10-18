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
  error: document.querySelector("#error"),
};

const state = {
  password: localStorage.getItem("translator-password") || "",
  passwordRequired: false,
  jobId: null,
  target: null,
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
    filename: state.filename,
    expected: state.expected,
    startedAt: state.startedAt,
  }));
}

function clearJob() {
  localStorage.removeItem("translator-job");
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

  const typedPassword = els.password.value.trim();
  if (typedPassword) {
    state.password = typedPassword;
    localStorage.setItem("translator-password", typedPassword);
  }

  state.target = els.to.value;
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
  } catch (error) {
    showError(`Could not load the app configuration (${error.message}). If you just deployed, check the function in the Vercel dashboard.`);
  }

  restoreJob();
}

els.form.addEventListener("submit", startJob);
setInterval(() => {
  if (!els.progress.classList.contains("hidden") && state.startedAt) {
    els.statusDetail.textContent = progressDetail();
  }
}, 1000);

init();
