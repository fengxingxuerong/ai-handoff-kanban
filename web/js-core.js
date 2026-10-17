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
const TASK_STATUSES = ["待办", "进行中", "待审查", "已完成", "阻塞"];
const STATUS_SET = new Set(["规划中", "进行中", "已暂停", "已完成"]);
const PRIORITY_SET = new Set(["高", "中", "低"]);
// 项目状态与任务状态是两套枚举（"待办"/"阻塞"只属于任务），别拿一个去校验另一个
const TASK_STATUS_SET = new Set(TASK_STATUSES);
// 优先级会被拼进 class 名（pri-高），所以连 AI 生成的值也不能当可信输入：先过白名单再落地。
function priCls(v) { return PRIORITY_SET.has(v) ? v : "中"; }
function taskStCls(v) { return TASK_STATUS_SET.has(v) ? v : "待办"; }

// ============ 工具 ============
async function api(path, opts) {
  const options = opts || {};
  const headers = { ...(options.headers || {}), "Content-Type": "application/json" };
  // 服务端只在设置 KANBAN_TOKEN 时注入这个 meta；没设置时保持空头，不影响现有调用。
  const tokenMeta = document.querySelector('meta[name="kanban-token"]');
  if (tokenMeta && tokenMeta.content) headers["X-Kanban-Token"] = tokenMeta.content;
  const res = await fetch(path, { ...options, headers });
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
    // 需要拍板的数量（逾期/待审查/阻塞）：单独一条请求，失败不拖累总览加载
    api("/api/inbox").then(r => updateInboxBadge(r.counts)).catch(() => {});
    // 总览是隐藏的时候（人在详情页里保存任务、拖卡片、改状态都会调到这里）
    // 数据要拉新，但把 N 张项目卡全删全建是白干 —— 切回总览时 showOverview 会再调一次，
    // 那时才真正渲染。真实规模下这是一次点击里最贵的一段 DOM 工作。
    if (document.getElementById("view-overview").style.display !== "none") renderOverview();
  } catch (e) {
    toast("加载失败：" + e.message);
    const grid = document.getElementById("grid");
    // 判据是"还没渲染过真实卡片"，不是"网格里没有子元素" —— 首屏那三张骨架卡也是
    // 子元素，用旧判据的话加载失败会永久停在骨架上（看着像一直在加载，实则是挂了）
    if (grid && !grid.dataset.rendered) {
      grid.innerHTML = '<div style="grid-column:1/-1;padding:40px 10px;text-align:center;color:var(--text-dim)">'
        + "项目列表加载失败：" + esc(e.message)
        + ' <button class="btn" data-act="loadOverview">重试</button></div>';
    }
  }
}

function setFilter(el, f) {
  filter = f;
  activeViewId = "";                                   // 手动改筛选 = 脱离已保存视图快照
  syncViewSelect();                                    // 下拉还显示着已脱离的视图名会误导（搜索框路径是同步的）
  document.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
  el.classList.add("active");
  renderOverview();
}

