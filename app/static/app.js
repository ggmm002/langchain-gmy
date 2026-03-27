const healthEl = document.getElementById("health");
const queryEl = document.getElementById("query");
const allowExecutionEl = document.getElementById("allowExecution");
const dryRunEl = document.getElementById("dryRun");
const submitEl = document.getElementById("submit");
const classificationEl = document.getElementById("classification");
const decisionDetailsEl = document.getElementById("decisionDetails");
const subsystemEl = document.getElementById("subsystem");
const traceEl = document.getElementById("trace");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderTags(items, fallback = "无") {
  if (!items || items.length === 0) {
    return `<span class="tag">${escapeHtml(fallback)}</span>`;
  }

  return items
    .map((item) => `<span class="tag">${escapeHtml(item)}</span>`)
    .join("");
}

function renderScores(scores) {
  const entries = Object.entries(scores || {});
  if (entries.length === 0) {
    return '<div class="empty-state">暂无候选分数。</div>';
  }

  return `
    <div class="score-row">
      ${entries
        .map(([name, score]) => {
          const percent = Math.round(Number(score) * 100);
          return `
            <div class="score-item">
              <div class="score-label">
                <span>${escapeHtml(name)}</span>
                <span>${percent}%</span>
              </div>
              <div class="progress"><span style="width:${percent}%"></span></div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderHealth(data) {
  const llmClass = data.llm_status === "ready" ? "" : data.llm_status === "degraded" ? "warning" : "danger";
  healthEl.innerHTML = `
    <div class="status-pill ${llmClass}">${escapeHtml(data.status)}</div>
    <div class="meta-grid">
      <div class="meta-card">
        <h4>应用</h4>
        <div>${escapeHtml(data.app_name)}</div>
        <div class="muted">版本 ${escapeHtml(data.version)}</div>
      </div>
      <div class="meta-card">
        <h4>LLM 分类器</h4>
        <div class="status-pill ${llmClass}">${escapeHtml(data.llm_status)}</div>
        <div class="muted">configured=${data.llm_configured} enabled=${data.llm_enabled}</div>
      </div>
    </div>
  `;
}

function renderClassification(result) {
  const classification = result.classification;
  classificationEl.innerHTML = `
    <div class="status-pill ${classification.route_target === "clarification" ? "warning" : ""}">
      ${escapeHtml(classification.primary_intent)}
    </div>
    <div class="meta-grid">
      <div class="meta-card">
        <h4>路由目标</h4>
        <div>${escapeHtml(classification.route_target)}</div>
      </div>
      <div class="meta-card">
        <h4>综合置信度</h4>
        <div>${Math.round(classification.confidence * 100)}%</div>
      </div>
      <div class="meta-card">
        <h4>需要确认</h4>
        <div>${classification.requires_confirmation ? "是" : "否"}</div>
      </div>
      <div class="meta-card">
        <h4>标准化查询</h4>
        <div>${escapeHtml(classification.normalized_query)}</div>
      </div>
    </div>
    <div class="meta-grid">
      <div class="meta-card">
        <h4>缺失槽位</h4>
        <div class="tag-row">${renderTags(classification.missing_slots, "无")}</div>
      </div>
      <div class="meta-card">
        <h4>风险标记</h4>
        <div class="tag-row">${renderTags(classification.risk_flags, "无")}</div>
      </div>
      <div class="meta-card">
        <h4>次意图</h4>
        <div class="tag-row">${renderTags(classification.secondary_intents, "无")}</div>
      </div>
      <div class="meta-card">
        <h4>解释</h4>
        <div>${escapeHtml(classification.rationale)}</div>
      </div>
    </div>
  `;
}

function renderDecisionDetails(result) {
  const sections = [
    ["规则判定", result.rule_decision],
    ["LLM 判定", result.llm_decision],
    ["融合结果", result.classification]
  ];

  decisionDetailsEl.innerHTML = `
    <div class="detail-list">
      ${sections
        .map(([title, decision]) => {
          if (!decision) {
            return `
              <div class="detail-card">
                <h4>${escapeHtml(title)}</h4>
                <div class="muted">当前不可用。</div>
              </div>
            `;
          }

          return `
            <div class="detail-card">
              <h4>${escapeHtml(title)}</h4>
              <div class="muted">主意图：${escapeHtml(decision.primary_intent)}，置信度 ${Math.round(decision.confidence * 100)}%</div>
              <div style="margin: 10px 0 12px;">${escapeHtml(decision.rationale)}</div>
              ${renderScores(decision.candidate_scores)}
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderSubsystem(result) {
  const subsystem = result.subsystem_result;
  subsystemEl.innerHTML = `
    <div class="status-pill ${subsystem.status.includes("clarification") ? "warning" : ""}">
      ${escapeHtml(subsystem.title)}
    </div>
    <p class="subsystem-summary">${escapeHtml(subsystem.summary)}</p>
    <div class="meta-card" style="margin-bottom: 12px;">
      <h4>建议动作</h4>
      <div class="tag-row">${renderTags(subsystem.suggestions, "无")}</div>
    </div>
    <pre>${escapeHtml(JSON.stringify(subsystem.data, null, 2))}</pre>
  `;
}

function renderTrace(result) {
  const trace = result.trace || [];
  if (trace.length === 0) {
    traceEl.innerHTML = '<div class="empty-state">暂无链路轨迹。</div>';
    return;
  }

  traceEl.innerHTML = `
    <div class="trace-list">
      ${trace
        .map((item) => `
          <div class="trace-item">
            <h4>${escapeHtml(item.stage)}</h4>
            <div class="muted">${escapeHtml(item.message)}</div>
            <pre>${escapeHtml(JSON.stringify(item.payload || {}, null, 2))}</pre>
          </div>
        `)
        .join("")}
    </div>
  `;
}

async function fetchHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    renderHealth(data);
  } catch (error) {
    healthEl.innerHTML = `<div class="status-pill danger">unreachable</div><p class="muted">${escapeHtml(error.message)}</p>`;
  }
}

async function submitQuery() {
  const query = queryEl.value.trim();
  if (!query) {
    queryEl.focus();
    return;
  }

  submitEl.disabled = true;
  submitEl.textContent = "识别中...";

  try {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        allow_action_execution: allowExecutionEl.checked,
        dry_run: dryRunEl.checked,
        trace: true
      })
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "请求失败");
    }

    renderClassification(data);
    renderDecisionDetails(data);
    renderSubsystem(data);
    renderTrace(data);
    await fetchHealth();
  } catch (error) {
    classificationEl.innerHTML = `<div class="empty-state">请求失败：${escapeHtml(error.message)}</div>`;
  } finally {
    submitEl.disabled = false;
    submitEl.textContent = "开始识别与路由";
  }
}

document.querySelectorAll(".chip").forEach((button) => {
  button.addEventListener("click", () => {
    queryEl.value = button.dataset.sample || "";
    queryEl.focus();
  });
});

submitEl.addEventListener("click", submitQuery);

queryEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
    submitQuery();
  }
});

fetchHealth();
