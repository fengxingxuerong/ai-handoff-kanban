/* 项目与任务增删改 / AI 设置 / 监管报告 / 指令 / 交接说明生成 / 仪表盘
 *
 * 2026-09-28 从 `web/index.html` 的 <script> 块按**原顺序**整段切出，内容逐字未改。
 * ⚠️ 加载顺序是契约：index.html 里这几个 <script> 的顺序不能调换 ——
 *    常量与函数在各自文件顶层执行，换序会撞 TDZ（ReferenceError）。
 */
// ============ 交接导出 ============
async function copyHandoffText() {
  const p = current;
  const lines = [];
  lines.push(`【${p.name}】项目交接摘要`);
  lines.push(`状态：${p.status}（进度 ${p.progress}%）| 优先级：${p.priority} | 标签：${(p.tags||[]).join("、") || "无"}${p.repo_path ? ` | 代码路径：${p.repo_path}` : ""}`);
  lines.push(`最近更新：${p.updated_at}`);
  lines.push("");
  if (p.background) { lines.push("【项目背景】"); lines.push(p.background); lines.push(""); }
  if (p.handoff_note) { lines.push("【交接说明（请务必先读）】"); lines.push(p.handoff_note); lines.push(""); }
  if (p.next_action) { lines.push("【下一步行动】"); lines.push(p.next_action); lines.push(""); }
  const tasks = p.tasks || [];
  if (tasks.length) {
    lines.push("【任务清单】");
    tasks.forEach(t => {
      const refs = (t.refs && t.refs.length) ? `（涉及 ${t.refs.join("、")}）` : "";
      lines.push(`- [${t.status}] ${t.title}（${t.priority}）${t.agent ? `由 ${t.agent} 负责` : ""}${t.desc ? `：${t.desc}` : ""}${refs}`);
    });
    lines.push("");
  }
  lines.push("—— 来自 AI 交接看板");
  try { await navigator.clipboard.writeText(lines.join("\n")); toast("交接摘要已复制 ✓"); }
  catch (e) { fallbackCopy(lines.join("\n")); }
}

async function copyHandoffMarkdown() {
  try {
    const md = await api("/api/export/" + currentPid);
    try { await navigator.clipboard.writeText(md); toast("Markdown 交接文档已复制 ✓"); }
    catch (e) { fallbackCopy(md); }
  } catch (e) { toast("导出失败：" + e.message); }
}

function fallbackCopy(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); toast("已复制 ✓"); } catch (e) { toast("复制失败，请手动复制"); }
  document.body.removeChild(ta);
}

// ============ 项目增删改 ============
// 标签 chip：预设的写在 HTML 里，项目自带的自定义标签按需创建（同名只留一个）。
// 关键是"界面上的 chip"与 selTags（保存时的真值）必须始终一一对应 ——
// 以前自定义 chip 的创建/删除散在输入框事件里，出现过标签在 selTags 里
// 而界面上没有 chip 的状态：用户看不见它，却会被写进项目。
function tagChip(t) {
  const holder = document.getElementById("f-tag-chips");
  const found = [...holder.querySelectorAll(".chip-tag")].find(c => c.dataset.t === t);
  if (found) return found;
  const chip = document.createElement("span");
  chip.className = "chip-tag";
  chip.dataset.t = t;
  chip.textContent = t;
  chip.addEventListener("click", () => toggleTag(chip, t));
  holder.appendChild(chip);
  return chip;
}

function toggleTag(chip, t) {
  if (selTags.has(t)) { selTags.delete(t); chip.classList.remove("active"); }
  else { selTags.add(t); chip.classList.add("active"); }
}

// 打开弹窗时按"这个项目有什么标签"整体重置。
// 以前是先 `selTags = new Set()`、再把项目标签塞回 Set，但只有"界面上正好有同名 chip"
// 时才高亮 —— 项目自带的自定义标签没有 chip，于是出现"标签在 selTags 里、界面上看不见"：
// 用户既看不到它也没法取消它。同时上一个项目留下的自定义 chip 还挂在原地可被点选。
function resetTagChips(tags) {
  selTags = new Set(tags || []);
  document.querySelectorAll("#f-tag-chips .chip-tag").forEach(c => {
    c.classList.toggle("active", selTags.has(c.dataset.t));
  });
  selTags.forEach(t => tagChip(t).classList.add("active"));
}

function fillProjectForm(p) {
  document.getElementById("f-name").value = p.name || "";
  document.getElementById("f-status").value = p.status || "规划中";
  document.getElementById("f-priority").value = p.priority || "中";
  document.getElementById("f-progress").value = (p.progress === 0 || p.progress) ? p.progress : "0";
  document.getElementById("f-repo").value = p.repo_path || "";
  document.getElementById("f-bg").value = p.background || "";
  document.getElementById("f-handoff").value = p.handoff_note || "";
  document.getElementById("f-next").value = p.next_action || "";
  resetTagChips(p.tags || []);
  renderMilestoneRows(p.milestones || []);
}

// —— 里程碑行编辑器（P2）——
// 数据形状约定（与后端 _clean_milestones 对齐）：[{name, done}]。
// 行容器里每个 .ms-row 持有两个控件：.ms-name 输入 + .ms-done 勾选。
// 保存时按行的出现顺序收集，已删除的行直接不读（DOM 里没了）。
function renderMilestoneRows(list) {
  const box = document.getElementById("f-milestones");
  box.innerHTML = (list || []).map((m, i) => milestoneRowHTML(m, i)).join("");
}

