/* 机器可读上下文 / 记忆 / 过程日志 / 会话捕获 / 逾期提醒 / 用量统计 / 启动
 *
 * 2026-09-28 从 `web/index.html` 的 <script> 块按**原顺序**整段切出，内容逐字未改。
 * ⚠️ 加载顺序是契约：index.html 里这几个 <script> 的顺序不能调换 ——
 *    常量与函数在各自文件顶层执行，换序会撞 TDZ（ReferenceError）。
 */
// ============ 机器可读上下文 ============
async function copyContextJson() {
  if (!currentPid) return;
  try {
    const ctx = await api("/api/context/" + currentPid);
    const text = JSON.stringify(ctx, null, 2);
    try { await navigator.clipboard.writeText(text); toast("机器可读上下文已复制 ✓ 直接粘给智能体即可接手"); }
    catch (e) { fallbackCopy(text); }
  } catch (e) { toast("获取失败：" + e.message); }
}

async function openToday() {
  openModal("today-modal");
  const box = document.getElementById("today-body");
  box.innerHTML = "加载中…";
  try {
    const r = await api("/api/today?limit=50");
    if (!r.tasks.length) {
      box.innerHTML = '<div class="hint">暂时没有待办：任务要么已完成，要么还在等前置完成。</div>';
      return;
    }
    box.innerHTML = r.tasks.map(t => `
      <div class="tcard" tabindex="0" role="button" data-keyact="1" data-act="gotoTask" data-arg="${esc(t.project_id)}" style="margin-bottom:8px;cursor:pointer">
        <div class="tt">${esc(t.title)}${t.due_date ? ` <span class="due-badge">📅 ${esc(t.due_date)}</span>` : ""}</div>
        <div class="td">${esc(t.project_name)} · ${esc(t.status)}${(t.refs && t.refs.length) ? " · 📍 " + t.refs.map(esc).join("、") : ""}</div>
        ${t.status === "阻塞" && t.blocked_reason ? `<div class="td blocked-reason" title="${esc(t.blocked_reason)}">⛔ ${esc(t.blocked_reason)}</div>` : ""}
        <div class="tfoot"><span class="tag2 pri-${priCls(t.priority)}">${esc(priCls(t.priority))}</span>${t.status === "阻塞" ? '<span class="agent">阻塞</span>' : ""}</div>
      </div>`).join("")
      + (r.truncated ? `<div class="hint">这里只有前 ${r.tasks.length} 条，共 ${r.total} 条可以动手 —— 剩下的请到各项目详情里看。</div>` : "");
  } catch (e) { box.innerHTML = "加载失败：" + esc(e.message); }
}

function gotoTask(pid) {
  closeModal("today-modal");
  closeModal("inbox-modal");
  if (pid) openDetail(pid);
}

// ============ 待拍板（Inbox：只列等人的——逾期 / 待审查 / 阻塞） ============
const INBOX_KINDS = {
  overdue: { label: "📅 已逾期", cls: "k-overdue", hint: "过了截止日还没完成：改期、推进还是砍掉，得有人定" },
  review:  { label: "🛂 待验收", cls: "k-review", hint: "干完了等人验收：通过就改已完成，不通过写清哪里不行" },
  blocked: { label: "⛔ 阻塞",   cls: "k-blocked", hint: "被障碍卡住：解决障碍，或者放弃并记录原因" },
};

function updateInboxBadge(counts) {
  const badge = document.getElementById("inbox-badge");
  if (!badge) return;
  const n = counts ? (counts.overdue || 0) + (counts.review || 0) + (counts.blocked || 0) : 0;
  badge.textContent = String(n);
  badge.style.display = n ? "inline-block" : "none";
}

