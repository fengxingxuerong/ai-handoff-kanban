/* 交接物：健康分趋势 / 完整交接包 / AGENTS.md / 多智能体入口文件投影与体检。*/
async function openTrends(arg) {
  openModal("trend-modal");
  document.getElementById("trend-body").innerHTML = '<div class="r-loading"><div class="spinner"></div><div>加载趋势数据…</div></div>';
  // arg === "current"：从详情页进来，只看这个项目；总览那枚按钮不带参 = 看全部项目
  const onlyCurrent = arg === "current" && !!current;
  try {
    const d = await api("/api/ai/trends");
    let series = d.series || [];
    if (onlyCurrent) series = series.filter(s => s.name === current.name);
    renderTrends(series, onlyCurrent);
  } catch (e) {
    document.getElementById("trend-body").innerHTML = '<div class="trend-empty" style="color:var(--red)">加载失败：' + esc(e.message) + "</div>";
  }
}

function renderTrends(series, onlyCurrent) {
  const body = document.getElementById("trend-body");
  if (!series.length) {
    body.innerHTML = onlyCurrent
      ? '<div class="trend-empty"><div class="big">📉</div>这个项目还没有监管报告。点顶栏「✨ AI → 📊 AI 监管」跑一次，之后这里就会出现它的健康分走势。</div>'
      : '<div class="trend-empty"><div class="big">📉</div>还没有监管报告数据。先去「📊 AI 监管」生成报告，就能看到各项目健康分随时间的趋势。</div>';
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

// ============ 入口投影体检（对标 std-agent 的 `status`） ============
// 写盘对"已存在的入口文件"是默认跳过的（怕冲掉用户手写的内容），于是
// "仓库里那份入口早就跟看板漂移了"这件事从来没人体检过 —— 而漂移的入口
// 恰恰是接手方最信任的东西。这里只读比对，逐目标给出状态，不改一个字节。
function renderAgentFileChecks(res) {
  const box = document.getElementById("agentsmd-files");
  const s = res.check_summary || {};
  const LABEL = { ok: "✓ 一致", drift: "⚠ 已漂移", missing: "· 未写入", blocked: "✗ 路径被拦", unreadable: "? 读不了" };
  const COLOR = { ok: "var(--green)", drift: "var(--orange)", missing: "var(--text-faint)", blocked: "var(--red)", unreadable: "var(--red)" };
  const head = `体检 ${esc(res.dir || "")}：`
    + `<b style="color:var(--green)">${s.ok || 0} 一致</b> · `
    + `<b style="color:${s.drift ? "var(--orange)" : "var(--text-faint)"}">${s.drift || 0} 已漂移</b> · `
    + `${s.missing || 0} 未写入`;
  const rows = (res.checks || []).map(c =>
    `<div style="display:flex;gap:10px"><span style="width:74px;flex-shrink:0;color:${COLOR[c.state] || "inherit"}">${LABEL[c.state] || esc(c.state)}</span><span>${esc(c.path)}</span></div>`).join("");
  const hint = (s.drift || 0)
    ? '<div style="margin-top:5px;color:var(--orange)">漂移的入口已经和看板对不上了 —— 勾选「覆盖」再点「写入项目目录」即可修复</div>'
    : "";
  box.innerHTML = `<div style="margin-bottom:4px;color:var(--text-faint)">${head}</div>${rows}${hint}`;
  box.style.display = "block";
}

async function checkAgentFiles() {
  if (!currentPid) return;
  const dir = document.getElementById("agentsmd-dir").value.trim();
  if (!dir) { toast("先填「项目根目录」——体检要读磁盘上已有的入口文件"); return; }
  const btn = document.getElementById("agentsmd-check-btn");
  if (btn) { btn.disabled = true; btn.textContent = "体检中…"; }
  try {
    const r = await api("/api/export/agent-files/" + currentPid, {
      method: "POST", body: JSON.stringify({ ...agentFilesBody(), check_only: true }),
    });
    renderAgentFileChecks(r);
    const s = r.check_summary || {};
    toast(`入口体检：${s.ok || 0} 一致 / ${s.drift || 0} 已漂移 / ${s.missing || 0} 未写入`);
  } catch (e) { toast("体检失败：" + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = "🔍 体检入口"; }
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