function milestoneRowHTML(m, i) {
  const name = (m && m.name ? m.name : "").trim();
  const done = !!(m && m.done);
  return `<div class="ms-row" style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
    <input class="ms-name" value="${esc(name)}" placeholder="里程碑名称（≤40 字）" maxlength="40" style="flex:1"
      aria-label="里程碑名称">
    <label class="switch" style="flex-shrink:0;margin:0" title="手工标记该节点已经过去">
      <input type="checkbox" class="ms-done"${done ? " checked" : ""} aria-label="里程碑 ${i + 1} 已完成标记">
      <span class="slider"></span>
    </label>
    <button class="btn small danger" type="button" data-act="removeMilestoneRow" data-arg="${i}" aria-label="删除里程碑 ${i + 1}">✕</button>
  </div>`;
}

function addMilestoneRow() {
  const box = document.getElementById("f-milestones");
  box.insertAdjacentHTML("beforeend", milestoneRowHTML({ name: "", done: false }, box.children.length));
}

function removeMilestoneRow(idx) {
  const box = document.getElementById("f-milestones");
  const row = box.children[idx];
  if (row) row.remove();
}

function collectMilestones() {
  const box = document.getElementById("f-milestones");
  const out = [];
  Array.from(box.children || []).forEach(c => {
    const input = c.querySelector(".ms-name");
    const check = c.querySelector(".ms-done");
    const name = (input ? input.value : "").trim();
    if (!name) return;            // 空名称的行不提交，后端同口径（`_clean_milestones`）
    out.push({ name, done: check ? check.checked : false });
  });
  return out;
}

async function openProjectModal(id) {
  editProjectId = id || null;
  selTags = new Set();
  document.getElementById("proj-modal-title").textContent = id ? "编辑项目" : "新建项目";
  document.getElementById("f-name").value = "";
  document.getElementById("f-status").value = "规划中";
  document.getElementById("f-priority").value = "中";
  document.getElementById("f-progress").value = "0";
  document.getElementById("f-repo").value = "";
  document.getElementById("f-bg").value = "";
  document.getElementById("f-handoff").value = "";
document.getElementById("f-next").value = "";
  resetTagChips([]);
  renderMilestoneRows([]);
  if (id) {
    // 编辑**必须**从 `/api/projects/{id}` 回填，不能用总览列表里那一份：
    // 列表接口只给卡片要用的字段，不含 background / handoff_note，
    // 而 PUT 是整体覆盖语义 —— 实测「从总览点编辑、只改一下进度、保存」
    // 会把用户手写的三周项目背景和交接说明清空，且界面上毫无痕迹。
    // 取不到详情就宁可不开这个表单，也不要开一个"保存即吞内容"的表单。
    let full = null;
    try { full = await api("/api/projects/" + id); } catch (e) { full = null; }
    if (editProjectId !== id) return;          // 期间用户又点了别的项目
    if (!full || !full.id) {
      toast("读不到这个项目的详情，已阻止打开编辑（直接保存会清空背景与交接说明）");
      return;
    }
    fillProjectForm(full);
  }
  openModal("proj-modal");
}

document.querySelectorAll("#f-tag-chips .chip-tag").forEach(c => {
  c.addEventListener("click", () => toggleTag(c, c.dataset.t));
});

document.getElementById("f-tag-input").addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const v = e.target.value.trim();
  if (!v) return;
  selTags.add(v);
  tagChip(v).classList.add("active");   // 没有同名 chip 就建一个，保证看得见的=会被存的
  e.target.value = "";
});

async function saveProject() {
  const name = document.getElementById("f-name").value.trim();
  if (!name) { toast("请填写项目名称"); return; }
  const body = {
    name,
    status: document.getElementById("f-status").value,
    priority: document.getElementById("f-priority").value,
    progress: Math.min(100, Math.max(0, parseInt(document.getElementById("f-progress").value) || 0)),
    tags: [...selTags],
    repo_path: document.getElementById("f-repo").value.trim(),
    background: document.getElementById("f-bg").value.trim(),
    handoff_note: document.getElementById("f-handoff").value.trim(),
next_action: document.getElementById("f-next").value.trim(),
    milestones: collectMilestones(),
  };
  try {
    if (editProjectId) {
      await api("/api/projects/" + editProjectId, { method: "PUT", body: JSON.stringify(body) });
      toast("项目已更新 ✓");
    } else {
      const created = await api("/api/projects", { method: "POST", body: JSON.stringify(body) });
      toast("项目已创建 ✓");
    }
    closeModal("proj-modal");
    if (document.getElementById("view-detail").classList.contains("active") && editProjectId === currentPid) {
      current = await api("/api/projects/" + currentPid);
      renderDetail();
    }
    loadOverview();
  } catch (e) { toast("保存失败：" + e.message); }
}

async function confirmDeleteProject(id, name) {
  if (!confirm(`确定删除项目「${name}」吗？该项目下的所有任务也会一并删除。`)) return;
  try {
    await api("/api/projects/" + id, { method: "DELETE" });
    toast("项目已删除");
    loadOverview();
  } catch (e) { toast("删除失败：" + e.message); }
}

function editProject() { openProjectModal(currentPid); }