async function openInbox() {
  openModal("inbox-modal");
  const box = document.getElementById("inbox-body");
  box.innerHTML = "加载中…";
  try {
    const r = await api("/api/inbox");
    updateInboxBadge(r.counts);
    if (!r.items.length) {
      box.innerHTML = '<div class="hint">没有需要你拍板的事：没有逾期、没有等验收的、也没有被阻塞的。'
        + "依赖还没满足的任务在「☀️ 今日」里。</div>";
      return;
    }
    const parts = ["review", "blocked", "overdue"].filter(k => r.counts[k]).map(k => {
      const meta = INBOX_KINDS[k];
      const rows = r.items.filter(it => it.kind === k).map(t => `
        <div class="tcard" tabindex="0" role="button" data-keyact="1" data-act="gotoTask" data-arg="${esc(t.project_id)}" style="margin-bottom:8px;cursor:pointer">
          <div class="tt">${esc(t.title)}${t.due_date ? ` <span class="due-badge">📅 ${esc(t.due_date)}</span>` : ""}</div>
          <div class="td">${esc(t.project_name)}</div>
          ${t.blocked_reason ? `<div class="td blocked-reason" title="${esc(t.blocked_reason)}">⛔ ${esc(t.blocked_reason)}</div>` : ""}
          <div class="tfoot"><span class="tag2 pri-${priCls(t.priority)}">${esc(priCls(t.priority))}</span></div>
        </div>`).join("");
      return `<div class="inbox-group ${meta.cls}">
        <div class="inbox-ghead">${meta.label} · ${r.counts[k]} 条 <span style="font-weight:400;color:var(--text-faint)">${meta.hint}</span></div>
        ${rows}</div>`;
    }).join("");
    box.innerHTML = parts;
  } catch (e) { box.innerHTML = "加载失败：" + esc(e.message); }
}

(function bindTaskSort() {
  const sel = document.getElementById("task-sort");
  if (!sel) return;
  sel.value = taskSortMode;
  sel.addEventListener("change", () => {
    taskSortMode = sel.value;
    try { localStorage.setItem("kanban-task-sort", taskSortMode); } catch (e) {}
    if (current) renderDetail();
  });
})();

