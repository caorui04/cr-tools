// 引用清单视图（P3.5-1 第 1 步 + M2 项目上下文）
// 引用链正向生成：清单 = CSL JSON（编号=顺序），格式切换 = 重渲染。
// 当前论文项目来自 State.currentPaperId（宿主项目树选中 / 本页下拉切换），无 demo-paper 兜底。

const BIBLIO_FORMATS = ["GB/T 7714-2015", "APA", "MLA", "Chicago"];

/** 当前论文项目（引用清单归属） */
function currentPaperId() {
  return State.currentPaperId || "";
}

async function renderCitationsView() {
  const container = document.getElementById("view-container");
  container.innerHTML = `
    <div class="card">
      <div style="font-weight:600;margin-bottom:8px">引用清单（论文工作台）</div>
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
        <label style="font-size:12px">当前论文项目</label>
        <select id="paperProjectSel" class="ipt" style="width:auto">
          <option value="">（未选择论文项目）</option>
        </select>
        <button class="btn btn-sm" id="paperAddBtn">添加引用</button>
        <select id="paperFormatSel" class="ipt" style="width:auto">
          ${BIBLIO_FORMATS.map(f => `<option value="${f}">${f}</option>`).join("")}
        </select>
        <button class="btn btn-sm" id="paperFormatBtn">切换格式</button>
      </div>
      <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">
        💡 当前论文项目由左侧宿主项目树选中（📝 论文项目）同步，也可在本页下拉切换。未选择时登记会提示。
      </div>
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
        <label style="font-size:12px">文献 doc_id</label>
        <input id="paperDocIdInput" class="ipt" placeholder="粘贴 doc_id（文档详情页可复制，或右侧 PDF 选段登记）" style="flex:1" />
      </div>
      <div id="paperBiblioContent"><div style="text-align:center;padding:16px;color:var(--text-secondary)">加载中...</div></div>
    </div>`;
  // 项目下拉选项（state.js loadPapersDropdown 负责填充，此处重置 loaded 标志强制重新加载）
  const sel = document.getElementById("paperProjectSel");
  sel.dataset.loaded = "";
  State.papers = [];
  loadPapersDropdown();
  sel.value = State.currentPaperId || "";
  sel.addEventListener("change", () => {
    setCurrentPaper(sel.value || null);
    // 同步宿主（宿主 currentPaperId 订阅会再下发回来，形成闭环）
    window.parent.postMessage({ type: "paper-kb:project-changed", paperId: State.currentPaperId }, "*");
    loadPaperBiblio();
  });
  document.getElementById("paperAddBtn").onclick = async () => {
    const pid = currentPaperId();
    if (!pid) { toast("请先在左侧项目树选中论文项目，或在本页下拉选择", true); return; }
    const docId = document.getElementById("paperDocIdInput").value.trim();
    if (!docId) { toast("请先粘贴 doc_id", true); return; }
    const { data, error } = await api.addPaperBiblio(pid, docId);
    if (error) toast("添加失败：" + error, true);
    else if (data && data.ok === false) toast(data.reason || "无法添加", true);
    else { toast("已加入清单"); document.getElementById("paperDocIdInput").value = ""; loadPaperBiblio(); }
  };
  document.getElementById("paperFormatBtn").onclick = async () => {
    const pid = currentPaperId();
    if (!pid) { toast("请先选择论文项目", true); return; }
    const fmt = document.getElementById("paperFormatSel").value;
    const { data, error } = await api.formatPaperBiblio(pid, fmt);
    if (error) toast("切换格式失败：" + error, true);
    else { toast("格式已切换"); renderPaperBiblio(data); }
  };
  document.getElementById("paperDocIdInput").addEventListener("keydown", e => {
    if (e.key === "Enter") document.getElementById("paperAddBtn").click();
  });
  // 宿主项目树切换 → 重载当前项目清单
  document.addEventListener("paper-changed", () => {
    const s = document.getElementById("paperProjectSel");
    if (s) s.value = State.currentPaperId || "";
    loadPaperBiblio();
  });
  loadPaperBiblio();
}

async function loadPaperBiblio() {
  const content = document.getElementById("paperBiblioContent");
  if (!content) return;
  const pid = currentPaperId();
  if (!pid) {
    content.innerHTML = `
      <div style="text-align:center;padding:16px;color:var(--text-secondary)">
        未选择论文项目。<br/>请在左侧项目树选中 📝 论文项目，或在本页下拉选择。
      </div>`;
    return;
  }
  content.innerHTML = `<div style="text-align:center;padding:16px;color:var(--text-secondary)">加载中...</div>`;
  const { data, error } = await api.getPaperBiblio(pid);
  if (error) {
    content.innerHTML = `<div style="color:var(--danger);padding:12px">加载失败：${error}</div>`;
    return;
  }
  renderPaperBiblio(data);
}