async function deleteProject() {
  if (!confirm(`确定删除项目「${current.name}」吗？该操作不可恢复。`)) return;
  try {
    await api("/api/projects/" + currentPid, { method: "DELETE" });
    toast("项目已删除");
    showOverview();
  } catch (e) { toast("删除失败：" + e.message); }
}

// ============ 任务增删改 ============
// 自动分配：把『指定优先级 + 未完成 + 依赖已就绪』的任务批量指派给某个智能体。
// 写入任务 agent 字段，走 POST /api/projects/{pid}/assign（锁内纯函数计算，幂等）。
function openAssignModal() {
  if (!currentPid) { toast("请先打开一个项目"); return; }
  document.getElementById("assign-agent").value = "";
  document.getElementById("assign-priority").value = "高";
  document.getElementById("assign-overwrite").checked = false;
  const result = document.getElementById("assign-result");
  result.style.display = "none";
  result.innerHTML = "";
  openModal("assign-modal");
}

async function runAutoAssign() {
  const agent = document.getElementById("assign-agent").value.trim();
  if (!agent) { toast("请填写要指派给的智能体名"); return; }
  const priority = document.getElementById("assign-priority").value;
  const overwrite = document.getElementById("assign-overwrite").checked;
  try {
    const j = await api("/api/projects/" + currentPid + "/assign", {
      method: "POST",
      body: JSON.stringify({ agent, priority, overwrite }),
    });
    const box = document.getElementById("assign-result");
    let html = "";
    if (j.count === 0) {
      html = '<div style="color:var(--text-dim)">没有可分配的任务。' + (j.skipped_count ? "（被跳过的 " + j.skipped_count + " 条见下方）" : "") + "</div>";
    } else {
      html += '<div style="font-weight:600;margin-bottom:6px">✅ 已指派 ' + j.count + " 条给「" + esc(agent) + "」</div>";
      html += "<ul style='margin:0 0 8px;padding-left:18px'>" +
        j.assigned.map(a => "<li>" + esc(a.title) + "</li>").join("") + "</ul>";
    }
    if (j.skipped && j.skipped.length) {
      html += '<div style="color:var(--text-faint);margin-top:4px">跳过 ' + j.skipped.length + " 条：</div>";
      html += "<ul style='margin:4px 0 0;padding-left:18px;color:var(--text-faint)'>" +
        j.skipped.map(s => "<li>" + esc(s.title) + " —— " + esc(s.reason) + "</li>").join("") + "</ul>";
    }
    box.innerHTML = html;
    box.style.display = "block";
    toast(j.count ? "已分配 " + j.count + " 个任务给 " + agent : "没有可分配的任务");
    current = await api("/api/projects/" + currentPid);
    renderDetail();
    loadOverview();
  } catch (e) { toast("分配失败：" + e.message); }
}

function openTaskModal(taskId) {
  editTaskId = taskId || null;
  document.getElementById("task-modal-title").textContent = taskId ? "编辑任务" : "新建任务";
  document.getElementById("t-delete").style.display = taskId ? "inline-block" : "none";
  document.getElementById("t-title").value = "";
  document.getElementById("t-desc").value = "";
  document.getElementById("t-status").value = "待办";
  document.getElementById("t-priority").value = "中";
  document.getElementById("t-agent").value = "";
  document.getElementById("t-refs").value = "";
  document.getElementById("t-acceptance").value = "";
  document.getElementById("t-due").value = "";
  const journalField = document.getElementById("t-journal-field");
  if (taskId) {
    const t0 = (current.tasks || []).find(x => x.id === taskId);
    if (t0) {
      journalField.style.display = "flex";
      renderJournal(t0.journal);
    } else { journalField.style.display = "none"; }
  } else {
    journalField.style.display = "none";
  }
const depsSel = document.getElementById("t-deps");
  depsSel.innerHTML = (current && current.tasks ? current.tasks : [])
    .filter(x => x.id !== taskId)
    .map(x => `<option value="${esc(x.id)}">${esc(x.title)}</option>`).join("");
  // 里程碑下拉：选项来自项目详情（编辑时 current 就是刚拉回来的那份，含 milestones）。
  // 空选项 = 「未归入」，对应后端 TaskIn.milestone 的 "" 默认值。
  const msSel = document.getElementById("t-milestone");
  msSel.innerHTML = `<option value="">未归入</option>`
    + (current && current.milestones && current.milestones.length
        ? current.milestones.map(m => `<option value="${esc((m.name || "").trim())}">${esc((m.name || "").trim())}</option>`).join("")
        : "");
  if (taskId) {
    const t = (current.tasks || []).find(x => x.id === taskId);
    if (t) {
      document.getElementById("t-title").value = t.title;
      document.getElementById("t-desc").value = t.desc || "";
      document.getElementById("t-status").value = t.status;
      document.getElementById("t-priority").value = t.priority;
      document.getElementById("t-agent").value = t.agent || "";
document.getElementById("t-refs").value = (t.refs || []).join(", ");
      document.getElementById("t-acceptance").value = (t.acceptance || []).join("\n");
      document.getElementById("t-due").value = t.due_date || "";
      document.getElementById("t-milestone").value = t.milestone || "";
      const mine = t.depends_on || [];
      Array.from(depsSel.options).forEach(o => { o.selected = mine.includes(o.value); });
    }
  }
  openModal("task-modal");
}

