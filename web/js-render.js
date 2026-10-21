/* 渲染工具：截止日期与拖拽 / Markdown / 就绪度 / 时间轴 / 依赖图 / 任务统计 / 里程碑 / 图表。*/
/* 拖拽与截止日期 / Markdown / 就绪度 / 趋势图 / 完整交接包 / AGENTS.md / 入口文件 / git 对照 / 会话提炼
 *
 * 2026-09-28 从 `web/index.html` 的 <script> 块按**原顺序**整段切出，内容逐字未改。
 * ⚠️ 加载顺序是契约：index.html 里这几个 <script> 的顺序不能调换 ——
 *    常量与函数在各自文件顶层执行，换序会撞 TDZ（ReferenceError）。
 */
// ============ 截止日期与拖拽 ============
function dueInfo(t) {
  const due = (t.due_date || "").trim();
  if (!due || t.status === "已完成") return null;
  const d = new Date(due + "T00:00:00");
  if (isNaN(d)) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((d - today) / 86400000);
  if (days < 0) return { cls: "due-overdue", text: "逾期 " + due.slice(5) };
  if (days <= 2) return { cls: "due-soon", text: "临期 " + due.slice(5) };
  return { cls: "due-ok", text: due.slice(5) };
}

async function moveTaskTo(tid, status, blockedReason) {
  // 拖一次卡片只意味着"改一个 status"。以前这里从页面缓存 `current` 里挑出整条任务
  // 再 PUT 回去，而 `current` 是打开详情时取的（页面不轮询）—— 中间智能体经 MCP 补的
  // refs / depends_on / desc 会被手里这份旧快照整体抹掉，界面上还提示"已移至进行中 ✓"。
  const t = (current.tasks || []).find(x => x.id === tid);
  if (!t || t.status === status) return;
  const body = { status };
  if (status === "阻塞" && blockedReason) body.blocked_reason = blockedReason;
  try {
    await api("/api/tasks/" + tid, { method: "PATCH", body: JSON.stringify(body) });
    toast(status === "阻塞" && blockedReason
      ? "已移至「阻塞」并记下原因 ✓" : `任务已移至「${status}」✓`);
    current = await api("/api/projects/" + currentPid);
    renderDetail();
    loadOverview();
  } catch (e) { toast("移动失败：" + e.message); }
}

// 拖进「阻塞」列先问一句原因（可跳过）。模态而不是原生 prompt()：
// prompt 会阻塞页面且不可样式化（契约测试明令禁止），模态还顺带能承载说明文案。
let pendingBlockedTid = null;
function askBlockedReason(tid) {
  if (!tid) return;
  pendingBlockedTid = tid;
  const input = document.getElementById("blocked-reason");
  if (input) input.value = "";
  openModal("blocked-modal");
}

function saveBlockedReason() {
  const tid = pendingBlockedTid;
  const input = document.getElementById("blocked-reason");
  const reason = input ? input.value.trim() : "";
  pendingBlockedTid = null;
  closeModal("blocked-modal");
  if (tid) moveTaskTo(tid, "阻塞", reason);
}

function skipBlockedReason() {
  const tid = pendingBlockedTid;
  pendingBlockedTid = null;
  closeModal("blocked-modal");
  if (tid) moveTaskTo(tid, "阻塞", "");
}

(function bindKanbanDnD() {
  const wrap = document.querySelector(".kanban");
  if (!wrap) return;
  const COL_MAP = { "k-todo": "待办", "k-doing": "进行中", "k-review": "待审查", "k-done": "已完成", "k-blocked": "阻塞" };
  wrap.addEventListener("dragover", e => {
    const col = e.target.closest(".kcol");
    if (!col) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    col.classList.add("drag-over");
  });
  wrap.addEventListener("dragleave", e => {
    const col = e.target.closest(".kcol");
    if (col && !col.contains(e.relatedTarget)) col.classList.remove("drag-over");
  });
  wrap.addEventListener("drop", e => {
    const col = e.target.closest(".kcol");
    if (!col) return;
    e.preventDefault();
    col.classList.remove("drag-over");
    const tid = e.dataTransfer.getData("text/plain");
    if (!tid) return;
    for (const [cls, st] of Object.entries(COL_MAP)) {
      if (col.classList.contains(cls)) {
        const t = (current.tasks || []).find(x => x.id === tid);
        // 已经是阻塞的直接走 moveTaskTo（它会 no-op），别无谓弹一次问原因的窗
        if (st === "阻塞" && (!t || t.status !== "阻塞")) askBlockedReason(tid);
        else moveTaskTo(tid, st);
        break;
      }
    }
  });
})();

