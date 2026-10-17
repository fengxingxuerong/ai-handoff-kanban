/* AI 相关：设置与端点 / 监管报告历史与应用 / 自然语言指令编排 / 交接说明生成 / 会话提炼。*/
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

// ============ AI 设置 ============
let aiConfig = null;
let pendingActions = [];
// 这一批 AI 指令的身份证号：创建类动作拿它当幂等键前缀。
// 以前"执行"没有键，同一条指令再点一次（或失败后重试）就把项目/任务建成一对。
let cmdBatchRef = "";

async function openAISettings() {
  try {
    aiConfig = await api("/api/ai/config");
    renderEndpoints(aiConfig.ai.endpoints || []);
    // 默认模型
    const sel = document.getElementById("ai-active-model");
    const models = [];
    (aiConfig.ai.endpoints || []).forEach(ep => (ep.models || []).forEach(m => { if (!models.includes(m)) models.push(m); }));
    sel.innerHTML = models.map(m => `<option ${m === aiConfig.ai.active_model ? "selected" : ""}>${esc(m)}</option>`).join("");
    // 定时
    const s = (aiConfig.ai.schedule || {});
    document.getElementById("ai-sched-enabled").checked = !!s.enabled;
    document.getElementById("ai-sched-freq").value = s.frequency || "daily";
    document.getElementById("ai-sched-time").value = s.time || "09:00";
    document.getElementById("ai-sched-last").textContent = s.last_run || "—";
    document.getElementById("mcp-readonly").checked = !!(aiConfig.ai.mcp_readonly);
    document.getElementById("ai-budget-limit").value = Number(aiConfig.ai.budget_limit || 0) || 0;
    // 打开时回设置页（用量 Tab 首次切入才加载），否则上次关在用量 Tab、这次打开还停在用量
    const usagePane = document.getElementById("ai-pane-usage");
    if (usagePane) usagePane.dataset.loaded = "";
    switchAITab("settings");
    loadBackups();
    openModal("ai-settings-modal");
  } catch (e) { toast("加载配置失败：" + e.message); }
}

function renderEndpoints(eps) {
  const wrap = document.getElementById("ai-endpoints");
  wrap.innerHTML = eps.map((ep, i) => `
    <div class="ep-card">
      <div class="ep-top">
        <input data-i="${i}" data-f="name" class="ep-name-input" value="${esc(ep.name)}">
        <label class="switch"><input type="checkbox" data-i="${i}" data-f="enabled" ${ep.enabled ? "checked" : ""}><span class="slider"></span></label>
      </div>
      <div class="field"><label>Base URL</label>
        <input data-i="${i}" data-f="base_url" value="${esc(ep.base_url)}" placeholder="https://.../v1"></div>
      <div class="field"><label>API Keys（每行一个，可多 Key 轮询）</label>
        <textarea data-i="${i}" data-f="keys" placeholder="sk-... 每行一个">${esc((ep.api_keys || []).join("\n"))}</textarea></div>
      <div class="field"><label>模型（逗号分隔，按优先级排序）</label>
        <input data-i="${i}" data-f="models" value="${esc((ep.models || []).join(", "))}"></div>
      <div class="field" style="flex-direction:row;gap:10px;align-items:flex-end">
        <div style="flex:1"><label>输入单价（$/千 token，0=不估算）</label>
          <input type="number" data-i="${i}" data-f="price_in" min="0" step="0.0001" value="${Number(ep.price_per_1k_input || 0) || 0}"></div>
        <div style="flex:1"><label>输出单价（$/千 token）</label>
          <input type="number" data-i="${i}" data-f="price_out" min="0" step="0.0001" value="${Number(ep.price_per_1k_output || 0) || 0}"></div>
      </div>
    </div>`).join("");
}

