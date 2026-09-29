/* 全局状态 / 工具 / 总览 / 事件委托总表 / 详情
 *
 * 2026-09-28 从 `web/index.html` 的 <script> 块按**原顺序**整段切出，内容逐字未改。
 * ⚠️ 加载顺序是契约：index.html 里这几个 <script> 的顺序不能调换 ——
 *    常量与函数在各自文件顶层执行，换序会撞 TDZ（ReferenceError）。
 */
// ============ 全局状态 ============
let projects = [];           // 总览数据
let stats = null;            // 统计/就绪度数据
let current = null;          // 当前详情项目
let currentPid = null;
let filter = "全部";
let editTagIndex = -1;       // 项目标签编辑用
let selTags = new Set();
let editTaskId = null;
let editProjectId = null;

const STATUS_COLORS = { "规划中": "gray", "进行中": "blue", "已暂停": "orange", "已完成": "green" };
const TASK_STATUSES = ["待办", "进行中", "已完成", "阻塞"];
const STATUS_SET = new Set(["规划中", "进行中", "已暂停", "已完成"]);
const PRIORITY_SET = new Set(["高", "中", "低"]);
// 项目状态与任务状态是两套枚举（"待办"/"阻塞"只属于任务），别拿一个去校验另一个
const TASK_STATUS_SET = new Set(TASK_STATUSES);
// 优先级会被拼进 class 名（pri-高），所以连 AI 生成的值也不能当可信输入：先过白名单再落地。
function priCls(v) { return PRIORITY_SET.has(v) ? v : "中"; }
function taskStCls(v) { return TASK_STATUS_SET.has(v) ? v : "待办"; }

// ============ 工具 ============
async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    let msg = "请求失败 (" + res.status + ")";
    try { const j = await res.json(); if (j.detail) msg = j.detail; } catch (e) {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

let toastTimer = null;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  // 保存路径、失败原因常常很长，固定 2.2 秒读不完就消失了：按时长放宽，上限 7 秒
  const ms = Math.min(7000, 2200 + Math.max(0, String(msg).length - 24) * 60);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function closeModal(id) {
  document.getElementById(id).classList.remove("show");
  document.getElementById(id).querySelector(".modal").style.animation = "none";
}

// 关闭只留这一条路径：右上角 ✕、底部「完成」、点遮罩、按 Esc 都走它。
// 以前遮罩与 Esc 各自直接摘 .show，跳过了上面那个动画复位（于是弹窗只有第一次
// 打开有 pop 动画），Esc 也既不关右侧的 AI 监管面板、也不清已解析出的待确认操作。
function closeOneModal(id) {
  const m = document.getElementById(id);
  modalScroll.set(id, m.scrollTop);          // 长弹窗重开时回到上次看到的位置
  closeModal(id);
  if (id === "ai-cmd-modal") {
    // 关掉指令窗口就等于放弃这一批操作，否则下次打开时「确认执行」还指着上次的解析结果
    pendingActions = [];
    const row = document.getElementById("cmd-exec-row");
    if (row) row.style.display = "none";
  }
  backToOpener();
}

// 最上面一层：弹窗优先，其次是全屏的 AI 监管面板。关闭、Tab 陷阱都以它为准。
function topLayer() {
  const shown = document.querySelectorAll(".modal-mask.show");
  if (shown.length) return shown[shown.length - 1];
  const panel = document.getElementById("report-panel");
  return panel && panel.classList.contains("show") ? panel : null;
}

function closeTopModal() {
  const top = topLayer();
  if (!top) return false;
  if (top.id === "report-panel") { closeReport(); backToOpener(); return true; }
  closeOneModal(top.id);
  return true;
}

// 焦点管理：打开时把焦点送进弹窗（优先第一个输入框），关闭时还给触发它的元素。
// 没有这一套，键盘和读屏用户关掉弹窗后焦点掉回 body，得从页面顶部重新 Tab 一遍。
const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),'
                + 'select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
const modalScroll = new Map();
let modalOpener = null;

function visibleFocusables(layer) {
  return [...layer.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null);
}

function focusInto(layer) {
  const items = visibleFocusables(layer);
  const input = items.find(el => /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && el.type !== "checkbox");
  (input || items[0] || layer).focus({ preventScroll: true });
}

