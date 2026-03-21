const APP_CONFIG = window.__KNOWLEDGE_CONFIG__ || {};
const DEFAULT_WAREHOUSE_APP_ID = APP_CONFIG.warehouse_app_id || "knowledge.yeying.pub";
const DEFAULT_WAREHOUSE_APP_ROOT = APP_CONFIG.warehouse_app_root || `/apps/${DEFAULT_WAREHOUSE_APP_ID}`;
const DEFAULT_WAREHOUSE_UPLOAD_DIR = APP_CONFIG.warehouse_upload_dir || `${DEFAULT_WAREHOUSE_APP_ROOT}/uploads`;

const state = {
  token: localStorage.getItem("knowledge_token") || "",
  wallet: localStorage.getItem("knowledge_wallet") || "",
  currentView: "dashboard",
  selectedKB: null,
  selectedDocument: null,
  selectedTaskId: null,
  selectedTaskDetail: null,
  selectedTaskItems: [],
  currentKBStats: null,
  currentKBWorkbench: null,
  warehouseReady: false,
  warehouseAppId: DEFAULT_WAREHOUSE_APP_ID,
  warehouseAppRoot: DEFAULT_WAREHOUSE_APP_ROOT,
  warehouseUploadDir: DEFAULT_WAREHOUSE_UPLOAD_DIR,
  currentBrowsePath: DEFAULT_WAREHOUSE_APP_ROOT,
  readCredentials: [],
  writeCredential: null,
  browseAccessSource: "",
  revealedCredentialSecrets: {},
  kbs: [],
  bindings: [],
  documents: [],
  tasks: [],
  uploads: [],
  longMemories: [],
  shortMemories: [],
  memoryIngestions: [],
  searchLabCompare: null,
  retrievalLogs: [],
  sourceGovernance: null,
  warehouseEntries: [],
  warehousePreview: null,
  opsOverview: null,
  opsStores: null,
  opsWorkers: [],
  opsFailures: [],
  confirmResolver: null,
  pathPickerFieldId: null,
  pathPickerCloseTimer: null,
  kbEditorMode: "edit",
  taskPollingTimer: null,
  taskPollingInFlight: false,
};

const TASK_POLL_INTERVAL_MS = 3000;

function el(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = el(id);
  if (node) node.textContent = value;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function highlightQuery(text, query) {
  const safe = escapeHtml(text);
  const keyword = String(query || "").trim();
  if (!keyword) return safe;
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return safe.replace(new RegExp(`(${escaped})`, "gi"), '<mark class="search-hit">$1</mark>');
}

function formatDate(value) {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function shortenMiddle(value, start = 8, end = 6) {
  const text = String(value || "");
  if (!text) return "";
  if (text.length <= start + end + 3) return text;
  return `${text.slice(0, start)}...${text.slice(-end)}`;
}

function currentWarehouseAppRoot() {
  return state.warehouseAppRoot || DEFAULT_WAREHOUSE_APP_ROOT;
}

function currentWarehouseUploadDir() {
  return state.warehouseUploadDir || DEFAULT_WAREHOUSE_UPLOAD_DIR;
}

function currentWriteCredentialId() {
  return Number(state.writeCredential?.id || 0) || null;
}

function currentBrowseAccessSource() {
  return String(state.browseAccessSource || "");
}

function currentBrowseCredentialId() {
  const source = currentBrowseAccessSource();
  if (source === "write") {
    return currentWriteCredentialId();
  }
  if (source.startsWith("read:")) {
    const value = Number(source.split(":")[1] || 0);
    return value > 0 ? value : null;
  }
  return null;
}

function isBrowseUsingWriteCredential() {
  return currentBrowseAccessSource() === "write";
}

function syncWarehouseConfig(data = {}) {
  state.warehouseAppId = data.current_app_id || state.warehouseAppId || DEFAULT_WAREHOUSE_APP_ID;
  state.warehouseAppRoot = data.current_app_root || state.warehouseAppRoot || DEFAULT_WAREHOUSE_APP_ROOT;
  state.warehouseUploadDir = data.current_app_upload_dir || state.warehouseUploadDir || DEFAULT_WAREHOUSE_UPLOAD_DIR;
  if (!state.currentBrowsePath || state.currentBrowsePath === "/" || state.currentBrowsePath.startsWith("/personal")) {
    state.currentBrowsePath = state.warehouseAppRoot;
  }
  const browseInput = el("browse-path");
  if (browseInput && (!browseInput.value || browseInput.value.startsWith("/personal"))) {
    browseInput.value = state.currentBrowsePath;
  }
  const uploadInput = el("target-dir");
  if (uploadInput && (!uploadInput.value || uploadInput.value.startsWith("/personal"))) {
    uploadInput.value = state.warehouseUploadDir;
  }
  const currentBrowsePath = el("current-browse-path");
  if (currentBrowsePath && (!currentBrowsePath.textContent || currentBrowsePath.textContent.startsWith("/personal"))) {
    currentBrowsePath.textContent = state.currentBrowsePath;
  }
}

function updateWarehouseCredentialSelectors() {
  const bindingSelect = el("binding-credential-id");
  if (bindingSelect) {
    const previous = String(bindingSelect.value || "");
    bindingSelect.innerHTML = [
      `<option value="">选择读凭证</option>`,
      ...state.readCredentials.map(
        (credential) =>
          `<option value="${credential.id}">${escapeHtml(`${credential.key_id} · ${credential.root_path} · ${credential.status}`)}</option>`,
      ),
    ].join("");
    if (previous && state.readCredentials.some((credential) => String(credential.id) === previous)) {
      bindingSelect.value = previous;
    }
  }

  const browseSelect = el("warehouse-access-source");
  if (browseSelect) {
    const previous = String(state.browseAccessSource || browseSelect.value || "");
    const options = [`<option value="">选择浏览凭证</option>`];
    if (state.writeCredential) {
      options.push(
        `<option value="write">${escapeHtml(`写凭证 · ${state.writeCredential.key_id} · ${state.writeCredential.root_path} · ${state.writeCredential.status}`)}</option>`,
      );
    }
    state.readCredentials.forEach((credential) => {
      options.push(
        `<option value="read:${credential.id}">${escapeHtml(`${credential.key_id} · ${credential.root_path} · ${credential.status}`)}</option>`,
      );
    });
    browseSelect.innerHTML = options.join("");
    const hasPrevious =
      (previous === "write" && Boolean(state.writeCredential)) ||
      state.readCredentials.some((credential) => `read:${credential.id}` === previous);
    if (hasPrevious) {
      browseSelect.value = previous;
      state.browseAccessSource = previous;
    } else if (state.writeCredential) {
      browseSelect.value = "write";
      state.browseAccessSource = "write";
    } else if (state.readCredentials.length) {
      browseSelect.value = `read:${state.readCredentials[0].id}`;
      state.browseAccessSource = browseSelect.value;
    } else {
      browseSelect.value = "";
      state.browseAccessSource = "";
    }
  }
}

function renderWalletSummary() {
  const wallet = String(state.wallet || "");
  const status = !wallet ? "未登录" : state.warehouseReady ? "已登录 · 凭证已就绪" : "已登录 · 等待导入仓库凭证";
  const walletLabel = wallet ? shortenMiddle(wallet) : "未连接钱包";
  setText("login-status", status);
  setText("pill-wallet", wallet ? `钱包：${shortenMiddle(wallet, 6, 4)}` : "钱包：未登录");
  const walletAddress = el("wallet-address");
  if (walletAddress) {
    walletAddress.textContent = walletLabel;
    walletAddress.title = wallet;
  }
}

function fillKBForm(kb = null) {
  const cfg = kb?.retrieval_config || {};
  el("kb-name").value = kb?.name || "";
  el("kb-desc").value = kb?.description || "";
  el("kb-chunk-size").value = cfg.chunk_size ?? 800;
  el("kb-chunk-overlap").value = cfg.chunk_overlap ?? 120;
  el("kb-retrieval-top-k").value = cfg.retrieval_top_k ?? 6;
  el("kb-memory-top-k").value = cfg.memory_top_k ?? 4;
  el("kb-embedding-model").value = cfg.embedding_model ?? "text-embedding-3-small";
}

function toTimestamp(value) {
  if (!value) return 0;
  const ts = new Date(value).getTime();
  return Number.isNaN(ts) ? 0 : ts;
}

function formatRelativeTime(value) {
  const ts = toTimestamp(value);
  if (!ts) return "-";
  const diffSeconds = Math.round((ts - Date.now()) / 1000);
  const absSeconds = Math.abs(diffSeconds);
  if (absSeconds < 60) return "刚刚";
  if (absSeconds < 3600) return `${Math.round(absSeconds / 60)} 分钟${diffSeconds < 0 ? "前" : "后"}`;
  if (absSeconds < 86400) return `${Math.round(absSeconds / 3600)} 小时${diffSeconds < 0 ? "前" : "后"}`;
  return `${Math.round(absSeconds / 86400)} 天${diffSeconds < 0 ? "前" : "后"}`;
}

function formatDuration(startValue, endValue) {
  const start = toTimestamp(startValue);
  const end = toTimestamp(endValue);
  if (!start && !end) return "-";
  if (start && !end) return "进行中";
  if (!start || !end || end < start) return "-";
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

function toneForTaskStatus(status) {
  return window.KnowledgeTasksPanel?.toneForTaskStatus
    ? window.KnowledgeTasksPanel.toneForTaskStatus(status)
    : "warning";
}

function toneForTaskItemStatus(status) {
  return window.KnowledgeTasksPanel?.toneForTaskItemStatus
    ? window.KnowledgeTasksPanel.toneForTaskItemStatus(status)
    : "warning";
}

function describeTaskQueue(task = {}) {
  return window.KnowledgeTasksPanel?.describeTaskQueue
    ? window.KnowledgeTasksPanel.describeTaskQueue(task)
    : "已完成";
}

function isTaskActiveStatus(status) {
  return ["pending", "running", "cancel_requested"].includes(String(status || "").toLowerCase());
}

function hasActiveTasks(tasks = state.tasks) {
  return (tasks || []).some((task) => isTaskActiveStatus(task.status));
}

function hasActiveTasksForSelectedKB(tasks = state.tasks) {
  if (!state.selectedKB) return false;
  return (tasks || []).some((task) => task.kb_id === state.selectedKB.id && isTaskActiveStatus(task.status));
}

function stopTaskPolling() {
  if (state.taskPollingTimer) {
    clearInterval(state.taskPollingTimer);
    state.taskPollingTimer = null;
  }
}

function ensureTaskPolling() {
  if (!state.token || !hasActiveTasks()) {
    stopTaskPolling();
    return;
  }
  if (state.taskPollingTimer) return;
  state.taskPollingTimer = setInterval(() => {
    pollTaskProgress().catch(() => {});
  }, TASK_POLL_INTERVAL_MS);
}

async function pollTaskProgress() {
  if (!state.token || state.taskPollingInFlight) return;
  if (!hasActiveTasks()) {
    stopTaskPolling();
    return;
  }
  state.taskPollingInFlight = true;
  try {
    const hadSelectedKBActive = hasActiveTasksForSelectedKB(state.tasks);
    const beforeStatuses = new Map(state.tasks.map((task) => [task.id, task.status]));
    await refreshTasks();
    const selectedKBTaskSettled =
      state.selectedKB &&
      state.tasks.some((task) => {
        const previous = beforeStatuses.get(task.id);
        return (
          task.kb_id === state.selectedKB.id &&
          isTaskActiveStatus(previous) &&
          !isTaskActiveStatus(task.status)
        );
      });
    if (state.selectedKB && (hadSelectedKBActive || hasActiveTasksForSelectedKB(state.tasks) || selectedKBTaskSettled)) {
      await refreshSelectedData();
    }
    if (!hasActiveTasks()) {
      stopTaskPolling();
    }
  } finally {
    state.taskPollingInFlight = false;
  }
}

function toneForMemoryEvent(event = {}) {
  const operation = String(event.notes_json?.operation || "");
  const status = String(event.status || "").toLowerCase();
  if (operation.startsWith("delete_") || status === "deleted") return "danger";
  if (status === "completed") return "success";
  return "warning";
}

function summarizeMemoryEvent(event = {}) {
  const notes = event.notes_json || {};
  const operation = String(notes.operation || "");
  const memoryNamespace = String(notes.memory_namespace || "");
  if (operation === "delete_long_term" || operation === "delete_short_term") {
    const isLongTerm = operation === "delete_long_term";
    const memoryLabel = isLongTerm ? "长期记忆" : "短期记忆";
    return {
      icon: "🗑",
      kind: "记忆删除",
      title: `删除${memoryLabel} #${notes.memory_id || event.id}`,
      subtitle: notes.content_preview || event.answer_preview || event.query_preview || "控制台删除记忆",
      detail: `短期删除 ${formatNumber(notes.deleted_short_term || 0)} · 长期删除 ${formatNumber(notes.deleted_long_term || 0)} · ${event.source || "console"}`,
      secondary: isLongTerm
        ? `分类：${notes.category || "-"} · 原来源：${notes.memory_source || "-"}`
        : `session=${event.session_id || "-"} · namespace=${memoryNamespace || "-"} · 类型：${notes.memory_type || "-"}`,
      nextLabel: "返回记忆",
      nextView: "memory",
    };
  }
  return {
    icon: "🧠",
    kind: "记忆沉淀",
    title: `session=${event.session_id}`,
    subtitle: event.query_preview || "自动沉淀事件",
    detail: `短期 ${formatNumber(event.short_term_created)} · 长期 ${formatNumber(event.long_term_created)} · ${event.source}`,
    secondary: `${memoryNamespace ? `namespace=${memoryNamespace} · ` : ""}${event.answer_preview ? `回答摘要：${event.answer_preview}` : ""}`.trim(),
    nextLabel: "继续检索",
    nextView: "retrieval",
  };
}

function formatMetadataValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function formatMetadataLabel(key) {
  return String(key || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function buildChunkMetadataEntries(chunk = {}) {
  const metadata = { ...(chunk.metadata || {}) };
  if (chunk.created_at) {
    metadata.chunk_created_at = formatDate(chunk.created_at);
  }
  if (chunk.embedding_model) {
    metadata.embedding_model = chunk.embedding_model;
  }
  if (chunk.index_status) {
    metadata.index_status = chunk.index_status;
  }
  const preferredOrder = [
    "chunk_strategy",
    "char_count",
    "rows",
    "source_path",
    "source_kind",
    "file_name",
    "file_type",
    "source_version",
    "chunk_created_at",
    "embedding_model",
    "index_status",
  ];
  const orderedEntries = [];
  const seen = new Set();
  preferredOrder.forEach((key) => {
    if (metadata[key] !== undefined && metadata[key] !== null && metadata[key] !== "") {
      orderedEntries.push([key, metadata[key]]);
      seen.add(key);
    }
  });
  Object.entries(metadata).forEach(([key, value]) => {
    if (seen.has(key) || value === undefined || value === null || value === "") return;
    orderedEntries.push([key, value]);
  });
  return orderedEntries.map(([key, value]) => ({
    key,
    label: formatMetadataLabel(key),
    value: formatMetadataValue(value),
  }));
}

function syncDocumentSummary(detail = null) {
  if (!detail?.id) return;
  const index = state.documents.findIndex((item) => item.id === detail.id);
  const summary = {
    id: detail.id,
    source_path: detail.source_path,
    source_file_name: detail.source_file_name,
    file_type: detail.file_type,
    source_kind: detail.source_kind,
    parse_status: detail.parse_status,
    chunk_count: detail.chunk_count,
    last_indexed_at: detail.last_indexed_at,
  };
  if (index >= 0) {
    state.documents[index] = { ...state.documents[index], ...summary };
  } else {
    state.documents.unshift(summary);
  }
}

function setOutput(data) {
  el("output").textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
}

function notify(type, message) {
  const container = el("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 2800);
}

function setView(view) {
  state.currentView = view;
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.viewTarget === view);
  });
  document.querySelectorAll(".view").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.view === view);
  });
}

