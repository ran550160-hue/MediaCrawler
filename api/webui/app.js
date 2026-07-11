const state = {
  preset: "search",
  status: "idle",
  files: [],
  selectedFile: null,
  logSocket: null,
  statusSocket: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const XHS_URL = "https://www.xiaohongshu.com/explore";

function setStatusText(message) {
  $("#statusText").textContent = message;
}

function setTaskState(status) {
  state.status = status || "idle";
  const pill = $("#taskState");
  pill.textContent = state.status;
  pill.className = `state-dot ${state.status === "running" ? "running" : state.status === "error" ? "error" : "idle"}`;
}

function addLog(message, level = "info", timestamp = "") {
  const stream = $("#logStream");
  const prefix = timestamp ? `[${timestamp}]` : `[${new Date().toLocaleTimeString()}]`;
  stream.textContent += `${prefix} ${level.toUpperCase()} ${message}\n`;
  stream.scrollTop = stream.scrollHeight;
}

function apiPathForFile(path) {
  return path.split("/").map(encodeURIComponent).join("/");
}

function splitCsv(value) {
  return (value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function numberOrNull(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function getFormPayload() {
  const form = $("#crawlerForm");
  const data = new FormData(form);
  const payload = {
    platform: data.get("platform"),
    login_type: data.get("login_type"),
    crawler_type: state.preset,
    keywords: String(data.get("keywords") || "").trim(),
    specified_ids: String(data.get("specified_ids") || "").trim(),
    creator_ids: String(data.get("creator_ids") || "").trim(),
    start_page: Number(data.get("start_page") || 1),
    enable_comments: data.has("enable_comments"),
    enable_sub_comments: data.has("enable_sub_comments"),
    save_option: data.get("save_option"),
    cookies: String(data.get("cookies") || "").trim(),
    headless: data.has("headless"),
    max_notes_count: numberOrNull(data.get("max_notes_count")),
    max_comments_count: numberOrNull(data.get("max_comments_count")),
    enable_cdp_mode: data.has("enable_cdp_mode"),
    cdp_connect_existing: data.has("cdp_connect_existing"),
    cdp_debug_port: Number(data.get("cdp_debug_port") || 9222),
    save_data_path: String(data.get("save_data_path") || "").trim(),
    enable_ip_proxy: data.has("enable_ip_proxy"),
    ip_proxy_provider_name: data.get("ip_proxy_provider_name"),
    static_proxy_url: String(data.get("static_proxy_url") || "").trim(),
  };

  Object.keys(payload).forEach((key) => {
    if (payload[key] === null) {
      delete payload[key];
    }
  });
  return payload;
}

function updateModeFields() {
  $$(".segmented button").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === state.preset);
  });
  $$(".mode-field").forEach((field) => {
    field.classList.toggle("hidden", field.dataset.mode !== state.preset);
  });
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload);
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return payload;
}

function renderEnvChecks(payload) {
  $("#envSummary").textContent = payload.message || "检查完成";
  const container = $("#envChecks");
  container.innerHTML = "";
  (payload.checks || []).forEach((check) => {
    const item = document.createElement("div");
    item.className = `check ${check.status}`;
    const status = document.createElement("strong");
    status.textContent = check.status;
    const body = document.createElement("div");
    const message = document.createElement("p");
    message.textContent = `${check.name}: ${check.message}`;
    body.appendChild(message);
    if (check.detail) {
      const detail = document.createElement("small");
      detail.textContent = check.detail;
      body.appendChild(detail);
    }
    item.append(status, body);
    container.appendChild(item);
  });
}

async function checkEnvironment() {
  const port = Number($("#crawlerForm").elements.cdp_debug_port.value || 9222);
  $("#checkEnvBtn").disabled = true;
  $("#envSummary").textContent = "检查中";
  try {
    const payload = await fetchJson(`/api/env/check?cdp_port=${encodeURIComponent(port)}`);
    renderEnvChecks(payload);
    setStatusText(payload.message);
  } catch (error) {
    $("#envSummary").textContent = "检查失败";
    addLog(error.message, "error");
  } finally {
    $("#checkEnvBtn").disabled = false;
  }
}

async function refreshCdpStatus() {
  const port = Number($("#crawlerForm").elements.cdp_debug_port.value || 9222);
  try {
    const status = await fetchJson(`/api/browser/cdp/status?port=${encodeURIComponent(port)}`);
    $("#cdpStatusText").textContent = status.reachable
      ? `CDP 已可用：${status.port}`
      : `CDP 未连接：${status.port}`;
    renderCdpProfile(status);
  } catch (error) {
    $("#cdpStatusText").textContent = `CDP 检测失败：${error.message}`;
  }
}

function renderCdpProfile(status) {
  const profile = status.user_data_dir || status.default_user_data_dir || "";
  if (profile && !$("#cdpUserDataDir").value.trim()) {
    $("#cdpUserDataDir").value = profile;
  }
  $("#cdpProfileText").textContent = profile
    ? `当前登录态目录：${profile}`
    : "用于保存小红书登录态，第一次扫码后会复用。";
}