async function downloadHandoffPage() {
  if (!currentPid) return;
  try {
    const res = await fetch(`/api/export/page/${currentPid}`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const html = await res.text();
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = (current && current.name ? current.name : "项目") + "_接手页_"
      + new Date().toISOString().slice(0, 10) + ".html";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(a.href);
    toast("接手页已下载 ✓ 双击即可打开（离线可用）");
  } catch (e) { toast("接手页生成失败：" + e.message); }
}

// ============ 项目记忆 ============
async function renderMemories(pid) {
  const box = document.getElementById("memory-body");
  if (!box) return;
  try {
    const r = await api("/api/memories?pid=" + encodeURIComponent(pid));
    const items = r.memories || [];
    box.innerHTML = items.length
      ? items.map(m => {
          // 条目级尺寸（服务端 text_size 给的 chars / est_tokens）：这一条读进模型上下文要占多少。
          // 界面上只显示字数（够用且不吵），token 估算放在 title 里 —— 它是近似值（CJK 1 字≈1 token），
          // 别让它看起来像个精确读数。缺字段时退回本地长度，宁可不显示也不渲染 "≈undefined 字"。
          const chars = typeof m.chars === "number" ? m.chars : (m.text || "").length;
          const tok = typeof m.est_tokens === "number" ? m.est_tokens : 0;
          const sizeTip = "读进上下文前先看这一条值不值：约 " + chars + " 字"
            + (tok ? " / 约 " + tok + " tok（中文按 1 字≈1 token 的近似估算，不作计费口径）" : "");
          return `
        <div class="memory-item${m.expired ? " expired" : ""}">
          <span class="m-type">${esc(m.type || "事实")}</span>
          <span class="m-text">${esc(m.text)}</span>
          <span class="m-size" title="${esc(sizeTip)}">≈${esc(String(chars))} 字</span>
          ${m.expired ? `<span class="m-expired" title="有效期到 ${esc(m.expires || "")}，已过期：不再进交接包/AGENTS.md，删除请点 ✕">⚠️ 已过期</span>` : ""}
          <span class="m-time" title="来源：${esc(m.source || "手动")}">${esc(m.source || "手动")} · ${esc((m.time || "").slice(5, 10))}</span>
          <button class="m-del" title="删除" data-act="deleteMemory" data-mid="${esc(m.id)}">✕</button>
        </div>`;
        }).join("")
      : '<div style="font-size:12px;color:var(--text-faint)">暂无记忆。会话提炼会自动沉淀决策/坑/约定，也可手动添加。</div>';
  } catch (e) { box.innerHTML = '<div style="font-size:12px;color:var(--red)">加载失败</div>'; }
}

function toggleMemoryForm() {
  const f = document.getElementById("memory-form");
  const open = f.style.display === "flex";
  f.style.display = open ? "none" : "flex";
  if (!open) document.getElementById("memory-text").focus();
}

// 以前是连着两个 prompt()（先问内容再问类型）：全站唯一的非弹窗录入，
// 输入框不能编辑、类型打错一个字就静默归成"事实"。改成与过程日志同一种内联表单。
async function saveMemory() {
  if (!currentPid) return;
  const input = document.getElementById("memory-text");
  const text = (input.value || "").trim();
  if (!text) { toast("先写一条内容"); input.focus(); return; }
  const type = document.getElementById("memory-type").value;
  const expires = document.getElementById("memory-expires").value || "";
  try {
    const r = await api("/api/memories", { method: "POST", body: JSON.stringify({
      pid: currentPid, type, text, source: "手动", expires }) });
    input.value = "";
    document.getElementById("memory-expires").value = "";
    toast(r.deduped ? "这条记忆已经有了 ✓" : (expires ? `记忆已记下 ✓ 有效期到 ${expires}` : "记忆已记下 ✓"));
    renderMemories(currentPid);
  } catch (e) { toast("添加失败：" + e.message); }
}

async function deleteMemory(mid) {
  if (!mid || !confirm("删除这条记忆？")) return;
  try {
    await api("/api/memories/" + mid, { method: "DELETE" });
    renderMemories(currentPid);
  } catch (e) { toast("删除失败：" + e.message); }
}

// ============ 任务过程日志 ============
async function appendJournal() {
  const input = document.getElementById("t-journal-input");
  const text = (input.value || "").trim();
  if (!text) { toast("先输入记录内容"); return; }
  if (!editTaskId) { toast("请先保存任务后再追加日志"); return; }
  try {
    await api("/api/tasks/" + editTaskId + "/journal", { method: "POST", body: JSON.stringify({ text }) });
    input.value = "";
    const t = (current.tasks || []).find(x => x.id === editTaskId);
    (t.journal = t.journal || []).push({ time: new Date().toLocaleString("sv-SE").slice(0, 16), text });
    renderJournal(t.journal);
    toast("已记录 ✓");
  } catch (e) { toast("记录失败：" + e.message); }
}

function renderJournal(journal) {
  const list = document.getElementById("t-journal-list");
  const items = journal || [];
  list.innerHTML = items.length
    ? items.map(e => e.folded
        // 折叠摘要的 time 是首尾日期区间（2026-09-01~2026-09-20），不是普通条目的
        // "YYYY-MM-DD HH:MM" —— 按 slice(5,16) 截会渲染成 [09-01~2026-] 这样的乱码
        ? `<div class="j-row j-folded" title="更早的 ${e.folded} 条过程记录已折叠成这条摘要"><b>[${esc(e.time || "")}]</b>${esc(e.text)}</div>`
        : `<div class="j-row"><b>[${esc((e.time || "").slice(5, 16))}]</b>${esc(e.text)}</div>`).join("")
    : '<div class="j-row">暂无记录</div>';
}

// ============ 会话捕获 ============
let capturedSession = null;

async function openSessionCapture() {
  capturedSession = null;
  document.getElementById("session-list").innerHTML = '<div style="font-size:12px;color:var(--text-faint);padding:4px">点「扫描」发现本机 AI 工具的最近会话</div>';
  document.getElementById("session-ingest-row").style.display = "none";
  document.getElementById("session-preview-row").style.display = "none";
  document.getElementById("session-result").style.display = "none";
  document.getElementById("session-ingest-btn").style.display = "none";
  document.getElementById("session-apply-btn").style.display = "none";
  openModal("session-modal");
  scanSessions();
}

async function scanSessions() {
  const list = document.getElementById("session-list");
  const btn = document.getElementById("scan-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/sessions/scan");
    const items = r.sessions || [];
    if (!items.length) {
      list.innerHTML = '<div style="font-size:12px;color:var(--text-faint);padding:4px">没有发现本地会话文件（未安装 Claude Code / Cline，或目录不存在）。自定义会话源可在配置文件 config.json 的 ai.session_sources 里添加。</div>';
      return;
    }
    list.innerHTML = items.map((s, i) => `
      <div class="sess-item" tabindex="0" role="button" data-keyact="1" data-i="${i}">
        <span class="s-src">${esc(s.source)}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(s.path)}">${esc(s.path.split(/[\\\\/]/).slice(-2).join("/"))}</span>
        <span style="color:var(--text-faint);flex-shrink:0">${esc(s.mtime)} · ${s.size_kb}KB</span>
      </div>`).join("");
    list.querySelectorAll(".sess-item").forEach(el => {
      el.addEventListener("click", () => {
        list.querySelectorAll(".sess-item").forEach(x => x.style.borderColor = "");
        el.style.borderColor = "var(--accent)";
        capturedSession = items[Number(el.dataset.i)];
        loadSessionPreviewAndPrepare();
      });
    });
  } catch (e) { list.innerHTML = '<div style="color:var(--red);font-size:12px">扫描失败：' + esc(e.message) + "</div>"; }
  finally { btn.classList.remove("ai-loading-btn"); }   // 空结果那条分支以前直接 return，按钮永久转圈
}