function renderDash() {
  const el = document.getElementById("dash");
  if (!stats || !stats.project_total) { el.innerHTML = ""; return; }
  const sd = stats.status_dist || {};
  const legend = Object.entries(sd).map(([k, v]) =>
    `<span class="lg"><i style="background:${CHART_COLORS[["规划中", "进行中", "已暂停", "已完成"].indexOf(k)] || "#94a3b8"}"></i>${k}<b>${v}</b></span>`).join("");
  const show = projects.slice(0, 10);
  const bars = show.map(p => `
    <div class="bar-row"><span class="b-name" title="${esc(p.name)}">${esc(p.name)}</span>
    <div class="b-track"><div class="b-fill" style="width:${p.progress}%"></div></div>
    <span class="b-num">${p.progress}%</span></div>`).join("");

  // 原来这里铺了 9 张同样大小的统计卡，九个数字等权重地排在眼前 —— 真正的重点
  // （逾期/临期/阻塞这些"要动手的"）反而淹在里面。现在压成 3 个 KPI：
  // 规模（项目）、体量（任务）、以及唯一需要你立刻关注的「需要关注」。
  const done = sd["已完成"] || 0, doing = sd["进行中"] || 0;
  const planning = sd["规划中"] || 0, paused = sd["已暂停"] || 0;
  const blocked = stats.blocked_tasks || 0;
  const duesoon = stats.duesoon_tasks || 0;
  const overdue = stats.overdue_tasks || 0;
  const attention = blocked + duesoon + overdue;

  el.innerHTML = `
    <div class="kpi-row">
      <div class="kpi accent">
        <span class="k-lab">项目总数</span>
        <span class="k-num">${stats.project_total}</span>
        <span class="k-sub">进行中 ${doing} · 规划中 ${planning} · 已暂停 ${paused} · 已完成 ${done}</span>
      </div>
      <div class="kpi blue">
        <span class="k-lab">任务总数</span>
        <span class="k-num">${stats.total_tasks}</span>
        <span class="k-sub">平均进度 ${stats.avg_progress}% · 平均就绪度 ${stats.avg_readiness}</span>
      </div>
      <div class="kpi ${attention ? "alert" : "ok"}">
        <span class="k-lab">需要关注</span>
        <span class="k-num">${attention}</span>
        <span class="k-sub">${attention ? `逾期 ${overdue} · 临期 ${duesoon} · 阻塞 ${blocked}` : "没有逾期 / 临期 / 阻塞的任务 👍"}</span>
      </div>
    </div>
    <div class="chart-row">
      <div class="chart-card"><h4>📊 项目状态分布</h4>
        <div class="chart-body">${donutSVG(sd, CHART_COLORS)}<div class="donut-legend">${legend}</div></div>
      </div>
      <div class="chart-card">
        <h4>📶 项目进度对比
          <span style="margin-left:auto;display:flex;gap:8px;text-transform:none;letter-spacing:0">
            <button class="btn small" data-act="openTrends">📈 健康趋势</button>
            <button class="btn small" data-act="openAllPackage">📦 导出全部交接包</button>
          </span>
        </h4>
        <div class="bars">${bars}</div>
        ${projects.length > 10 ? `<div style="font-size:11px;color:var(--text-faint);margin-top:6px">仅显示前 10 个项目，共 ${projects.length} 个</div>` : ""}
      </div>
    </div>`;
}

