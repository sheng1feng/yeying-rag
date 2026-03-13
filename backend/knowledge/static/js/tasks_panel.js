window.KnowledgeTasksPanel = (() => {
  function toneForTaskStatus(status) {
    const value = String(status || "").toLowerCase();
    if (value === "succeeded") return "success";
    if (value === "canceled") return "info";
    if (value === "failed") return "danger";
    if (value === "cancel_requested") return "warning";
    if (value === "partial_success") return "warning";
    return "warning";
  }

  function toneForTaskItemStatus(status) {
    const value = String(status || "").toLowerCase();
    if (["indexed", "deleted", "succeeded"].includes(value)) return "success";
    if (value === "rolled_back") return "info";
    if (value === "skipped") return "info";
    if (value === "failed") return "danger";
    return "warning";
  }

  function describeTaskQueue(task = {}) {
    if (task.status === "running") return "执行中";
    if (task.status === "cancel_requested") return "取消中，等待当前文件处理完成后自动回退";
    if (task.status === "canceled") return "已取消";
    if (task.status === "pending") {
      const queueLabel = task.queue_position ? `排队中 · 第 ${task.queue_position} 位` : "排队中";
      if (task.current_running_task_type) {
        return `${queueLabel} · 当前执行 ${task.current_running_task_type} #${task.current_running_task_id}`;
      }
      return queueLabel;
    }
    return "已完成";
  }

  function renderTaskList({ tasks, selectedTaskId, helpers }) {
    const { escapeHtml, formatDate } = helpers;
    if (!tasks.length) {
      return `<div class="empty">暂无任务。</div>`;
    }
    return tasks
      .map(
        (task) => `
          <div class="table-row task-table ${selectedTaskId === task.id ? "active" : ""}">
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
  }

  function renderTaskDetail({ task, items, helpers }) {
    const { escapeHtml, formatDate, formatDuration, formatNumber } = helpers;
    if (!task) {
      return { className: "empty", html: "点击任务列表中的“详情”查看 task item 明细。" };
    }
    const taskItems = Array.isArray(items) ? items : [];
    const groups = [
      { key: "success", title: "成功项", tone: "success", items: [] },
      { key: "rolled_back", title: "已回退", tone: "info", items: [] },
      { key: "skipped", title: "跳过项", tone: "info", items: [] },
      { key: "failed", title: "失败项", tone: "danger", items: [] },
      { key: "other", title: "其他", tone: "warning", items: [] },
    ];
    taskItems.forEach((item) => {
      const status = String(item.status || "").toLowerCase();
      if (["indexed", "deleted", "succeeded"].includes(status)) groups[0].items.push(item);
      else if (status === "rolled_back") groups[1].items.push(item);
      else if (status === "skipped") groups[2].items.push(item);
      else if (status === "failed") groups[3].items.push(item);
      else groups[4].items.push(item);
    });
    const activeGroups = groups.filter((group) => group.items.length > 0);
    return {
      className: "",
      html: `
        <div class="detail-grid">
          <div class="detail-card">
            <div class="detail-label">任务类型</div>
            <div class="detail-value">${escapeHtml(task.task_type)}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">当前状态</div>
            <div class="detail-value"><span class="pill ${toneForTaskStatus(task.status)}">${escapeHtml(task.status)}</span></div>
            <div class="helper">队列信息：${escapeHtml(describeTaskQueue(task))}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">开始时间</div>
            <div class="detail-value">${formatDate(task.started_at)}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">结束时间</div>
            <div class="detail-value">${formatDate(task.finished_at)}</div>
          </div>
          <div class="detail-card">
            <div class="detail-label">执行耗时</div>
            <div class="detail-value">${formatDuration(task.started_at || task.created_at, task.finished_at)}</div>
          </div>
        </div>
        <div class="detail-card" style="margin-top: 12px">
          <div class="detail-label">源路径</div>
          <div class="detail-value">${escapeHtml((task.source_paths || []).join(", ") || "-")}</div>
          <div class="helper">错误信息：${escapeHtml(task.error_message || "-")}</div>
        </div>
        <div class="detail-card" style="margin-top: 12px">
          <div class="detail-label">任务统计</div>
          <div class="code">${escapeHtml(JSON.stringify(task.stats_json || {}, null, 2))}</div>
        </div>
        <div style="margin-top: 16px">
          ${
            activeGroups.length
              ? activeGroups
                  .map(
                    (group) => `
                      <div class="detail-card" style="margin-top: 10px">
                        <div class="section-header">
                          <h3>${escapeHtml(group.title)} (${formatNumber(group.items.length)})</h3>
                        </div>
                        <div class="list">
                          ${group.items
                            .map(
                              (item) => `
                                <div class="list-item">
                                  <div class="list-title">
                                    <span class="pill ${toneForTaskItemStatus(item.status)}">${escapeHtml(item.status)}</span>
                                    ${escapeHtml(item.file_name || "(未命名)")}
                                  </div>
                                  <div class="list-subtitle">路径：${escapeHtml(item.source_path)}</div>
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
      `,
    };
  }

  return {
    toneForTaskStatus,
    toneForTaskItemStatus,
    describeTaskQueue,
    renderTaskList,
    renderTaskDetail,
  };
})();