async function saveTask() {
  const title = document.getElementById("t-title").value.trim();
  if (!title) { toast("请填写任务标题"); return; }
  const body = {
    title,
    desc: document.getElementById("t-desc").value.trim(),
    status: document.getElementById("t-status").value,
    priority: document.getElementById("t-priority").value,
    agent: document.getElementById("t-agent").value.trim(),
    due_date: document.getElementById("t-due").value || "",
    refs: document.getElementById("t-refs").value.split(",").map(s => s.trim()).filter(Boolean),
    // 验收标准一行一条；后端会再去空去重限量（超 10 条留前 10、单条超 120 字截断）
    acceptance: document.getElementById("t-acceptance").value.split("\n").map(s => s.trim()).filter(Boolean),
    depends_on: Array.from(document.getElementById("t-deps").selectedOptions).map(o => o.value),
    milestone: document.getElementById("t-milestone").value,
  };
  try {
    if (editTaskId) {
      await api("/api/tasks/" + editTaskId, { method: "PUT", body: JSON.stringify(body) });
      toast("任务已更新 ✓");
    } else {
      await api("/api/projects/" + currentPid + "/tasks", { method: "POST", body: JSON.stringify(body) });
      toast("任务已创建 ✓");
    }
    closeModal("task-modal");
    current = await api("/api/projects/" + currentPid);
    renderDetail();
    loadOverview();
  } catch (e) { toast("保存失败：" + e.message); }
}

async function deleteTask() {
  if (!editTaskId) return;
  const t = (current.tasks || []).find(x => x.id === editTaskId);
  if (!confirm("删除任务「" + (t ? t.title : editTaskId) + "」？它的过程日志会一并删掉，不能撤销。")) return;
  try {
    await api("/api/tasks/" + editTaskId, { method: "DELETE" });
    editTaskId = null;
    closeModal("task-modal");
    toast("任务已删除");
    current = await api("/api/projects/" + currentPid);
    renderDetail();
    loadOverview();
  } catch (e) { toast("删除失败：" + e.message); }
}

// 键盘：Esc 逐层关最上面的浮层；Enter / 空格 打开被聚焦的卡片
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeTopModal(); return; }
  if (e.key !== "Enter" && e.key !== " ") return;
  const el = document.activeElement;
  if (el && el.dataset && el.dataset.keyact && el.click) { e.preventDefault(); el.click(); }
});

// ============ AI 设置 ============
let aiConfig = null;
let pendingActions = [];
// 这一批 AI 指令的身份证号：创建类动作拿它当幂等键前缀。
// 以前"执行"没有键，同一条指令再点一次（或失败后重试）就把项目/任务建成一对。
let cmdBatchRef = "";

async function openAISettings() {
  try {
    aiConfig = await api("/api/ai/config");
    renderEndpoints(aiConfig.ai.endpoints || []);
    // 默认模型
    const sel = document.getElementById("ai-active-model");
    const models = [];
    (aiConfig.ai.endpoints || []).forEach(ep => (ep.models || []).forEach(m => { if (!models.includes(m)) models.push(m); }));
    sel.innerHTML = models.map(m => `<option ${m === aiConfig.ai.active_model ? "selected" : ""}>${esc(m)}</option>`).join("");
    // 定时
    const s = (aiConfig.ai.schedule || {});
    document.getElementById("ai-sched-enabled").checked = !!s.enabled;
    document.getElementById("ai-sched-freq").value = s.frequency || "daily";
    document.getElementById("ai-sched-time").value = s.time || "09:00";
    document.getElementById("ai-sched-last").textContent = s.last_run || "—";
    document.getElementById("mcp-readonly").checked = !!(aiConfig.ai.mcp_readonly);
    document.getElementById("ai-budget-limit").value = Number(aiConfig.ai.budget_limit || 0) || 0;
    // 打开时回设置页（用量 Tab 首次切入才加载），否则上次关在用量 Tab、这次打开还停在用量
    const usagePane = document.getElementById("ai-pane-usage");
    if (usagePane) usagePane.dataset.loaded = "";
    switchAITab("settings");
    loadBackups();
    openModal("ai-settings-modal");
  } catch (e) { toast("加载配置失败：" + e.message); }
}

function renderEndpoints(eps) {
  const wrap = document.getElementById("ai-endpoints");
  wrap.innerHTML = eps.map((ep, i) => `
    <div class="ep-card">
      <div class="ep-top">
        <input data-i="${i}" data-f="name" class="ep-name-input" value="${esc(ep.name)}" style="flex:1;background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:7px 10px;color:var(--text);font-size:13px;font-weight:600">
        <label class="switch"><input type="checkbox" data-i="${i}" data-f="enabled" ${ep.enabled ? "checked" : ""}><span class="slider"></span></label>
      </div>
      <div class="field"><label>Base URL</label>
        <input data-i="${i}" data-f="base_url" value="${esc(ep.base_url)}" placeholder="https://.../v1"></div>
      <div class="field"><label>API Keys（每行一个，可多 Key 轮询）</label>
        <textarea data-i="${i}" data-f="keys" placeholder="sk-... 每行一个">${esc((ep.api_keys || []).join("\n"))}</textarea></div>
      <div class="field"><label>模型（逗号分隔，按优先级排序）</label>
        <input data-i="${i}" data-f="models" value="${esc((ep.models || []).join(", "))}"></div>
      <div class="field" style="flex-direction:row;gap:10px;align-items:flex-end">
        <div style="flex:1"><label>输入单价（$/千 token，0=不估算）</label>
          <input type="number" data-i="${i}" data-f="price_in" min="0" step="0.0001" value="${Number(ep.price_per_1k_input || 0) || 0}"></div>
        <div style="flex:1"><label>输出单价（$/千 token）</label>
          <input type="number" data-i="${i}" data-f="price_out" min="0" step="0.0001" value="${Number(ep.price_per_1k_output || 0) || 0}"></div>
      </div>
    </div>`).join("");
}