async function loadSessionPreviewAndPrepare() {
  if (!capturedSession) return;
  document.getElementById("session-ingest-row").style.display = "block";
  document.getElementById("session-preview-row").style.display = "block";
  document.getElementById("session-result").style.display = "none";
  document.getElementById("session-ingest-btn").style.display = "block";
  document.getElementById("session-apply-btn").style.display = "none";
  // 项目下拉：默认当前项目（若在详情页）
  const sel = document.getElementById("session-pid");
  const data = await api("/api/projects");
  sel.innerHTML = (data.projects || []).map(pp => `<option value="${esc(pp.id)}">${esc(pp.name)}</option>`).join("");
  if (currentPid) sel.value = currentPid;
  try {
    const pv = await api("/api/sessions/preview?path=" + encodeURIComponent(capturedSession.path));
    document.getElementById("session-preview").value = pv.text;
  } catch (e) {
    document.getElementById("session-preview").value = "预览失败：" + e.message;
  }
}

async function ingestSession() {
  if (!capturedSession) return;
  const pid = document.getElementById("session-pid").value;
  if (!pid) { toast("请选择入库项目"); return; }
  // 这里以前写 `currentPid = pid`：在 A 的详情页把会话入库到 B，之后屏幕仍是 A，
  // 但"＋ 新建任务"、拖卡片、刷新全都打在 B 上 —— 一次选择把全局改掉了。
  summarizeTargetPid = pid;
  const btn = document.getElementById("session-ingest-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/sessions/ingest", { method: "POST", body: JSON.stringify({
      pid, path: capturedSession.path }) });
    summarizeData = r.parsed;
    summarizeBatchRef = r.batch_ref || "";
    const box = document.getElementById("session-result");
    box.style.display = "block";
    const d = r.parsed;
    const tasks = (d.new_tasks || []).map(t =>
      `<div class="task-row"><span class="tag2 pri-${priCls(t.priority)}">${esc(priCls(t.priority))}</span><span>${esc(t.title)}</span></div>`).join("");
    box.innerHTML = `<div class="summ-box">
      <div class="sec"><span class="lab">📌 总结</span><div class="body">${esc(d.summary || "（无）")}</div></div>
      ${tasks ? `<div class="sec"><span class="lab">➕ 建议新增任务（${(d.new_tasks || []).length} 个）</span><div class="tasks">${tasks}</div></div>` : ""}
      ${d.next_action ? `<div class="sec"><span class="lab">🎯 建议下一步</span><div class="body">${esc(d.next_action)}</div></div>` : ""}
      ${d.handoff_note_updates ? `<div class="sec"><span class="lab">🤝 建议补充进交接说明</span><div class="body">${esc(d.handoff_note_updates)}</div></div>` : ""}
    </div>`;
    document.getElementById("session-apply-btn").style.display =
      ((d.new_tasks || []).length || d.next_action || d.handoff_note_updates) ? "block" : "none";
    if (!tasks && !d.next_action) toast("这段会话没有提炼出可入库的内容");
  } catch (e) { toast("提炼失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

// 会话捕获与粘贴提炼共用 applySummarize：入库目标与幂等键都挂在 summarizeTargetPid
// / summarizeBatchRef 上（不是 currentPid），两个弹窗各自关各自的那一个。
// ============ 逾期/临期提醒 ============
function setBanner(visible) {
  const banner = document.getElementById("remind-banner");
  banner.style.display = visible ? "flex" : "none";
  // 两个视图的高度都按视口算（calc(100vh - ...)），横幅在正常流里占多高就得从
  // 可用高度里扣掉多少，否则横幅出现时页面多出第二条滚动条、看板底部被裁。
  // 10 是横幅自己的 margin-top。必须在 display 改完之后再量，否则 offsetHeight 是 0。
  document.documentElement.style.setProperty("--banner-h", visible ? (banner.offsetHeight + 10) + "px" : "0px");
}

function checkReminders(stats) {
  if (!stats) return;
  const overdue = stats.overdue_tasks || 0;
  const soon = stats.duesoon_tasks || 0;
  if (!overdue && !soon) { setBanner(false); return; }
  let today;
  try { today = new Date().toISOString().slice(0, 10); } catch (e) { return; }
  try {
    if (localStorage.getItem("kanban-remind-date") === today) { setBanner(false); return; }
  } catch (e) {}
  const banner = document.getElementById("remind-banner");
  banner.innerHTML = `
    <span>⏰ 提醒：${overdue ? `<b>${overdue} 个任务已逾期</b>` : ""}${overdue && soon ? "，" : ""}${soon ? `<b>${soon} 个任务 2 天内到期</b>` : ""}。建议尽快处理或调整截止日期。</span>
    <button class="btn small" data-act="dismissReminder">知道了</button>`;
  setBanner(true);
}

function dismissReminder() {
  setBanner(false);
  try { localStorage.setItem("kanban-remind-date", new Date().toISOString().slice(0, 10)); } catch (e) {}
}

// ============ 用量统计 ============
// 渲染按日折线（原生 SVG）、端点/模型分布与预算水位。数据来自 /api/usage 聚合：
// totals / by_date(升序) / by_endpoint(调用降序) / by_model / budget。
let lastBudgetLevel = "";
async function loadUsage() {
  const box = document.getElementById("usage-body");
  if (!box) return;
  box.innerHTML = "加载中…";
  try {
    const u = await api("/api/usage");
    const t = u.totals || {};
    const days = u.by_date || [];
    const eps = u.by_endpoint || [];
    const models = Object.entries(u.by_model || {}).sort((a, b) => b[1].calls - a[1].calls);
    const budget = u.budget || null;

    // —— 预算告警：80% 黄 / 100% 红；级别与上次不同才 toast，避免反复打扰 ——
    let budgetHtml = "";
    if (budget && budget.limit > 0) {
      const pct = budget.pct || 0;
      const level = pct >= 100 ? "over" : pct >= 80 ? "warn" : "ok";
      const barColor = pct >= 100 ? "var(--red)" : pct >= 80 ? "#e6a700" : "var(--green, #21a65c)";
      const stateText = pct >= 100 ? "已超预算" : pct >= 80 ? "临近预算上限" : "预算内";
      const costBtn = eps.some(e => e.cost > 0) ? "" : "（端点未配单价，成本按 0 估算）";
      budgetHtml = `
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px">
            <span><b>本月预算</b> $${esc(budget.limit.toLocaleString())}</span>
            <span style="color:${barColor};font-weight:600">${esc(stateText)} · 已用 $${esc(budget.used.toLocaleString())}（${pct}%）${costBtn}</span>
          </div>
          <div style="height:8px;border-radius:5px;background:var(--border);overflow:hidden">
            <div style="height:100%;width:${Math.min(100, pct)}%;background:${barColor}"></div>
          </div>
        </div>`;
      if (level !== lastBudgetLevel && level !== "ok") {
        lastBudgetLevel = level;
        toast(level === "over" ? `⚠️ 本月 AI 用量已达预算 ${pct}%` : `⚠️ 本月 AI 用量已用 ${pct}%（≥80% 阈值）`);
      } else if (level === "ok") { lastBudgetLevel = "ok"; }
    }

    // —— 按日折线（原生 SVG，无第三方库）：横轴日期、纵轴调用次数 ——
    let chartHtml = "";
    if (days.length) {
      const W = 560, H = 130, PL = 34, PR = 10, PT = 12, PB = 22;
      const iw = W - PL - PR, ih = H - PT - PB;
      const maxCalls = Math.max(1, ...days.map(d => Math.max(d.calls || 0, 1)));
      const xs = i => PL + (days.length === 1 ? iw / 2 : i * iw / (days.length - 1));
      const ys = c => PT + ih - (c / maxCalls) * ih;
      const pts = days.map((d, i) => `${xs(i).toFixed(1)},${ys(d.calls || 0).toFixed(1)}`).join(" ");
      const grid = [0, 0.5, 1].map(f => {
        const y = PT + ih * f;
        return `<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W - PR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>` +
               `<text x="${PL - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--text-faint)">${Math.round(maxCalls * (1 - f))}</text>`;
      }).join("");
      const xLabels = days.map((d, i) => {
        const show = days.length <= 12 || i % Math.ceil(days.length / 12) === 0 || i === days.length - 1;
        return show ? `<text x="${xs(i).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="9" fill="var(--text-faint)">${esc(d.date.slice(5))}</text>` : "";
      }).join("");
      const dots = days.map((d, i) =>
        `<circle cx="${xs(i).toFixed(1)}" cy="${ys(d.calls || 0).toFixed(1)}" r="2.6" fill="var(--accent, #2e7cf6)"><title>${esc(d.date)}：${d.calls} 次调用${d.failed ? "（失败 " + d.failed + "）" : ""}</title></circle>`).join("");
      chartHtml = `
        <div style="margin:8px 0 4px"><b>按日调用趋势</b></div>
        <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block" role="img" aria-label="按日 AI 调用次数折线图">
          <line x1="${PL}" y1="${PT}" x2="${PL}" y2="${H - PB}" stroke="var(--border)" stroke-width="1"/>
          ${grid}${xLabels}
          <polyline points="${pts}" fill="none" stroke="var(--accent, #2e7cf6)" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
          ${dots}
        </svg>`;
    }

    // —— 分布：端点 + 模型（都按调用数降序，最近 8 条） ——
    const rows = (list, kf, showCost) => list.slice(0, 8).map((it, i) => {
      const name = kf(it);
      const c = it.calls || 0, f = it.failed || 0, tk = it.tokens || 0;
      return `<div style="display:flex;gap:8px;align-items:center;font-size:12px;padding:2.5px 0">
        <span style="width:16px;color:var(--text-faint);text-align:right">${i + 1}</span>
        <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(name)}">${esc(name)}</span>
        <span style="color:var(--text-dim)">${c}次${f ? `<span style="color:var(--red)">/败${f}</span>` : ""} · ${tk.toLocaleString()}tk</span>
        ${showCost ? `<span style="min-width:64px;text-align:right">$${esc((it.cost || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }))}</span>` : ""}
      </div>`;
    }).join("") || "<div class='hint'>暂无记录（AI 功能调用后自动记录）</div>";
    const priced = eps.some(e => e.cost > 0);

    box.innerHTML = `
      ${budgetHtml}
      <div style="font-size:12px;line-height:1.9">
        调用 <b>${t.calls || 0}</b> 次（失败 ${t.failed || 0}） ·
        Token <b>${(t.prompt_tokens || 0).toLocaleString()}</b> + <b>${(t.completion_tokens || 0).toLocaleString()}</b> ·
        估算成本 <b>$${esc((t.cost || 0).toLocaleString(undefined, { maximumFractionDigits: 2 }))}</b>${priced ? "" : "（未配单价按 0）"}
      </div>
      ${chartHtml}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px">
        <div><b style="font-size:12.5px">按端点</b>${rows(eps, e => e.endpoint, priced)}</div>
        <div><b style="font-size:12.5px">按模型</b>${rows(models.map(([k, v]) => ({ endpoint: k, ...v })), m => m.endpoint, priced)}</div>
      </div>
      ${t.failed ? `<div style="margin-top:8px;font-size:11.5px;color:var(--red)">有失败调用，详情见 usage.jsonl</div>` : ""}`;
  } catch (e) {
    box.innerHTML = "加载失败：" + esc(e.message);
  }
}

// ============ 启动 ============
async function loadVersion() {
  try {
    const h = await api("/api/health");
    // 数据文件到底落在哪，是排查"我改的这份是谁的板子"时第一个要问的问题；
    // 后端一直返回 data_file，界面上以前从不显示
    document.getElementById("app-version").textContent =
      `AI 交接看板 v${h.version} · build ${h.build_time} · ${h.frozen ? "打包版" : "源码运行"}`;
    document.getElementById("app-version").title = "数据文件：" + (h.data_file || "未知");
  } catch (e) {
    document.getElementById("app-version").textContent = "版本信息读取失败";
  }
}
initPanelCollapse();
loadViews();
loadVersion();
loadOverview();
