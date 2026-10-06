/* 全量导出导入 / 备份 / 恢复 / 全局搜索 / 深浅色主题
 *
 * 2026-09-28 从 `web/index.html` 的 <script> 块按**原顺序**整段切出，内容逐字未改。
 * ⚠️ 加载顺序是契约：index.html 里这几个 <script> 的顺序不能调换 ——
 *    常量与函数在各自文件顶层执行，换序会撞 TDZ（ReferenceError）。
 */
// ============ 全量数据导出 / 导入 ============
async function exportData() {
  // 用 fetch 拿原始响应体：api() 会把结果解析成 JSON，拿不到下载用的 blob
  try {
    const res = await fetch("/api/data/export");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "").slice(0, 12);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `kanban-data_${stamp}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast("已导出全部数据 ✓");
  } catch (e) { toast("导出失败：" + e.message); }
}

function importData() {
  const input = document.getElementById("import-file");
  input.value = "";                       // 清空才能连续导入同一个文件
  input.click();
}

async function onImportFilePicked(ev) {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  const text = await file.text();
  try {
    JSON.parse(text);
  } catch (e) {
    toast("这个文件不是合法 JSON：" + e.message);
    return;
  }
  // 后端是覆盖语义（这正是"换机恢复"该有的行为），必须说清楚，别让用户以为在合并
  if (!confirm(`导入会【覆盖】现有全部项目（导入前会自动做一份备份）。\n\n文件：${file.name}\n确定继续？`)) return;
  try {
    const r = await api("/api/data/import", { method: "POST", body: JSON.stringify({ content: text }) });
    // 后端返回的是 `{ok, imported}`，没有 `projects` 这个键 —— 以前这里读
    // `(r.projects || []).length`，于是导入明明成功，界面永远提示"已导入 0 个项目 ✓"。
    const n = (r && typeof r.imported === "number") ? r.imported : 0;
    let msg = `已导入 ${n} 个项目 ✓`;
    if (r && typeof r.memories === "number") msg += `、${r.memories} 条记忆`;
    // 文件声明的结构版本比本看板新：字段可能被规范化掉了，这句话必须让用户看到
    if (r && r.schema_note) msg += "｜" + r.schema_note;
    // 有条目被丢掉同样要说：导入别人的板子时"少两个项目"和"一个没少"以前长得一模一样
    if (r && r.dropped_note) msg += "｜⚠ " + r.dropped_note;
    toast(msg);
    await loadOverview();
  } catch (e) { toast("导入失败：" + e.message); }
}

// 文件选择框不会触发 click 动作，事件委托覆盖不到，单独绑定
(function bindImportInput() {
  const input = document.getElementById("import-file");
  if (input) input.addEventListener("change", onImportFilePicked);
})();

// ============ 数据备份 ============
async function backupNow() {
  const btn = document.getElementById("backup-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/backup", { method: "POST" });
    toast("备份成功：共 " + r.total + " 份 ✓");
    document.getElementById("backup-tip").textContent = "最新备份：" + r.folder.split(/[\\/]/).pop();
    loadBackups();
  } catch (e) { toast("备份失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

async function loadBackups() {
  const list = document.getElementById("backup-list");
  if (!list) return;
  try {
    const r = await api("/api/backups");
    list.innerHTML = (r.backups || []).length
      ? r.backups.map(b => `<div class="bk"><span>📦 ${esc(b.time)}</span><span>${b.files.join("、")}</span></div>`).join("")
      : '<div style="padding:6px 2px">暂无备份</div>';
  } catch (e) { list.innerHTML = '<div style="padding:6px 2px">加载失败</div>'; }
}

// ============ 数据恢复 ============
async function showRecoverable() {
  const box = document.getElementById("recover-list");
  if (!box) return;
  box.style.display = "block";
  box.innerHTML = "加载中…";
  try {
    const r = await api("/api/recoverable");
    const cur = r.current || {};
    const items = r.recoverable || [];
    const head = `<div style="color:var(--text-faint)">当前：${cur.project_count || 0} 个项目、`
      + `${cur.task_count || 0} 个任务</div>`;
    if (!items.length) {
      box.innerHTML = head + '<div style="margin-top:4px">没有可恢复的来源（还没有 .bak 或手动快照）</div>';
      return;
    }
    const rows = items.map(it => {
      const usable = it.usable;
      const desc = usable
        ? `${it.project_count} 个项目 / ${it.task_count} 个任务 · ${esc(it.mtime || "")} · ${(it.names || []).map(esc).join("、")}`
        : `<span style="color:var(--red)">${esc(it.reason || "不可用")}</span>`;
      const btn = usable
        ? `<button class="btn" data-act="restoreData" data-arg="${esc(it.source)}">恢复</button>`
        : "";
      return `<div style="display:flex;justify-content:space-between;gap:10px;align-items:center;padding:4px 0;border-top:1px solid var(--line)">`
        + `<div><div>${esc(it.label)}</div><div style="color:var(--text-faint);font-size:11px">${desc}</div></div>`
        + `<div>${btn}</div></div>`;
    }).join("");
    box.innerHTML = head + rows
      + '<div style="margin-top:6px;color:var(--text-faint);font-size:11px">恢复会覆盖当前数据；覆盖前会自动把当前数据另存为 projects.json.before-restore.bak</div>';
  } catch (e) {
    box.innerHTML = "加载失败：" + esc(e.message);
  }
}

async function restoreData(source) {
  if (!source) return;
  if (!confirm(`确定用「${source}」覆盖当前看板数据吗？\n当前数据会先另存为 before-restore.bak，`
               + `在下面的列表里能选回来。`)) return;
  try {
    const r = await api("/api/restore", { method: "POST", body: JSON.stringify({ source }) });
    // 少恢复了必须说出来：一句"已恢复 N 个项目"分不清"本来就只有 N 个"和"丢了几条"
    toast(`已恢复 ${r.project_count} 个项目、${r.task_count} 个任务 ✓`
          + (r.dropped_note ? `\n⚠ ${r.dropped_note}` : ""));
    await loadOverview();
    showRecoverable();
  } catch (e) { toast("恢复失败：" + e.message); }
}

// ============ 全局任务搜索 / 命令面板 ============
let gsTimer = null;

// —— Ctrl+K 命令面板：每个命令是一个 {id, label, hint, run, need}。
//    走 data-act 分发（run 只写"怎么干"，paletteRun 统一负责"先关面板再干"），
//    不写内联 onclick —— test_frontend.py 会数死这个数。
const PALETTE_CMDS = [
  { id: "new-project", label: "＋ 新建项目", hint: "打开项目编辑弹窗，从零建一个交接项目", run: () => openProjectModal() },
  { id: "new-task", label: "＋ 新建任务", hint: "给当前项目加一条任务（需先在详情页）", need: "detail", run: () => openTaskModal() },
  { id: "view-overview", label: "🏠 返回总览", hint: "切回全部项目卡片视图", run: () => showOverview() },
  { id: "view-today", label: "📅 今日待办", hint: "打开「今天能动手的任务」面板", run: () => openToday() },
  { id: "view-inbox", label: "🔔 需要拍板", hint: "逾期 / 待审查 / 阻塞——只列等人的事", run: () => openInbox() },
  { id: "copy-handoff", label: "📋 复制交接摘要", hint: "把当前项目的交接摘要复制到剪贴板（接管惯性）", need: "detail", run: () => copyHandoffText() },
  { id: "copy-handoff-md", label: "📄 复制交接 Markdown", hint: "复制当前项目完整交接文案（Markdown 格式）", need: "detail", run: () => copyHandoffMarkdown() },
  { id: "export-package", label: "📦 导出交接包", hint: "生成当前项目完整交接包（可脱敏）", need: "detail", run: () => openHandoffPackage() },
  { id: "export-all-package", label: "🗃️ 全项目交接包", hint: "导出所有项目的合并交接包", run: () => openAllPackage() },
  { id: "copy-context-json", label: "🧠 复制机器可读上下文", hint: "当前项目的 JSON 上下文，贴给智能体即可接手", need: "detail", run: () => copyContextJson() },
  { id: "ai-command", label: "🤖 AI 指令", hint: "给智能体下自然语言指令（新建/状态/交接说明）", run: () => openAICmd() },
  { id: "ai-settings", label: "⚙️ AI 设置", hint: "端点 / Key / 模型 / 用量统计", run: () => openAISettings() },
  { id: "session-capture", label: "📥 会话捕获", hint: "扫描本机 AI 工具会话，提炼入库", run: () => openSessionCapture() },
  { id: "data-export", label: "💾 导出数据", hint: "把整块看板数据导出为 JSON 文件", run: () => exportData() },
  { id: "backup-now", label: "🛡️ 立即备份", hint: "给当前数据拍一份快照（保留 10 份）", run: () => backupNow() },
  { id: "toggle-theme", label: "🎨 切换深浅色", hint: "在深色 / 浅色主题之间切换", run: () => toggleTheme() },
];

function paletteEnabled(cmd) {
  return cmd.need !== "detail" || !!currentPid;
}

function renderPalette(q) {
  const box = document.getElementById("palette-commands");
  if (!box) return;
  const kw = (q || "").replace(/^>\s*/, "").trim().toLowerCase();
  const list = PALETTE_CMDS.filter(c =>
    !kw || c.label.toLowerCase().includes(kw) || c.hint.toLowerCase().includes(kw)
      || c.id.includes(kw));
  box.innerHTML = list.length
    ? list.map(c => {
        const on = paletteEnabled(c);
        return `<button type="button" class="palette-item${on ? "" : " off"}" data-act="paletteRun" data-arg="${esc(c.id)}"
          tabindex="0" ${on ? "" : 'disabled title="先打开一个项目详情页"'}
          style="display:flex;align-items:center;gap:10px;width:100%;text-align:left;background:var(--bg-soft);border:1px solid var(--border);border-radius:8px;padding:8px 11px;cursor:pointer;opacity:${on ? "1" : ".5"}">
          <span style="flex:1"><b>${esc(c.label)}</b><span style="display:block;font-size:11.5px;color:var(--text-faint)">${esc(c.hint)}</span></span>
          <span class="badge" style="flex-shrink:0;font-size:10px">↵ 执行</span></button>`;
      }).join("")
    : '<div style="color:var(--text-faint);font-size:12px;padding:8px">没有匹配的命令</div>';
}

async function openGlobalSearch() {
  const stuck = [...document.querySelectorAll(".modal-mask.show")].filter(m => m.id !== "global-search-modal");
  if (stuck.length) {           // 有窗口开着时 Ctrl+K 是"往回退一层"，不是上架搜索浮层
    closeOneModal(stuck[stuck.length - 1].id);
    return;
  }
  const input = document.getElementById("gs-input");
  input.value = "";
  document.getElementById("gs-results").innerHTML = "";
  renderPalette("");
  openModal("global-search-modal");
  setTimeout(() => input.focus(), 50);
}

// 命令面板是"搜索 + 命令"二合一：输入的关键词以 `>` 开头就进命令过滤，
// 否则照旧搜任务。空输入展示全部命令（比空搜索更常用）。
function paletteRun(id) {
  closeOneModal("global-search-modal");
  const cmd = PALETTE_CMDS.find(c => c.id === id);
  if (!cmd) { toast("未知命令：" + id); return; }
  if (!paletteEnabled(cmd)) { toast("这个命令需要先打开一个项目详情页"); return; }
  try { cmd.run(); } catch (e) { toast("命令执行失败：" + e.message); }
}

async function runGlobalSearch(q) {
  const box = document.getElementById("gs-results");
  if (!q) { box.innerHTML = '<div style="color:var(--text-faint);font-size:12px;padding:8px">输入关键词搜索所有项目的任务…</div>'; return; }
  try {
    const r = await api("/api/search/tasks?q=" + encodeURIComponent(q));
    const list = r.results || [];
    // "给我 30 条"不等于"只有 30 条"：不说的话，第 31 条在用户眼里就是不存在。
    // 用 .gs-note 而不是 .gs-item —— 下面给结果行绑点击，提示行不该跟着可点。
    const note = r.truncated
      ? `<div class="gs-note" style="opacity:.75;font-size:11.5px;padding:6px 8px">`
        + `只列出前 ${esc(String(r.limit || list.length))} 条，后面还有 —— 换个更具体的关键词</div>`
      : "";
    if (!list.length) { box.innerHTML = '<div style="color:var(--text-faint);font-size:12px;padding:8px">没有匹配的任务</div>'; return; }
    box.innerHTML = note + list.map(t => `
      <div class="gs-item" tabindex="0" role="button" data-keyact="1" data-pid="${esc(t.project_id)}" style="cursor:pointer;background:var(--bg-soft);border:1px solid var(--border);border-radius:9px;padding:9px 12px;display:flex;align-items:center;gap:8px">
        <span class="badge st-${taskStCls(t.status)}">${esc(taskStCls(t.status))}</span>
        <span style="flex:1">${esc(t.title)}</span>
        <span class="tag2 pri-${priCls(t.priority)}">${esc(priCls(t.priority))}</span>
        ${t.due_date ? `<span class="due-badge due-ok">📅 ${esc(t.due_date.slice(5))}</span>` : ""}
        ${t.agent ? `<span class="agent" title="这条由哪个智能体负责">🤖 ${esc(t.agent)}</span>` : ""}
        <span style="font-size:11px;color:var(--text-faint);flex-shrink:0">${esc(t.project_name)}</span>
      </div>`).join("");
    box.querySelectorAll(".gs-item").forEach(el => {
      el.addEventListener("click", () => {
        closeModal("global-search-modal");
        openDetail(el.dataset.pid);
      });
    });
  } catch (e) {
    box.innerHTML = '<div style="color:var(--red);font-size:12px">搜索失败：' + esc(e.message) + "</div>";
  }
}

(function bindGlobalSearch() {
  const input = document.getElementById("gs-input");
  if (!input) return;
  input.addEventListener("input", () => {
    clearTimeout(gsTimer);
    const v = input.value.trim();
    if (!v || v.startsWith(">")) {
      document.getElementById("gs-results").innerHTML = "";
      renderPalette(v);
      return;
    }
    document.getElementById("palette-commands").innerHTML = "";
    gsTimer = setTimeout(() => runGlobalSearch(v), 250);
  });
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openGlobalSearch();
    }
  });
})();

// ============ 深浅色主题 ============
function applyTheme(t) {
  const root = document.documentElement;
  if (t === "light") root.setAttribute("data-theme", "light");
  else root.removeAttribute("data-theme");
  const btn = document.getElementById("theme-btn");
  if (btn) btn.textContent = t === "light" ? "☀️" : "🌙";
  try { localStorage.setItem("kanban-theme", t); } catch (e) {}
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  applyTheme(cur === "light" ? "dark" : "light");
  // 依赖图的填色是服务端算好的，换主题不重画就会出现"浅底上一块深卡片"
  renderGraph();
}

(function initTheme() {
  let t = "dark";
  try { t = localStorage.getItem("kanban-theme") || "dark"; } catch (e) {}
  applyTheme(t);
})();