async function api(path, options = {}) {
  const headers = options.headers || {};
  if (state.token) {
    headers.Authorization = `Bearer ${state.token}`;
  }
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(typeof data === "string" ? data : JSON.stringify(data));
  }
  return data;
}

function withFeedback(fn, successMessage = "") {
  return async (...args) => {
    try {
      const result = await fn(...args);
      if (successMessage) {
        notify("success", successMessage);
      }
      return result;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      notify("error", message);
      setOutput(message);
      throw error;
    }
  };
}

function setLoggedIn(wallet) {
  state.wallet = wallet;
  localStorage.setItem("knowledge_wallet", wallet);
  renderWalletSummary();
}

function renderLoggedOutState() {
  renderWalletSummary();
  setText("selected-kb-name", "未选择");
  setText("selected-kb-desc", "请选择一个知识库后继续导入和检索。");
  if (el("current-kb-stats")) {
    el("current-kb-stats").className = "empty";
    el("current-kb-stats").textContent = "请选择知识库后查看统计。";
  }
}

function clearSession() {
  stopTaskPolling();
  state.token = "";
  state.wallet = "";
  state.warehouseReady = false;
  state.readCredentials = [];
  state.writeCredential = null;
  state.browseAccessSource = "";
  state.revealedCredentialSecrets = {};
  state.selectedKB = null;
  state.selectedDocument = null;
  state.selectedTaskId = null;
  state.selectedTaskDetail = null;
  state.selectedTaskItems = [];
  state.currentKBStats = null;
  state.currentKBWorkbench = null;
  localStorage.removeItem("knowledge_token");
  localStorage.removeItem("knowledge_wallet");
  closeDrawer();
  closeDocumentDrawer();
  closePathPicker();
  renderLoggedOutState();
}

function updateSelectedKBUI() {
  const kb = state.selectedKB;
  if (!kb) {
    el("selected-kb-name").textContent = "未选择";
    el("selected-kb-desc").textContent = "请选择一个知识库后继续来源治理、知识项管理与发布。";
    return;
  }
  el("selected-kb-name").textContent = `#${kb.id} ${kb.name}`;
  el("selected-kb-desc").textContent = kb.description || "无描述";
  el("task-kb-id").value = kb.id;
  if (el("memory-kb-id")) el("memory-kb-id").value = kb.id;
}

function setWarehouseReady(ready) {
  state.warehouseReady = ready;
  renderWalletSummary();
}

function updateMetrics() {
  el("metric-kbs").textContent = String(state.kbs.length);
  el("metric-bindings").textContent = String(state.bindings.length);
  el("metric-docs").textContent = String(state.documents.length);
  el("metric-tasks").textContent = String(state.tasks.length);
  el("pill-kb").textContent = `知识库：${state.kbs.length}`;
  el("pill-task").textContent = `任务：${state.tasks.length}`;
}

function buildRecentActivities() {
  const activities = [];

  state.uploads.slice(0, 6).forEach((upload) => {
    const actions = [{ label: "定位", action: "open-browse-path", path: upload.warehouse_target_path, useWriteCredential: true }];
    if (state.selectedKB) {
      actions.push({ label: "导入", action: "import-path", path: upload.warehouse_target_path, useWriteCredential: true });
    }
    activities.push({
      sortTs: toTimestamp(upload.created_at),
      icon: "↑",
      kind: "上传",
      title: upload.file_name,
      subtitle: upload.warehouse_target_path,
      time: upload.created_at,
      detail: `${formatNumber(upload.size || 0)} bytes · 已写入 Knowledge App 目录`,
      status: "uploaded",
      tone: "success",
      actions,
    });
  });

  state.tasks.slice(0, 6).forEach((task) => {
    activities.push({
      sortTs: toTimestamp(task.finished_at || task.started_at || task.created_at),
      icon: "⚙",
      kind: "任务",
      title: `#${task.id} · ${task.task_type}`,
      subtitle: (task.source_paths || []).join("，") || "-",
      time: task.finished_at || task.started_at || task.created_at,
      detail: `知识库 #${task.kb_id}${task.error_message ? ` · ${task.error_message}` : ""}`,
      status: task.status,
      tone: toneForTaskStatus(task.status),
      actions: [{ label: "查看详情", action: "show-task", taskId: task.id, jumpView: "tasks" }],
    });
  });

  state.documents.slice(0, 6).forEach((doc) => {
    activities.push({
      sortTs: toTimestamp(doc.last_indexed_at),
      icon: "📚",
      kind: "文档",
      title: doc.source_file_name,
      subtitle: doc.source_path,
      time: doc.last_indexed_at,
      detail: `${formatNumber(doc.chunk_count || 0)} chunks · ${doc.file_type || "-"}`,
      status: doc.parse_status || "indexed",
      tone: toneForTaskItemStatus(doc.parse_status || "indexed"),
      actions: [
        { label: "查看文档", action: "select-document", docId: doc.id, jumpView: "documents" },
        { label: "定位", action: "open-browse-path", path: doc.source_path },
      ],
    });
  });

  state.memoryIngestions.slice(0, 4).forEach((event) => {
    const summary = summarizeMemoryEvent(event);
    activities.push({
      sortTs: toTimestamp(event.created_at),
      icon: summary.icon,
      kind: summary.kind,
      title: summary.title,
      subtitle: summary.subtitle,
      time: event.created_at,
      detail: summary.detail,
      status: event.status,
      tone: toneForMemoryEvent(event),
      actions: [
        { label: "详情", action: "show-memory-ingestion", eventId: event.id, jumpView: "memory" },
        { label: summary.nextLabel, action: "jump-view", view: summary.nextView },
      ],
    });
  });

  return activities
    .sort((left, right) => right.sortTs - left.sortTs)
    .slice(0, 8);
}

function renderRecentActivity() {
  const box = el("recent-activity");
  if (!box) return;
  const activities = buildRecentActivities();
  if (!activities.length) {
    box.className = "empty";
    box.textContent = "暂无最近活动，先上传到 warehouse 或创建导入任务。";
    return;
  }
  box.className = "activity-list";
  box.innerHTML = activities
    .map(
      (activity) => `
        <div class="activity-item">
          <div class="activity-main">
            <div class="activity-kind"><span>${activity.icon}</span><span>${escapeHtml(activity.kind)}</span></div>
            <div class="list-title">${escapeHtml(activity.title)}</div>
            <div class="list-subtitle">${escapeHtml(activity.subtitle)}</div>
            <div class="activity-meta">
              <span>${formatRelativeTime(activity.time)}</span>
              <span>${formatDate(activity.time)}</span>
              <span>${escapeHtml(activity.detail)}</span>
            </div>
          </div>
          <div class="activity-side">
            <span class="pill ${activity.tone}">${escapeHtml(activity.status)}</span>
            <div class="list-actions">
              ${activity.actions
                .map((action) => {
                  const attrs = [
                    `data-action="${action.action}"`,
                    action.path ? `data-path="${escapeHtml(action.path)}"` : "",
                    action.credentialId ? `data-credential-id="${action.credentialId}"` : "",
                    action.useWriteCredential ? `data-use-write-credential="true"` : "",
                    action.taskId ? `data-task-id="${action.taskId}"` : "",
                    action.docId ? `data-doc-id="${action.docId}"` : "",
                    action.eventId ? `data-event-id="${action.eventId}"` : "",
                    action.jumpView ? `data-jump-view="${action.jumpView}"` : "",
                    action.view ? `data-view="${action.view}"` : "",
                  ]
                    .filter(Boolean)
                    .join(" ");
                  return `<button class="secondary" ${attrs}>${escapeHtml(action.label)}</button>`;
                })
                .join("")}
            </div>
          </div>
        </div>
      `,
    )
    .join("");
}

function renderCurrentKBStats() {
  const box = el("current-kb-stats");
  if (!state.selectedKB || !state.currentKBStats) {
    box.className = "empty";
    box.textContent = "请选择知识库后查看统计。";
    return;
  }
  box.className = "grid-2";
  box.innerHTML = `
    <div class="list-item">
      <div class="list-title">绑定源</div>
      <div class="metric-value">${formatNumber(state.currentKBStats.bindings_count)}</div>
    </div>
    <div class="list-item">
      <div class="list-title">文档数</div>
      <div class="metric-value">${formatNumber(state.currentKBStats.documents_count)}</div>
    </div>
    <div class="list-item">
      <div class="list-title">Chunk 数</div>
      <div class="metric-value">${formatNumber(state.currentKBStats.chunks_count)}</div>
    </div>
    <div class="list-item">
      <div class="list-title">最近任务</div>
      <div class="list-subtitle">${escapeHtml(state.currentKBStats.latest_task_status || "-")}</div>
      <div class="helper">${formatDate(state.currentKBStats.latest_task_finished_at)}</div>
    </div>
  `;
}

function renderKBWorkbench() {
  const box = el("kb-workbench");
  if (!box) return;
  const helper = window.KnowledgeKBWorkbench;
  if (helper?.renderWorkbench) {
    box.className = "card-shell";
    box.innerHTML = helper.renderWorkbench({
      selectedKB: state.selectedKB,
      workbench: state.currentKBWorkbench,
      helpers: { escapeHtml, formatDate, formatNumber },
    });
    return;
  }
  if (!state.selectedKB || !state.currentKBWorkbench) {
    box.className = "empty";
    box.textContent = "请选择知识库后查看绑定状态、最近任务和同步建议。";
    return;
  }
  box.className = "code";
  box.textContent = JSON.stringify(state.currentKBWorkbench, null, 2);
}

function openDrawer(title, data) {
  el("drawer-title").textContent = title;
  el("drawer-content").textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  el("detail-drawer").classList.add("open");
}

function closeDrawer() {
  el("detail-drawer").classList.remove("open");
}

function openDocumentDrawer(detail = state.selectedDocument) {
  const drawer = el("document-drawer");
  if (!drawer || !detail) return;
  if (state.documentDrawerCloseTimer) {
    clearTimeout(state.documentDrawerCloseTimer);
    state.documentDrawerCloseTimer = null;
  }
  setText("document-drawer-title", `文档详情：${detail.source_file_name}`);
  setText("document-drawer-subtitle", `${detail.source_path} · ${formatNumber(detail.chunk_count || 0)} chunks`);
  drawer.classList.add("open");
}

function closeDocumentDrawer() {
  const drawer = el("document-drawer");
  if (!drawer) return;
  drawer.classList.remove("open");
}

function openKBEditor(mode = "edit", kb = state.selectedKB) {
  const drawer = el("kb-editor-drawer");
  if (!drawer) return;
  state.kbEditorMode = mode;
  if (mode === "create") {
    setText("kb-editor-title", "新建知识库");
    setText("kb-editor-subtitle", "填写名称、描述和检索参数后创建知识库。");
    fillKBForm(null);
  } else {
    if (!kb) {
      throw new Error("请先选择知识库");
    }
    setText("kb-editor-title", `编辑知识库 #${kb.id}`);
    setText("kb-editor-subtitle", "修改配置后会自动同步到当前知识库；chunk 参数变化会触发已有文档重建。");
    fillKBForm(kb);
  }
  el("create-kb").style.display = mode === "create" ? "" : "none";
  el("update-kb").style.display = mode === "edit" ? "" : "none";
  drawer.classList.add("open");
}

function closeKBEditor() {
  const drawer = el("kb-editor-drawer");
  if (!drawer) return;
  drawer.classList.remove("open");
}

function confirmAction(title, message) {
  el("confirm-title").textContent = title;
  el("confirm-message").textContent = message;
  el("confirm-modal").classList.remove("hidden");
  return new Promise((resolve) => {
    state.confirmResolver = resolve;
  });
}