async function saveAISettings() {
  const epEls = [...document.querySelectorAll("#ai-endpoints .ep-card")];
  const endpoints = epEls.map(card => ({
    name: card.querySelector('[data-f="name"]').value.trim(),
    base_url: card.querySelector('[data-f="base_url"]').value.trim(),
    api_keys: card.querySelector('[data-f="keys"]').value.split("\n").map(s => s.trim()).filter(Boolean),
    models: card.querySelector('[data-f="models"]').value.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    enabled: card.querySelector('[data-f="enabled"]').checked,
    price_per_1k_input: Number(card.querySelector('[data-f="price_in"]').value) || 0,
    price_per_1k_output: Number(card.querySelector('[data-f="price_out"]').value) || 0,
  }));
  const budgetEl = document.getElementById("ai-budget-limit");
  const body = {
    endpoints,
    active_model: document.getElementById("ai-active-model").value,
    mcp_readonly: document.getElementById("mcp-readonly").checked,
    budget_limit: budgetEl ? Number(budgetEl.value) || 0 : 0,
    schedule: {
      enabled: document.getElementById("ai-sched-enabled").checked,
      frequency: document.getElementById("ai-sched-freq").value,
      time: document.getElementById("ai-sched-time").value,
    },
  };
  try {
    await api("/api/ai/config", { method: "PUT", body: JSON.stringify(body) });
    toast("AI 设置已保存 ✓");
    closeModal("ai-settings-modal");
  } catch (e) { toast("保存失败：" + e.message); }
}