async function startCdpBrowser() {
  const form = $("#crawlerForm");
  const port = Number(form.elements.cdp_debug_port.value || 9222);
  const userDataDir = $("#cdpUserDataDir").value.trim();
  $("#startCdpBtn").disabled = true;
  $("#cdpStatusText").textContent = "启动中";
  try {
    const result = await fetchJson("/api/browser/cdp/start", {
      method: "POST",
      body: JSON.stringify({
        port,
        headless: form.elements.headless.checked,
        user_data_dir: userDataDir,
        start_url: XHS_URL,
      }),
    });
    form.elements.cdp_debug_port.value = result.port;
    $("#cdpStatusText").textContent = `${result.message || "CDP 浏览器已启动"}`;
    renderCdpProfile(result);
    addLog(result.message || `CDP browser ready on ${result.port}`, "success");
    await checkEnvironment();
  } catch (error) {
    $("#cdpStatusText").textContent = "启动失败";
    addLog(error.message, "error");
  } finally {
    $("#startCdpBtn").disabled = false;
  }
}

async function openXhsPage() {
  const port = Number($("#crawlerForm").elements.cdp_debug_port.value || 9222);
  $("#openXhsBtn").disabled = true;
  try {
    const result = await fetchJson("/api/browser/cdp/open", {
      method: "POST",
      body: JSON.stringify({ port, url: XHS_URL }),
    });
    $("#cdpStatusText").textContent = result.message || "已打开小红书";
    renderCdpProfile(result);
    addLog(result.message || "Opened Xiaohongshu in CDP browser", "success");
  } catch (error) {
    $("#cdpStatusText").textContent = "打开失败";
    addLog(error.message, "error");
  } finally {
    $("#openXhsBtn").disabled = false;
  }
}

async function startCrawler(event) {
  event.preventDefault();
  $("#startBtn").disabled = true;
  try {
    const payload = getFormPayload();
    const result = await fetchJson("/api/crawler/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    addLog(result.message || "Crawler started", "success");
    setStatusText("采集已启动");
    setTaskState("running");
    syncExportDefaults();
  } catch (error) {
    addLog(error.message, "error");
    setStatusText("启动失败");
  } finally {
    $("#startBtn").disabled = false;
  }
}

async function stopCrawler() {
  $("#stopBtn").disabled = true;
  try {
    const result = await fetchJson("/api/crawler/stop", { method: "POST" });
    addLog(result.message || "Crawler stopped", "warning");
    setStatusText("采集已停止");
    setTaskState("idle");
    await loadFiles();
  } catch (error) {
    addLog(error.message, "error");
  } finally {
    $("#stopBtn").disabled = false;
  }
}

async function loadLogs() {
  try {
    const payload = await fetchJson("/api/crawler/logs?limit=100");
    $("#logStream").textContent = "";
    (payload.logs || []).forEach((entry) => addLog(entry.message, entry.level, entry.timestamp));
  } catch (error) {
    addLog(error.message, "error");
  }
}

async function loadFiles() {
  const fileType = $("#fileTypeFilter").value;
  const query = fileType ? `?file_type=${encodeURIComponent(fileType)}` : "";
  try {
    const payload = await fetchJson(`/api/data/files${query}`);
    state.files = payload.files || [];
    renderFileList();
    if (!state.selectedFile && state.files.length) {
      await previewFile(state.files[0]);
    }
  } catch (error) {
    addLog(error.message, "error");
  }
}

function formatSize(size) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function renderFileList() {
  const container = $("#fileList");
  container.innerHTML = "";
  if (!state.files.length) {
    container.innerHTML = '<div class="muted">暂无数据文件</div>';
    $("#previewTitle").textContent = "未选择文件";
    $("#previewTable").innerHTML = "";
    $("#downloadLink").classList.add("hidden");
    return;
  }

  state.files.forEach((file) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `file-item ${state.selectedFile && state.selectedFile.path === file.path ? "active" : ""}`;
    button.innerHTML = `
      <strong></strong>
      <small></small>
      <small></small>
    `;
    button.querySelector("strong").textContent = file.name;
    const meta = `${file.type.toUpperCase()} · ${formatSize(file.size)} · ${file.record_count ?? "-"} 条`;
    button.querySelectorAll("small")[0].textContent = meta;
    button.querySelectorAll("small")[1].textContent = file.path;
    button.addEventListener("click", () => previewFile(file));
    container.appendChild(button);
  });
}

async function previewFile(file) {
  state.selectedFile = file;
  renderFileList();
  $("#previewTitle").textContent = file.name;
  const encodedPath = apiPathForFile(file.path);
  $("#downloadLink").href = `/api/data/download/${encodedPath}`;
  $("#downloadLink").classList.remove("hidden");
  $("#previewTable").innerHTML = '<div class="muted">加载中</div>';
  try {
    const payload = await fetchJson(`/api/data/files/${encodedPath}?preview=true&limit=100`);
    renderPreview(payload.data, payload.columns);
  } catch (error) {
    $("#previewTable").innerHTML = "";
    const message = document.createElement("div");
    message.className = "muted";
    message.textContent = error.message;
    $("#previewTable").appendChild(message);
  }
}