function closeConfirm(result) {
  el("confirm-modal").classList.add("hidden");
  if (typeof state.confirmResolver === "function") {
    state.confirmResolver(result);
  }
  state.confirmResolver = null;
}

function renderBreadcrumbs() {
  const container = el("warehouse-breadcrumbs");
  const crumbs = buildPathCrumbs(state.currentBrowsePath);
  container.innerHTML = crumbs
    .map(
      (crumb) => `
        <button class="crumb" data-action="crumb" data-path="${crumb.path}">
          ${escapeHtml(crumb.label)}
        </button>
      `,
    )
    .join("");
}

function buildPathCrumbs(path) {
  const parts = String(path || "/").split("/").filter(Boolean);
  const crumbs = [{ label: "root", path: "/" }];
  let current = "";
  for (const part of parts) {
    current += `/${part}`;
    crumbs.push({ label: part, path: current });
  }
  return crumbs;
}

function renderWarehousePreview() {
  const box = el("warehouse-preview");
  if (!state.warehousePreview) {
    box.className = "empty";
    box.textContent = "点击左侧文件可查看预览，点击目录可进入。";
    return;
  }
  box.className = "code";
  box.textContent = JSON.stringify(state.warehousePreview, null, 2);
}

function pathFieldConfig(fieldId) {
  return {
    "browse-path": { allowFiles: false, allowDirectories: true },
    "target-dir": { allowFiles: false, allowDirectories: true },
    "read-credential-root-path": { allowFiles: true, allowDirectories: true },
    "write-credential-root-path": { allowFiles: true, allowDirectories: true },
    "binding-path": { allowFiles: true, allowDirectories: true },
    "task-source-path": { allowFiles: true, allowDirectories: true },
  }[fieldId] || { allowFiles: true, allowDirectories: true };
}

function pathFieldLabel(fieldId) {
  return {
    "browse-path": "浏览路径",
    "target-dir": "上传目录",
    "read-credential-root-path": "读凭证根路径",
    "write-credential-root-path": "写凭证根路径",
    "binding-path": "绑定路径",
    "task-source-path": "任务源路径",
  }[fieldId] || "路径";
}

function pathMatchesField(fieldId, path, entryType) {
  const config = pathFieldConfig(fieldId);
  if (entryType === "file" && !config.allowFiles) return false;
  if (entryType === "directory" && !config.allowDirectories) return false;
  return true;
}

function collectPathPickerSections(fieldId) {
  const keyword = (el("path-picker-filter")?.value || "").trim().toLowerCase();
  return (state.warehouseEntries || [])
    .filter((entry) => `${entry.name} ${entry.path}`.toLowerCase().includes(keyword))
    .filter((entry) => {
      if (entry.entry_type === "directory") return true;
      return pathMatchesField(fieldId, entry.path, entry.entry_type);
    });
}

function renderPathPicker(fieldId) {
  const menu = el("path-picker-menu");
  const sectionsEl = el("path-picker-sections");
  const input = el(fieldId);
  const modeEl = el("path-picker-mode");
  const contextEl = el("path-picker-context");
  if (!menu || !sectionsEl || !input) return;

  const config = pathFieldConfig(fieldId);
  const sections = collectPathPickerSections(fieldId);
  if (state.pathPickerCloseTimer) {
    clearTimeout(state.pathPickerCloseTimer);
    state.pathPickerCloseTimer = null;
  }
  if (contextEl) {
    contextEl.textContent = `为“${pathFieldLabel(fieldId)}”选择 warehouse 路径。当前目录：${state.currentBrowsePath || "/"}`;
  }
  if (modeEl) {
    modeEl.innerHTML = `
      <span class="picker-chip ${config.allowDirectories ? "active" : ""}">可选目录</span>
      <span class="picker-chip ${config.allowFiles ? "active" : ""}">可选文件</span>
      <span class="picker-chip active">当前 App 目录</span>
    `;
  }
  const currentPath = state.currentBrowsePath || "/";
  const parentPath = currentPath === "/" ? null : currentPath.replace(/\/[^/]+$/, "") || "/";
  const crumbs = buildPathCrumbs(currentPath);
  sectionsEl.innerHTML = `
    <div class="path-picker-section">
      <div class="path-picker-section-title">当前目录</div>
      <div class="detail-path">${escapeHtml(currentPath)}</div>
      <div class="list-actions" style="margin-bottom: 10px">
        ${parentPath ? `<button class="ghost" data-picker-nav-path="${parentPath}">返回上级</button>` : ""}
        ${config.allowDirectories ? `<button class="secondary" data-picker-select="1" data-picker-target="${fieldId}" data-picker-path="${escapeHtml(currentPath)}">选择当前目录</button>` : ""}
      </div>
      <div class="breadcrumbs">
        ${crumbs
          .map(
            (crumb) => `
              <button class="crumb" data-picker-nav-path="${crumb.path}">${escapeHtml(crumb.label)}</button>
            `,
          )
          .join("")}
      </div>
    </div>
    <div class="path-picker-section">
      <div class="path-picker-section-title">目录结构</div>
      <div class="path-picker-list">
        ${
          sections.length
            ? sections
                .map((entry) => {
                  const isDirectory = entry.entry_type === "directory";
                  const canSelect = pathMatchesField(fieldId, entry.path, entry.entry_type);
                  return `
                    <div class="path-picker-item-shell">
                      <button
                        class="path-picker-item"
                        ${isDirectory ? `data-picker-nav-path="${escapeHtml(entry.path)}"` : `data-picker-select="1" data-picker-target="${fieldId}" data-picker-path="${escapeHtml(entry.path)}"`}
                      >
                        <span>${isDirectory ? "📁" : "📄"} ${escapeHtml(entry.name)}</span>
                        <span class="path-picker-meta">${escapeHtml(entry.modified_at ? formatDate(entry.modified_at) : entry.path)}</span>
                      </button>
                      ${isDirectory && canSelect ? `<button class="ghost" data-picker-select="1" data-picker-target="${fieldId}" data-picker-path="${escapeHtml(entry.path)}">选中</button>` : ""}
                    </div>
                  `;
                })
                .join("")
            : `<div class="empty">当前目录为空。</div>`
        }
      </div>
    </div>
  `;
  state.pathPickerFieldId = fieldId;
  menu.classList.remove("hidden");
  requestAnimationFrame(() => menu.classList.add("open"));
}

function closePathPicker() {
  const menu = el("path-picker-menu");
  if (!menu) return;
  menu.classList.remove("open");
  if (state.pathPickerCloseTimer) {
    clearTimeout(state.pathPickerCloseTimer);
  }
  state.pathPickerCloseTimer = setTimeout(() => {
    menu.classList.add("hidden");
    state.pathPickerCloseTimer = null;
  }, 220);
  state.pathPickerFieldId = null;
}

function renderTaskDetail(task = state.selectedTaskDetail, items = state.selectedTaskItems) {
  const box = el("task-detail");
  if (window.KnowledgeTasksPanel?.renderTaskDetail) {
    const payload = window.KnowledgeTasksPanel.renderTaskDetail({
      task,
      items,
      helpers: { escapeHtml, formatDate, formatDuration, formatNumber },
    });
    box.className = payload.className || "";
    box.innerHTML = payload.html;
    return;
  }
  if (!task) {
    box.className = "empty";
    box.textContent = "点击任务列表中的“详情”查看 task item 明细。";
    return;
  }
  const taskItems = Array.isArray(items) ? items : [];
  const groups = [
    { key: "success", title: "成功项", tone: "success", items: [] },
    { key: "rolled_back", title: "已回退", tone: "info", items: [] },
    { key: "skipped", title: "跳过项", tone: "info", items: [] },
    { key: "failed", title: "失败项", tone: "danger", items: [] },
    { key: "running", title: "处理中", tone: "warning", items: [] },
    { key: "other", title: "其他", tone: "warning", items: [] },
  ];
  taskItems.forEach((item) => {
    const status = String(item.status || "").toLowerCase();
    const group =
      groups.find((candidate) => {
        if (candidate.key === "success") return ["indexed", "deleted", "succeeded"].includes(status);
        if (candidate.key === "rolled_back") return status === "rolled_back";
        if (candidate.key === "skipped") return status === "skipped";
        if (candidate.key === "failed") return status === "failed";
        if (candidate.key === "running") return ["pending", "running"].includes(status);
        return false;
      }) || groups.find((candidate) => candidate.key === "other");
    group.items.push(item);
  });
  const visibleGroups = groups.filter((group) => group.items.length > 0);
  const statsEntries = Object.entries(task.stats_json || {});
  const totalChunks = taskItems.reduce((sum, item) => sum + Number(item.processed_chunks || 0), 0);
  box.className = "task-detail-shell";
  box.innerHTML = `
    <div class="task-summary-grid">
      <div class="detail-card">
        <div class="detail-label">任务概览</div>
        <div class="detail-value">#${task.id} · ${escapeHtml(task.task_type)}</div>
        <div class="helper">知识库 #${task.kb_id}</div>
        <div class="helper">创建时间：${formatDate(task.created_at)}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">执行状态</div>
        <div class="detail-value"><span class="pill ${toneForTaskStatus(task.status)}">${escapeHtml(task.status)}</span></div>
        <div class="helper">队列信息：${escapeHtml(describeTaskQueue(task))}</div>
        <div class="helper">开始时间：${formatDate(task.started_at)}</div>
        <div class="helper">结束时间：${formatDate(task.finished_at)}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">执行耗时</div>
        <div class="detail-value">${formatDuration(task.started_at || task.created_at, task.finished_at)}</div>
        <div class="helper">task item：${formatNumber(taskItems.length)}</div>
        <div class="helper">累计 chunks：${formatNumber(totalChunks)}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">结果摘要</div>
        <div class="task-mini-grid">
          <div class="task-mini-stat"><strong>${formatNumber(groups.find((group) => group.key === "success")?.items.length || 0)}</strong><span>成功</span></div>
          <div class="task-mini-stat"><strong>${formatNumber(groups.find((group) => group.key === "skipped")?.items.length || 0)}</strong><span>跳过</span></div>
          <div class="task-mini-stat"><strong>${formatNumber(groups.find((group) => group.key === "failed")?.items.length || 0)}</strong><span>失败</span></div>
          <div class="task-mini-stat"><strong>${formatNumber(groups.find((group) => group.key === "running")?.items.length || 0)}</strong><span>处理中</span></div>
        </div>
        ${
          task.cancelable
            ? `<div class="list-actions" style="margin-top: 10px"><button class="danger" data-action="cancel-task" data-task-id="${task.id}">${task.status === "cancel_requested" ? "取消中" : "取消任务"}</button></div>`
            : ""
        }
      </div>
    </div>

    <div class="detail-card">
      <div class="detail-label">源路径</div>
      <div class="task-path-list">
        ${
          (task.source_paths || []).length
            ? (task.source_paths || [])
                .map(
                  (path) => `
                    <div class="task-path-item">
                      <span class="task-path-value">${escapeHtml(path)}</span>
                      <div class="toolbar">
                        <button class="secondary" data-action="open-browse-path" data-path="${escapeHtml(path)}">定位</button>
                        <button class="ghost" data-action="fill-task-source" data-path="${escapeHtml(path)}">设为任务源</button>
                      </div>
                    </div>
                  `,
                )
                .join("")
            : `<div class="helper">当前任务未记录源路径。</div>`
        }
      </div>
    </div>

    <div class="detail-card">
      <div class="detail-label">任务统计</div>
      ${
        statsEntries.length
          ? `<div class="kv-list">
              ${statsEntries
                .map(
                  ([key, value]) => `
                    <div class="kv-item">
                      <span>${escapeHtml(key)}</span>
                      <strong>${escapeHtml(typeof value === "object" ? JSON.stringify(value) : String(value))}</strong>
                    </div>
                  `,
                )
                .join("")}
            </div>`
          : `<div class="helper">暂无额外统计字段。</div>`
      }
    </div>

    ${
      task.error_message
        ? `
          <div class="task-alert danger">
            <strong>任务报错</strong>
            <div class="helper">${escapeHtml(task.error_message)}</div>
          </div>
        `
        : ""
    }

    <div class="task-group-list">
      ${
        visibleGroups.length
          ? visibleGroups
              .map(
                (group) => `
                  <div class="task-group">
                    <div class="task-group-head">
                      <div class="task-group-title">${group.title}</div>
                      <span class="pill ${group.tone}">${formatNumber(group.items.length)}</span>
                    </div>
                    <div class="detail-list">
                      ${group.items
                        .map(
                          (item) => `
                            <div class="detail-list-item task-item-card task-item-${group.tone}">
                              <div class="detail-list-head">
                                <strong>${escapeHtml(item.file_name || item.source_path)}</strong>
                                <span class="pill ${toneForTaskItemStatus(item.status)}">${escapeHtml(item.status)}</span>
                              </div>
                              <div class="helper">路径：${escapeHtml(item.source_path)}</div>
                              <div class="helper">版本：${escapeHtml(item.source_version || "-")} · chunks：${formatNumber(item.processed_chunks || 0)}</div>
                              <div class="helper">记录时间：${formatDate(item.created_at)}</div>
                              <div class="helper">说明：${escapeHtml(item.message || "-")}</div>
                              <div class="list-actions">
                                <button class="secondary" data-action="open-browse-path" data-path="${escapeHtml(item.source_path)}">定位源文件</button>
                                <button class="ghost" data-action="fill-task-source" data-path="${escapeHtml(item.source_path)}">设为任务源</button>
                              </div>
                            </div>
                          `,
                        )
                        .join("")}
                    </div>
                  </div>
                `,
              )
              .join("")
          : `<div class="empty">当前任务还没有 task item，通常表示任务尚未开始处理。</div>`
      }
    </div>
  `;
}

