/* 表单编辑：项目与任务增删改 / 里程碑行编辑 / 标签 chip / 自动分配 / 交接文本复制。*/
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
  return `<div class="ms-row">
    <input class="ms-name" value="${esc(name)}" placeholder="里程碑名称（≤40 字）" maxlength="40"
      aria-label="里程碑名称">
    <label class="switch ms-switch" title="手工标记该节点已经过去">
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

function removeMilestoneRow(btn) {
  // 按元素删，不按下标删：data-arg 是渲染时写死的序号，删过一行之后剩余行的
  // data-arg 与 DOM 下标错位，按下标会删错行（甚至点第三行的 ✕ 删不到任何行）。
  const row = btn.closest(".ms-row");
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
  document.getElementById("t-copy-id").style.display = taskId ? "inline-block" : "none";
  document.getElementById("t-title").value = "";
  document.getElementById("t-desc").value = "";
  document.getElementById("t-status").value = "待办";
  document.getElementById("t-priority").value = "中";
  document.getElementById("t-agent").value = "";
  document.getElementById("t-refs").value = "";
  document.getElementById("t-acceptance").value = "";
  document.getElementById("t-due").value = "";
  document.getElementById("t-blocked").value = "";
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
      document.getElementById("t-blocked").value = t.blocked_reason || "";
      document.getElementById("t-milestone").value = t.milestone || "";
      const mine = t.depends_on || [];
      Array.from(depsSel.options).forEach(o => { o.selected = mine.includes(o.value); });
    }
  }
  syncBlockedRow();
  openModal("task-modal");
}

// 「阻塞原因」行的显隐跟着状态下拉走：只在状态=阻塞时可填。
// 后端语义是"字段随状态存在，离开自动清"（见 _apply_task），界面把它明示出来，
// 不然用户会以为填了就永远在。监听在 openTaskModal 之外绑定一次（无内联事件契约）。
function syncBlockedRow() {
  const row = document.getElementById("t-blocked-row");
  const sel = document.getElementById("t-status");
  if (row && sel) row.style.display = sel.value === "阻塞" ? "flex" : "none";
}

(function bindBlockedRowToggle() {
  const sel = document.getElementById("t-status");
  if (!sel) return;
  sel.addEventListener("change", syncBlockedRow);
})();

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
    // 阻塞原因：非阻塞状态后端会忽略并清空（字段随状态存在），前端不做状态判断副本
    blocked_reason: document.getElementById("t-blocked").value.trim(),
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

// 任务编号一键复制（第六轮吸取：tsk/overclick 的"随时可复制 id"）。
// 用途：把"让智能体改哪一条"说清楚，或者贴进别的工具里引用 —— 以前只能从
// 接口响应或交接包里翻。剪贴板 API 不可用（非安全上下文/权限拒绝）时退回 execCommand。
function copyTaskId() {
  if (!editTaskId) return;
  const tid = editTaskId;
  const ok = () => toast("已复制任务编号 " + tid + " ✓");
  const fallback = () => {
    const ta = document.createElement("textarea");
    ta.value = tid;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (e) { copied = false; }
    document.body.removeChild(ta);
    return copied;
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(tid).then(ok)
      .catch(() => { fallback() ? ok() : toast("复制失败，请手动复制"); });
    return;
  }
  fallback() ? ok() : toast("复制失败，请手动复制");
}

// 键盘：Esc 逐层关最上面的浮层；Enter / 空格 打开被聚焦的卡片
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeTopModal(); return; }
  if (e.key !== "Enter" && e.key !== " ") return;
  const el = document.activeElement;
  if (el && el.dataset && el.dataset.keyact && el.click) { e.preventDefault(); el.click(); }
});

