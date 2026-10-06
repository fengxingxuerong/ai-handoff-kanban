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

async function moveTaskTo(tid, status) {
  // 拖一次卡片只意味着"改一个 status"。以前这里从页面缓存 `current` 里挑出整条任务
  // 再 PUT 回去，而 `current` 是打开详情时取的（页面不轮询）—— 中间智能体经 MCP 补的
  // refs / depends_on / desc 会被手里这份旧快照整体抹掉，界面上还提示"已移至进行中 ✓"。
  const t = (current.tasks || []).find(x => x.id === tid);
  if (!t || t.status === status) return;
  try {
    await api("/api/tasks/" + tid, { method: "PATCH", body: JSON.stringify({ status }) });
    toast(`任务已移至「${status}」✓`);
    current = await api("/api/projects/" + currentPid);
    renderDetail();
    loadOverview();
  } catch (e) { toast("移动失败：" + e.message); }
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
      if (col.classList.contains(cls)) { moveTaskTo(tid, st); break; }
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
  el.innerHTML = `
    <div class="stat-cards">
      <div class="stat-card"><span class="s-num accent">${stats.project_total}</span><span class="s-lab">项目总数</span></div>
      <div class="stat-card"><span class="s-num" style="color:var(--blue)">${sd["进行中"] || 0}</span><span class="s-lab">进行中</span></div>
      <div class="stat-card"><span class="s-num green">${sd["已完成"] || 0}</span><span class="s-lab">已完成</span></div>
      <div class="stat-card"><span class="s-num purple">${stats.total_tasks}</span><span class="s-lab">任务总数</span></div>
      <div class="stat-card"><span class="s-num red">${stats.blocked_tasks}</span><span class="s-lab">阻塞任务</span></div>
      <div class="stat-card"><span class="s-num orange">${stats.duesoon_tasks || 0}</span><span class="s-lab">临期任务(2天内)</span></div>
      <div class="stat-card"><span class="s-num ${stats.overdue_tasks ? "red" : ""}" style="${stats.overdue_tasks ? "" : "color:var(--text-faint)"}">${stats.overdue_tasks || 0}</span><span class="s-lab">逾期任务</span></div>
      <div class="stat-card"><span class="s-num accent">${stats.avg_progress}%</span><span class="s-lab">平均进度</span></div>
      <div class="stat-card"><span class="s-num orange">${stats.avg_readiness}</span><span class="s-lab">平均就绪度</span></div>
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
  const rd = (stats && stats.readiness || []).find(x => x.id === pid);
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
async function openTrends() {
  openModal("trend-modal");
  document.getElementById("trend-body").innerHTML = '<div class="r-loading"><div class="spinner"></div><div>加载趋势数据…</div></div>';
  try {
    const d = await api("/api/ai/trends");
    renderTrends(d.series || []);
  } catch (e) {
    document.getElementById("trend-body").innerHTML = '<div class="trend-empty" style="color:var(--red)">加载失败：' + esc(e.message) + "</div>";
  }
}

function renderTrends(series) {
  const body = document.getElementById("trend-body");
  if (!series.length) {
    body.innerHTML = '<div class="trend-empty"><div class="big">📉</div>还没有监管报告数据。先去「📊 AI 监管」生成报告，就能看到各项目健康分随时间的趋势。</div>';
    return;
  }
  const times = [...new Set(series.flatMap(s => (s.points || []).map(p => p.time)))].sort();
  const W = 680, H = 320, padL = 46, padB = 36, padT = 18, padR = 18;
  const iw = W - padL - padR, ih = H - padT - padB;
  const x = i => times.length <= 1 ? padL + iw / 2 : padL + iw * (i / (times.length - 1));
  const y = v => padT + ih * (1 - (Math.max(0, Math.min(100, Number(v) || 0)) / 100));
  let svg = `<svg width="100%" height="${H}" viewBox="0 0 ${W} ${H}" style="background:var(--bg-soft);border-radius:10px;border:1px solid var(--border)">`;
  for (let g = 0; g <= 4; g++) {
    const gy = padT + ih * (g / 4);
    const val = Math.round(100 - 100 * (g / 4));
    svg += `<line x1="${padL}" y1="${gy}" x2="${W - padR}" y2="${gy}" stroke="var(--border)" stroke-width="1" stroke-dasharray="4 4"/>`;
    svg += `<text x="${padL - 8}" y="${gy + 4}" text-anchor="end" fill="var(--text-faint)" font-size="10">${val}</text>`;
  }
  const step = times.length > 6 ? Math.ceil(times.length / 6) : 1;
  times.forEach((t, i) => {
    if (i % step !== 0 && i !== times.length - 1) return;
    const label = String(t || "").slice(5, 16);
    svg += `<text x="${x(i)}" y="${H - 12}" text-anchor="middle" fill="var(--text-faint)" font-size="9">${esc(label)}</text>`;
  });
  series.forEach((s, si) => {
    const col = TREND_COLORS[si % TREND_COLORS.length];
    const pts = (s.points || []).map((p, i) => `${x(i)},${y(p.health)}`).join(" ");
    if ((s.points || []).length > 1) svg += `<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" opacity=".9"/>`;
    (s.points || []).forEach((p, i) => {
      svg += `<circle cx="${x(i)}" cy="${y(p.health)}" r="3.5" fill="${col}" stroke-width="1.5" style="stroke:var(--bg)"><title>${esc(s.name)} ${esc(p.time)}: ${esc(p.health)}分</title></circle>`;
    });
  });
  svg += "</svg>";
  const legend = series.map((s, si) => `<span class="tl"><i style="background:${TREND_COLORS[si % TREND_COLORS.length]}"></i>${esc(s.name)}</span>`).join("");
  body.innerHTML = `<div class="trend-wrap">${svg}<div class="trend-legend">${legend}</div></div>`;
}

