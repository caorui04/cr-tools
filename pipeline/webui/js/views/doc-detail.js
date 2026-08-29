// 文档详情页
async function renderDocDetailView(docId) {
  const container = document.getElementById("view-container");
  container.innerHTML = `<div style="text-align:center;padding:20px">加载中...</div>`;

  const { data: doc, error } = await api.getDocDetail(docId);
  if (error || !doc) {
    container.innerHTML = `<div class="card" style="color:var(--danger)">文档未找到: ${esc(docId)}</div>`;
    return;
  }

  const { data: cite } = await api.citeDoc(docId, "GB/T 7714-2015");
  const conf = cite?.field_confidence || {};

  container.innerHTML = `
    <div class="card">
      <button class="btn btn-sm" onclick="location.hash='#docs'" style="margin-bottom:8px">← 返回列表</button>
      <h3>${esc(doc.source_file || docId)}</h3>
      <div style="font-size:12px;color:var(--text-secondary);margin:6px 0">
        ID: ${esc(doc.doc_id)} · 页数: ${doc.page_count} · 状态: ${esc(doc.metadata_status)}
      </div>
      <div style="font-size:12px;color:var(--text-secondary)">
        📄 预览在右侧面板（选中文字可「引用此段」）
      </div>
    </div>
    ${cite ? `
    <div class="card" id="citeInfoCard" data-docid="${esc(docId)}">
      <h4 style="margin-bottom:6px">引用信息</h4>
      <div style="font-size:13px;margin-bottom:6px">${esc(cite.formatted || "[stub]")}</div>
      <div style="margin-top:8px">${Object.entries(conf).map(([k,v]) => renderConfidenceBar(k, v)).join("")}</div>
      <button class="btn btn-success btn-sm" style="margin-top:8px" onclick="confirmBib('${docId}')">确认引用</button>
    </div>` : ""}
    <div class="card" id="digestCard">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <h4 style="margin:0">📖 速览卡</h4>
        <button class="btn btn-sm" id="digestRefreshBtn">↻ 重新生成</button>
      </div>
      <div id="digestContent"><div style="text-align:center;padding:12px;color:var(--text-secondary)">加载中...</div></div>
    </div>
  `;
  loadDigest(docId);
  // PDF.js 预览（本地托管，替换浏览器原生 iframe 查看器）+ 选区引用交互
  // 支持 ?page=N 初始页码（A4 引用清单回跳）：hash 形如 #docs/{id}?page=3
  const pageParam = (location.hash.match(/[?&]page=(\d+)/) || [])[1];
  const initialPage = pageParam ? parseInt(pageParam, 10) : null;
  const pdfUrl = `${API_BASE}/docs/${docId}/file`;
  window.PdfViewer.render(pdfUrl, window.__KB_TOKEN__ || "", { docId, page: initialPage });
  window.PdfViewer.attachEvents();

  // 选区「引用此段」→ 登记到当前论文项目清单（M2：绑当前项目，无则提示，去 demo-paper 兜底）
  window.__onPdfCite = async ({ docId: selDocId, page, snippet }) => {
    const paperId = (State.currentPaperId || "").trim();
    if (!paperId) { toast("请先在左侧项目树选中 📝 论文项目（引用将登记到该项目）", true); return; }
    const { data, error } = await api.addPaperBiblio(paperId, selDocId, { page, snippet });
    if (error) toast("登记失败：" + error, true);
    else if (data && data.ok === false) toast(data.reason || "无法登记", true);
    else {
      toast(`已登记引用（第 ${page} 页）到清单「${paperId}」`);
      document.getElementById("pdfCiteInfo").textContent = `已登记：${snippet.slice(0, 40)}…`;
    }
  };
}

// 速览卡生成轮询（模块级防重复启动）
let digestPollTimer = null;
let digestPollDocId = null;

function stopDigestPoll() {
  if (digestPollTimer) { clearInterval(digestPollTimer); digestPollTimer = null; }
  digestPollDocId = null;
}

/** 生成/重生成速览卡：触发后台任务，进入"提炼中"态并轮询进度 */
async function digestGen(docId, force) {
  const content = document.getElementById("digestContent");
  if (!content) return;
  content.innerHTML = `<div style="text-align:center;padding:12px;color:var(--text-secondary)">正在启动速览卡提炼...</div>`;
  const { data, error } = await api.getDocDigest(docId, force);
  if (error) {
    content.innerHTML = `<div style="color:var(--danger);font-size:12px">生成失败：${error}</div>`;
    return;
  }
  if (data && data.status === "processing") {
    renderDigestProcessing(docId, data);
  } else if (data && data.sections) {
    stopDigestPoll();
    renderDigest(data);
  }
}