function renderDocumentDetail(detail = null) {
  const box = el("document-detail");
  if (!detail) {
    box.className = "empty";
    box.textContent = "请选择左侧文档查看切片内容。";
    return;
  }
  const chunks = detail.chunks || [];
  box.className = "";
  box.innerHTML = `
    <div class="detail-grid">
      <div class="detail-card">
        <div class="detail-label">文件名</div>
        <div class="detail-value">${escapeHtml(detail.source_file_name)}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">文件类型</div>
        <div class="detail-value">${escapeHtml(detail.file_type || "-")}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">来源</div>
        <div class="detail-value">${escapeHtml(detail.source_kind || "-")}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">Chunk 数</div>
        <div class="detail-value">${formatNumber(detail.chunk_count || 0)}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">解析状态</div>
        <div class="detail-value">${escapeHtml(detail.parse_status || "-")}</div>
      </div>
      <div class="detail-card">
        <div class="detail-label">最近索引</div>
        <div class="detail-value">${formatDate(detail.last_indexed_at)}</div>
      </div>
    </div>
    <div class="detail-path">${escapeHtml(detail.source_path)}</div>
    <div class="list-actions detail-actions">
      <button class="secondary" data-action="show-document-json">查看文档 JSON</button>
    </div>
    <div class="detail-chunks">
      ${chunks
        .map(
          (chunk) => {
            const metadataEntries = buildChunkMetadataEntries(chunk);
            return `
            <div class="chunk-card">
              <div class="chunk-head">
                <span>Chunk #${chunk.chunk_index}</span>
                <div class="chunk-actions">
                  <span class="pill">${escapeHtml(chunk.metadata?.chunk_strategy || "default")}</span>
                  <button class="ghost" data-action="show-chunk-detail" data-chunk-id="${chunk.id}">查看详情</button>
                </div>
              </div>
              <div class="chunk-meta-grid">
                ${
                  metadataEntries.length
                    ? metadataEntries
                        .map(
                          (item) => `
                            <div class="chunk-meta-item">
                              <div class="detail-label">${escapeHtml(item.label)}</div>
                              <div class="detail-value">${escapeHtml(item.value)}</div>
                            </div>
                          `,
                        )
                        .join("")
                    : `<div class="empty">当前 chunk 没有 metadata。</div>`
                }
              </div>
              <div class="chunk-text">${escapeHtml(chunk.text)}</div>
            </div>
          `;
          },
        )
        .join("")}
    </div>
  `;
}

async function loginWithWallet() {
  if (!window.ethereum) {
    throw new Error("未检测到浏览器钱包");
  }
  const [wallet] = await window.ethereum.request({ method: "eth_requestAccounts" });
  const challenge = await api("/auth/challenge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wallet_address: wallet }),
  });
  const signature = await window.ethereum.request({
    method: "personal_sign",
    params: [challenge.message, wallet],
  });
  const token = await api("/auth/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ wallet_address: wallet, signature }),
  });
  state.token = token.access_token;
  localStorage.setItem("knowledge_token", token.access_token);
  setLoggedIn(token.wallet_address);
  await refreshAll();
  setOutput(token);
}

async function logout() {
  clearSession();
  state.kbs = [];
  state.bindings = [];
  state.documents = [];
  state.tasks = [];
  state.uploads = [];
  state.readCredentials = [];
  state.writeCredential = null;
  state.browseAccessSource = "";
  state.revealedCredentialSecrets = {};
  state.longMemories = [];
  state.shortMemories = [];
  state.memoryIngestions = [];
  state.searchLabCompare = null;
  state.retrievalLogs = [];
  state.sourceGovernance = null;
  state.warehouseEntries = [];
  state.currentBrowsePath = DEFAULT_WAREHOUSE_APP_ROOT;
  state.opsFailures = [];
  syncWarehouseConfig();
  renderAll();
}

async function refreshWarehouseStatus() {
  const data = await api("/warehouse/status");
  syncWarehouseConfig(data);
  setWarehouseReady(Boolean(data.credentials_ready));
  return data;
}

async function refreshReadCredentials() {
  state.readCredentials = await api("/warehouse/credentials/read");
  updateWarehouseCredentialSelectors();
  renderReadCredentials();
}

async function refreshWriteCredential() {
  const payload = await api("/warehouse/credentials/write");
  state.writeCredential = payload.configured ? payload.credential : null;
  updateWarehouseCredentialSelectors();
  renderWriteCredential();
}