// ============ 完整交接包 ============
let packageMode = "single";

// 交接包尺寸提示：接手方是模型，上下文窗口才是它的稀缺资源。
// 阈值与 app/main.py 的 context_budget() 一致（150 / 300 行），改一处要改两处。
const PKG_BUDGET = { warn: 150, over: 300 };

function renderPackageBudget() {
  const ta = document.getElementById("package-content");
  const box = document.getElementById("package-budget");
  if (!ta || !box) return;
  const text = ta.value || "";
  const lines = text ? text.replace(/\r\n?/g, "\n").replace(/\n+$/, "").split("\n").length : 0;
  let kb = 0;
  try { kb = Math.round(new TextEncoder().encode(text).length / 102.4) / 10; }
  catch (e) { kb = Math.round(text.length / 102.4) / 10; }
  const level = lines <= PKG_BUDGET.warn ? "ok" : (lines <= PKG_BUDGET.over ? "warn" : "over");
  box.textContent = "约 " + lines + " 行 / " + kb + " KB" + ({
    ok: "", warn: " · 偏长，建议精简背景与已完成任务",
    over: " · 过长，接手方可能读不完或被截断，建议裁剪",
  })[level];
  box.style.color = { ok: "var(--text-faint)", warn: "var(--orange)", over: "var(--red)" }[level];
}

(function bindPackageBudget() {
  const ta = document.getElementById("package-content");
  if (ta) ta.addEventListener("input", renderPackageBudget);
})();

async function openHandoffPackage() {
  packageMode = "single";
  document.getElementById("package-title").textContent = "📦 完整交接包 · " + (current ? current.name : "");
  try {
    const redact = document.getElementById("package-redact").checked ? 1 : 0;
    const md = await api("/api/export/" + currentPid + (redact ? "?redact=1" : ""));
    document.getElementById("package-content").value = md;
    renderPackageBudget();
    openModal("package-modal");
  } catch (e) { toast("生成失败：" + e.message); }
}

async function openAllPackage() {
  packageMode = "all";
  document.getElementById("package-title").textContent = "📦 全项目交接包";
  try {
    const redact = document.getElementById("package-redact").checked ? 1 : 0;
    const md = await api("/api/export-all" + (redact ? "?redact=1" : ""));
    document.getElementById("package-content").value = md;
    renderPackageBudget();
    openModal("package-modal");
  } catch (e) { toast("生成失败：" + e.message); }
}

async function copyPackage() {
  const ta = document.getElementById("package-content");
  try { await navigator.clipboard.writeText(ta.value); toast("交接包已复制 ✓"); }
  catch (e) { fallbackCopy(ta.value); }
}