// ============ Markdown 渲染（轻量、安全） ============
function renderMarkdown(text) {
  if (!text) return '<span style="color:var(--text-faint)">（未填写）</span>';
  let s = esc(text);
  s = s.replace(/```([\s\S]*?)```/g, (m, code) => `<pre class="md-pre">${code.trim()}</pre>`);
  s = s.replace(/`([^`\n]+)`/g, '<code class="md-code">$1</code>');
  s = s.replace(/^### (.*)$/gm, '<h5 class="md-h5">$1</h5>');
  s = s.replace(/^## (.*)$/gm, '<h4 class="md-h4">$1</h4>');
  s = s.replace(/^# (.*)$/gm, '<h3 class="md-h3">$1</h3>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|\n)&gt; (.*)$/gm, '$1<blockquote class="md-quote">$2</blockquote>');
  s = s.replace(/^---\s*$/gm, '<hr class="md-hr">');
  s = s.replace(/(?:^|\n)((?:[-*] .*(?:\n|$))+)/g, (m, block) => {
    const items = block.trim().split("\n").map(l => l.replace(/^[-*] /, "")).map(i => `<li class="md-li">${i}</li>`).join("");
    return '\n<ul class="md-ul">' + items + "</ul>\n";
  });
  s = s.replace(/(?:^|\n)((?:\d+\. .*(?:\n|$))+)/g, (m, block) => {
    const items = block.trim().split("\n").map(l => l.replace(/^\d+\. /, "")).map(i => `<li class="md-li">${i}</li>`).join("");
    return '\n<ol class="md-ol">' + items + "</ol>\n";
  });
  s = s.split(/\n{2,}/).map(p => p.trim()).filter(Boolean).map(p => {
    if (/^<(\w+)/.test(p)) return p;
    return `<p class="md-p">${p.replace(/\n/g, "<br>")}</p>`;
  }).join("\n");
  return s;
}

// ============ 就绪度检查清单 ============
function renderReadiness(pid) {
  const body = document.getElementById("readiness-body");
  if (!body) return;
  // 优先用项目详情里随 current 一起回来的那份：详情页内的任务新建/改状态/删除
  // 都会重取 current 并重绘，而全局 stats 只由总览刷新 —— 只看 stats 的话
  // 会出现「任务明明在下面，就绪度却写着没有任务清单」的旧快照。
  const rd = (current && current.id === pid && current.readiness)
    || ((stats && stats.readiness || []).find(x => x.id === pid));
  if (!rd) { body.innerHTML = '<div style="font-size:12px;color:var(--text-faint)">暂无数据</div>'; return; }
  const cls = rd.score >= 80 ? "rd-hi" : rd.score >= 50 ? "rd-mid" : "rd-lo";
  const tip = rd.score >= 80 ? "交接准备充分，可以放心交给新智能体 👍" : rd.score >= 50 ? "基本可用，建议按清单补齐缺口" : "交接信息不足，强烈建议补全再交接";
  body.innerHTML = `
    <div class="readiness-top">
      <div class="rd-ring ${cls}">${rd.score}</div>
      <div style="font-size:12px;color:var(--text-dim);line-height:1.6">满分 100 分<br>${tip}</div>
    </div>
    <div class="rd-items">
      ${rd.items.map(it => `<div class="rd-item ${it.ok ? "ok" : "no"}"><span class="mk">${it.ok ? "✓" : "✗"}</span><span>${esc(it.label)}</span><span class="pt">${it.score} 分</span></div>`).join("")}
    </div>`;
}

// ============ 健康分趋势图 ============
// ============ 截止时间轴 ============
// 详情页此前只有两个维度：状态（看板五列）和依赖顺序（依赖图）—— 唯独没有**时间**。
// 任务设了 due_date 之后，界面上只落到卡片上一行小字和仪表盘里的两个计数，
// 看不出"这周要交几件、哪件已经拖了"。这里把设了截止的未完成任务按日期排成一条轴。
// 口径与后端一致：due_date 只认 YYYY-MM-DD（_sanitize 会归一），逾期按自然日算。
function renderTimeline() {
  const panel = document.getElementById("timeline-panel");
  const body = document.getElementById("timeline-body");
  const note = document.getElementById("timeline-note");
  if (!panel || !body || !current) return;

  const DAY = 86400000;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const dated = (current.tasks || []).filter(t => t.due_date);
  const open = dated.filter(t => t.status !== "已完成");

  if (!open.length) {
    panel.style.display = "block";
    body.innerHTML = dated.length
      ? '<div class="tl-empty">所有设了截止日期的任务都已完成 ✓</div>'
      : '<div class="tl-empty">还没有任务设截止日期。<br>给任务填上「截止日期」后，这里会按时间排出来'
        + '（<b>逾期</b>红 · <b>今天</b>橙 · 越远越淡）。</div>';
    if (note) note.textContent = "";
    return;
  }

  const groups = new Map();
  for (const t of open) {
    const d = String(t.due_date).slice(0, 10);
    if (!groups.has(d)) groups.set(d, []);
    groups.get(d).push(t);
  }
  const days = [...groups.keys()].sort();
  const rows = days.map(d => {
    const diff = Math.round((new Date(d + "T00:00:00") - today) / DAY);
    const [cls, lab] = diff < 0 ? ["over", `逾期 ${-diff} 天`]
      : diff === 0 ? ["today", "今天"]
      : diff === 1 ? ["soon", "明天"]
      : diff <= 7 ? ["soon", `${diff} 天后`]
      : ["later", `${diff} 天后`];
    const items = groups.get(d).map(t =>
      `<span class="tl-chip st-${taskStCls(t.status)}" title="${esc(t.title)}｜${esc(t.status)}｜优先级 ${esc(priCls(t.priority))}">${esc(t.title)}</span>`).join("");
    return `<div class="tl-row ${cls}">
      <div class="tl-date"><b>${esc(d.slice(5))}</b><span>${lab}</span></div>
      <div class="tl-items">${items}</div>
    </div>`;
  }).join("");

  // 逾期多少条要明说 —— 只画颜色不报数，等于把"要不要马上动手"交给用户去数
  const overdue = days.filter(d => new Date(d + "T00:00:00") < today)
    .reduce((a, d) => a + groups.get(d).length, 0);
  const doneCount = dated.length - open.length;
  const bits = [`${open.length} 条未完成设了截止`];
  bits.push(overdue ? `${overdue} 条已逾期` : "没有逾期");
  if (doneCount) bits.push(`另有 ${doneCount} 条已完成`);
  note.textContent = bits.join(" · ");

  body.innerHTML = rows;
  panel.style.display = "block";
}

// 依赖图的描边颜色 = 任务状态（后端 dep_graph_svg 上的色），但图上从来没有说明 ——
// 不解释颜色的图等于让人猜。图例与图同一处渲染，跟着主题走。
function graphLegend() {
  return `<div class="graph-legend">${["待办", "进行中", "待审查", "已完成", "阻塞"]
    .map(s => `<span><i class="st-${s}"></i>${s}</span>`).join("")}</div>`;
}

/**
 * 依赖图：画的是"这堆活的形状"，SVG 由后端那份 `dep_graph_svg` 生成
 * （前端不再画第二遍 —— 同一件事两套实现是本项目反复栽的形状）。
 * 没有依赖时整块隐藏：那是"确实没有"，不是"读不到"，两者不能混成同一种沉默，
 * 所以请求失败时反过来——必须显形。
 */
async function renderGraph() {
  const panel = document.getElementById("graph-panel");
  const body = document.getElementById("graph-body");
  const note = document.getElementById("graph-note");
  if (!panel || !currentPid || !current) return;
  const pid = currentPid;
  const theme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  try {
    const g = await api(`/api/projects/${pid}/graph?theme=${theme}`);
    if (currentPid !== pid) return;
    if (g.empty) {
      panel.style.display = "none";
      body.innerHTML = "";
      return;
    }
    body.innerHTML = g.svg + graphLegend();
    const bits = [g.truncated ? `共 ${g.nodes} 条，只画前 ${g.shown} 条` : `${g.nodes} 条任务`];
    // 末列里可能混着"第 5 层"的节点，图上看着像并行、其实有前后关系 —— 不标注就是图在撒谎
    if (g.collapsed) bits.push("深于 4 层的已并入末列，末列内可能仍有先后");
    note.textContent = bits.join(" · ");
    panel.style.display = "block";
  } catch (e) {
    if (currentPid !== pid) return;
    panel.style.display = "block";
body.innerHTML = `<span style="color:var(--red)">依赖图画不出来：${esc(e.message)}</span>`;
    note.textContent = "";
  }
}

/**
 * 任务统计（P0-2）：数据由后端 `/api/projects/{pid}/stats` 聚合（口径与
 * due._due_state / executable_tasks 对齐），前端只负责把三块画出来——
 * 状态分布（SVG 堆叠条）、截止热力（逾期 / 未来 7 天 / 未设 / 更晚）、
 * 依赖阻塞链（谁在等谁）。空任务也要说话：显式写"没有任务"，不是四张
 * 零卡片让人猜；请求失败同样必须显形（与 renderGraph 同一条纪律）。
 */
async function renderStats() {
  const panel = document.getElementById("stats-panel");
  const body = document.getElementById("stats-body");
  const note = document.getElementById("stats-note");
  if (!panel || !currentPid) return;
  const pid = currentPid;
  panel.style.display = "block";
  body.innerHTML = "统计读取中…";
  note.textContent = "";
  try {
    const s = await api(`/api/projects/${pid}/stats`);
    if (currentPid !== pid) return;
    const bs = s.by_status || {};
    // 五个状态都要进分母：曾漏「待审查」——项目只有待审查任务时这里显示"该项目还没有任务"，
    // 与看板第五列直接矛盾（2026-10-06 审查发现）
    const n = ["待办", "进行中", "待审查", "已完成", "阻塞"].reduce((a, k) => a + (bs[k] || 0), 0);
    if (!n) {
      body.innerHTML = '<div style="color:var(--text-faint)">该项目还没有任务。</div>';
      return;
    }
    // 状态分布：一条横向堆叠条，颜色跟看板五列同一套（var(--gray/blue/purple/green/red)）。
    // 宽度按占比；最后一个非零段用 100 减已累计，避免浮点尾差让整条宽度对不齐。
    const seg = [["待办", "var(--gray)"], ["进行中", "var(--blue)"], ["待审查", "var(--purple)"], ["已完成", "var(--green)"], ["阻塞", "var(--red)"]];
    const nonzero = seg.filter(([k]) => (bs[k] || 0) > 0);
    let acc = 0;
    const parts = nonzero.map(([k, col], i) => {
      const v = bs[k] || 0;
      const w = v / n * 100;
      const x = acc;
      acc += w;
      const wFinal = (i === nonzero.length - 1) ? (100 - x) : w;
      return `<rect x="${x.toFixed(2)}" y="0" width="${wFinal.toFixed(2)}" height="100%" fill="${col}"><title>${esc(`${k} ${v} / ${n}`)}</title></rect>`;
    }).join("");
    const legend = seg.map(([k, col]) => {
      const v = bs[k] || 0;
      return `<span class="lg"><i style="background:${col};display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px"></i>${k}<b>${v}</b></span>`;
    }).join("");
    // 截止热力：四个桶，颜色传达紧急程度（逾期红 / 未来 7 天黄 / 更晚与未设灰）。
    const due = s.due || {};
    const heat = [
      ["overdue", due.overdue || 0, "red", "逾期"],
      ["within_7d", due.within_7d || 0, "#f59e0b", "未来 7 天"],
      ["later", due.later || 0, "", "更晚"],
      ["no_due", due.no_due || 0, "", "未设截止"],
    ].map(([, v, col, lab]) =>
      `<span class="heat-cell"><b style="${col ? `color:${col}` : ""}">${v}</b><span style="color:var(--text-faint)">${lab}</span></span>`).join("");
    // 依赖阻塞链：count=0 时明确说"没阻塞"，有则列明细（阻塞源 title 只读展示，不跳转）。
    const blk = s.blocked || { count: 0, tasks: [] };
    const blockedHtml = blk.count === 0
      ? `<div style="color:var(--green)">✓ 没有任务被前置阻塞</div>`
      : `<div style="color:var(--red)">⚠ ${blk.count} 个任务被前置阻塞</div>`
        + blk.tasks.map(t => `<div style="font-size:12px;margin-top:4px">· ${esc(t.title)} — 在等：${(t.blocked_by || []).map(x => esc(x.title)).join("、")}</div>`).join("")
        + (blk.tasks.length < blk.count ? `<div style="font-size:11px;color:var(--text-faint);margin-top:4px">…共 ${blk.count} 条，只列前 ${blk.tasks.length} 条</div>` : "");
    // 停滞检测（第三轮竞品吸取，对标 agent-board）：count=0 也显式说，不留空白被读成"没在检查"
    const stl = s.stalled || { count: 0, tasks: [] };
    const stalledHtml = stl.count === 0
      ? `<div style="color:var(--text-faint)">没有停滞的进行中任务</div>`
      : `<div style="color:#f59e0b">⚠ ${stl.count} 个进行中任务超 7 天没动</div>`
        + stl.tasks.map(t => `<div style="font-size:12px;margin-top:4px">· ${esc(t.title)} — 已搁置 ${t.idle_days} 天</div>`).join("")
        + (stl.tasks.length < stl.count ? `<div style="font-size:11px;color:var(--text-faint);margin-top:4px">…共 ${stl.count} 条，只列前 ${stl.tasks.length} 条</div>` : "")
    body.innerHTML = `
      <div style="margin-bottom:10px">
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">状态分布</div>
        <svg viewBox="0 0 100 10" preserveAspectRatio="none" style="width:100%;height:10px;border-radius:5px;overflow:hidden">${parts}</svg>
        <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:6px;font-size:12px">${legend}</div>
      </div>
      <div style="margin-bottom:10px">
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">截止日期（未完成任务）</div>
        <div style="display:flex;gap:14px;flex-wrap:wrap">${heat}</div>
      </div>
      <div style="margin-top:10px">
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">任务停滞（进行中超 7 天未动）</div>
        ${stalledHtml}
      </div>
      <div style="margin-top:10px">
        <div style="font-size:12px;font-weight:600;margin-bottom:6px">依赖阻塞</div>
        ${blockedHtml}
      </div>`;
    note.textContent = `共 ${n} 个任务`;
  } catch (e) {
    if (currentPid !== pid) return;
    panel.style.display = "block";
    body.innerHTML = `<span style="color:var(--red)">统计读取失败：${esc(e.message)}</span>`;
    note.textContent = "";
  }
}

// 里程碑分组 + 简易进度条（P2）。
// 进度数据不在这里重算：口径唯一在后端 `milestone_progress`（详情接口已附
// `milestones_progress`），这里只渲染 —— 「两条路给不同答案」是本项目反复出问题
// 的形状（AGENTS.md 约定 20）。任务归组判断只读展示，不参与计算。
function renderMilestones() {
  const panel = document.getElementById("ms-panel");
  const body = document.getElementById("ms-body");
  const p = current || {};
  const list = p.milestones_progress || [];
  if (!list.length) {
    panel.style.display = "none";
    return;
  }
  // 未归组任务数：有任务但没归进任何里程碑的，也要说出来（方案：兜底显示、不强删）。
  const names = new Set((p.milestones || []).map(m => (m.name || "").trim()).filter(Boolean));
  let unassigned = 0;
  (p.tasks || []).forEach(t => {
    const ms = (t.milestone || "").trim();
    if (!ms || !names.has(ms)) unassigned++;
  });
  const rows = list.map(m => {
    const w = Math.max(0, Math.min(100, Number(m.progress) || 0));
    const badge = m.done
      ? `<span class="badge" style="background:var(--green);color:#fff;flex-shrink:0">✓ 已完结</span>`
      : "";
    const sub = m.total
      ? `${m.done_tasks}/${m.total} 个任务 · ${w}%`
      : (m.done ? "无任务，手工标记完结" : "还没有任务归入");
    return `<div style="margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="font-weight:600;font-size:12.5px">${esc(m.name)}</span>${badge}
        <span style="margin-left:auto;color:var(--text-faint);font-size:11px">${esc(sub)}</span>
      </div>
      <div class="progress-row" style="margin:0">
        <div class="bar"><i style="width:${w}%"></i></div>
        <span class="num">${w}%</span>
      </div>
    </div>`;
  }).join("");
  const footer = unassigned > 0
    ? `<div style="font-size:11px;color:var(--text-faint);margin-top:4px">还有 ${unassigned} 个任务未归入任何里程碑</div>`
    : "";
  body.innerHTML = rows + footer;
  panel.style.display = "block";
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