function renderReadCredentials() {
  const list = el("read-credential-list");
  if (!list) return;
  if (!state.readCredentials.length) {
    list.innerHTML = `<div class="empty">还没有导入读凭证。</div>`;
    return;
  }
  list.innerHTML = state.readCredentials
    .map((credential) => {
      const revealed = state.revealedCredentialSecrets[credential.id];
      return `
        <div class="list-item">
          <div class="list-head">
            <div class="list-title">${escapeHtml(credential.key_id)}</div>
            <span class="pill ${credential.status === "active" ? "success" : "warning"}">${escapeHtml(credential.status)}</span>
          </div>
          <div class="list-subtitle">${escapeHtml(credential.root_path)}</div>
          <div class="helper">sk=${escapeHtml(revealed || credential.key_secret_masked)} · 最近校验 ${formatDate(credential.last_verified_at)} · 最近使用 ${formatDate(credential.last_used_at)}</div>
          <div class="list-actions">
            <button class="ghost" data-action="reveal-read-credential" data-credential-id="${credential.id}">${revealed ? "重新显示" : "显示 sk"}</button>
            <button class="secondary" data-action="use-read-credential" data-credential-id="${credential.id}">设为浏览/绑定</button>
            <button class="danger" data-action="delete-read-credential" data-credential-id="${credential.id}">删除</button>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderWriteCredential() {
  const list = el("write-credential-list");
  if (!list) return;
  if (!state.writeCredential) {
    list.innerHTML = `<div class="empty">还没有配置写凭证。</div>`;
    return;
  }
  const credential = state.writeCredential;
  const revealed = state.revealedCredentialSecrets[credential.id];
  list.innerHTML = `
    <div class="list-item">
      <div class="list-head">
        <div class="list-title">${escapeHtml(credential.key_id)}</div>
        <span class="pill ${credential.status === "active" ? "success" : "warning"}">${escapeHtml(credential.status)}</span>
      </div>
      <div class="list-subtitle">${escapeHtml(credential.root_path)}</div>
      <div class="helper">sk=${escapeHtml(revealed || credential.key_secret_masked)} · 最近校验 ${formatDate(credential.last_verified_at)} · 最近使用 ${formatDate(credential.last_used_at)}</div>
      <div class="list-actions">
        <button class="ghost" data-action="reveal-write-credential">${revealed ? "重新显示" : "显示 sk"}</button>
        <button class="secondary" data-action="use-write-credential">设为浏览凭证</button>
      </div>
    </div>
  `;
}

async function saveReadCredential() {
  const keyId = (el("read-credential-key-id").value || "").trim();
  const keySecret = (el("read-credential-key-secret").value || "").trim();
  const rootPath = (el("read-credential-root-path").value || "").trim();
  if (!keyId || !keySecret || !rootPath) {
    throw new Error("请完整填写读凭证的 ak / sk / root_path");
  }
  const result = await api("/warehouse/credentials/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key_id: keyId, key_secret: keySecret, root_path: rootPath }),
  });
  await Promise.all([refreshWarehouseStatus(), refreshReadCredentials()]);
  el("read-credential-key-id").value = "";
  el("read-credential-key-secret").value = "";
  el("read-credential-root-path").value = rootPath;
  setOutput(result);
}

async function deleteReadCredential(credentialId) {
  const confirmed = await confirmAction("删除读凭证", "删除后，所有引用它的绑定都必须先解绑。");
  if (!confirmed) return;
  await api(`/warehouse/credentials/read/${credentialId}`, { method: "DELETE" });
  delete state.revealedCredentialSecrets[credentialId];
  await Promise.all([refreshWarehouseStatus(), refreshReadCredentials()]);
  setOutput({ ok: true, deleted_credential_id: credentialId });
}

async function revealReadCredential(credentialId) {
  const result = await api(`/warehouse/credentials/read/${credentialId}/secret`);
  state.revealedCredentialSecrets[credentialId] = result.key_secret;
  renderReadCredentials();
}

async function saveWriteCredential() {
  const keyId = (el("write-credential-key-id").value || "").trim();
  const keySecret = (el("write-credential-key-secret").value || "").trim();
  const rootPath = (el("write-credential-root-path").value || "").trim();
  if (!keyId || !keySecret || !rootPath) {
    throw new Error("请完整填写写凭证的 ak / sk / root_path");
  }
  const result = await api("/warehouse/credentials/write", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key_id: keyId, key_secret: keySecret, root_path: rootPath }),
  });
  await Promise.all([refreshWarehouseStatus(), refreshWriteCredential()]);
  el("write-credential-key-id").value = "";
  el("write-credential-key-secret").value = "";
  el("write-credential-root-path").value = rootPath;
  setOutput(result);
}

async function deleteWriteCredential() {
  const confirmed = await confirmAction("删除写凭证", "删除后将无法继续上传到当前 Knowledge App 目录。");
  if (!confirmed) return;
  const credentialId = currentWriteCredentialId();
  await api("/warehouse/credentials/write", { method: "DELETE" });
  if (credentialId) {
    delete state.revealedCredentialSecrets[credentialId];
  }
  await Promise.all([refreshWarehouseStatus(), refreshWriteCredential()]);
  setOutput({ ok: true });
}

async function revealWriteCredential() {
  const result = await api("/warehouse/credentials/write/secret");
  state.revealedCredentialSecrets[result.id] = result.key_secret;
  renderWriteCredential();
}

function renderOps() {
  const overview = el("ops-overview");
  if (!state.opsOverview) {
    overview.className = "empty";
    overview.textContent = "尚未加载运维状态。";
  } else {
    overview.className = "grid-3";
    overview.innerHTML = `
      <div class="list-item"><div class="list-title">知识库</div><div class="metric-value">${formatNumber(state.opsOverview.knowledge_bases)}</div></div>
      <div class="list-item"><div class="list-title">文档</div><div class="metric-value">${formatNumber(state.opsOverview.documents)}</div></div>
      <div class="list-item"><div class="list-title">Chunk</div><div class="metric-value">${formatNumber(state.opsOverview.chunks)}</div></div>
      <div class="list-item"><div class="list-title">待执行任务</div><div class="metric-value">${formatNumber(state.opsOverview.tasks_pending)}</div></div>
      <div class="list-item"><div class="list-title">长期记忆</div><div class="metric-value">${formatNumber(state.opsOverview.long_term_memories)}</div></div>
      <div class="list-item"><div class="list-title">记忆事件</div><div class="metric-value">${formatNumber(state.opsOverview.memory_ingestions)}</div></div>
    `;
  }

  const stores = el("ops-stores");
  if (!state.opsStores) {
    stores.className = "empty";
    stores.textContent = "尚未加载健康状态。";
  } else {
    const vectorStatus =
      typeof state.opsStores.vector_store_status === "object"
        ? JSON.stringify(state.opsStores.vector_store_status)
        : String(state.opsStores.vector_store_status || "-");
    stores.className = "detail-list";
    stores.innerHTML = `
      <div class="detail-list-item">
        <div class="detail-list-head"><strong>数据库</strong><span class="pill ${state.opsStores.database === "ok" ? "success" : "danger"}">${escapeHtml(state.opsStores.database)}</span></div>
      </div>
      <div class="detail-list-item">
        <div class="detail-list-head"><strong>向量检索</strong><span class="pill ${String(vectorStatus).includes("error") ? "danger" : "success"}">${escapeHtml(state.opsStores.vector_store_mode)}</span></div>
        <div class="helper">${escapeHtml(vectorStatus)}</div>
      </div>
      <div class="detail-list-item">
        <div class="detail-list-head"><strong>模型网关</strong><span class="pill ${String(state.opsStores.model_provider_status).includes("configured") ? "success" : "warning"}">${escapeHtml(state.opsStores.model_provider_mode)}</span></div>
        <div class="helper">${escapeHtml(state.opsStores.model_provider_status || "-")}</div>
      </div>
      <div class="detail-list-item">
        <div class="detail-list-head"><strong>资产仓库</strong><span class="pill">${escapeHtml(state.opsStores.warehouse_gateway_mode || "-")}</span></div>
        <div class="helper">${escapeHtml(state.opsStores.warehouse_base_url || "-")}</div>
      </div>
    `;
  }

  const workers = el("ops-workers");
  if (!state.opsWorkers.length) {
    workers.innerHTML = `<div class="empty">还没有 worker 心跳，先处理一次任务或启动 worker。</div>`;
  } else {
    workers.innerHTML = state.opsWorkers
      .map(
        (worker) => `
          <div class="list-item">
            <div class="list-head">
              <div class="list-title">${escapeHtml(worker.worker_name)}</div>
              <span class="pill ${worker.status === "idle" || worker.status === "running" ? "success" : worker.status === "stale" ? "warning" : "danger"}">${escapeHtml(worker.status)}</span>
            </div>
            <div class="list-meta muted">
              <span>last seen: ${formatDate(worker.last_seen_at)}</span>
              <span>last processed: ${formatDate(worker.last_processed_at)}</span>
            </div>
            <div class="helper">processed_count=${formatNumber(worker.processed_count)} ${worker.last_error ? `· last_error=${worker.last_error}` : ""}</div>
          </div>
        `,
      )
      .join("");
  }

  const failures = el("ops-failures");
  if (!failures) return;
  if (!state.opsFailures.length) {
    failures.innerHTML = `<div class="empty">最近没有失败或部分成功的任务。</div>`;
  } else {
    failures.innerHTML = state.opsFailures
      .map(
        (task) => `
          <div class="list-item">
            <div class="list-head">
              <div class="list-title">任务 #${task.id} · ${escapeHtml(task.task_type)}</div>
              <span class="pill ${task.status === "failed" ? "danger" : "warning"}">${escapeHtml(task.status)}</span>
            </div>
            <div class="list-subtitle">${escapeHtml((task.source_paths || []).join(", ") || "-")}</div>
            <div class="helper">知识库 #${task.kb_id} · ${formatDate(task.finished_at || task.created_at)}</div>
            <div class="helper">${escapeHtml(task.error_message || JSON.stringify(task.stats_json || {}))}</div>
            <div class="list-actions">
              <button class="secondary" data-action="show-task" data-task-id="${task.id}">查看详情</button>
              <button class="ghost" data-action="jump-view" data-view="tasks">前往任务页</button>
            </div>
          </div>
        `,
      )
      .join("");
  }
}

function renderSystemReadiness() {
  const box = el("system-readiness");
  if (!box) return;
  const checks = [
    { label: "钱包登录", ok: Boolean(state.token), detail: state.wallet ? "已登录" : "未登录" },
    {
      label: "资产仓库",
      ok: state.warehouseReady,
      detail: state.warehouseReady
        ? `读凭证 ${state.readCredentials.length} 个 · 写凭证 ${state.writeCredential ? "已配置" : "未配置"}`
        : "尚未导入仓库凭证",
    },
    {
      label: "向量检索",
      ok: Boolean(state.opsStores?.vector_store_status),
      detail:
        typeof state.opsStores?.vector_store_status === "object"
          ? JSON.stringify(state.opsStores.vector_store_status)
          : String(state.opsStores?.vector_store_status || "-"),
    },
    { label: "模型网关", ok: Boolean(state.opsStores?.model_provider_status), detail: String(state.opsStores?.model_provider_status || "-") },
  ];
  box.className = "readiness-grid";
  box.innerHTML = checks
    .map(
      (item) => `
        <div class="readiness-item">
          <strong>${escapeHtml(item.label)}</strong>
          <span class="pill ${item.ok ? "success" : "warning"}">${item.ok ? "就绪" : "待处理"}</span>
          <div class="helper">${escapeHtml(item.detail)}</div>
        </div>
      `,
    )
    .join("");
}

async function refreshOps() {
  const [overview, stores, workers, failures] = await Promise.all([
    api("/ops/overview"),
    api("/ops/stores/health"),
    api("/ops/workers"),
    api("/ops/tasks/failures"),
  ]);
  state.opsOverview = overview;
  state.opsStores = stores;
  state.opsWorkers = workers;
  state.opsFailures = failures;
  renderOps();
}

async function refreshKBs() {
  state.kbs = await api("/kbs");
  if (state.selectedKB) {
    const latest = state.kbs.find((kb) => kb.id === state.selectedKB.id);
    state.selectedKB = latest || state.kbs[0] || null;
  } else {
    state.selectedKB = state.kbs[0] || null;
  }
  updateSelectedKBUI();
  renderKBList();
  updateMetrics();
}

async function refreshCurrentKBStats() {
  if (!state.selectedKB) {
    state.currentKBStats = null;
    state.currentKBWorkbench = null;
    renderCurrentKBStats();
    renderKBWorkbench();
    return;
  }
  state.currentKBWorkbench = await api(`/kbs/${state.selectedKB.id}/workbench`);
  state.currentKBStats = state.currentKBWorkbench.stats;
  renderCurrentKBStats();
  renderKBWorkbench();
}

function renderKBList() {
  const list = el("kb-list");
  if (!state.kbs.length) {
    list.innerHTML = `<div class="empty">还没有知识库，先创建一个。</div>`;
    return;
  }
  list.innerHTML = state.kbs
    .map(
      (kb) => `
        <div class="list-item ${state.selectedKB?.id === kb.id ? "active" : ""}">
          <div class="list-head">
            <div>
              <div class="list-title">#${kb.id} ${escapeHtml(kb.name)}</div>
              <div class="list-subtitle">${escapeHtml(kb.description || "无描述")}</div>
            </div>
            <span class="pill">${escapeHtml(kb.status)}</span>
          </div>
          <div class="list-actions">
            <button class="secondary" data-action="select-kb" data-kb-id="${kb.id}">选中</button>
            <button class="ghost" data-action="edit-kb" data-kb-id="${kb.id}">编辑</button>
            <button class="ghost" data-action="show-kb" data-kb-id="${kb.id}">JSON</button>
            <button class="danger" data-action="delete-kb" data-kb-id="${kb.id}">删除</button>
          </div>
        </div>
      `,
    )
    .join("");
}

function currentKBOrThrow() {
  const kbId = state.selectedKB?.id || Number(el("task-kb-id").value || 0);
  if (!kbId) {
    throw new Error("请先选择知识库");
  }
  return kbId;
}

function kbPayloadFromForm() {
  return {
    name: el("kb-name").value.trim(),
    description: el("kb-desc").value.trim(),
    retrieval_config: {
      chunk_size: Number(el("kb-chunk-size").value || 800),
      chunk_overlap: Number(el("kb-chunk-overlap").value || 120),
      retrieval_top_k: Number(el("kb-retrieval-top-k").value || 6),
      memory_top_k: Number(el("kb-memory-top-k").value || 4),
      embedding_model: el("kb-embedding-model").value.trim() || "text-embedding-3-small",
    },
  };
}

async function createKB() {
  const payload = kbPayloadFromForm();
  if (!payload.name) {
    throw new Error("知识库名称不能为空");
  }
  const kb = await api("/kbs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setOutput(kb);
  await refreshKBs();
  await refreshSelectedData();
  closeKBEditor();
}

async function updateKB() {
  const kbId = currentKBOrThrow();
  const currentKB = state.kbs.find((kb) => kb.id === kbId) || state.selectedKB;
  const currentConfig = currentKB?.retrieval_config || {};
  const payload = kbPayloadFromForm();
  const chunkingConfigChanged =
    Number(currentConfig.chunk_size ?? 800) !== payload.retrieval_config.chunk_size ||
    Number(currentConfig.chunk_overlap ?? 120) !== payload.retrieval_config.chunk_overlap ||
    String(currentConfig.embedding_model ?? "text-embedding-3-small") !== payload.retrieval_config.embedding_model;
  const hasIndexedDocuments = state.documents.length > 0;
  const kb = await api(`/kbs/${kbId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let output = kb;
  if (chunkingConfigChanged && hasIndexedDocuments) {
    const processResult = await api("/tasks/process-pending", { method: "POST" });
    output = { kb, process_result: processResult };
    if (processResult.processed > 0) {
      notify("info", "chunk 参数已同步到已有文档并完成重建");
    } else {
      notify("info", "chunk 参数已更新，已有文档重建任务已排队或等待 worker 处理");
    }
  }
  setOutput(output);
  await refreshKBs();
  await refreshTasks();
  await refreshSelectedData();
  closeKBEditor();
}

async function deleteKB(kbId = null) {
  const targetId = kbId || currentKBOrThrow();
  const confirmed = await confirmAction("删除知识库", `确认删除知识库 #${targetId} 吗？索引、文档和绑定关系会一起删除。`);
  if (!confirmed) return;
  await api(`/kbs/${targetId}`, { method: "DELETE" });
  if (state.selectedKB?.id === targetId) {
    state.selectedKB = null;
  }
  setOutput({ ok: true, deleted_kb_id: targetId });
  await refreshKBs();
  await refreshSelectedData();
}

async function refreshBindings() {
  if (!state.selectedKB) {
    state.bindings = [];
    renderBindings();
    updateMetrics();
    return;
  }
  state.bindings = await api(`/kbs/${state.selectedKB.id}/bindings`);
  renderBindings();
  updateMetrics();
}

function renderBindings() {
  const list = el("binding-list");
  const helper = window.KnowledgeKBWorkbench;
  if (helper?.renderBindings) {
    list.innerHTML = helper.renderBindings({
      selectedKB: state.selectedKB,
      bindings: state.bindings,
      helpers: { escapeHtml, formatDate, formatNumber },
    });
    return;
  }
  if (!state.selectedKB) {
    list.innerHTML = `<div class="empty">先选中一个知识库。</div>`;
    return;
  }
  list.innerHTML = `<div class="code">${escapeHtml(JSON.stringify(state.bindings, null, 2))}</div>`;
}

function warehouseBrowseQuery(path, credentialId = currentBrowseCredentialId(), useWriteCredential = isBrowseUsingWriteCredential()) {
  const params = new URLSearchParams({ path });
  if (credentialId) params.set("credential_id", String(credentialId));
  if (useWriteCredential) params.set("use_write_credential", "true");
  return params.toString();
}

async function previewWarehouseFile(path, credentialId = currentBrowseCredentialId(), useWriteCredential = isBrowseUsingWriteCredential()) {
  if (!credentialId && !useWriteCredential) {
    throw new Error("请先选择浏览凭证");
  }
  const result = await api(`/warehouse/preview?${warehouseBrowseQuery(path, credentialId, useWriteCredential)}`);
  state.warehousePreview = result;
  renderWarehousePreview();
  setOutput(result);
}

async function addBinding(path = null, scopeType = "file") {
  const kbId = currentKBOrThrow();
  const sourcePath = (path || el("binding-path").value || "").trim();
  const credentialId = Number(el("binding-credential-id").value || 0);
  if (!sourcePath) {
    throw new Error("绑定路径不能为空");
  }
  if (!credentialId) {
    throw new Error("请选择读凭证");
  }
  const result = await api(`/kbs/${kbId}/bindings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_path: sourcePath, scope_type: scopeType, credential_id: credentialId }),
  });
  el("binding-path").value = sourcePath;
  setOutput(result);
  await refreshBindings();
  await refreshCurrentKBStats();
}

async function deleteBinding(bindingId) {
  const kbId = currentKBOrThrow();
  const confirmed = await confirmAction("解绑源路径", "解绑后不会删除 warehouse 原文件，但后续不会继续从该路径导入。");
  if (!confirmed) return;
  const result = await api(`/kbs/${kbId}/bindings/${bindingId}`, { method: "DELETE" });
  setOutput(result);
  await refreshBindings();
  await refreshCurrentKBStats();
}

async function updateBindingEnabled(bindingId, enabled) {
  const kbId = currentKBOrThrow();
  const actionLabel = enabled ? "启用" : "停用";
  const confirmed = await confirmAction(
    `${actionLabel}绑定源`,
    enabled
      ? "启用后，该绑定源会重新参与按绑定源创建的导入/重建/删除任务。"
      : "停用后，该绑定源不会参与按绑定源创建的导入/重建/删除任务，但已索引文档不会自动删除。",
  );
  if (!confirmed) return;
  const result = await api(`/kbs/${kbId}/bindings/${bindingId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: Boolean(enabled) }),
  });
  setOutput(result);
  await refreshBindings();
  await refreshCurrentKBStats();
}

async function browseWarehouse(path = null, credentialId = currentBrowseCredentialId(), useWriteCredential = isBrowseUsingWriteCredential()) {
  const targetPath = path || el("browse-path").value || currentWarehouseAppRoot();
  if (!credentialId && !useWriteCredential) {
    throw new Error("请先选择浏览凭证");
  }
  const data = await api(`/warehouse/browse?${warehouseBrowseQuery(targetPath, credentialId, useWriteCredential)}`);
  syncWarehouseConfig();
  state.currentBrowsePath = data.path;
  state.warehouseEntries = data.entries || [];
  el("browse-path").value = data.path;
  el("current-browse-path").textContent = data.path;
  renderBreadcrumbs();
  renderWarehouseEntries();
  if (state.pathPickerFieldId) {
    renderPathPicker(state.pathPickerFieldId);
  }
  setOutput(data);
}

function renderWarehouseEntries() {
  const list = el("warehouse-list");
  const keyword = (el("warehouse-filter")?.value || "").trim().toLowerCase();
  const entries = keyword
    ? state.warehouseEntries.filter((entry) => `${entry.name} ${entry.path}`.toLowerCase().includes(keyword))
    : state.warehouseEntries;
  if (!entries.length) {
    list.innerHTML = `<div class="empty">当前路径为空，或还没有文件。</div>`;
    return;
  }
  list.innerHTML = entries
    .map((entry) => {
      const nameCell = `${entry.entry_type === "directory" ? "📁" : "📄"} ${escapeHtml(entry.name)}`;
      return `
        <div class="table-row warehouse-table ${state.warehousePreview?.path === entry.path ? "active" : ""}">
          <div class="table-cell" title="${escapeHtml(entry.path)}">${nameCell}</div>
          <div class="table-cell">${escapeHtml(entry.entry_type)}</div>
          <div class="table-cell">${formatNumber(entry.size || 0)} B</div>
          <div class="table-cell">${formatDate(entry.modified_at)}</div>
          <div class="table-actions">
            ${entry.entry_type === "directory"
              ? `<button class="secondary" data-action="open-entry" data-path="${entry.path}">打开</button>`
              : `<button class="ghost" data-action="preview-entry" data-path="${entry.path}" data-name="${escapeHtml(entry.name)}">预览</button>`}
            <button class="ghost" data-action="bind-path" data-path="${entry.path}" data-scope="${entry.entry_type === "directory" ? "directory" : "file"}">绑定</button>
            <button data-action="import-path" data-path="${entry.path}">导入</button>
          </div>
        </div>
      `;
    })
    .join("");
}

async function uploadAppFile() {
  const fileInput = el("upload-file");
  if (!fileInput.files.length) {
    throw new Error("请选择文件");
  }
  if (!state.writeCredential) {
    throw new Error("请先配置写凭证");
  }
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  form.append("target_dir", el("target-dir").value || currentWarehouseUploadDir());
  const result = await api("/warehouse/upload", {
    method: "POST",
    body: form,
  });
  el("task-source-path").value = result.warehouse_path;
  el("binding-path").value = result.warehouse_path;
  setOutput(result);
  await refreshUploads();
  await browseWarehouse(el("target-dir").value || currentWarehouseUploadDir());
}

async function refreshUploads() {
  state.uploads = await api("/warehouse/uploads");
  const list = el("upload-list");
  if (!state.uploads.length) {
    list.innerHTML = `<div class="empty">暂无上传记录。</div>`;
    renderRecentActivity();
    return;
  }
  list.innerHTML = state.uploads
    .map(
      (upload) => `
        <div class="list-item">
          <div class="list-title">${escapeHtml(upload.file_name)}</div>
          <div class="list-subtitle">${escapeHtml(upload.warehouse_target_path)}</div>
          <div class="list-meta muted">
            <span>${upload.size} bytes</span>
            <span>${formatDate(upload.created_at)}</span>
          </div>
          <div class="list-actions">
            <button class="ghost" data-action="bind-path" data-path="${upload.warehouse_target_path}" data-scope="file">绑定</button>
            <button data-action="import-path" data-path="${upload.warehouse_target_path}" data-use-write-credential="true">导入</button>
            <button class="secondary" data-action="open-browse-path" data-path="${upload.warehouse_target_path}" data-use-write-credential="true">定位</button>
          </div>
        </div>
      `,
    )
    .join("");
  renderRecentActivity();
}

async function createTask(taskType) {
  const kbId = currentKBOrThrow();
  const sourcePath = (el("task-source-path").value || "").trim();
  if (!sourcePath) {
    throw new Error("源路径不能为空");
  }
  const credentialId = currentBrowseCredentialId() || currentWriteCredentialId();
  const result = await api(`/kbs/${kbId}/tasks/${taskType}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_paths: [sourcePath], credential_id: credentialId }),
  });
  setOutput(result);
  await refreshTasks();
}

function enabledBindingIds() {
  return (state.bindings || []).filter((binding) => binding.enabled).map((binding) => Number(binding.id));
}

async function createTaskFromBindings(taskType, bindingIds = []) {
  const kbId = currentKBOrThrow();
  const resolvedBindingIds = (bindingIds || []).map((value) => Number(value)).filter((value) => value > 0);
  if (!resolvedBindingIds.length && !enabledBindingIds().length) {
    throw new Error("当前知识库没有可用的已启用绑定源");
  }
  if (taskType === "delete") {
    const targetCount = resolvedBindingIds.length || enabledBindingIds().length;
    const confirmed = await confirmAction(
      "按绑定源删除索引",
      `将为 ${targetCount} 个绑定源创建删除任务，并清理这些绑定源对应的已索引文档。warehouse 原文件不会被删除。`,
    );
    if (!confirmed) return;
  }
  const result = await api(`/kbs/${kbId}/tasks/${taskType}-from-bindings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ binding_ids: resolvedBindingIds }),
  });
  setOutput(result);
  await refreshTasks();
}

