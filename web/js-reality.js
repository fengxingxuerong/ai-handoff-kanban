/* 代码现实：git 快照与「看板说的 vs 代码里真发生的」判定（只读，不写盘）。*/
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
  // 任务里写的「相关代码位置」还找不找得到 —— 文件改名/挪走后 refs 会变成死路，
  // 而看板上看不出任何异常（接手的人照着找一圈才发现，这是交接里最贵的浪费）。
  const refs = g && g.refs;
  if (refs && refs.ok && (refs.missing || []).length) {
    const shown = refs.missing.slice(0, 3).map(m => m.ref).join("、");
    out.push(["warn", `任务里写的代码位置有 <b>${refs.missing.length}</b> 处已经找不到了`
                   + `（${esc(shown)}${refs.missing.length > 3 ? " 等" : ""}）—— 文件多半被改名或挪走了，`
                   + `接手的人照着找会扑空。`]);
  }
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