async function saveAISettings() {
  const epEls = [...document.querySelectorAll("#ai-endpoints .ep-card")];
  const endpoints = epEls.map(card => ({
    name: card.querySelector('[data-f="name"]').value.trim(),
    base_url: card.querySelector('[data-f="base_url"]').value.trim(),
    api_keys: card.querySelector('[data-f="keys"]').value.split("\n").map(s => s.trim()).filter(Boolean),
    models: card.querySelector('[data-f="models"]').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    enabled: card.querySelector('[data-f="enabled"]').checked,
    price_per_1k_input: Number(card.querySelector('[data-f="price_in"]').value) || 0,
    price_per_1k_output: Number(card.querySelector('[data-f="price_out"]').value) || 0,
  }));
  const budgetEl = document.getElementById("ai-budget-limit");
  const body = {
    endpoints,
    active_model: document.getElementById("ai-active-model").value,
    mcp_readonly: document.getElementById("mcp-readonly").checked,
    budget_limit: budgetEl ? Number(budgetEl.value) || 0 : 0,
    schedule: {
      enabled: document.getElementById("ai-sched-enabled").checked,
      frequency: document.getElementById("ai-sched-freq").value,
      time: document.getElementById("ai-sched-time").value,
    },
  };
  try {
    await api("/api/ai/config", { method: "PUT", body: JSON.stringify(body) });
    toast("AI 设置已保存 ✓");
    closeModal("ai-settings-modal");
  } catch (e) { toast("保存失败：" + e.message); }
}