async function createImportTask(path = null) {
  if (path) {
    el("task-source-path").value = path;
  }
  await createTask("import");
}

async function createImportTaskFromBindings(bindingIds = []) {
  await createTaskFromBindings("import", bindingIds);
}

async function createReindexTask() {
  await createTask("reindex");
}

async function createReindexTaskFromBindings(bindingIds = []) {
  await createTaskFromBindings("reindex", bindingIds);
}

async function createDeleteTask() {
  await createTask("delete");
}

async function createDeleteTaskFromBindings(bindingIds = []) {
  await createTaskFromBindings("delete", bindingIds);
}

async function retryTask(taskId) {
  const result = await api(`/tasks/${taskId}/retry`, { method: "POST" });
  setOutput(result);
  await refreshTasks();
}

async function cancelTask(taskId) {
  const task = state.tasks.find((item) => item.id === taskId) || state.selectedTaskDetail;
  const confirmed = await confirmAction(
    "取消任务",
    task?.status === "running" || task?.status === "cancel_requested"
      ? "运行中的任务会在当前文件处理完后自动回退已写入结果，并标记为 canceled。"
      : "未开始的任务会直接取消，不会进入执行队列。",
  );
  if (!confirmed) return;
  const result = await api(`/tasks/${taskId}/cancel`, { method: "POST" });
  setOutput(result);
  await refreshTasks();
  await refreshSelectedData();
}

async function showTask(taskId) {
  const [task, items] = await Promise.all([api(`/tasks/${taskId}`), api(`/tasks/${taskId}/items`)]);
  state.selectedTaskId = taskId;
  state.selectedTaskDetail = task;
  state.selectedTaskItems = items;
  renderTaskDetail();
}

async function refreshTasks() {
  state.tasks = await api("/tasks");
  if (state.selectedTaskId) {
    try {
      const [task, items] = await Promise.all([api(`/tasks/${state.selectedTaskId}`), api(`/tasks/${state.selectedTaskId}/items`)]);
      state.selectedTaskDetail = task;
      state.selectedTaskItems = items;
    } catch {
      state.selectedTaskId = null;
      state.selectedTaskDetail = null;
      state.selectedTaskItems = [];
    }
  }
  updateMetrics();
  const list = el("task-list");
  const statusFilter = el("task-status-filter")?.value || "";
  const tasks = statusFilter ? state.tasks.filter((task) => task.status === statusFilter) : state.tasks;
  if (!tasks.length) {
    list.innerHTML = `<div class="empty">暂无任务。</div>`;
    renderTaskDetail();
    renderRecentActivity();
    ensureTaskPolling();
    return;
  }
  if (window.KnowledgeTasksPanel?.renderTaskList) {
    list.innerHTML = window.KnowledgeTasksPanel.renderTaskList({
      tasks,
      selectedTaskId: state.selectedTaskId,
      helpers: { escapeHtml, formatDate },
    });
    renderTaskDetail();
    renderRecentActivity();
    ensureTaskPolling();
    return;
  }
  list.innerHTML = tasks
    .map(
      (task) => `
        <div class="table-row task-table ${state.selectedTaskId === task.id ? "active" : ""}">
          <div class="table-cell">#${task.id}</div>
          <div class="table-cell">
            <div>${escapeHtml(task.task_type)}</div>
            <div class="helper">${escapeHtml(task.queue_state || "-")}</div>
          </div>
          <div class="table-cell">
            <span class="pill ${toneForTaskStatus(task.status)}">${task.status}</span>
            <div class="helper">${escapeHtml(describeTaskQueue(task))}</div>
          </div>
          <div class="table-cell" title="${escapeHtml(task.source_paths.join(", "))}">${escapeHtml(task.source_paths.join(", "))}</div>
          <div class="table-cell">${formatDate(task.finished_at || task.created_at)}</div>
          <div class="table-actions">
            <button class="secondary" data-action="show-task" data-task-id="${task.id}">详情</button>
            ${task.cancelable ? `<button class="ghost" data-action="cancel-task" data-task-id="${task.id}">${task.status === "cancel_requested" ? "取消中" : "取消"}</button>` : ""}
            ${task.status === "failed" || task.status === "partial_success" ? `<button data-action="retry-task" data-task-id="${task.id}">重试</button>` : ""}
          </div>
        </div>
      `,
    )
    .join("");
  renderTaskDetail();
  renderRecentActivity();
  ensureTaskPolling();
}

async function processPendingTasks() {
  const result = await api("/tasks/process-pending", { method: "POST" });
  setOutput(result);
  if (result.worker_busy) {
    notify("info", result.message || "已有 worker 在处理任务，当前任务继续排队");
  }
  await refreshTasks();
  await refreshSelectedData();
}

async function refreshDocuments() {
  if (!state.selectedKB) {
    state.documents = [];
    state.selectedDocument = null;
    closeDocumentDrawer();
    renderDocuments();
    updateMetrics();
    renderRecentActivity();
    return;
  }
  state.documents = await api(`/kbs/${state.selectedKB.id}/documents`);
  if (state.selectedDocument) {
    const latestSummary = state.documents.find((doc) => doc.id === state.selectedDocument.id);
    if (latestSummary) {
      state.selectedDocument = { ...state.selectedDocument, ...latestSummary };
    }
  }
  if (state.selectedDocument && !state.documents.some((doc) => doc.id === state.selectedDocument.id)) {
    state.selectedDocument = null;
    closeDocumentDrawer();
    renderDocumentDetail(null);
  }
  renderDocuments();
  updateMetrics();
  renderRecentActivity();
}

function renderDocuments() {
  const list = el("document-list");
  const keyword = (el("document-filter")?.value || "").trim().toLowerCase();
  if (!state.selectedKB) {
    list.innerHTML = `<div class="empty">先选中一个知识库。</div>`;
    return;
  }
  const documents = keyword
    ? state.documents.filter((doc) =>
        `${doc.source_file_name} ${doc.source_path} ${doc.file_type || ""}`.toLowerCase().includes(keyword),
      )
    : state.documents;
  if (!documents.length) {
    list.innerHTML = `<div class="empty">当前知识库还没有导入文档。</div>`;
    return;
  }
  list.innerHTML = documents
    .map(
      (doc) => `
        <div class="table-row document-table ${state.selectedDocument?.id === doc.id ? "active" : ""}">
          <div class="table-cell" title="${escapeHtml(doc.source_path)}">${escapeHtml(doc.source_file_name)}</div>
          <div class="table-cell">${escapeHtml(doc.file_type || "-")}</div>
          <div class="table-cell">${escapeHtml(doc.source_kind)}</div>
          <div class="table-cell">${formatNumber(doc.chunk_count)}</div>
          <div class="table-cell">${formatDate(doc.last_indexed_at)}</div>
          <div class="table-actions">
            <button class="secondary" data-action="select-document" data-doc-id="${doc.id}">查看详情</button>
            <button class="ghost" data-action="reindex-path" data-path="${doc.source_path}">重建</button>
            <button class="danger" data-action="delete-document" data-doc-id="${doc.id}">删除</button>
          </div>
        </div>
      `,
    )
    .join("");
}

async function showDocument(docId) {
  if (!state.selectedKB) {
    throw new Error("请先选择知识库");
  }
  const detail = await api(`/kbs/${state.selectedKB.id}/documents/${docId}`);
  syncDocumentSummary(detail);
  state.selectedDocument = detail;
  renderDocuments();
  renderDocumentDetail(detail);
  openDocumentDrawer(detail);
  setOutput(detail);
}

async function deleteDocument(docId) {
  if (!state.selectedKB) {
    throw new Error("请先选择知识库");
  }
  const confirmed = await confirmAction("删除索引文档", "该操作只删除 knowledge 中的索引文档，不会删除 warehouse 原始文件。");
  if (!confirmed) return;
  const result = await api(`/kbs/${state.selectedKB.id}/documents/${docId}`, { method: "DELETE" });
  setOutput(result);
  state.selectedDocument = null;
  closeDocumentDrawer();
  renderDocumentDetail(null);
  await refreshDocuments();
  await refreshCurrentKBStats();
}

async function refreshLongMemory() {
  state.longMemories = await api("/memory/long-term");
  const list = el("long-memory-list");
  if (!state.longMemories.length) {
    list.innerHTML = `<div class="empty">暂无长期记忆。</div>`;
    return;
  }
  list.innerHTML = state.longMemories
    .map(
      (memory) => `
        <div class="list-item">
          <div class="list-head">
            <div class="list-title">${escapeHtml(memory.category)}</div>
            <span class="pill">${escapeHtml(memory.source)}</span>
          </div>
          <div class="list-subtitle">${escapeHtml(memory.content)}</div>
          <div class="list-actions">
            <button class="ghost" data-action="show-long-memory" data-memory-id="${memory.id}">详情</button>
            <button class="danger" data-action="delete-long-memory" data-memory-id="${memory.id}">删除</button>
          </div>
        </div>
      `,
    )
    .join("");
}

