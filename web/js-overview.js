/* 总览视图：项目卡片的加载与渲染 / 筛选 / 保存的视图 / 置顶与最近访问 / 批量操作。*/
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
    if (document.getElementById("view-overview").style.display !== "none") {
      renderOverview();
      loadRepoWatchOverview();
      ensureRepoWatchOverviewPolling();
    }
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

// 空结果页的「查看全部」：同时清掉状态筛选、搜索词与已保存视图，
// 让用户不必先找到「全部」chip、再回头清搜索框（两条路都要手动走一遍）。
function resetOverview() {
  filter = "全部";
  activeViewId = "";
  if (searchInput) searchInput.value = "";
  updateSearchClear();
  document.querySelectorAll(".chip").forEach(c =>
    c.classList.toggle("active", c.dataset.f === "全部"));
  syncViewSelect();
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
  const listHead = document.getElementById("list-head");
  if (listHead) listHead.style.display = projects.length ? "" : "none";
  const msg = document.getElementById("empty-msg");
  const onboard = document.getElementById("empty-onboard");
  const emptyAction = document.getElementById("empty-action");
  if (list.length) {
    msg.textContent = "";
    if (onboard) onboard.hidden = true;
    if (emptyAction) emptyAction.hidden = true;
  } else if (!projects.length) {
    msg.textContent = "还没有项目。先建一个，把交接说明和下一步写进去；换智能体时一键复制即可。";
    if (onboard) onboard.hidden = false;
    if (emptyAction) emptyAction.hidden = true;
  } else if (q) {
    msg.textContent = `没有匹配「${q}」的项目（共 ${projects.length} 个）—— 换个词，或清空搜索框`;
    if (onboard) onboard.hidden = true;
    if (emptyAction) { emptyAction.textContent = "清空搜索，查看全部"; emptyAction.hidden = false; }
  } else {
    msg.textContent = `「${filter}」状态下没有项目（共 ${projects.length} 个）—— 点「全部」看所有项目`;
    if (onboard) onboard.hidden = true;
    if (emptyAction) { emptyAction.textContent = "查看全部项目"; emptyAction.hidden = false; }
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

// 搜索框防抖：卡片是全量重渲染，逐字符触发在项目多时会卡
let searchTimer = null;
const searchInput = document.getElementById("search");

// 清空按钮只在输入框有内容时出现：省得用户为了退出一段搜索去全选删除
function updateSearchClear() {
  const btn = document.getElementById("search-clear");
  if (btn) btn.hidden = !(searchInput && searchInput.value);
}

function clearSearch() {
  if (!searchInput) return;
  searchInput.value = "";
  if (activeViewId) {
    activeViewId = "";
    syncViewSelect();
  }
  updateSearchClear();
  renderOverview();
  searchInput.focus();
}

if (searchInput) {
  searchInput.addEventListener("input", () => {
    if (activeViewId) {                               // 手动改搜索 = 脱离已保存视图
      activeViewId = "";
      syncViewSelect();
    }
    updateSearchClear();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderOverview, 120);
  });
  // 搜索框里按 Esc = 退出这次搜索（标准交互）；stopPropagation 避免同时触发全局 Esc 关弹窗
  searchInput.addEventListener("keydown", e => {
    if (e.key === "Escape" && searchInput.value) {
      e.preventDefault();
      e.stopPropagation();
      clearSearch();
    }
  });
  updateSearchClear();
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
  updateSearchClear();
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



// ============ 总览仓库变化标记 ============
let _repoOverviewTimer = null;
let _repoOverviewLoading = false;

function renderRepoFlags(changes) {
  const changed = (changes || []).filter(e => e && e.changed);
  document.querySelectorAll("#grid .pcard").forEach(card => card.classList.remove("has-repo-change"));
  document.querySelectorAll("#grid .repo-flag").forEach(x => x.remove());
  changed.forEach(e => {
    const card = document.querySelector('#grid .pcard[data-pid="' + String(e.project_id).replace(/"/g, '\\"') + '"]');
    if (!card) return;
    card.classList.add("has-repo-change");
    const sub = card.querySelector(".card-sub");
    if (sub) {
      const flag = document.createElement("span");
      flag.className = "repo-flag";
      flag.textContent = "代码变化";
      flag.title = e.reason || "仓库有新的提交或未提交文件";
      sub.appendChild(flag);
    }
  });
  const note = document.getElementById("repo-change-note");
  if (note) note.textContent = changed.length
    ? `按最近更新排序 · ${changed.length} 个代码有变化`
    : "按最近更新排序";
}

async function loadRepoWatchOverview() {
  if (_repoOverviewLoading) return;
  _repoOverviewLoading = true;
  try {
    const r = await api("/api/repo-watch");
    renderRepoFlags(r.changes || []);
  } catch (e) {
    // 雷达失败不影响总览；下一次轮询/刷新再试。
  } finally {
    _repoOverviewLoading = false;
  }
}

function ensureRepoWatchOverviewPolling() {
  if (_repoOverviewTimer) return;
  _repoOverviewTimer = setInterval(() => {
    if (document.hidden) return;                 // 窗口最小化/隐藏时不打全板 git 巡检
    if (document.getElementById("view-overview").style.display !== "none") loadRepoWatchOverview();
  }, 30000);
}