async function testAI() {
  const btn = document.getElementById("ai-test-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/ai/test", { method: "POST" });
    if (r.ok) toast("连接成功：" + r.endpoint + " → " + (r.reply || "OK") + " ✓");
    else toast("连接失败：" + r.error);
  } catch (e) { toast("测试失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

// ============ AI 监管报告 ============
let currentReport = null;

async function loadReportHistory() {
  const wrap = document.getElementById("report-history");
  const list = document.getElementById("report-history-list");
  if (!wrap || !list) return;
  try {
    const r = await api("/api/ai/reports");
    const items = (r && r.reports) || [];
    if (!items.length) { wrap.style.display = "none"; list.innerHTML = ""; return; }
    list.innerHTML = items.slice(0, 12).map(x => {
      const tip = `${x.trigger || "手动"} · ${x.endpoint || "未知端点"} · ${x.insight_count} 条洞察`
        + (x.summary ? `｜${String(x.summary).slice(0, 80)}` : "");
      return `<button class="btn small" data-act="openReport" data-arg="${esc(x.id)}" `
        + `title="${esc(tip)}">${esc(String(x.created_at || "").slice(5, 16))}</button>`;
    }).join("");
    wrap.style.display = "block";
  } catch (e) {
    // 拿不到历史就说一句，别整条静默消失——那和"从来没有过历史报告"长得一模一样
    wrap.style.display = "block";
    list.innerHTML = `<span style="font-size:11px;color:var(--red)">历史报告读不到：${esc(e.message)}</span>`;
  }
}

async function openReport(rid) {
  const panel = document.getElementById("report-panel");
  document.getElementById("report-mask").classList.add("show");
  panel.classList.add("show");
  loadReportHistory();
  const rb = panel.querySelector(".r-body");
  if (rb) rb.scrollTop = modalScroll.get("report-panel") || 0;
  focusInto(panel);
  document.getElementById("report-body").innerHTML = '<div class="r-loading"><div class="spinner"></div><div>AI 正在分析全部项目，请稍候…</div></div>';
  document.getElementById("report-meta").textContent = rid ? "历史报告" : "生成中…";
  try {
    if (rid) {
      currentReport = await api("/api/ai/reports/" + rid);
      document.getElementById("report-meta").textContent = "历史报告 · " + currentReport.created_at + " · " + (currentReport.endpoint || "");
    } else {
      currentReport = await api("/api/ai/analyze", { method: "POST" });
      document.getElementById("report-meta").textContent = "生成于 " + currentReport.created_at + " · " + (currentReport.endpoint || "");
    }
    renderReport(currentReport);
  } catch (e) {
    // 「重试」必须重试**刚才那一次**：以前这里的按钮不带 rid，于是"回看某份历史报告失败"
    // 点一下重试变成"重新跑一次 AI 分析"（花配额、多存一份报告），而标题还写着"历史报告"。
    const retryArg = rid ? ` data-arg="${esc(rid)}"` : "";
    document.getElementById("report-body").innerHTML =
      '<div class="r-loading" style="color:var(--red)">' + (rid ? "读取历史报告失败：" : "分析失败：")
      + esc(e.message) + '<br><br><button class="btn" data-act="openReport"' + retryArg
      + '>重试</button></div>';
  }
}

function closeReport() {
  const panel = document.getElementById("report-panel");
  const rb = panel.querySelector(".r-body");
  modalScroll.set("report-panel", rb ? rb.scrollTop : 0);
  document.getElementById("report-mask").classList.remove("show");
  panel.classList.remove("show");
}

function healthClass(h) {
  h = Number(h) || 0;
  return h >= 70 ? "hi" : h >= 45 ? "mid" : "lo";
}

function renderReport(r) {
  const body = document.getElementById("report-body");
  const TREND_MARK = { down: ['▼', 'var(--red)'], up: ['▲', 'var(--green)'], flat: ['◆', 'var(--text-faint)'], new: ['✦', 'var(--accent2)'] };
  const insights = (r.insights || []).map(it => {
    const tr = it.trend || {};
    const mk = TREND_MARK[tr.level] || TREND_MARK.new;
    const trendHtml = tr.level === "down"
      ? `<span class="due-badge due-overdue">${mk[0]} 较上次 ${tr.delta > 0 ? "+" : ""}${tr.delta}</span>`
      : tr.level === "up"
        ? `<span class="due-badge" style="color:var(--green);background:rgba(52,211,153,.12)">${mk[0]} 较上次 +${tr.delta}</span>`
        : tr.level === "flat"
          ? `<span class="due-badge due-ok">${mk[0]} 持平</span>`
          : `<span class="due-badge due-ok">${mk[0]} 首次评估</span>`;
    return `
    <div class="insight">
      <div class="i-top">
        <div class="health ${healthClass(it.health)}">${Number(it.health) || "—"}</div>
        <div>
          <div class="i-name">${esc(it.name || "")} ${trendHtml}</div>
          <div class="i-reason">${esc(it.reason || "")}</div>
        </div>
      </div>
      ${(it.risks || []).length ? `<div class="i-sec">⚠ 风险点</div><div class="risk-list">${it.risks.map(x => `<div class="risk-item">${esc(x)}</div>`).join("")}</div>` : ""}
      ${(it.suggestions || []).length ? `<div class="i-sec">💡 建议</div><div class="sug-list">${it.suggestions.map((s, i) => `
        <div class="sug-item"><div class="sug-text">${esc(s.text || "")}</div>
        <button class="apply-btn" data-pid="${esc(it.id || "")}" data-txt="${esc(s.text || "")}" data-act="applySuggestion">应用</button></div>`).join("")}</div>` : ""}
    </div>`;
  }).join("");
  const global = (r.global_suggestions || []).length ? `
    <div class="r-global">
      <div class="lab">🌐 全局建议</div>
      <ul>${r.global_suggestions.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
    </div>` : "";
  body.innerHTML = `
    <div class="r-summary"><span class="lab">📌 监管摘要</span>${esc(r.summary || "（无摘要）")}</div>
    ${insights ? `<div class="r-insights">${insights}</div>` : '<div class="r-loading" style="padding:30px">（无项目洞察）</div>'}
    ${global}`;
}

async function applySuggestion(btn) {
  const pid = btn.dataset.pid;
  const txt = btn.dataset.txt;
  if (!pid || !txt) return;
  if (!confirm("把这条建议写入该项目的「下一步行动」？")) return;
  try {
    const r = await api("/api/ai/apply-suggestions", {
      method: "POST",
      body: JSON.stringify([{ project_id: pid, field: "next_action", value: txt }]),
    });
    // 服务端可能一条都没写（项目已被删；内容带着脱敏掩码；字段不在清单；值过长）。
    // 原来这里不看返回值直接宣布"已应用 ✓"，于是点了个按钮、什么也没发生、界面还说成功了。
    if (!r || !r.applied) {
      const why = r && r.results && r.results[0] && r.results[0].reason;
      toast("未写入：" + (why || (r && r.skipped_masked
        ? "这条建议里带着脱敏掩码，请到项目里核对原文后手动补"
        : "没找到对应项目（可能已被删除）")));
      return;
    }
    btn.classList.add("done");
    btn.textContent = "已应用 ✓";
    toast("建议已写入项目下一步行动 ✓");
  } catch (e) { toast("应用失败：" + e.message); }
}

// ============ AI 指令（自然语言维护） ============
function openAICmd() {
  pendingActions = [];
  document.getElementById("cmd-exec-row").style.display = "none";
  openModal("ai-cmd-modal");
}

function cmdAddMsg(cls, html) {
  const chat = document.getElementById("cmd-chat");
  const div = document.createElement("div");
  div.className = "cmd-msg " + cls;
  div.innerHTML = html;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

const OP_TAG = { create_project: "新建项目", update_project: "改项目", delete_project: "删项目", create_task: "加任务", update_task: "改任务", delete_task: "删任务" };

function renderActions(actions) {
  pendingActions = actions;
  const row = document.getElementById("cmd-exec-row");
  if (!actions || !actions.length) { row.style.display = "none"; return; }
  row.style.display = "block";
  const btn = row.querySelector("button");
  btn.textContent = `✅ 确认执行以上 ${actions.length} 个操作`;
  const list = actions.map(a => {
    // a.type 来自模型返回，未在白名单里时也不能原样进 innerHTML（标签是拼出来的）
    const type = String(a.type || "");
    const tag = OP_TAG[type] || esc("未知操作 " + type);
    const cls = type.startsWith("delete") ? "delete" : type.startsWith("update") ? "update" : "create";
    const desc = describeAction(a);
    return `<div class="op"><span class="op-tag ${cls}">${tag}</span><span>${esc(desc)}</span></div>`;
  }).join("");
  const holder = document.getElementById("cmd-actions-holder");
  if (holder) holder.remove();
  const wrap = document.createElement("div");
  wrap.id = "cmd-actions-holder";
  wrap.innerHTML = list;
  cmdAddMsg("ai", wrap.innerHTML);
}

function describeAction(a) {
  switch (a.type) {
    case "create_project": return `新建项目「${a.name || ""}」` + (a.priority ? `（优先级${a.priority}）` : "");
    case "update_project": return `项目「${a.project_id || a.name || ""}」的 ${a.field || ""} 改为「${a.value || ""}」`;
    case "delete_project": return `删除项目「${a.project_id || a.name || ""}」`;
    case "create_task": return `在项目「${a.project_id || ""}」下新建任务「${a.title || ""}」` + (a.priority ? `（${a.priority}）` : "");
    case "update_task": return `任务「${a.task_id || a.title || ""}」的 ${a.field || ""} 改为「${a.value || ""}」`;
    case "delete_task": return `删除任务「${a.task_id || a.title || ""}」`;
    default: return JSON.stringify(a);
  }
}

async function sendCommand() {
  const input = document.getElementById("cmd-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  cmdAddMsg("user", esc(text));
  const sendBtn = document.getElementById("cmd-send-btn");
  sendBtn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/ai/command", { method: "POST", body: JSON.stringify({ text }) });
    const parsed = r.parsed || {};
    cmdBatchRef = r.batch_ref || "";
    if (parsed.need_clarification) {
      cmdAddMsg("ai", "🤔 需要补充：" + esc(parsed.need_clarification));
      document.getElementById("cmd-exec-row").style.display = "none";
    } else if (parsed.actions && parsed.actions.length) {
      renderActions(parsed.actions);
    } else {
      cmdAddMsg("ai", "没解析出可执行的操作，换个说法试试？");
      document.getElementById("cmd-exec-row").style.display = "none";
    }
  } catch (e) {
    cmdAddMsg("ai", "❌ " + esc(e.message));
  }
  sendBtn.classList.remove("ai-loading-btn");
}

async function executeActions() {
  if (!pendingActions || !pendingActions.length) return;
  const btn = document.getElementById("cmd-exec-row").querySelector("button");
  btn.classList.add("ai-loading-btn");
  btn.textContent = "执行中…";
  let ok = 0, fail = 0;
  const batch = cmdBatchRef;       // 进入时捕获一次，两次连点用的是同一批键
  for (let i = 0; i < pendingActions.length; i++) {
    const a = pendingActions[i];
    try {
      await execSingleAction(a, i, batch);
      ok++;
    } catch (e) {
      fail++;
      cmdAddMsg("ai", "❌ " + esc(describeAction(a)) + " 执行失败：" + esc(e.message));
    }
  }
  btn.classList.remove("ai-loading-btn");
  document.getElementById("cmd-exec-row").style.display = "none";
  cmdAddMsg("ai", `✅ 执行完成：成功 ${ok} 个，失败 ${fail} 个`);
  // 约定：过程留在聊天气泡里，但**结果一律再播报一次 toast** —— 弹窗会被关掉，
  // 而 toast 是全站唯一的 aria-live 区域；少了这句，键盘/读屏用户根本不知道写进去了没有。
  toast(fail ? "已执行：成功 " + ok + " 个，失败 " + fail + " 个" : "已执行 " + ok + " 个操作 ✓");
  loadOverview();
  if (document.getElementById("view-detail").classList.contains("active") && currentPid) {
    current = await api("/api/projects/" + currentPid);
    renderDetail();
  }
}

/* AI 指令可改字段 = 看板里的普通内容字段，**排除带本机权限的那一个**：
   项目的 repo_path 一旦指向某个本地目录，监管分析就会去跑 git，
   把分支、最后一条提交、脏文件数拼进 prompt 送给模型（_projects_brief）。
   模型可能被项目名/背景里的注入文案带偏，一条 {field:"repo_path", value:"D:\\某仓库"}
   就等于让外部内容远程探测本机目录结构 —— 所以 AI 只能改内容，不能改"读哪里"。
   用户自己在编辑弹窗里填 repo_path 走的是另一条路径，不受这里限制。 */
const AI_PROJECT_FIELDS = ["name", "status", "progress", "priority", "tags",
                           "background", "handoff_note", "next_action"];
const AI_TASK_FIELDS = ["title", "desc", "status", "priority", "agent", "due_date",
                        "refs", "depends_on"];

function assertAiField(allowed, field, type) {
  if (!allowed.includes(field)) throw new Error(`${type} 不支持修改字段「${field}」`);
}

async function execSingleAction(a, i, batch) {
  const type = a.type;
  // 创建类动作带幂等键：这一批（同一次解析结果）里的第 i 条，键由调用方在执行开始时
  // 一次性捕获（见 executeActions），不要在循环里读全局 —— 全局会被上一次的执行清空，
  // 那时再兜一个固定前缀就会跨命令撞键：**下一条**正常指令的第一条创建动作会被
  // 判成"重放"而一个字都不落盘，那比建两遍更难查。
  // 拿不到 batch_ref（服务端是旧的）就不带键，退回"可能重复"而不是"静默不建"。
  const qs = batch ? "?client_ref=" + encodeURIComponent(batch + ":" + i) : "";
  if (type === "create_project") {
    await api("/api/projects" + qs, { method: "POST", body: JSON.stringify({
      name: a.name || "新项目", status: a.status || "规划中", priority: a.priority || "中",
      progress: Number(a.progress) || 0, next_action: a.next_action || "", handoff_note: a.handoff_note || "", background: a.background || "",
    }) });
  } else if (type === "update_project") {
    assertAiField(AI_PROJECT_FIELDS, a.field, "update_project");
    // PATCH：以前是 GET 整份 → 改一个字段 → PUT 整份，而那次 GET 在临界区外 ——
    // 期间智能体写进这个项目的 next_action / 交接说明会被手里这份旧快照抹掉。
    await api("/api/projects/" + a.project_id, { method: "PATCH",
      body: JSON.stringify({ [a.field]: a.value }) });
  } else if (type === "delete_project") {
    await api("/api/projects/" + a.project_id, { method: "DELETE" });
  } else if (type === "create_task") {
    await api("/api/projects/" + a.project_id + "/tasks" + qs, { method: "POST", body: JSON.stringify({
      title: a.title || "新任务", desc: a.desc || "", priority: a.priority || "中",
      status: a.status || "待办", agent: a.agent || "",
    }) });
  } else if (type === "update_task") {
    assertAiField(AI_TASK_FIELDS, a.field, "update_task");
    const tid = await resolveTaskId(a);
    if (!tid) throw new Error("找不到对应任务");
    // 不再为了改一个字段先整份读回项目、改完再 PUT 回去（同 update_project 的理由；
    // 顺带省掉一次全量读盘 —— resolveTaskId 里该读的那一次已经读了）
    await api("/api/tasks/" + tid, { method: "PATCH",
      body: JSON.stringify({ [a.field]: a.value }) });
  } else if (type === "delete_task") {
    const tid = await resolveTaskId(a);
    if (!tid) throw new Error("找不到对应任务");
    await api("/api/tasks/" + tid, { method: "DELETE" });
  } else {
    throw new Error("不支持的操作类型：" + type);
  }
}

async function resolveTaskId(a) {
  if (a.task_id) return a.task_id;
  const project = await findProjectOfTask(a);
  if (!project) return null;
  const t = (project.tasks || []).find(x => x.title === a.title || (a.title && x.title.includes(a.title)));
  return t ? t.id : null;
}

async function findProjectOfTask(a) {
  if (a.project_id) return api("/api/projects/" + a.project_id);
  // 只有 task_id 时按 id 反查所属项目：简报里的任务标题现在带脱敏掩码，
  // 拿标题当匹配键已经不可靠了，id 才是稳的那个
  if (a.task_id) {
    try {
      const hit = await api("/api/tasks/" + a.task_id);
      if (hit && hit.project_id) return api("/api/projects/" + hit.project_id);
    } catch (e) { return null; }
  }
  // 按名称匹配
  if (a.project_name) {
    const all = await api("/api/projects");
    const p = all.projects.find(x => x.name === a.project_name || x.name.includes(a.project_name));
    return p ? api("/api/projects/" + p.id) : null;
  }
  return null;
}

// ============ AI 交接说明生成 ============
async function openAIHandoff() {
  if (!currentPid) return;
  document.getElementById("ai-handoff-mode").value = "generate";
  document.getElementById("ai-handoff-result").value = "";
  openModal("ai-handoff-modal");
}

async function generateHandoff() {
  const mode = document.getElementById("ai-handoff-mode").value;
  const btn = document.getElementById("ai-handoff-gen-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/ai/handoff/" + currentPid, { method: "POST", body: JSON.stringify({ mode }) });
    document.getElementById("ai-handoff-result").value = r.text;
    toast("生成完成 ✓");
  } catch (e) { toast("生成失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

async function applyHandoff() {
  const text = document.getElementById("ai-handoff-result").value.trim();
  if (!text) { toast("内容为空，先点「生成」试试"); return; }
  if (!confirm("将生成的交接说明写入项目并覆盖现有内容？")) return;
  try {
    await api("/api/projects/" + currentPid, { method: "PATCH",
      body: JSON.stringify({ handoff_note: text }) });
    toast("交接说明已更新 ✓");
    closeModal("ai-handoff-modal");
    current = await api("/api/projects/" + currentPid);
    renderDetail();
  } catch (e) { toast("应用失败：" + e.message); }
}

// ============ 仪表盘 / 图表 ============
const CHART_COLORS = ["#94a3b8", "#60a5fa", "#fbbf24", "#34d399"];
const TREND_COLORS = ["#4cc2ff", "#8b7cf6", "#34d399", "#fbbf24", "#f87171", "#60a5fa", "#f472b6", "#a3e635"];

function donutSVG(dist, colors, size = 112, r = 40) {
  const total = Object.values(dist).reduce((a, b) => a + b, 0) || 1;
  const C = 2 * Math.PI * r;
  let offset = 0, segs = "";
  Object.values(dist).forEach((v, i) => {
    if (!v) return;
    const dash = (v / total) * C;
    segs += `<circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${colors[i % colors.length]}" stroke-width="15" stroke-dasharray="${dash} ${C - dash}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${size / 2} ${size / 2})"><title>${Object.keys(dist)[i]}: ${v}</title></circle>`;
    offset += dash;
  });
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${segs}
    <text x="50%" y="47%" text-anchor="middle" fill="var(--text)" font-size="21" font-weight="800">${total}</text>
    <text x="50%" y="62%" text-anchor="middle" fill="var(--text-faint)" font-size="9">项目</text></svg>`;
}