function renderOverview() {
  const q = (document.getElementById("search").value || "").trim().toLowerCase();
  let list = projects.filter(p => {
    // 「最近」按浏览记录筛，别混进状态判断里 —— 它不是一种项目状态
    if (filter === "最近") {
      if (!recentIds().includes(p.id)) return false;
    } else if (filter !== "全部" && p.status !== filter) return false;
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
  // 置顶的排最前：每天打开的那两三个项目不用每次翻找
  const pins = pinnedIds();
  if (pins.length) list.sort((a, b) => (pins.indexOf(b.id) >= 0) - (pins.indexOf(a.id) >= 0));
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
  grid.dataset.rendered = "1";   // 骨架屏让位给真实卡片；也是给失败分支的信号"已经有内容了"
  bindCardEvents();
  updateBulkBar();               // 重画后同步批量条（勾选状态与计数）
}

function cardHTML(p) {
  const c = p.task_counts || {};
  const pinned = isPinned(p.id);
  const st = STATUS_SET.has(p.status) ? p.status : "规划中";
  const pri = PRIORITY_SET.has(p.priority) ? p.priority : "中";
  const rd = (stats && stats.readiness || []).find(x => x.id === p.id);
  const prog = Math.max(0, Math.min(100, Number(p.progress) || 0));
  const rdBadge = rd ? `<span class="rd-badge rd-${rd.score >= 80 ? "hi" : rd.score >= 50 ? "mid" : "lo"}" title="交接就绪度 ${rd.score}/100">就绪 ${rd.score}</span>` : "";
  // 五个计数原来各占一枚小药丸横着铺开（「待办 2 进行中 1 待审查 0 已完成 0 阻塞 0」），
  // 五段文字等权重，而真正看得懂的信息只有"几个在动、几个卡住"。改成一条堆叠条 + 总数：
  // 形状本身就是信息（哪一段粗），零值段自动不占宽度，还把卡片省下两行。
  const SEG = [["待办", "todo"], ["进行中", "doing"], ["待审查", "review"],
               ["已完成", "done"], ["阻塞", "blocked"]];
  const total = SEG.reduce((a, [k]) => a + (Number(c[k]) || 0), 0) || p.total_tasks || 0;
  const stack = `<div class="stack" role="img" aria-label="任务构成：`
    + SEG.map(([k]) => `${k} ${c[k] || 0}`).join("、") + `">`
    + SEG.map(([k, cls]) => Number(c[k] || 0) ? `<i class="s-${cls}" style="flex:${c[k]}"></i>` : "").join("")
    + `</div>`;
  return `<div class="pcard" data-pid="${esc(p.id)}" tabindex="0" role="button" data-keyact="1">
    <div class="head">
      <input type="checkbox" class="pick" data-pick="1" aria-label="选择这个项目"
             ${selectedPids.has(p.id) ? "checked" : ""}>
      <button class="btn small pin${pinned ? " on" : ""}" data-act="pin" aria-pressed="${pinned}"
              title="${pinned ? "取消置顶" : "置顶（排在总览最前面）"}">${pinned ? "★" : "☆"}</button>
      <span class="name" title="${esc(p.name)}">${esc(p.name)}</span>
      <span class="actions">
        <button class="btn small" data-act="edit" title="编辑项目">✎</button>
        <button class="btn small danger" data-act="del" title="删除项目">🗑</button>
      </span>
    </div>
    <div class="card-sub">
      ${rdBadge}
      <span class="badge st-${st}">${esc(st)}</span>
    </div>
    <div class="progress-row">
      <div class="bar"><i style="width:${prog}%"></i></div>
      <span class="num">${prog}%</span>
    </div>
    <div class="stack-row">
      ${stack}
      <span class="cnt">${total} 个任务</span>
    </div>
    ${p.next_action ? `<div class="next-action"><span class="lab">下一步</span>${esc(p.next_action)}</div>` : ""}
    <div class="card-foot">
      <span class="pri-lab pr-${pri}">优先级 ${esc(pri)}</span>
      ${(p.tags && p.tags.length) ? p.tags.map(t => `<span class="tag">${esc(t)}</span>`).join("") : ""}
      <span class="spacer"></span>
      <span>更新于 ${esc(p.updated_at)}</span>
    </div>
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
    const pick = e.target.closest("[data-pick]");
    if (pick) {                                            // 勾选：只改选择集，不打开详情
      if (pick.checked) selectedPids.add(pid); else selectedPids.delete(pid);
      updateBulkBar();
      return;
    }
    const actBtn = e.target.closest("[data-act]");
    if (actBtn) {
      const act = actBtn.dataset.act;
      if (act === "pin") { togglePin(pid); return; }        // 卡片上的按钮不该顺带打开详情
      if (act === "edit") { openProjectModal(pid); return; }
      if (act === "del") {
        const p = (projects || []).find(x => x.id === pid);
        confirmDeleteProject(pid, p ? p.name : "");
        return;
      }
      return;
    }
    if (e.target.closest(".actions")) return;              // 点在按钮外的空白处
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
  switchAITab: (arg) => switchAITab(arg),
  setFilter: (arg, ev, el) => setFilter(el, arg),
  showOverview: () => showOverview(),
  openTrends: (arg) => openTrends(arg),
  // 卡片上的置顶星标：实际分发在 bindCardEvents（卡片内的点击不走全局委托），
  // 这里登记一份是为了契约（每个 data-act 都要在表里）兼兜底。
  pin: (arg, ev, el) => togglePin(el.closest(".pcard") && el.closest(".pcard").dataset.pid),
  // 批量操作：data-arg 要转发（目标状态就写在它上面）
  bulkStatus: (arg) => bulkSetStatus(arg),
  bulkDelete: () => bulkDelete(),
  bulkClear: () => bulkClear(),
  openAllPackage: () => openAllPackage(),
  openToday: () => openToday(),
  openInbox: () => openInbox(),
  gotoTask: (arg) => gotoTask(arg),
  // —— 详情页 ——
  openTaskModal: () => openTaskModal(),
  openAssignModal: () => openAssignModal(),
  runAutoAssign: () => runAutoAssign(),
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
  addMilestoneRow: () => addMilestoneRow(),
  removeMilestoneRow: (arg, ev, el) => removeMilestoneRow(el),
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
  paletteRun: (arg) => paletteRun(arg),
  retryDetail: (arg) => openDetail(arg),
  loadOverview: () => loadOverview(),
  toggleTheme: () => toggleTheme(),
  copyContextJson: () => copyContextJson(),
  toggleMemoryForm: () => toggleMemoryForm(),
  saveMemory: () => saveMemory(),
  deleteMemory: (arg, ev, el) => deleteMemory(el.dataset.mid),
  toggleViewForm: () => toggleViewForm(),
  saveView: () => saveView(),
  deleteView: () => deleteView(),
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
  checkAgentFiles: () => checkAgentFiles(),
  writeAgentFiles: (arg, ev, el) => writeAgentFiles({ currentTarget: el }),
  copyPackage: () => copyPackage(),
  runSummarize: () => runSummarize(),
  toggleHeaderMenu: (arg, ev, el) => toggleHeaderMenu(el),
  togglePanel: (arg, ev, el) => togglePanel(el),
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

// ============ 顶栏下拉菜单 ============
// 之前 9 个按钮一字排开，窄窗口还会折行；现在只留高频的（今日 / 拍板）+ 三个图标按钮，
// AI 指令 / AI 监管 / 会话捕获收进「✨ AI」下拉里，顶栏一眼看得出主次。
// 键盘可达：菜单项是真 <button role="menuitem">，Tab 进得去，Esc 收得起。
function closeHeaderMenus() {
  document.querySelectorAll(".hmenu").forEach(m => { m.hidden = true; });
  document.querySelectorAll('[data-act="toggleHeaderMenu"]')
    .forEach(b => b.setAttribute("aria-expanded", "false"));
}

function toggleHeaderMenu(el) {
  const menu = document.getElementById("hmenu-" + (el.dataset.arg || ""));
  if (!menu) return;
  const willOpen = menu.hidden;
  closeHeaderMenus();                       // 同时只开一个
  if (willOpen) {
    menu.hidden = false;
    el.setAttribute("aria-expanded", "true");
    const first = menu.querySelector(".hmenu-item");
    if (first) first.focus();               // 打开就把焦点送进去，键盘用户不用再 Tab
  }
}

document.addEventListener("click", e => {
  // 点到菜单外面就收起
  if (!e.target.closest(".hmenu-wrap")) closeHeaderMenus();
  const el = e.target.closest("[data-act]");
  if (!el) return;
  // 卡片内的点击一律交给 bindCardEvents（它要拿 pid）。这里必须写 closest(".pcard")，
  // 不能写 closest("#grid .pcard") —— 后代组合选择器在 closest() 里不会按预期命中，
  // 于是同一个按钮会被处理两次（2026-10-17 实测：★ 一次点击里"置顶"又"取消"，存了个空数组）。
  // 这个坑一直是潜伏的：以前卡片里只有未注册进 ACTIONS 的 edit/del，第二次调用只留了条
  // console.warn 看不见。
  if (el.closest(".pcard")) return;
  runAction(el.dataset.act, e, el);
  if (e.target.closest(".hmenu-item")) closeHeaderMenus();   // 菜单项在 wrap 内，选完要显式收
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeHeaderMenus();
});

// ============ 详情页面板折叠 ============
// 详情页左列十来块面板全展开要滚很久，而「项目背景 / 看板说的 vs 代码 / 项目记忆」是
// 接手时才细看的参考信息，日常更关心任务与时间轴。这里让每块面板的**标题本身**成为开关
// （h3 加 role=button + data-keyact，点击与 Enter/空格走同一条路），低频的默认收起,
// 折叠态记在 localStorage，下次打开保持一致。
// 默认只收起真正"接手时才细看"的那一块（项目背景）。另外两块**不折**：
// · 项目记忆：记决策/坑/约定是持续动作，不是接手时才看；它自己的列表也有滚动区
// · 看板说的 vs 代码：它是**告警出口**（进度对不上、refs 失效、代码停了都从这里说），
//   默认收起等于把要动手的信号藏起来 —— 2026-10-17 加 refs 失效检测时实测到这点
const PANEL_DEFAULT_COLLAPSED = ["项目背景"];

function _panelKey(panel, h3) {
  return "kanban-panel-" + (panel.id || (h3.textContent || "").trim().slice(0, 14));
}

function _applyPanelCollapse(panel, collapsed) {
  panel.classList.toggle("collapsed", collapsed);
  const h3 = panel.querySelector(":scope > h3");
  if (h3) h3.setAttribute("aria-expanded", String(!collapsed));
}

function initPanelCollapse() {
  document.querySelectorAll(".panel").forEach(panel => {
    const h3 = panel.querySelector(":scope > h3");
    if (!h3 || h3.dataset.collapsible) return;
    h3.dataset.collapsible = "1";
    h3.dataset.act = "togglePanel";     // 复用全局 click 委托
    h3.dataset.keyact = "1";            // 复用全局 keydown（Enter/空格 → click）
    h3.tabIndex = 0;
    h3.setAttribute("role", "button");
    h3.classList.add("panel-head-toggle");
    const text = (h3.textContent || "").trim();
    let collapsed = false;
    try { collapsed = localStorage.getItem(_panelKey(panel, h3)) === "1"; } catch (e) { collapsed = false; }
    if (!collapsed && PANEL_DEFAULT_COLLAPSED.some(k => text.includes(k))) collapsed = true;
    _applyPanelCollapse(panel, collapsed);
  });
}

function togglePanel(el) {
  const panel = el.closest(".panel");
  if (!panel) return;
  const collapsed = !panel.classList.contains("collapsed");
  _applyPanelCollapse(panel, collapsed);
  try { localStorage.setItem(_panelKey(panel, el), collapsed ? "1" : "0"); } catch (e) {}
}

// ============ 个人工作台偏好（置顶 / 最近访问） ============
// 存 localStorage 而不是看板数据：这是**这台机器上这个人的浏览偏好**，不是项目事实。
// 落进 projects.json 的话，它会跟着交接包 / 全项目导出跑到别人机器上，
// 也会让 MCP 看到的项目对象平白多两个与项目本身无关的字段。
function _lsList(key) {
  try {
    const v = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(v) ? v : [];
  } catch (e) { return []; }
}
function pinnedIds() { return _lsList("kanban-pinned"); }
function isPinned(pid) { return pinnedIds().includes(pid); }
function togglePin(pid) {
  if (!pid) return;
  const cur = pinnedIds();
  const i = cur.indexOf(pid);
  if (i >= 0) cur.splice(i, 1); else cur.unshift(pid);
  try { localStorage.setItem("kanban-pinned", JSON.stringify(cur)); } catch (e) {}
  renderOverview();
  toast(i >= 0 ? "已取消置顶" : "已置顶，会排在总览最前面");
}
function recentIds() { return _lsList("kanban-recent"); }
function pushRecent(pid) {
  if (!pid) return;
  const cur = recentIds().filter(x => x !== pid);
  cur.unshift(pid);
  try { localStorage.setItem("kanban-recent", JSON.stringify(cur.slice(0, 8))); } catch (e) {}
}

// ============ 批量操作（多选项目） ============
// 选中态只活在内存里：它表达的是"现在要动这几个"的临时选择，刷新即该消失。
// 持久化反而危险 —— 下次打开时还选中着一批自己早忘了的项目，手一滑就批量删了。
let selectedPids = new Set();

function updateBulkBar() {
  const bar = document.getElementById("bulk-bar");
  if (!bar) return;
  // 项目可能已被删、或被当前筛选挡掉：选中的集合里只留"现在还看得见"的
  const visible = new Set();
  document.querySelectorAll("#grid .pcard").forEach(c => visible.add(c.dataset.pid));
  [...selectedPids].forEach(pid => { if (!visible.has(pid)) selectedPids.delete(pid); });
  const n = selectedPids.size;
  bar.style.display = n ? "flex" : "none";
  const cnt = document.getElementById("bulk-count");
  if (cnt) cnt.textContent = "已选 " + n + " 个";
}

function bulkClear() {
  selectedPids.clear();
  renderOverview();       // 重画一遍把复选框的勾去掉
  updateBulkBar();
}

async function bulkSetStatus(status) {
  const ids = [...selectedPids];
  if (!ids.length) return;
  let ok = 0, fail = 0;
  for (const pid of ids) {
    try {
      await api("/api/projects/" + pid, { method: "PATCH", body: JSON.stringify({ status }) });
      ok++;
    } catch (e) { fail++; }
  }
  toast(`已把 ${ok} 个项目设为「${status}」` + (fail ? `，${fail} 个失败` : ""));
  selectedPids.clear();
  await loadOverview();
  updateBulkBar();
}

async function bulkDelete() {
  const ids = [...selectedPids];
  if (!ids.length) return;
  const names = (projects || []).filter(p => ids.includes(p.id)).map(p => p.name);
  const preview = names.slice(0, 5).join("\n") + (names.length > 5 ? `\n…另 ${names.length - 5} 个` : "");
  if (!confirm(`确定删除这 ${ids.length} 个项目吗？\n\n${preview}\n\n删除前会自动拍一份快照，可在「数据恢复」里回滚。`)) return;
  try {
    const res = await api("/api/projects/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ ids, confirm: true }),
    });
    toast(`已删除 ${res.deleted.length} 个项目；快照：${res.backup}`);
  } catch (e) {
    toast("批量删除失败：" + e.message);
  }
  selectedPids.clear();
  await loadOverview();
  updateBulkBar();
}

// ============ AI 设置弹窗 Tab 切换 ============
// 「设置 / 用量」是两个 tabpanel：切换只有一条路，aria-selected 与 hidden 同进退，
// 免得出现"按钮亮在用量、页面还停在设置"的错位。打开弹窗默认回设置页（openAISettings 里调）。
function switchAITab(name) {
  const btn = document.getElementById("ai-tab-btn-" + name);
  if (!btn) return;
  document.querySelectorAll("#ai-settings-modal .ai-tab").forEach(b => b.classList.toggle("active", b === btn));
  document.querySelectorAll("#ai-settings-modal .ai-tab").forEach(b => b.setAttribute("aria-selected", String(b === btn)));
  const paneSettings = document.getElementById("ai-pane-settings");
  const paneUsage = document.getElementById("ai-pane-usage");
  paneSettings.hidden = name !== "settings";
  paneUsage.hidden = name !== "usage";
  // 首次进入用量页且还没加载过就补一次（打开设置时不急着拉，切进来再看）
  if (name === "usage" && !paneUsage.dataset.loaded) {
    paneUsage.dataset.loaded = "1";
    loadUsage();
  }
}

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
    if (activeViewId) {                               // 手动改搜索 = 脱离已保存视图
      activeViewId = "";
      syncViewSelect();
    }
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderOverview, 120);
  });
}

// ============ 保存的视图（Plane Views 轻量版） ============
// 保存的不是"当前画面"，而是`筛选状态 + 搜索词`——它俩是总览卡片的全部过滤维度。
// 打开总览时并行拉一次，保存/删除后本地刷新，不随每次 loadOverview 重新拉。
let savedViews = [];          // [{id, name, filter, q}]
let activeViewId = "";        // 当前应用中的视图 id（手动改 chip/搜索即清空）

async function loadViews() {
  try {
    const r = await api("/api/views");
    savedViews = r.views || [];
  } catch (e) { savedViews = []; }
  syncViewSelect();
}

function syncViewSelect() {
  const sel = document.getElementById("view-sel");
  if (!sel) return;
  sel.innerHTML = '<option value="">视图</option>'
    + savedViews.map(v => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join("");
  sel.value = activeViewId;
}

function toggleViewForm() {
  const f = document.getElementById("view-form");
  const show = f.style.display === "none";
  f.style.display = show ? "flex" : "none";
  if (show) document.getElementById("view-name").focus();
}

function applyView(vid) {
  const v = savedViews.find(x => x.id === vid);
  activeViewId = vid || "";
  if (!v) return;
  filter = v.filter || "全部";
  document.querySelectorAll(".filter-bar .chip").forEach(c =>
    c.classList.toggle("active", c.dataset.f === filter));
  const box = document.getElementById("search");
  if (box) box.value = v.q || "";
  renderOverview();
}

async function saveView() {
  const inp = document.getElementById("view-name");
  const name = (inp.value || "").trim();
  if (!name) { toast("先给视图起个名字"); inp.focus(); return; }
  const q = (document.getElementById("search").value || "").trim();
  try {
    const r = await api("/api/views", { method: "POST", body: JSON.stringify({ name, filter, q }) });
    activeViewId = r.id;
    inp.value = "";
    document.getElementById("view-form").style.display = "none";
    await loadViews();
    toast(r.updated ? `视图「${name}」已更新 ✓` : `视图「${name}」已保存 ✓`);
  } catch (e) { toast("保存视图失败：" + e.message); }
}

async function deleteView() {
  const vid = document.getElementById("view-sel").value;
  if (!vid) { toast("先在视图下拉里选中要删除的视图"); return; }
  const v = savedViews.find(x => x.id === vid);
  if (!v) return;
  if (!confirm(`删除视图「${v.name}」？这只影响筛选菜单，不会动任何项目数据。`)) return;
  try {
    await api("/api/views/" + vid, { method: "DELETE" });
    activeViewId = "";
    document.getElementById("view-form").style.display = "none";
    await loadViews();
    toast("视图已删除 ✓");
  } catch (e) { toast("删除视图失败：" + e.message); }
}

(function bindViewSelect() {
  const sel = document.getElementById("view-sel");
  if (sel) sel.addEventListener("change", () => applyView(sel.value));
})();

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
  pushRecent(pid);
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
  renderMilestones();
  renderTimeline();
  const tasks = sortTasks(p.tasks || [], taskSortMode);
  document.getElementById("d-task-total").textContent = "共 " + tasks.length + " 个任务";
  const cols = { "待办": "col-todo", "进行中": "col-doing", "待审查": "col-review", "已完成": "col-done", "阻塞": "col-blocked" };
  const counts = { "待办": 0, "进行中": 0, "待审查": 0, "已完成": 0, "阻塞": 0 };
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
    const accCnt = (t.acceptance && t.acceptance.length) || 0;
    const accHtml = accCnt ? `<span class="acc-chip" title="验收标准 ${accCnt} 条（编辑任务可查看）">☑ ${accCnt}</span>` : "";
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
    const reviewHtml = (t.status === "待审查" && current && current.repo_path)
      ? `<span class="review-chip" title="验收时去详情页「代码现实」对照最近提交">🛂 待审查</span>` : "";
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
        ${accHtml}
        ${reviewHtml ? `<span style="margin-left:auto">${reviewHtml}</span>` : idleHtml ? `<span style="margin-left:auto">${idleHtml}</span>` : ""}
      </div>`;
    document.getElementById(cols[tst] || "col-todo").appendChild(el);
  });
  for (const [k, v] of Object.entries(counts)) {
    document.getElementById("c-" + ({ "待办": "todo", "进行中": "doing", "待审查": "review", "已完成": "done", "阻塞": "blocked" }[k])).textContent = v;
  }
renderReality();
  renderGraph();
  renderStats();
}

