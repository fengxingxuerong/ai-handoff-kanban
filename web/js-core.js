/* 基础设施层：全局状态 / 通用工具 / 弹窗 a11y / 事件委托总表与分发 / 顶栏菜单。
 *
 * 分层（2026-10-18 起，10 个文件；无构建、无模块系统，靠 index.html 的顺序加载）：
 *   js-core      基础设施与分发（本文件）
 *   js-overview  总览：卡片列表 / 筛选 / 保存的视图 / 置顶与最近 / 批量操作
 *   js-detail    详情视图：打开与重绘 / 面板折叠 / 列内排序
 *   js-render    渲染工具：截止徽章与拖拽 / Markdown / 就绪度 / 时间轴 / 依赖图 / 统计 / 里程碑 / 图表
 *   js-reality   代码现实：git 快照与「看板说的 vs 代码里真发生的」结论
 *   js-handoff   交接物：趋势图 / 完整交接包 / AGENTS.md / 多智能体入口文件
 *   js-edit      表单编辑：项目 / 任务 / 里程碑 / 标签 / 自动分配
 *   js-ai        AI：设置与端点 / 监管报告 / 自然语言指令 / 交接说明生成 / 会话提炼
 *   js-data      数据：导入导出 / 备份恢复 / 全局搜索与命令面板 / 主题
 *   js-boot      启动序列与零散视图：今日 / 拍板 / 记忆 / 过程日志 / 会话捕获 / 提醒 / 用量
 *
 * 拆的是"关注点"，不是"字节数"：改一处行为只该动一个文件。
 * 全部函数仍是全局函数（无 import/export），跨文件调用发生在事件回调里，与加载顺序无关。
 */
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

// 长弹窗的滚动容器是 .mbody（弹窗框定高、内容在框内滚，见 style.css 的 .modal）。
// 滚动记忆记的必须是它：以前记的是遮罩的 scrollTop，而遮罩只在旧布局下才滚。
function modalScroller(el) {
  return el.querySelector(".mbody") || el;
}

// 关闭只留这一条路径：右上角 ✕、底部「完成」、点遮罩、按 Esc 都走它。
// 以前遮罩与 Esc 各自直接摘 .show，跳过了上面那个动画复位（于是弹窗只有第一次
// 打开有 pop 动画），Esc 也既不关右侧的 AI 监管面板、也不清已解析出的待确认操作。
function closeOneModal(id) {
  const m = document.getElementById(id);
  modalScroll.set(id, modalScroller(m).scrollTop);   // 长弹窗重开时回到上次看到的位置
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
  modalScroller(m).scrollTop = modalScroll.get(id) || 0;
  focusInto(m);
}

document.querySelectorAll(".modal-mask").forEach(m => {
  m.addEventListener("click", e => { if (e.target === m) closeOneModal(m.id); });
});

// ============ 事件委托总表 ============
// 全部按钮/控件通过 data-act（可选 data-arg）声明行为，由此处统一分发：
// 页面里不再有内联 onclick，既避免拼串注入，也便于一眼看清所有交互入口。
const ACTIONS = {
  // —— 顶部与总览 ——
  openProjectModal: () => openProjectModal(),
  clearSearch: () => clearSearch(),
  resetOverview: () => resetOverview(),
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
  repoAck: () => ackRepoWatch(),
  repoLater: () => dismissRepoWatch(),
  // —— 弹窗通用 ——
  closeModal: arg => closeOneModal(arg),
  closeReport: () => closeReport(),
  // —— 表单保存 ——
  saveProject: () => saveProject(),
  saveTask: () => saveTask(),
  addMilestoneRow: () => addMilestoneRow(),
  removeMilestoneRow: (arg, ev, el) => removeMilestoneRow(el),
  deleteTask: () => deleteTask(),
  copyTaskId: () => copyTaskId(),
  saveBlockedReason: () => saveBlockedReason(),
  skipBlockedReason: () => skipBlockedReason(),
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
  exportRepoSnapshot: (arg, ev, el) => exportRepoSnapshot({ currentTarget: el }),
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

// 详情页「更多」菜单是个原生 <details>：点开只有一条路，收起也只有一条路
// （再点一次它自己）。于是点别处、按 Esc、选完菜单项它都挂在那儿 ——
// 实测最别扭的一幕是：点「🗑 删除」走完确认门、项目都没了，那个菜单还开着。
// 这里补齐另外三条收起路径，与顶栏那套（.hmenu）保持同一手感。
function closeDetailMenu(restoreFocus) {
  const open = document.querySelector("details.d-more[open]");
  if (!open) return false;
  open.open = false;
  if (restoreFocus) {
    const summary = open.querySelector("summary");
    if (summary) summary.focus({ preventScroll: true });
  }
  return true;
}

document.addEventListener("click", e => {
  // 点到菜单外面就收起
  if (!e.target.closest(".hmenu-wrap")) closeHeaderMenus();
  // 点标题本身不算"外面"，否则原生展开/收起会跟这里打架
  if (!e.target.closest(".d-more")) closeDetailMenu();
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
  // 同上：详情「更多」选完也要收 —— **但多步动作除外**：
  // 按钮可以在 runAction 里给自己打 `data-keep-menu="1"`（比如"先预览、再确认"那种），
  // 否则第一下之后菜单就收了，第二步的按钮在 DOM 里可见、实际点不到（2026-10-26 实测）。
  if (e.target.closest(".d-more-pop") && !el.dataset.keepMenu) closeDetailMenu();
});

document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    closeHeaderMenus();
    // 弹窗/监管面板开着时 Esc 归它们（js-edit 里的 closeTopModal）；
    // 没有上层时才收详情菜单，并把它上面的焦点还给触发它的标题 —— 键盘用户不然就掉回 body。
    if (!topLayer()) closeDetailMenu(true);
  }
});

