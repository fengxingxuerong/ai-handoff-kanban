/* 详情视图：打开与重绘 / 面板折叠 / 刷新时机 / 列内排序。*/
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
    startRepoWatch(pid);
  } catch (e) {
    setDetailState("error", e.message, pid);   // 停在原地让人能重试，而不是闪一条 toast
  }
}

function showOverview() {
  stopRepoWatch();
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
    // 阻塞原因放正文（不只藏在编辑弹窗里）：它是"这条为什么停着"的答案，
    // 也是用户决定"去解决还是放弃"的材料（第六轮吸取：阻塞必带原因）。
    const blockHtml = (t.status === "阻塞" && t.blocked_reason)
      ? `<div class="td blocked-reason" title="${esc(t.blocked_reason)}">⛔ ${esc(t.blocked_reason)}</div>` : "";
    el.innerHTML = `
      <div class="tt">${esc(t.title)}${dueHtml}</div>
      ${t.desc ? `<div class="td">${esc(t.desc)}</div>` : ""}
      ${blockHtml}
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



// ============ 仓库变化雷达 ============
// 只在详情页停留时轮询当前仓库；检测到变化才提示，不自动写 progress。
let _repoWatchTimer = null;
let _repoWatchEvent = null;

function stopRepoWatch() {
  if (_repoWatchTimer) {
    clearInterval(_repoWatchTimer);
    _repoWatchTimer = null;
  }
  _repoWatchEvent = null;
  const el = document.getElementById("repo-watch");
  if (el) { el.hidden = true; el.innerHTML = ""; }
}

function _repoWatchHtml(e) {
  const head = (e.head && e.head.hash) ? e.head.hash : "—";
  const dirty = Number(e.dirty_count || 0);
  const files = (e.dirty_files || []).slice(0, 4).map(esc).join("、");
  const suggestion = (e.suggested_progress !== null && e.suggested_progress !== undefined)
    ? `建议进度 ${e.current_progress}% → ${e.suggested_progress}%`
    : "代码已变化，请确认下一步";
  return `
    <div class="rw-mark"></div>
    <div class="rw-body">
      <div class="rw-title">代码有变化 <span class="rw-head">${esc(head)}</span></div>
      <div class="rw-meta">${dirty ? `${dirty} 个未提交文件${files ? "：" + files : ""}` : "有新提交"} · ${esc(suggestion)}</div>
      ${e.reason ? `<div class="rw-reason">${esc(e.reason)}</div>` : ""}
    </div>
    <div class="rw-actions">
      <button class="btn primary small" data-act="repoAck">${e.suggested_progress !== null && e.suggested_progress !== undefined ? "确认并同步" : "标记已读"}</button>
      <button class="btn small" data-act="repoLater">稍后</button>
    </div>`;
}

async function loadRepoWatch(pid) {
  const el = document.getElementById("repo-watch");
  if (!el) return;
  try {
    const r = await api("/api/repo-watch?pid=" + encodeURIComponent(pid));
    const e = (r.changes || [])[0];
    _repoWatchEvent = e || null;
    if (!e || !e.changed) { el.hidden = true; el.innerHTML = ""; return; }
    el.innerHTML = _repoWatchHtml(e);
    el.hidden = false;
  } catch (e) {
    // 仓库不可读/网络失败时不要把详情页搞成错误页；下次轮询再试。
    el.hidden = true;
  }
}

function startRepoWatch(pid) {
  stopRepoWatch();
  if (!pid) return;
  loadRepoWatch(pid);
  _repoWatchTimer = setInterval(() => {
    if (document.hidden) return;                 // 窗口不可见时不跑 git 扫描
    if (document.getElementById("view-detail").classList.contains("active") && currentPid === pid) {
      loadRepoWatch(pid);
    }
  }, 15000);
}

async function ackRepoWatch() {
  if (!_repoWatchEvent || !currentPid) return;
  const progress = _repoWatchEvent.suggested_progress;
  try {
    const r = await api("/api/projects/" + encodeURIComponent(currentPid) + "/repo-ack", {
      method: "POST",
      body: JSON.stringify({ progress: progress == null ? null : progress, note: "代码变化雷达" }),
    });
    toast(progress == null ? "已标记代码变化" : `已同步进度：${r.before}% → ${r.after}%`);
    stopRepoWatch();
    await openDetail(currentPid);
  } catch (e) {
    toast("同步失败：" + e.message);
  }
}

function dismissRepoWatch() {
  const el = document.getElementById("repo-watch");
  if (el) { el.hidden = true; el.innerHTML = ""; }
  stopRepoWatch();
  toast("已稍后处理；下次打开详情会再提醒");
}