function renderPreview(data, declaredColumns) {
  const container = $("#previewTable");
  container.innerHTML = "";
  const rows = Array.isArray(data) ? data : [data];
  if (!rows.length) {
    container.innerHTML = '<div class="muted">没有可预览记录</div>';
    return;
  }

  const columns = declaredColumns && declaredColumns.length
    ? declaredColumns
    : Array.from(rows.reduce((set, row) => {
        Object.keys(row || {}).slice(0, 24).forEach((key) => set.add(key));
        return set;
      }, new Set()));

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  columns.forEach((column) => {
    const th = document.createElement("th");
    th.textContent = column;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach((column) => {
      const td = document.createElement("td");
      const value = row ? row[column] : "";
      td.textContent = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value ?? "");
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  container.appendChild(table);
}

function connectSockets() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const base = `${protocol}://${window.location.host}`;

  try {
    state.logSocket = new WebSocket(`${base}/api/ws/logs`);
    state.logSocket.onmessage = (event) => {
      const entry = JSON.parse(event.data);
      addLog(entry.message, entry.level, entry.timestamp);
    };
  } catch (error) {
    addLog(error.message, "warning");
  }

  try {
    state.statusSocket = new WebSocket(`${base}/api/ws/status`);
    state.statusSocket.onmessage = async (event) => {
      const status = JSON.parse(event.data);
      const previousStatus = state.status;
      setTaskState(status.status || "idle");
      if (status.status === "idle" && previousStatus === "running") {
        await loadFiles();
      }
    };
  } catch (error) {
    addLog(error.message, "warning");
  }
}

function syncExportDefaults() {
  const formData = new FormData($("#crawlerForm"));
  const keywords = String(formData.get("keywords") || "").trim();
  if (keywords) {
    $("#exportForm").elements.keywords.value = keywords;
  }
  const platform = formData.get("platform");
  const presetLabels = { search: "关键词搜索", detail: "指定内容", creator: "创作者主页" };
  $("#exportForm").elements.name.value = `MediaCrawler ${platform} ${presetLabels[state.preset]}`;
}

async function exportDataset(event) {
  event.preventDefault();
  const crawlerData = new FormData($("#crawlerForm"));
  const exportData = new FormData($("#exportForm"));
  const platform = crawlerData.get("platform");
  if (platform !== "xhs") {
    $("#exportResult").textContent = "当前仅支持小红书数据集导出";
    return;
  }

  $("#exportBtn").disabled = true;
  $("#exportResult").textContent = "整理中";
  try {
    const payload = {
      name: String(exportData.get("name") || "").trim(),
      keywords: splitCsv(exportData.get("keywords")),
      description: String(exportData.get("description") || "").trim() || null,
      platform,
      crawler_type: state.preset,
      data_root: "data",
      output_dir: "datasets",
    };
    const result = await fetchJson("/api/datasets/export", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    $("#exportResult").textContent = `${result.dataset_id} · 内容 ${result.metrics.content_count} · 评论 ${result.metrics.comment_count}`;
    addLog(`Dataset exported: ${result.dataset_dir}`, "success");
  } catch (error) {
    $("#exportResult").textContent = "整理失败";
    addLog(error.message, "error");
  } finally {
    $("#exportBtn").disabled = false;
  }
}

function bindEvents() {
  $$(".segmented button").forEach((button) => {
    button.addEventListener("click", () => {
      state.preset = button.dataset.preset;
      updateModeFields();
      syncExportDefaults();
    });
  });
  $("#crawlerForm").addEventListener("submit", startCrawler);
  $("#stopBtn").addEventListener("click", stopCrawler);
  $("#checkEnvBtn").addEventListener("click", checkEnvironment);
  $("#startCdpBtn").addEventListener("click", startCdpBrowser);
  $("#openXhsBtn").addEventListener("click", openXhsPage);
  $("#refreshDataBtn").addEventListener("click", loadFiles);
  $("#clearLogsBtn").addEventListener("click", () => {
    $("#logStream").textContent = "";
  });
  $("#fileTypeFilter").addEventListener("change", () => {
    state.selectedFile = null;
    loadFiles();
  });
  $("#exportForm").addEventListener("submit", exportDataset);
  $("#crawlerForm").addEventListener("input", syncExportDefaults);
  $("#crawlerForm").elements.cdp_debug_port.addEventListener("change", refreshCdpStatus);
}

async function init() {
  $("#agentBaseUrl").textContent = `Hermes/WSL base URL: ${window.location.origin}`;
  bindEvents();
  updateModeFields();
  syncExportDefaults();
  connectSockets();
  await Promise.all([checkEnvironment(), refreshCdpStatus(), loadLogs(), loadFiles()]);
}

init();