async function savePackage(ev) {
  const content = document.getElementById("package-content").value;
  const name = (packageMode === "all" ? "全项目交接包" : (current ? current.name : "项目")) + "_" + new Date().toISOString().slice(0, 10) + ".md";
  const btn = (ev && ev.currentTarget) || document.getElementById("package-save-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/export/save", { method: "POST", body: JSON.stringify({ filename: name, content }) });
    toast("已保存：" + r.path);
  } catch (e) { toast("保存失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

// ============ AGENTS.md 生成 ============
async function openAgentsMD() {
  if (!currentPid) return;
  const pathInput = document.getElementById("agentsmd-path");
  pathInput.value = current.repo_path ? (current.repo_path.replace(/[\\/]+$/, "") + "\\AGENTS.md") : "";
  document.getElementById("agentsmd-content").value = "";
  openModal("agentsmd-modal");
}

async function generateAgentsMD() {
  const btn = document.getElementById("agentsmd-gen-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/export/agentsmd/" + currentPid, { method: "POST" });
    document.getElementById("agentsmd-content").value = r.text;
    toast(r.source === "ai" ? "AI 已按标准生成 AGENTS.md ✓" : "已用本地模板生成（AI 暂时不可用）");
  } catch (e) { toast("生成失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

function copyAgentsMD() {
  const ta = document.getElementById("agentsmd-content");
  if (!ta.value.trim()) { toast("内容为空，先点「生成」"); return; }
  try { navigator.clipboard.writeText(ta.value); toast("已复制 ✓"); }
  catch (e) { fallbackCopy(ta.value); }
}

// ============ 多智能体入口文件 ============
const AGENT_ENTRY_HINT = "AGENTS.md / CLAUDE.md / .cursor/rules/project.mdc / .github/copilot-instructions.md / GEMINI.md / .clinerules/project.md / CONVENTIONS.md / .windsurfrules";

function agentFilesBody() {
  const content = document.getElementById("agentsmd-content").value.trim();
  return {
    dir: document.getElementById("agentsmd-dir").value.trim(),
    content: content || null,
    overwrite: !!(document.getElementById("agentsmd-overwrite") || {}).checked,
  };
}

function renderAgentFiles(res) {
  const box = document.getElementById("agentsmd-files");
  const written = res.written || [];
  const skippedExisting = res.skipped_existing || [];
  const failed = res.failed || [];
  const srcName = { ai: "AI 生成", local: "本地模板", "passed-in": "沿用当前内容" }[res.source] || res.source;
  const head = res.preview ? "预览（未写盘）" : `已写入 ${written.length} 个到 ${esc(res.dir || "")}`;
  const rows = (res.files || []).map(f => {
    let right;
    if (res.preview) right = f.content.length + " 字符";
    else if (written.includes(f.path)) right = "已写入";
    else if (skippedExisting.includes(f.path)) right = "已存在，跳过";
    else right = "失败";
    return `<div style="display:flex;justify-content:space-between;gap:12px"><span>${esc(f.path)}</span><span>${esc(right)}</span></div>`;
  }).join("");
  let extra = "";
  if (skippedExisting.length) extra = `<div style="margin-top:4px;color:var(--text-faint)">跳过了 ${skippedExisting.length} 个已有文件（勾选「覆盖」可强制写入）</div>`;
  else if (failed.length) extra = `<div style="margin-top:4px;color:var(--red)">${failed.length} 个写入失败</div>`;
  box.innerHTML = `<div style="margin-bottom:4px;color:var(--text-faint)">${head} · 内容来源：${esc(srcName)}</div>${rows}${extra}`;
  box.style.display = "block";
}

async function previewAgentFiles() {
  if (!currentPid) return;
  try {
    const r = await api("/api/export/agent-files/" + currentPid, {
      method: "POST", body: JSON.stringify(agentFilesBody()),
    });
    renderAgentFiles(r);
    toast(`将写出 ${r.files.length} 个入口文件`);
  } catch (e) { toast("预览失败：" + e.message); }
}

async function writeAgentFiles(ev) {
  if (!currentPid) return;
  const dir = document.getElementById("agentsmd-dir").value.trim();
  if (!dir) { toast("请填写项目根目录；只想看看就点「预览入口」"); return; }
  const btn = (ev && ev.currentTarget) || document.getElementById("agentsmd-write-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/export/agent-files/" + currentPid, {
      method: "POST", body: JSON.stringify(agentFilesBody()),
    });
    renderAgentFiles(r);
    const extra = (r.skipped_existing || []).length
      ? `，跳过 ${r.skipped_existing.length} 个已有文件（要覆盖请勾选）` : "";
    toast(`已写入 ${r.written.length} 个文件到 ${r.dir}${extra} ✓`);
  } catch (e) { toast("写入失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

async function saveAgentsMD(ev) {
  const content = document.getElementById("agentsmd-content").value;
  if (!content.trim()) { toast("内容为空，先点「生成」"); return; }
  let path = document.getElementById("agentsmd-path").value.trim();
  if (!path) { toast("请填写保存路径"); return; }
  // 与后端保存白名单一字不差（app/main.py 的 `save_export`）：以前少一条 `.markdown`，
  // 后端明明允许，却被前端挡在门外 —— 两条路给不同答案的最小一例。
  if (!/\.(md|txt|markdown|agentsmd)$/i.test(path)) { toast("路径需以 .md / .txt / .markdown / .agentsmd 结尾"); return; }
  const btn = (ev && ev.currentTarget) || document.getElementById("agentsmd-save-btn");
  btn.classList.add("ai-loading-btn");
  try {
    // 目标默认就是 <仓库>\AGENTS.md，很可能是用户手写的 —— 与"写入项目目录"共用同一个
    // 覆盖开关，409 时 api() 会把服务端的提示原样送到 toast 里。
    const r = await api("/api/export/save", { method: "POST", body: JSON.stringify({
      filename: "AGENTS.md", content, path,
      overwrite: !!(document.getElementById("agentsmd-overwrite") || {}).checked,
    }) });
    toast("已保存：" + r.path + " ✓");
  } catch (e) { toast("保存失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

// ============ 代码现状（git）与「看板 vs 现实」对照 ============
function daysSince(s) {
  if (!s) return null;
  const t = new Date(String(s).trim().replace(" ", "T"));
  return isNaN(t.getTime()) ? null : Math.floor((Date.now() - t.getTime()) / 86400000);
}

function boardLastTouch(p) {
  // 看板「最后一次真动过」= 任务 journal 的时间与项目 updated_at 里最新的那个。
  // 只看 updated_at 不够：它会被「只把下拉框里的优先级点了一下」顶上去。
  let best = p.updated_at || "";
  (p.tasks || []).forEach(t => (t.journal || []).forEach(e => {
    if ((e.time || "") > best) best = e.time;
  }));
  return best;
}

// 详情页每次重绘都会跑 renderDetail，而 /api/projects/{pid}/git 是**故意不缓存**的
// （它是给人点的，要现值）。所以这里自己按 pid 存一份，只有点「提交明细」才强制刷新。
let gitCache = { pid: null, data: null, failed: null };

async function fetchGit(pid, force) {
  if (!pid || (!force && gitCache.pid === pid && (gitCache.data || gitCache.failed))) {
    return gitCache.data;
  }
  // 没填代码路径就别发这个请求：后端必然回 400，控制台里那条"资源加载失败"
  // 会把真正该被看见的报错淹掉
  if (!(current && current.repo_path)) {
    gitCache = { pid, data: null, failed: null };
    return null;
  }
  try {
    const g = await api(`/api/projects/${pid}/git?limit=8`);
    gitCache = { pid, data: g, failed: null };
  } catch (e) {
    gitCache = { pid, data: null, failed: e.message };
  }
  return gitCache.data;
}

/**
 * 分歧判据。以前这里只写「days>=7 且 dirty==0」两个常数，却打出
 * 「看板还在推进，代码却停了？」—— 判据里根本没有一项关于看板是否推进，
 * 于是 progress 停在 0%、三个月没动的项目同样会喊这句话。
 * 现在两边都要成立才下结论：代码侧停滞 + 看板侧确实有动静。
 */
function realityVerdicts(p, g) {
  const out = [];
  const tasks = p.tasks || [];
  const done = tasks.filter(t => t.status === "已完成").length;
  const idle = daysSince(boardLastTouch(p));
  // 这两条只看看板自己前后对不对，一个 git 字段都不碰 —— 以前却被下面的
  // `if (!p.repo_path) return` 一起挡在门外，于是"没填代码路径"的项目
  // （占大多数）永远听不到"进度写 90% 却一条任务没完成"这句最该听到的话。
  if (tasks.length && p.progress >= 60 && done === 0) {
    out.push(["warn", `进度条写 ${p.progress}%，但 ${tasks.length} 条任务里没有一条已完成 —— `
                   + "两边对不上，接手方不知道该信哪个。"]);
  }
  if (tasks.length && done / tasks.length >= 0.6 && p.progress <= 25) {
    out.push(["warn", `${done}/${tasks.length} 条任务已完成，进度条却只有 ${p.progress}% —— 对不上`
                   + "（也可能是还有活没登记，那就把任务补进来）。"]);
  }
  if (!p.repo_path) {
    out.push(["", "还没填代码路径：上面那句是**只对着看板自己**核出来的，"
                  + "填上才能把「写的进度」与「git 里真发生的提交」放在一起看 —— 这件事正是这个看板存在的理由。"]);
    return out;
  }
  if (!g) {
    out.push(["", gitCache.failed ? ("代码现状读不到：" + esc(gitCache.failed)
             + "（确认路径存在且是个 git 仓库）") : "代码现状读取中…"]);
    return out;
  }
  const codeIdle = g.days_since_commit, dirty = g.dirty_count || 0;
  if (codeIdle !== null && codeIdle !== undefined && codeIdle >= 7 && dirty === 0
      && idle !== null && idle <= 6) {
    out.push(["bad", `看板这边 ${idle} 天内还记过动静，代码却已经 ${codeIdle} 天没提交、工作区也是干净的`
                   + ` —— 要么进度是估的，要么改动没进 git。`]);
  }
  if (codeIdle !== null && codeIdle !== undefined && codeIdle <= 2 && idle !== null && idle >= 7) {
    out.push(["warn", `代码 ${codeIdle} 天前刚提交过，看板却停在 ${idle} 天前 —— 接手的人会低估进度。`
                   + `顺手更新一下 progress / next_action。`]);
  }
  if (!out.length) out.push(["ok", "两边节奏对得上：看板记录的动静与提交记录没有互相打脸。"]);
  return out;
}

async function renderReality() {
  const box = document.getElementById("reality-body");
  const vbox = document.getElementById("reality-verdicts");
  if (!box || !current || !currentPid) return;
  const pid = currentPid;
  const p = current;
  const tasks = p.tasks || [];
  const done = tasks.filter(t => t.status === "已完成").length;
  const blocked = tasks.filter(t => t.status === "阻塞").length;
  const idle = daysSince(boardLastTouch(p));
  const board = [];
  board.push(`完成进度 <b>${p.progress}%</b> · 任务 <b>${done}/${tasks.length}</b> 已完成`
             + (blocked ? ` · <b>${blocked}</b> 阻塞` : ""));
  board.push(idle === null ? "看板还没有任何动静记录"
                           : `最后记下动静：<b>${idle}</b> 天前`);
  box.innerHTML = `<div><div class="t">📋 看板自报</div><div class="b">${board.join("<br>")}</div></div>`
    + `<div><div class="t">🌿 代码现实</div><div class="b" id="reality-code">读取中…</div></div>`;
  // 先占一行再等 git：否则这段时间里面板是"两块卡片 + 没有结论"，
  // 读的人会把"还没算完"当成"没查出问题"
  vbox.innerHTML = '<div class="verdict">正在核对两边节奏…</div>';
  const g = await fetchGit(pid);
  if (currentPid !== pid) return;                 // 期间切到别的项目了，别把上一份贴回来
  const code = document.getElementById("reality-code");
  if (g) {
    const head = g.head || {};
    code.innerHTML = `分支 <b>${esc(g.branch || "(detached)")}</b>`
      + ` · 距上次提交 <b>${g.days_since_commit === null ? "?" : g.days_since_commit}</b> 天`
      + `<br>未提交改动 <b>${g.dirty_count}</b> 个`
      + (head.subject ? `<br>HEAD ${esc(head.hash)} ${esc(String(head.subject).slice(0, 40))}` : "");
  } else {
    code.innerHTML = p.repo_path ? '<span style="color:var(--red)">读不到</span>' : "未填代码路径";
  }
  vbox.innerHTML = realityVerdicts(p, g).map(v =>
    `<div class="verdict ${v[0]}">${v[1]}</div>`).join("");
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
    body.innerHTML = g.svg;
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
      <div>
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

async function loadGitInfo() {
  if (!currentPid) return;
  const panel = document.getElementById("git-panel");
  const box = document.getElementById("git-body");
  panel.style.display = "block";
  box.innerHTML = "读取中…";
  const pid = currentPid;
  try {
    // force：这个按钮的语义就是"给我看现值"，不能吐上一次缓存
    const g = await fetchGit(pid, true);
    if (!g) throw new Error(gitCache.failed || "读不到");
    const head = g.head || {};
    const rows = [];
    rows.push(`分支 <b>${esc(g.branch || "(detached)")}</b> · 工作区${g.dirty_count === 0 ? "干净" : `有 ${g.dirty_count} 个未提交改动`}`);
    if (head.hash) rows.push(`HEAD ${esc(head.hash)} · ${esc(head.date)} · ${esc(head.subject || "")}${head.author ? ` · ${esc(head.author)}` : ""}`);
    if (g.days_since_commit !== null) rows.push(`距上次提交 <b>${g.days_since_commit}</b> 天`);
    rows.push('<div style="margin-top:6px;color:var(--text-faint)">最近提交</div>');
    (g.commits || []).forEach(c => rows.push(`${esc(c.hash)} ${esc(c.date)} ${esc(c.subject || "")}`));
    if (g.dirty_files && g.dirty_files.length) {
      rows.push('<div style="margin-top:6px;color:var(--text-faint)">未提交文件</div>');
      g.dirty_files.forEach(f => rows.push(esc(f)));
    }
    box.innerHTML = rows.join("<br>");
    renderReality();          // 强制刷新后，上面的对照块也换成同一份现值
  } catch (e) {
    box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`
      + '<div style="margin-top:4px;color:var(--text-faint)">先在项目里填对「代码路径」，并确认它是个 git 仓库</div>';
  }
}

// ============ 会话提炼 ============
let summarizeData = null;
// 这次提炼要落到哪个项目 / 这批任务的幂等键前缀。
// 单独拿出来的理由：以前"选另一个项目入库"直接改全局 currentPid，屏幕还停在原项目，
// 之后点「＋ 新建任务」却建进了另一个项目 —— 目标属于这次提炼，不属于"当前正在看谁"。
let summarizeTargetPid = "";
let summarizeBatchRef = "";

async function openSummarize() {
  if (!currentPid) return;
  summarizeData = null;
  document.getElementById("summarize-text").value = "";
  document.getElementById("summarize-result").style.display = "none";
  document.getElementById("summarize-apply-btn").style.display = "none";
  openModal("summarize-modal");
}

async function runSummarize() {
  const text = document.getElementById("summarize-text").value.trim();
  if (text.length < 10) { toast("请粘贴至少 10 个字的对话内容"); return; }
  const btn = document.getElementById("summarize-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/ai/summarize", { method: "POST", body: JSON.stringify({ pid: currentPid, text }) });
    summarizeData = r.parsed;
    summarizeTargetPid = currentPid;
    summarizeBatchRef = r.batch_ref || "";
    renderSummarizeResult(r.parsed);
  } catch (e) { toast("提炼失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

function renderSummarizeResult(d) {
  const box = document.getElementById("summarize-result");
  box.style.display = "block";
  const tasks = (d.new_tasks || []).map(t =>
    `<div class="task-row"><span class="tag2 pri-${priCls(t.priority)}">${esc(priCls(t.priority))}</span><span>${esc(t.title)}</span></div>`).join("");
  const nextSec = d.next_action ? `<div class="sec"><span class="lab">🎯 建议下一步</span><div class="body">${esc(d.next_action)}</div></div>` : "";
  const handoffSec = d.handoff_note_updates ? `<div class="sec"><span class="lab">🤝 建议补充进交接说明</span><div class="body">${esc(d.handoff_note_updates)}</div></div>` : "";
  box.innerHTML = `<div class="summ-box">
    <div class="sec"><span class="lab">📌 总结</span><div class="body">${esc(d.summary || "（无）")}</div></div>
    ${tasks ? `<div class="sec"><span class="lab">➕ 建议新增任务（${(d.new_tasks || []).length} 个）</span><div class="tasks">${tasks}</div></div>` : ""}
    ${nextSec}
    ${handoffSec}
  </div>`;
  document.getElementById("summarize-apply-btn").style.display =
    ((d.new_tasks || []).length || d.next_action || d.handoff_note_updates) ? "block" : "none";
}

async function applySummarize(modalId) {
  if (!summarizeData) return;
  if (!confirm("将创建提炼出的任务，并更新「下一步行动」与交接说明，确认？")) return;
  const btn = document.getElementById("summarize-apply-btn");
  btn.classList.add("ai-loading-btn");
  // 入库目标跟着**这次提炼**走，不跟着 currentPid 走：会话捕获里选了别的项目时，
  // 以前会把全局 currentPid 一起改掉，于是屏幕上还停在 A、之后点的每一件事都落在 B。
  const target = summarizeTargetPid || currentPid;
  let ok = 0, fail = 0, memFail = 0;
  // 进入时捕获一次：连点两下用的是同一批键；而下一次**重新提炼**会连同 summarizeData
  // 一起换掉它。不要在循环里读全局、也不要事后清空 —— 那会让"另一批提炼"的第 0 条
  // 撞上兜底键，被判成重放而一个字都不落盘（静默不建比重复建更难查）。
  const batch = summarizeBatchRef;
  try {
    const tasks = summarizeData.new_tasks || [];
    for (let i = 0; i < tasks.length; i++) {
      const t = tasks[i];
      try {
        const qs = batch ? `?client_ref=${encodeURIComponent(batch + ":" + i)}` : "";
        await api(`/api/projects/${target}/tasks${qs}`,
          { method: "POST", body: JSON.stringify({
            title: t.title || "提炼任务", desc: t.desc || "", priority: t.priority || "中",
            status: t.status || "待办", agent: "" }) });
        ok++;
      } catch (e) { fail++; }
    }
    const p = await api("/api/projects/" + target);
    // 只把这次真要改的两个字段发回去（PATCH），别拿刚读的整份再 PUT 一次：
    // 读与写之间智能体可能已经动过这个项目，整份写回会把那一段抹掉。
    const patch = {};
    if (summarizeData.next_action && summarizeData.next_action !== p.next_action) {
      patch.next_action = summarizeData.next_action;
    }
    if (summarizeData.handoff_note_updates) {
      const note = (p.handoff_note || "") + (p.handoff_note ? "\n\n" : "") + "**会话提炼补充**：" + summarizeData.handoff_note_updates;
      if (note !== p.handoff_note) patch.handoff_note = note;
    }
    if (Object.keys(patch).length) await api("/api/projects/" + target, { method: "PATCH",
      body: JSON.stringify(patch) });
    // 会话中的决策/坑/约定自动沉淀为结构化记忆
    for (const m of (summarizeData.memories || [])) {
      if (!m.text) continue;
      try {
        await api("/api/memories", { method: "POST", body: JSON.stringify({
          pid: target, type: m.type || "事实", text: m.text, source: "会话提炼" }) });
      } catch (e) { memFail++; }        // 不阻塞主流程，但不能一句话都不说
    }
    toast(`应用完成：新增任务 ${ok} 个，失败 ${fail} 个 ✓`
          + (memFail ? `；另有 ${memFail} 条记忆没记上（点「查记忆」可以看现状）` : ""));
    // 关的是**发起这次应用的那个弹窗**：会话捕获的按钮在 session-modal 里，
    // 以前写死关 summarize-modal，于是捕获弹窗不关、按钮还在，再点一次就是第二遍。
    closeOneModal(modalId || "summarize-modal");
    if (target === currentPid) {
      current = await api("/api/projects/" + currentPid);
      renderDetail();
    }
    loadOverview();
  } catch (e) { toast("应用失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

