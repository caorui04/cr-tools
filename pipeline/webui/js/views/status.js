// 状态面板
let statusInterval = null;

// 管线环节（M1→M4 顺序）
const STAGE_DEFS = [
  { key: "m1", label: "M1 识别" },
  { key: "m2", label: "M2 OCR抽取" },
  { key: "m3", label: "M3 翻译" },
  { key: "m4", label: "M4 向量化" },
];

function renderStageBar(label, stage) {
  if (!stage || !stage.total) {
    return `
      <div class="stage-row">
        <div class="stage-head"><span class="stage-name">${label}</span><span class="stage-idle">空闲</span></div>
      </div>`;
  }
  const done = stage.done || 0;
  const total = stage.total;
  const pct = Math.min(100, Math.round((done / total) * 100));
  const docId = String(stage.doc_id || "");
  const docShort = docId.length > 24 ? docId.slice(0, 21) + "..." : docId;
  return `
    <div class="stage-row">
      <div class="stage-head">
        <span class="stage-name">${label}</span>
        <span class="stage-nums">${done}/${total} (${pct}%)</span>
      </div>
      <div class="stage-bar"><div class="stage-bar-fill${pct >= 100 ? " done" : ""}" style="width:${pct}%"></div></div>
      <div class="stage-doc" title="${docId}">${docShort}</div>
    </div>`;
}

async function renderStatusView() {
  const container = document.getElementById("view-container");
  container.innerHTML = `<div id="statusContent"><div style="text-align:center;padding:20px">加载中...</div></div>`;
  await refreshStatus();
  if (statusInterval) clearInterval(statusInterval);
  statusInterval = setInterval(refreshStatus, 5000);
}

async function refreshStatus() {
  const { data, error } = await api.getStatus();
  const content = document.getElementById("statusContent");
  if (!content) return;

  if (error) {
    content.innerHTML = `
      <div class="card" style="text-align:center">
        <div class="status-dot offline"></div> <span style="color:var(--danger)">服务未连接</span>
      </div>`;
    return;
  }

  const stages = data.stages || {};
  content.innerHTML = `
    <div class="card">
      <div style="display:flex;align-items:center;gap:6px;margin-bottom:10px">
        <div class="status-dot online"></div> <strong>在线</strong> · 运行 ${Math.floor((data.uptime_s||0)/60)} 分钟
      </div>
      ${STAGE_DEFS.map(s => renderStageBar(s.label, stages[s.key])).join("")}
      <div style="font-size:12px;color:var(--text-secondary);margin-top:8px">
        文档 ${data.docs_total||0} · 知识块 ${data.chunks_total||0} · manifest v${data.manifest_version||0}
        ${(data.processing||[]).length ? ` · 处理中: ${data.processing.join(", ")}` : ""}
      </div>
    </div>
    <div class="card" id="providerCard">
      <div style="font-weight:600;margin-bottom:8px">分析后端</div>
      <div style="font-size:12px;margin-bottom:8px" id="providerInfo">加载中...</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn btn-sm" data-mode="local">本地</button>
        <button class="btn btn-sm" data-mode="cloud">云端</button>
        <button class="btn btn-sm" data-mode="off">完全本地（关云）</button>
      </div>
      <div style="font-size:11px;color:var(--text-secondary);margin-top:6px" id="providerMsg"></div>
    </div>`;
  const pc = document.getElementById("providerCard");
  if (!pc) return;
  await refreshProvider();
  pc.querySelectorAll("[data-mode]").forEach(btn => {
    btn.onclick = async () => {
      btn.disabled = true;
      const { data, error } = await api.setProviderMode(btn.dataset.mode);
      btn.disabled = false;
      const msg = document.getElementById("providerMsg");
      if (msg) {
        msg.textContent = error ? ("切换失败：" + error) : (data?.message || "");
        msg.style.color = (!error && data && data.success) ? "var(--success, #2e7d32)" : "var(--danger, #c62828)";
      }
      refreshProvider();
    };
  });
}

async function refreshProvider() {
  const { data, error } = await api.getProvider();
  const info = document.getElementById("providerInfo");
  const msg = document.getElementById("providerMsg");
  if (!info) return;
  if (error) { info.textContent = "获取失败：" + error; return; }
  const modeLabel = { local: "本地", cloud: "云端", off: "完全本地（关云）" }[data.mode] || data.mode;
  info.textContent = `当前模式：${modeLabel} · 实际后端：${data.provider || "stub"}${data.success === false ? "（未可用）" : ""}`;
  if (msg && data.message && data.success === false) {
    msg.textContent = data.message;
    msg.style.color = "var(--danger, #c62828)";
  }
}