/** 提炼中态：进度 + 分析后端模式切换（用户要求在生成结束前可切本地/云端） */
function renderDigestProcessing(docId, prog) {
  const content = document.getElementById("digestContent");
  if (!content) return;
  stopDigestPoll();
  content.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <h4 style="margin:0">📖 速览卡</h4>
      <span style="font-size:11px;color:var(--text-secondary)" id="digestProcTip">生成中...（长文需数分钟，可切后端加速/提质）</span>
    </div>
    <div style="font-size:12px;margin-bottom:6px" id="digestProcText">速览提炼中，正在启动...</div>
    <div style="height:6px;background:var(--border,#e0e0e0);border-radius:3px;overflow:hidden;margin-bottom:8px">
      <div id="digestProcBar" style="height:100%;width:3%;background:var(--primary,#1976d2);transition:width .3s"></div>
    </div>
    <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px" id="digestDmodeLabel">当前后端：<span id="digestDmodeName">查询中...</span></div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:8px">
      <span style="font-size:11px;color:var(--text-secondary)">切换分析后端（提炼速度/质量）：</span>
      <button class="btn btn-sm" data-dmode="local">本地</button>
      <button class="btn btn-sm" data-dmode="cloud">云端</button>
      <button class="btn btn-sm" data-dmode="off">完全本地（关云）</button>
      <span style="font-size:11px;color:var(--text-secondary)" id="digestDmodeMsg"></span>
    </div>`;
  // 显示当前分析后端（本地/云端/完全本地）
  api.getProvider().then(({ data: pv }) => {
    const name = document.getElementById("digestDmodeName");
    if (!name) return;
    if (!pv || pv.error) { name.textContent = "未知"; return; }
    const modeLabel = { local: "本地", cloud: "云端", off: "完全本地（关云）" }[pv.mode] || pv.mode;
    name.textContent = `${modeLabel}（${pv.provider || "stub"}）`;
    // 切换成功后 digestGen 会重渲染本视图 → 自动刷新该行
  });
  content.querySelectorAll("[data-dmode]").forEach(btn => {
    btn.onclick = async () => {
      btn.disabled = true;
      const msg = document.getElementById("digestDmodeMsg");
      const modeLabel = { local: "本地", cloud: "云端", off: "完全本地（关云）" }[btn.dataset.dmode];
      const { data, error } = await api.setProviderMode(btn.dataset.dmode);
      btn.disabled = false;
      if (error || (data && data.success === false)) {
        if (msg) { msg.textContent = "切换失败：" + (error || (data && data.message) || ""); msg.style.color = "var(--danger,#c62828)"; }
        return;
      }
      if (msg) { msg.textContent = `已切换至${modeLabel}，正在重新生成...`; msg.style.color = "var(--success,#2e7d32)"; }
      // 用新后端重新触发提炼（force 重生成）
      digestGen(docId, true);
    };
  });
  // 轮询进度（800ms 粒度，云端每块约 3s，能感知平滑递增）
  digestPollDocId = docId;
  digestPollTimer = setInterval(async () => {
    const { data } = await api.getDocDigestProgress(digestPollDocId);
    if (!data) return;
    if (data.status === "processing") {
      const text = document.getElementById("digestProcText");
      const bar = document.getElementById("digestProcBar");
      if (!data.total) {
        if (text) text.textContent = "速览提炼中，正在启动...";
        return;
      }
      if (text) text.textContent = `速览提炼中，当前处理进度 ${data.done || 0}/${data.total}`;
      if (bar) bar.style.width = Math.round((data.done / data.total) * 100) + "%";
    } else if (data.status === "done") {
      stopDigestPoll();
      // 重新取完整卡
      const { data: full } = await api.getDocDigest(docId);
      if (full && full.sections) renderDigest(full);
      else if (full && full.status === "processing") renderDigestProcessing(docId, full);
      else { const c = document.getElementById("digestContent"); if (c) c.innerHTML = "<div style='color:var(--danger);font-size:12px'>提炼结果读取失败</div>"; }
    } else if (data.status === "error") {
      stopDigestPoll();
      const c = document.getElementById("digestContent");
      if (c) c.innerHTML = `<div style="color:var(--danger);font-size:12px">速览卡生成失败：${data.error || "未知错误"}。<br/><button class="btn btn-sm" style="margin-top:6px" id="digestRetryBtn">重试</button></div>`;
      const rb = document.getElementById("digestRetryBtn");
      if (rb) rb.onclick = () => digestGen(docId, true);
    } else if (data.status === "idle") {
      // 任务丢失（服务重启等）→ 重新触发
      digestGen(docId, true);
    }
  }, 800);
}

async function loadDigest(docId) {
  const content = document.getElementById("digestContent");
  if (!content) return;
  const { data, error } = await api.getDocDigest(docId);
  if (error) {
    content.innerHTML = `
      <div style="font-size:12px;color:var(--text-secondary)">
        尚未生成速览卡（${error}）。<br/>
        <button class="btn btn-sm" style="margin-top:6px" id="digestGenBtn">生成速览卡</button>
        <div style="font-size:11px;color:var(--text-secondary);margin-top:4px">
          本地模型提炼全文约 1-3 分钟，长文（20+ 页）可能 3-5 分钟，可切云端加速；完成后缓存，下次秒开
        </div>
      </div>`;
    const gen = document.getElementById("digestGenBtn");
    if (gen) gen.onclick = () => digestGen(docId, false);
    return;
  }
  if (data && data.status === "processing") {
    renderDigestProcessing(docId, data);
    return;
  }
  renderDigest(data);
  const refresh = document.getElementById("digestRefreshBtn");
  if (refresh) refresh.onclick = () => digestGen(docId, true);
}

function renderDigest(d) {
  const content = document.getElementById("digestContent");
  if (!content) return;
  const docIdRef = (d && d.doc_id) || digestPollDocId || "";
  const sections = (d.sections || []).map(s => `
    <div style="margin-top:6px">
      <div style="font-weight:600;font-size:12px">▸ ${esc(s.heading || "节")}
        ${s.pages && s.pages.length ? `<a href="javascript:void(0)" class="jump-page" data-goto="${s.pages[0]}" title="在右侧预览跳到原文第 ${s.pages[0]} 页${s.pages.length > 1 && s.pages[1] !== s.pages[0] ? `（本节跨第 ${s.pages[0]}-${s.pages[1]} 页）` : ""}">第 ${s.pages[0]} 页 ↗</a>` : ""}
      </div>
      <ul style="margin:2px 0 0 18px;font-size:12px;color:var(--text)">
        ${(s.points || []).map(p => `<li>${esc(p)}</li>`).join("")}
      </ul>
    </div>`).join("");
  content.innerHTML = `
    <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px">
      来源：${d.source === "cached" ? "已缓存" : "本次生成"}${d.doc_id ? " · " + esc(d.doc_id) : ""}
    </div>
    ${d.title ? `<div style="font-weight:600;margin-bottom:6px">${esc(d.title)}</div>` : ""}
    ${d.core_points && d.core_points.length ? `
      <div style="font-weight:600;font-size:12px;margin-top:6px">核心论点</div>
      <ul style="margin:2px 0 0 18px;font-size:12px">
        ${d.core_points.map(p => `<li>${esc(p)}</li>`).join("")}
      </ul>` : ""}
    ${d.key_concepts && d.key_concepts.length ? `
      <div style="font-weight:600;font-size:12px;margin-top:6px">关键概念</div>
      <div style="font-size:12px;margin-top:2px">${d.key_concepts.map(c => `<span class="tag">${esc(c)}</span>`).join(" ")}</div>` : ""}
    <div style="font-weight:600;font-size:12px;margin-top:8px">章节地图（点页码跳转右侧预览）</div>
    ${sections || "<div style='font-size:12px;color:var(--text-secondary)'>（无章节信息）</div>"}
    ${d.relevance ? `<div style="font-size:11px;color:var(--text-secondary);margin-top:8px">${esc(d.relevance)}</div>` : ""}
    <div style="font-size:11px;color:var(--text-secondary);margin-top:8px;border-top:1px dashed var(--border);padding-top:4px">
      速览卡只提炼思想层要点，不含原文摘录；引用请回原文核对。
    </div>`;
  // 章节地图页码 → 右侧预览跳页（旧缓存无 pages 字段则不显示徽标，渐进增强）
  content.querySelectorAll("[data-goto]").forEach(a => {
    a.onclick = () => {
      const p = Number(a.dataset.goto);
      if (window.PdfViewer && typeof window.PdfViewer.gotoPage === "function") {
        window.PdfViewer.gotoPage(p);
        toast(`已跳到原文第 ${p} 页`);
      }
    };
  });
  // 重新生成按钮（loadDigest / 轮询完成两个入口都走到 renderDigest，统一绑定）
  const refresh = document.getElementById("digestRefreshBtn");
  if (refresh && !refresh.dataset.bound) {
    refresh.dataset.bound = "1";
    refresh.onclick = () => digestGen(docIdRef, true);
  }
  // 引用信息随速览卡一并重新提取（cite_updated）→ 提示 + 刷新引用信息卡
  if (d.cite_updated) {
    toast("引用信息已用当前后端重新提取，请核对后确认");
    refreshCiteInfoCard();
  }
}

/** 重拉 /cite 刷新「引用信息」卡（速览卡重生成后引用一并更新） */
async function refreshCiteInfoCard() {
  const card = document.getElementById("citeInfoCard");
  if (!card) return;
  const docId = (card.dataset.docid) || "";
  if (!docId) return;
  const { data: cite } = await api.citeDoc(docId, "GB/T 7714-2015");
  if (!cite) return;
  const conf = cite.field_confidence || {};
  card.innerHTML = `
    <h4 style="margin-bottom:6px">引用信息 <span style="font-size:11px;color:var(--text-secondary)">（已重新提取，未确认）</span></h4>
    <div style="font-size:13px;margin-bottom:6px">${esc(cite.formatted || "[stub]")}</div>
    <div style="margin-top:8px">${Object.entries(conf).map(([k,v]) => renderConfidenceBar(k, v)).join("")}</div>
    <button class="btn btn-success btn-sm" style="margin-top:8px" onclick="confirmBib('${docId}')">确认引用</button>`;
}

async function confirmBib(docId) {
  const { data: cite } = await api.citeDoc(docId, "GB/T 7714-2015");
  if (cite?.draft) {
    const { error } = await api.confirmBibliography(docId, cite.draft);
    if (error) toast("确认失败: " + error, true);
    else toast("引用已确认");
  }
}
