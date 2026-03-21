window.KnowledgeKBWorkbench = (() => {
  function syncTone(syncStatus) {
    const status = String(syncStatus || "").trim().toLowerCase();
    if (status === "indexed") return "success";
    if (status === "syncing") return "warning";
    if (status === "failed") return "danger";
    if (status === "disabled") return "muted";
    return "info";
  }

  function renderBindings({ selectedKB, bindings, helpers }) {
    if (!selectedKB) {
      return `<div class="empty">先选中一个知识库。</div>`;
    }
    if (!bindings.length) {
      return `<div class="empty">当前知识库还没有绑定源。</div>`;
    }
    const { escapeHtml, formatDate, formatNumber } = helpers;
    return bindings
      .map(
        (binding) => `
          <div class="list-item">
            <div class="list-title">${escapeHtml(binding.source_path)}</div>
            <div class="list-subtitle">
              ${binding.scope_type} · ${binding.enabled ? "enabled" : "disabled"} · 文档 ${formatNumber(binding.document_count || 0)} · Chunk ${formatNumber(binding.chunk_count || 0)}
            </div>
            <div class="helper">
              ${escapeHtml(binding.credential_key_id || "-")} · ${escapeHtml(binding.credential_root_path || "-")} · ${escapeHtml(binding.credential_status || "missing")}
            </div>
            <div class="pill-row">
              <span class="pill ${syncTone(binding.sync_status)}">${escapeHtml(binding.sync_status || "pending_sync")}</span>
              <span class="muted">最近同步 ${binding.last_imported_at ? formatDate(binding.last_imported_at) : "未同步"}</span>
            </div>
            <div class="helper">${escapeHtml(binding.status_reason || "")}</div>
            <div class="list-actions">
              <button class="ghost" data-action="import-binding" data-binding-id="${binding.id}">导入</button>
              <button class="ghost" data-action="reindex-binding" data-binding-id="${binding.id}">重建</button>
              <button class="ghost" data-action="open-browse-path" data-path="${binding.source_path}" data-credential-id="${binding.credential_id || ""}">定位</button>
              <button class="secondary" data-action="${binding.enabled ? "disable-binding" : "enable-binding"}" data-binding-id="${binding.id}">
                ${binding.enabled ? "停用" : "启用"}
              </button>
              <button class="danger" data-action="delete-binding" data-binding-id="${binding.id}">解绑</button>
            </div>
          </div>
        `,
      )
      .join("");
  }

  function renderWorkbench({ selectedKB, workbench, helpers }) {
    if (!selectedKB || !workbench) {
      return `<div class="empty">请选择知识库后查看绑定状态、最近任务和同步建议。</div>`;
    }
    const { escapeHtml, formatDate, formatNumber } = helpers;
    const counts = workbench.binding_status_counts || {};
    const recentTasks = workbench.recent_tasks || [];
    const recentTaskHtml = recentTasks.length
      ? recentTasks
          .map(
            (task) => `
              <div class="list-item compact">
                <div class="list-title">#${task.id} · ${escapeHtml(task.task_type)} · ${escapeHtml(task.status)}</div>
                <div class="list-subtitle">${escapeHtml((task.source_paths || []).slice(0, 2).join(", ") || "-")}</div>
                <div class="helper">${formatDate(task.finished_at || task.created_at)}</div>
              </div>
            `,
          )
          .join("")
      : `<div class="empty">当前知识库还没有任务记录。</div>`;
    return `
      <div class="workbench-grid">
        <div class="workbench-metrics">
          <div class="list-item">
            <div class="list-title">已索引绑定源</div>
            <div class="metric-value">${formatNumber(counts.indexed || 0)}</div>
          </div>
          <div class="list-item">
            <div class="list-title">待同步绑定源</div>
            <div class="metric-value">${formatNumber(counts.pending_sync || 0)}</div>
          </div>
          <div class="list-item">
            <div class="list-title">同步中绑定源</div>
            <div class="metric-value">${formatNumber(counts.syncing || 0)}</div>
          </div>
          <div class="list-item">
            <div class="list-title">失败绑定源</div>
            <div class="metric-value">${formatNumber(counts.failed || 0)}</div>
          </div>
        </div>
        <div class="hint-box">
          <div><strong>建议动作</strong></div>
          <div class="helper">
            ${counts.pending_sync ? `存在 ${formatNumber(counts.pending_sync)} 个待同步绑定源，建议先执行“导入全部绑定源”。` : "当前没有待同步绑定源。"}
          </div>
          <div class="helper">
            ${counts.failed ? `存在 ${formatNumber(counts.failed)} 个失败绑定源，建议执行“重建全部绑定源”或查看最近任务。` : "当前没有失败绑定源。"}
          </div>
          <div class="helper">最近任务用于判断知识库当前是否处于稳定、待同步或失败状态。</div>
        </div>
      </div>
      <div class="section-header" style="margin-top: 14px">
        <h3>最近任务</h3>
      </div>
      <div class="list">${recentTaskHtml}</div>
    `;
  }

  return {
    renderBindings,
    renderWorkbench,
  };
})();