function renderPaperBiblio(data) {
  const content = document.getElementById("paperBiblioContent");
  if (!content) return;
  const fmtSel = document.getElementById("paperFormatSel");
  if (fmtSel && data.format) fmtSel.value = data.format;
  const items = (data && data.items) || [];
  if (!items.length) {
    content.innerHTML = `
      <div style="text-align:center;padding:16px;color:var(--text-secondary)">
        清单为空。<br/>在下方输入文献 doc_id 点击「添加引用」，或从「文档」页/右侧 PDF 预览选段登记。
      </div>`;
    return;
  }
  const pending = items.filter(i => !i.confirmed).length;
  const verifiedCount = items.filter(i => i.verified).length;
  content.innerHTML = `
    <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px">
      共 ${items.length} 条 · 格式 ${esc(data.format || "")} · ${pending ? `⚠ ${pending} 条未确认` : "✓ 全部已确认"}
      ${verifiedCount ? ` · 🔍 ${verifiedCount} 条已核验` : ""}
    </div>
    ${items.map(item => {
      const v = item.verified;
      const vBadge = v ? (v.verdict === "supported" ? '<span class="vbadge v-ok">✅ 支持</span>'
        : v.verdict === "not_supported" ? '<span class="vbadge v-bad">⚠️ 不支持</span>'
        : '<span class="vbadge v-un">❓ 不确定</span>') : "";
      const draftLink = item.draft_id ? ` · 草稿：<span class="jump-page" data-draft-goto="${esc(item.draft_id)}">${esc(item.draft_id)}${item.anchor ? " · " + esc(item.anchor) : ""}</span>` : "";
      return `
      <div class="cite-item">
        <div class="cite-seq">[${item.seq}]</div>
        <div style="flex:1;min-width:0">
          <div class="cite-text">${esc(item.formatted || "(无渲染)")}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:2px">
            doc_id: ${esc(item.doc_id)}${item.confirmed ? "" : " · <span style='color:#c98a00'>未确认（草稿）</span>"}
            ${item.page ? ` · <a href="#docs/${esc(item.doc_id)}?page=${item.page}" class="jump-page" title="在右侧预览跳到原文第 ${item.page} 页">第 ${item.page} 页 ↗</a>` : ""}
            ${draftLink}
            ${item.added_at ? " · " + esc(item.added_at) : ""} ${vBadge}
          </div>
          ${v ? `
            <div class="verify-result v-${v.verdict}">
              <div>${v.verdict === "supported" ? "✅ 该论点被文献支持" : v.verdict === "not_supported" ? "⚠️ 文献与该论点矛盾或不支持" : "❓ 文献未提供足够依据"}（置信度 ${(v.confidence * 100).toFixed(0)}% · ${v.mode === "llm" ? "模型判定" : "关键词启发式"}）</div>
              ${v.evidence && v.evidence !== "empty" ? `<div class="verify-evidence">证据片段：${esc(v.evidence)}</div>` : ""}
              ${v.warning ? `<div class="verify-warning">${esc(v.warning)}</div>` : ""}
            </div>` : ""}
          <div class="verify-form" id="vf-${item.seq}" style="display:${v ? "none" : "flex"};gap:4px;margin-top:4px">
            <input class="ipt" id="vc-${item.seq}" placeholder="粘贴你在论文中写的论点（claim）" style="flex:1" />
            <button class="btn btn-sm" data-verify="${item.seq}">核验</button>
          </div>
        </div>
        <button class="btn btn-sm btn-danger" data-seq="${item.seq}">删除</button>
      </div>`;}).join("")}`;
  content.querySelectorAll("[data-seq]").forEach(btn => {
    btn.onclick = async () => {
      const seq = Number(btn.dataset.seq);
      const { data: r, error } = await api.removePaperBiblio(currentPaperId(), seq);
      if (error) toast("删除失败：" + error, true);
      else { toast("已删除，编号已重排"); renderPaperBiblio(r); }
    };
  });
  content.querySelectorAll("[data-verify]").forEach(btn => {
    btn.onclick = async () => {
      const seq = Number(btn.dataset.verify);
      const claimEl = document.getElementById(`vc-${seq}`);
      const claim = claimEl ? claimEl.value.trim() : "";
      if (!claim) { toast("请先粘贴论点 claim", true); return; }
      btn.disabled = true;
      btn.textContent = "核验中（约 10-60s）...";
      const { data, error } = await api.verifyPaperBiblio(currentPaperId(), seq, claim);
      if (error) toast("核验失败：" + error, true);
      else { toast("核验完成"); loadPaperBiblio(); }
    };
  });
  // 草稿锚点回跳（M4 宿主草稿编辑器实现前先提示）
  content.querySelectorAll("[data-draft-goto]").forEach(el => {
    el.onclick = () => {
      toast("草稿编辑器回跳将在宿主版本提供（M4）");
    };
  });
}