async function saveLongMemory() {
  const payload = {
    kb_id: el("memory-kb-id").value ? Number(el("memory-kb-id").value) : null,
    category: el("memory-category").value || "general",
    content: el("memory-content").value.trim(),
    source: "console",
    score: 100,
  };
  if (!payload.content) {
    throw new Error("长期记忆内容不能为空");
  }
  const result = await api("/memory/long-term", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setOutput(result);
  el("memory-content").value = "";
  await refreshLongMemory();
}

async function deleteLongMemory(memoryId) {
  const confirmed = await confirmAction("删除长期记忆", "删除后该条长期记忆将不再出现在检索上下文中。");
  if (!confirmed) return;
  const result = await api(`/memory/long-term/${memoryId}`, { method: "DELETE" });
  setOutput(result);
  await Promise.all([refreshLongMemory(), refreshMemoryIngestions()]);
}

async function refreshShortMemory() {
  const sessionId = el("short-session-id").value || "";
  const memoryNamespace = el("short-memory-namespace").value.trim();
  const params = new URLSearchParams();
  if (sessionId) params.set("session_id", sessionId);
  if (memoryNamespace) params.set("memory_namespace", memoryNamespace);
  state.shortMemories = await api(`/memory/short-term?${params.toString()}`);
  const list = el("short-memory-list");
  if (!state.shortMemories.length) {
    list.innerHTML = `<div class="empty">当前 session 暂无短期记忆。</div>`;
    return;
  }
  list.innerHTML = state.shortMemories
    .map(
      (memory) => `
        <div class="list-item">
          <div class="list-head">
            <div class="list-title">${escapeHtml(memory.memory_type)}</div>
            <div>
              <span class="pill">${escapeHtml(memory.session_id)}</span>
              ${memory.memory_namespace ? `<span class="pill">${escapeHtml(memory.memory_namespace)}</span>` : ""}
            </div>
          </div>
          <div class="list-subtitle">${escapeHtml(memory.content)}</div>
          <div class="list-actions">
            <button class="ghost" data-action="show-short-memory" data-memory-id="${memory.id}">详情</button>
            <button class="danger" data-action="delete-short-memory" data-memory-id="${memory.id}">删除</button>
          </div>
        </div>
      `,
    )
    .join("");
}

async function refreshMemoryIngestions() {
  state.memoryIngestions = await api("/memory/ingestions?limit=12");
  renderMemoryIngestions();
  renderRecentActivity();
}

function renderMemoryIngestions() {
  const list = el("memory-ingestion-list");
  if (!list) return;
  if (!state.memoryIngestions.length) {
    list.innerHTML = `<div class="empty">还没有沉淀或记忆操作记录。先在“检索与上下文”里沉淀一轮记忆，或在控制台增删记忆。</div>`;
    return;
  }
  list.innerHTML = state.memoryIngestions
    .map((event) => {
      const summary = summarizeMemoryEvent(event);
      return `
        <div class="list-item">
          <div class="list-head">
            <div>
              <div class="list-title">${escapeHtml(summary.title)}</div>
              <div class="list-subtitle">${escapeHtml(summary.subtitle || "无摘要")}</div>
            </div>
            <span class="pill ${toneForMemoryEvent(event)}">${escapeHtml(event.status)}</span>
          </div>
          <div class="helper">来源：${escapeHtml(event.source)} · ${formatDate(event.created_at)} · trace=${escapeHtml(event.trace_id || "-")}</div>
          <div class="helper">${escapeHtml(summary.detail)}</div>
          <div class="helper">知识源：${escapeHtml((event.source_refs_json || []).join(", ") || "-")}</div>
          ${summary.secondary ? `<div class="helper">${escapeHtml(summary.secondary)}</div>` : ""}
          <div class="list-actions">
            <button class="secondary" data-action="show-memory-ingestion" data-event-id="${event.id}">详情</button>
            <button class="ghost" data-action="jump-view" data-view="${summary.nextView}">${escapeHtml(summary.nextLabel)}</button>
          </div>
        </div>
      `;
    })
    .join("");
}

async function saveShortMemory() {
  const payload = {
    session_id: el("short-session-id").value.trim(),
    memory_namespace: el("short-memory-namespace").value.trim() || null,
    memory_type: el("short-memory-type").value,
    content: el("short-memory-content").value.trim(),
  };
  if (!payload.session_id || !payload.content) {
    throw new Error("session_id 和短期记忆内容不能为空");
  }
  const result = await api("/memory/short-term", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setOutput(result);
  el("short-memory-content").value = "";
  await refreshShortMemory();
}

async function deleteShortMemory(memoryId) {
  const confirmed = await confirmAction("删除短期记忆", "删除后该条短期记忆不会再参与 retrieval-context 生成。");
  if (!confirmed) return;
  const result = await api(`/memory/short-term/${memoryId}`, { method: "DELETE" });
  setOutput(result);
  await Promise.all([refreshShortMemory(), refreshMemoryIngestions()]);
}

async function refreshRetrievalLogs() {
  if (!state.selectedKB) {
    state.retrievalLogs = [];
    renderRetrievalLogs();
    return;
  }
  state.retrievalLogs = await api(`/kbs/${state.selectedKB.id}/retrieval-logs`);
  renderRetrievalLogs();
}

async function refreshSourceGovernance() {
  if (!state.selectedKB) {
    state.sourceGovernance = null;
    renderSourceGovernance();
    return;
  }
  state.sourceGovernance = await api(`/kbs/${state.selectedKB.id}/source-governance`);
  renderSourceGovernance();
}

async function runSearchLabCompare() {
  const kbId = currentKBOrThrow();
  const query = (el("search-lab-query").value || "").trim();
  const topK = Number(el("search-lab-top-k").value || 5) || 5;
  const resultView = el("search-lab-result-view").value || "audit";
  const availabilityMode = el("search-lab-availability-mode").value || "allow_all";
  if (!query) {
    throw new Error("请先输入 query");
  }
  const result = await api(`/kbs/${kbId}/search-lab/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      top_k: topK,
      result_view: resultView,
      availability_mode: availabilityMode,
    }),
  });
  state.searchLabCompare = result;
  setOutput(result);
  await Promise.all([refreshRetrievalLogs(), refreshSourceGovernance()]);
  renderSearchLabCompare();
}

function renderSearchLabMode(modeTitle, payload) {
  const hits = payload?.hits || [];
  return `
    <div class="detail-list-item">
      <div class="detail-list-head"><strong>${escapeHtml(modeTitle)}</strong><span class="pill">${escapeHtml(payload?.mode || "-")}</span></div>
      ${
        hits.length
          ? hits
              .map(
                (hit) => `
                  <div class="list-item">
                    <div class="list-head">
                      <div class="list-title">${escapeHtml(hit.title || hit.text || hit.result_kind || "-")}</div>
                      <span class="pill ${hit.content_health_status === "healthy" ? "success" : hit.content_health_status === "stale" ? "warning" : "danger"}">${escapeHtml(hit.content_health_status)}</span>
                    </div>
                    <div class="list-subtitle">${escapeHtml(hit.statement || hit.text || "")}</div>
                    <div class="helper">kind=${escapeHtml(hit.result_kind)} · score=${escapeHtml(String(hit.score ?? "-"))}</div>
                    ${hit.source_refs?.length ? `<div class="helper">sources=${escapeHtml(hit.source_refs.join(", "))}</div>` : ""}
                    ${hit.audit_info && Object.keys(hit.audit_info).length ? `<div class="helper">${escapeHtml(JSON.stringify(hit.audit_info))}</div>` : ""}
                  </div>
                `,
              )
              .join("")
          : `<div class="empty">无命中。</div>`
      }
    </div>
  `;
}

function renderSearchLabCompare() {
  const box = el("search-lab-compare");
  if (!box) return;
  if (!state.selectedKB) {
    box.className = "empty";
    box.textContent = "先选择知识库。";
    return;
  }
  if (!state.searchLabCompare) {
    box.className = "empty";
    box.textContent = "输入 query 后运行 search lab，对比 formal/evidence/formal_first。";
    return;
  }
  const payload = state.searchLabCompare;
  box.className = "detail-list";
  box.innerHTML = `
    <div class="detail-list-item">
      <div class="detail-list-head"><strong>当前发布面</strong><span class="pill">${escapeHtml(payload.current_release?.version || "workspace-only")}</span></div>
      <div class="helper">query=${escapeHtml(payload.query)} · retrieval_log_id=${escapeHtml(String(payload.retrieval_log_id || "-"))}</div>
    </div>
    ${renderSearchLabMode("Formal Only", payload.formal_only)}
    ${renderSearchLabMode("Evidence Only", payload.evidence_only)}
    ${renderSearchLabMode("Formal First", payload.formal_first)}
  `;
}

function renderRetrievalLogs() {
  const list = el("search-lab-log-list");
  if (!list) return;
  if (!state.selectedKB) {
    list.innerHTML = `<div class="empty">先选择知识库后查看检索日志。</div>`;
    return;
  }
  if (!state.retrievalLogs.length) {
    list.innerHTML = `<div class="empty">当前知识库还没有检索日志。</div>`;
    return;
  }
  list.innerHTML = state.retrievalLogs
    .map(
      (log) => `
        <div class="list-item">
          <div class="list-head">
            <div class="list-title">${escapeHtml(log.query)}</div>
            <span class="pill">${escapeHtml(log.query_mode)}</span>
          </div>
          <div class="helper">release_id=${escapeHtml(String(log.release_id || "-"))} · ${formatDate(log.created_at)}</div>
          <div class="helper">${escapeHtml(JSON.stringify(log.result_summary_json || {}))}</div>
          <div class="list-actions">
            <button class="ghost" data-action="show-retrieval-log" data-log-id="${log.id}">详情</button>
          </div>
        </div>
      `,
    )
    .join("");
}

function renderSourceGovernance() {
  const box = el("search-lab-governance");
  if (!box) return;
  if (!state.selectedKB) {
    box.className = "empty";
    box.textContent = "先选择知识库后查看来源治理信息。";
    return;
  }
  if (!state.sourceGovernance) {
    box.className = "empty";
    box.textContent = "当前知识库尚未加载来源治理信息。";
    return;
  }
  const counts = state.sourceGovernance.status_counts || {};
  const assets = state.sourceGovernance.assets || [];
  box.className = "detail-list";
  box.innerHTML = `
    <div class="detail-list-item">
      <div class="detail-list-head"><strong>治理摘要</strong><span class="pill">sources=${escapeHtml(String(counts.sources_total || 0))}</span></div>
      <div class="helper">source_missing=${escapeHtml(String(counts.source_missing || 0))} · stale=${escapeHtml(String(counts.stale || 0))} · assets_missing=${escapeHtml(String(counts.assets_missing || 0))}</div>
    </div>
    ${
      assets.length
        ? assets
            .map(
              (asset) => `
                <div class="list-item">
                  <div class="list-head">
                    <div class="list-title">${escapeHtml(asset.asset_path)}</div>
                    <span class="pill ${asset.availability_status === "missing" ? "danger" : "warning"}">${escapeHtml(asset.availability_status)}</span>
                  </div>
                  <div class="helper">source_id=${escapeHtml(String(asset.source_id))} · evidence_count=${escapeHtml(String(asset.evidence_count || 0))}</div>
                </div>
              `,
            )
            .join("")
        : `<div class="empty">当前没有需要治理的 source_missing / stale 资产。</div>`
    }
  `;
}

async function refreshSelectedData() {
  await Promise.all([refreshBindings(), refreshDocuments(), refreshCurrentKBStats(), refreshRetrievalLogs(), refreshSourceGovernance()]);
}

async function refreshAll() {
  if (!state.token) {
    renderAll();
    return;
  }
  await Promise.all([
    refreshWarehouseStatus(),
    refreshReadCredentials(),
    refreshWriteCredential(),
    refreshKBs(),
    refreshTasks(),
    refreshUploads(),
    refreshOps(),
  ]);
  await refreshSelectedData();
  renderAll();
}

function renderAll() {
  renderWalletSummary();
  updateSelectedKBUI();
  renderKBList();
  updateWarehouseCredentialSelectors();
  renderReadCredentials();
  renderWriteCredential();
  renderBindings();
  renderKBWorkbench();
  renderWarehouseEntries();
  renderWarehousePreview();
  renderDocuments();
  renderCurrentKBStats();
  renderBreadcrumbs();
  renderTaskDetail();
  renderOps();
  renderSearchLabCompare();
  renderRetrievalLogs();
  renderSourceGovernance();
  renderSystemReadiness();
  updateMetrics();
  renderRecentActivity();
}

function attachStaticEvents() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.addEventListener("click", () => setView(item.dataset.viewTarget));
  });

  el("jump-dashboard").addEventListener("click", () => setView("dashboard"));
  el("jump-warehouse").addEventListener("click", () => setView("warehouse"));
  if (el("jump-search-lab")) {
    el("jump-search-lab").addEventListener("click", () => setView("search-lab"));
  }
  el("refresh-ops").addEventListener("click", () => withFeedback(refreshOps, "运维状态已刷新")().catch(() => {}));

  el("connect-wallet").addEventListener("click", () => withFeedback(loginWithWallet, "knowledge 登录成功")().catch(() => {}));
  el("logout-button").addEventListener("click", () => withFeedback(logout, "已退出 knowledge")().catch(() => {}));
  el("dashboard-refresh-all").addEventListener("click", () => withFeedback(refreshAll, "已刷新全部数据")().catch(() => {}));

  el("refresh-kbs").addEventListener("click", () => withFeedback(refreshKBs, "知识库列表已刷新")().catch(() => {}));
  el("open-create-kb").addEventListener("click", () => {
    try {
      openKBEditor("create");
    } catch (err) {
      notify("error", err.message);
    }
  });
  el("open-edit-kb").addEventListener("click", () => {
    try {
      openKBEditor("edit");
    } catch (err) {
      notify("error", err.message);
    }
  });
  el("create-kb").addEventListener("click", () => withFeedback(createKB, "知识库创建成功")().catch(() => {}));
  el("update-kb").addEventListener("click", () => withFeedback(updateKB, "知识库配置已更新")().catch(() => {}));
  el("delete-kb").addEventListener("click", () => withFeedback(() => deleteKB(), "知识库已删除")().catch(() => {}));
  el("refresh-bindings").addEventListener("click", () => withFeedback(refreshBindings, "绑定源已刷新")().catch(() => {}));
  el("add-binding").addEventListener("click", () => withFeedback(() => addBinding(), "绑定源已添加")().catch(() => {}));
  el("save-read-credential").addEventListener("click", () =>
    withFeedback(saveReadCredential, "读凭证已导入")().catch(() => {}),
  );

  el("browse-warehouse").addEventListener("click", () => withFeedback(() => browseWarehouse(), "仓库目录已刷新")().catch(() => {}));
  el("browse-app-root").addEventListener("click", () => withFeedback(() => browseWarehouse(currentWarehouseAppRoot()))().catch(() => {}));
  el("browse-upload-root").addEventListener("click", () => withFeedback(() => browseWarehouse(currentWarehouseUploadDir()))().catch(() => {}));
  el("warehouse-access-source").addEventListener("change", (event) => {
    state.browseAccessSource = event.target.value || "";
  });
  el("clear-warehouse-filter").addEventListener("click", () => {
    el("warehouse-filter").value = "";
    renderWarehouseEntries();
  });
  el("warehouse-filter").addEventListener("input", () => renderWarehouseEntries());
  el("path-picker-close").addEventListener("click", () => closePathPicker());
  el("path-picker-filter").addEventListener("input", () => {
    if (state.pathPickerFieldId) {
      renderPathPicker(state.pathPickerFieldId);
    }
  });
  document.querySelectorAll("[data-path-field]").forEach((node) => {
    node.addEventListener("focus", () => renderPathPicker(node.id));
    node.addEventListener("click", () => renderPathPicker(node.id));
  });
  el("save-write-credential").addEventListener("click", () =>
    withFeedback(saveWriteCredential, "写凭证已保存")().catch(() => {}),
  );
  el("delete-write-credential").addEventListener("click", () =>
    withFeedback(deleteWriteCredential, "写凭证已删除")().catch(() => {}),
  );
  el("upload-app").addEventListener("click", () => withFeedback(uploadAppFile, "文件已上传到 Knowledge App 目录")().catch(() => {}));
  el("refresh-uploads").addEventListener("click", () => withFeedback(refreshUploads, "上传记录已刷新")().catch(() => {}));

  el("create-import-task").addEventListener("click", () => withFeedback(() => createImportTask(), "导入任务已创建")().catch(() => {}));
  el("create-reindex-task").addEventListener("click", () => withFeedback(createReindexTask, "重建任务已创建")().catch(() => {}));
  el("create-delete-task").addEventListener("click", () => withFeedback(createDeleteTask, "删除任务已创建")().catch(() => {}));
  el("create-import-from-bindings").addEventListener("click", () =>
    withFeedback(createImportTaskFromBindings, "已按绑定源创建导入任务")().catch(() => {}),
  );
  el("create-reindex-from-bindings").addEventListener("click", () =>
    withFeedback(createReindexTaskFromBindings, "已按绑定源创建重建任务")().catch(() => {}),
  );
  el("create-delete-from-bindings").addEventListener("click", () =>
    withFeedback(createDeleteTaskFromBindings, "已按绑定源创建删除任务")().catch(() => {}),
  );
  el("clear-task-filter").addEventListener("click", () => {
    el("task-status-filter").value = "";
    refreshTasks().catch((err) => {
      notify("error", err.message);
      setOutput(err.message);
    });
  });
  el("task-status-filter").addEventListener("change", () => {
    refreshTasks().catch((err) => {
      notify("error", err.message);
      setOutput(err.message);
    });
  });
  el("refresh-tasks").addEventListener("click", () => withFeedback(refreshTasks, "任务列表已刷新")().catch(() => {}));
  el("process-pending-tasks").addEventListener("click", () => withFeedback(processPendingTasks, "已处理待执行任务")().catch(() => {}));

  el("clear-document-filter").addEventListener("click", () => {
    el("document-filter").value = "";
    renderDocuments();
  });
  el("document-filter").addEventListener("input", () => renderDocuments());
  el("refresh-documents").addEventListener("click", () => withFeedback(refreshDocuments, "文档列表已刷新")().catch(() => {}));
  el("run-search-lab").addEventListener("click", () => withFeedback(runSearchLabCompare, "Search Lab 对比已更新")().catch(() => {}));
  el("refresh-search-lab").addEventListener("click", () =>
    withFeedback(async () => {
      await Promise.all([refreshRetrievalLogs(), refreshSourceGovernance()]);
      renderSearchLabCompare();
    }, "Search Lab 数据已刷新")().catch(() => {}),
  );
  el("refresh-retrieval-logs").addEventListener("click", () => withFeedback(refreshRetrievalLogs, "检索日志已刷新")().catch(() => {}));
  el("refresh-source-governance").addEventListener("click", () =>
    withFeedback(refreshSourceGovernance, "来源治理信息已刷新")().catch(() => {}),
  );

  el("confirm-cancel").addEventListener("click", () => closeConfirm(false));
  el("confirm-ok").addEventListener("click", () => closeConfirm(true));
  el("drawer-close").addEventListener("click", () => closeDrawer());
  el("detail-drawer").addEventListener("click", (event) => {
    if (event.target.id === "detail-drawer") {
      closeDrawer();
    }
  });
  el("kb-editor-close").addEventListener("click", () => closeKBEditor());
  el("kb-editor-drawer").addEventListener("click", (event) => {
    if (event.target.id === "kb-editor-drawer") {
      closeKBEditor();
    }
  });
  el("document-drawer-close").addEventListener("click", () => closeDocumentDrawer());
  el("document-drawer").addEventListener("click", (event) => {
    if (event.target.id === "document-drawer") {
      closeDocumentDrawer();
    }
  });
  el("path-picker-menu").addEventListener("click", (event) => {
    if (event.target.id === "path-picker-menu") {
      closePathPicker();
    }
  });
  document.addEventListener("click", (event) => {
    const menu = el("path-picker-menu");
    if (!menu || menu.classList.contains("hidden")) return;
    const target = event.target;
    if (!(target instanceof Node)) return;
    const insideMenu = menu.contains(target);
    const insideField = target instanceof Element && Boolean(target.closest("[data-path-field], .path-input-wrap"));
    if (!insideMenu && !insideField) {
      closePathPicker();
    }
  });

  document.body.addEventListener("click", (event) => {
    const pickerTarget = event.target.closest("[data-picker-select]");
    if (pickerTarget) {
      const fieldId = pickerTarget.dataset.pickerTarget;
      const path = pickerTarget.dataset.pickerPath;
      if (fieldId && path && el(fieldId)) {
        el(fieldId).value = path;
        closePathPicker();
        if (fieldId === "browse-path") {
          withFeedback(() => browseWarehouse(path), "已切换仓库路径")().catch(() => {});
        }
      }
      return;
    }
    const pickerNav = event.target.closest("[data-picker-nav-path]");
    if (pickerNav) {
      const path = pickerNav.dataset.pickerNavPath;
      if (path) {
        browseWarehouse(path).catch((err) => {
          notify("error", err.message);
          setOutput(err.message);
        });
      }
      return;
    }

    const target = event.target.closest("[data-action]");
    if (!target) return;
    const { action } = target.dataset;
    if (action === "select-kb") {
      const kb = state.kbs.find((item) => item.id === Number(target.dataset.kbId));
      state.selectedKB = kb || null;
      refreshSelectedData()
        .then(renderAll)
        .then(() => notify("info", "已切换当前知识库"))
        .catch((err) => {
          notify("error", err.message);
          setOutput(err.message);
        });
      return;
    }
    if (action === "show-kb") {
      const kb = state.kbs.find((item) => item.id === Number(target.dataset.kbId));
      if (kb) openDrawer(`知识库 #${kb.id}`, kb);
      return;
    }
    if (action === "edit-kb") {
      const kb = state.kbs.find((item) => item.id === Number(target.dataset.kbId));
      if (!kb) return;
      state.selectedKB = kb;
      updateSelectedKBUI();
      renderKBList();
      try {
        openKBEditor("edit", kb);
      } catch (err) {
        notify("error", err.message);
      }
      return;
    }
    if (action === "delete-kb") {
      withFeedback(() => deleteKB(Number(target.dataset.kbId)), "知识库已删除")().catch(() => {});
      return;
    }
    if (action === "delete-binding") {
      withFeedback(() => deleteBinding(Number(target.dataset.bindingId)), "绑定源已解绑")().catch(() => {});
      return;
    }
    if (action === "reveal-read-credential") {
      withFeedback(() => revealReadCredential(Number(target.dataset.credentialId)))().catch(() => {});
      return;
    }
    if (action === "delete-read-credential") {
      withFeedback(() => deleteReadCredential(Number(target.dataset.credentialId)), "读凭证已删除")().catch(() => {});
      return;
    }
    if (action === "use-read-credential") {
      const credentialId = Number(target.dataset.credentialId || 0);
      if (credentialId > 0) {
        const bindingSelect = el("binding-credential-id");
        if (bindingSelect) bindingSelect.value = String(credentialId);
        const browseSelect = el("warehouse-access-source");
        if (browseSelect) browseSelect.value = `read:${credentialId}`;
        state.browseAccessSource = `read:${credentialId}`;
        notify("success", "已切换到该读凭证");
      }
      return;
    }
    if (action === "reveal-write-credential") {
      withFeedback(revealWriteCredential)().catch(() => {});
      return;
    }
    if (action === "use-write-credential") {
      const browseSelect = el("warehouse-access-source");
      if (browseSelect) browseSelect.value = "write";
      state.browseAccessSource = "write";
      notify("success", "已切换到写凭证浏览");
      return;
    }
    if (action === "open-entry") {
      withFeedback(() => browseWarehouse(target.dataset.path))().catch(() => {});
      return;
    }
    if (action === "preview-entry") {
      withFeedback(() => previewWarehouseFile(target.dataset.path))().catch(() => {});
      return;
    }
    if (action === "bind-path") {
      withFeedback(() => addBinding(target.dataset.path, target.dataset.scope), "路径已绑定到当前知识库")().catch(() => {});
      return;
    }
    if (action === "import-path") {
      if (target.dataset.useWriteCredential === "true") {
        const browseSelect = el("warehouse-access-source");
        if (browseSelect && state.writeCredential) {
          browseSelect.value = "write";
        }
        if (state.writeCredential) {
          state.browseAccessSource = "write";
        }
      }
      withFeedback(() => createImportTask(target.dataset.path), "导入任务已创建")().catch(() => {});
      return;
    }
    if (action === "import-binding") {
      withFeedback(() => createImportTaskFromBindings([Number(target.dataset.bindingId)]), "已按绑定源创建导入任务")().catch(() => {});
      return;
    }
    if (action === "reindex-binding") {
      withFeedback(() => createReindexTaskFromBindings([Number(target.dataset.bindingId)]), "已按绑定源创建重建任务")().catch(() => {});
      return;
    }
    if (action === "disable-binding") {
      withFeedback(() => updateBindingEnabled(Number(target.dataset.bindingId), false), "绑定源已停用")().catch(() => {});
      return;
    }
    if (action === "enable-binding") {
      withFeedback(() => updateBindingEnabled(Number(target.dataset.bindingId), true), "绑定源已启用")().catch(() => {});
      return;
    }
    if (action === "open-browse-path") {
      const path = target.dataset.path || "/";
      const targetPath = path.includes("/") ? path.replace(/\/[^/]+$/, "") || "/" : path;
      if (target.dataset.credentialId) {
        const browseSelect = el("warehouse-access-source");
        if (browseSelect) browseSelect.value = `read:${target.dataset.credentialId}`;
        state.browseAccessSource = `read:${target.dataset.credentialId}`;
      } else if (target.dataset.useWriteCredential === "true") {
        const browseSelect = el("warehouse-access-source");
        if (browseSelect) browseSelect.value = "write";
        state.browseAccessSource = "write";
      }
      withFeedback(() => browseWarehouse(targetPath))().catch(() => {});
      setView("warehouse");
      return;
    }
    if (action === "crumb") {
      withFeedback(() => browseWarehouse(target.dataset.path))().catch(() => {});
      return;
    }
    if (action === "show-task") {
      if (target.dataset.jumpView) setView(target.dataset.jumpView);
      showTask(Number(target.dataset.taskId)).catch((err) => {
        notify("error", err.message);
        setOutput(err.message);
      });
      return;
    }
    if (action === "retry-task") {
      withFeedback(() => retryTask(Number(target.dataset.taskId)), "已创建重试任务")().catch(() => {});
      return;
    }
    if (action === "cancel-task") {
      withFeedback(() => cancelTask(Number(target.dataset.taskId)), "任务取消请求已提交")().catch(() => {});
      return;
    }
    if (action === "select-document") {
      if (target.dataset.jumpView) setView(target.dataset.jumpView);
      withFeedback(() => showDocument(Number(target.dataset.docId)))().catch(() => {});
      return;
    }
    if (action === "show-document-json") {
      if (state.selectedDocument) {
        openDrawer(`文档详情：${state.selectedDocument.source_file_name}`, state.selectedDocument);
      }
      return;
    }
    if (action === "show-chunk-detail") {
      const chunk = state.selectedDocument?.chunks?.find((item) => item.id === Number(target.dataset.chunkId));
      if (chunk) {
        openDrawer(`Chunk #${chunk.chunk_index}`, chunk);
      }
      return;
    }
    if (action === "fill-task-source") {
      el("task-source-path").value = target.dataset.path || "";
      setView("tasks");
      notify("success", "已填入任务源路径");
      return;
    }
    if (action === "reindex-path") {
      el("task-source-path").value = target.dataset.path;
      withFeedback(createReindexTask, "重建任务已创建")().catch(() => {});
      return;
    }
    if (action === "delete-document") {
      withFeedback(() => deleteDocument(Number(target.dataset.docId)), "索引文档已删除")().catch(() => {});
      return;
    }
    if (action === "delete-long-memory") {
      withFeedback(() => deleteLongMemory(Number(target.dataset.memoryId)), "长期记忆已删除")().catch(() => {});
      return;
    }
    if (action === "delete-short-memory") {
      withFeedback(() => deleteShortMemory(Number(target.dataset.memoryId)), "短期记忆已删除")().catch(() => {});
      return;
    }
    if (action === "show-long-memory") {
      const memory = state.longMemories.find((item) => item.id === Number(target.dataset.memoryId));
      if (memory) openDrawer(`长期记忆 #${memory.id}`, memory);
      return;
    }
    if (action === "show-short-memory") {
      const memory = state.shortMemories.find((item) => item.id === Number(target.dataset.memoryId));
      if (memory) openDrawer(`短期记忆 #${memory.id}`, memory);
      return;
    }
    if (action === "show-memory-ingestion") {
      if (target.dataset.jumpView) setView(target.dataset.jumpView);
      const eventRow = state.memoryIngestions.find((item) => item.id === Number(target.dataset.eventId));
      if (eventRow) openDrawer(`记忆沉淀 #${eventRow.id}`, eventRow);
      return;
    }
    if (action === "show-retrieval-log") {
      const log = state.retrievalLogs.find((item) => item.id === Number(target.dataset.logId));
      if (log) openDrawer(`检索日志 #${log.id}`, log);
      return;
    }
    if (action === "jump-view") {
      setView(target.dataset.view || "dashboard");
      return;
    }
  });
}

attachStaticEvents();

if (state.token && state.wallet) {
  setLoggedIn(state.wallet);
  refreshAll().catch((err) => {
    notify("error", err.message);
    setOutput(err.message);
  });
} else {
  renderLoggedOutState();
  renderAll();
}
