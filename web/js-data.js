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

// ============ 全局任务搜索 ============
let gsTimer = null;

async function openGlobalSearch() {
  const stuck = [...document.querySelectorAll(".modal-mask.show")].filter(m => m.id !== "global-search-modal");
  if (stuck.length) {           // 有窗口开着时 Ctrl+K 是"往回退一层"，不是上架搜索浮层
    closeOneModal(stuck[stuck.length - 1].id);
    return;
  }
  document.getElementById("gs-input").value = "";
  document.getElementById("gs-results").innerHTML = '<div style="color:var(--text-faint);font-size:12px;padding:8px">输入关键词搜索所有项目的任务…</div>';
  openModal("global-search-modal");
  setTimeout(() => document.getElementById("gs-input").focus(), 50);
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
    gsTimer = setTimeout(() => runGlobalSearch(input.value.trim()), 250);
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