function backToOpener() {
  if (topLayer() || !modalOpener || !modalOpener.isConnected) return;
  modalOpener.focus({ preventScroll: true });
  modalOpener = null;
}

function trapTab(e) {
  const layer = topLayer();
  if (!layer) return;
  const items = visibleFocusables(layer);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  const out = !layer.contains(document.activeElement);
  if ((e.shiftKey && (document.activeElement === first || out))) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

function openModal(id) {
  const m = document.getElementById(id);
  // closeModal 为了收尾把动画关了；重开前复位回来，否则 pop 动画只有第一次有
  const box = m.querySelector(".modal");
  if (box) box.style.animation = "";
  if (!m.classList.contains("show")) modalOpener = document.activeElement;
  m.classList.add("show");
  m.scrollTop = modalScroll.get(id) || 0;
  focusInto(m);
}

document.querySelectorAll(".modal-mask").forEach(m => {
  m.addEventListener("click", e => { if (e.target === m) closeOneModal(m.id); });
});

// ============ 总览 ============
async function loadOverview() {
  try {
    const [pData, sData] = await Promise.all([api("/api/projects"), api("/api/stats")]);
    projects = pData.projects;
    stats = sData;
    if (pData.data_recovered) {
      toast(pData.recovered_from === "last-good"
        ? "数据文件损坏：已自动从最近一次正常保存的数据恢复（损坏文件留档为 .bak）"
        : "检测到数据文件损坏：已备份为 .bak 并重置为空看板");
    }
    renderDash();
    checkReminders(stats);
    // 总览是隐藏的时候（人在详情页里保存任务、拖卡片、改状态都会调到这里）
    // 数据要拉新，但把 N 张项目卡全删全建是白干 —— 切回总览时 showOverview 会再调一次，
    // 那时才真正渲染。真实规模下这是一次点击里最贵的一段 DOM 工作。
    if (document.getElementById("view-overview").style.display !== "none") renderOverview();
  } catch (e) {
    toast("加载失败：" + e.message);
    const grid = document.getElementById("grid");
    if (grid && !grid.childElementCount) {          // 首屏就失败时不该留一个空白网格
      grid.innerHTML = '<div style="grid-column:1/-1;padding:40px 10px;text-align:center;color:var(--text-dim)">'
        + "项目列表加载失败：" + esc(e.message)
        + ' <button class="btn" data-act="loadOverview">重试</button></div>';
    }
  }
}

function setFilter(el, f) {
  filter = f;
  document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
  el.classList.add("active");
  renderOverview();
}

function renderOverview() {
  const q = (document.getElementById("search").value || "").trim().toLowerCase();
  let list = projects.filter(p => {
    if (filter !== "全部" && p.status !== filter) return false;
    if (!q) return true;
    // 这个 haystack 必须和输入框 placeholder 说的那几项一致（有一条测试逐字对账）。
    // 总览列表接口本来就不含 background / handoff_note，所以这里搜不到它们 ——
    // 想按正文搜是 Ctrl+K 那个任务搜索，不是这个框。
    // `|| ""` 是有用的：`next_action` 缺键时字符串拼出 "undefined"，
    // 于是搜 "undef" 会命中所有没填下一步的项目。
    const hay = (p.name + " " + (p.tags || []).join(" ") + " " + (p.next_action || "")
                 + " " + (p.repo_path || "")).toLowerCase();
    return hay.includes(q);
  });
  document.getElementById("proj-count").textContent = "共 " + list.length + " 个项目";
  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty");
  // 三种"屏幕上没有卡片"的原因必须分开说。以前共用一句「还没有项目，点右上角新建」，
  // 于是手里有 5 个项目的人搜索打了个不匹配的词，界面就大字宣布"还没有项目"——
  // 看着就像数据被人清空了。
  empty.style.display = list.length ? "none" : "block";
  const msg = document.getElementById("empty-msg");
  if (list.length) {
    msg.textContent = "";
  } else if (!projects.length) {
    msg.textContent = "还没有项目，点右上角「新建项目」开始搭建你的交接看板吧";
  } else if (q) {
    msg.textContent = `没有匹配「${q}」的项目（共 ${projects.length} 个）—— 换个词，或清空搜索框`;
  } else {
    msg.textContent = `「${filter}」状态下没有项目（共 ${projects.length} 个）—— 点「全部」看所有项目`;
  }
  grid.innerHTML = list.map(p => cardHTML(p)).join("");
  bindCardEvents();
}

function cardHTML(p) {
  const c = p.task_counts || {};
  const st = STATUS_SET.has(p.status) ? p.status : "规划中";
  const pri = PRIORITY_SET.has(p.priority) ? p.priority : "中";
  const rd = (stats && stats.readiness || []).find(x => x.id === p.id);
  const prog = Math.max(0, Math.min(100, Number(p.progress) || 0));
  const rdBadge = rd ? `<span class="rd-badge rd-${rd.score >= 80 ? "hi" : rd.score >= 50 ? "mid" : "lo"}" title="交接就绪度 ${rd.score}/100">就绪 ${rd.score}</span>` : "";
  return `<div class="pcard" data-pid="${esc(p.id)}" tabindex="0" role="button" data-keyact="1">
    <div class="top">
      <div>
        <div class="name">${esc(p.name)}${rdBadge}</div>
        <div style="margin-top:6px"><span class="badge st-${st}">${esc(st)}</span>
        <span class="badge pr-${pri}" style="background:transparent;padding-left:2px">优先级 ${esc(pri)}</span></div>
      </div>
      <div class="actions">
        <button class="btn small" data-act="edit">编辑</button>
        <button class="btn small danger" data-act="del">删除</button>
      </div>
    </div>
    <div class="progress-row">
      <div class="bar"><i style="width:${prog}%"></i></div>
      <span class="num">${prog}%</span>
    </div>
    <div class="stats">
      <span class="stat s-todo">待办 <b>${c["待办"] || 0}</b></span>
      <span class="stat s-doing">进行中 <b>${c["进行中"] || 0}</b></span>
      <span class="stat s-done">已完成 <b>${c["已完成"] || 0}</b></span>
      <span class="stat s-blocked">阻塞 <b>${c["阻塞"] || 0}</b></span>
    </div>
    ${p.next_action ? `<div class="next-action"><span class="lab">下一步</span>${esc(p.next_action)}</div>` : ""}
    ${(p.tags && p.tags.length) ? `<div class="tags">${p.tags.map(t => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
    <div class="meta"><span>更新于 ${esc(p.updated_at)}</span><span>${p.total_tasks} 个任务</span></div>
  </div>`;
}

// 卡片交互统一走事件委托：不再把项目名拼进内联 onclick
// （原先 confirmDeleteProject('id','${esc(name)}') 里 &#39; 会被 HTML 解码回单引号，含撇号的项目名直接让按钮报语法错误）
function bindCardEvents() {
  const grid = document.getElementById("grid");
  if (!grid || grid.dataset.bound) return;
  grid.dataset.bound = "1";
  grid.addEventListener("click", e => {
    const card = e.target.closest(".pcard");
    if (!card) return;
    const pid = card.dataset.pid;
    if (e.target.closest(".actions")) {
      const actBtn = e.target.closest("[data-act]");
      if (!actBtn) return;            // 点在按钮外的空白处，不打开详情
      const p = (projects || []).find(x => x.id === pid);
      if (actBtn.dataset.act === "edit") openProjectModal(pid);
      else confirmDeleteProject(pid, p ? p.name : "");
      return;
    }
    openDetail(pid);
  });
}

// ============ 事件委托总表 ============
// 全部按钮/控件通过 data-act（可选 data-arg）声明行为，由此处统一分发：
// 页面里不再有内联 onclick，既避免拼串注入，也便于一眼看清所有交互入口。
const ACTIONS = {
  // —— 顶部与总览 ——
  openProjectModal: () => openProjectModal(),
  openAICmd: () => openAICmd(),
  // 要转发 data-arg：报告面板既能"现生成一份"（无参）也能回看某一份历史（带 rid）。
  // 写成 `() => openReport()` 会把 rid 丢掉，点历史条目变成重新跑一次分析。
  openReport: (arg) => openReport(arg),
  openAISettings: () => openAISettings(),
  setFilter: (arg, ev, el) => setFilter(el, arg),
  showOverview: () => showOverview(),
  openTrends: () => openTrends(),
  openAllPackage: () => openAllPackage(),
  openToday: () => openToday(),
  gotoTask: (arg) => gotoTask(arg),
  // —— 详情页 ——
  openTaskModal: () => openTaskModal(),
  editProject: () => editProject(),
  deleteProject: () => deleteProject(),
  copyHandoffText: () => copyHandoffText(),
  copyHandoffMarkdown: () => copyHandoffMarkdown(),
  openHandoffPackage: () => openHandoffPackage(),
  openAgentsMD: () => openAgentsMD(),
  openSummarize: () => openSummarize(),
  loadGitInfo: () => loadGitInfo(),
  downloadHandoffPage: () => downloadHandoffPage(),
  openAIHandoff: () => openAIHandoff(),
  applySuggestion: (arg, ev, el) => applySuggestion(el),
  // —— 弹窗通用 ——
  closeModal: arg => closeOneModal(arg),
  closeReport: () => closeReport(),
  // —— 表单保存 ——
  saveProject: () => saveProject(),
  saveTask: () => saveTask(),
  deleteTask: () => deleteTask(),
  saveAISettings: () => saveAISettings(),
  savePackage: (arg, ev, el) => savePackage({ currentTarget: el }),
  saveAgentsMD: (arg, ev, el) => saveAgentsMD({ currentTarget: el }),
  // —— AI 能力 ——
  testAI: () => testAI(),
  backupNow: () => backupNow(),
  exportData: () => exportData(),
  importData: () => importData(),
  openGlobalSearch: () => openGlobalSearch(),
  retryDetail: (arg) => openDetail(arg),
  loadOverview: () => loadOverview(),
  toggleTheme: () => toggleTheme(),
  copyContextJson: () => copyContextJson(),
  toggleMemoryForm: () => toggleMemoryForm(),
  saveMemory: () => saveMemory(),
  deleteMemory: (arg, ev, el) => deleteMemory(el.dataset.mid),
  appendJournal: () => appendJournal(),
  openSessionCapture: () => openSessionCapture(),
  scanSessions: () => scanSessions(),
  dismissReminder: () => dismissReminder(),
  ingestSession: () => ingestSession(),
  applySummarize: arg => applySummarize(arg),
  showRecoverable: () => showRecoverable(),
  restoreData: (arg) => restoreData(arg),
  sendCommand: () => sendCommand(),
  executeActions: () => executeActions(),
  generateHandoff: () => generateHandoff(),
  applyHandoff: () => applyHandoff(),
  generateAgentsMD: () => generateAgentsMD(),
  copyAgentsMD: () => copyAgentsMD(),
  previewAgentFiles: () => previewAgentFiles(),
  writeAgentFiles: (arg, ev, el) => writeAgentFiles({ currentTarget: el }),
  copyPackage: () => copyPackage(),
  runSummarize: () => runSummarize(),
};

function runAction(name, ev, el) {
  const fn = ACTIONS[name];
  if (!fn) { console.warn("未注册的操作：" + name); return; }
  // ACTIONS 里绝大多数实现是 async：try/catch 抓不到 promise rejection，
  // 失败会表现成"点了没反应"。这里统一兜一层，让任何一条都能被看见。
  const fail = err => {
    console.error("操作执行失败：" + name, err);
    toast("操作失败：" + (err && err.message ? err.message : err));
  };
  try {
    const r = fn(el.dataset.arg, ev, el);
    if (r && typeof r.catch === "function") r.catch(fail);
  } catch (err) {
    fail(err);
  }
}

document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  if (el.closest("#grid .pcard")) return;   // 项目卡片由 bindCardEvents 单独处理（需要 pid）
  runAction(el.dataset.act, e, el);
});

document.addEventListener("keydown", e => {
  if (e.key === "Tab") { trapTab(e); return; }
  if (e.key !== "Enter") return;
  const el = e.target.closest("[data-enter]");
  if (el) runAction(el.dataset.enter, e, el);
});

// 搜索框防抖：卡片是全量重渲染，逐字符触发在项目多时会卡
let searchTimer = null;
const searchInput = document.getElementById("search");
if (searchInput) {
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderOverview, 120);
  });
}

// ============ 详情 ============
function setDetailState(mode, err, pid) {
  const box = document.getElementById("detail-state");
  if (!mode) { box.style.display = "none"; box.innerHTML = ""; return; }
  box.innerHTML = mode === "loading"
    ? "加载中…"
    : "<div>打开失败：" + esc(err || "未知错误") + "</div>"
      + '<button class="btn" data-act="retryDetail" data-arg="' + esc(pid || "") + '">重试</button>';
  box.style.display = "flex";
}

async function openDetail(pid) {
  currentPid = pid;
  // 换项目看一眼就要一份现值：清掉上一份 git 缓存（同一次停留期间仍复用，
  // 免得每存一个任务就去跑一次 git log）
  gitCache = { pid: null, data: null, failed: null };
  document.getElementById("view-overview").style.display = "none";
  document.getElementById("view-detail").classList.add("active");
  setDetailState("loading");
  try {
    current = await api("/api/projects/" + pid);
    setDetailState(null);
    renderDetail();
  } catch (e) {
    setDetailState("error", e.message, pid);   // 停在原地让人能重试，而不是闪一条 toast
  }
}

function showOverview() {
  document.getElementById("view-overview").style.display = "block";
  document.getElementById("view-detail").classList.remove("active");
  loadOverview();
}

// 看板的数据会被"别人"改掉：MCP 服务是另一个进程，桌面端也可能开着第二个窗口，
// 或者刚被某个智能体用 kanban_update_task 写过。以前切回窗口看到的还是离开时的样子，
// 得手动点刷新才知道变了 —— 这里在窗口重新可见/重新获得焦点时补一次拉取。
(function bindRefreshOnReturn() {
  let lastAt = 0;
  const refresh = () => {
    if (document.hidden) return;
    const now = Date.now();
    if (now - lastAt < 4000) return;     // 防抖：最小化恢复/反复切窗不该连打接口
    lastAt = now;
    const detail = document.getElementById("view-detail");
    if (detail.classList.contains("active") && currentPid) {
      api("/api/projects/" + currentPid)
        .then(p => { current = p; renderDetail(); })
        .catch(e => toast("刷新当前项目失败：" + e.message));
    }
    loadOverview();
  };
  document.addEventListener("visibilitychange", refresh);
  window.addEventListener("focus", refresh);
})();

// 列内排序（只影响显示，不动数据顺序 —— 数据顺序由新建/更新的时间决定）
let taskSortMode = "default";
try { taskSortMode = localStorage.getItem("kanban-task-sort") || "default"; } catch (e) {}

function sortTasks(tasks, mode) {
  const arr = (tasks || []).slice();
  const pri = { "高": 0, "中": 1, "低": 2 };
  if (mode === "priority") {
    arr.sort((a, b) => (pri[a.priority] ?? 1) - (pri[b.priority] ?? 1)
      || String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  } else if (mode === "due") {
    // 已完成的往后排；没设截止的排在有截止的后面
    const key = t => (t.status === "已完成" ? "9" : "0") + (t.due_date || "9999-12-31");
    arr.sort((a, b) => key(a).localeCompare(key(b)));
  } else if (mode === "updated") {
    arr.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  }
  return arr;
}

function renderDetail() {
  const p = current;
  const stName = STATUS_SET.has(p.status) ? p.status : "规划中";
  const prName = PRIORITY_SET.has(p.priority) ? p.priority : "中";
  const prog = Math.max(0, Math.min(100, Number(p.progress) || 0));
  document.getElementById("d-name").textContent = p.name;
  const st = document.getElementById("d-status");
  st.className = "badge st-" + stName;
  st.textContent = stName;
  const pr = document.getElementById("d-priority");
  pr.className = "badge pr-" + prName;
  pr.textContent = "优先级 " + prName;
  document.getElementById("d-progress-val").textContent = prog + "%";
  document.getElementById("d-progress-num").textContent = prog + "%";
  document.getElementById("d-progress-bar").style.width = prog + "%";
  document.getElementById("d-repo").textContent = p.repo_path || "—";
  document.getElementById("d-tags").textContent = (p.tags || []).join("、") || "—";
  document.getElementById("d-updated").textContent = p.updated_at || "—";
  document.getElementById("d-background").innerHTML = renderMarkdown(p.background || "（未填写）");
  document.getElementById("d-handoff").innerHTML = renderMarkdown(p.handoff_note || "（未填写，建议补充给智能体的交接说明）");
  document.getElementById("d-next").innerHTML = renderMarkdown(p.next_action || "（未填写）");
  renderReadiness(p.id);
  renderMemories(p.id);
  const tasks = sortTasks(p.tasks || [], taskSortMode);
  document.getElementById("d-task-total").textContent = "共 " + tasks.length + " 个任务";
  const cols = { "待办": "col-todo", "进行中": "col-doing", "已完成": "col-done", "阻塞": "col-blocked" };
  const counts = { "待办": 0, "进行中": 0, "已完成": 0, "阻塞": 0 };
  for (const k of Object.keys(cols)) document.getElementById(cols[k]).innerHTML = "";
  tasks.forEach(t => {
    const tst = taskStCls(t.status);
    const tpri = priCls(t.priority);
    counts[tst] = (counts[tst] || 0) + 1;
    const el = document.createElement("div");
    el.className = "tcard";
    el.draggable = true;
    el.tabIndex = 0;
    el.dataset.keyact = "1";            // 键盘 Enter/空格 也能打开（见全局 keydown）
    el.onclick = () => openTaskModal(t.id);
    el.ondragstart = (e) => {
      e.dataTransfer.setData("text/plain", t.id);
      e.dataTransfer.effectAllowed = "move";
      el.classList.add("dragging");
    };
    el.ondragend = () => el.classList.remove("dragging");
    const refHtml = (t.refs && t.refs.length)
      ? `<div class="td" style="font-family:Consolas,monospace;font-size:11px">📍 ${t.refs.map(r => esc(r)).join("、")}</div>`
      : "";
    const depHtml = (t.depends_on && t.depends_on.length && t.status !== "已完成")
      ? (() => {
          // 光列「前置：A、B」没回答那个真正的问题：**这条现在能不能动手**。
          // 后端在 /api/context 与 /api/projects/{pid}/next 里都算了 waiting_for，
          // 界面上一直没有 —— 用户判断"下一步做哪条"只能自己对着四列看。
          const pend = [], sat = [];
          t.depends_on.forEach(d => {
            const x = (current.tasks || []).find(y => y.id === d);
            (x && x.status === "已完成" ? sat : pend).push(x ? x.title : d);
          });
          if (!pend.length) {
            return `<div class="td dep-line free" style="font-size:11px">✓ ${sat.length} 条前置已完成，可以动手</div>`;
          }
          return `<div class="td dep-line wait" style="font-size:11px">在等 ${pend.length} 条前置：${pend.map(esc).join("、")}</div>`;
        })()
      : "";
    // 「这条多久没动静了」：journal 是后端每次状态流转自动记一条的，天然就是活动时间线。
    // 以前 journal 只在任务编辑弹窗里出现，卡片上看不出"这条今天还在动"还是"三周没人碰"。
    const lastBeat = (t.journal && t.journal.length)
      ? (t.journal[t.journal.length - 1].time || t.updated_at || "") : (t.updated_at || "");
    const idleD = daysSince(lastBeat);
    const idleHtml = (t.status !== "已完成" && idleD !== null && idleD >= 7)
      ? `<span class="idle-chip long">⏱ ${idleD} 天没记录</span>` : "";
    const di = dueInfo(t);
    const dueHtml = di ? `<span class="due-badge ${di.cls}">📅 ${esc(di.text)}</span>` : "";
    el.innerHTML = `
      <div class="tt">${esc(t.title)}${dueHtml}</div>
      ${t.desc ? `<div class="td">${esc(t.desc)}</div>` : ""}
      ${refHtml}
      ${depHtml}
      <div class="tfoot">
        <span class="tag2 pri-${tpri}">${esc(tpri)}</span>
        ${t.agent ? `<span class="agent">🤖 ${esc(t.agent)}</span>` : ""}
        ${idleHtml ? `<span style="margin-left:auto">${idleHtml}</span>` : ""}
      </div>`;
    document.getElementById(cols[tst] || "col-todo").appendChild(el);
  });
  for (const [k, v] of Object.entries(counts)) {
    document.getElementById("c-" + ({ "待办": "todo", "进行中": "doing", "已完成": "done", "阻塞": "blocked" }[k])).textContent = v;
  }
  renderReality();
  renderGraph();
}