async function testAI() {
  const btn = document.getElementById("ai-test-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/ai/test", { method: "POST" });
    if (r.ok) toast("连接成功：" + r.endpoint + " → " + (r.reply || "OK") + " ✓");
    else toast("连接失败：" + r.error);
  } catch (e) { toast("测试失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

// ============ AI 监管报告 ============
let currentReport = null;

async function loadReportHistory() {
  const wrap = document.getElementById("report-history");
  const list = document.getElementById("report-history-list");
  if (!wrap || !list) return;
  try {
    const r = await api("/api/ai/reports");
    const items = (r && r.reports) || [];
    if (!items.length) { wrap.style.display = "none"; list.innerHTML = ""; return; }
    list.innerHTML = items.slice(0, 12).map(x => {
      const tip = `${x.trigger || "手动"} · ${x.endpoint || "未知端点"} · ${x.insight_count} 条洞察`
        + (x.summary ? `｜${String(x.summary).slice(0, 80)}` : "");
      return `<button class="btn small" data-act="openReport" data-arg="${esc(x.id)}" `
        + `title="${esc(tip)}">${esc(String(x.created_at || "").slice(5, 16))}</button>`;
    }).join("");
    wrap.style.display = "block";
  } catch (e) {
    // 拿不到历史就说一句，别整条静默消失——那和"从来没有过历史报告"长得一模一样
    wrap.style.display = "block";
    list.innerHTML = `<span style="font-size:11px;color:var(--red)">历史报告读不到：${esc(e.message)}</span>`;
  }
}

async function openReport(rid) {
  const panel = document.getElementById("report-panel");
  document.getElementById("report-mask").classList.add("show");
  panel.classList.add("show");
  loadReportHistory();
  const rb = panel.querySelector(".r-body");
  if (rb) rb.scrollTop = modalScroll.get("report-panel") || 0;
  focusInto(panel);
  document.getElementById("report-body").innerHTML = '<div class="r-loading"><div class="spinner"></div><div>AI 正在分析全部项目，请稍候…</div></div>';
  document.getElementById("report-meta").textContent = rid ? "历史报告" : "生成中…";
  try {
    if (rid) {
      currentReport = await api("/api/ai/reports/" + rid);
      document.getElementById("report-meta").textContent = "历史报告 · " + currentReport.created_at + " · " + (currentReport.endpoint || "");
    } else {
      currentReport = await api("/api/ai/analyze", { method: "POST" });
      document.getElementById("report-meta").textContent = "生成于 " + currentReport.created_at + " · " + (currentReport.endpoint || "");
    }
    renderReport(currentReport);
  } catch (e) {
    // 「重试」必须重试**刚才那一次**：以前这里的按钮不带 rid，于是"回看某份历史报告失败"
    // 点一下重试变成"重新跑一次 AI 分析"（花配额、多存一份报告），而标题还写着"历史报告"。
    const retryArg = rid ? ` data-arg="${esc(rid)}"` : "";
    document.getElementById("report-body").innerHTML =
      '<div class="r-loading" style="color:var(--red)">' + (rid ? "读取历史报告失败：" : "分析失败：")
      + esc(e.message) + '<br><br><button class="btn" data-act="openReport"' + retryArg
      + '>重试</button></div>';
  }
}

function closeReport() {
  const panel = document.getElementById("report-panel");
  const rb = panel.querySelector(".r-body");
  modalScroll.set("report-panel", rb ? rb.scrollTop : 0);
  document.getElementById("report-mask").classList.remove("show");
  panel.classList.remove("show");
}

function healthClass(h) {
  h = Number(h) || 0;
  return h >= 70 ? "hi" : h >= 45 ? "mid" : "lo";
}

function renderReport(r) {
  const body = document.getElementById("report-body");
  const TREND_MARK = { down: ['▼', 'var(--red)'], up: ['▲', 'var(--green)'], flat: ['◆', 'var(--text-faint)'], new: ['✦', 'var(--accent2)'] };
  const insights = (r.insights || []).map(it => {
    const tr = it.trend || {};
    const mk = TREND_MARK[tr.level] || TREND_MARK.new;
    const trendHtml = tr.level === "down"
      ? `<span class="due-badge due-overdue">${mk[0]} 较上次 ${tr.delta > 0 ? "+" : ""}${tr.delta}</span>`
      : tr.level === "up"
        ? `<span class="due-badge" style="color:var(--green);background:rgba(52,211,153,.12)">${mk[0]} 较上次 +${tr.delta}</span>`
        : tr.level === "flat"
          ? `<span class="due-badge due-ok">${mk[0]} 持平</span>`
          : `<span class="due-badge due-ok">${mk[0]} 首次评估</span>`;
    return `
    <div class="insight">
      <div class="i-top">
        <div class="health ${healthClass(it.health)}">${Number(it.health) || "—"}</div>
        <div>
          <div class="i-name">${esc(it.name || "")} ${trendHtml}</div>
          <div class="i-reason">${esc(it.reason || "")}</div>
        </div>
      </div>
      ${(it.risks || []).length ? `<div class="i-sec">⚠ 风险点</div><div class="risk-list">${it.risks.map(x => `<div class="risk-item">${esc(x)}</div>`).join("")}</div>` : ""}
      ${(it.suggestions || []).length ? `<div class="i-sec">💡 建议</div><div class="sug-list">${it.suggestions.map((s, i) => `
        <div class="sug-item"><div class="sug-text">${esc(s.text || "")}</div>
        <button class="apply-btn" data-pid="${esc(it.id || "")}" data-txt="${esc(s.text || "")}" data-act="applySuggestion">应用</button></div>`).join("")}</div>` : ""}
    </div>`;
  }).join("");
  const global = (r.global_suggestions || []).length ? `
    <div class="r-global">
      <div class="lab">🌐 全局建议</div>
      <ul>${r.global_suggestions.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
    </div>` : "";
  body.innerHTML = `
    <div class="r-summary"><span class="lab">📌 监管摘要</span>${esc(r.summary || "（无摘要）")}</div>
    ${insights ? `<div class="r-insights">${insights}</div>` : '<div class="r-loading" style="padding:30px">（无项目洞察）</div>'}
    ${global}`;
}

async function applySuggestion(btn) {
  const pid = btn.dataset.pid;
  const txt = btn.dataset.txt;
  if (!pid || !txt) return;
  if (!confirm("把这条建议写入该项目的「下一步行动」？")) return;
  try {
    const r = await api("/api/ai/apply-suggestions", {
      method: "POST",
      body: JSON.stringify([{ project_id: pid, field: "next_action", value: txt }]),
    });
    // 服务端可能一条都没写（项目已被删；内容带着脱敏掩码；字段不在清单；值过长）。
    // 原来这里不看返回值直接宣布"已应用 ✓"，于是点了个按钮、什么也没发生、界面还说成功了。
    if (!r || !r.applied) {
      const why = r && r.results && r.results[0] && r.results[0].reason;
      toast("未写入：" + (why || (r && r.skipped_masked
        ? "这条建议里带着脱敏掩码，请到项目里核对原文后手动补"
        : "没找到对应项目（可能已被删除）")));
      return;
    }
    btn.classList.add("done");
    btn.textContent = "已应用 ✓";
    toast("建议已写入项目下一步行动 ✓");
  } catch (e) { toast("应用失败：" + e.message); }
}

// ============ AI 指令（自然语言维护） ============
function openAICmd() {
  pendingActions = [];
  document.getElementById("cmd-exec-row").style.display = "none";
  openModal("ai-cmd-modal");
}

function cmdAddMsg(cls, html) {
  const chat = document.getElementById("cmd-chat");
  const div = document.createElement("div");
  div.className = "cmd-msg " + cls;
  div.innerHTML = html;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

const OP_TAG = { create_project: "新建项目", update_project: "改项目", delete_project: "删项目", create_task: "加任务", update_task: "改任务", delete_task: "删任务" };

function renderActions(actions) {
  pendingActions = actions;
  const row = document.getElementById("cmd-exec-row");
  if (!actions || !actions.length) { row.style.display = "none"; return; }
  row.style.display = "block";
  const btn = row.querySelector("button");
  btn.textContent = `✅ 确认执行以上 ${actions.length} 个操作`;
  const list = actions.map(a => {
    // a.type 来自模型返回，未在白名单里时也不能原样进 innerHTML（标签是拼出来的）
    const type = String(a.type || "");
    const tag = OP_TAG[type] || esc("未知操作 " + type);
    const cls = type.startsWith("delete") ? "delete" : type.startsWith("update") ? "update" : "create";
    const desc = describeAction(a);
    return `<div class="op"><span class="op-tag ${cls}">${tag}</span><span>${esc(desc)}</span></div>`;
  }).join("");
  const holder = document.getElementById("cmd-actions-holder");
  if (holder) holder.remove();
  const wrap = document.createElement("div");
  wrap.id = "cmd-actions-holder";
  wrap.innerHTML = list;
  cmdAddMsg("ai", wrap.innerHTML);
}

function describeAction(a) {
  switch (a.type) {
    case "create_project": return `新建项目「${a.name || ""}」` + (a.priority ? `（优先级${a.priority}）` : "");
    case "update_project": return `项目「${a.project_id || a.name || ""}」的 ${a.field || ""} 改为「${a.value || ""}」`;
    case "delete_project": return `删除项目「${a.project_id || a.name || ""}」`;
    case "create_task": return `在项目「${a.project_id || ""}」下新建任务「${a.title || ""}」` + (a.priority ? `（${a.priority}）` : "");
    case "update_task": return `任务「${a.task_id || a.title || ""}」的 ${a.field || ""} 改为「${a.value || ""}」`;
    case "delete_task": return `删除任务「${a.task_id || a.title || ""}」`;
    default: return JSON.stringify(a);
  }
}

async function sendCommand() {
  const input = document.getElementById("cmd-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  cmdAddMsg("user", esc(text));
  const sendBtn = document.getElementById("cmd-send-btn");
  sendBtn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/ai/command", { method: "POST", body: JSON.stringify({ text }) });
    const parsed = r.parsed || {};
    cmdBatchRef = r.batch_ref || "";
    if (parsed.need_clarification) {
      cmdAddMsg("ai", "🤔 需要补充：" + esc(parsed.need_clarification));
      document.getElementById("cmd-exec-row").style.display = "none";
    } else if (parsed.actions && parsed.actions.length) {
      renderActions(parsed.actions);
    } else {
      cmdAddMsg("ai", "没解析出可执行的操作，换个说法试试？");
      document.getElementById("cmd-exec-row").style.display = "none";
    }
  } catch (e) {
    cmdAddMsg("ai", "❌ " + esc(e.message));
  }
  sendBtn.classList.remove("ai-loading-btn");
}

async function executeActions() {
  if (!pendingActions || !pendingActions.length) return;
  const btn = document.getElementById("cmd-exec-row").querySelector("button");
  btn.classList.add("ai-loading-btn");
  btn.textContent = "执行中…";
  let ok = 0, fail = 0;
  const batch = cmdBatchRef;       // 进入时捕获一次，两次连点用的是同一批键
  for (let i = 0; i < pendingActions.length; i++) {
    const a = pendingActions[i];
    try {
      await execSingleAction(a, i, batch);
      ok++;
    } catch (e) {
      fail++;
      cmdAddMsg("ai", "❌ " + esc(describeAction(a)) + " 执行失败：" + esc(e.message));
    }
  }
  btn.classList.remove("ai-loading-btn");
  document.getElementById("cmd-exec-row").style.display = "none";
  cmdAddMsg("ai", `✅ 执行完成：成功 ${ok} 个，失败 ${fail} 个`);
  // 约定：过程留在聊天气泡里，但**结果一律再播报一次 toast** —— 弹窗会被关掉，
  // 而 toast 是全站唯一的 aria-live 区域；少了这句，键盘/读屏用户根本不知道写进去了没有。
  toast(fail ? "已执行：成功 " + ok + " 个，失败 " + fail + " 个" : "已执行 " + ok + " 个操作 ✓");
  loadOverview();
  if (document.getElementById("view-detail").classList.contains("active") && currentPid) {
    current = await api("/api/projects/" + currentPid);
    renderDetail();
  }
}

/* AI 指令可改字段 = 看板里的普通内容字段，**排除带本机权限的那一个**：
   项目的 repo_path 一旦指向某个本地目录，监管分析就会去跑 git，
   把分支、最后一条提交、脏文件数拼进 prompt 送给模型（_projects_brief）。
   模型可能被项目名/背景里的注入文案带偏，一条 {field:"repo_path", value:"D:\\某仓库"}
   就等于让外部内容远程探测本机目录结构 —— 所以 AI 只能改内容，不能改"读哪里"。
   用户自己在编辑弹窗里填 repo_path 走的是另一条路径，不受这里限制。 */
const AI_PROJECT_FIELDS = ["name", "status", "progress", "priority", "tags",
                           "background", "handoff_note", "next_action"];
const AI_TASK_FIELDS = ["title", "desc", "status", "priority", "agent", "due_date",
                        "refs", "depends_on"];

function assertAiField(allowed, field, type) {
  if (!allowed.includes(field)) throw new Error(`${type} 不支持修改字段「${field}」`);
}

async function execSingleAction(a, i, batch) {
  const type = a.type;
  // 创建类动作带幂等键：这一批（同一次解析结果）里的第 i 条，键由调用方在执行开始时
  // 一次性捕获（见 executeActions），不要在循环里读全局 —— 全局会被上一次的执行清空，
  // 那时再兜一个固定前缀就会跨命令撞键：**下一条**正常指令的第一条创建动作会被
  // 判成"重放"而一个字都不落盘，那比建两遍更难查。
  // 拿不到 batch_ref（服务端是旧的）就不带键，退回"可能重复"而不是"静默不建"。
  const qs = batch ? "?client_ref=" + encodeURIComponent(batch + ":" + i) : "";
  if (type === "create_project") {
    await api("/api/projects" + qs, { method: "POST", body: JSON.stringify({
      name: a.name || "新项目", status: a.status || "规划中", priority: a.priority || "中",
      progress: Number(a.progress) || 0, next_action: a.next_action || "", handoff_note: a.handoff_note || "", background: a.background || "",
    }) });
  } else if (type === "update_project") {
    assertAiField(AI_PROJECT_FIELDS, a.field, "update_project");
    // PATCH：以前是 GET 整份 → 改一个字段 → PUT 整份，而那次 GET 在临界区外 ——
    // 期间智能体写进这个项目的 next_action / 交接说明会被手里这份旧快照抹掉。
    await api("/api/projects/" + a.project_id, { method: "PATCH",
      body: JSON.stringify({ [a.field]: a.value }) });
  } else if (type === "delete_project") {
    await api("/api/projects/" + a.project_id, { method: "DELETE" });
  } else if (type === "create_task") {
    await api("/api/projects/" + a.project_id + "/tasks" + qs, { method: "POST", body: JSON.stringify({
      title: a.title || "新任务", desc: a.desc || "", priority: a.priority || "中",
      status: a.status || "待办", agent: a.agent || "",
    }) });
  } else if (type === "update_task") {
    assertAiField(AI_TASK_FIELDS, a.field, "update_task");
    const tid = await resolveTaskId(a);
    if (!tid) throw new Error("找不到对应任务");
    // 不再为了改一个字段先整份读回项目、改完再 PUT 回去（同 update_project 的理由；
    // 顺带省掉一次全量读盘 —— resolveTaskId 里该读的那一次已经读了）
    await api("/api/tasks/" + tid, { method: "PATCH",
      body: JSON.stringify({ [a.field]: a.value }) });
  } else if (type === "delete_task") {
    const tid = await resolveTaskId(a);
    if (!tid) throw new Error("找不到对应任务");
    await api("/api/tasks/" + tid, { method: "DELETE" });
  } else {
    throw new Error("不支持的操作类型：" + type);
  }
}

async function resolveTaskId(a) {
  if (a.task_id) return a.task_id;
  const project = await findProjectOfTask(a);
  if (!project) return null;
  const t = (project.tasks || []).find(x => x.title === a.title || (a.title && x.title.includes(a.title)));
  return t ? t.id : null;
}

async function findProjectOfTask(a) {
  if (a.project_id) return api("/api/projects/" + a.project_id);
  // 只有 task_id 时按 id 反查所属项目：简报里的任务标题现在带脱敏掩码，
  // 拿标题当匹配键已经不可靠了，id 才是稳的那个
  if (a.task_id) {
    try {
      const hit = await api("/api/tasks/" + a.task_id);
      if (hit && hit.project_id) return api("/api/projects/" + hit.project_id);
    } catch (e) { return null; }
  }
  // 按名称匹配
  if (a.project_name) {
    const all = await api("/api/projects");
    const p = all.projects.find(x => x.name === a.project_name || x.name.includes(a.project_name));
    return p ? api("/api/projects/" + p.id) : null;
  }
  return null;
}

// ============ AI 交接说明生成 ============
async function openAIHandoff() {
  if (!currentPid) return;
  document.getElementById("ai-handoff-mode").value = "generate";
  document.getElementById("ai-handoff-result").value = "";
  openModal("ai-handoff-modal");
}

async function generateHandoff() {
  const mode = document.getElementById("ai-handoff-mode").value;
  const btn = document.getElementById("ai-handoff-gen-btn");
  btn.classList.add("ai-loading-btn");
  try {
    const r = await api("/api/ai/handoff/" + currentPid, { method: "POST", body: JSON.stringify({ mode }) });
    document.getElementById("ai-handoff-result").value = r.text;
    toast("生成完成 ✓");
  } catch (e) { toast("生成失败：" + e.message); }
  btn.classList.remove("ai-loading-btn");
}

async function applyHandoff() {
  const text = document.getElementById("ai-handoff-result").value.trim();
  if (!text) { toast("内容为空，先点「生成」试试"); return; }
  if (!confirm("将生成的交接说明写入项目并覆盖现有内容？")) return;
  try {
    await api("/api/projects/" + currentPid, { method: "PATCH",
      body: JSON.stringify({ handoff_note: text }) });
    toast("交接说明已更新 ✓");
    closeModal("ai-handoff-modal");
    current = await api("/api/projects/" + currentPid);
    renderDetail();
  } catch (e) { toast("应用失败：" + e.message); }
}

